"""Access-tiers Phase B — LIBRARY ENFORCEMENT at the HTTP boundary.

Plan: ``.claude/plans/access_tiers_gating.md`` §4.4 / §4.5 + PART 9 traps 1/2/3/5/6
Decisions: ``.claude/plans/access_tiers_gating_DECISIONS.md`` D11, D12, D14, D16.1, D16.2.

Phase A tested Layer B as a function (``backend/tests/test_library_gating.py``).
This file tests the ROUTES that call it, because every bug Phase B can introduce
lives in the wiring rather than in the decision:

  * ``GET /library/full/{type}/{key}`` — resolution → entitlement → content, in
    that order, with a 402 (never a 401) and NO content bytes on refusal.
  * the five hub endpoints — depth by tier, and the ``Cache-Control`` branch that
    keeps an authed hub body out of the shared hour-cache.

⚠ THE LOAD-BEARING TEST IN THIS FILE is ``test_authed_hub_is_never_shared_cached``
(+ its anon twin). Every other assertion here protects revenue; that one protects
correctness — a tier-varying body in a shared cache leaks a subscriber's page to
the next anonymous visitor, and no amount of entitlement logic upstream can undo
it. See D11 / PART 9 trap 2.

Fixture style + the in-memory PostgREST stand-in are REUSED from the Phase A file
(``FakeSupabase`` applies real filters and real ON CONFLICT DO NOTHING semantics,
which is what makes "charged exactly once" a real assertion rather than a mock
replay). The app under test is a throwaway FastAPI carrying only this router, so
the ``Depends`` wiring is exercised for real without booting Logfire/Redis.
"""
from __future__ import annotations

import json
from typing import Any, Optional

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.app.api import public_library as pl
from backend.app.deps import get_current_user_optional, get_supabase
from backend.app.errors import LunaHTTPException, luna_exception_handler
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import case_service, library_service as ls

# Reuse the Phase A stand-in + row builders verbatim — one fake, one set of
# semantics, so the two files can never drift on what a quota row looks like.
from backend.tests.test_library_gating import (  # noqa: F401
    FREE_PERIOD,
    PRO_PERIOD,
    REG_ID,
    RESETS_AT,
    USER,
    FakeSupabase,
    quota_row,
    unlock_row,
)

AUTH_ID = "auth-0000-1111"
REG_SLUG = "nizam-al-amal"
ART_SLUG = "madda-3"
CIRC_ID = "cccccccc-0000-0000-0000-000000000001"
CIRC_SLUG = "taamim-1"
FORM_ID = "ffffffff-0000-0000-0000-000000000001"
FORM_SLUG = "namudhaj-1"

CANARY = "CANARY-FULL-BODY-اسرار"


@pytest.fixture(autouse=True)
def _clear_module_caches():
    """``_gate_defaults_cache`` / ``_published_ids_cache`` are module-level TTL
    caches (PART 9 trap 1 — they are global BECAUSE they are tier-free). One
    test's seeded policy must never leak into the next."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    # Same story for the unfiltered section-total memo in the route module.
    pl._total_pages_memo.clear()
    # The route limiter's Redis-less fallback window is ALSO process-global and
    # would otherwise leak a spent budget from one test into the next.
    library_rate_limit._fallback.reset()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    pl._total_pages_memo.clear()
    library_rate_limit._fallback.reset()


class _User:
    """Stands in for AuthUser — the routes only ever read ``auth_id``."""

    auth_id = AUTH_ID
    email = "lawyer@example.com"
    role = "authenticated"


def _app(supabase: Any, user: Optional[_User] = None) -> FastAPI:
    """A throwaway app carrying ONLY the library router.

    The route-scoped 20/min limiter is overridden away: it is a module-level
    singleton whose Redis-less fallback is a PROCESS-LOCAL window, so leaving it
    live would make request 21 of the whole FILE fail for reasons that have
    nothing to do with entitlement (it does exactly that — caught 2026-07-27).
    Its presence on the route, and that it really runs, are asserted separately
    in section 8.
    """
    app = FastAPI()
    app.state.redis = None
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(pl.router)
    app.dependency_overrides[get_supabase] = lambda: supabase
    app.dependency_overrides[get_current_user_optional] = lambda: user
    app.dependency_overrides[library_rate_limit] = lambda: None
    return app


def _client(supabase: Any, user: Optional[_User] = None) -> TestClient:
    return TestClient(_app(supabase, user))


@pytest.fixture(autouse=True)
def _map_auth_id_to_user_id(monkeypatch):
    """``AuthUser.auth_id`` → ``users.user_id`` (D16.1). The real helper uses
    ``.maybe_single()``, which the stand-in does not model; the mapping itself is
    covered by case_service's own tests."""
    monkeypatch.setattr(
        case_service,
        "get_user_id",
        lambda supabase, auth_id: USER if auth_id == AUTH_ID else None,
    )


# ---------------------------------------------------------------------------
# Corpus fixtures — a gated نظام, a مادة, a تعميم, a form.
# ---------------------------------------------------------------------------


def _corpus(**overrides: Any) -> FakeSupabase:
    """One gated regulation (18 مواد ⇒ cost 1), reachable by slug, plus a
    circular and an APPROVED+PUBLISHED form. No ``seo_gate_defaults`` rows, so
    ``resolve_gate`` lands on its fail-closed 'gated' default (Phase A convention).
    """
    tables: dict[str, Any] = {
        "seo_item_meta": [
            {"content_type": "regulation", "content_id": REG_ID, "slug": REG_SLUG,
             "seo_tier": None, "gate_override": None},
            {"content_type": "circular", "content_id": CIRC_ID, "slug": CIRC_SLUG,
             "seo_tier": None, "gate_override": None},
        ],
        "seo_articles": [
            {"regulation_id": REG_ID, "article_no": i, "article_label": f"المادة {i}",
             "slug": f"madda-{i}", "chunk_id": f"ch-{i}",
             "article_text": f"{CANARY} — نص المادة {i}",
             "extraction_status": "extracted"}
            for i in range(1, 19)
        ],
        "seo_sharh": [],
        "chunks_v2": [
            {"id": f"ch-{i}", "regulation_id": REG_ID, "content": f"{CANARY} chunk {i}"}
            for i in range(1, 19)
        ],
        "circulars": [{"id": CIRC_ID, "content": f"{CANARY} — نص التعميم"}],
        "forms": [
            {"id": FORM_ID, "slug": FORM_SLUG, "review_status": "approved",
             "is_published": True, "body_md": f"{CANARY} — نص النموذج"},
        ],
    }
    tables.update(overrides)
    return FakeSupabase(**tables)


def _open_tier_corpus() -> FakeSupabase:
    """The same نظام, pinned OPEN by its sidecar ``seo_tier`` (§1.6 open tier)."""
    fake = _corpus()
    fake.tables["seo_item_meta"] = [
        {"content_type": "regulation", "content_id": REG_ID, "slug": REG_SLUG,
         "seo_tier": "open", "gate_override": None},
    ]
    return fake


def _full_url(content_type: str, key: str) -> str:
    return f"/api/v1/library/full/{content_type}/{key}"


def _has_content(body: dict) -> bool:
    """Any actual document bytes in the payload? The canary is embedded in every
    seeded body, so this is a byte-level check rather than a shape check."""
    return CANARY in json.dumps(body, ensure_ascii=False)


# ===========================================================================
# 1. /library/full — anonymous
# ===========================================================================


def test_anon_reveal_is_402_with_reason_anonymous_and_no_content() -> None:
    """D14: anonymous refusal is 402, NEVER 401 — this endpoint is reached from
    public pages and a 401 trips the frontend's global redirect-to-login."""
    fake = _corpus()
    fake.quota_row = quota_row()
    res = _client(fake).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402
    body = res.json()
    assert body["reason"] == "anonymous"
    assert body["error"]["code"] == "LIBRARY_ANONYMOUS"
    assert not _has_content(body)
    assert res.headers["cache-control"] == "private, no-store"
    assert fake.tables["library_unlocks"] == []      # anon never writes a row


def test_anon_reveal_of_an_OPEN_TIER_item_is_also_refused() -> None:
    """RESOLVED POLICY QUESTION (asked by the Phase B brief).

    ``resolve_access`` checks ``user_id is None`` at step 2, BEFORE it resolves
    the item's gate at step 3. So an open-tier نظام is refused for anon here with
    ``reason='anonymous'`` — /library/full serves NO anonymous caller, ever, not
    even for an item whose gate is 'open'.

    That is the correct behaviour, not an oversight: an open item's ANON page
    (``/public/library/regulations/{slug}``) already ships its full bytes
    untruncated, so anon loses nothing by being refused on the reveal endpoint —
    while the alternative (letting anon through whenever the gate says 'open')
    would put a tier-varying, entitlement-evaluated response on a path anonymous
    clients can hammer, for zero product gain.

    The same open item IS free for a signed-in caller — asserted below in
    ``test_open_tier_item_is_free_for_a_signed_in_user``.
    """
    fake = _open_tier_corpus()
    fake.quota_row = quota_row()
    res = _client(fake).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402
    assert res.json()["reason"] == "anonymous"
    assert not _has_content(res.json())


def test_open_tier_item_is_free_for_a_signed_in_user() -> None:
    """Counterpart to the test above: gate 'open' ⇒ served, no charge, no row."""
    fake = _open_tier_corpus()
    fake.quota_row = quota_row(limit=10, used=10)   # exhausted, and irrelevant
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 200
    assert _has_content(res.json())
    assert fake.tables["library_unlocks"] == []


# ===========================================================================
# 2. /library/full — free tier: grant, re-read, exhaustion
# ===========================================================================


def test_free_user_with_quota_gets_content_and_is_charged_once() -> None:
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    client = _client(fake, _User())

    first = client.get(_full_url("regulation", REG_SLUG))
    assert first.status_code == 200, first.text
    assert _has_content(first.json())
    assert first.headers["cache-control"] == "private, no-store"

    rows = fake.tables["library_unlocks"]
    assert len(rows) == 1
    assert rows[0]["content_type"] == "regulation"
    assert rows[0]["content_id"] == REG_ID          # the SIDECAR id, not the slug
    assert rows[0]["period_key"] == FREE_PERIOD
    assert rows[0]["cost"] == 1
    assert rows[0]["surface"] == "library"

    # The RPC row is a snapshot; reflect the charge the way the DB would.
    fake.quota_row = quota_row(limit=10, used=1)
    second = client.get(_full_url("regulation", REG_SLUG))
    assert second.status_code == 200
    assert _has_content(second.json())
    assert len(fake.tables["library_unlocks"]) == 1  # charged ONCE, not twice


def test_free_user_at_the_limit_is_refused_with_the_reset_date() -> None:
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=10)
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402
    body = res.json()
    assert body["reason"] == "quota_exhausted"
    assert body["error"]["code"] == "LIBRARY_QUOTA_EXCEEDED"
    assert (body["used"], body["limit"]) == (10, 10)
    assert body["resets_at"] == RESETS_AT
    assert body["detail"] == "تم استهلاك رصيد فتح المصادر لهذه الفترة."
    assert not _has_content(body)
    assert fake.tables["library_unlocks"] == []


def test_refusal_never_leaks_content_for_any_reason() -> None:
    """Sweep every refusal reason a real caller can reach on this route and
    assert the same thing about all of them: no bytes."""
    cases = {
        "anonymous": (None, quota_row()),
        "quota_exhausted": (_User(), quota_row(limit=10, used=10)),
        "locked": (_User(), quota_row(locked=True)),
    }
    for reason, (user, qrow) in cases.items():
        fake = _corpus()
        fake.quota_row = qrow
        res = _client(fake, user).get(_full_url("regulation", REG_SLUG))
        assert res.status_code == 402, reason
        assert res.json()["reason"] == reason
        assert not _has_content(res.json()), reason
        assert res.headers["cache-control"] == "private, no-store", reason


def test_frozen_library_refusal_carries_the_shelf_count() -> None:
    """Downgraded user, paid-era row: refused, but the shelf count drives the
    «لديك {n} مصدراً محفوظاً في مكتبتك» upgrade CTA (§5B.4)."""
    fake = _corpus()
    fake.quota_row = quota_row(plan="free", limit=10, used=0, period_key=FREE_PERIOD)
    fake.tables["library_unlocks"] = [
        unlock_row(period_key=PRO_PERIOD, cost=1),
        unlock_row(content_type="judgment", content_id="case-9", period_key=PRO_PERIOD),
    ]
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402
    body = res.json()
    assert body["reason"] == "frozen_library"
    assert body["stored_count"] == 2
    assert not _has_content(body)


def test_a_paid_user_reaches_a_frozen_row_again() -> None:
    """§1.2 predicate clause 1: re-upgrading unfreezes the whole shelf."""
    fake = _corpus()
    fake.quota_row = quota_row(plan="pro", limit=200, used=0,
                               period_key="pro:20260701:0")
    fake.tables["library_unlocks"] = [unlock_row(period_key=PRO_PERIOD, cost=1)]
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 200
    assert _has_content(res.json())
    assert len(fake.tables["library_unlocks"]) == 1     # nothing re-charged


# ===========================================================================
# 3. /library/full — resolution order + per-type keys
# ===========================================================================


def test_an_unknown_slug_is_404_and_is_never_charged() -> None:
    """Resolution runs BEFORE entitlement: a 404 must not cost an unlock."""
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("regulation", "la-yujad"))

    assert res.status_code == 404
    assert res.json()["error"]["message"] == "النظام غير موجود"
    assert fake.tables["library_unlocks"] == []
    # Even the 404 is unshareable: a cached one would survive the publish that
    # fixes it, and this path has nothing an intermediary should store.
    assert res.headers["cache-control"] == "private, no-store"


def test_an_unsupported_content_type_is_404() -> None:
    fake = _corpus()
    fake.quota_row = quota_row()
    res = _client(fake, _User()).get(_full_url("service", "anything"))
    assert res.status_code == 404
    assert res.json()["error"]["message"] == "المحتوى غير موجود"


def test_a_malformed_article_key_is_404_not_a_charge() -> None:
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("article", REG_SLUG))
    assert res.status_code == 404
    assert res.json()["error"]["message"] == "المادة غير موجودة"
    assert fake.tables["library_unlocks"] == []


def test_article_reveal_charges_at_the_article_key() -> None:
    """The ledger row must carry the sidecar article key ``{reg_id}#{no}`` — the
    same id space ``resolve_gate``/D5 and the مكتبتي shelf use."""
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("article", f"{REG_SLUG}/{ART_SLUG}"))

    assert res.status_code == 200, res.text
    assert _has_content(res.json())
    rows = fake.tables["library_unlocks"]
    assert len(rows) == 1
    assert (rows[0]["content_type"], rows[0]["content_id"]) == ("article", f"{REG_ID}#3")


def test_an_unlocked_regulation_covers_its_articles_over_http(monkeypatch) -> None:
    """D5 end-to-end: the route passes ``parent_regulation_id`` through, so a
    مادة clicked out of a نظام the user just read is NOT re-charged."""
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]
    res = _client(fake, _User()).get(_full_url("article", f"{REG_SLUG}/{ART_SLUG}"))

    assert res.status_code == 200
    assert len(fake.tables["library_unlocks"]) == 1     # no second row


def test_circular_reveal_charges_at_the_sidecar_id() -> None:
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("circular", CIRC_SLUG))

    assert res.status_code == 200, res.text
    assert _has_content(res.json())
    rows = fake.tables["library_unlocks"]
    assert (rows[0]["content_type"], rows[0]["content_id"]) == ("circular", CIRC_ID)


# ---- PART 9 trap 6: the forms liability gate outranks entitlement ----------


def test_approved_form_is_revealed_to_an_entitled_user() -> None:
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("form", FORM_SLUG))

    assert res.status_code == 200, res.text
    assert _has_content(res.json())
    rows = fake.tables["library_unlocks"]
    assert (rows[0]["content_type"], rows[0]["content_id"]) == ("form", FORM_ID)


def test_unapproved_form_is_refused_to_a_MAX_subscriber() -> None:
    """PART 9 trap 6 — the liability gate (``review_status='approved' AND
    is_published``) survives EVERY tier. A Max subscriber with 1,000 unlocks and
    an unlimited-looking balance still cannot see an unapproved form, and is not
    charged for the attempt.

    It surfaces as 404, not 402: a draft form must stay indistinguishable from a
    missing one (the same rule the anon page follows), and 402 would confirm the
    slug exists.
    """
    fake = _corpus()
    fake.tables["forms"] = [
        {"id": FORM_ID, "slug": FORM_SLUG, "review_status": "draft",
         "is_published": False, "body_md": f"{CANARY} — مسودة"},
    ]
    fake.quota_row = quota_row(plan="max", limit=1000, used=0,
                               period_key="max:20260701:0")
    res = _client(fake, _User()).get(_full_url("form", FORM_SLUG))

    assert res.status_code == 404
    assert res.json()["error"]["message"] == "النموذج غير موجود"
    assert not _has_content(res.json())
    assert fake.tables["library_unlocks"] == []


def test_published_but_unapproved_form_is_also_refused() -> None:
    """Both halves of the predicate are required, not either."""
    fake = _corpus()
    fake.tables["forms"] = [
        {"id": FORM_ID, "slug": FORM_SLUG, "review_status": "draft",
         "is_published": True, "body_md": f"{CANARY} — مسودة منشورة"},
    ]
    fake.quota_row = quota_row(plan="max", limit=1000, used=0,
                               period_key="max:20260701:0")
    res = _client(fake, _User()).get(_full_url("form", FORM_SLUG))
    assert res.status_code == 404
    assert fake.tables["library_unlocks"] == []


# ===========================================================================
# 4. مكتبتي shelf write — D16.2 (exactly one record_use per reveal)
# ===========================================================================


def _install_fake_items_service(monkeypatch) -> list[tuple]:
    """Inject a stand-in ``library_items_service`` (the B2 agent owns the real
    one; it lands this same wave). Returns the call log."""
    import types

    from backend.app import services as services_pkg

    calls: list[tuple] = []

    async def record_use(supabase, user_id, content_type, content_id):
        calls.append((user_id, content_type, content_id))

    module = types.ModuleType("backend.app.services.library_items_service")
    module.record_use = record_use
    monkeypatch.setattr(
        services_pkg, "library_items_service", module, raising=False
    )
    return calls


def test_a_charged_reveal_DOES_record_one_use(monkeypatch) -> None:
    """The reveal shelves the item — it is the ONLY thing that can.

    FINAL MODEL (user decision 2026-07-28) — everything in مكتبتي is ungated:

      view a GATED page            → nothing. Not shelved, not charged. This is
                                     what keeps the free summary layer free
                                     (§5.1): ten skimmed summaries must not cost
                                     ten unlocks.
      view an OPEN item            → LibraryUseBeacon shelves it, free.
      reveal / «عرض المصدر» / «حفظ» → unlock AND shelf, server-side.

    So for a gated item the reveal is the first moment it may enter the shelf,
    and the beacon deliberately never fires for it. The two cover DISJOINT sets,
    which is what makes "exactly once" true.

    (This file has asserted three different rules on this line as the policy
    moved; the model above is the settled one. If it changes again, change the
    beacon's `gate` guard in the same commit — the disjointness is the invariant,
    not either half on its own.)
    """
    calls = _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 200
    assert calls == [(USER, "regulation", REG_ID)]


def test_a_free_re_read_still_records_a_use(monkeypatch) -> None:
    """The shelf counts USES, not purchases: an already-unlocked re-read is
    still one use, or «الأكثر استخداماً» would rank by first purchase and never
    move again."""
    calls = _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=1)
    fake.tables["library_unlocks"] = [unlock_row(period_key=FREE_PERIOD, cost=1)]
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 200
    assert len(calls) == 1


def test_a_REFUSED_reveal_shelves_nothing(monkeypatch) -> None:
    """Nothing unreadable may reach مكتبتي — that is the whole rule."""
    calls = _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=10)  # exhausted
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402
    assert calls == []


def test_a_refused_reveal_records_no_use(monkeypatch) -> None:
    calls = _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=10)
    assert _client(fake, _User()).get(_full_url("regulation", REG_SLUG)).status_code == 402
    assert calls == []


def test_a_shelf_write_failure_never_breaks_the_read(monkeypatch) -> None:
    """D16.2: a shelf-write failure must never break a content read."""
    import types

    from backend.app import services as services_pkg

    async def _boom(*_a, **_k):
        raise RuntimeError("library_items table is on fire")

    module = types.ModuleType("backend.app.services.library_items_service")
    module.record_use = _boom
    monkeypatch.setattr(services_pkg, "library_items_service", module, raising=False)

    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)
    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 200
    assert _has_content(res.json())


# ===========================================================================
# 5. hub_page_allowed — depth by tier (D12)
# ===========================================================================


def test_hub_page_allowed_per_tier() -> None:
    assert ls.ANON_HUB_MAX_PAGE == 1
    assert ls.FREE_HUB_MAX_PAGE == 3

    assert ls.hub_page_allowed(1, "anon") is True
    assert ls.hub_page_allowed(2, "anon") is False

    assert ls.hub_page_allowed(3, "free") is True
    assert ls.hub_page_allowed(4, "free") is False

    assert ls.hub_page_allowed(99, "paid") is True
    assert ls.hub_page_allowed(10_000, "paid") is True


def test_hub_page_allowed_treats_an_unknown_tier_as_anon() -> None:
    """Fail-closed: a typo'd tier can only ever hand out LESS depth."""
    assert ls.hub_page_allowed(1, "premium") is True
    assert ls.hub_page_allowed(2, "premium") is False
    assert ls.hub_page_allowed(2, "") is False


# ===========================================================================
# 6. THE CACHE TRAP — D11 / PART 9 trap 2
# ===========================================================================

HUBS = [
    ("/api/v1/public/library/regulations", "list_regulations_hub",
     "regulations_hub_total_pages"),
    ("/api/v1/public/library/compliance", "list_compliance_hub",
     "compliance_hub_total_pages"),
    ("/api/v1/public/library/circulars", "list_circulars_hub",
     "circulars_hub_total_pages"),
    ("/api/v1/public/library/judgments", "list_judgments_hub",
     "judgments_hub_total_pages"),
    ("/api/v1/public/library/forms", "list_forms_hub", "forms_hub_total_pages"),
]


@pytest.fixture
def stub_hubs(monkeypatch):
    """Replace every hub lister with a canned envelope. The hub QUERY is covered
    by the wing test files; what is under test here is the tier/cache wiring."""
    for _path, lister, counter in HUBS:
        monkeypatch.setattr(
            ls, lister,
            lambda _supabase, **kw: {"items": [], "page": int(kw.get("page") or 1),
                                     "total_pages": 40},
        )
        monkeypatch.setattr(ls, counter, lambda *_a, **_k: 40)


def _hub_fake(**qrow: Any) -> FakeSupabase:
    fake = FakeSupabase()
    fake.quota_row = quota_row(**qrow) if qrow else quota_row()
    return fake


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_authed_hub_is_never_shared_cached(stub_hubs, path) -> None:
    """⚠ THE LOAD-BEARING TEST (D11 / trap 2).

    Hub bodies now vary by tier. The hour-cache header was previously set at the
    TOP of every hub handler, unconditionally — so the first authed request would
    park a subscriber's deep page in the shared cache and it would be replayed to
    the next anonymous visitor (and to Googlebot) for an hour. Whenever a user is
    present the response MUST be private/no-store.
    """
    fake = _hub_fake()
    res = _client(fake, _User()).get(path, params={"page": 1})

    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "private, no-store"
    assert "max-age" not in res.headers["cache-control"]
    assert "public" not in res.headers["cache-control"]


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_anonymous_hub_keeps_the_shared_hour_cache(stub_hubs, path) -> None:
    """The anon variant is the one that must stay cacheable — it is the SEO
    surface. ``Vary: Authorization`` is belt-and-braces for any intermediary
    that would otherwise reuse it for an authed request."""
    res = _client(_hub_fake()).get(path, params={"page": 1})

    assert res.status_code == 200, res.text
    assert res.headers["cache-control"] == "public, max-age=3600"
    assert "Authorization" in res.headers.get("vary", "")


def test_an_authed_hub_hit_cannot_poison_the_next_anon_hit(stub_hubs) -> None:
    """The Phase B done-criterion, expressed as a test: curl anon immediately
    after an authed hit and confirm the anon body is the anon body — deep page,
    paid caller, then the same URL anonymously."""
    fake = _hub_fake(plan="pro", limit=200, period_key="pro:20260701:0")
    path = "/api/v1/public/library/regulations"

    authed = _client(fake, _User()).get(path, params={"page": 9})
    assert authed.status_code == 200
    assert authed.json()["cap_reached"] is False        # paid sees page 9
    assert authed.headers["cache-control"] == "private, no-store"

    anon = _client(_hub_fake()).get(path, params={"page": 9})
    assert anon.json()["cap_reached"] is True           # anon sees the wall
    assert anon.json()["items"] == []
    assert anon.headers["cache-control"] == "public, max-age=3600"


# ===========================================================================
# 7. Hub depth + max_page over HTTP
# ===========================================================================


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_anon_hub_caps_at_page_one(stub_hubs, path) -> None:
    """Tightened from 3 to 1 by policy (§4.5) — discovery is sitemap + mesh."""
    client = _client(_hub_fake())

    ok = client.get(path, params={"page": 1})
    assert ok.json()["cap_reached"] is False
    assert ok.json()["max_page"] == 1

    walled = client.get(path, params={"page": 2})
    assert walled.status_code == 200            # a CTA wall, never a 4xx
    assert walled.json()["cap_reached"] is True
    assert walled.json()["items"] == []
    # ⚠ CHANGED 2026-07-28, RE-SCOPED 2026-07-30. It first asserted
    # `total_pages == 40` ("the real count still ships"); §2.1 replaced that with
    # a flat ceiling because a wall carrying a FILTERED count is a counting
    # oracle. But the clamp was applied to every anon request, filtered or not,
    # which also hid the plain section size — and that number is public (nav
    # copy, hub blurbs, sitemap) and is what the paginator needs to offer a
    # clickable last page. So: unfiltered reports the truth, filtered still gets
    # the ceiling and still skips the query. Both halves are asserted in
    # backend/tests/test_library_filter_hardening.py §3.
    assert walled.json()["total_pages"] == 40

    # ⚠ THE PROBE IS NO LONGER ``q`` (bm25_navigation_search.md D9): search is
    # registered-only and an anonymous ``?q=`` is DROPPED, which leaves an
    # UNFILTERED request — so it reports the real section total, and the flat
    # ceiling has to be probed with a filter anon can still send. One per wing;
    # the ceiling behaviour itself is unchanged.
    anon_filter = {
        HUBS[0][0]: {"doc_type": "law_statute"},
        HUBS[1][0]: {"provider": "وزارة"},
        HUBS[2][0]: {"entity": "3f8c1d2e-0000-4000-8000-000000000001"},
        HUBS[3][0]: {"court_level": "appeal"},
        HUBS[4][0]: {"category": ls.FORM_CATEGORIES[0]},
    }[path]
    filtered = client.get(path, params={"page": 2, **anon_filter})
    assert filtered.json()["cap_reached"] is True
    assert filtered.json()["total_pages"] == pl._ANON_WALL_TOTAL_PAGES
    assert filtered.json()["total_pages"] < 40

    # And the D9 half: a dropped ``q`` is not a filter, so the wall answers with
    # the section total — the same number for every query string.
    dropped = client.get(path, params={"page": 2, "q": "نظام"})
    assert dropped.json()["cap_reached"] is True
    assert dropped.json()["total_pages"] == 40


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_free_hub_caps_at_page_three(stub_hubs, path) -> None:
    client = _client(_hub_fake(plan="free", limit=10), _User())

    assert client.get(path, params={"page": 3}).json()["cap_reached"] is False
    walled = client.get(path, params={"page": 4}).json()
    assert walled["cap_reached"] is True
    assert walled["max_page"] == 3


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_paid_hub_is_unbounded(stub_hubs, path) -> None:
    fake = _hub_fake(plan="max", limit=1000, period_key="max:20260701:0")
    body = _client(fake, _User()).get(path, params={"page": 99}).json()

    assert body["cap_reached"] is False
    assert body["max_page"] >= 99


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_max_page_reflects_the_callers_tier_on_every_hub(stub_hubs, path) -> None:
    """D12: the frontend sizes its CTA wall from this number, so it must report
    the CALLER's cap — the bug being prevented is the old hardcoded
    ``max_anon_page = ANON_HUB_MAX_PAGE`` default."""
    anon = _client(_hub_fake()).get(path, params={"page": 1}).json()
    free = _client(_hub_fake(plan="free"), _User()).get(
        path, params={"page": 1}).json()
    paid = _client(
        _hub_fake(plan="pro", limit=200, period_key="pro:20260701:0"), _User()
    ).get(path, params={"page": 1}).json()

    assert (anon["max_page"], free["max_page"]) == (1, 3)
    assert paid["max_page"] > 3


@pytest.mark.parametrize("path", [h[0] for h in HUBS])
def test_max_anon_page_is_kept_as_a_deprecated_alias(stub_hubs, path) -> None:
    """One release of overlap so the frontend does not break mid-build (D12).
    It now carries the CALLER's cap despite its name."""
    for user, expected in ((None, 1), (_User(), 3)):
        body = _client(_hub_fake(plan="free"), user).get(
            path, params={"page": 1}).json()
        assert body["max_anon_page"] == body["max_page"] == expected


def test_a_locked_account_browses_like_a_free_one(stub_hubs) -> None:
    """D12: 'a locked account browsing hubs is harmless — treat it as free, not
    paid.' It must not be silently promoted by the ``is_paid`` fallthrough."""
    fake = FakeSupabase()
    fake.quota_row = quota_row(locked=True)
    body = _client(fake, _User()).get(
        "/api/v1/public/library/regulations", params={"page": 1}).json()

    assert body["max_page"] == 3
    assert _client(fake, _User()).get(
        "/api/v1/public/library/regulations", params={"page": 4}
    ).json()["cap_reached"] is True


def test_a_quota_read_failure_degrades_the_hub_to_free_not_500(stub_hubs) -> None:
    """A hub page carries zero gated bytes, so a quota-RPC hiccup must not 500 a
    public page — it degrades to the free cap."""
    class _Boom(FakeSupabase):
        def rpc(self, name, params):
            raise RuntimeError("RPC unavailable")

    fake = _Boom()
    res = _client(fake, _User()).get(
        "/api/v1/public/library/regulations", params={"page": 3})

    assert res.status_code == 200
    assert res.json()["cap_reached"] is False
    assert res.json()["max_page"] == 3
    assert res.headers["cache-control"] == "private, no-store"


# ===========================================================================
# 8. Wiring assertions (things a refactor silently drops)
# ===========================================================================


def _route(path: str):
    return next(r for r in pl.router.routes if getattr(r, "path", None) == path)


def test_full_reveal_is_wired_to_the_route_rate_limiter() -> None:
    """D13.2 — 20/min on the reveal family, keyed off the VERIFIED caller, and
    SHARED with the workspace reference-source endpoint: ONE budget, so nobody
    buys 40/min by alternating between the two.

    The shared singleton must be on the route *itself*, not a per-route copy —
    a second ``RouteRateLimiter`` instance would give each endpoint its own
    window and quietly double the reveal budget.
    """
    route = _route("/api/v1/library/full/{content_type}/{key:path}")
    deps = [d.call for d in route.dependant.dependencies]
    assert library_rate_limit in deps


def test_the_reveal_route_actually_runs_with_the_LIVE_rate_limiter() -> None:
    """Regression guard for the 422 above: the other tests override the limiter
    away, which would hide a broken ``Depends`` signature completely. This one
    runs the real dependency (no Redis ⇒ its fail-closed in-process window) and
    asserts the route still reaches its own logic — a 402 refusal, not a 422.
    """
    fake = _corpus()
    fake.quota_row = quota_row()

    app = FastAPI()
    app.state.redis = None
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(pl.router)
    app.dependency_overrides[get_supabase] = lambda: fake
    app.dependency_overrides[get_current_user_optional] = lambda: None
    # NOTE: the limiter is deliberately NOT overridden here.
    res = TestClient(app).get(_full_url("regulation", REG_SLUG))

    assert res.status_code == 402, res.text
    assert res.json()["reason"] == "anonymous"


def test_every_hub_uses_optional_auth_not_required_auth() -> None:
    """A public hub must never answer 401 — that would eject a visitor whose
    token merely expired (and would break Googlebot the day a header leaks in)."""
    from backend.app.deps import get_current_user

    for path, _lister, _counter in HUBS:
        deps = [d.call for d in _route(path).dependant.dependencies]
        assert get_current_user_optional in deps, path
        assert get_current_user not in deps, path


def test_the_reveal_uses_optional_auth_so_anon_gets_402_not_401() -> None:
    route = _route("/api/v1/library/full/{content_type}/{key:path}")
    from backend.app.deps import get_current_user

    deps = [d.call for d in route.dependant.dependencies]
    assert get_current_user_optional in deps
    assert get_current_user not in deps


def test_the_unmetered_library_comment_is_gone() -> None:
    """§4.4: the comment declaring library reads deliberately unmetered is now
    the OPPOSITE of the policy. This guards against a merge resurrecting it."""
    import inspect

    source = inspect.getsource(pl)
    assert "reading the full library never costs points" not in source
    assert "Deliberately NO quota/points wiring" not in source


# ===========================================================================
# «المصادر الرسمية» — part of what the unlock buys
# ===========================================================================
#
# User decision 2026-07-28, REVERSING the plan's §1.2 «the official source URL is
# always shown, gated or not» and its §1.3 never-gated classification.
#
# Rationale: the block is not a generic outbound link. It is a deep link carrying
# the source system's own identifier (the BOE law UUID, the MoJ judgment id), so
# publishing it across 3,373 regulations hands out a ready-made
# slug → official-ID crosswalk of the corpus.
#
# The split that makes this safe: the ANON payload withholds it at LAYER A (keyed
# on the ITEM's gate, never on the viewer, so the ISR cache stays sound), and the
# metered reveal serves it alongside the content the unlock paid for.


def test_the_reveal_serves_the_official_sources(monkeypatch) -> None:
    _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID,
         "landing_url": "https://laws.boe.gov.sa/BoeLaws/Laws/LawDetails/abc/1",
         "pdf_url": None}
    ]
    fake.quota_row = quota_row(limit=10, used=0)

    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 200
    assert res.json()["official_sources"] == [
        {"title": "الموقع الرسمي",
         "href": "https://laws.boe.gov.sa/BoeLaws/Laws/LawDetails/abc/1"}
    ]


def test_a_refused_reveal_leaks_no_official_source(monkeypatch) -> None:
    """The whole point: a reader who cannot unlock must not receive the deep link
    by any route, including the refusal body."""
    _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID, "landing_url": "https://laws.boe.gov.sa/SECRET/1",
         "pdf_url": None}
    ]
    fake.quota_row = quota_row(limit=10, used=10)  # exhausted

    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 402
    assert "laws.boe.gov.sa" not in res.text
    assert "official_sources" not in res.text


def test_anon_reveal_leaks_no_official_source(monkeypatch) -> None:
    _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID, "landing_url": "https://laws.boe.gov.sa/SECRET/1",
         "pdf_url": None}
    ]
    res = _client(fake, None).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 402
    assert "laws.boe.gov.sa" not in res.text


def test_official_sources_failure_never_breaks_a_paid_reveal(monkeypatch) -> None:
    """The content the user just paid for matters more than the link to it."""
    _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.fail_tables = {"regulations_v2"}
    fake.quota_row = quota_row(limit=10, used=0)

    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 200
    assert res.json()["official_sources"] == []
    assert res.json()["sections"], "content must still be served"


def test_article_and_form_reveals_carry_no_official_sources(monkeypatch) -> None:
    """A مادة page never had its own block (its parent نظام carries it) and
    FormDetail has no such field — so there is nothing to gate or to serve."""
    _install_fake_items_service(monkeypatch)
    fake = _corpus()
    fake.quota_row = quota_row(limit=10, used=0)

    res = _client(fake, _User()).get(_full_url("form", FORM_SLUG))
    assert res.status_code == 200
    assert res.json()["official_sources"] == []


def test_an_OPEN_tier_page_PUBLISHES_its_official_sources(monkeypatch) -> None:
    """An open نظام is open end-to-end — the source link included (2026-08-01).

    Withholding exists to keep the slug → official-ID crosswalk out of anonymous
    hands. An open-tier نظام has no crosswalk left to protect: this same payload
    already ships its entire text to crawlers, so hiding the link to its own
    official source protects nothing and just makes the page worse.

    The "exactly one renderer" invariant survives because an open item never
    reveals — nothing on it is gated — so the block cannot appear twice.
    """
    fake = _open_tier_corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID, "landing_url": "https://laws.boe.gov.sa/x/1", "pdf_url": None}
    ]

    doc = ls.get_regulation_doc(fake, REG_SLUG)
    assert doc["gate"] == "open", "fixture is not open-tier — test proves nothing"
    assert doc["official_sources"] == [
        {"title": "الموقع الرسمي", "href": "https://laws.boe.gov.sa/x/1"}
    ]


def test_a_GATED_page_still_withholds_its_official_sources(monkeypatch) -> None:
    """The withholding rule is unchanged for everything that IS gated — which is
    the whole corpus bar the 54 open-tier أنظمة."""
    fake = _corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID, "landing_url": "https://laws.boe.gov.sa/x/1", "pdf_url": None}
    ]

    doc = ls.get_regulation_doc(fake, REG_SLUG)
    assert doc["gate"] == "gated", "fixture is not gated — test proves nothing"
    assert doc["official_sources"] == []
    assert "laws.boe.gov.sa" not in json.dumps(doc, ensure_ascii=False)


def test_an_OPEN_regulation_ships_EVERY_article_to_anon() -> None:
    """The open tier's whole point: crawlers and signed-out readers get the
    entire نظام, and the page has nothing left to offer a reveal for.

    This is the regression the 3-مادة preview caused — it ran unconditionally, so
    نظام العمل published 3 of its 232 مواد and wore a «سجّل مجانًا لعرض النظام
    كاملًا» gate over a document nothing gates. `gated` is derived downstream from
    exactly the three values asserted here, so proving them proves the CTA is off.
    """
    fake = _open_tier_corpus()
    fake.tables["regulations_v2"] = [{"id": REG_ID, "landing_url": None}]

    doc = ls.get_regulation_doc(fake, REG_SLUG)

    assert doc["gate"] == "open", "fixture is not open-tier — test proves nothing"
    assert len(doc["visible_sections"]) == 18, "an open نظام must ship every مادة"
    assert doc["hidden_section_count"] == 0
    assert not any(s["is_truncated"] for s in doc["visible_sections"])
    # Set, not list: the stand-in orders `article_no` as TEXT (1, 10, 11, 2…),
    # while Postgres orders the real integer column. Coverage of the ORDER
    # itself belongs where a real DB is in play, not here.
    assert {s["id"] for s in doc["visible_sections"]} == {
        f"art-{i}" for i in range(1, 19)
    }


def test_a_GATED_regulation_still_ships_only_the_three_article_preview() -> None:
    """The preview is untouched for everything that IS gated."""
    fake = _corpus()
    fake.tables["regulations_v2"] = [{"id": REG_ID, "landing_url": None}]

    doc = ls.get_regulation_doc(fake, REG_SLUG)

    assert doc["gate"] == "gated", "fixture is not gated — test proves nothing"
    assert len(doc["visible_sections"]) == 3
    assert doc["hidden_section_count"] == 15


def test_an_OPEN_regulation_renders_a_shared_fallback_chunk_ONCE() -> None:
    """A multi-مادة fallback chunk must not repeat itself once per مادة.

    ``extraction_status != 'extracted'`` means the body IS the owning chunk, and
    such a chunk spans a run of مواد («المادة (1) – المادة (4): …»). At 3 sections
    the duplication was invisible; across a whole open نظام it is tens of
    thousands of characters of the same paragraphs. The run collapses to one
    section titled by the chunk, and the swallowed مواد survive as ``also_ids`` so
    every TOC row still has an anchor to land on.
    """
    fake = _corpus(
        seo_item_meta=[
            {"content_type": "regulation", "content_id": REG_ID, "slug": REG_SLUG,
             "seo_tier": "open", "gate_override": None},
        ],
        seo_articles=[
            {"regulation_id": REG_ID, "article_no": i, "article_label": f"المادة {i}",
             "slug": f"madda-{i}", "chunk_id": "ch-run", "article_text": None,
             "extraction_status": "chunk_fallback"}
            for i in range(1, 5)
        ],
        chunks_v2=[
            {"id": "ch-run", "regulation_id": REG_ID,
             "title": "المادة (1) – المادة (4): التعاريف",
             "content": f"{CANARY} — نص المواد ١–٤"},
        ],
        regulations_v2=[{"id": REG_ID, "landing_url": None}],
    )

    doc = ls.get_regulation_doc(fake, REG_SLUG)

    assert doc["gate"] == "open"
    assert len(doc["visible_sections"]) == 1, "the shared chunk rendered per-مادة"
    section = doc["visible_sections"][0]
    assert section["id"] == "art-1"
    assert section["title"] == "المادة (1) – المادة (4): التعاريف"
    assert section["also_ids"] == ["art-2", "art-3", "art-4"]
    assert doc["hidden_section_count"] == 0
    # One copy of the body, not four.
    assert json.dumps(doc, ensure_ascii=False).count(CANARY) == 1


def test_an_OPEN_tier_reveal_DOES_return_the_official_sources(monkeypatch) -> None:
    """...and the reveal is where they come from, for open and gated alike.

    On an open item the reveal is free (`reason='open'`, no ledger row), so this
    costs a signed-in reader nothing — it is anonymous access to the crosswalk
    that is being closed, not paid access to the link.
    """
    _install_fake_items_service(monkeypatch)
    fake = _open_tier_corpus()
    fake.tables["regulations_v2"] = [
        {"id": REG_ID, "landing_url": "https://laws.boe.gov.sa/x/1", "pdf_url": None}
    ]
    fake.quota_row = quota_row(limit=10, used=0)

    res = _client(fake, _User()).get(_full_url("regulation", REG_SLUG))
    assert res.status_code == 200
    assert res.json()["official_sources"] == [
        {"title": "الموقع الرسمي", "href": "https://laws.boe.gov.sa/x/1"}
    ]
    assert fake.tables["library_unlocks"] == [], "an open item must not be charged"
