"""«فتح الحكم / النظام / التعميم في ريحان» on the reference CARD.

Plan: ``.claude/plans/references_window_fixes.md`` item 1 (+ item 2's label).

The button existed only inside the reveal dialog, i.e. after the reader spent an
unlock — because ``library_url`` was computed only in the reveal endpoint. The
list payload never resolved a slug, so the card had nothing to link to.

What these tests pin, and why each one is load-bearing:

* **It is NAVIGATION, not content.** A link to a page that enforces its own
  access tier is not a reveal. Resolving it must never call ``resolve_access``,
  never write ``library_unlocks``, and never touch the quota — otherwise the
  reader is charged twice for one document and the balance chip moves when
  nothing was unlocked.
* **A compliance reference NEVER gets one.** ``_URL_PREFIX`` has no ``service``
  key: the /compliance wing was retired, so any link there is a 404 dressed up
  as navigation.
* **No slug ⇒ no button.** Never a hub fallback, never a guessed URL.
* **The resolution is BATCHED.** ≤4 round-trips for a whole panel regardless of
  reference count — the alternative (one ``reference_resolver`` pass per card) is
  both N round-trips and a code path that CHARGES.
* **Fail-soft.** A sidecar blip costs the buttons, never the panel.

No live DB. Supabase is the in-memory PostgREST stand-in from
``test_library_gating``, reused so the row-level query semantics are real.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

import pytest

from backend.app.services import library_items_service as lis
from backend.app.services import library_service as ls
from backend.app.services import references_service

from backend.tests.test_library_gating import FakeSupabase, quota_row

# ---------------------------------------------------------------------------
# ids
# ---------------------------------------------------------------------------

WI = "11111111-1111-4111-8111-111111111111"

CHUNK_ID = "aaaa1111-1111-4111-8111-111111111111"
REG_ID = "cccc3333-3333-4333-8333-333333333333"
CASE_ID = "dddd4444-4444-4444-8444-444444444444"
CASE_ID_2 = "dddd5555-5555-4555-8555-555555555555"
CIRC_ID = "eeee5555-5555-4555-8555-555555555555"
SVC_ID = "ffff6666-6666-4666-8666-666666666666"
# simple_search (plan §6.1a): ONE مادة and a WHOLE نظام, each with its own
# domain + ref_id prefix. ``regdoc:`` points at the SAME نظام the chunk above
# belongs to — that shared destination is the point (§6.2).
ART_ID = "aaaa9999-9999-4999-8999-999999999999"

CASE_SUBJECT = "نزاع عمالي حول مطالبة موظفة سابقة بمكافأة أعمال التفتيش الميداني"


def run(coro):
    """Run one coroutine to completion (no pytest-asyncio dependency)."""
    return asyncio.run(coro)


@pytest.fixture(autouse=True)
def _isolate_module_state():
    """``library_service``'s gate/published-id caches are module-level TTL caches;
    a seeded slug leaking into the next test would silently mint a URL."""
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()
    yield
    ls._gate_defaults_cache["value"] = None
    ls._gate_defaults_cache["expires_at"] = 0.0
    ls._published_ids_cache.clear()


def meta(content_type: str, content_id: str, slug: str) -> dict[str, Any]:
    return {"content_type": content_type, "content_id": content_id, "slug": slug}


def ref_row(
    *,
    n: int,
    domain: str,
    ref_id: str,
    item_id: Optional[str],
    relevance: str = "high",
) -> dict[str, Any]:
    return {
        "ref_pk": f"p{n}",
        "wi_id": WI,
        "item_id": item_id,
        "ref_id": ref_id,
        "domain": domain,
        "n": n,
        "relevance": relevance,
        "used": True,
        "sub_queries": [],
    }


class CountingSupabase(FakeSupabase):
    """FakeSupabase that records every table it is queried against.

    One ``.table(name)`` call is one PostgREST round-trip in this codebase (a
    chain is built and executed exactly once), so the list length IS the
    round-trip count — which is what the batching bound is asserted on.
    """

    def __init__(self, **tables: Any) -> None:
        super().__init__(**tables)
        self.queries: list[str] = []

    def table(self, name: str):  # type: ignore[override]
        self.queries.append(name)
        return super().table(name)


def corpus(**tables: Any) -> CountingSupabase:
    """The corpus rows the reference→URL resolver reads."""
    seeded: dict[str, Any] = {
        "quota_row": quota_row(),
        "chunks_v2": [
            {
                "id": CHUNK_ID,
                "regulation_id": REG_ID,
                "owns": {"MADDA": [7, 8, 9]},
                "content": "نص المقطع.",
            }
        ],
        "cases": [
            {
                "id": CASE_ID,
                "case_ref": "case-ref-1",
                "court": "المحكمة العمالية",
                "case_number": "123/45",
                "date_hijri": "1445/06/01",
                "short_summary": CASE_SUBJECT + ".",
                "summary": "## الملخص\n- " + CASE_SUBJECT + ".\n",
                "referenced_regulations": [],
            },
            {
                "id": CASE_ID_2,
                "case_ref": "case-ref-2",
                "court": "المحكمة التجارية",
                "case_number": "999/1",
                "short_summary": "نزاع تجاري حول عقد توريد مستلزمات ومعدات طبية.",
                "summary": "",
                "referenced_regulations": [],
            },
        ],
        "circulars": [{"id": CIRC_ID, "title": "تعميم مهم", "content": "ن" * 5000}],
        "articles_v2": [
            {
                "id": ART_ID,
                "regulation_id": REG_ID,
                "article_number": "81",
                "content": "نص المادة.",
            }
        ],
        "regulations_v2": [
            {
                "id": REG_ID,
                "title": "نظام العمل الصادر بالمرسوم",
                "clean_title": "نظام العمل",
                "landing_url": "https://laws.gov.sa/x",
                "llm_summary": "ملخص النظام.",
            }
        ],
        "services": [
            {
                "id": SVC_ID,
                "service_ref": "svc-1",
                "service_name_ar": "وزارة العدل - إصدار صك",
                "provider_name": "وزارة العدل",
                "service_context": "سياق الخدمة.",
                "service_url": "https://my.gov.sa/x",
                "url": "",
            }
        ],
        "entities": [],
        "seo_item_meta": [],
    }
    seeded.update(tables)
    quota = seeded.pop("quota_row")
    return CountingSupabase(quota_row=quota, **seeded)


# ===========================================================================
# 1. The batched resolver
# ===========================================================================


def test_published_judgment_gets_its_page() -> None:
    supabase = corpus(seo_item_meta=[meta("judgment", CASE_ID, "hukm-ummali-123")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID)],
        )
    )
    assert urls == {1: "/judgments/hukm-ummali-123"}


def test_unpublished_judgment_gets_no_url() -> None:
    """No sidecar slug ⇒ no page ⇒ no key at all, and the panel drops the button.

    Never a hub fallback: a button that promises the ruling and delivers a list
    is worse than a button that isn't there."""
    supabase = corpus(seo_item_meta=[])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID)],
        )
    )
    assert urls == {}


def test_compliance_reference_never_gets_a_url() -> None:
    """``_URL_PREFIX`` has NO ``service`` key — the wing was retired (2026-08-03).

    Seeding a ``service`` slug is the point of this test: even when the sidecar
    happens to carry one, a government service must render no in-app button,
    because there is no page behind it. Re-adding the prefix without rebuilding
    those pages ships 404s."""
    supabase = corpus(seo_item_meta=[meta("service", SVC_ID, "isdar-sak")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(
                    n=1,
                    domain="compliance",
                    ref_id="compliance:deadbeefcafebabe",
                    item_id=SVC_ID,
                )
            ],
        )
    )
    assert urls == {}
    # Not even a lookup was attempted — there is nothing to look up.
    assert "seo_item_meta" not in supabase.queries


def test_regulation_chunk_lifts_to_its_nizam_page() -> None:
    """``reg:<uuid>`` is a ``chunks_v2.id``, not a regulation id. The card links
    to the نظام page (user decision 2026-08-01), never to a مادة sub-page."""
    supabase = corpus(seo_item_meta=[meta("regulation", REG_ID, "nizam-al-amal")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=3, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID)],
        )
    )
    assert urls == {3: "/regulations/nizam-al-amal"}


# --- the two simple_search domains (plan §6.1a / §13 follow-up 1) ----------
#
# Wave 1 shipped both domains through the RESOLVER (so the reveal dialog's
# button worked) but not through this batched LIST path, which branched on
# cases/circulars/regulations only and fell through to "no button" by design.
# «فتح النظام في ريحان» was therefore missing from the CARD — the one place a
# reader sees it before spending an unlock.


def test_regulation_doc_reference_links_to_its_nizam_page() -> None:
    """``regdoc:<regulations_v2.id>`` — the id IS the نظام, so the only query is
    the sidecar. No chunk hop, no article hop."""
    supabase = corpus(seo_item_meta=[meta("regulation", REG_ID, "nizam-al-amal")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}",
                     item_id=REG_ID)],
        )
    )
    assert urls == {1: "/regulations/nizam-al-amal"}
    assert supabase.queries == ["seo_item_meta"]


def test_article_reference_lifts_to_its_nizam_page() -> None:
    """A مادة resolves to its نظام page — the SAME collapse ``_public_page_url``
    applies at ``ct == 'article'`` (user decision 2026-08-01), not a new
    ``/regulations/{reg}/{article}`` scheme. ``_URL_PREFIX`` still has no
    ``article`` key, deliberately."""
    supabase = corpus(seo_item_meta=[meta("regulation", REG_ID, "nizam-al-amal")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="articles", ref_id=f"article:{ART_ID}", item_id=ART_ID)],
        )
    )
    assert urls == {1: "/regulations/nizam-al-amal"}
    # One parent lookup (articles_v2 is a VIEW — no FK to embed through), then
    # the wing's single sidecar call.
    assert supabase.queries == ["articles_v2", "seo_item_meta"]


def test_the_new_domains_fall_back_to_their_ref_id_tails() -> None:
    """A row whose write-time ``item_id`` resolution failed still links, from the
    prefix alone — the same fallback the other three wings have."""
    supabase = corpus(seo_item_meta=[meta("regulation", REG_ID, "nizam-al-amal")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(n=1, domain="articles", ref_id=f"article:{ART_ID}", item_id=None),
                ref_row(n=2, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}",
                        item_id=None),
            ],
        )
    )
    assert urls == {1: "/regulations/nizam-al-amal", 2: "/regulations/nizam-al-amal"}


def test_a_regdoc_row_never_reads_a_reg_prefix() -> None:
    """§6.2, from this side. ``reg:`` carries a **chunks_v2.id**; accepting one as
    a ``regulations_v2.id`` here would hand the sidecar the wrong key and link the
    card at whatever document happens to share that uuid — a wrong page is worse
    than no page. With no ``item_id`` to fall back on, the row gets no button."""
    supabase = corpus(seo_item_meta=[meta("regulation", CHUNK_ID, "wrong-page")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="regulation_docs", ref_id=f"reg:{CHUNK_ID}",
                     item_id=None)],
        )
    )
    assert urls == {}
    assert supabase.queries == []


def test_an_unpublished_nizam_gives_the_new_domains_no_button_either() -> None:
    """No slug ⇒ no page ⇒ no key. Never a hub fallback for these two either."""
    supabase = corpus(seo_item_meta=[])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(n=1, domain="articles", ref_id=f"article:{ART_ID}", item_id=ART_ID),
                ref_row(n=2, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}",
                        item_id=REG_ID),
            ],
        )
    )
    assert urls == {}


def test_an_article_lookup_failure_only_costs_the_madda_cards() -> None:
    """Fail-soft per wing: an ``articles_v2`` blip must not take the نظام, the
    ruling or the تعميم buttons down with it."""
    supabase = corpus(
        seo_item_meta=[
            meta("regulation", REG_ID, "nizam-al-amal"),
            meta("judgment", CASE_ID, "hukm-ummali-123"),
        ]
    )
    supabase.fail_tables.add("articles_v2")

    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(n=1, domain="articles", ref_id=f"article:{ART_ID}", item_id=ART_ID),
                ref_row(n=2, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}",
                        item_id=REG_ID),
                ref_row(n=3, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
            ],
        )
    )
    assert urls == {2: "/regulations/nizam-al-amal", 3: "/judgments/hukm-ummali-123"}


def test_all_six_domains_share_one_sidecar_call_per_wing() -> None:
    """THE cost assertion, re-stated with the two new domains on the panel.

    Chunks, مواد and whole أنظمة all land in the ``regulation`` bucket, so they
    share ONE ``seo_item_meta`` call — the bound only moves from ≤4 to ≤5, and
    only because ``articles_v2`` needs its own parent hop."""
    rows: list[dict[str, Any]] = []
    n = 0
    for _ in range(5):
        n += 1
        rows.append(ref_row(n=n, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID))
        n += 1
        rows.append(
            ref_row(n=n, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID)
        )
        n += 1
        rows.append(
            ref_row(n=n, domain="circulars", ref_id=f"circular:{CIRC_ID}", item_id=CIRC_ID)
        )
        n += 1
        rows.append(
            ref_row(n=n, domain="articles", ref_id=f"article:{ART_ID}", item_id=ART_ID)
        )
        n += 1
        rows.append(
            ref_row(n=n, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}", item_id=REG_ID)
        )
        n += 1
        rows.append(
            ref_row(n=n, domain="compliance", ref_id="compliance:deadbeefcafebabe",
                    item_id=SVC_ID)
        )

    supabase = corpus(
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
            meta("circular", CIRC_ID, "taamim-muhim"),
        ]
    )
    urls = run(lis.public_page_urls_for_reference_rows(supabase, rows))

    assert len(rows) == 30
    assert len(urls) == 25          # every ref except the 5 compliance ones
    assert len(supabase.queries) <= 5, supabase.queries
    assert supabase.queries.count("chunks_v2") == 1
    assert supabase.queries.count("articles_v2") == 1
    # The headline: مواد + chunks + whole أنظمة = ONE regulation sidecar call.
    assert supabase.queries.count("seo_item_meta") == 3


def test_circular_uses_its_item_id_with_no_lookup() -> None:
    supabase = corpus(seo_item_meta=[meta("circular", CIRC_ID, "taamim-muhim")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=2, domain="circulars", ref_id=f"circular:{CIRC_ID}", item_id=CIRC_ID)],
        )
    )
    assert urls == {2: "/circulars/taamim-muhim"}
    # ``item_id`` IS ``circulars.id`` — the only query is the sidecar.
    assert supabase.queries == ["seo_item_meta"]


def test_null_item_id_falls_back_to_the_ref_id_tail() -> None:
    """Legacy rows whose write-time resolution failed still link, from the
    ``ref_id`` alone: ``reg:``/``circular:`` carry the uuid, ``case:`` carries
    the ``case_ref`` (resolved through the batch helper the write path owns)."""
    supabase = corpus(
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
            meta("circular", CIRC_ID, "taamim-muhim"),
        ]
    )
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=None),
                ref_row(n=2, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=None),
                ref_row(n=3, domain="circulars", ref_id=f"circular:{CIRC_ID}", item_id=None),
            ],
        )
    )
    assert urls == {
        1: "/judgments/hukm-ummali-123",
        2: "/regulations/nizam-al-amal",
        3: "/circulars/taamim-muhim",
    }


def test_resolution_is_batched_not_per_reference() -> None:
    """THE cost assertion. 24 references across all four domains must cost the
    same handful of round-trips as one — ≤4: the chunk→نظام lookup plus one
    sidecar lookup per wing present. A per-card ``reference_resolver`` pass would
    be O(n) round-trips AND would run the charging code path."""
    rows: list[dict[str, Any]] = []
    n = 0
    for _ in range(6):
        n += 1
        rows.append(ref_row(n=n, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID))
        n += 1
        rows.append(
            ref_row(n=n, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID)
        )
        n += 1
        rows.append(
            ref_row(n=n, domain="circulars", ref_id=f"circular:{CIRC_ID}", item_id=CIRC_ID)
        )
        n += 1
        rows.append(
            ref_row(
                n=n,
                domain="compliance",
                ref_id="compliance:deadbeefcafebabe",
                item_id=SVC_ID,
            )
        )

    supabase = corpus(
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
            meta("circular", CIRC_ID, "taamim-muhim"),
        ]
    )
    urls = run(lis.public_page_urls_for_reference_rows(supabase, rows))

    assert len(rows) == 24
    assert len(urls) == 18          # every ref except the 6 compliance ones
    assert len(supabase.queries) <= 4, supabase.queries
    assert supabase.queries.count("chunks_v2") == 1
    assert supabase.queries.count("seo_item_meta") == 3


def test_a_sidecar_failure_costs_the_buttons_not_the_panel() -> None:
    """Fail-soft, in the direction that keeps the panel rendering."""
    supabase = corpus(seo_item_meta=[meta("judgment", CASE_ID, "hukm-ummali-123")])
    supabase.fail_tables.add("seo_item_meta")

    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID)],
        )
    )
    assert urls == {}


def test_a_chunk_lookup_failure_only_costs_the_regulation_cards() -> None:
    """One wing failing must not take the others' buttons with it."""
    supabase = corpus(
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
        ]
    )
    supabase.fail_tables.add("chunks_v2")

    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
                ref_row(n=2, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID),
            ],
        )
    )
    assert urls == {1: "/judgments/hukm-ummali-123"}


def test_empty_input_makes_no_query_at_all() -> None:
    supabase = corpus()
    assert run(lis.public_page_urls_for_reference_rows(supabase, [])) == {}
    assert supabase.queries == []


def test_malformed_rows_are_skipped_never_raised_on() -> None:
    """A row with no ``n``, an unknown domain, or a junk ``ref_id`` yields no
    button — never an exception, never a guessed URL."""
    supabase = corpus(seo_item_meta=[meta("judgment", CASE_ID, "hukm-ummali-123")])
    urls = run(
        lis.public_page_urls_for_reference_rows(
            supabase,
            [
                {"n": None, "domain": "cases", "item_id": CASE_ID, "ref_id": ""},
                ref_row(n=2, domain="wat", ref_id="wat:1", item_id=CASE_ID),
                ref_row(n=3, domain="regulations", ref_id="reg:not-a-uuid", item_id=None),
                ref_row(n=4, domain="cases", ref_id="case:unknown-ref", item_id=None),
            ],
        )
    )
    assert urls == {}


# ===========================================================================
# 2. End to end — the panel payload
# ===========================================================================


def panel(supabase: FakeSupabase) -> list[dict[str, Any]]:
    return run(references_service.fetch_item_references_payload(supabase, WI))


def test_payload_carries_library_url_per_wing() -> None:
    """The list — not the reveal — is where the card gets its link now."""
    supabase = corpus(
        workspace_item_references=[
            ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
            ref_row(n=2, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID),
            ref_row(
                n=3,
                domain="compliance",
                ref_id="compliance:deadbeefcafebabe",
                item_id=SVC_ID,
            ),
        ],
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
            meta("service", SVC_ID, "isdar-sak"),
        ],
    )
    entries = panel(supabase)

    by_n = {e["n"]: e for e in entries}
    assert by_n[1]["library_url"] == "/judgments/hukm-ummali-123"
    assert by_n[2]["library_url"] == "/regulations/nizam-al-amal"
    # A government service has no page in our library — no button, ever.
    assert by_n[3]["library_url"] is None

    # The key is present on EVERY entry (never absent), so the client can treat
    # ``null`` as "no page" rather than "old payload".
    assert all("library_url" in e for e in entries)


def test_payload_carries_library_url_for_the_simple_search_domains() -> None:
    """End to end, through the real card builder: «فتح النظام في ريحان» now
    renders on a مادة card and a whole-نظام card, not only inside the reveal
    dialog the reader reaches by SPENDING an unlock. That asymmetry — the button
    existing only after payment — is the wave-1 gap this closes."""
    supabase = corpus(
        workspace_item_references=[
            ref_row(n=1, domain="articles", ref_id=f"article:{ART_ID}", item_id=ART_ID),
            ref_row(n=2, domain="regulation_docs", ref_id=f"regdoc:{REG_ID}",
                    item_id=REG_ID),
        ],
        seo_item_meta=[meta("regulation", REG_ID, "nizam-al-amal")],
    )
    entries = panel(supabase)

    by_n = {e["n"]: e for e in entries}
    assert by_n[1]["library_url"] == "/regulations/nizam-al-amal"
    assert by_n[2]["library_url"] == "/regulations/nizam-al-amal"
    # The mesh is untouched — distinct domains and prefixes, per §6.1a / §6.2.
    assert by_n[1]["domain"] == "articles"
    assert by_n[1]["ref_id"] == f"article:{ART_ID}"
    assert by_n[2]["domain"] == "regulation_docs"
    assert by_n[2]["ref_id"] == f"regdoc:{REG_ID}"
    # …and it stayed NAVIGATION: no charge, no ledger row, no quota RPC.
    assert supabase.tables["library_unlocks"] == []
    assert supabase.rpc_calls == []


def test_payload_library_url_is_null_when_nothing_is_published() -> None:
    supabase = corpus(
        workspace_item_references=[
            ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
        ],
        seo_item_meta=[],
    )
    entries = panel(supabase)
    assert entries[0]["library_url"] is None
    # …and the citation mesh is untouched by the absent link.
    assert entries[0]["ref_id"] == "case:case-ref-1"
    assert entries[0]["domain"] == "cases"


def test_the_link_is_navigation_and_is_never_metered() -> None:
    """It must not charge, must not write the money table, and must not even ask
    the quota RPC — a link to a page that enforces its own tier is not a reveal.
    Metering it would double-charge the reader and move the balance chip for an
    unlock that never happened."""
    supabase = corpus(
        workspace_item_references=[
            ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
            ref_row(n=2, domain="regulations", ref_id=f"reg:{CHUNK_ID}", item_id=CHUNK_ID),
        ],
        seo_item_meta=[
            meta("judgment", CASE_ID, "hukm-ummali-123"),
            meta("regulation", REG_ID, "nizam-al-amal"),
        ],
    )
    entries = panel(supabase)

    assert [e["library_url"] for e in entries] == [
        "/judgments/hukm-ummali-123",
        "/regulations/nizam-al-amal",
    ]
    assert supabase.tables["library_unlocks"] == []
    assert supabase.rpc_calls == []
    assert "library_items" not in supabase.tables


def test_a_judgment_card_is_labelled_with_its_subject_not_qadiya() -> None:
    """Item 2 ships WITH item 1 for a reason: the card's label and the H1 of the
    page its button opens are both ``judgment_subject()``, so they are the same
    sentence. A card reading «قضية» that opens a page titled «نزاع عمالي…» is the
    defect this pair exists to close."""
    from shared.seo.judgment_naming import judgment_subject

    supabase = corpus(
        workspace_item_references=[
            ref_row(n=1, domain="cases", ref_id="case:case-ref-1", item_id=CASE_ID),
        ],
        seo_item_meta=[meta("judgment", CASE_ID, "hukm-ummali-123")],
    )
    entry = panel(supabase)[0]

    case_row = next(c for c in supabase.tables["cases"] if c["id"] == CASE_ID)
    assert entry["title"] == judgment_subject(case_row)
    assert entry["title"] != "قضية"
    assert "نزاع عمالي" in entry["title"]
    # The SUBJECT alone — no « — المحكمة … 1445هـ» tail on a chat card.
    assert "1445" not in entry["title"]
    assert entry["title"].count("—") == 0
