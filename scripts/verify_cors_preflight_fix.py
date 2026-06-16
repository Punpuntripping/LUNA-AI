"""
Repro + verification for the 2026-06-16 login outage (surgical, no logfire/app import).

OTEL's FastAPI span-namer crashes computing the span for an OPTIONS *preflight*
to an included-router path ('_IncludedRouter' object has no attribute 'path'),
returning a bare 500 with no CORS headers -> browsers block every cross-origin
login/API call. logfire.instrument_fastapi() calls FastAPIInstrumentor under the
hood, so we drive the instrumentor directly to isolate the exact crash and the
fix without pulling logfire.configure() or the heavy app import.

  A) OLD order (instrument outermost / CORS inner) -> OPTIONS preflight = 500  (the bug)
  B) NEW order (CORS outermost / instrument inner) -> OPTIONS preflight = 200  (the fix)
  D) Direct-on-app route (like /health)            -> OPTIONS preflight = 200  (immune, matches prod)

Run:  .venv/Scripts/python.exe -u scripts/verify_cors_preflight_fix.py
"""
from __future__ import annotations

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from starlette.testclient import TestClient

CORS_KW = dict(
    allow_origins=["https://rayhanai.com"],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
)
PREFLIGHT = {
    "Origin": "https://rayhanai.com",
    "Access-Control-Request-Method": "POST",
    "Access-Control-Request-Headers": "content-type",
}


def _build(order: str) -> FastAPI:
    app = FastAPI()

    # A route defined DIRECTLY on the app (immune — has a real `.path`).
    @app.get("/api/v1/health")
    async def health():  # noqa: ANN202
        return {"status": "ok"}

    # A route via include_router (the broken case — OPTIONS matches a sub-router
    # object the OTEL span-namer can't read `.path` from).
    r = APIRouter()

    @r.post("/login")
    async def login():  # noqa: ANN202
        return {"ok": True}

    app.include_router(r, prefix="/api/v1/auth")

    if order == "old":
        # CORS first => INNER; instrument last => OUTERMOST (the prod-broken order).
        app.add_middleware(CORSMiddleware, **CORS_KW)
        FastAPIInstrumentor.instrument_app(app)
    else:
        # instrument first => INNER; CORS last => OUTERMOST (the fix).
        FastAPIInstrumentor.instrument_app(app)
        app.add_middleware(CORSMiddleware, **CORS_KW)
    return app


def _probe(app: FastAPI, path: str) -> tuple[int, str | None]:
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.options(path, headers=PREFLIGHT)
    return resp.status_code, resp.headers.get("access-control-allow-origin")


def main() -> int:
    old = _build("old")
    new = _build("new")

    a_code, a_acao = _probe(old, "/api/v1/auth/login")
    print(f"A) OLD (instrument outermost): OPTIONS /api/v1/auth/login -> {a_code}  ACAO={a_acao}")

    b_code, b_acao = _probe(new, "/api/v1/auth/login")
    print(f"B) NEW (CORS outermost):       OPTIONS /api/v1/auth/login -> {b_code}  ACAO={b_acao}")

    d_code, _ = _probe(old, "/api/v1/health")
    print(f"D) OLD, direct route:          OPTIONS /api/v1/health     -> {d_code}  (immune)")

    checks = [
        ("bug reproduced: OLD /auth/login == 500", a_code == 500),
        ("fix works:      NEW /auth/login == 200 + CORS", b_code == 200 and b_acao == "https://rayhanai.com"),
        ("matches prod:   OLD /health     == 200", d_code == 200),
    ]
    print("\n--- summary ---")
    ok = True
    for name, passed in checks:
        print(f"  {'PASS' if passed else 'FAIL'}  {name}")
        ok = ok and passed
    print("\nRESULT:", "ALL PASS — bug reproduced and fix verified" if ok else "SOMETHING FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
