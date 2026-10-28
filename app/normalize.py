"""Map raw provider records onto one schema.

Google Maps field names are verified against Bright Data's docs (reviews: review_id,
reviewer_name, review_rating, review, review_date, number_of_likes, response_of_owner,
place_name, place_id, url, address). Tripadvisor / Yelp field names are NOT verified, so
those use tolerant fallbacks; the untouched record is always kept in `raw`.
"""
from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone

TRANSIT_WORDS = ("station", "transit", "metro", "subway line", "train", "mta", "platform")


def pick(rec: dict, *keys, default=None):
    for k in keys:
        v = rec.get(k)
        if v not in (None, "", []):
            return v
    return default


def to_float(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = re.search(r"\d+(?:\.\d+)?", str(v))
    return float(m.group()) if m else None


def to_int(v) -> int | None:
    f = to_float(v)
    return int(f) if f is not None else None


def parse_date(v) -> str | None:
    """Return YYYY-MM-DD, or None (relative strings like '2 weeks ago' stay in date_raw)."""
    if not v:
        return None
    s = str(v).strip().replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).date().isoformat()
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%d/%m/%Y", "%B %d, %Y", "%b %d, %Y", "%B %Y"):
        try:
            return datetime.strptime(str(v).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def sentiment_of(rating: float | None) -> str:
    if rating is None:
        return ""
    return "Positive" if rating >= 4 else "Neutral" if rating >= 3 else "Negative"


def is_error_record(rec: dict) -> bool:
    return bool(rec.get("error") or rec.get("error_code")) and not (
        rec.get("review") or rec.get("review_text") or rec.get("text"))


def normalize_review(source: str, rec: dict, run_ctx: dict = None) -> dict | None:
    if run_ctx is None: run_ctx = {}
    if is_error_record(rec):
        return None
    if source == "google_maps":
        text = pick(rec, "review", default="")
        rating = to_float(pick(rec, "review_rating", "rating"))
        author = pick(rec, "reviewer_name")
        raw_date = pick(rec, "review_date")
        place_name = pick(rec, "place_name", "name") or run_ctx.get("brand")
        likes = to_int(pick(rec, "number_of_likes"))
        owner = pick(rec, "response_of_owner", default="")
    else:  # tripadvisor / yelp: unverified field names -> tolerant lookup
        title = pick(rec, "title", "review_title", default="")
        body = pick(rec, "review_text", "text", "review", "content", "body", "comment", default="")
        text = f"{title}. {body}".strip(". ") if title and body else (body or title)
        rating = to_float(pick(rec, "rating", "review_rating", "stars", "bubble_rating"))
        author = pick(rec, "reviewer_name", "author", "author_name", "user_name", "user", "reviewer")
        raw_date = pick(rec, "review_date", "published_date", "date", "date_of_experience", "time_created")
        place_name = pick(rec, "place_name", "name", "business_name", "restaurant_name") or run_ctx.get("brand")
        likes = to_int(pick(rec, "number_of_likes", "helpful_votes", "useful"))
        owner = pick(rec, "response_of_owner", "owner_response", "management_response", default="")
    if not text and rating is None:
        return None
    if isinstance(author, dict):
        author = author.get("name") or author.get("username")
    author_str = str(author) if author else None
    
    place_id = str(pick(rec, "place_id", "business_id", "location_id", "cid", default="") or "")
    place_url = pick(rec, "url", "place_url", "business_url", "link")
    review_id = pick(rec, "review_id", "id")
    if not review_id:  # stable fallback so reruns dedupe
        basis = f"{source}|{place_id or place_url}|{author_str}|{raw_date}|{str(text)[:80]}"
        review_id = hashlib.sha1(basis.encode()).hexdigest()[:16]
        
    url = pick(rec, "review_url", "url") if pick(rec, "review_url") else place_url
    
    return {
        "source": source, "review_id": str(review_id), "place_id": place_id,
        "place_name": place_name, "place_url": place_url,
        "address": pick(rec, "address", "place_address"),
        "author": author_str, "rating": rating, "text": str(text or ""),
        "review_date": parse_date(raw_date), "date_raw": str(raw_date) if raw_date else None,
        "likes": likes, "owner_response": str(owner or ""), "raw": rec,
        "brand": run_ctx.get("brand"),
        "record_type": "review",
        "url": url,
        "engagement": likes,
        "language": pick(rec, "language", "lang", default="en"),
        "author_hash": hashlib.sha256(author_str.encode()).hexdigest()[:16] if author_str else None,
        "collected_at": run_ctx.get("collected_at") or datetime.now(timezone.utc).isoformat(),
        "snapshot_id": run_ctx.get("snapshot_id")
    }


def normalize_place(rec: dict) -> dict | None:
    url = rec.get("url")
    if not url or rec.get("error"):
        return None
    place_id = rec.get("place_id") or rec.get("cid") or hashlib.sha1(url.encode()).hexdigest()[:16]
    return {
        "place_id": str(place_id), "name": rec.get("name"), "url": url,
        "address": rec.get("address"), "rating": to_float(rec.get("rating")),
        "reviews_count": to_int(rec.get("reviews_count")),
        "lat": to_float(rec.get("lat")), "lon": to_float(rec.get("lon")), "raw": rec,
        "category": rec.get("category") or "",
        "closed": bool(rec.get("permanently_closed")),
    }


def is_brand_place(place: dict, brand: str) -> bool:
    """Check if place matches brand or any of its known aliases, dropping transit decoys."""
    from .menu import load_brands_config
    name = (place.get("name") or "").lower()
    haystack = f"{name} {(place.get('category') or '').lower()}"
    cfg = load_brands_config().get(brand, {})
    aliases = [brand.lower()] + [a.lower() for a in cfg.get("aliases", [])]
    if not any(a in name for a in aliases):
        return False
    if any(w in haystack for w in TRANSIT_WORDS):
        return False
    return not place.get("closed")

