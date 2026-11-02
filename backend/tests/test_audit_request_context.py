"""
audit_logs.ip_address / user_agent used to be NULL on every row: write_audit_log
took no such arguments and nothing populated the columns. They are now filled
from the ambient request context published by the request-id middleware.

These tests pin the two halves of that contract — inside a request the columns
are populated, outside one they stay absent — plus the trust boundary, since an
IP that can be spoofed is worse than no IP at all.
"""
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from backend.app.middleware import request_context
from backend.app.middleware.request_context import (
    capture_request_context,
    get_client_ip,
    get_user_agent,
)
from backend.app.services import audit_service


class _FakeSupabase:
    """Records insert payloads the way the real client would receive them."""

    def __init__(self) -> None:
        self.inserts: list[tuple[str, dict]] = []

    def table(self, name: str):
        self._name = name
        return self

    def insert(self, payload: dict):
        self.inserts.append((self._name, payload))
        return self

    def execute(self):
        return None


def _app_writing_audit(fake: _FakeSupabase) -> FastAPI:
    app = FastAPI()

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        capture_request_context(request)
        return await call_next(request)

    @app.get("/thing")
    def thing():
        audit_service.write_audit_log(
            fake,
            user_id="11111111-1111-1111-1111-111111111111",
            action="create",
            resource_type="conversation",
        )
        return {"ok": True}

    return app


def test_audit_row_carries_ip_and_user_agent_inside_a_request() -> None:
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    resp = client.get(
        "/thing",
        headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "Mozilla/5.0 (probe)"},
    )
    assert resp.status_code == 200

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert payload["ip_address"] == "203.0.113.9"
    assert payload["user_agent"] == "Mozilla/5.0 (probe)"
    # The pre-existing columns must be untouched by the change.
    assert payload["action"] == "create"
    assert payload["resource_type"] == "conversation"


def test_cf_connecting_ip_is_ignored_while_trust_cf_headers_is_off() -> None:
    """The trust boundary is resolve_client_ip's, not ours.

    With TRUST_CF_HEADERS off (the default), a client-supplied CF-Connecting-IP
    must NOT win — otherwise anyone could forge the audit trail's origin by
    sending the header directly to the Railway origin.
    """
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    client.get(
        "/thing",
        headers={
            "CF-Connecting-IP": "198.51.100.7",
            "X-Forwarded-For": "203.0.113.9",
            "User-Agent": "probe",
        },
    )

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert payload["ip_address"] == "203.0.113.9"


def test_long_user_agent_is_truncated() -> None:
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    client.get("/thing", headers={"User-Agent": "A" * 5000})

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert len(payload["user_agent"]) == 400


def test_outside_a_request_the_columns_are_omitted() -> None:
    """Scripts, APScheduler jobs and agent workers have no request context.

    They must keep writing exactly the payload they always did — no keys with
    None values, which would otherwise overwrite nothing but add noise.
    """
    assert get_client_ip() is None
    assert get_user_agent() is None

    fake = _FakeSupabase()
    audit_service.write_audit_log(
        fake,
        user_id="11111111-1111-1111-1111-111111111111",
        action="delete",
        resource_type="account",
    )

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert "ip_address" not in payload
    assert "user_agent" not in payload


def test_audit_write_never_raises_even_if_the_insert_explodes() -> None:
    """The fire-and-forget guarantee must survive the new lookups."""

    class _Exploding(_FakeSupabase):
        def execute(self):
            raise RuntimeError("db down")

    audit_service.write_audit_log(
        _Exploding(),
        user_id="11111111-1111-1111-1111-111111111111",
        action="create",
        resource_type="conversation",
    )


# ---------------------------------------------------------------------------
# M-1: a malformed IP must not be a way to keep an action out of the audit log
# (agents_reports/security_review_2026-09-25.md, library_guardrails_hardening 1c)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "forged",
    [
        "x",
        "not an ip",
        "999.1.1.1",
        "203.0.113.9:443",
        "<script>",
        "fe80::1%eth0",
        # The leftmost hop is what resolves. A bad one is dropped, it does not
        # promote the next hop, which the same client may have chosen.
        "x, 203.0.113.9",
    ],
)
def test_invalid_ip_is_dropped_and_the_row_is_still_written(forged: str) -> None:
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    resp = client.get("/thing", headers={"X-Forwarded-For": forged, "User-Agent": "probe"})
    assert resp.status_code == 200

    rows = [p for name, p in fake.inserts if name == "audit_logs"]
    assert len(rows) == 1
    assert "ip_address" not in rows[0]
    assert rows[0]["user_agent"] == "probe"
    assert rows[0]["action"] == "create"


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("203.0.113.9", "203.0.113.9"),
        ("  203.0.113.9  ", "203.0.113.9"),
        ("2001:db8::1", "2001:db8::1"),
        ("2001:0DB8:0000:0000:0000:0000:0000:0001", "2001:db8::1"),
    ],
)
def test_valid_ip_is_stored_in_canonical_form(raw: str, stored: str) -> None:
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    client.get("/thing", headers={"X-Forwarded-For": raw})

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert payload["ip_address"] == stored


def test_resolver_failure_leaves_no_ip_and_does_not_break_the_request(monkeypatch) -> None:
    def explode(_request):
        raise RuntimeError("resolver failed")

    monkeypatch.setattr("backend.app.middleware.rate_limit.resolve_client_ip", explode)
    fake = _FakeSupabase()
    client = TestClient(_app_writing_audit(fake))

    assert client.get("/thing", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 200

    _, payload = next(p for p in fake.inserts if p[0] == "audit_logs")
    assert "ip_address" not in payload


class _RejectsRequestContext(_FakeSupabase):
    """Fails the way Postgres does on a bad INET: the whole INSERT, and only
    while the offending columns are present."""

    def execute(self):
        _, payload = self.inserts[-1]
        if "ip_address" in payload or "user_agent" in payload:
            raise RuntimeError("invalid input syntax for type inet")
        return None


class _AlwaysFails(_FakeSupabase):
    def execute(self):
        raise RuntimeError("db down")


def test_failed_insert_is_retried_once_without_the_request_context() -> None:
    fake = _RejectsRequestContext()
    client = TestClient(_app_writing_audit(fake))

    resp = client.get(
        "/thing", headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "probe"}
    )
    assert resp.status_code == 200

    rows = [p for name, p in fake.inserts if name == "audit_logs"]
    assert len(rows) == 2
    assert rows[0]["ip_address"] == "203.0.113.9"
    assert rows[0]["user_agent"] == "probe"
    # The surviving row keeps everything except the two caller-influenced columns.
    assert "ip_address" not in rows[1]
    assert "user_agent" not in rows[1]
    assert rows[1]["user_id"] == "11111111-1111-1111-1111-111111111111"
    assert rows[1]["action"] == "create"
    assert rows[1]["resource_type"] == "conversation"


def test_retry_keeps_resource_id_and_metadata() -> None:
    fake = _RejectsRequestContext()
    token_ip = request_context._CLIENT_IP.set("203.0.113.9")
    try:
        audit_service.write_audit_log(
            fake,
            user_id="11111111-1111-1111-1111-111111111111",
            action="delete",
            resource_type="account",
            resource_id="22222222-2222-2222-2222-222222222222",
            metadata={"reason": "requested"},
        )
    finally:
        request_context._CLIENT_IP.reset(token_ip)

    _, retried = fake.inserts[-1]
    assert retried["resource_id"] == "22222222-2222-2222-2222-222222222222"
    assert retried["metadata"] == {"reason": "requested"}
    assert "ip_address" not in retried


def test_retry_runs_with_only_a_user_agent_in_the_row() -> None:
    fake = _RejectsRequestContext()
    token_ua = request_context._USER_AGENT.set("probe")
    try:
        audit_service.write_audit_log(
            fake,
            user_id="11111111-1111-1111-1111-111111111111",
            action="create",
            resource_type="conversation",
        )
    finally:
        request_context._USER_AGENT.reset(token_ua)

    assert len(fake.inserts) == 2
    assert "user_agent" not in fake.inserts[-1][1]


def test_retry_happens_once_and_a_second_failure_is_swallowed() -> None:
    fake = _AlwaysFails()
    client = TestClient(_app_writing_audit(fake))

    resp = client.get(
        "/thing", headers={"X-Forwarded-For": "203.0.113.9", "User-Agent": "probe"}
    )

    assert resp.status_code == 200
    assert len(fake.inserts) == 2


def test_no_retry_when_the_row_carries_no_request_context() -> None:
    """The identical insert would fail identically, and during a database outage
    a second attempt only doubles the wait on the caller."""
    fake = _AlwaysFails()

    audit_service.write_audit_log(
        fake,
        user_id="11111111-1111-1111-1111-111111111111",
        action="delete",
        resource_type="account",
    )

    assert len(fake.inserts) == 1
