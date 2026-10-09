import os
import subprocess
import sys
import uuid

import pytest
from fastapi.testclient import TestClient

import zoo

KEY = "zoo-test-key-0123456789abcdef"
PANEL = "http://localhost:5173"
SHOP = "https://angular-static.s4.zoo.sorv.dev"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INTERNAL_TOKEN", KEY)
    monkeypatch.setenv("ZOO_PANEL_ORIGIN", PANEL)
    monkeypatch.setenv("CORS_ORIGINS", SHOP)
    monkeypatch.setenv("PUBLIC_URL", "https://catalog-api.s2.zoo.sorv.dev")
    monkeypatch.setenv("DATABASE_URL", os.environ.get("TEST_DATABASE_URL", "postgres://u@127.0.0.1:1/none"))
    monkeypatch.setenv("REDIS_URL", os.environ.get("TEST_REDIS_URL", "redis://127.0.0.1:1/0"))
    import main
    return TestClient(main.app)


def signed(path, caller="mesh-shop", key=KEY, trace=None):
    h = {"X-Zoo-Signature": zoo.sign_header(key, caller, "GET", path)}
    if trace:
        h["X-Zoo-Trace"] = trace
    return h


def test_health_and_panel_cors(client):
    r = client.get("/_zoo/health", headers={"Origin": PANEL})
    assert r.status_code == 200 and r.json()["name"] == "catalog-api"
    assert r.headers["access-control-allow-origin"] == PANEL and r.headers["vary"] == "Origin"
    assert client.get("/_zoo/health", headers={"Origin": SHOP}).headers["access-control-allow-origin"] == SHOP
    assert "access-control-allow-origin" not in client.get("/_zoo/probe", headers={"Origin": SHOP}).headers
    assert "access-control-allow-origin" not in client.get("/_zoo/health", headers={"Origin": "https://evil.example"}).headers
    pre = client.options("/_zoo/probe", headers={"Origin": PANEL, "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204 and pre.headers["access-control-max-age"] == "600"


def test_api_cors_uses_cors_origins(client):
    pre = client.options("/api/items", headers={"Origin": SHOP, "Access-Control-Request-Method": "GET"})
    assert pre.status_code == 204 and pre.headers["access-control-allow-origin"] == SHOP
    bad = client.options("/api/items", headers={"Origin": PANEL, "Access-Control-Request-Method": "GET"})
    assert bad.status_code == 204 and "access-control-allow-origin" not in bad.headers
    r = client.get("/api/items/nope", headers={"Origin": SHOP})
    assert r.status_code == 400 and r.headers["access-control-allow-origin"] == SHOP


def test_invalid_signature_is_401_never_public(client):
    assert client.get("/api/items/ZOO-1", headers={"X-Zoo-Signature": "garbage"}).json() == {"ok": False, "error": "bad format"}
    r = client.get("/api/items/ZOO-1", headers=signed("/api/items/ZOO-1", key="wrong-key"))
    assert r.status_code == 401 and r.json()["error"] == "bad signature"
    r = client.get("/api/items", headers=signed("/api/items", caller="laravel-jobs"))
    assert r.status_code == 401 and r.json()["error"] == "unknown caller"
    r = client.get("/api/items?x=1", headers=signed("/api/items"))
    assert r.status_code == 401 and r.json()["error"] == "bad signature"


def test_sku_and_trace_validation(client):
    assert client.get("/api/items/ZOO-1%27--").status_code == 400
    assert client.get("/api/items/zoo-1").status_code == 400
    assert client.get("/_zoo/trace/not-a-uuid").status_code == 400
    r = client.get("/api/items/ZOO-1", headers=signed("/api/items/ZOO-1", trace="not-a-uuid"))
    assert r.status_code == 400


def test_verify(client):
    r = client.get("/_zoo/verify")
    assert r.status_code == 401 and r.json() == {"ok": False, "error": "missing signature"}
    r = client.get("/_zoo/verify", headers=signed("/_zoo/verify", caller="sveltekit-ssr"))
    body = r.json()
    assert r.status_code == 200 and body["ok"] and body["key_fp"] == "915a" and body["caller"] == "sveltekit-ssr"
    assert body["public_url"] == "https://catalog-api.s2.zoo.sorv.dev" and body["verified_by"] == "INTERNAL_TOKEN"


needs_services = pytest.mark.skipif(not os.environ.get("TEST_DATABASE_URL") or not os.environ.get("TEST_REDIS_URL"),
                                    reason="set TEST_DATABASE_URL and TEST_REDIS_URL for the integration test")


@needs_services
def test_integration_migrate_lookup_probe(client):
    env = os.environ | {"DATABASE_URL": os.environ["TEST_DATABASE_URL"]}
    for _ in range(2):  # the seed is idempotent
        subprocess.run([sys.executable, "-m", "alembic", "upgrade", "head"], check=True, env=env)
    items = client.get("/api/items").json()
    assert items["count"] == 20 and {"sku", "name", "price_cents"} <= items["items"][0].keys()
    trace = str(uuid.uuid4())
    r = client.get("/api/items/ZOO-7", headers=signed("/api/items/ZOO-7", trace=trace))
    assert r.status_code == 200 and r.json()["sku"] == "ZOO-7" and {"name", "price_cents"} <= r.json().keys()
    hops = client.get(f"/_zoo/trace/{trace}").json()
    assert hops["found"] and hops["hops"][0]["step"] == "signed-lookup"
    unknown = str(uuid.uuid4())
    assert client.get(f"/_zoo/trace/{unknown}").json() == {"trace": unknown, "found": False, "hops": []}
    assert client.get("/api/items/ZOO-999").status_code == 404
    probe = client.get("/_zoo/probe").json()
    assert probe["ok"], probe
