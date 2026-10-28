"""Aspect-based sentiment per menu item. Bright Data supplies review text only; this runs on our side.

lexicon : free, offline, deterministic. Crude (word lists + negation), good for a baseline/QA.
llm     : Anthropic Messages API, much better on sarcasm and multi-item sentences. Costs tokens and
          sends public review text (no author names) to a third party.
Every mention: item (canonical menu name or "(general)"), aspect, sentiment in [-1, 1], quote.
"""
from __future__ import annotations

import asyncio
import json
import re

import httpx

from .menu import ASPECTS, GENERAL, compile_menu, detect_aspects, match_items

POS = set("""great good love loved loves fresh friendly fast quick delicious perfect best amazing clean tasty
excellent generous nice awesome value wonderful fantastic yummy favorite favourite helpful happy""".split())
NEG = set("""bad terrible awful stale rude slow dirty wrong missing tiny expensive overpriced gross bland worst cold
soggy disappointing disappointment horrible forgot stingy sticky filthy dry disgusting waste never mess
unfriendly lazy""".split())
NEGATORS = {"not", "no", "never", "without", "hardly", "barely", "wasn't", "isn't", "didn't", "don't"}

LLM_BATCH = 15
ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANALYSIS_VERSION = 1


def split_sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+|\s+but\s+", text or "")
    return [p.strip() for p in parts if p and p.strip()]


def score_sentence(sent: str) -> tuple[int, int]:
    pos = neg = left = 0
    for tok in re.findall(r"[a-z']+", sent.lower()):
        if tok in NEGATORS or tok.endswith("n't"):
            left = 3
            continue
        pol = 1 if tok in POS else -1 if tok in NEG else 0
        if pol:
            if left > 0:
                pol = -pol
            pos, neg = (pos + 1, neg) if pol > 0 else (pos, neg + 1)
        left = max(0, left - 1)
    return pos, neg


class LexiconAnalyzer:
    name = "lexicon"
    version = ANALYSIS_VERSION

    async def analyze(self, reviews: list[dict], menu: dict, progress=None) -> list[dict]:
        compiled = compile_menu(menu)
        out: list[dict] = []
        for n, r in enumerate(reviews, 1):
            prev_items: list[str] = []
            for sent in split_sentences(r["text"]):
                explicit = match_items(sent, compiled)
                pos, neg = score_sentence(sent)
                items = explicit or (prev_items if (pos + neg) > 0 else [])
                prev_items = explicit
                aspects = detect_aspects(sent)
                if not (pos + neg) and not (explicit or aspects):
                    continue
                if pos + neg:
                    score, basis = (pos - neg) / (pos + neg), "text"
                elif r.get("rating") is not None:
                    score, basis = (r["rating"] - 3) / 2, "rating"
                else:
                    continue
                for item in items or [GENERAL]:
                    for aspect in aspects or ["general"]:
                        out.append({"review_id": r["review_id"], "source": r["source"], "item": item,
                                    "aspect": aspect, "sentiment": round(score, 3), "quote": sent[:160],
                                    "basis": basis, "method": self.name})
            if progress and n % 200 == 0:
                progress(n)
        return out


SYSTEM = ("You extract aspect-based sentiment from restaurant customer reviews. Reply with ONLY a JSON "
          "array, no prose, no markdown fences.")

import hashlib
from pathlib import Path

def _get_cache_dir() -> Path:
    p = Path("data/llm_cache")
    p.mkdir(parents=True, exist_ok=True)
    return p

class LLMAnalyzer:
    name = "llm"
    version = ANALYSIS_VERSION

    def __init__(self, api_key: str, model: str):
        self.key, self.model = api_key, model
        self.failed_batches = 0
        self.cache_dir = _get_cache_dir()

    def _prompt(self, batch: list[dict], menu: dict) -> str:
        items = ", ".join(menu)
        reviews = "\n".join(f'[{r["review_id"]}] (rating {r.get("rating")}) {r["text"][:700]}' for r in batch)
        return (f"Menu items: {items}\nAspects: {', '.join(ASPECTS)}\n\n"
                "For each review list every menu item it discusses (use the exact menu name; use "
                f'"{GENERAL}" for brand/store-level comments) with one aspect each and sentiment from -1 '
                "(very negative) to 1 (very positive), plus a quote of at most 12 words from the review.\n"
                'Schema: [{"id":"<review id>","mentions":[{"item":"...","aspect":"...","sentiment":0.0,"quote":"..."}]}]\n'
                f"Reviews:\n{reviews}")

    async def _call(self, http: httpx.AsyncClient, prompt: str) -> str:
        h = hashlib.md5((self.model + prompt).encode()).hexdigest()
        cf = self.cache_dir / f"{h}.json"
        if cf.exists():
            return cf.read_text()
            
        body = {"model": self.model, "max_tokens": 4000, "system": SYSTEM,
                "messages": [{"role": "user", "content": prompt}]}
        headers = {"x-api-key": self.key, "anthropic-version": "2023-06-01", "content-type": "application/json"}
        for attempt in range(4):
            r = await http.post(ANTHROPIC_URL, headers=headers, json=body)
            if r.status_code in (429, 500, 529):
                await asyncio.sleep(int(r.headers.get("retry-after") or 2 ** attempt))
                continue
            if r.status_code in (401, 403):
                raise RuntimeError(f"Anthropic rejected ANTHROPIC_API_KEY (HTTP {r.status_code})")
            r.raise_for_status()
            res = "".join(b.get("text", "") for b in r.json().get("content", []))
            cf.write_text(res)
            return res
        raise RuntimeError("Anthropic API kept returning 429/5xx")

    async def _batch(self, http, batch, menu, compiled, sem) -> list[dict]:
        by_id = {r["review_id"]: r for r in batch}
        async with sem:
            try:
                txt = await self._call(http, self._prompt(batch, menu))
                data = json.loads(txt[txt.index("["): txt.rindex("]") + 1])
            except RuntimeError:
                raise
            except Exception:
                self.failed_batches += 1
                return []
        out = []
        for entry in data:
            r = by_id.get(str(entry.get("id")))
            if not r:
                continue
            for m in entry.get("mentions", []):
                item = m.get("item") if m.get("item") in menu else (match_items(str(m.get("item")), compiled) or [GENERAL])[0]
                aspect = m.get("aspect") if m.get("aspect") in ASPECTS else "general"
                try:
                    s = max(-1.0, min(1.0, float(m.get("sentiment"))))
                except (TypeError, ValueError):
                    continue
                out.append({"review_id": r["review_id"], "source": r["source"], "item": item, "aspect": aspect,
                            "sentiment": round(s, 3), "quote": str(m.get("quote", ""))[:160],
                            "basis": "llm", "method": self.name})
        return out

    async def analyze(self, reviews: list[dict], menu: dict, progress=None) -> list[dict]:
        compiled, sem = compile_menu(menu), asyncio.Semaphore(3)
        batches = [reviews[i:i + LLM_BATCH] for i in range(0, len(reviews), LLM_BATCH)]
        done = 0
        async with httpx.AsyncClient(timeout=120) as http:
            async def run(b):
                nonlocal done
                res = await self._batch(http, b, menu, compiled, sem)
                done += len(b)
                if progress:
                    progress(done)
                return res
            results = await asyncio.gather(*(run(b) for b in batches))
        if batches and self.failed_batches == len(batches):
            raise RuntimeError("LLM returned unparseable output for every batch")
        return [m for res in results for m in res]
