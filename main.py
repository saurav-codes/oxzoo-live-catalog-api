"""catalog-api: a FastAPI catalog over Postgres with a Redis read-through
cache. Public reads use CORS_ORIGINS; a request carrying X-Zoo-Signature
must verify with INTERNAL_TOKEN (mesh-shop, sveltekit-ssr) or gets 401."""

import logging
import os
import re
import secrets

from fastapi import FastAPI, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import text

import db
import zoo

NAME = "catalog-api"
STACK = "FastAPI + SQLAlchemy 2 + Alembic"
CALLERS = {"mesh-shop", "sveltekit-ssr"}
SKU_RE = re.compile(r"^ZOO-[0-9]{1,6}$")

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
app = FastAPI(title=NAME, docs_url=None, redoc_url=None, openapi_url=None)


def cors_envs(path: str) -> tuple[str, ...]:
    """The origin lists a path answers CORS for. Health also allows the
    CORS_ORIGINS sites (angular-static's selftest reads it)."""
    if path == "/_zoo/health":
        return ("ZOO_PANEL_ORIGIN", "CORS_ORIGINS")
    if path.startswith("/_zoo/"):
        return () if path == "/_zoo/verify" else ("ZOO_PANEL_ORIGIN",)
    if path.startswith("/api/"):
        return ("CORS_ORIGINS",)
    return ()


def first_headers(fn, origin: str | None, envs: tuple[str, ...]) -> dict[str, str]:
    for env in envs:
        if h := fn(origin, env):
            return h
    return {}


@app.middleware("http")
async def cors(request: Request, call_next):
    envs = cors_envs(request.url.path)
    origin = request.headers.get("origin")
    if envs and request.method == "OPTIONS":
        return Response(status_code=204, headers=first_headers(zoo.preflight_headers, origin, envs))
    response = await call_next(request)
    response.headers.update(first_headers(zoo.cors_headers, origin, envs))
    return response


def raw_target(request: Request) -> str:
    path = request.scope.get("raw_path") or request.url.path.encode()
    query = request.scope.get("query_string") or b""
    return path.decode("latin-1") + ("?" + query.decode("latin-1") if query else "")


async def capped_body(request: Request) -> bytes | None:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > zoo.MAX_BODY:
            return None
    return body


async def signed_caller(request: Request) -> str | JSONResponse:
    """The verified caller, or the 401 to send."""
    body = await capped_body(request)
    if body is None:
        return JSONResponse({"ok": False, "error": "body too large"}, status_code=413)
    try:
        return zoo.verify(request.headers.get("x-zoo-signature"), request.method, raw_target(request), body,
                          os.environ.get("INTERNAL_TOKEN", ""), CALLERS)
    except zoo.SigError as e:
        return JSONResponse({"ok": False, "error": e.reason}, status_code=401)


async def signed_hop(request: Request, what: str) -> JSONResponse | None:
    """For a signed request: verify it, record signed-lookup under its trace.
    None for a public request or a recorded one; else the error to send."""
    if "x-zoo-signature" not in request.headers:
        return None
    caller = await signed_caller(request)
    if isinstance(caller, JSONResponse):
        return caller
    trace = request.headers.get("x-zoo-trace")
    if trace is not None:
        if not zoo.valid_trace(trace):
            return JSONResponse({"error": "bad trace id"}, status_code=400)
        await run_in_threadpool(db.record_hop, trace, "signed-lookup", f"{caller} looked up {what}")
    return None


@app.get("/api/items")
async def items(request: Request):
    if (err := await signed_hop(request, "all items")) is not None:
        return err
    rows, cache = await run_in_threadpool(db.list_items)
    return JSONResponse({"items": rows, "count": len(rows)}, headers={"X-Cache": cache})


@app.get("/api/items/{sku}")
async def item(sku: str, request: Request):
    if not SKU_RE.fullmatch(sku):
        return JSONResponse({"error": "sku must look like ZOO-1"}, status_code=400)
    if (err := await signed_hop(request, sku)) is not None:
        return err
    row, cache = await run_in_threadpool(db.get_item, sku)
    if row is None:
        return JSONResponse({"error": "not found", "sku": sku}, status_code=404)
    return JSONResponse(row, headers={"X-Cache": cache})


@app.get("/", response_class=HTMLResponse)
async def index():
    rows, cache = await run_in_threadpool(db.list_items)
    lines = "".join(f"<tr><td>{r['sku']}</td><td>{r['name']}</td><td>{r['price_cents'] / 100:.2f}</td>"
                    f"<td>{r['stock']}</td></tr>" for r in rows)
    return (f"<!doctype html><title>{NAME}</title><h1>{NAME}</h1><p>{len(rows)} items, cache {cache}. "
            f"JSON: <a href='/api/items'>/api/items</a></p><table><tr><th>SKU</th><th>Name</th><th>Price</th>"
            f"<th>Stock</th></tr>{lines}</table>")


# ---- zoo contract -------------------------------------------------------


@app.get("/_zoo/health")
async def health():
    return zoo.health(NAME, STACK)


@app.get("/_zoo/verify")
async def verify(request: Request):
    caller = await signed_caller(request)
    if isinstance(caller, JSONResponse):
        return caller
    return zoo.identity(NAME, STACK) | {
        "ok": True, "public_url": os.environ.get("PUBLIC_URL", ""), "verified_by": "INTERNAL_TOKEN",
        "key_fp": zoo.fp(os.environ["INTERNAL_TOKEN"]), "caller": caller,
    }


@app.get("/_zoo/trace/{trace}")
async def trace(trace: str):
    if not zoo.valid_trace(trace):
        return JSONResponse({"error": "bad trace id"}, status_code=400)
    rows = await run_in_threadpool(db.hops, trace)
    return zoo.trace_body(trace, [zoo.hop(h.at, h.step, h.detail) for h in rows])


def check_postgres() -> str:
    token = secrets.token_hex(8)
    with db.engine().begin() as c:
        row_id = c.execute(text("INSERT INTO zoo_probe (token) VALUES (:t) RETURNING id"), {"t": token}).scalar_one()
        got = c.execute(text("SELECT token FROM zoo_probe WHERE id = :id"), {"id": row_id}).scalar_one()
        c.execute(text("DELETE FROM zoo_probe WHERE id = :id"), {"id": row_id})
        version = c.execute(text("SHOW server_version")).scalar_one()
    if got != token:
        raise RuntimeError("read back a different token")
    return f"zoo_probe row round trip, server {version.split()[0]}"


def check_redis() -> str:
    r, key, token = db.cache_client(), f"zoo:probe:{secrets.token_hex(6)}", secrets.token_hex(8)
    r.set(key, token, ex=30)
    got, ttl = r.get(key), r.ttl(key)
    r.delete(key)
    if got != token.encode() or not 0 < ttl <= 30:
        raise RuntimeError(f"read back {got!r} with ttl {ttl}")
    return f"SET/GET/DEL with TTL {ttl}s"


def check_cache() -> str:
    db.cache_client().delete("catalog:item:ZOO-1")
    first, how1 = db.get_item("ZOO-1")
    second, how2 = db.get_item("ZOO-1")
    if first is None or first != second or (how1, how2) != ("miss", "hit"):
        raise RuntimeError(f"expected miss then hit with the same item, got {how1}, {how2}")
    return f"ZOO-1 read through: {how1}, then {how2}"


prober = zoo.Prober(
    NAME, STACK,
    lambda: [
        zoo.Check("postgres", "Postgres write, read, delete", check_postgres, ["DATABASE_URL"]),
        zoo.Check("redis", "Redis SET/GET/DEL with TTL", check_redis, ["REDIS_URL"]),
        zoo.Check("cache", "Read-through item cache (miss, then hit)", check_cache, ["DATABASE_URL", "REDIS_URL"]),
    ],
    lambda: [zoo.var("INTERNAL_TOKEN", "verifies"), zoo.var("DATABASE_URL", "service"), zoo.var("REDIS_URL", "service"), zoo.var("CORS_ORIGINS", "plain"), zoo.var("ZOO_PANEL_ORIGIN", "plain")],
)


@app.get("/_zoo/probe")
async def probe():
    status, body = await run_in_threadpool(prober.run)
    return JSONResponse(body, status_code=status)
