"""Offline stand-in for Bright Data. Mirrors verified Google Maps field names and adds
realistic traps (a transit-station decoy, an error record, duplicate reviews)."""
from __future__ import annotations

import hashlib
import random
import re
from datetime import date, timedelta

SEED_STATES = [(40.7128, -74.0060, "New York", "NY"), (34.0522, -118.2437, "Los Angeles", "CA"),
               (41.8781, -87.6298, "Chicago", "IL"), (29.7604, -95.3698, "Houston", "TX"),
               (25.7617, -80.1918, "Miami", "FL")]


def _where(lat: float, lon: float) -> str:
    city, st = min(SEED_STATES, key=lambda t: (t[0] - lat) ** 2 + (t[1] - lon) ** 2)[2:]
    return f"{city}, {st} 10001"


BRAND_REVIEWS = {
    "Burger King": {
        "neg": [
            "The Whopper was cold and the bun was soggy, total disappointment.",
            "Fries were limp, cold, and unsalted, and the Whopper was sloppy.",
            "Ordered a Bacon King and they completely forgot the bacon.",
            "Chicken Fries were dry and overcooked, tasted like old oil.",
            "Waited 25 minutes in the drive-thru line, staff seemed slow and overwhelmed.",
            "Store was dirty, tables not cleaned and drink machine out of syrup.",
            "Way too expensive now for what you get, $16 for a Whopper meal is crazy.",
            "Employee was rude when I asked for dipping sauce they missed.",
            "Delivery through the app arrived cold and missing half the items.",
            "The onion rings were cold and burnt, will not be returning.",
            "The burger meat was rubbery and lacked flame-grilled taste.",
        ],
        "pos": [
            "Loved the Whopper, flame-grilled flavor was on point and veggies fresh.",
            "The Bacon King was fresh, hot, and stacked with crispy bacon.",
            "Chicken Fries were hot and crispy, quick service through the drive-thru.",
            "The onion rings with zesty sauce are unmatched, great value.",
            "Clean store, fast friendly staff, and the app deal saved me money.",
            "Fresh hot fries and perfect Whopper, best fast food in the area.",
            "Double Whopper meal was delicious, order was completely accurate.",
            "Staff at the counter was polite and food was ready in under two minutes.",
            "Great value with the Whopper deal, loved the burger.",
        ],
        "neutral": [
            "It was okay, standard fast food burger and fries for a quick lunch.",
            "Food was decent, wait was average, typical Burger King experience.",
        ],
    },
    "McDonald's": {
        "neg": [
            "Big Mac was thrown together and cold, lettuce everywhere in the box.",
            "Fries were soggy and cold, and the ice cream machine was broken again.",
            "Quarter Pounder was dry and burnt, very disappointing.",
            "Waited 20 minutes for fast food, staff seemed confused.",
            "Forgot my McNuggets sweet and sour sauce again.",
            "Dirty tables and sticky floor near the drink station.",
        ],
        "pos": [
            "Fresh hot fries, perfectly salted and crispy, Big Mac was great.",
            "McNuggets were fresh and crispy, friendly service at the drive-thru.",
            "Quarter Pounder with cheese was hot, juicy, and delicious.",
            "Clean store, fast service, and the mobile app deals are awesome.",
        ],
        "neutral": [
            "Standard McDonald's, fast and predictable for a quick bite.",
        ],
    },
    "Wendy's": {
        "neg": [
            "Baconator was greasy and cold, fries were soggy.",
            "Frosty machine was down and the drive-thru took 20 minutes.",
            "Dave's Single was dry and the tomato was wilted.",
            "Staff was dismissive and messed up our burger order.",
        ],
        "pos": [
            "Dave's Single was fresh, juicy, and delicious with hot crispy fries.",
            "Love the chocolate Frosty and spicy chicken nuggets, great quick service.",
            "Baconator was hot and fresh, friendly staff and clean dining room.",
        ],
        "neutral": [
            "Food was okay, standard Wendy's lunch.",
        ],
    },
    "Subway": {
        "neg": [
            "The meatball marinara was cold and soggy, total disappointment.",
            "Meatball sub was greasy and the sauce tasted bland.",
            "Ordered the meatball marinara and the bread was stale and the meat tiny.",
            "Spicy Italian was bland and overpriced.",
            "Turkey breast sandwich was dry and the portion was tiny.",
            "Waited 25 minutes for a simple sandwich, staff seemed overwhelmed and slow.",
            "Bread was stale and the meat portion was tiny for the price.",
            "They got my order wrong again, missing toppings I asked for.",
            "Store was dirty, tables not cleaned and the floor was sticky.",
            "Way too expensive now for what you get, price keeps going up.",
            "Employee was rude when I asked for a refund.",
            "Delivery through the app took forever and the sandwich arrived cold.",
        ],
        "pos": [
            "Loved the Italian BMT, fresh veggies and perfect bread.",
            "The cookies are great and the footlong deal is real value.",
            "Fresh bread and friendly staff, quick service.",
            "Great value with the footlong deal, loved the sandwich.",
            "Clean store and my order was perfect, fresh veggies.",
            "Cookies are great and the app deal saved me money.",
        ],
        "neutral": [
            "It was okay, nothing special but fine for a quick lunch.",
        ],
    },
}

GENERIC_NEG = [
    "Food was cold and service was very slow, quite disappointing.",
    "Order was incorrect and employee at the register was rude.",
    "Tables were dirty and the restaurant felt unkempt.",
    "Overpriced for the portion size and quality delivered.",
]
GENERIC_POS = [
    "Delicious food, hot and fresh, and friendly fast customer service.",
    "Clean restaurant, great value deals, and our order was 100% accurate.",
    "Quick service and tasty meal, will definitely visit again.",
]
GENERIC_NEUTRAL = [
    "It was fine, average experience for a quick meal.",
]


def _detect_brand(inp: dict, url: str) -> str:
    if inp.get("brand"):
        return inp["brand"]
    low = url.lower()
    if "burger" in low or "bk" in low:
        return "Burger King"
    if "mcdonald" in low or "mcd" in low:
        return "McDonald's"
    if "wendy" in low:
        return "Wendy's"
    if "subway" in low:
        return "Subway"
    m = re.search(r"/place/([^/@]+)", url)
    if m:
        b = m.group(1).replace("+", " ").replace("%20", " ")
        if b and not b.startswith("mock"):
            return b
    return inp.get("keyword", "Subway")


def _get_brand_texts(brand: str) -> tuple[list[str], list[str], list[str]]:
    for k, v in BRAND_REVIEWS.items():
        if k.lower() == brand.lower() or brand.lower() in k.lower():
            return v["neg"], v["pos"], v["neutral"]
    return GENERIC_NEG, GENERIC_POS, GENERIC_NEUTRAL


def _rng(seed: str) -> random.Random:
    return random.Random(int(hashlib.md5(seed.encode()).hexdigest()[:8], 16))


class MockProvider:
    async def collect(self, *, source: str, kind: str, dataset_id: str, inputs: list[dict],
                      discover_by: str | None = None, limit_per_input: int | None = None,
                      snapshot_id: str | None = None, on_snapshot=None, **_ignored) -> list[dict]:
        if on_snapshot:
            res = on_snapshot(f"mock_snap_{kind}_{source}")
            if hasattr(res, "__await__"):
                await res
        if kind == "discover":
            return self._discover(inputs, limit_per_input)
        return self._reviews(source, inputs)

    def _discover(self, inputs, limit):
        out = []
        for inp in inputs:
            brand = inp.get("keyword") or inp.get("brand") or "Subway"
            lat = inp.get("lat", SEED_STATES[0][0])
            lon = inp.get("long", SEED_STATES[0][1])
            tag = f'{lat:.2f}_{lon:.2f}'
            category = "Sandwich shop" if brand.lower() == "subway" else "Fast food restaurant"
            rows = [{  # decoy: the transit station decoy
                "place_id": f"mock_transit_{tag}", "name": f"{brand} Station", "category": "Transit station",
                "url": f"https://www.google.com/maps/place/{brand}+Station/@{lat},{lon},17z/mock_t",
                "address": "Transit Ave", "rating": 4.1, "reviews_count": 900, "lat": lat, "lon": lon}]
            for i in range(6):
                r = _rng(f"{tag}{i}")
                rows.append({
                    "place_id": f"mock_place_{tag}_{i}", "name": brand, "category": category,
                    "url": f"https://www.google.com/maps/place/{brand}/@{lat},{lon},17z/mock_{tag}_{i}",
                    "address": f"{100 + i} Main St, {_where(lat, lon)}", "rating": round(3 + r.random() * 1.5, 1),
                    "reviews_count": r.randint(40, 400), "lat": lat + i * 0.01,
                    "lon": lon + i * 0.01, "country_code": inp.get("country", "US")})
            out += rows[:limit] if limit else rows
        return out

    def _reviews(self, source, inputs):
        out = []
        for inp in inputs:
            url = inp["url"]
            brand = _detect_brand(inp, url)
            neg_pool, pos_pool, neu_pool = _get_brand_texts(brand)
            r = _rng(url)
            days = inp.get("days_limit") or 365
            place_id = "mock_" + hashlib.md5(url.encode()).hexdigest()[:8]
            m = re.search(r"@(-?\d+\.\d+),(-?\d+\.\d+)", url)
            addr = f"100 Main St, {_where(float(m.group(1)), float(m.group(2)))}" if m else "100 Main St"
            requested_max = inp.get("max_reviews")
            n = min(requested_max, 100) if requested_max else r.randint(15, 30)
            for i in range(n):
                rating = r.choices([1, 2, 3, 4, 5], weights=[18, 14, 12, 26, 30])[0]
                text = r.choice(neg_pool if rating <= 2 else neu_pool if rating == 3 else pos_pool)
                when = date.today() - timedelta(days=r.randint(0, days))
                if source == "google_maps":
                    out.append({
                        "url": url, "place_id": place_id, "place_name": brand,
                        "address": addr, "review_id": f"{place_id}-{i}",
                        "reviewer_name": f"Reviewer {r.randint(1, 999)}", "review_rating": rating,
                        "review": text, "review_date": when.isoformat(),
                        "number_of_likes": r.randint(0, 5),
                        "response_of_owner": "Thanks for the feedback!" if r.random() < 0.2 else None})
                else:  # tolerant provider keys
                    out.append({"url": url, "id": f"{place_id}-{i}", "rating": rating,
                                "title": "Visit", "text": text, "published_date": when.isoformat(),
                                "author": f"user{r.randint(1, 999)}", "name": brand, "place_name": brand})
            if n > 2:  # duplicate the first record: must be de-duplicated downstream
                out.append(dict(out[-n]))
            out.append({"url": url, "error": "dead_page", "error_code": "dead_page"})
        return out
