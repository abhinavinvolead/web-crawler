"""Bright Data Scraper API client: trigger -> poll -> download (async flow).

Documented behaviour this code follows (docs.brightdata.com, Sept 2026):
  POST /datasets/v3/trigger?dataset_id=..            -> {"snapshot_id": ...}
  GET  /datasets/v3/progress/{snapshot_id}           -> status: starting|running|digesting|ready|failed|canceled
  GET  /datasets/v3/snapshot/{snapshot_id}?format=json
  Discovery: add type=discover_new&discover_by=..., body {"input":[...], "limit_per_input": n}
  429: wait Retry-After (else exponential backoff). 25 x 429 in 5 min blacklists the IP.
  Billing: per delivered record; failed-input records are still billed. Snapshots live 30 days.
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Awaitable, Callable
import random

import httpx

from .config import Settings

BASE = "https://api.brightdata.com/datasets/v3"
SCRAPERS_JSON = "https://docs.brightdata.com/scrapers.json"

OnSnapshot = Callable[[str], Awaitable[None] | None]
OnProgress = Callable[[str], Awaitable[None] | None]

class BrightDataError(RuntimeError): pass
class BrightDataAuthError(BrightDataError): pass
class BrightDataRateLimitError(BrightDataError): pass
class BrightDataTransientError(BrightDataError): pass
class BrightDataPermanentError(BrightDataError): pass
class BrightDataInputError(BrightDataError): pass

_trigger_sem = asyncio.Semaphore(5)

class BrightDataClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self.headers = {"Authorization": f"Bearer {settings.api_key}"}

    async def _request(self, http: httpx.AsyncClient, method: str, url: str, **kw) -> httpx.Response:
        delay = 2
        for i in range(6):
            try:
                r = await http.request(method, url, headers=self.headers, **kw)
            except httpx.RequestError as e:
                if i == 5: raise BrightDataTransientError(f"Network error: {e}")
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
                continue

            if r.status_code == 429:
                wait = int(r.headers.get("Retry-After") or delay)
                await asyncio.sleep(wait + random.uniform(0, 0.5))  # never retry immediately: that gets the IP blacklisted
                delay *= 2
                continue
            if r.status_code in (401, 403):
                raise BrightDataAuthError(f"Bright Data rejected the API key (HTTP {r.status_code}). "
                                      "Check BRIGHTDATA_API_KEY and that Scraper API is enabled.")
            if r.status_code >= 500:
                if i == 5: raise BrightDataTransientError(f"Server error {r.status_code}")
                await asyncio.sleep(delay + random.uniform(0, 0.5))
                delay *= 2
                continue
            if r.status_code >= 400:
                raise BrightDataInputError(f"Client error {r.status_code}: {r.text[:300]}")
            return r
        raise BrightDataRateLimitError("Bright Data kept returning HTTP 429 (concurrency/job limit). "
                                      "Lower concurrency and retry later.")

    async def collect(self, *, source: str, kind: str, dataset_id: str, inputs: list[dict],
                      discover_by: str | None = None, limit_per_input: int | None = None,
                      snapshot_id: str | None = None, on_snapshot: OnSnapshot | None = None,
                      on_progress: OnProgress | None = None, **_ignored) -> list[dict]:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as http:
            if not snapshot_id:  # resume path re-polls instead of re-triggering (no double billing)
                async with _trigger_sem:
                    snapshot_id = await self._trigger(http, dataset_id, inputs, discover_by, limit_per_input)
                if on_snapshot:
                    res = on_snapshot(snapshot_id)
                    if asyncio.iscoroutine(res):
                        await res
            # Allow poll_timeout to scale with large scrape datasets
            deadline = time.monotonic() + max(self.s.poll_timeout, 120.0)
            await self._wait_ready(http, snapshot_id, deadline, on_progress)
            return await self._download(http, snapshot_id, deadline)

    async def _trigger(self, http, dataset_id, inputs, discover_by, limit_per_input) -> str:
        params = {"dataset_id": dataset_id, "include_errors": "true"}
        if discover_by:
            params.update({"type": "discover_new", "discover_by": discover_by})
            body: object = {"input": inputs}
            if limit_per_input:
                body["limit_per_input"] = limit_per_input  # type: ignore[index]
        else:
            # Clean inputs so Bright Data scraper API never receives unsupported keys (e.g. brand)
            clean_inputs = []
            for item in inputs:
                c = {"url": item["url"]}
                if "days_limit" in item and item["days_limit"] is not None:
                    c["days_limit"] = item["days_limit"]
                clean_inputs.append(c)
            body = clean_inputs
        r = await self._request(http, "POST", f"{BASE}/trigger", params=params, json=body)
        snap = r.json().get("snapshot_id")
        if not snap:
            raise BrightDataPermanentError(f"Trigger returned no snapshot_id: {r.text[:300]}")
        return snap

    async def _wait_ready(self, http, snapshot_id: str, deadline: float,
                          on_progress: OnProgress | None = None) -> None:
        while True:
            if time.monotonic() >= deadline:
                raise BrightDataTransientError(
                    f"Snapshot {snapshot_id} not ready after {self.s.poll_timeout:.0f}s "
                    "(status still running). Resume later."
                )
            r = await self._request(http, "GET", f"{BASE}/progress/{snapshot_id}")
            resp_data = r.json()
            status = resp_data.get("status")
            records_count = resp_data.get("records")
            progress_msg = f"{status}" + (f" ({records_count} records collected)" if records_count is not None else "")
            if on_progress:
                result = on_progress(progress_msg)
                if asyncio.iscoroutine(result):
                    await result
            if status == "ready":
                return
            if status in ("failed", "canceled"):
                raise BrightDataPermanentError(f"Snapshot {snapshot_id} {status}: {r.text[:300]}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BrightDataTransientError(
                    f"Snapshot {snapshot_id} not ready after {self.s.poll_timeout:.0f}s "
                    f"(status={status}). Resume later."
                )
            await asyncio.sleep(min(self.s.poll_interval, remaining))


    async def _download(self, http, snapshot_id: str, deadline: float) -> list[dict]:
        for _ in range(30):
            if time.monotonic() >= deadline:
                raise BrightDataTransientError(
                    f"Snapshot {snapshot_id} was ready but exceeded the download timeout. Resume later."
                )
            r = await self._request(http, "GET", f"{BASE}/snapshot/{snapshot_id}",
                                    params={"format": "json"})
            if r.status_code == 202:  # still digesting
                await asyncio.sleep(min(self.s.poll_interval, max(0, deadline - time.monotonic())))
                continue
            return _parse_records(r)
        raise BrightDataTransientError(f"Snapshot {snapshot_id} still not downloadable after retries.")


def _parse_records(r: httpx.Response) -> list[dict]:
    try:
        data = r.json()
    except ValueError:  # NDJSON fallback
        data = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    if isinstance(data, dict):
        data = data.get("data") or [data]
    return [d for d in data if isinstance(d, dict)]


_cache: dict[str, list[dict]] = {}


async def fetch_scrapers() -> list[dict]:
    """Public list of every pre-built scraper (no API key needed). Cached per process."""
    if "all" not in _cache:
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.get(SCRAPERS_JSON)
            r.raise_for_status()
            _cache["all"] = r.json().get("scrapers", [])
    return _cache["all"]


async def resolve_dataset(
    domain_suffix: str,
    name_contains: str = "review",
    collection_method: str = "collect_by_url",
) -> tuple[str | None, list[str]]:
    """Find a catalogue dataset by domain, name, and advertised input method."""
    items = await fetch_scrapers()
    hits = [s for s in items
            if s.get("domain", "").endswith(domain_suffix)
            and (not name_contains or name_contains in s.get("name", "").lower())
            and collection_method in (
                s.get("scraper_type") or s.get("collection_methods") or []
            )]
    desc = [f'{h["dataset_id"]} ({h["name"]})' for h in hits]
    return (hits[0].get("dataset_id") or hits[0].get("id") if hits else None), desc
