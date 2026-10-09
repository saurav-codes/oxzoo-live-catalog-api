# catalog-api

Deployed with [ox](https://deploywithox.com): deploy a repo to your own server with one command, no Docker. [Docs](https://deploywithox.com/docs) · [Guide for this stack](https://deploywithox.com/docs/guides/fastapi)

**Live demo:** https://catalog-api.s2.zoo.sorv.dev

> **Role in the zoo:** project `catalog-api` of [oxzoo-live](https://github.com/saurav-codes/oxzoo-live-control/blob/main/zoo/README.md#projects), deployed with [ox](https://deploywithox.com) on server s2 at https://catalog-api.s2.zoo.sorv.dev. The contract it follows is [DESIGN.md](https://github.com/saurav-codes/oxzoo-live-control/blob/main/zoo/DESIGN.md).

FastAPI + SQLAlchemy 2 + Alembic over Postgres, with a Redis read-through
cache. It is the zoo's product catalog and the verifier for `INTERNAL_TOKEN`.

## What it proves

- Zero config: no ox.toml. ox detects the uvicorn start from `main.py`, the
  install from `uv.lock`, the migrate from `alembic.ini`, and the postgres and
  redis services from the lockfile.
- Alembic migrations (schema, then an idempotent seed of 20 items) run in
  the migrate step.
- A read-through cache: item reads go to Redis first (30 s TTL), and the
  `X-Cache` response header says `hit`, `miss` or `unavailable`.
- Signed calls: a request carrying `X-Zoo-Signature` must verify with
  `INTERNAL_TOKEN` (callers `mesh-shop`, `sveltekit-ssr`) or gets 401; it is
  never served as a public read. With a valid `X-Zoo-Trace` it records the
  `signed-lookup` hop (shop-order chain).

## API

- `GET /api/items`: `{"items":[{"sku","name","price_cents","stock",...}],"count":n}`.
- `GET /api/items/<sku>`: one item; SKUs match `^ZOO-[0-9]{1,6}$` (else 400), unknown 404.
- `GET /` a small HTML table of the catalog.
- CORS: `/api/*` for `CORS_ORIGINS`; `/_zoo/health` for `ZOO_PANEL_ORIGIN`
  and `CORS_ORIGINS`; the other `/_zoo/*` for `ZOO_PANEL_ORIGIN`; never
  `/_zoo/verify`.

## Zoo endpoints

- `GET /_zoo/health`, `GET /_zoo/probe`, `GET /_zoo/verify` (signed),
  `GET /_zoo/trace/<id>`.
- Probe checks: Postgres row write/read/delete, Redis SET/GET/DEL with TTL,
  and the item cache (miss, then hit, same item).

## Variables

| Variable | From | Role in the probe |
| --- | --- | --- |
| `DATABASE_URL` | ox, shared postgres | service |
| `REDIS_URL` | ox, private redis | service |
| `PORT`, `PUBLIC_HOST`, `PUBLIC_URL`, `OX_ENV`, `OX_RELEASE` | ox | |
| `INTERNAL_TOKEN` | yours, secret shared with mesh-shop and sveltekit-ssr | verifies |
| `CORS_ORIGINS` | yours, `https://angular-static.s4.zoo.sorv.dev,https://zoo-control.s1.zoo.sorv.dev` | plain |
| `ZOO_PANEL_ORIGIN` | yours, `https://zoo-control.s1.zoo.sorv.dev` | plain |

`db.py` turns `postgres://` into `postgresql+psycopg://` for SQLAlchemy.

## Run locally

```sh
uv sync
export DATABASE_URL=postgres://user@127.0.0.1:5432/catalog REDIS_URL=redis://127.0.0.1:6379/1
uv run alembic upgrade head
uv run uvicorn main:app --host 127.0.0.1 --port 8000
```

## Tests

```sh
uv run pytest
TEST_DATABASE_URL=postgres://... TEST_REDIS_URL=redis://... uv run pytest   # plus the integration test
```

Recorded: unit only 31 passed, 1 skipped; with local Postgres 18 and Redis 8,
32 passed (migrate twice, list, signed lookup with a trace, trace read back,
404, probe all ok). A live uvicorn run answered the probe ok.

## ox check

```
ox check . (manifest: none)

  app.start                  uv run uvicorn main:app --host 127.0.0.1 --port $PORT detected:main.py
  build.install              uv sync --frozen --no-dev                            detected:uv.lock
  build.migrate              uv run alembic upgrade head                          detected:alembic.ini
  tools.python               3.13                                                 detected:.python-version
  tools.uv                   0.11                                                 default
  services.postgres          postgres 18 (shared)                                 detected:uv.lock
  services.redis             redis 8 (only for this project)                      detected:uv.lock

  Provided by ox: PORT, HOST, OX_ENV, OX_PROJECT, OX_RELEASE, OX_DATA_DIR, PUBLIC_URL, PUBLIC_HOST, DATABASE_URL, REDIS_URL
  Set on the dashboard before the first deploy: INTERNAL_TOKEN, CORS_ORIGINS, ZOO_PANEL_ORIGIN
  hint: SQLAlchemy (from uv.lock) rejects DATABASE_URL's postgres:// scheme; in your code, use os.environ["DATABASE_URL"].replace("postgres://", "postgresql+psycopg://", 1)

Ready to deploy.
```

The SQLAlchemy hint shows even though `db.py` already converts the scheme.

Not verified here: calls from the real mesh-shop and sveltekit-ssr, and
ox's proxy in front of uvicorn.
