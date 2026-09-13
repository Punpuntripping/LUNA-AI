"""
audit_logs.ip_address / user_agent used to be NULL on every row: write_audit_log
took no such arguments and nothing populated the columns. They are now filled
from the ambient request context published by the request-id middleware.

These tests pin the two halves of that contract — inside a request the columns
are populated, outside one they stay absent — plus the trust boundary, since an
IP that can be spoofed is worse than no IP at all.
"""
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

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
