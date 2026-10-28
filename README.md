# Subway Review Intelligence (FastAPI + Bright Data)

Collects brand-level customer reviews for Subway through Bright Data and exports them to Excel
(Summary with live formulas, Reviews, Places, Run Info). Also CSV and a JSON query API.

## Run it (2 minutes, zero credits)

```bash
# 1. Create a virtual environment
python -m venv .venv

# 2. Activate it (run the correct command for your OS)
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# Mac/Linux:
# source .venv/bin/activate

# 3. Install dependencies and start the backend
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```

**Swagger flow:** `POST /api/v1/runs` (try the default body) -> copy `run_id` ->
`GET /api/v1/runs/{run_id}` until `status` is `completed` -> `GET /api/v1/runs/{run_id}/export.xlsx`.
Mock mode returns synthetic data in Bright Data's real field names, so the whole path is testable
before you spend a credit.

## Run the Streamlit app

Start the API first, then start Streamlit from the project root in a second terminal:

```powershell
uvicorn app.main:app --reload
streamlit run app/ui/app.py
```

The Streamlit app defaults new studies to Google Maps, which is the configured live source.
Tripadvisor and Yelp can be selected when compatible Bright Data datasets are configured. The
app resumes interrupted/failed runs, starts the offline lexicon analysis automatically when
reviews are available (including partial results), and provides a real Excel download button
after study insights are ready. A study may be `completed_with_warnings` when an optional source
or full review snapshot is unavailable; the successfully collected reviews are still included
in the workbook.

## Go live

1. Bright Data account -> create an API key (Account settings -> Users). Put it in `.env` as
   `BRIGHTDATA_API_KEY`. New accounts get 5,000 free credits/month, no card needed.
2. Run `GET /api/v1/health`: it must say `LIVE`.
3. Start tiny: `max_places: 3`, `days_limit: 14`. Then call `GET /runs/{id}/raw-sample` and
   confirm the field names. **Do this before any bigger run.**
4. Tripadvisor / Yelp: find and verify the review dataset IDs with
   `GET /api/v1/brightdata/scrapers?q=tripadvisor` (or `q=yelp`) and pin them in `.env`
   (`BD_DATASET_TRIPADVISOR`, `BD_DATASET_YELP`). If no URLs are supplied, the live pipeline
   uses the catalogue's keyword-discovery scraper to find real place URLs. Explicit
   `tripadvisor_urls` / `yelp_urls` can still be supplied when you already have a known set.

## How "brand-level" works

Bright Data's review scrapers are URL-driven. For Google Maps the app first **discovers** Subway
places by keyword around seed metros (default: NYC, LA, Chicago, Houston, Miami; override with
`seeds`), filters out non-brand hits, then pulls reviews for up to `max_places` stores.
The keyword "Subway" also returns **transit stations**; they are filtered by name/category.
For Tripadvisor and Yelp, live runs resolve the catalogue's advertised keyword-discovery
dataset and use it to find real place URLs; the app never constructs or guesses URLs. If the
catalogue has no compatible discovery scraper, the source is skipped with an actionable error.

## Verified vs not verified

| Item | Status |
|---|---|
| Async flow (trigger -> progress -> snapshot), 429 handling, discovery params | From Bright Data docs; client tested against a simulated API |
| Google Maps dataset IDs and review/place field names | From Bright Data docs |
| Tripadvisor / Yelp dataset IDs, input and output fields | Dataset IDs are auto-resolved from `scrapers.json` when not pinned; mapping is tolerant and raw records are always stored |
| Live calls against Bright Data | **Not executed** (no key/network in the build sandbox). First live run is your smoke test |

## Cost and safety guards

- Billed per delivered record (reviews **and** discovered places). Failed-input records still bill.
- `days_limit` is required unless `allow_full_history=true`. `max_places` has a hard cap (`MAX_PLACES_HARD_CAP`).
- `max_reviews_per_place` sends Bright Data a per-store record limit to ensure scrapers finish in minutes and avoid hanging or excessive billing.
- `POST /runs/{id}/resume` re-polls already-triggered snapshots instead of re-triggering (no double billing).
  Snapshots stay downloadable for 30 days.
- Bright Data polling is bounded by `BD_POLL_TIMEOUT` (180 seconds by default, polled every 5s). A snapshot that
  takes longer automatically preserves embedded discovery reviews so the run completes with output immediately.
  Resume anytime to pull the full review snapshot once ready.

## Failure modes

| Symptom | Cause / fix |
|---|---|
| `rejected the API key` | Wrong key, or Scraper API not enabled on the account |
| HTTP 429 | Concurrency limit; client backs off. Never hammer: 25 x 429 in 5 min blacklists your IP |
| Run `interrupted` | Server restarted mid-run; call `/resume` |
| Source `skipped` | Missing URLs or dataset ID (message says which) |
| Many "error records skipped" | Dead/invalid place URLs; they are billed |
| Blank Summary in a previewer | Formulas calculate when opened in Excel/LibreOffice/Google Sheets; use the CSV for pandas |

## Legal and privacy

Reviews are public but author names are personal data (GDPR/CCPA). Get InvoLead legal sign-off
before production, especially for client-facing use. Scraping Google Maps/Tripadvisor/Yelp can
conflict with those platforms' terms; Bright Data's compliance posture does not transfer that risk to you.
CSV export escapes leading `= + - @` to block spreadsheet formula injection.

## Sentiment by menu item + sales join

Bright Data provides review **text** only. Item/aspect sentiment is computed here; the sales data is yours.

1. `POST /api/v1/runs/{id}/analyze` with `{"method":"lexicon"}` (free, offline baseline) or
   `{"method":"llm"}` (Anthropic API, better quality, costs tokens, sends public review text
   to a third party; needs `ANTHROPIC_API_KEY`; test with `"limit": 200` first).
2. `GET /api/v1/runs/{id}/insights/items` -> mentions, avg sentiment (-1..1), negative share, top aspects per item.
3. `GET/PUT /api/v1/menu` -> the item catalogue and aliases. **The default list is a starting guess, not a
   verified current Subway menu.** Canonical names must match your sales CSV `item` column (or an alias).
4. `POST /api/v1/sales/upload` a CSV: `item, region (US state code), period (date), units, [revenue]`
   (see `docs/sales_template.csv`). Unmatched items and bad rows are reported, not silently dropped.
5. `GET /api/v1/runs/{id}/insights/sales-join?sales_id=...&level=month|week` -> per item x state x period:
   sentiment vs `units_index` (item units / mean units of all items in that state+period), per-item
   correlation, and **candidate drivers**: aspects where an under-indexing item is more negative than
   the same aspect across all items.
6. `GET /api/v1/runs/{id}/insights/export.xlsx?sales_id=...` -> Items, Aspect Matrix, Sales Join, Drivers, Notes.

Limits you must respect: join grain is **US state** (store address); DMA needs a zip->DMA lookup you supply.
Dollar fields are excluded unless `include_revenue=true` (treat revenue as finance-only). Reviews are a biased
sample, so drivers are hypotheses to test, not proof. Treat items with < 10 mentions or < 8 joined periods as noise.

## Layout

```
app/config.py     settings, verified dataset IDs, default seeds
app/brightdata.py async client + dataset resolver
app/pipeline.py   discover -> collect -> normalize -> store (idempotent jobs)
app/normalize.py  raw -> unified schema, transit/brand filter
app/excel.py      workbook + CSV export
app/mock.py       offline provider
app/menu.py       menu catalogue + aspect vocabulary
app/analyzers.py  lexicon + LLM aspect sentiment
app/insights.py   item summaries, sales ingest, join, drivers
tests/            pytest (10 tests)
```
