from app.insights import _mean_and_var, study_insights
import math

def test_mean_and_var():
    xs = [2, 4, 4, 4, 5, 5, 7, 9]
    m, v = _mean_and_var(xs)
    assert m == 5.0
    assert math.isclose(v, 4.5714285714) # Sample variance of xs

def test_synthetic_study(monkeypatch):
    import app.db as db
    
    # We will mock the database calls in study_insights
    monkeypatch.setattr(db, "get_study", lambda sid: {
        "id": "st_123",
        "focus_brand": "Subway",
        "run_ids": ["run_subway", "run_mcd"]
    })
    
    monkeypatch.setattr(db, "get_run", lambda rid: {
        "run_subway": {"brand": "Subway"},
        "run_mcd": {"brand": "McDonald's"}
    }[rid])
    
    monkeypatch.setattr(db, "all_reviews", lambda rid: {
        "run_subway": [{"rating": 5}, {"rating": 3}, {"rating": 4}],
        "run_mcd": [{"rating": 2}, {"rating": 2}, {"rating": 3}, {"rating": 1}]
    }[rid])
    
    monkeypatch.setattr(db, "mentions_for", lambda rid: {
        "run_subway": [
            {"sentiment": 0.8, "review_date": "2026-09-01"},
            {"sentiment": 0.6, "review_date": "2026-09-15"},
            {"sentiment": -0.2, "review_date": "2026-10-01"}
        ],
        "run_mcd": [
            {"sentiment": -0.5, "review_date": "2026-09-05"},
            {"sentiment": -0.8, "review_date": "2026-09-20"},
            {"sentiment": 0.1, "review_date": "2026-10-05"},
            {"sentiment": -0.9, "review_date": "2026-10-10"}
        ]
    }[rid])
    
    res = study_insights("st_123")
    assert res["focus_brand"] == "Subway"
    assert len(res["brands"]) == 2
    
    subway = next(b for b in res["brands"] if b["brand"] == "Subway")
    mcd = next(b for b in res["brands"] if b["brand"] == "McDonald's")
    
    assert subway["reviews"] == 3
    assert subway["mentions"] == 3
    assert subway["mean_rating"] == 4.0
    
    assert mcd["reviews"] == 4
    assert mcd["mentions"] == 4
    assert mcd["mean_rating"] == 2.0
    
    # Delta should be McD - Subway
    # Rating Delta = 2.0 - 4.0 = -2.0
    assert mcd["delta_vs_focus"]["rating"] == -2.0
    
    # Sentiment Delta
    # Subway Sentiments: 0.8, 0.6, -0.2 -> mean = 0.4
    # McD Sentiments: -0.5, -0.8, 0.1, -0.9 -> mean = -0.525
    assert math.isclose(subway["mean_sentiment"], 0.4)
    assert math.isclose(mcd["mean_sentiment"], -0.525)
    
    assert math.isclose(mcd["delta_vs_focus"]["sentiment"], -0.925)
