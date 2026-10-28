"""Sentiment-by-menu-item summaries and the sales join.

Join grain: item x US state x month|week. Region comes from the store address (state code), so
the sales file must use state codes. DMA/zip-level joins need a zip->DMA lookup you supply.
"""
from __future__ import annotations

import csv
import io
import math
import re
from collections import defaultdict
from datetime import date, timedelta

from . import db
from .analyzers import LexiconAnalyzer, LLMAnalyzer
from .config import get_settings
from .menu import GENERAL, compile_menu, load_menu, match_items
from .normalize import parse_date, to_float

US_STATES = set("AL AK AZ AR CA CO CT DE FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM "
                "NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY DC".split())
NEG_THRESHOLD = -0.25
CAVEATS = [
    "Reviews are a self-selected sample that skews toward complaints and toward the stores/dates collected.",
    "Correlation between sentiment and units is not causation; treat drivers as hypotheses to test.",
    "Rows with few mentions are noisy. Prefer items with 10+ mentions and 8+ joined periods.",
    "Region is the US state parsed from store addresses; sales must use the same state codes.",
]


def state_from_address(addr: str | None) -> str | None:
    if not addr:
        return None
    for rx in (r",\s*([A-Z]{2})\s+\d{5}", r",\s*([A-Z]{2})\s*(?:,|$)"):
        m = re.search(rx, addr)
        if m and m.group(1) in US_STATES:
            return m.group(1)
    return None


def region_of(review: dict, by_url: dict, by_id: dict) -> str | None:
    pl = by_url.get(review.get("place_url")) or by_id.get(review.get("place_id")) or {}
    return state_from_address(pl.get("address")) or state_from_address(review.get("address"))


# ------------------------------------------------------------------ run analysis
async def analyze_run(run_id: str, method: str = "lexicon", limit: int | None = None) -> None:
    s = get_settings()
    db.set_analysis(run_id, status="running", method=method, processed=0, total=0, error=None)
    try:
        run = db.get_run(run_id)
        reviews = [r for r in db.all_reviews(run_id) if r["text"]]
        if limit:
            reviews = reviews[:limit]
        db.set_analysis(run_id, total=len(reviews))
        if method == "llm":
            if not s.anthropic_key:
                raise RuntimeError("method=llm needs ANTHROPIC_API_KEY in .env")
            analyzer = LLMAnalyzer(s.anthropic_key, s.anthropic_model)
        else:
            analyzer = LexiconAnalyzer()
        mentions = await analyzer.analyze(reviews, load_menu(run["brand"]),
                                          progress=lambda n: db.set_analysis(run_id, processed=n))
        places = db.places_for(run_id)
        by_url, by_id = {p["url"]: p for p in places}, {p["place_id"]: p for p in places}
        meta = {r["review_id"]: r for r in reviews}
        for m in mentions:
            r = meta[m["review_id"]]
            m["region"], m["review_date"] = region_of(r, by_url, by_id), r["review_date"]
        db.replace_mentions(run_id, mentions)
        db.set_analysis(run_id, status="completed", processed=len(reviews))
    except Exception as e:
        import traceback
        traceback.print_exc()
        db.set_analysis(run_id, status="failed", error=str(e))


# ------------------------------------------------------------------ summaries
def _avg(xs):
    return round(sum(xs) / len(xs), 3) if xs else None


def summarize_items(mentions: list[dict]) -> list[dict]:
    by_item: dict[str, list[dict]] = defaultdict(list)
    for m in mentions:
        by_item[m["item"]].append(m)
    rows = []
    for item, ms in by_item.items():
        asp: dict[str, list[float]] = defaultdict(list)
        for m in ms:
            asp[m["aspect"]].append(m["sentiment"])
        top = sorted(asp.items(), key=lambda kv: -len(kv[1]))[:3]
        rows.append({
            "item": item, "mentions": len(ms), "reviews": len({m["review_id"] for m in ms}),
            "avg_sentiment": _avg([m["sentiment"] for m in ms]),
            "negative_share": round(sum(m["sentiment"] <= NEG_THRESHOLD for m in ms) / len(ms), 3),
            "top_aspects": [{"aspect": a, "mentions": len(v), "avg_sentiment": _avg(v)} for a, v in top]})
    return sorted(rows, key=lambda r: -r["mentions"])


def aspect_matrix(mentions: list[dict]) -> dict[str, dict[str, dict]]:
    cell: dict = defaultdict(lambda: defaultdict(list))
    for m in mentions:
        cell[m["item"]][m["aspect"]].append(m["sentiment"])
    return {i: {a: {"n": len(v), "avg": _avg(v)} for a, v in d.items()} for i, d in cell.items()}


# ------------------------------------------------------------------ sales ingestion
def parse_sales_csv(raw: bytes, brand: str = "Subway") -> tuple[list[dict], list[str]]:
    """Required columns: item, region, period, units. Optional: revenue."""
    text = raw.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    cols = {(c or "").strip().lower(): c for c in (reader.fieldnames or [])}
    missing = [c for c in ("item", "region", "period", "units") if c not in cols]
    if missing:
        raise ValueError(f"CSV missing required columns: {missing}. Found: {list(cols)}")
    compiled = compile_menu(load_menu(brand))
    rows, warns = [], []
    for n, r in enumerate(reader, start=2):
        item_raw = (r[cols["item"]] or "").strip()
        canon = next((m for m in load_menu(brand) if m.lower() == item_raw.lower()), None) \
            or (match_items(item_raw, compiled) or [None])[0]
        period, units = parse_date(r[cols["period"]]), to_float(r[cols["units"]])
        region = (r[cols["region"]] or "").strip().upper()
        if period is None or units is None:
            warns.append(f"row {n}: bad period/units, skipped")
            continue
        if region not in US_STATES:
            warns.append(f"row {n}: region '{region}' is not a US state code, skipped")
            continue
        rev = to_float(r[cols["revenue"]]) if "revenue" in cols else None
        rows.append({"item_raw": item_raw, "item": canon, "region": region, "period": period,
                     "units": units, "revenue": rev})
    return rows, warns


# ------------------------------------------------------------------ the join
def period_key(d: str, level: str) -> str:
    dt = date.fromisoformat(d)
    return (dt.replace(day=1) if level == "month" else dt - timedelta(days=dt.weekday())).isoformat()


def pearson(xs: list[float], ys: list[float]) -> float | None:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    return None if sx == 0 or sy == 0 else round(sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy), 3)


def sales_join(run_id: str, sales_id: str, level: str = "month", include_revenue: bool = False,
               min_mentions: int = 10, under_index: float = 0.85) -> dict:
    mentions = [m for m in db.mentions_for(run_id) if m["item"] != GENERAL]
    sales = [r for r in db.sales_rows_for(sales_id)]
    unmatched = sorted({r["item_raw"] for r in sales if r["item"] is None})

    sent: dict = defaultdict(list)
    for m in mentions:
        if m["region"] and m["review_date"]:
            sent[(m["item"], m["region"], period_key(m["review_date"], level))].append(m)
    units: dict = defaultdict(lambda: [0.0, 0.0])
    for r in sales:
        if r["item"]:
            k = (r["item"], r["region"], period_key(r["period"], level))
            units[k][0] += r["units"]
            units[k][1] += r["revenue"] or 0.0

    peers: dict = defaultdict(list)  # (region, period) -> unit totals across items
    for (item, reg, per), (u, _) in units.items():
        peers[(reg, per)].append(u)
    region_sent: dict = defaultdict(list)
    for (item, reg, per), ms in sent.items():
        region_sent[(reg, per)] += [m["sentiment"] for m in ms]

    rows = []
    for key, ms in sent.items():
        if key not in units:
            continue
        item, reg, per = key
        u, rev = units[key]
        mean_u = sum(peers[(reg, per)]) / len(peers[(reg, per)])
        avg = _avg([m["sentiment"] for m in ms])
        row = {"item": item, "region": reg, "period": per, "mentions": len(ms), "avg_sentiment": avg,
               "negative_share": round(sum(m["sentiment"] <= NEG_THRESHOLD for m in ms) / len(ms), 3),
               "units": u, "units_index": round(u / mean_u, 3) if mean_u else None,
               "sentiment_gap": round(avg - _avg(region_sent[(reg, per)]), 3)}
        if include_revenue:
            row["revenue"] = rev
        rows.append(row)

    per_item: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        per_item[r["item"]].append(r)
    items = []
    for item, rs in per_item.items():
        idx = [r["units_index"] for r in rs if r["units_index"] is not None]
        n_m = sum(r["mentions"] for r in rs)
        r_val = pearson([r["avg_sentiment"] for r in rs if r["units_index"] is not None], idx)
        items.append({"item": item, "joined_cells": len(rs), "mentions": n_m,
                      "avg_sentiment": _avg([r["avg_sentiment"] for r in rs]),
                      "mean_units_index": _avg(idx), "sentiment_vs_units_r": r_val,
                      "confidence": "ok" if len(rs) >= 8 and n_m >= min_mentions else "low (few cells/mentions)"})
    items.sort(key=lambda r: (r["mean_units_index"] is None, r["mean_units_index"] or 0))

    return {"level": level, "joined_rows": sorted(rows, key=lambda r: (r["item"], r["region"], r["period"])),
            "items": items, "drivers": _drivers(mentions, items, min_mentions, under_index),
            "unmatched_sales_items": unmatched, "caveats": CAVEATS}


def _drivers(mentions, items, min_mentions, under_index) -> list[dict]:
    """For under-indexing items: which aspects are most negative vs the same aspect across all items."""
    overall: dict[str, list[float]] = defaultdict(list)
    per: dict = defaultdict(lambda: defaultdict(list))
    quotes: dict = defaultdict(list)
    for m in mentions:
        overall[m["aspect"]].append(m["sentiment"])
        per[m["item"]][m["aspect"]].append(m["sentiment"])
        quotes[(m["item"], m["aspect"])].append((m["sentiment"], m["quote"]))
    out = []
    for it in items:
        if it["mean_units_index"] is None or it["mean_units_index"] >= under_index or it["mentions"] < min_mentions:
            continue
        gaps = []
        for asp, vals in per[it["item"]].items():
            if len(vals) >= 3 and asp != "general":
                gaps.append({"aspect": asp, "mentions": len(vals), "avg_sentiment": _avg(vals),
                             "gap_vs_all_items": round(sum(vals) / len(vals) - sum(overall[asp]) / len(overall[asp]), 3),
                             "worst_quotes": [q for _, q in sorted(quotes[(it["item"], asp)])[:2]]})
        gaps = sorted((g for g in gaps if g["gap_vs_all_items"] < 0), key=lambda g: g["gap_vs_all_items"])
        out.append({"item": it["item"], "mean_units_index": it["mean_units_index"], "candidate_drivers": gaps[:3]})
    return out


# ------------------------------------------------------------------ competitive
def _mean_and_var(xs):
    n = len(xs)
    if n < 2: return (_avg(xs) or 0.0), 0.0
    m = sum(xs)/n
    var = sum((x - m)**2 for x in xs) / (n - 1)
    return m, var

def study_insights(study_id: str) -> dict:
    s = db.get_study(study_id)
    if not s:
        raise ValueError("Study not found")
    
    brand_stats = {}
    total_mentions = 0
    
    for r_id in s["run_ids"]:
        run = db.get_run(r_id)
        if not run: continue
        brand = run["brand"]
        revs = db.all_reviews(r_id)
        ments = db.mentions_for(r_id)
        if not revs:
            continue
            
        n_revs = len(revs)
        n_ments = len(ments) if ments else n_revs
        total_mentions += n_ments
        
        ratings = [r["rating"] for r in revs if r.get("rating") is not None]
        rat_mean, rat_var = _mean_and_var(ratings)
        
        if ments:
            sents = [m["sentiment"] for m in ments]
            sent_mean, sent_var = _mean_and_var(sents)
            neg_share = sum(x <= NEG_THRESHOLD for x in sents) / len(sents) if sents else 0
            
            by_month = defaultdict(list)
            for m in ments:
                r_date = m.get("review_date")
                if r_date:
                    by_month[r_date[:7]].append(m["sentiment"])
        else:
            # Fallback to star rating sentiment: (rating - 3)/2
            sents = [((r["rating"] - 3) / 2.0) for r in revs if r.get("rating") is not None]
            sent_mean, sent_var = _mean_and_var(sents) if sents else (0.0, 0.0)
            neg_share = sum(r["rating"] <= 2 for r in revs if r.get("rating") is not None) / len(ratings) if ratings else 0
            
            by_month = defaultdict(list)
            for r in revs:
                r_date = r.get("review_date")
                if r_date and r.get("rating") is not None:
                    by_month[r_date[:7]].append((r["rating"] - 3) / 2.0)

        trend = {ym: _avg(vals) for ym, vals in sorted(by_month.items())}
        
        brand_stats[brand] = {
            "brand": brand,
            "reviews": n_revs,
            "mentions": n_ments,
            "mean_rating": round(rat_mean, 3) if rat_mean else None,
            "rat_var": rat_var,
            "rat_n": len(ratings),
            "mean_sentiment": round(sent_mean, 3),
            "sent_var": sent_var,
            "negative_share": round(neg_share, 3),
            "monthly_trend": trend
        }

        
    focus = s["focus_brand"]
    focus_stat = brand_stats.get(focus)
    
    out_brands = []
    for b_name, b in brand_stats.items():
        sov = b["mentions"] / total_mentions if total_mentions else 0
        b["share_of_voice"] = round(sov, 3)
        
        if focus_stat and b_name != focus:
            # 95% CI delta = (m1 - m2) +- 1.96 * sqrt(var1/n1 + var2/n2)
            # Guard against zero mentions to avoid ZeroDivisionError
            if b["mentions"] > 0 and focus_stat["mentions"] > 0:
                s_se = math.sqrt((b["sent_var"]/b["mentions"]) + (focus_stat["sent_var"]/focus_stat["mentions"]))
                s_diff = b["mean_sentiment"] - focus_stat["mean_sentiment"]
                
                r_se = math.sqrt((b["rat_var"]/b["rat_n"]) + (focus_stat["rat_var"]/focus_stat["rat_n"])) if b["rat_n"] and focus_stat["rat_n"] else 0
                r_diff = (b["mean_rating"] - focus_stat["mean_rating"]) if b["mean_rating"] and focus_stat["mean_rating"] else 0
                
                b["delta_vs_focus"] = {
                    "sentiment": round(s_diff, 3),
                    "sentiment_95ci": [round(s_diff - 1.96*s_se, 3), round(s_diff + 1.96*s_se, 3)],
                    "rating": round(r_diff, 3) if b["mean_rating"] else None,
                    "rating_95ci": [round(r_diff - 1.96*r_se, 3), round(r_diff + 1.96*r_se, 3)] if b["mean_rating"] else None
                }
        
    for b in brand_stats.values():
        b.pop("rat_var", None)
        b.pop("rat_n", None)
        b.pop("sent_var", None)
        out_brands.append(b)
        
    return {"study_id": study_id, "focus_brand": focus, "brands": out_brands}
