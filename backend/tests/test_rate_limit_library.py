"""Rate-limiter hardening for the library family.

Covers ``.claude/plans/access_tiers_gating_DECISIONS.md`` D13:

1. **Path normalization** (D13.1) — the global middleware used to put the raw
   ``request.url.path`` in the Redis key, so every regulation slug got its own
   60/min bucket and breadth-first scraping was free. Every item path of a
   section must now share ONE key; hub list paths must keep their own.
2. **Route-scoped limiter** (D13.2) — 20/min keyed on the VERIFIED user. A
   forged/unverified JWT must not mint a fresh bucket (PART 9 trap 11).
3. **Fail-CLOSED** (D13.3) — with Redis gone the route limiter still limits,
   while the middleware still fails OPEN for everything else.

No Redis, no DB, no live app: a fake async Redis records the exact keys the
middleware/limiter build, which is the load-bearing assertion for (1), and the
limiter is exercised through a throwaway FastAPI app so the ``Depends`` wiring
another agent will copy is what is actually tested.
"""
from __future__ import annotations

import time
import typing
from typing import Any, Dict, List, Optional, Tuple

import pytest
from fastapi import Depends, FastAPI, Request
from fastapi.security import HTTPAuthorizationCredentials
from fastapi.testclient import TestClient

from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware import rate_limit as rl
from backend.app.middleware.rate_limit import (
    RateLimitMiddleware,
    normalize_rate_limit_path,
)
from backend.app.middleware.route_limits import (
    LIBRARY_ROUTE_RATE_LIMIT,
    RouteRateLimiter,
    library_rate_limit,
)
from shared.auth.jwt import AuthUser, TokenInvalidError


# ---------------------------------------------------------------------------
# Fake async Redis — enough of the ZSET surface for both limiters.
# ---------------------------------------------------------------------------


class _FakePipeline:
    def __init__(self, store: "FakeRedis") -> None:
        self._store = store
        self._ops: List[Tuple[str, tuple]] = []

    def zremrangebyscore(self, key: str, lo: float, hi: float) -> "_FakePipeline":
        self._ops.append(("zremrangebyscore", (key, lo, hi)))
        return self

    def zadd(self, key: str, mapping: Dict[str, float]) -> "_FakePipeline":
        self._ops.append(("zadd", (key, mapping)))
        return self

    def zcard(self, key: str) -> "_FakePipeline":
        self._ops.append(("zcard", (key,)))
        return self

    def expire(self, key: str, seconds: int) -> "_FakePipeline":
        self._ops.append(("expire", (key, seconds)))
        return self

    async def execute(self) -> List[Any]:
        if self._store.fail:
            raise ConnectionError("redis is down")
        results: List[Any] = []
        for op, args in self._ops:
            if op == "zremrangebyscore":
                key, lo, hi = args
                bucket = self._store.zsets.setdefault(key, {})
                doomed = [m for m, score in bucket.items() if lo <= score <= hi]
                for m in doomed:
                    bucket.pop(m, None)
                results.append(len(doomed))
            elif op == "zadd":
                key, mapping = args
                bucket = self._store.zsets.setdefault(key, {})
                added = sum(1 for m in mapping if m not in bucket)
                bucket.update(mapping)
                self._store.keys_seen.append(key)
                results.append(added)
            elif op == "zcard":
                key = args[0]
                results.append(len(self._store.zsets.get(key, {})))
            elif op == "expire":
                results.append(True)
        return results


class FakeRedis:
    """In-memory stand-in. ``fail=True`` makes every pipeline raise."""

    def __init__(self, fail: bool = False) -> None:
        self.zsets: Dict[str, Dict[str, float]] = {}
        self.keys_seen: List[str] = []
        self.fail = fail

    def pipeline(self) -> _FakePipeline:
        return _FakePipeline(self)


# ---------------------------------------------------------------------------
# App builders
# ---------------------------------------------------------------------------


def _middleware_app(redis: Optional[FakeRedis]) -> FastAPI:
    """Minimal app carrying ONLY the global limiter middleware."""
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.state.redis = redis

    @app.get("/{full_path:path}")
    async def catch_all(full_path: str) -> Dict[str, str]:
        return {"ok": full_path}

    return app


def _route_limited_app(
    redis: Optional[FakeRedis],
    limiter: Optional[RouteRateLimiter] = None,
) -> FastAPI:
    """App exposing a route wired exactly like the real consumers will be."""
    app = FastAPI()
    app.state.redis = redis
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    dep = limiter or library_rate_limit

    @app.get("/api/v1/library/full/{content_type}/{key:path}")
    async def full(content_type: str, key: str, state=Depends(dep)) -> Dict[str, Any]:
        return {"identity": state.identity, "backend": state.backend}

    return app


def _fresh_limiter(**kwargs: Any) -> RouteRateLimiter:
    """A limiter with its own process-local state (tests must not share it)."""
    kwargs.setdefault("scope", "library")
    kwargs.setdefault("limit", LIBRARY_ROUTE_RATE_LIMIT)
    return RouteRateLimiter(**kwargs)


# A fixed test value, pinned so neither a LIBRARY_ITEM_RATE_LIMIT env var in the
# developer's shell nor a change to the shipped default (600/min — a deliberately
# loose runaway-client backstop, NOT an enumeration control; see rate_limit.py)
# can change what these tests assert. What is under test is that the collapsed
# bucket EXISTS and is shared, not the size of the budget.
_ITEM_LIMIT = 120


@pytest.fixture(autouse=True)
def _reset_shared_limiter(monkeypatch):
    """The module-level singleton is shared; clear its fallback between tests."""
    monkeypatch.setattr(rl, "LIBRARY_ITEM_RATE_LIMIT", _ITEM_LIMIT)
    library_rate_limit._fallback.reset()
    yield
    library_rate_limit._fallback.reset()


def test_env_override_parsing_is_defensive(monkeypatch) -> None:
    """LIBRARY_ITEM_RATE_LIMIT is env-tunable; junk must not disable limiting."""
    monkeypatch.delenv("X_RL_TEST", raising=False)
    assert rl._env_int("X_RL_TEST", 120) == 120          # unset -> default
    for junk in ("", "   ", "abc", "0", "-5"):
        monkeypatch.setenv("X_RL_TEST", junk)
        assert rl._env_int("X_RL_TEST", 120) == 120
    monkeypatch.setenv("X_RL_TEST", "45")
    assert rl._env_int("X_RL_TEST", 120) == 45


# ===========================================================================
# 1. Path normalization (D13.1)
# ===========================================================================


def test_normalize_collapses_every_section_item_path() -> None:
    for section in ("regulations", "compliance", "circulars", "judgments", "forms"):
        assert (
            normalize_rate_limit_path(f"/api/v1/public/library/{section}/some-slug")
            == f"/api/v1/public/library/{section}/:item"
        )


def test_normalize_collapses_nested_article_path_into_the_same_bucket() -> None:
    doc = normalize_rate_limit_path("/api/v1/public/library/regulations/nizam-x")
    article = normalize_rate_limit_path(
        "/api/v1/public/library/regulations/nizam-x/articles/madda-12"
    )
    assert article == doc == "/api/v1/public/library/regulations/:item"


def test_normalize_collapses_library_full_paths() -> None:
    assert (
        normalize_rate_limit_path("/api/v1/library/full/regulation/nizam-x")
        == "/api/v1/library/full/regulation/:item"
    )
    # article keys carry an internal '/' ({reg_slug}/{article_slug})
    assert (
        normalize_rate_limit_path("/api/v1/library/full/article/nizam-x/madda-12")
        == "/api/v1/library/full/article/:item"
    )


def test_normalize_leaves_hub_and_unrelated_paths_alone() -> None:
    untouched = [
        "/api/v1/public/library/regulations",
        "/api/v1/public/library/judgments",
        "/api/v1/public/library/sitemap/articles",  # not an item family
        "/api/v1/library/full/regulation",          # no item tail
        "/api/v1/conversations/abc/messages",
        "/api/v1/auth/login",
    ]
    for path in untouched:
        assert normalize_rate_limit_path(path) == path


def test_hundred_slugs_collapse_to_one_redis_bucket() -> None:
    """The core hole: 100 distinct slugs must share ONE key, and the 121st
    request (LIBRARY_ITEM_RATE_LIMIT=120) must be refused."""
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for i in range(100):
        r = client.get(f"/api/v1/public/library/regulations/nizam-{i}")
        assert r.status_code == 200, r.text

    library_keys = {k for k in redis.keys_seen if "public/library" in k}
    assert len(library_keys) == 1, library_keys
    assert ":item:" in next(iter(library_keys))

    # Burn the rest of the shared budget, then trip it.
    for _ in range(_ITEM_LIMIT - 100):
        assert client.get("/api/v1/public/library/regulations/anything").status_code == 200
    blocked = client.get("/api/v1/public/library/regulations/one-more-slug")
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_two_sections_do_not_share_a_bucket() -> None:
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    client.get("/api/v1/public/library/regulations/a")
    client.get("/api/v1/public/library/judgments/b")

    library_keys = {k for k in redis.keys_seen if "public/library" in k}
    assert len(library_keys) == 2, library_keys


def test_hub_list_path_keeps_its_own_bucket() -> None:
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    client.get("/api/v1/public/library/regulations")          # hub
    client.get("/api/v1/public/library/regulations/some-slug")  # item

    library_keys = {k for k in redis.keys_seen if "public/library" in k}
    assert len(library_keys) == 2, library_keys
    assert any(k.endswith("/regulations:60") for k in library_keys), library_keys
    assert any("/regulations/:item:" in k for k in library_keys), library_keys


def test_hub_bucket_is_not_the_item_budget() -> None:
    """Hub pages keep the DEFAULT 60/min, not the wider item budget."""
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for _ in range(rl.DEFAULT_RATE_LIMIT):
        assert client.get("/api/v1/public/library/regulations").status_code == 200
    assert client.get("/api/v1/public/library/regulations").status_code == 429


# ===========================================================================
# 2. Route-scoped limiter (D13.2)
# ===========================================================================


def _auth(token: str) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _fake_verifier(monkeypatch, mapping: Dict[str, str]) -> None:
    """Map token -> auth_id; anything else fails verification."""

    def _extract(token: str) -> AuthUser:
        auth_id = mapping.get(token)
        if auth_id is None:
            raise TokenInvalidError("bad signature")
        return AuthUser(auth_id=auth_id, email="a@b.c", role="authenticated")

    monkeypatch.setattr(
        "backend.app.middleware.route_limits.extract_user", _extract, raising=True
    )


def test_route_limiter_blocks_the_twenty_first_request(monkeypatch) -> None:
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    client = TestClient(_route_limited_app(FakeRedis(), _fresh_limiter()))

    for i in range(LIBRARY_ROUTE_RATE_LIMIT):
        r = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
        assert r.status_code == 200, f"request {i + 1}: {r.text}"

    blocked = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
    assert blocked.status_code == 429


def test_route_limiter_429_matches_the_middleware_contract(monkeypatch) -> None:
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    limiter = _fresh_limiter(limit=1)
    client = TestClient(_route_limited_app(FakeRedis(), limiter))

    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 200
    blocked = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))

    assert blocked.status_code == 429
    body = blocked.json()
    assert body == {
        "error": {
            "code": "RATE_LIMITED",
            "message": rl.RATE_LIMIT_MESSAGE,
            "status": 429,
        },
        "detail": rl.RATE_LIMIT_MESSAGE,
    }
    # Arabic, not English (Rule #5)
    assert body["detail"] == "تم تجاوز الحد المسموح من الطلبات"
    assert blocked.headers["X-RateLimit-Remaining"] == "0"
    assert blocked.headers["Retry-After"] == str(limiter.window_seconds)
    assert int(blocked.headers["X-RateLimit-Reset"]) >= int(time.time())


def test_middleware_does_not_overwrite_a_downstream_429s_headers(monkeypatch) -> None:
    """The route limiter's 429 travels back out through the middleware; the
    middleware must not stamp its own (much larger) remaining onto it."""
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    app = _route_limited_app(FakeRedis(), _fresh_limiter(limit=1))
    app.add_middleware(RateLimitMiddleware)
    client = TestClient(app)

    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 200
    blocked = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
    assert blocked.status_code == 429
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


# ===========================================================================
# 3. Identity: verified user only (PART 9 trap 11)
# ===========================================================================


def test_two_verified_users_get_separate_buckets(monkeypatch) -> None:
    _fake_verifier(
        monkeypatch,
        {
            "tok-a": "11111111-1111-1111-1111-111111111111",
            "tok-b": "22222222-2222-2222-2222-222222222222",
        },
    )
    redis = FakeRedis()
    client = TestClient(_route_limited_app(redis, _fresh_limiter(limit=1)))

    a = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
    b = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-b"))
    assert a.status_code == 200 and b.status_code == 200

    # User A has spent their budget; user B is untouched by it.
    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 429
    assert b.json()["identity"] == "user:22222222-2222-2222-2222-222222222222"

    route_keys = {k for k in redis.keys_seen if k.startswith("ratelimit:route:")}
    assert route_keys == {
        "ratelimit:route:library:user:11111111-1111-1111-1111-111111111111:60",
        "ratelimit:route:library:user:22222222-2222-2222-2222-222222222222:60",
    }


def test_forged_tokens_cannot_mint_fresh_buckets(monkeypatch) -> None:
    """Unverifiable tokens degrade to the client IP — the claimed `sub` is
    never used, so rotating forged tokens does not buy new budget."""
    _fake_verifier(monkeypatch, {})  # nothing verifies
    redis = FakeRedis()
    client = TestClient(_route_limited_app(redis, _fresh_limiter(limit=5)))

    statuses = [
        client.get(
            "/api/v1/library/full/regulation/x",
            headers=_auth(f"forged-token-{i}"),  # a different 'sub' every time
        ).status_code
        for i in range(8)
    ]
    assert statuses[:5] == [200] * 5
    assert statuses[5:] == [429] * 3

    route_keys = {k for k in redis.keys_seen if k.startswith("ratelimit:route:")}
    assert len(route_keys) == 1, route_keys
    key = next(iter(route_keys))
    assert ":ip:" in key
    assert "user:" not in key


def test_well_formed_forged_jwt_moves_the_middleware_key_but_not_the_route_key(
    monkeypatch,
) -> None:
    """Trap 11, side by side.

    The middleware decodes with ``verify_signature: False``, so an attacker's
    self-signed JWT DOES rotate its bucket there. The route-scoped limiter must
    not budge: the claimed ``sub`` never reaches its key.
    """
    import jwt as pyjwt

    _fake_verifier(monkeypatch, {})  # signature check fails for everything
    redis = FakeRedis()
    app = _route_limited_app(redis, _fresh_limiter(limit=50))
    app.add_middleware(RateLimitMiddleware)
    client = TestClient(app)

    claimed_subs = [f"0000000{i}-0000-0000-0000-00000000000{i}" for i in range(3)]
    for sub in claimed_subs:
        token = pyjwt.encode(
            {
                "sub": sub,
                "email": "attacker@example.com",
                "role": "authenticated",
                "aud": "authenticated",
                "iat": int(time.time()),
                "exp": int(time.time()) + 3600,
            },
            "attacker-chosen-secret-long-enough-for-hs256",
            algorithm="HS256",
        )
        assert client.get(
            "/api/v1/library/full/regulation/x", headers=_auth(token)
        ).status_code == 200

    middleware_keys = {k for k in redis.keys_seen if not k.startswith("ratelimit:route:")}
    route_keys = {k for k in redis.keys_seen if k.startswith("ratelimit:route:")}

    # Middleware: one bucket per forged sub (the known, deferred weakness).
    assert len(middleware_keys) == 3, middleware_keys
    # Route limiter: one IP bucket, no forged sub anywhere in it.
    assert len(route_keys) == 1, route_keys
    key = next(iter(route_keys))
    assert ":ip:" in key
    assert all(sub not in key for sub in claimed_subs)


def test_anonymous_caller_falls_back_to_client_ip(monkeypatch) -> None:
    _fake_verifier(monkeypatch, {})
    redis = FakeRedis()
    client = TestClient(_route_limited_app(redis, _fresh_limiter(limit=2)))

    r = client.get(
        "/api/v1/library/full/regulation/x",
        headers={"X-Forwarded-For": "203.0.113.9, 10.0.0.1"},
    )
    assert r.status_code == 200
    assert r.json()["identity"] == "ip:203.0.113.9"


def test_expired_or_invalid_token_does_not_share_the_verified_bucket(monkeypatch) -> None:
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    redis = FakeRedis()
    client = TestClient(_route_limited_app(redis, _fresh_limiter(limit=5)))

    good = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
    bad = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-expired"))

    assert good.json()["identity"].startswith("user:")
    assert bad.json()["identity"].startswith("ip:")


# ===========================================================================
# 4. Fail-CLOSED for the library family, fail-OPEN everywhere else (D13.3)
# ===========================================================================


@pytest.mark.parametrize("redis", [None, FakeRedis(fail=True)])
def test_route_limiter_still_limits_without_redis(monkeypatch, redis) -> None:
    """Redis missing (None) or erroring — the in-process fallback must hold."""
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    limiter = _fresh_limiter()
    client = TestClient(_route_limited_app(redis, limiter))

    for i in range(LIBRARY_ROUTE_RATE_LIMIT):
        r = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
        assert r.status_code == 200, f"request {i + 1}: {r.text}"
        assert r.json()["backend"] == "process"

    blocked = client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a"))
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "RATE_LIMITED"


def test_in_process_fallback_separates_identities(monkeypatch) -> None:
    _fake_verifier(
        monkeypatch,
        {
            "tok-a": "11111111-1111-1111-1111-111111111111",
            "tok-b": "22222222-2222-2222-2222-222222222222",
        },
    )
    client = TestClient(_route_limited_app(None, _fresh_limiter(limit=1)))

    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 200
    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 429
    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-b")).status_code == 200


def test_in_process_fallback_window_expires() -> None:
    """Blocked identities recover once the window slides past."""
    limiter = _fresh_limiter(limit=2, window_seconds=60)
    window = limiter._fallback

    assert window.hit("user:x")[0] == 1
    assert window.hit("user:x")[0] == 2
    assert window.hit("user:x")[0] == 3  # > limit -> refused

    # Age every recorded timestamp out of the window.
    bucket = window._buckets["user:x"]
    for _ in range(len(bucket)):
        bucket.append(bucket.popleft() - 3600)

    assert window.hit("user:x")[0] == 1


def test_in_process_fallback_memory_is_bounded() -> None:
    limiter = _fresh_limiter(limit=3)
    window = limiter._fallback
    for _ in range(50):
        window.hit("user:flood")
    assert len(window._buckets["user:flood"]) <= 4  # limit + 1


def test_middleware_still_fails_open_for_non_library_paths() -> None:
    """The middleware's fail-open stance is deliberate and must not regress."""
    for redis in (None, FakeRedis(fail=True)):
        client = TestClient(_middleware_app(redis))
        for _ in range(rl.DEFAULT_RATE_LIMIT + 40):
            assert client.get("/api/v1/conversations").status_code == 200


def test_middleware_fails_open_for_library_paths_too() -> None:
    """Only the ROUTE-scoped limiter is fail-closed. The middleware is a
    damper, and a Redis outage must not 500/429 the public pages; the paid
    bytes stay bounded because /library/full carries the route dependency."""
    client = TestClient(_middleware_app(None))
    for i in range(_ITEM_LIMIT + 10):
        assert client.get(f"/api/v1/public/library/regulations/s{i}").status_code == 200


# ===========================================================================
# 5. Wiring contract — the shape another agent will copy
# ===========================================================================


def test_exported_dependency_is_callable_with_the_expected_signature() -> None:
    import inspect

    from backend.app.middleware import route_limits

    assert isinstance(route_limits.library_rate_limit, RouteRateLimiter)
    assert route_limits.library_rate_limit.limit == 20
    assert route_limits.library_rate_limit.window_seconds == 60
    assert route_limits.library_rate_limit.scope == "library"

    params = inspect.signature(route_limits.library_rate_limit).parameters
    assert list(params) == ["request", "credentials"]
    # `from __future__ import annotations` stringifies these; resolve them the
    # way FastAPI does before building the dependant.
    hints = typing.get_type_hints(type(route_limits.library_rate_limit).__call__)
    assert hints["request"] is Request
    assert hints["credentials"] == Optional[HTTPAuthorizationCredentials]


def test_both_consumer_routes_can_share_one_budget(monkeypatch) -> None:
    """`/library/full/*` and the workspace reference-source endpoint use the
    same limiter instance, so alternating between them must NOT double the
    budget."""
    _fake_verifier(monkeypatch, {"tok-a": "11111111-1111-1111-1111-111111111111"})
    limiter = _fresh_limiter(limit=2)

    app = FastAPI()
    app.state.redis = FakeRedis()
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)

    @app.get("/api/v1/library/full/{content_type}/{key:path}")
    async def full(content_type: str, key: str, _rl=Depends(limiter)) -> Dict[str, bool]:
        return {"ok": True}

    @app.get("/api/v1/workspace/{item_id}/references/{n}/source")
    async def source(item_id: str, n: int, _rl=Depends(limiter)) -> Dict[str, bool]:
        return {"ok": True}

    client = TestClient(app)
    assert client.get("/api/v1/library/full/regulation/x", headers=_auth("tok-a")).status_code == 200
    assert client.get("/api/v1/workspace/abc/references/1/source", headers=_auth("tok-a")).status_code == 200
    assert client.get("/api/v1/workspace/abc/references/2/source", headers=_auth("tok-a")).status_code == 429


# ---------------------------------------------------------------------------
# Regression guard: the PEP 563 trap that 422'd every guarded route
# ---------------------------------------------------------------------------


def test_route_limits_annotations_are_runtime_resolvable():
    """``library_rate_limit`` is a callable INSTANCE, so its annotations must be
    real objects — not PEP 563 strings.

    FastAPI resolves a dependency's annotations via
    ``getattr(call, "__globals__", {})``. An instance has no ``__globals__``, so
    if ``route_limits.py`` ever regains ``from __future__ import annotations``,
    every annotation stays an unevaluated ForwardRef, FastAPI stops recognising
    ``request: Request`` as the Request object, reclassifies it as a QUERY
    parameter, and EVERY call to every guarded route dies with
    ``422 {"loc": ["query", "request"]}``.

    This shipped once (2026-07-27) and took out the whole reveal path, so it is
    pinned at the signature level rather than only end-to-end.
    """
    from fastapi.dependencies.utils import get_typed_signature

    sig = get_typed_signature(library_rate_limit)
    params = sig.parameters

    assert params["request"].annotation is Request, (
        "request annotation resolved to "
        f"{params['request'].annotation!r} — route_limits.py has almost "
        "certainly regained `from __future__ import annotations`; remove it."
    )
    assert not isinstance(params["credentials"].annotation, str)
    assert not isinstance(
        params["credentials"].annotation, typing.ForwardRef
    ), "credentials annotation is an unresolved ForwardRef"


def test_guarded_route_accepts_a_request_with_no_query_params():
    """End-to-end counterpart: the trap above manifests as a 422 naming a query
    parameter called `request`, on a route that declares no query params."""
    limiter = _fresh_limiter(limit=5)

    app = FastAPI()
    app.state.redis = FakeRedis()
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)

    @app.get("/api/v1/library/full/{content_type}/{key:path}")
    async def full(content_type: str, key: str, _rl=Depends(limiter)) -> Dict[str, bool]:
        return {"ok": True}

    res = TestClient(app).get("/api/v1/library/full/regulation/some-slug")
    assert res.status_code != 422, res.text
    assert res.status_code == 200, res.text


# ---------------------------------------------------------------------------
# ISR bake bypass (X-ISR-Bake-Secret) — rate_limit.is_isr_bake_request
# ---------------------------------------------------------------------------
# The renderer's `next build` fires hundreds of library GETs from one IP in
# one burst; with the right secret those must never 429, and NOTHING else may
# ride along: wrong secret, unset env, non-GET, and non-library paths all keep
# today's limits exactly.

_BAKE_SECRET = "test-bake-secret-value"


def test_bake_secret_bypasses_hub_list_limit(monkeypatch) -> None:
    monkeypatch.setenv(rl.ISR_BAKE_SECRET_ENV, _BAKE_SECRET)
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))
    headers = {rl.ISR_BAKE_HEADER: _BAKE_SECRET}

    # Far past DEFAULT_RATE_LIMIT on ONE normalized hub bucket — the exact
    # shape of the 2026-08-06 build failure (38 sector_slug variants, one key).
    for i in range(rl.DEFAULT_RATE_LIMIT * 2):
        r = client.get(
            f"/api/v1/public/library/regulations?page=1&sector_slug=s-{i % 38}",
            headers=headers,
        )
        assert r.status_code == 200, r.text

    # Bypassed requests must not even touch Redis — no key, no count.
    assert not {k for k in redis.keys_seen if "public/library" in k}

    # The bare unified-hub path (no trailing segment) is part of the bake too.
    assert client.get("/api/v1/public/library", headers=headers).status_code == 200


def test_bake_secret_wrong_or_absent_keeps_the_limit(monkeypatch) -> None:
    monkeypatch.setenv(rl.ISR_BAKE_SECRET_ENV, _BAKE_SECRET)
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for _ in range(rl.DEFAULT_RATE_LIMIT):
        r = client.get(
            "/api/v1/public/library/regulations",
            headers={rl.ISR_BAKE_HEADER: "forged"},
        )
        assert r.status_code == 200, r.text
    blocked = client.get("/api/v1/public/library/regulations")
    assert blocked.status_code == 429


def test_bake_secret_env_unset_is_dead_code(monkeypatch) -> None:
    monkeypatch.delenv(rl.ISR_BAKE_SECRET_ENV, raising=False)
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))

    for _ in range(rl.DEFAULT_RATE_LIMIT):
        client.get(
            "/api/v1/public/library/regulations",
            headers={rl.ISR_BAKE_HEADER: ""},
        )
    blocked = client.get(
        "/api/v1/public/library/regulations",
        headers={rl.ISR_BAKE_HEADER: ""},
    )
    assert blocked.status_code == 429


def test_bake_secret_scope_excludes_non_library_and_non_get(monkeypatch) -> None:
    """The bypass hands out nothing beyond public-library GETs: auth keeps its
    10/min, and a POST under the library prefix still counts."""
    monkeypatch.setenv(rl.ISR_BAKE_SECRET_ENV, _BAKE_SECRET)
    redis = FakeRedis()
    client = TestClient(_middleware_app(redis))
    headers = {rl.ISR_BAKE_HEADER: _BAKE_SECRET}

    for _ in range(rl.AUTH_RATE_LIMIT):
        assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 429

    assert rl.is_isr_bake_request is not None  # symbol exported
    library_post_keys_before = set(redis.keys_seen)
    client.post("/api/v1/public/library/regulations", headers=headers)
    assert set(redis.keys_seen) != library_post_keys_before
