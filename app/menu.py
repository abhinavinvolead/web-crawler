"""Menu catalogue + aspect vocabulary.

DEFAULT_MENU is a starting point written from general knowledge, NOT a verified current Subway
menu. Edit it with PUT /api/v1/menu (stored next to the DB); canonical names must match the
`item` column of your sales CSV (or one of their aliases).
"""
from __future__ import annotations

import json
import re
import yaml
from pathlib import Path

from .config import get_settings

GENERAL = "(general)"

DEFAULT_MENU: dict[str, list[str]] = {
    "Italian B.M.T.": ["bmt", "b.m.t", "italian bmt", "italian b.m.t."],
    "Spicy Italian": ["spicy italian"],
    "Meatball Marinara": ["meatball", "meatball marinara"],
    "Turkey Breast": ["turkey breast", "turkey sub", "turkey sandwich"],
    "Chicken Teriyaki": ["teriyaki"],
    "Steak & Cheese": ["steak and cheese", "steak & cheese", "cheesesteak", "philly"],
    "Tuna": ["tuna"],
    "Veggie Delite": ["veggie delite", "veggie sub", "veggie sandwich"],
    "Cold Cut Combo": ["cold cut"],
    "Rotisserie-Style Chicken": ["rotisserie"],
    "Subway Series": ["subway series"],
    "Footlong": ["footlong", "foot long", "foot-long"],
    "Cookies": ["cookie"],
    "Breakfast": ["breakfast", "egg and cheese", "egg & cheese"],
    "Wraps": ["wrap"],
    "Salads": ["salad"],
    "Soup": ["soup"],
}

ASPECT_KEYWORDS: dict[str, list[str]] = {
    "taste": ["taste", "tasted", "flavor", "flavour", "delicious", "bland", "tasty", "gross", "dry", "salty"],
    "bread": ["bread", "bun", "toasted", "stale", "roll"],
    "freshness": ["fresh", "stale", "soggy", "wilted", "old", "frozen", "veggies", "lettuce"],
    "portion": ["portion", "skimpy", "tiny", "generous", "stingy", "meat", "filling", "small"],
    "price_value": ["price", "prices", "expensive", "cheap", "value", "deal", "cost", "overpriced", "pay", "$"],
    "service_staff": ["staff", "employee", "employees", "rude", "friendly", "service", "manager", "worker", "cashier"],
    "wait_time": ["wait", "waited", "slow", "quick", "fast", "line", "minutes", "forever"],
    "cleanliness": ["clean", "dirty", "sticky", "filthy", "mess", "tables", "floor", "bathroom"],
    "order_accuracy": ["wrong", "missing", "forgot", "mistake", "incorrect", "ordered"],
    "delivery_app": ["delivery", "app", "doordash", "uber", "grubhub", "online", "pickup"],
}
ASPECTS = list(ASPECT_KEYWORDS) + ["general"]


def load_brands_config() -> dict:
    f = Path(__file__).parent / "brands.yaml"
    if not f.exists():
        return {}
    with open(f) as fp:
        return yaml.safe_load(fp).get("brands", {})


def _menu_file(brand: str) -> Path:
    b = brand.lower().replace("'", "").replace(" ", "_")
    return Path(get_settings().db_path).parent / f"menu_{b}.json"


def load_menu(brand: str = "Subway") -> dict[str, list[str]]:
    f = _menu_file(brand)
    if f.exists():
        return json.loads(f.read_text())
    config = load_brands_config()
    b_conf = config.get(brand)
    if not b_conf:
        # Case-insensitive lookup or alias match
        b_low = brand.strip().lower()
        for k, v in config.items():
            if k.lower() == b_low:
                b_conf = v
                break
            aliases = [a.lower() for a in v.get("aliases", [])]
            if b_low in aliases:
                b_conf = v
                break
    if b_conf and "menu" in b_conf:
        return b_conf["menu"]
    return DEFAULT_MENU if brand.lower() == "subway" else {}



def save_menu(brand: str, menu: dict[str, list[str]]) -> None:
    _menu_file(brand).write_text(json.dumps(menu, indent=2))


def compile_menu(menu: dict[str, list[str]]) -> list[tuple[str, re.Pattern]]:
    out = []
    for canonical, aliases in menu.items():
        names = {canonical, *aliases}
        pat = "|".join(re.escape(n.lower()) for n in sorted(names, key=len, reverse=True))
        out.append((canonical, re.compile(rf"(?<![a-z])(?:{pat})s?(?![a-z])", re.I)))
    return out


def match_items(text: str, compiled: list[tuple[str, re.Pattern]]) -> list[str]:
    return [name for name, rx in compiled if rx.search(text or "")]


def detect_aspects(text: str) -> list[str]:
    low = (text or "").lower()
    tokens = set(re.findall(r"[a-z$']+|\$", low))
    return [a for a, kws in ASPECT_KEYWORDS.items() if any((k in tokens) or (k == "$" and "$" in low) for k in kws)]
