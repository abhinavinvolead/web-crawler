"""Subway review intelligence API. Swagger UI: http://localhost:8000/docs"""
from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from . import db
from .brightdata import fetch_scrapers
from .config import DEFAULT_SEEDS, get_settings
from .excel import build_insights_workbook, build_workbook, csv_bytes
from . import insights
from .menu import load_menu, save_menu
from . import pipeline

COST_NOTE = ("Bright Data bills per delivered record (each review and each discovered place counts); "
             "new accounts get 5,000 free credits/month. Records that fail on bad input are still billed.")


from .logger import setup_logging

@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    db.init_db()
    db.mark_interrupted()
    yield


app = FastAPI(title="Subway Review Intelligence", version="1.0.0", lifespan=lifespan,
              description="Collect brand-level customer reviews for Subway via Bright Data and "
                          "export them to Excel. Runs are async: POST /runs, poll GET /runs/{id}, "
                          "then download export.xlsx.")


class Seed(BaseModel):
    name: str = "Custom"
    lat: float
    long: float
    zoom_level: int = Field(12, ge=8, le=18)
    country: str = "US"


class RunRequest(BaseModel):
    brand: str = Field("Subway", min_length=2)
    sources: list[Literal["google_maps", "tripadvisor", "yelp"]] = ["google_maps"]
    days_limit: int | None = Field(30, ge=1, le=3650,
                                   description="Google Maps only: reviews from the last N days.")
    allow_full_history: bool = Field(False, description="Required to set days_limit=null (can be very costly).")
    max_places: int = Field(5, ge=1, description="Brand-level sample: how many stores to pull reviews for.")
    seeds: list[Seed] | None = Field(None, description=f"Search centres. Default: {[s['name'] for s in DEFAULT_SEEDS]}")
    google_maps_urls: list[str] = Field([], description="Skip discovery and use these Google Maps place URLs.")
    tripadvisor_urls: list[str] = Field([], description="Optional explicit URLs; otherwise Bright Data discovery is used.")
    yelp_urls: list[str] = Field([], description="Optional explicit URLs; otherwise Bright Data discovery is used.")
    max_reviews_per_place: int | None = Field(None, ge=1, description="Client-side cap; does NOT reduce billing.")

    model_config = {"json_schema_extra": {"example": {
        "brand": "Subway", "sources": ["google_maps"], "days_limit": 30, "max_places": 5}}}


def _run_or_404(run_id: str) -> dict:
    run = db.get_run(run_id)
    if not run:
        raise HTTPException(404, "Run not found")
    return run


@app.get("/api/v1/health", tags=["system"])
def health():
    s = get_settings()
    return {"ok": True, "mode": "MOCK (no credits used)" if s.mock else "LIVE (Bright Data)",
            "api_key_set": bool(s.api_key)}


@app.get("/api/v1/config", tags=["system"])
def config():
    s = get_settings()
    return {"mock": s.mock, "dataset_ids": {
        "google_maps_reviews": s.dataset_gmaps_reviews, "google_maps_places": s.dataset_gmaps_places,
        "tripadvisor": s.dataset_tripadvisor or "(auto-resolve from scrapers.json at run time)",
        "yelp": s.dataset_yelp or "(auto-resolve from scrapers.json at run time)"},
        "max_places_hard_cap": s.max_places_hard_cap, "poll_interval_s": s.poll_interval,
        "cost_note": COST_NOTE}


@app.get("/api/v1/brightdata/scrapers", tags=["system"])
async def find_scrapers(q: str = Query(..., min_length=2, examples=["tripadvisor"])):
    """Search Bright Data's public scraper catalogue for a dataset_id (no API key needed)."""
    try:
        items = await fetch_scrapers()
    except Exception as e:
        raise HTTPException(502, f"Could not reach docs.brightdata.com/scrapers.json: {e}")
    ql = q.lower()
    hits = [i for i in items if ql in i.get("name", "").lower() or ql in i.get("domain", "").lower()]
    return {"count": len(hits), "results": hits[:25]}


@app.get("/api/v1/sources", tags=["system"])
def list_sources():
    from .registry import registry
    return [{"name": k, "domain": v.config.domain, "has_discovery": v.config.has_discovery, 
             "capabilities": v.config.capabilities} for k, v in registry.items()]


@app.get("/api/v1/brands", tags=["system"])
def list_brands():
    """List configured brands supported by the intelligence engine."""
    from .menu import load_brands_config
    return list(load_brands_config().keys())




class StudyRequest(BaseModel):
    name: str = Field(..., description="Name of the study (e.g. 'Subway vs McD NY')")
    focus_brand: str = Field(..., description="The primary brand")
    competitors: list[str] = Field(default_factory=list, description="Competitor brands")
    sources: list[str] = Field(["google_maps"], description="Sources to pull from")
    days_limit: int | None = Field(30, description="Max age of reviews to collect")
    max_places: int = Field(3, description="Max stores per brand to discover")
    seeds: list[dict] = Field(None, description="Location seeds")
    allow_full_history: bool = False
    max_reviews_per_place: int | None = None
    study_budget: int = Field(5000, description="Maximum total records to collect for the study across all brands")
    google_maps_urls: list[str] = Field(default_factory=list, description="Direct Google Maps store URLs (skips discovery for speed)")
    poll_timeout: float | None = Field(None, description="Optional custom poll timeout in seconds")

@app.post("/api/v1/studies", status_code=202, tags=["studies"])
def create_study(req: StudyRequest, background: BackgroundTasks):
    s = get_settings()
    if req.max_places > s.max_places_hard_cap:
        raise HTTPException(400, f"max_places exceeds hard cap {s.max_places_hard_cap}")
        
    if req.study_budget > 10000:
        raise HTTPException(400, "study_budget cannot exceed 10000 records to prevent billing overages")
        
    from .menu import load_brands_config
    brands_config = load_brands_config()
    allowed_brands = set(brands_config.keys())
    
    if req.focus_brand not in allowed_brands:
        raise HTTPException(400, f"Focus brand '{req.focus_brand}' not found in brands.yaml")
        
    comp_set = set()
    for comp in req.competitors:
        if comp not in allowed_brands:
            raise HTTPException(400, f"Competitor '{comp}' not found in brands.yaml")
        if comp == req.focus_brand:
            raise HTTPException(400, f"Focus brand '{comp}' cannot also be a competitor")
        if comp in comp_set:
            raise HTTPException(400, f"Duplicate competitor '{comp}'")
        comp_set.add(comp)
    
    # Simple budget cap logic: divide budget evenly among brands
    brands = [req.focus_brand] + req.competitors
    per_brand_budget = req.study_budget // len(brands)
    if req.max_reviews_per_place is None:
        if req.max_places > 0:
            req.max_reviews_per_place = max(1, per_brand_budget // req.max_places)
    
    study_params = req.model_dump(exclude={"name", "focus_brand", "competitors"})
    study_id = db.create_study(req.name, req.focus_brand, req.competitors, study_params)
    
    run_ids = []
    for brand in brands:
        run_params = study_params.copy()
        if brand == req.focus_brand and req.google_maps_urls:
            run_params["google_maps_urls"] = req.google_maps_urls
        run_req = RunRequest(brand=brand, **run_params)
        run_id = db.create_run(brand, run_req.model_dump())
        db.link_run_to_study(study_id, run_id)
        run_ids.append(run_id)

    async def _execute_study_runs(r_ids: list[str]):
        await asyncio.gather(*(pipeline.execute_run(rid) for rid in r_ids), return_exceptions=True)

    if background:
        background.add_task(_execute_study_runs, run_ids)
    else:
        asyncio.create_task(_execute_study_runs(run_ids))
    return {"study_id": study_id, "run_ids": run_ids}


@app.get("/api/v1/studies", tags=["studies"])
def list_studies():
    return db.list_studies()

@app.get("/api/v1/studies/{study_id}", tags=["studies"])
def get_study(study_id: str):
    s = db.get_study(study_id)
    if not s: raise HTTPException(404, "Study not found")
    s["runs"] = [db.get_run(r) for r in s["run_ids"]]
    return s

@app.get("/api/v1/studies/{study_id}/insights", tags=["insights"])
def get_study_insights(study_id: str):
    try:
        return insights.study_insights(study_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/v1/studies/{study_id}/export.xlsx", tags=["export"])
def export_study_xlsx(study_id: str):
    s = db.get_study(study_id)
    if not s: raise HTTPException(404, "Study not found")
    
    try:
        ins = insights.study_insights(study_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
        
    all_ments = {}
    all_reviews = {}
    all_places = {}
    for r_id in s["run_ids"]:
        run = db.get_run(r_id)
        if run:
            b = run["brand"]
            all_ments[b] = db.mentions_for(r_id)
            all_reviews[b] = db.all_reviews(r_id)
            all_places[b] = db.places_for(r_id)
            
    from pathlib import Path
    from .excel import build_study_insights_workbook
    path = Path(get_settings().export_dir) / f"study_{study_id}_insights.xlsx"
    build_study_insights_workbook(path, s, ins, all_ments, all_reviews, all_places)
    
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.post("/api/v1/runs", status_code=202, tags=["runs"])
def create_run(req: RunRequest, background: BackgroundTasks):
    s = get_settings()
    if not s.mock and not s.api_key:
        raise HTTPException(400, "BD_MOCK=false but BRIGHTDATA_API_KEY is empty.")
    if req.days_limit is None and not req.allow_full_history:
        raise HTTPException(400, "days_limit=null pulls ALL history and can burn credits. "
                                 "Set allow_full_history=true to confirm.")
    if req.max_places > s.max_places_hard_cap:
        raise HTTPException(400, f"max_places exceeds hard cap {s.max_places_hard_cap} "
                                 "(raise MAX_PLACES_HARD_CAP in .env to override).")
    run_id = db.create_run(req.brand, req.model_dump())
    db.event(run_id, f"queued ({'MOCK' if s.mock else 'LIVE'} mode)")
    pipeline.runner.enqueue(run_id, background_tasks=background)
    return {"run_id": run_id, "status_url": f"/api/v1/runs/{run_id}",
            "export_xlsx": f"/api/v1/runs/{run_id}/export.xlsx", "cost_note": COST_NOTE}


@app.get("/api/v1/runs", tags=["runs"])
def list_runs():
    return db.list_runs()


@app.get("/api/v1/runs/{run_id}", tags=["runs"])
def get_run(run_id: str):
    run = _run_or_404(run_id)
    return {**run, "jobs": db.jobs_for(run_id), "by_source": db.counts_by_source(run_id),
            "places": len(db.places_for(run_id)), "events": db.events(run_id)}


@app.post("/api/v1/runs/{run_id}/resume", status_code=202, tags=["runs"])
def resume_run(run_id: str, background: BackgroundTasks,
               retry_failed: bool = Query(False, description="Re-trigger ONLY URLs that errored (billable)."),
               sync_cloud: bool = Query(False, description="Re-check Bright Data cloud status for existing snapshot.")):
    """Continue an interrupted/failed run. Jobs already triggered are re-polled, not re-billed."""
    run = _run_or_404(run_id)
    if run["status"] in ("running", "queued"):
        settings = get_settings()
        reclaim_timeout = min(settings.poll_timeout, 60.0)
        if not db.reclaim_stuck_run(run_id, reclaim_timeout):
            raise HTTPException(
                409,
                f"Run is {run['status']} and is still active. "
                "Retry resume after the provider polling timeout.",
            )
        db.event(
            run_id,
            f"stale {run['status']} run reclaimed after exceeding "
            f"{reclaim_timeout:.0f}s polling window",
        )
    if sync_cloud:
        queued = db.queue_cloud_sync(run_id)
        db.event(run_id, f"cloud sync requested (jobs reset for polling: {queued})")
    elif retry_failed:
        queued = db.queue_retries(run_id)
        db.event(run_id, f"retry requested (jobs queued for retry: {queued})")
        if not queued:
            raise HTTPException(409, "Nothing to retry: no errored URLs recorded for this run.")
    else:
        db.event(run_id, "resume requested")
    pipeline.runner.enqueue(run_id, background_tasks=background)
    return {"run_id": run_id, "status": "resuming"}


@app.post("/api/v1/studies/{study_id}/sync-cloud", status_code=202, tags=["studies"])
def sync_study_cloud(study_id: str, background: BackgroundTasks):
    """Re-check Bright Data cloud snapshots for all runs in a study and import completed reviews."""
    s = db.get_study(study_id)
    if not s: raise HTTPException(404, "Study not found")
    for r_id in s["run_ids"]:
        db.queue_cloud_sync(r_id)
        pipeline.runner.enqueue(r_id, background_tasks=background)
    return {"study_id": study_id, "status": "syncing"}



@app.get("/api/v1/runs/{run_id}/reviews", tags=["data"])
def get_reviews(run_id: str, source: str | None = None, min_rating: float | None = None,
                max_rating: float | None = None, q: str | None = Query(None, description="Text contains"),
                limit: int = Query(100, le=1000), offset: int = 0):
    _run_or_404(run_id)
    rows = db.query_reviews(run_id, source=source, min_rating=min_rating, max_rating=max_rating,
                            q=q, limit=limit, offset=offset)
    for r in rows:
        r.pop("raw", None)
    return {"count": len(rows), "results": rows}


@app.get("/api/v1/runs/{run_id}/raw-sample", tags=["data"])
def raw_sample(run_id: str, source: str | None = None):
    """Raw provider records exactly as Bright Data delivered them, per job (errors included).
    Works even when no review survived normalisation."""
    import json
    _run_or_404(run_id)
    out = []
    for j in db.jobs_for(run_id):
        if source and j["source"] != source:
            continue
        raw_first = []
        raw_val = j.get("sample")
        if raw_val:
            try:
                raw_first = json.loads(raw_val)
            except Exception:
                raw_first = [{"raw_preview": raw_val[:1000], "note": "Unparsed raw sample string"}]
        out.append({
            "source": j["source"],
            "kind": j["kind"],
            "status": j["status"],
            "dataset_id": j["dataset_id"],
            "snapshot_id": j["snapshot_id"],
            "message": j["message"],
            "raw_first_records": raw_first,
        })
    return out


@app.get("/api/v1/runs/{run_id}/places", tags=["data"])
def get_places(run_id: str):
    _run_or_404(run_id)
    rows = db.places_for(run_id)
    for r in rows:
        r.pop("raw", None)
    return rows


@app.get("/api/v1/runs/{run_id}/export.xlsx", tags=["export"])
def export_xlsx(run_id: str):
    run = _run_or_404(run_id)
    reviews = db.all_reviews(run_id)
    if not reviews:
        raise HTTPException(409, f"No reviews yet (run status: {run['status']}).")
    from pathlib import Path
    path = Path(get_settings().export_dir) / f"{run['brand'].lower()}_reviews_{run_id}.xlsx"
    build_workbook(path, run, reviews, db.places_for(run_id), db.jobs_for(run_id))
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.get("/api/v1/runs/{run_id}/export.csv", tags=["export"])
def export_csv(run_id: str):
    _run_or_404(run_id)
    return Response(csv_bytes(db.all_reviews(run_id)), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="reviews_{run_id}.csv"'})


# ======================================================================= insights layer
class AnalyzeRequest(BaseModel):
    method: Literal["lexicon", "llm"] = Field("lexicon", description="lexicon = free/offline baseline; "
                                              "llm = Anthropic API (needs ANTHROPIC_API_KEY, costs tokens).")
    limit: int | None = Field(None, ge=1, description="Analyse only the first N reviews (use for cheap LLM tests).")


@app.get("/api/v1/menu", tags=["insights"])
def get_menu(brand: str = "Subway"):
    """Canonical menu items and aliases used for item detection and sales matching."""
    return load_menu(brand)


@app.put("/api/v1/menu", tags=["insights"])
def put_menu(menu: dict[str, list[str]], brand: str = "Subway"):
    if not menu:
        raise HTTPException(400, "Menu cannot be empty")
    save_menu(brand, menu)
    return {"items": len(menu)}


@app.post("/api/v1/runs/{run_id}/analyze", status_code=202, tags=["insights"])
def analyze(run_id: str, req: AnalyzeRequest, background: BackgroundTasks):
    _run_or_404(run_id)
    if not db.query_reviews(run_id, limit=1):
        raise HTTPException(409, "Run has no reviews to analyse yet.")
    if req.method == "llm" and not get_settings().anthropic_key:
        raise HTTPException(400, "method=llm needs ANTHROPIC_API_KEY in .env")
    db.set_analysis(run_id, status="queued", method=req.method)
    background.add_task(insights.analyze_run, run_id, req.method, req.limit)
    return {"run_id": run_id, "status_url": f"/api/v1/runs/{run_id}/analysis"}


@app.get("/api/v1/runs/{run_id}/analysis", tags=["insights"])
def analysis_status(run_id: str):
    _run_or_404(run_id)
    return db.get_analysis(run_id) or {"status": "not_started"}


def _mentions_or_409(run_id: str) -> list[dict]:
    _run_or_404(run_id)
    m = db.mentions_for(run_id)
    if not m:
        raise HTTPException(409, "No analysis yet. POST /runs/{id}/analyze first.")
    return m


@app.get("/api/v1/runs/{run_id}/insights/items", tags=["insights"])
def insight_items(run_id: str):
    """Per menu item: mentions, average sentiment (-1..1), negative share, top aspects."""
    return insights.summarize_items(_mentions_or_409(run_id))


@app.post("/api/v1/sales/upload", tags=["insights"])
async def upload_sales(file: UploadFile = File(...), brand: str = Query("Subway", description="Brand this sales data belongs to")):
    """CSV with columns: item, region (US state code), period (date), units, [revenue]."""
    try:
        rows, warns = insights.parse_sales_csv(await file.read(), brand=brand)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not rows:
        raise HTTPException(400, f"No usable rows. {warns[:5]}")
    sid = db.create_sales_upload(file.filename or "sales.csv", rows, warns)
    unmatched = sorted({r["item_raw"] for r in rows if r["item"] is None})
    return {"sales_id": sid, "rows": len(rows), "warnings": warns[:20], "unmatched_items": unmatched}


@app.get("/api/v1/sales", tags=["insights"])
def list_sales():
    return db.list_sales_uploads()


@app.get("/api/v1/runs/{run_id}/insights/sales-join", tags=["insights"])
def sales_join(run_id: str, sales_id: str, level: Literal["month", "week"] = "month",
               include_revenue: bool = Query(False, description="Dollar fields are finance-only by default.")):
    _mentions_or_409(run_id)
    return insights.sales_join(run_id, sales_id, level, include_revenue)


@app.get("/api/v1/runs/{run_id}/insights/export.xlsx", tags=["export"])
def export_insights(run_id: str, sales_id: str | None = None, level: Literal["month", "week"] = "month"):
    from pathlib import Path
    run = _run_or_404(run_id)
    m = _mentions_or_409(run_id)
    join = insights.sales_join(run_id, sales_id, level) if sales_id else None
    path = Path(get_settings().export_dir) / f"{run['brand'].lower()}_insights_{run_id}.xlsx"
    build_insights_workbook(path, run, insights.summarize_items(m), insights.aspect_matrix(m), join)
    return FileResponse(path, filename=path.name,
                        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
