"""SQLAlchemy storage (stdlib only). Raw provider payloads are always kept in `raw`."""
from __future__ import annotations

import json
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from sqlalchemy import (
    create_engine, MetaData, Table, Column, String, Integer, Float, Text, Index, UniqueConstraint, PrimaryKeyConstraint
)
from sqlalchemy.sql import text

from .config import get_settings

metadata = MetaData()

runs = Table('runs', metadata,
    Column('id', String, primary_key=True),
    Column('brand', String),
    Column('status', String),
    Column('stage', String),
    Column('params', Text),
    Column('error', Text),
    Column('created_at', String),
    Column('updated_at', String)
)

jobs = Table('jobs', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('run_id', String),
    Column('source', String),
    Column('kind', String),
    Column('dataset_id', String),
    Column('snapshot_id', String),
    Column('status', String),
    Column('records', Integer, server_default="0"),
    Column('error_records', Integer, server_default="0"),
    Column('message', Text),
    Column('sample', Text),
    Column('retry_urls', Text),
    Column('updated_at', String),
    UniqueConstraint('run_id', 'source', 'kind', name='uq_jobs_run_source_kind')
)

places = Table('places', metadata,
    Column('run_id', String),
    Column('source', String),
    Column('place_id', String),
    Column('name', String),
    Column('url', String),
    Column('address', String),
    Column('rating', Float),
    Column('reviews_count', Integer),
    Column('lat', Float),
    Column('lon', Float),
    Column('raw', Text),
    UniqueConstraint('run_id', 'source', 'place_id', name='uq_places_run_source_place_id')
)

reviews = Table('reviews', metadata,
    Column('run_id', String),
    Column('source', String),
    Column('review_id', String),
    Column('place_id', String),
    Column('place_name', String),
    Column('place_url', String),
    Column('address', String),
    Column('author', String),
    Column('rating', Float),
    Column('text', Text),
    Column('review_date', String),
    Column('date_raw', String),
    Column('likes', Integer),
    Column('owner_response', Text),
    Column('raw', Text),
    Column('brand', String),
    Column('record_type', String),
    Column('url', String),
    Column('engagement', Integer),
    Column('language', String),
    Column('author_hash', String),
    Column('collected_at', String),
    Column('snapshot_id', String),
    UniqueConstraint('run_id', 'source', 'review_id', name='uq_reviews_run_source_review_id')
)
Index('idx_reviews_run', reviews.c.run_id, reviews.c.source)

events = Table('events', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('run_id', String),
    Column('ts', String),
    Column('message', Text)
)

mentions = Table('mentions', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('run_id', String),
    Column('review_id', String),
    Column('source', String),
    Column('item', String),
    Column('aspect', String),
    Column('sentiment', Float),
    Column('quote', Text),
    Column('basis', Text),
    Column('method', Text),
    Column('region', Text),
    Column('review_date', String)
)
Index('idx_mentions_run', mentions.c.run_id, mentions.c.item)

analysis = Table('analysis', metadata,
    Column('run_id', String, primary_key=True),
    Column('status', String),
    Column('method', String),
    Column('processed', Integer),
    Column('total', Integer),
    Column('error', Text),
    Column('updated_at', String)
)

sales_uploads = Table('sales_uploads', metadata,
    Column('id', String, primary_key=True),
    Column('name', String),
    Column('rows', Integer),
    Column('warnings', Text),
    Column('created_at', String)
)

sales_rows = Table('sales_rows', metadata,
    Column('id', Integer, primary_key=True, autoincrement=True),
    Column('upload_id', String),
    Column('item_raw', String),
    Column('item', String),
    Column('region', String),
    Column('period', String),
    Column('units', Float),
    Column('revenue', Float)
)


studies = Table('studies', metadata,
    Column('id', String, primary_key=True),
    Column('name', String),
    Column('focus_brand', String),
    Column('competitors', Text), # json list
    Column('params', Text), # run params
    Column('created_at', String)
)

study_runs = Table('study_runs', metadata,
    Column('study_id', String),
    Column('run_id', String),
    PrimaryKeyConstraint('study_id', 'run_id')
)

engine = create_engine(
    get_settings().database_url,
    connect_args={"timeout": 30} if "sqlite" in get_settings().database_url else {}
)

def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")

@contextmanager
def conn():
    with engine.begin() as connection:
        yield connection

def init_db() -> None:
    metadata.create_all(engine)

def to_dict(row) -> dict:
    return dict(row._mapping) if row else None

# ---------------------------------------------------------------- studies
def create_study(name: str, focus_brand: str, competitors: list[str], params: dict) -> str:
    study_id = "st_" + uuid.uuid4().hex[:10]
    with conn() as c:
        c.execute(
            text("INSERT INTO studies (id, name, focus_brand, competitors, params, created_at) "
                 "VALUES (:id, :name, :focus, :comp, :params, :ts)"),
            {"id": study_id, "name": name, "focus": focus_brand, "comp": json.dumps(competitors),
             "params": json.dumps(params), "ts": now()}
        )
    return study_id

def link_run_to_study(study_id: str, run_id: str) -> None:
    with conn() as c:
        c.execute(text("INSERT INTO study_runs (study_id, run_id) VALUES (:sid, :rid)"),
                  {"sid": study_id, "rid": run_id})

def get_study(study_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(text("SELECT * FROM studies WHERE id=:id"), {"id": study_id}).fetchone()
        if not row: return None
        d = to_dict(row)
        d["competitors"] = json.loads(d["competitors"])
        d["params"] = json.loads(d["params"])
        runs = c.execute(text("SELECT run_id FROM study_runs WHERE study_id=:id"), {"id": study_id}).fetchall()
        d["run_ids"] = [r[0] for r in runs]
        return d

def list_studies() -> list[dict]:
    with conn() as c:
        rows = c.execute(text("SELECT * FROM studies ORDER BY created_at DESC")).fetchall()
    out = []
    for r in rows:
        d = to_dict(r)
        d["competitors"] = json.loads(d["competitors"])
        d["params"] = json.loads(d["params"])
        out.append(d)
    return out

# ---------------------------------------------------------------- runs
def create_run(brand: str, params: dict) -> str:
    run_id = uuid.uuid4().hex[:12]
    with conn() as c:
        c.execute(
            text("INSERT INTO runs (id, brand, status, stage, params, error, created_at, updated_at) "
                 "VALUES (:id, :brand, :status, :stage, :params, :error, :created_at, :updated_at)"),
            {"id": run_id, "brand": brand, "status": "queued", "stage": "queued",
             "params": json.dumps(params), "error": None, "created_at": now(), "updated_at": now()}
        )
    return run_id

def update_run(run_id: str, **fields) -> None:
    fields["updated_at"] = now()
    cols = ", ".join(f"{k}=:{k}" for k in fields)
    with conn() as c:
        fields["run_id"] = run_id
        c.execute(text(f"UPDATE runs SET {cols} WHERE id=:run_id"), fields)

def get_run(run_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(text("SELECT * FROM runs WHERE id=:run_id"), {"run_id": run_id}).fetchone()
    if not row:
        return None
    d = to_dict(row)
    if d and isinstance(d.get("params"), str):
        d["params"] = json.loads(d["params"])
    return d

def list_runs(limit: int = 50) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            text("SELECT id, brand, status, stage, created_at, updated_at, error FROM runs "
                 "ORDER BY created_at DESC LIMIT :limit"), {"limit": limit}).fetchall()
    return [to_dict(r) for r in rows]

def mark_interrupted() -> int:
    with conn() as c:
        res = c.execute(
            text("UPDATE runs SET status='interrupted', error='Server restarted mid-run; "
                 "POST /runs/{id}/resume to continue without re-billing triggered jobs.' "
                 "WHERE status='running'")
        )
        return res.rowcount


def reclaim_stuck_run(run_id: str, max_age_seconds: float) -> bool:
    """Release a run whose active snapshot/job has exceeded its polling window."""
    cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=max_age_seconds)
    ).isoformat(timespec="seconds")
    with conn() as c:
        result = c.execute(
            text(
                "UPDATE runs SET status='interrupted', "
                "error='Run exceeded the provider polling window; resume requested.', "
                "updated_at=:updated_at "
                "WHERE id=:run_id AND status IN ('running', 'queued') AND ("
                "updated_at < :cutoff OR EXISTS ("
                "SELECT 1 FROM jobs WHERE jobs.run_id=runs.id "
                "AND jobs.status IN ('triggered', 'pending') "
                "AND jobs.updated_at < :cutoff))"
            ),
            {"run_id": run_id, "cutoff": cutoff, "updated_at": now()},
        )
        return result.rowcount == 1

def event(run_id: str, message: str) -> None:
    with conn() as c:
        c.execute(text("INSERT INTO events (run_id, ts, message) VALUES (:run_id, :ts, :msg)"),
                  {"run_id": run_id, "ts": now(), "msg": message})

def events(run_id: str, limit: int = 30) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            text("SELECT ts, message FROM events WHERE run_id=:run_id ORDER BY id DESC LIMIT :limit"),
            {"run_id": run_id, "limit": limit}).fetchall()
    return [to_dict(r) for r in reversed(rows)]

# ---------------------------------------------------------------- jobs
def get_job(run_id: str, source: str, kind: str) -> dict | None:
    with conn() as c:
        row = c.execute(text("SELECT * FROM jobs WHERE run_id=:run_id AND source=:source AND kind=:kind"),
                        {"run_id": run_id, "source": source, "kind": kind}).fetchone()
    return to_dict(row)

def upsert_job(run_id: str, source: str, kind: str, **fields) -> None:
    fields["updated_at"] = now()
    with conn() as c:
        # Cross DB support for upsert is complex, but we can do insert ... ON CONFLICT (if postgres)
        # We will use select, then insert or update to be database-agnostic.
        row = c.execute(text("SELECT id FROM jobs WHERE run_id=:run_id AND source=:source AND kind=:kind"),
                        {"run_id": run_id, "source": source, "kind": kind}).fetchone()
        
        args = {"run_id": run_id, "source": source, "kind": kind, **fields}
        if "status" not in args:
            args["status"] = "pending"
            
        if not row:
            cols = ["run_id", "source", "kind"] + list(fields.keys())
            if "status" not in fields:
                cols.append("status")
            vals = [f":{c}" for c in cols]
            c.execute(text(f"INSERT INTO jobs ({', '.join(cols)}) VALUES ({', '.join(vals)})"), args)
        else:
            if not fields: return
            cols = ", ".join(f"{k}=:{k}" for k in fields)
            c.execute(text(f"UPDATE jobs SET {cols} WHERE run_id=:run_id AND source=:source AND kind=:kind"), args)

def jobs_for(run_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(text("SELECT * FROM jobs WHERE run_id=:run_id ORDER BY id"), {"run_id": run_id}).fetchall()
    return [to_dict(r) for r in rows]

# ---------------------------------------------------------------- places / reviews
def save_places(run_id: str, source: str, p_list: list[dict]) -> int:
    if not p_list: return 0
    n = 0
    with conn() as c:
        for p in p_list:
            row = c.execute(text("SELECT 1 FROM places WHERE run_id=:run_id AND source=:source AND place_id=:place_id"),
                            {"run_id": run_id, "source": source, "place_id": p["place_id"]}).fetchone()
            if not row:
                c.execute(
                    text("INSERT INTO places (run_id, source, place_id, name, url, address, rating, reviews_count, lat, lon, raw) "
                         "VALUES (:run_id, :source, :place_id, :name, :url, :address, :rating, :reviews_count, :lat, :lon, :raw)"),
                    {"run_id": run_id, "source": source, "place_id": p["place_id"], "name": p.get("name"), "url": p.get("url"),
                     "address": p.get("address"), "rating": p.get("rating"), "reviews_count": p.get("reviews_count"),
                     "lat": p.get("lat"), "lon": p.get("lon"), "raw": json.dumps(p.get("raw", {}), default=str)}
                )
                n += 1
    return n

def places_for(run_id: str, source: str | None = None) -> list[dict]:
    q = "SELECT * FROM places WHERE run_id=:run_id"
    args = {"run_id": run_id}
    if source:
        q += " AND source=:source"
        args["source"] = source
    with conn() as c:
        rows = c.execute(text(q), args).fetchall()
    return [to_dict(r) for r in rows]

def save_reviews(run_id: str, r_list: list[dict]) -> int:
    if not r_list: return 0
    n = 0
    with conn() as c:
        for r in r_list:
            row = c.execute(text("SELECT 1 FROM reviews WHERE run_id=:run_id AND source=:source AND review_id=:review_id"),
                            {"run_id": run_id, "source": r["source"], "review_id": r["review_id"]}).fetchone()
            if not row:
                c.execute(
                    text("INSERT INTO reviews (run_id, source, review_id, place_id, place_name, place_url, address, author, rating, text, review_date, date_raw, likes, owner_response, raw, brand, record_type, url, engagement, language, author_hash, collected_at, snapshot_id) "
                         "VALUES (:run_id, :source, :review_id, :place_id, :place_name, :place_url, :address, :author, :rating, :text, :review_date, :date_raw, :likes, :owner_response, :raw, :brand, :record_type, :url, :engagement, :language, :author_hash, :collected_at, :snapshot_id)"),
                    {"run_id": run_id, "source": r["source"], "review_id": r["review_id"], "place_id": r.get("place_id"),
                     "place_name": r.get("place_name"), "place_url": r.get("place_url"), "address": r.get("address"),
                     "author": r.get("author"), "rating": r.get("rating"), "text": r.get("text"), "review_date": r.get("review_date"),
                     "date_raw": r.get("date_raw"), "likes": r.get("likes"), "owner_response": r.get("owner_response"),
                     "raw": json.dumps(r.get("raw", {}), default=str), "brand": r.get("brand"), "record_type": r.get("record_type"),
                     "url": r.get("url"), "engagement": r.get("engagement"), "language": r.get("language"),
                     "author_hash": r.get("author_hash"), "collected_at": r.get("collected_at"), "snapshot_id": r.get("snapshot_id")}
                )
                n += 1
    return n

def query_reviews(run_id: str, *, source=None, min_rating=None, max_rating=None,
                  q=None, limit=100, offset=0) -> list[dict]:
    sql = "SELECT * FROM reviews WHERE run_id=:run_id"
    args = {"run_id": run_id, "limit": limit, "offset": offset}
    if source:
        sql += " AND source=:source"; args["source"] = source
    if min_rating is not None:
        sql += " AND rating>=:min_rating"; args["min_rating"] = min_rating
    if max_rating is not None:
        sql += " AND rating<=:max_rating"; args["max_rating"] = max_rating
    if q:
        sql += " AND text LIKE :q"; args["q"] = f"%{q}%"
    
    # Sort by review_date DESC, but since we don't have rowid natively in PG, sort by review_id
    sql += " ORDER BY review_date DESC, review_id LIMIT :limit OFFSET :offset"
    
    with conn() as c:
        rows = c.execute(text(sql), args).fetchall()
    return [to_dict(r) for r in rows]

def all_reviews(run_id: str) -> list[dict]:
    return query_reviews(run_id, limit=10_000_000)

def counts_by_source(run_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(
            text("SELECT source, COUNT(*) AS reviews, ROUND(AVG(rating),2) AS avg_rating "
                 "FROM reviews WHERE run_id=:run_id GROUP BY source"), {"run_id": run_id}).fetchall()
    return [to_dict(r) for r in rows]

# ---------------------------------------------------------------- analysis / mentions
def set_analysis(run_id: str, **f) -> None:
    f["updated_at"] = now()
    with conn() as c:
        row = c.execute(text("SELECT 1 FROM analysis WHERE run_id=:run_id"), {"run_id": run_id}).fetchone()
        if not row:
            c.execute(text("INSERT INTO analysis (run_id) VALUES (:run_id)"), {"run_id": run_id})
        cols = ", ".join(f"{k}=:{k}" for k in f)
        f["run_id"] = run_id
        c.execute(text(f"UPDATE analysis SET {cols} WHERE run_id=:run_id"), f)

def get_analysis(run_id: str) -> dict | None:
    with conn() as c:
        row = c.execute(text("SELECT * FROM analysis WHERE run_id=:run_id"), {"run_id": run_id}).fetchone()
    return to_dict(row)

def replace_mentions(run_id: str, rows: list[dict]) -> None:
    if not rows: return
    with conn() as c:
        c.execute(text("DELETE FROM mentions WHERE run_id=:run_id"), {"run_id": run_id})
        for m in rows:
            c.execute(
                text("INSERT INTO mentions (run_id, review_id, source, item, aspect, sentiment, quote, basis, method, region, review_date) "
                     "VALUES (:run_id, :review_id, :source, :item, :aspect, :sentiment, :quote, :basis, :method, :region, :review_date)"),
                {"run_id": run_id, "review_id": m["review_id"], "source": m["source"], "item": m["item"], "aspect": m["aspect"],
                 "sentiment": m["sentiment"], "quote": m.get("quote"), "basis": m.get("basis"), "method": m.get("method"),
                 "region": m.get("region"), "review_date": m.get("review_date")}
            )

def mentions_for(run_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(text("SELECT * FROM mentions WHERE run_id=:run_id"), {"run_id": run_id}).fetchall()
    return [to_dict(r) for r in rows]

# ---------------------------------------------------------------- sales
def create_sales_upload(name: str, rows: list[dict], warnings: list[str]) -> str:
    uid = uuid.uuid4().hex[:10]
    with conn() as c:
        c.execute(text("INSERT INTO sales_uploads (id, name, rows, warnings, created_at) VALUES (:uid, :name, :rows, :warnings, :created_at)"),
                  {"uid": uid, "name": name, "rows": len(rows), "warnings": json.dumps(warnings[:50]), "created_at": now()})
        for r in rows:
            c.execute(text("INSERT INTO sales_rows (upload_id, item_raw, item, region, period, units, revenue) "
                           "VALUES (:uid, :item_raw, :item, :region, :period, :units, :revenue)"),
                      {"uid": uid, "item_raw": r["item_raw"], "item": r["item"], "region": r["region"], "period": r["period"],
                       "units": r["units"], "revenue": r["revenue"]})
    return uid

def list_sales_uploads() -> list[dict]:
    with conn() as c:
        rows = c.execute(text("SELECT * FROM sales_uploads ORDER BY created_at DESC")).fetchall()
    out = [to_dict(r) for r in rows]
    for o in out:
        o["warnings"] = json.loads(o["warnings"] or "[]")
    return out

def sales_rows_for(upload_id: str) -> list[dict]:
    with conn() as c:
        rows = c.execute(text("SELECT * FROM sales_rows WHERE upload_id=:upload_id"), {"upload_id": upload_id}).fetchall()
    return [to_dict(r) for r in rows]

def queue_retries(run_id: str) -> int:
    """Reset review jobs that have failed URLs so the next execute re-triggers ONLY those URLs."""
    with conn() as c:
        res = c.execute(
            text("UPDATE jobs SET status='pending', snapshot_id=NULL, updated_at=:updated_at "
                 "WHERE run_id=:run_id AND kind='reviews' AND retry_urls IS NOT NULL AND retry_urls != '[]'"),
            {"updated_at": now(), "run_id": run_id}
        )
        return res.rowcount

def queue_cloud_sync(run_id: str) -> int:
    """Reset review jobs that have an existing snapshot_id so the next execute checks and pulls from the cloud."""
    with conn() as c:
        res = c.execute(
            text("UPDATE jobs SET status='pending', updated_at=:updated_at "
                 "WHERE run_id=:run_id AND kind='reviews' AND snapshot_id IS NOT NULL AND snapshot_id != ''"),
            {"updated_at": now(), "run_id": run_id}
        )
        return res.rowcount

def delete_fallback_reviews(run_id: str) -> None:
    with conn() as c:
        c.execute(text("DELETE FROM reviews WHERE run_id=:run_id AND review_id LIKE 'top-%'"), {"run_id": run_id})

