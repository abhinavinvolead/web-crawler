import sys
import httpx
import time
import argparse

def main(brand: str = "Subway", max_places: int = 1):
    print(f"Running smoke test for {brand}...")
    
    with httpx.Client(base_url="http://localhost:8000") as client:
        # Check health
        try:
            r = client.get("/api/v1/health")
            r.raise_for_status()
            print("API is up.")
        except Exception as e:
            print(f"API is down: {e}")
            sys.exit(1)
            
        # Start run
        r = client.post("/api/v1/runs", json={
            "brand": brand,
            "sources": ["google_maps"],
            "max_places": max_places,
            "days_limit": 30
        })
        r.raise_for_status()
        run_id = r.json()["run_id"]
        print(f"Started run {run_id}. Waiting for completion...")
        
        # Poll
        while True:
            r = client.get(f"/api/v1/runs/{run_id}")
            status = r.json()["status"]
            print(f"Status: {status}")
            if status in ("completed", "completed_with_warnings", "failed"):
                break
            time.sleep(2)
            
        print("Run finished.")
        # Check places
        places = client.get(f"/api/v1/runs/{run_id}/places").json()
        print(f"Discovered {len(places)} places.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--brand", default="Subway")
    parser.add_argument("--max-places", type=int, default=1)
    args = parser.parse_args()
    main(args.brand, args.max_places)
