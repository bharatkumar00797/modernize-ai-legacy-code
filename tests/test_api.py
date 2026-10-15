from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient

from modernize.api import create_app
from modernize.api.security import ApiKeyAuth, RateLimiter
from modernize.api.settings import ApiSettings
from modernize.config import Settings

DEV = ApiSettings(dev_mode=True, sync_runs=True)


def client(
    settings: ApiSettings = DEV,
    base_settings: Settings | None = None,
    **kwargs: Any,
) -> TestClient:
    return TestClient(create_app(settings, base_settings or Settings(), **kwargs))


def test_healthz_and_config() -> None:
    c = client()
    health = c.get("/healthz").json()
    assert health["status"] == "ok"
    cfg = c.get("/api/config").json()
    assert cfg["access_mode"] == "dev"
    assert "py2-style-utils" in cfg["examples"]
    assert "api_key" not in json.dumps(cfg).lower() or "MODERNIZE_API_KEY" not in json.dumps(cfg)


def test_provider_key_never_returned(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODERNIZE_API_KEY", "sk-secret-should-never-leak")
    c = client(ApiSettings(dev_mode=True, sync_runs=True), Settings.from_env())
    blob = json.dumps(c.get("/api/config").json()) + c.get("/openapi.json").text
    assert "sk-secret-should-never-leak" not in blob


def test_openapi_lists_run_routes() -> None:
    paths = client().get("/openapi.json").json()["paths"]
    assert "/api/runs" in paths
    assert "/api/runs/{run_id}/events" in paths
    assert "/api/runs/{run_id}/report.md" in paths
    assert "/api/runs/{run_id}/patch.diff" in paths


def test_examples_and_transforms() -> None:
    c = client()
    ids = [s["id"] for s in c.get("/api/examples").json()["examples"]]
    assert "insecure-config" in ids
    transforms = c.get("/api/transforms").json()["transforms"]
    assert any(t["id"] == "eval_to_ast_literal" for t in transforms)
    assert all(t["id"] != "model_diff" for t in transforms)


def test_sync_run_returns_report_and_events() -> None:
    res = client().post("/api/runs", json={"example": "old-api-helpers", "max_steps": 10})
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "finished"
    assert body["tests_green"] is True
    assert body["kept"] >= 1
    assert body["events"] and body["events"][0]["event"] == "analyzed"


def test_report_patch_and_events_cursor() -> None:
    c = client()
    created = c.post("/api/runs", json={"example": "py2-style-utils"}).json()
    rid = created["id"]
    page = c.get(f"/api/runs/{rid}/events", params={"since": 0}).json()
    assert page["done"] is True
    assert page["next_cursor"] == len(page["events"]) == created["event_count"]
    tail = c.get(f"/api/runs/{rid}/events", params={"since": page["next_cursor"]}).json()
    assert tail["events"] == []
    md = c.get(f"/api/runs/{rid}/report.md")
    assert md.headers["content-type"].startswith("text/markdown")
    assert "py2" in md.text.lower() or "smell" in md.text.lower()
    patch = c.get(f"/api/runs/{rid}/patch.diff")
    assert patch.status_code == 200
    assert "---" in patch.text or patch.text == ""
    assert c.get("/api/runs").json()["runs"][0]["id"] == rid


def test_async_mode_returns_202_then_finishes() -> None:
    c = client(ApiSettings(dev_mode=True))
    with c:
        res = c.post("/api/runs", json={"example": "insecure-config"})
        assert res.status_code == 202
        rid = res.json()["id"]
        detail = None
        for _ in range(200):
            detail = c.get(f"/api/runs/{rid}").json()
            if detail["state"] == "finished":
                break
            time.sleep(0.1)
        assert detail is not None and detail["tests_green"] is True


def test_public_demo_restrictions_and_ownership() -> None:
    c = client(ApiSettings(sync_runs=True))
    assert c.get("/api/config").json()["access_mode"] == "public-demo"
    res = c.post("/api/runs", json={"example": "py2-style-utils", "provider": "openai"})
    assert res.status_code == 403
    ok = c.post("/api/runs", json={"example": "old-api-helpers"})
    assert ok.status_code == 200 and ok.json()["provider"] == "mock"
    rid = ok.json()["id"]
    other = TestClient(c.app, client=("203.0.113.9", 5000))
    assert other.get(f"/api/runs/{rid}").status_code == 404
    assert other.get("/api/runs").json()["runs"] == []


def test_api_key_mode() -> None:
    c = client(ApiSettings(api_keys=("s3cret-key",), sync_runs=True))
    assert c.get("/healthz").status_code == 200
    assert c.get("/api/examples").status_code == 401
    assert c.get("/api/examples", headers={"X-API-Key": "wrong"}).status_code == 401
    assert c.get("/api/examples", headers={"X-API-Key": "s3cret-key"}).status_code == 200
    bearer = {"Authorization": "Bearer s3cret-key"}
    res = c.post("/api/runs", json={"example": "old-api-helpers"}, headers=bearer)
    assert res.status_code == 200
    assert c.get("/api/config", headers=bearer).json()["authenticated"] is True


def test_general_rate_limit() -> None:
    c = client(ApiSettings(dev_mode=True, rate_limit_per_minute=3))
    codes = [c.get("/api/transforms").status_code for _ in range(4)]
    assert codes == [200, 200, 200, 429]


def test_run_rate_limit_counts_only_valid_requests() -> None:
    c = client(ApiSettings(dev_mode=True, sync_runs=True, run_limit_per_minute=1))
    assert c.post("/api/runs", json={"example": "bad-id"}).status_code == 422
    assert c.post("/api/runs", json={"example": "old-api-helpers"}).status_code == 200
    res = c.post("/api/runs", json={"example": "old-api-helpers"})
    assert res.status_code == 429 and "retry-after" in res.headers


def test_body_limit() -> None:
    big = json.dumps({"example": "old-api-helpers", "pad": "x" * 70_000})
    res = client().post("/api/runs", content=big, headers={"Content-Type": "application/json"})
    assert res.status_code == 413


def test_security_headers_and_dashboard() -> None:
    c = client()
    page = c.get("/")
    assert page.status_code == 200 and "Modernize AI" in page.text
    csp = page.headers["content-security-policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp
    assert page.headers["x-frame-options"] == "DENY"
    assert c.get("/api/examples").headers["cache-control"] == "no-store"
    assert c.get("/static/app.js").status_code == 200
    js = c.get("/static/app.js").text
    assert "innerHTML" not in js
    assert "insertAdjacentHTML" not in js
    assert "document.write" not in js


def test_crashing_run_is_reported_as_error() -> None:
    def boom(*_: Any) -> Any:
        raise RuntimeError("provider exploded")

    res = client(run_fn=boom).post("/api/runs", json={"example": "old-api-helpers"})
    assert res.status_code == 200
    assert res.json()["state"] == "error" and "exploded" in res.json()["error"]


def test_queue_full_returns_503() -> None:
    import threading

    gate = threading.Event()

    def slow(project: Any, settings: Any, max_steps: Any, hook: Any) -> Any:
        gate.wait(5)
        raise RuntimeError("stopped")

    c = client(ApiSettings(dev_mode=True, max_queued_runs=1), run_fn=slow)
    with c:
        assert c.post("/api/runs", json={"example": "old-api-helpers"}).status_code == 202
        res = c.post("/api/runs", json={"example": "old-api-helpers"})
        assert res.status_code == 503
        gate.set()


def test_api_key_auth_and_rate_limiter_units() -> None:
    auth = ApiKeyAuth(("a", "b"))
    assert auth.enabled and auth.verify("b") and auth.verify("c") is None
    assert auth.verify(None) is None
    now = [0.0]
    limiter = RateLimiter(2, 10, clock=lambda: now[0])
    assert limiter.check("x") is None and limiter.check("x") is None
    assert limiter.check("x") == pytest.approx(10)
    now[0] = 10.5
    assert limiter.check("x") is None


def test_settings_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MODERNIZE_API_KEYS", "k1, k2")
    monkeypatch.setenv("VERCEL", "1")
    s = ApiSettings.from_env()
    assert s.api_keys == ("k1", "k2") and s.access_mode == "api-key" and s.sync_runs


def test_cli_serve_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    import uvicorn

    from modernize import cli

    seen: dict[str, Any] = {}
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: seen.update(kw))
    monkeypatch.setenv("PORT", "9123")
    assert cli.main(["serve", "--dev"]) == 0
    assert seen["port"] == 9123 and seen["host"] == "127.0.0.1"


def test_extra_fields_forbidden() -> None:
    assert (
        client().post("/api/runs", json={"example": "old-api-helpers", "extra": 1}).status_code
        == 422
    )


def test_public_demo_run_visible_when_proxy_hop_changes() -> None:
    # Render's edge appends a different internal hop to X-Forwarded-For on each request.
    api = client(ApiSettings(trust_proxy=True, sync_runs=True))
    created = api.post(
        "/api/runs",
        json={"example": "old-api-helpers"},
        headers={"X-Forwarded-For": "203.0.113.7, 10.0.0.1"},
    )
    assert created.status_code == 200
    run_id = created.json()["id"]
    hop = {"X-Forwarded-For": "203.0.113.7, 10.0.0.2"}
    assert api.get(f"/api/runs/{run_id}", headers=hop).status_code == 200
    assert api.get(f"/api/runs/{run_id}/report.md", headers=hop).status_code == 200
    runs = api.get("/api/runs", headers=hop).json()["runs"]
    assert [r["id"] for r in runs] == [run_id]
    other = {"X-Forwarded-For": "198.51.100.9, 10.0.0.1"}
    assert api.get(f"/api/runs/{run_id}", headers=other).status_code == 404
