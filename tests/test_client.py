"""Live-client logic tested against a simulated Bright Data API (no network, no credits)."""
import asyncio
import json

import httpx
import pytest

from app import brightdata
from app.config import Settings


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setenv("BRIGHTDATA_API_KEY", "test")
    monkeypatch.setenv("BD_MOCK", "false")
    monkeypatch.setenv("BD_POLL_INTERVAL", "0")
    return Settings()


def _patch(monkeypatch, handler):
    real = httpx.AsyncClient
    monkeypatch.setattr(brightdata.httpx, "AsyncClient",
                        lambda **kw: real(transport=httpx.MockTransport(handler), **kw))


def test_trigger_poll_download_with_429_and_202(monkeypatch, settings):
    calls = {"progress": 0, "snapshot": 0, "trigger": 0, "bodies": [], "params": []}

    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == "Bearer test"
        path = req.url.path
        if path.endswith("/trigger"):
            calls["trigger"] += 1
            calls["params"].append(dict(req.url.params))
            calls["bodies"].append(json.loads(req.content))
            if calls["trigger"] == 1:  # first attempt rate-limited; Retry-After honoured
                return httpx.Response(429, headers={"Retry-After": "0"})
            return httpx.Response(200, json={"snapshot_id": "sd_1"})
        if "/progress/" in path:
            calls["progress"] += 1
            return httpx.Response(200, json={"status": "running" if calls["progress"] < 3 else "ready"})
        if "/snapshot/" in path:
            calls["snapshot"] += 1
            if calls["snapshot"] == 1:  # still digesting
                return httpx.Response(202, json={"status": "building"})
            return httpx.Response(200, json=[{"review_id": "a", "review": "ok", "review_rating": 5}])
        return httpx.Response(404)

    _patch(monkeypatch, handler)
    saved = []
    out = asyncio.run(brightdata.BrightDataClient(settings).collect(
        source="google_maps", kind="discover", dataset_id="gd_m8ebnr0q2qlklc02fz",
        inputs=[{"country": "US", "lat": 1, "long": 2, "zoom_level": 12, "keyword": "Subway"}],
        discover_by="location", limit_per_input=3, on_snapshot=saved.append))
    assert out == [{"review_id": "a", "review": "ok", "review_rating": 5}]
    assert saved == ["sd_1"]
    assert calls["trigger"] == 2 and calls["progress"] == 3 and calls["snapshot"] == 2
    p = calls["params"][-1]
    assert p["type"] == "discover_new" and p["discover_by"] == "location" and p["include_errors"] == "true"
    assert calls["bodies"][-1]["limit_per_input"] == 3 and "input" in calls["bodies"][-1]


def test_collect_by_url_sends_array_body(monkeypatch, settings):
    bodies = []

    def handler(req):
        if req.url.path.endswith("/trigger"):
            bodies.append((json.loads(req.content), dict(req.url.params)))
            return httpx.Response(200, json={"snapshot_id": "sd_2"})
        if "/progress/" in req.url.path:
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(200, json=[])

    _patch(monkeypatch, handler)
    asyncio.run(brightdata.BrightDataClient(settings).collect(
        source="google_maps", kind="reviews", dataset_id="gd_luzfs1dn2oa0teb81",
        inputs=[{"url": "https://www.google.com/maps/place/X", "days_limit": 30}]))
    body, params = bodies[0]
    assert body == [{"url": "https://www.google.com/maps/place/X", "days_limit": 30}]
    assert "discover_by" not in params and params["dataset_id"] == "gd_luzfs1dn2oa0teb81"


def test_resume_does_not_retrigger(monkeypatch, settings):
    seen = []

    def handler(req):
        seen.append(req.url.path)
        if "/progress/" in req.url.path:
            return httpx.Response(200, json={"status": "ready"})
        return httpx.Response(200, json=[{"review_id": "z", "review": "x", "review_rating": 1}])

    _patch(monkeypatch, handler)
    out = asyncio.run(brightdata.BrightDataClient(settings).collect(
        source="google_maps", kind="reviews", dataset_id="x", inputs=[{"url": "u"}], snapshot_id="sd_old"))
    assert len(out) == 1 and not any(p.endswith("/trigger") for p in seen)


def test_auth_error_is_clear(monkeypatch, settings):
    _patch(monkeypatch, lambda r: httpx.Response(401))
    with pytest.raises(brightdata.BrightDataError, match="API key"):
        asyncio.run(brightdata.BrightDataClient(settings).collect(
            source="google_maps", kind="reviews", dataset_id="x", inputs=[{"url": "u"}]))


def test_stuck_snapshot_has_bounded_timeout(monkeypatch, settings):
    settings.poll_timeout = 0

    def handler(req):
        if req.url.path.endswith("/trigger"):
            return httpx.Response(200, json={"snapshot_id": "stuck"})
        return httpx.Response(200, json={"status": "running"})

    _patch(monkeypatch, handler)
    with pytest.raises(brightdata.BrightDataTransientError, match="not ready"):
        asyncio.run(brightdata.BrightDataClient(settings).collect(
            source="google_maps", kind="reviews", dataset_id="x", inputs=[{"url": "u"}]))
