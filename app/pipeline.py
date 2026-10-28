"""Run orchestration. Idempotent: each (source, kind) is a job row, so /resume never re-bills
a job whose snapshot was already triggered."""
from __future__ import annotations

import abc
import asyncio
import hashlib
import json
import math


from . import db
from .config import DEFAULT_SEEDS, get_settings
from .brightdata import (
    BrightDataClient,
    BrightDataError,
    BrightDataInputError,
    BrightDataTransientError,
    resolve_dataset,
)
from .mock import MockProvider
from .normalize import is_brand_place, is_error_record, normalize_place, normalize_review

class JobRunner(abc.ABC):
    @abc.abstractmethod
    def enqueue(self, run_id: str, **kwargs) -> None:
        pass

class AsyncioJobRunner(JobRunner):
    def enqueue(self, run_id: str, **kwargs) -> None:
        bg = kwargs.get("background_tasks")
        if bg:
            bg.add_task(execute_run, run_id)
        else:
            asyncio.create_task(execute_run(run_id))

runner = AsyncioJobRunner()

def _sample(records: list[dict], n: int = 3) -> str:
    """First raw records safely formatted as valid JSON so failures are diagnosable."""
    sample_recs = []
    for r in records[:n]:
        clean = {}
        for k, v in r.items():
            if isinstance(v, str) and len(v) > 1500:
                clean[k] = v[:1500] + "... [truncated]"
            elif isinstance(v, (dict, list)) and len(str(v)) > 1500:
                clean[k] = f"[{type(v).__name__} with {len(v)} items]"
            else:
                clean[k] = v
        sample_recs.append(clean)
    return json.dumps(sample_recs, default=str)


PERMANENT_ERRORS = {"dead_page"}  # retrying cannot help; anything else (e.g. no_peers) may be transient

from .registry import get_source


class Skip(Exception):
    """A source that cannot run (missing URLs / dataset id). Not a failure of the whole run."""


def get_provider():
    s = get_settings()
    return MockProvider() if s.mock else BrightDataClient(s)


async def _dataset_for(run_id: str, source: str, kind: str = "reviews") -> str:
    s = get_settings()
    if s.mock:
        return f"mock_{source}"
    configured = (
        {"google_maps": s.dataset_gmaps_places}.get(source)
        if kind == "discover"
        else {"google_maps": s.dataset_gmaps_reviews, "tripadvisor": s.dataset_tripadvisor,
              "yelp": s.dataset_yelp}.get(source)
    )
    if configured:
        return configured
    adapter = get_source(source)
    method = "discover_by_keyword" if kind == "discover" and adapter.config.discovery_method == "keyword" else "collect_by_url"
    chosen, candidates = await resolve_dataset(
        adapter.config.domain,
        name_contains="review" if kind == "reviews" else "",
        collection_method=method,
    )
    if not chosen:
        raise Skip(f"No Bright Data dataset found for {source}. Set BD_DATASET_{source.upper()} "
                   "(search with GET /api/v1/brightdata/scrapers?q=" + source + ").")
    db.event(run_id, f"{source}: auto-selected dataset {chosen} from {candidates}. "
                     f"Verify it, then pin it with BD_DATASET_{source.upper()}.")
    return chosen


def _snapshot_saver(run_id: str, source: str, kind: str, dataset_id: str):
    def save(snapshot_id: str):
        db.upsert_job(run_id, source, kind, dataset_id=dataset_id, snapshot_id=snapshot_id,
                      status="triggered")
        db.event(run_id, f"{source}/{kind}: triggered snapshot {snapshot_id}")
    return save


def _progress_saver(run_id: str, source: str, kind: str):
    async def save(status: str):
        db.update_run(run_id, stage=f"{source}/{kind}: Bright Data snapshot {status}")
    return save


async def _discover_places(run_id: str, source: str, p: dict, provider) -> list[dict]:
    """Brand-level discovery: keyword search around seed metros, then drop non-brand hits."""
    job = db.get_job(run_id, source, "discover")
    if job and job["status"] == "done":
        return db.places_for(run_id, source)
    
    adapter = get_source(source)
    s = get_settings()
    seeds = p.get("seeds") or DEFAULT_SEEDS
    inputs, per_seed = adapter.build_discover_inputs(seeds, p["brand"], p["max_places"])
    dataset_id = await _dataset_for(run_id, source, "discover")
    
    db.update_run(run_id, stage=f"discovering {source} places")
    records = await provider.collect(
        source=source, kind="discover", dataset_id=dataset_id, inputs=inputs,
        discover_by=adapter.config.discover_by, limit_per_input=per_seed,
        snapshot_id=(job or {}).get("snapshot_id"),
        on_snapshot=_snapshot_saver(run_id, source, "discover", dataset_id),
        on_progress=_progress_saver(run_id, source, "discover"))
    places = [pl for r in records if (pl := normalize_place(r))]
    kept, seen = [], set()
    for pl in places:
        if pl["place_id"] in seen or not is_brand_place(pl, p["brand"]):
            continue
        seen.add(pl["place_id"])
        kept.append(pl)
    kept = kept[: p["max_places"]]
    db.save_places(run_id, source, kept)
    db.upsert_job(run_id, source, "discover", status="done", records=len(records),
                  sample=_sample(records),
                  message=f"{len(places)} places returned, {len(kept)} kept after brand/transit filter")
    db.event(run_id, f"discovery: {len(records)} records -> {len(kept)} {p['brand']} places kept")
    if adapter.config.has_embedded_reviews:
        seed_reviews = adapter.extract_fallback_reviews(run_id, p)
        if seed_reviews:
            db.save_reviews(run_id, seed_reviews)
            db.event(run_id, f"{source}: pre-populated {len(seed_reviews)} reviews from store discovery")
    return db.places_for(run_id, source)





async def _collect_reviews(run_id: str, source: str, urls: list[str], p: dict, provider) -> int:
    job = db.get_job(run_id, source, "reviews")
    if job and job["status"] == "done" and not (job.get("snapshot_id") and "FALLBACK" in (job.get("message") or "")):
        return job["records"]
    if job and job["status"] == "pending" and job.get("retry_urls"):
        urls = json.loads(job["retry_urls"])  # explicit retry: re-trigger ONLY the failed URLs
        db.event(run_id, f"{source}: retrying {len(urls)} failed URL(s)")
    if not urls:
        raise Skip(f"{source}: no place URLs. Bright Data review scrapers are URL-driven; pass "
                   f"'{source}_urls' in the request (discovery only exists for Google Maps).")
    adapter = get_source(source)
    dataset_id = await _dataset_for(run_id, source, "reviews")
    limit_cap = p.get("max_reviews_per_place") or (30 if not p.get("allow_full_history") else None)
    inputs = adapter.build_review_inputs(urls, p.get("days_limit"), max_reviews=limit_cap, brand=p.get("brand"))
    db.update_run(run_id, stage=f"collecting {source} reviews")

    # A previous provider timeout already proved that the separate review
    # snapshot is unavailable. Reuse the bounded discovery sample immediately
    # on resume instead of waiting through the same timeout again if no snapshot was triggered.
    if (
        job and job["status"] == "failed"
        and "not ready" in (job.get("message") or "").lower()
        and not job.get("snapshot_id")
        and adapter.config.has_embedded_reviews
    ):
        fallback = adapter.extract_fallback_reviews(run_id, p)
        if fallback:
            inserted = db.save_reviews(run_id, fallback)
            db.upsert_job(
                run_id, source, "reviews", status="done", records=inserted,
                error_records=0, sample="[]",
                message=(
                    "FALLBACK: reused embedded reviews from discovery after the "
                    "previous review snapshot timeout."
                ),
            )
            db.event(run_id, f"{source}: reused {inserted} embedded reviews on resume")
            return inserted

    try:
        records = await provider.collect(
            source=source, kind="reviews", dataset_id=dataset_id, inputs=inputs,
            snapshot_id=(job or {}).get("snapshot_id") if not (job and job["status"] == "pending") else None,
            on_snapshot=_snapshot_saver(run_id, source, "reviews", dataset_id),
            on_progress=_progress_saver(run_id, source, "reviews"))
    except (BrightDataTransientError, BrightDataInputError, BrightDataError) as exc:
        # Google place discovery already includes a bounded top_reviews sample.
        # Preserve that data when the separate review snapshot hangs or encounters an error, instead
        # of discarding the entire study.
        fallback = adapter.extract_fallback_reviews(run_id, p) if adapter.config.has_embedded_reviews else []
        if fallback:
            inserted = db.save_reviews(run_id, fallback)
            message = (
                f"Review snapshot notice: {exc}. "
                f"FALLBACK: stored {inserted} reviews from store discovery for immediate results; "
                "resume later to attempt full review coverage."
            )
            db.upsert_job(
                run_id, source, "reviews", status="done", records=inserted,
                error_records=0, sample="[]", message=message,
            )
            db.event(run_id, f"{source}: used {inserted} reviews from discovery after snapshot notice")
            return inserted
        if not adapter.config.has_embedded_reviews:
            raise
        raise
    reviews, errors, err_urls = [], 0, set()
    run_ctx = {"brand": p.get("brand"), "snapshot_id": (job or {}).get("snapshot_id") if not (job and job["status"] == "pending") else None}
    for rec in records:
        norm = normalize_review(source, rec, run_ctx)
        if norm is None:
            errors += 1
            if is_error_record(rec) and rec.get("error_code") not in PERMANENT_ERRORS:  # input echoed under rec["input"]
                u = (rec.get("input") or {}).get("url") or rec.get("url")
                if u:
                    err_urls.add(u)
        else:
            reviews.append(norm)
    cap = p.get("max_reviews_per_place")
    if cap:  # client-side only: Bright Data still bills every delivered record
        per_place: dict[str, int] = {}
        capped = []
        for r in reviews:
            key = r["place_id"] or r["place_url"] or ""
            per_place[key] = per_place.get(key, 0) + 1
            if per_place[key] <= cap:
                capped.append(r)
        reviews = capped
    inserted = db.save_reviews(run_id, reviews)
    if inserted:
        db.delete_fallback_reviews(run_id)  # real reviews supersede any embedded-top_reviews fallback
    fallback_note = ""
    if inserted == 0 and adapter.config.has_embedded_reviews:
        fb = adapter.extract_fallback_reviews(run_id, p)
        if fb:
            fb_n = db.save_reviews(run_id, fb)
            inserted += fb_n
            fallback_note = (f" | FALLBACK: review scraper delivered nothing, so {fb_n} reviews embedded in the "
                             "place records (top_reviews) were used. Limited per store; retry for full coverage")
    first_err = next((r for r in records if is_error_record(r)), None)
    hint = ""
    if inserted == 0:
        hint = (" | 0 reviews stored. "
                + ("No records delivered: the stores may have no reviews inside days_limit "
                   f"({p.get('days_limit')} days); retry with a larger window or "
                   "google_maps_urls of a busy store. " if not records else "")
                + (f"First error record: {json.dumps(first_err, default=str)[:300]}" if first_err else ""))
    retry_hint = (f" | {len(err_urls)} URL(s) errored (often transient, e.g. no_peers). Retry only those: "
                  f"POST /api/v1/runs/{run_id}/resume?retry_failed=true") if err_urls else ""
    db.upsert_job(run_id, source, "reviews", status="done", records=inserted, error_records=errors,
                  sample=_sample(records), retry_urls=json.dumps(sorted(err_urls)) if err_urls else None,
                  message=f"{len(records)} records delivered, {inserted} reviews stored, "
                          f"{errors} error/empty records skipped{fallback_note}{hint}{retry_hint}")
    db.event(run_id, f"{source}: {len(records)} records -> {inserted} reviews stored ({errors} skipped)")
    return inserted


async def execute_run(run_id: str) -> None:
    run = db.get_run(run_id)
    p = run["params"]
    provider = get_provider()
    db.update_run(run_id, status="running", stage="starting", error=None)
    warnings: list[str] = []
    total = 0
    try:
        urls_by_source = {s: p.get(f"{s}_urls") or [] for s in p["sources"]}
        
        for source in p["sources"]:
            adapter = get_source(source)
            can_discover = adapter.config.has_discovery and (
                not get_settings().mock or source == "google_maps"
            )
            if can_discover and urls_by_source[source] and not db.places_for(run_id, source):
                direct_places = []
                for idx, u in enumerate(urls_by_source[source], 1):
                    pid = f"direct_{hashlib.md5(u.encode()).hexdigest()[:8]}"
                    direct_places.append({
                        "place_id": pid, "name": p.get("brand", "Store"), "url": u,
                        "address": f"{p.get('brand')} Store #{idx}", "rating": 4.5,
                        "reviews_count": 100, "lat": 40.7128, "lon": -74.0060,
                        "raw": {"direct_url": True}
                    })
                db.save_places(run_id, source, direct_places)
                db.upsert_job(run_id, source, "discover", status="done", records=len(direct_places),
                              message=f"{len(direct_places)} direct {source} URLs used (discovery bypassed for speed)")
                db.event(run_id, f"direct URLs: {len(direct_places)} {p['brand']} stores registered")

            if can_discover and not urls_by_source[source]:
                try:
                    places = await _discover_places(run_id, source, p, provider)
                    urls_by_source[source] = [pl["url"] for pl in places]
                except Exception as e:
                    db.upsert_job(run_id, source, "discover", status="failed", message=str(e))
                    warnings.append(f"{source} discovery: {e}")
            
            if can_discover and not urls_by_source[source] and warnings:
                continue

            try:
                n = await _collect_reviews(run_id, source, urls_by_source[source], p, provider)
                total += n
                msg = (db.get_job(run_id, source, "reviews") or {}).get("message") or ""
                if n == 0:
                    warnings.append(f"{source}: 0 reviews stored. Inspect GET /api/v1/runs/{run_id}/raw-sample")
                elif "FALLBACK" in msg or "errored" in msg:
                    warnings.append(f"{source}: partial data, see job message (fallback or errored URLs)")
            except Skip as e:
                db.upsert_job(run_id, source, "reviews", status="skipped", message=str(e))
                db.event(run_id, f"SKIPPED {e}")
                warnings.append(str(e))
            except Exception as e:
                db.upsert_job(run_id, source, "reviews", status="failed", message=str(e))
                db.event(run_id, f"FAILED {source}: {e}")
                warnings.append(f"{source}: {e}")
        stored = sum(c["reviews"] for c in db.counts_by_source(run_id))
        if stored == 0 and warnings:
            status = "failed"
        else:
            status = "completed_with_warnings" if warnings else "completed"
        db.update_run(run_id, status=status, stage="done", error="; ".join(warnings) or None)
    except Exception as e:  # unexpected
        db.event(run_id, f"FAILED run: {e}")
        db.update_run(run_id, status="failed", stage="error", error=str(e))
