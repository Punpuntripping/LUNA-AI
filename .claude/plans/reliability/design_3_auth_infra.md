# Design 3 — Auth + Shared-Infra Resilience (S3, S4, S5)

**Scope:** JWKS hardening, GoTrue call hardening, Redis reconnect supervisor, error-code hygiene.

## Verified facts (from installed packages)

1. **PyJWT 2.11.0** `PyJWKClient.__init__` signature: `PyJWKClient(uri, cache_keys=False, max_cached_keys=16, cache_jwk_set=True, lifespan=300, headers=None, timeout=30, ssl_context=None)` — `timeout=` **is available**; fetch uses `urllib.request.urlopen(..., timeout=self.timeout)` and raises `jwt.exceptions.PyJWKClientConnectionError` on failure.
2. **Critical PyJWT gotcha:** `fetch_data()`'s `finally` block calls `self.jwk_set_cache.put(jwk_set)` with `jwk_set=None` on failure, and `JWKSetCache.put(None)` **clears the cache**. A single failed refresh wipes the cached keyset — PyJWT alone does NOT keep last-known-good keys across an outage. Our wrapper must restore them.
3. **PyJWT already refreshes on kid-miss**: `get_signing_key()` internally retries with `get_signing_keys(refresh=True)` when the kid isn't cached. We only add retry-on-`InvalidSignatureError` and the last-good fallback.
4. **supabase_auth exception taxonomy** (from `supabase_auth/helpers.py::handle_exception`):
   - Network errors (httpx `ConnectError`, `ReadTimeout`, anything not `HTTPStatusError`) → **`AuthRetryableError`** (status 0).
   - HTTP 502/503/504 from GoTrue → **`AuthRetryableError`**.
   - HTTP 400/401/422 (bad credentials, invalid refresh token) → **`AuthApiError`** with `.status` and `.code`.
   - Unparseable error body → **`AuthUnknownError`**.
   - All inherit `supabase_auth.errors.AuthError`. **No string matching needed — `AuthRetryableError` vs `AuthApiError.status` is the exact split.**
5. GoTrue's sync client builds `httpx.Client(...)` with **no explicit timeout → httpx default `Timeout(5.0)`**. A `wait_for`-abandoned thread self-terminates in ~5s; no thread leak accumulation.
6. `redis.asyncio` clients auto-reconnect per command — the singleton client object can be reused forever; "swapping `app.state.redis`" just means re-pointing to the singleton vs `None`.

---

## Fix 1 — JWKS hardening (`shared/auth/jwt.py` + lifespan)

Replace the singleton block (lines 23–34) with a resilient subclass:

```python
import threading
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientConnectionError, PyJWKClientError

_JWKS_FETCH_TIMEOUT = 5.0
_JWKS_CACHE_LIFESPAN = 300

class AuthUnavailableError(AuthError):
    """Auth dependency (JWKS) is unreachable — caller should 503, not 401."""
    def __init__(self, detail: str = "Auth service unavailable"):
        super().__init__(detail, 503)

class ResilientJWKClient(PyJWKClient):
    """PyJWKClient that keeps last-known-good keys across JWKS outages.

    PyJWT's fetch_data() clears its internal cache on a failed fetch
    (puts None in the finally block) — this subclass restores the last
    successful payload so a JWKS blip never invalidates known kids."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._last_good: dict | None = None
        self._lock = threading.Lock()

    def fetch_data(self):
        try:
            data = super().fetch_data()
            with self._lock:
                self._last_good = data
            return data
        except PyJWKClientConnectionError:
            with self._lock:
                last_good = self._last_good
            if last_good is not None:
                logger.warning("JWKS fetch failed — serving last-known-good keyset")
                if self.jwk_set_cache is not None:
                    self.jwk_set_cache.put(last_good)  # undo the None-put, re-arm TTL
                return last_good
            raise

_jwks_client: Optional[ResilientJWKClient] = None

def _get_jwks_client() -> ResilientJWKClient:
    global _jwks_client
    if _jwks_client is None:
        settings = get_settings()
        jwks_url = f"{settings.SUPABASE_URL}/auth/v1/.well-known/jwks.json"
        _jwks_client = ResilientJWKClient(
            jwks_url, cache_jwk_set=True,
            lifespan=_JWKS_CACHE_LIFESPAN, timeout=_JWKS_FETCH_TIMEOUT,
        )
    return _jwks_client

def prewarm_jwks() -> None:
    """Best-effort JWKS pre-fetch. Called from lifespan startup (in a thread)."""
    try:
        keys = _get_jwks_client().get_signing_keys()
        logger.info("JWKS pre-warmed (%d signing keys)", len(keys))
    except Exception as e:  # noqa: BLE001
        logger.warning("JWKS pre-warm failed (will retry lazily): %s", e)
```

**Deliberate change:** drop `cache_keys=True`. The per-kid `lru_cache` it installs bypasses refresh-on-failure (and caches rotated-out keys forever). The `jwk_set_cache` (5-min TTL) + `_last_good` give the same hot-path behavior. Cost: `PyJWKSet.from_dict` re-parses 1–2 EC keys per ES256 decode — microseconds.

### Modified decode path (`decode_token`)

```python
    try:
        if alg == "HS256":
            signing_key = settings.SUPABASE_JWT_SECRET
        else:
            jwks_client = _get_jwks_client()
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            # kid-miss already triggers one internal refresh inside PyJWT.

        try:
            payload = jwt.decode(token, signing_key, ...)  # unchanged kwargs
        except jwt.InvalidSignatureError:
            if alg != "ES256":
                raise
            # Key may have rotated under the same kid / stale cache:
            # force ONE refresh and retry once.
            logger.warning("ES256 signature failed — forcing JWKS refresh and retrying")
            jwks_client.get_signing_keys(refresh=True)
            signing_key = jwks_client.get_signing_key_from_jwt(token).key
            payload = jwt.decode(token, signing_key, ...)  # second failure propagates
        return payload

    except PyJWKClientConnectionError as e:
        # JWKS unreachable AND no last-known-good keys (cold start during outage).
        logger.error("JWKS unreachable, no cached keys: %s", e)
        raise AuthUnavailableError()
    except PyJWKClientError as e:
        raise TokenInvalidError(f"Unknown signing key: {e}")
    except jwt.ExpiredSignatureError: ...   # unchanged ladder below
```

### `deps.get_current_user` (`backend/app/deps.py`)

- Add `except AuthUnavailableError` **before** the generic `AuthError` catch → `LunaHTTPException(503, ErrorCode.SERVICE_UNAVAILABLE, "الخدمة غير متاحة مؤقتاً، حاول مجدداً")`. (Order matters: `AuthUnavailableError` subclasses `AuthError`, which currently maps to 401 at line 59.)
- Change `user = extract_user(token)` → `user = await asyncio.to_thread(extract_user, token)` — the JWKS fetch is sync urllib with a 5s timeout inside an `async def` dependency.

### Lifespan addition (`backend/app/main.py`, after step 1, ~line 68)

```python
    # 1c. JWKS pre-warm — non-blocking, best-effort.
    app.state.jwks_prewarm_task = asyncio.create_task(asyncio.to_thread(prewarm_jwks))
```

Non-blocking so a slow/down JWKS endpoint adds **zero** cold-start latency. Keep the task reference to prevent GC.

---

## Fix 2 — GoTrue call hardening (`backend/app/api/auth.py`)

### Shared helper + imports

```python
import asyncio
from supabase_auth.errors import AuthApiError, AuthRetryableError, AuthSessionMissingError

_GOTRUE_TIMEOUT = 5.0
_MSG_SERVICE_UNAVAILABLE = "الخدمة غير متاحة مؤقتاً، حاول مجدداً"

async def _gotrue_call(fn, /, *args, **kwargs):
    """Run a sync GoTrue call off the event loop with a hard 5s deadline."""
    return await asyncio.wait_for(
        asyncio.to_thread(fn, *args, **kwargs), timeout=_GOTRUE_TIMEOUT)
```

(`asyncio.TimeoutError is builtins.TimeoutError` on Python 3.11+ — catch `TimeoutError`.)

### Exception ladder

| Exception | Meaning | Response |
|---|---|---|
| `AuthRetryableError` | network error inside gotrue, OR GoTrue 502/503/504 | **503 SERVICE_UNAVAILABLE** |
| `TimeoutError` (from `wait_for`) | GoTrue hung past 5s | **503 SERVICE_UNAVAILABLE** |
| `AuthApiError` with `e.status in (400, 401, 403, 422)` | bad credentials / invalid-expired refresh token | **401 AUTH_INVALID / AUTH_EXPIRED** |
| `AuthApiError` other status (5xx) | GoTrue server error | **503 SERVICE_UNAVAILABLE** |
| `AuthUnknownError` / `AuthSessionMissingError` | unparseable response / no session | refresh: 401; login: 503 (don't blame the user's password for garbage responses) |
| anything else | our bug | **500 INTERNAL_ERROR** |

### `login` (replaces lines 50–59 — deletes the string-matching block)

```python
    try:
        response = await _gotrue_call(
            supabase_auth.auth.sign_in_with_password,
            {"email": body.email, "password": body.password})
    except (AuthRetryableError, TimeoutError) as e:
        logger.error("GoTrue unavailable during login: %s", e)
        raise LunaHTTPException(status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE,
                                detail=_MSG_SERVICE_UNAVAILABLE)
    except AuthApiError as e:
        if e.status in (400, 401, 403, 422):
            raise LunaHTTPException(status_code=401, code=ErrorCode.AUTH_INVALID,
                                    detail="بيانات الدخول غير صحيحة")
        logger.error("GoTrue API error during login (status=%s code=%s)", e.status, e.code)
        raise LunaHTTPException(status_code=503, code=ErrorCode.SERVICE_UNAVAILABLE,
                                detail=_MSG_SERVICE_UNAVAILABLE)
    except Exception as e:
        logger.exception("Unexpected login error: %s", e)
        raise LunaHTTPException(status_code=500, code=ErrorCode.INTERNAL_ERROR,
                                detail="حدث خطأ داخلي")
```

### `refresh` (replaces lines 115–129)

Same ladder via `await _gotrue_call(supabase_auth.auth.refresh_session, body.refresh_token)`:
- `(AuthRetryableError, TimeoutError)` → 503 (the headline fix: **outage no longer masquerades as expired token**, so the frontend won't force-logout users during a Supabase blip).
- `AuthApiError` 400/401/403 and `AuthSessionMissingError` → 401 `AUTH_EXPIRED`.
- Other `AuthApiError` → 503. Catch-all → 500.

### `logout` (lines 146–149)

**Decision:** keep 200 on degraded logout — the client discards tokens either way and a 503 would trap users; but log loudly at WARNING with degraded flags (`gotrue_ok`/`redis_ok`) instead of two independent whispers. Shared-device risk bounded by token expiry.

Also align `shared/auth/jwt.py::refresh_token()` (lines 208–235): it duplicates `/refresh`'s logic with a blanket 401; route doesn't use it — delete it, or give it the same `AuthRetryableError` → `AuthUnavailableError` split.

---

## Fix 3 — Redis supervisor (lifespan, `backend/app/main.py`)

**Decision on nulling out:** **yes** — null `app.state.redis` after 3 consecutive supervisor ping failures. Every per-request fail-open path otherwise pays the 5s `socket_connect_timeout` against a dead host; nulling converts that to a free `is None` check. Recovery is the supervisor's job. The ≤ ~45s window between death and detection still relies on existing per-request fail-open (`rate_limit.py:130-133`).

Replace step 2 of lifespan (lines 80–88):

```python
    # 2. Redis — supervised. app.state.redis is the singleton client when
    #    healthy, None when down. A background task owns the transitions.
    app.state.redis = None
    redis_client = get_async_redis_client()   # singleton; auto-reconnects per command

    async def _redis_supervisor() -> None:
        backoff, was_down_logged, failures = 1.0, False, 0
        while True:
            try:
                await redis_client.ping()
                if app.state.redis is None:
                    app.state.redis = redis_client
                    logger.info("Redis %s — rate limiting enabled",
                                "recovered" if was_down_logged else "client ready")
                    was_down_logged = False
                failures, backoff = 0, 1.0
                await asyncio.sleep(15)            # healthy: poll every 15s
            except asyncio.CancelledError:
                raise
            except Exception as e:                 # noqa: BLE001
                failures += 1
                if app.state.redis is not None and failures >= 3:
                    app.state.redis = None         # stop per-request hammering
                if not was_down_logged:
                    logger.warning("Redis unavailable (failure %d): %s — "
                                   "rate limiting disabled, reconnecting with backoff",
                                   failures, e)
                    was_down_logged = True
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    app.state.redis_supervisor = asyncio.create_task(_redis_supervisor())
```

Shutdown: `app.state.redis_supervisor.cancel()` (+ `await` with `contextlib.suppress(asyncio.CancelledError)`), then close `redis_client` directly — the current shutdown closes `app.state.redis`, which may be `None` mid-outage even though the client exists; close the singleton, not the state pointer.

Details:
- Startup is no longer gated on Redis at all (today's code blocks on `ping()`).
- 3-failure threshold avoids flapping; per-request fail-open covers the gap while healthy-flagged.
- WARNING once per outage, INFO once on recovery — clean alerting signal.
- **Known limitation (documented):** `shared/cache/redis.py` helpers and `shared/quota/redis_store.py` call `get_async_redis_client()` directly, bypassing `app.state.redis` — they still pay the 5s timeout when Redis is dead. Optional follow-up: module-level `redis_healthy: bool` flag in `shared/cache/redis.py`, flipped by the supervisor.

---

## Fix 4 — Error-code hygiene sweep

### New ErrorCode (`backend/app/errors.py`)

```python
    # Dependency outage (Supabase GoTrue/JWKS/Postgres/Storage, Redis)
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
```

Canonical Arabic string (single module-level constant `MSG_SERVICE_UNAVAILABLE` in `errors.py`, reused by all domains): **"الخدمة غير متاحة مؤقتاً، حاول مجدداً"**. Add `Retry-After: 5` header on 503s where convenient.

### Sweep table

| Site | Today | New behavior |
|---|---|---|
| `auth.py:54-59` (login) | all exceptions + string-match → 401 | ladder: outage → **503**; only `AuthApiError` 4xx → 401 |
| `auth.py:127-129` (refresh) | all exceptions → 401 AUTH_EXPIRED | outage → **503**; only 4xx/`AuthSessionMissingError` → 401 |
| `auth.py:146-156` (logout) | silent best-effort, always 200 | keep 200; **single WARNING with degraded flags** |
| `deps.py:62-64` (get_current_user catch-all) | every unexpected exception → 401 | `AuthUnavailableError` → **503**; keep catch-all 401 for true token garbage |
| `shared/auth/jwt.py:233-235` (`refresh_token` helper) | catch-all → 401 | delete (unused) or apply same split |
| `case_service.py:285-289` (`get_case_detail` swallows conversation fetch) | wrong stats served as truth | **raise** 500 — a detail page with fabricated stats is worse than a retryable error |
| `case_service.py:502-544` (count helpers fake 0) | silent fake-zero counts | **Resolved by design 6**: the `case_counts` RPC replaces the helpers entirely; failures propagate as 500. (The Optional[int] alternative was considered and rejected in consolidation — see master plan §conflicts.) |
| `references_service.py:179-181` (`_select_reference_rows` → `[]`) | DB failure renders as "no references" | **re-raise**; route maps to 500 "حدث خطأ أثناء جلب المراجع". Enrichment failures (lines 241, 280, 366, 398) **stay best-effort** — degraded cards beat a failed response; only the primary row-select must not lie |

## Risks

1. Pre-warm slowing cold start — eliminated by `create_task`.
2. `wait_for` abandons threads — gotrue's 5s default timeout means they die on their own.
3. Dropping `cache_keys=True` — negligible per-decode re-parse; it's what makes forced refresh take effect.
4. **Frontend contract changes** — new 503 + `SERVICE_UNAVAILABLE` on login/refresh (frontend must NOT treat refresh-503 as "logged out" — retain tokens and retry). Coordinate before deploy.
5. Last-known-good keys trust rotated-out keys during an outage — bounded by token `exp` and only until JWKS recovers; strictly better than 401-ing everyone.
6. `_AUTH_401` is a module-level shared exception instance in deps.py — don't reuse that pattern for the new 503 (headers dict is shared/mutable); construct per-raise.

## Verification

1. **JWKS outage (warm):** authenticated request, then block JWKS via hosts file; wait >5 min (cache expiry); `/auth/me` → 200 + "serving last-known-good keyset" log. Unblock → next refresh silent.
2. **JWKS outage (cold):** restart with block in place → "pre-warm failed" WARNING at boot, ES256 requests → **503** (not 401), HS256 unaffected.
3. **GoTrue split:** wrong password → 401 fast. Block GoTrue → login returns **503 within ~5–6s**; `/refresh` with valid token during block → 503, same token works after unblock (no false expiry).
4. **Redis mid-run kill:** stop Redis while hitting an endpoint in a loop → 200s continue (fail-open), WARNING within ~45s, rate-limit headers disappear. Start Redis → INFO recovery within ≤30s, headers return. Boot with Redis down: app starts instantly.
5. **Hygiene units:** monkeypatched `_select_reference_rows` raising → references endpoint 500 envelope, not `[]`; tampered ES256 signature → exactly one forced refresh then 401.

## Critical files
- `shared/auth/jwt.py`, `backend/app/api/auth.py`, `backend/app/main.py`, `backend/app/deps.py`, `backend/app/errors.py`
- Minor: `backend/app/services/references_service.py`, `backend/app/services/case_service.py` (coordinated with design 6)
