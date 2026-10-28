import asyncio
from app.brightdata import fetch_scrapers

async def main():
    print("Fetching available Bright Data scrapers...")
    items = await fetch_scrapers()
    print(f"Found {len(items)} total scrapers.")
    
    hits = [i for i in items if "review" in i.get("name", "").lower() and "collect_by_url" in i.get("collection_methods", [])]
    print(f"\n{len(hits)} scrapers support URL-based review collection:")
    for h in hits:
        print(f" - {h['dataset_id']}: {h['name']} ({h.get('domain', 'No domain')})")

if __name__ == "__main__":
    asyncio.run(main())
