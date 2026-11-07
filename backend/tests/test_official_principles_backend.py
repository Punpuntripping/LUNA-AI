"""Official judicial principles («مبدأ قضائي») — persistence, reveal, backend readers.

Plan ``.claude/plans/official_principles_refs.md`` §3.6 (builder D). Pins:

- migration 212 widens the live 6-domain CHECK by exactly ``principles``;
- ``reference_resolver``: ``principle:<uuid>`` → ``('principle', '')``,
  ``always_free`` (D8), and still FAILS CLOSED on a malformed id;
- ``references_service``: the list read (``by_domain`` → ``_build_principle_shells``
  → the card), the stub (``official_principle``, never the ``regulation``
  fallback), the reveal dispatch (``PrincipleSourceView``), and the write path's
  ``item_id``;
- the metered reveal endpoint: a principle is free — no ledger row, no shelf
  write, ``balance: null``, no in-app library link;
- ``public_blog_service`` / ``library_items_service`` /
  ``retrieval_artifacts_service`` / the editorial context trim each know the
  domain.

The two principle rows are ``official_principles`` nos. 2 and 343 (entity 18329),
copied from the live table on 2026-11-07 — never retyped.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from agents.deep_search_v4.aggregator.models import Reference
from agents.deep_search_v4.source_viewer import PrincipleSourceView
from agents.deep_search_v4.ura import principles as ura_principles
from agents.deep_search_v4.ura.principles import principle_from_row
from backend.app.api import workspace as workspace_api
from backend.app.services import (
    library_items_service,
    public_blog_service,
    reference_resolver,
    references_service,
    retrieval_artifacts_service,
)

# The reveal-endpoint harness + its module-state reset (gate caches, the shared
# 20/min limiter) — reused, not re-faked, exactly as that file reuses
# test_library_gating's FakeSupabase.
from backend.tests.test_reference_source import (  # noqa: F401 — fixture import
    CIRC_ID,
    USER_A,
    WI_A,
    _client,
    _install_fake_library_items,
    _isolate_module_state,
    _url,
    base_supabase,
    ref_row,
    run,
)

# --------------------------------------------------------------------------- #
# Live rows (official_principles nos. 2 and 343, entity 18329)
# --------------------------------------------------------------------------- #

P2_ID = "64052e1b-17d9-511d-8508-ed401e624d1a"
P343_ID = "8bb84190-29d5-59d6-9b36-23fcf35ff7ba"
P_GONE_ID = "9a9a9a9a-0000-4000-8000-000000000000"
SOURCE_URL = "https://drive.google.com/file/d/19wu0BYdazRzTB0xjmCkk0cgNu2O5L8qG/view"

ROW_P2 = {
    "id": P2_ID,
    "principle_ref": "18329_principles_0002",
    "entity_ref": "18329",
    "principle_no": 2,
    "chapter_no": 1,
    "chapter_title": "الأصول والقواعد",
    "text": "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة.",
    "content": "(هـ ق ع): (٢٥٤)، (١٣/١٠/١٣٩١)",  # never read — Arabic-Indic + codes
    "citations": [
        {
            "body": "الهيئة القضائية العليا",
            "printed": "(هـ ق ع): (٢٥٤)، (١٣/١٠/١٣٩١)",
            "body_code": "هـ ق ع",
            "date_hijri": "13/10/1391",
            "date_valid": True,
            "year_hijri": 1391,
            "decision_no": "254",
        }
    ],
    "issuing_bodies": ["هـ ق ع"],
    "issuing_body_names": ["الهيئة القضائية العليا"],
    "source": {"pages": [33, 33], "source_url": SOURCE_URL},
}

ROW_P343 = {
    "id": P343_ID,
    "principle_ref": "18329_principles_0343",
    "entity_ref": "18329",
    "principle_no": 343,
    "chapter_no": 23,
    "chapter_title": "إحياء الموات",
    "text": (
        "الإحياء إذا ثبت شرعًا ولم تكن الأرض مملوكة لأحد، وليس الإحياء معارضًا "
        "للمصلحة العامة، ولم تمنعه الجهات الرسمية حتى حصل الإحياء الصحيح، لم "
        "يَسُغ إهدار عمله، إلا إن ثبت حصول ضرر به على المصلحة العامة."
    ),
    "citations": [
        {
            "body": "مجلس القضاء الأعلى بهيئته الدائمة",
            "printed": "(م ق د): (٦١٤/٦)، (١٣/١١/١٤٢١)",
            "body_code": "م ق د",
            "date_hijri": "13/11/1421",
            "date_valid": True,
            "year_hijri": 1421,
            "decision_no": "614/6",
        }
    ],
    "issuing_bodies": ["م ق د"],
    "issuing_body_names": ["مجلس القضاء الأعلى بهيئته الدائمة"],
    "source": {"pages": [132, 132], "source_url": SOURCE_URL},
}

P2_TITLE = "المبدأ رقم 2 — الأصول والقواعد"
P2_DECISION = "القرار 254 بتاريخ 13/10/1391هـ"


def _principle_row(n: int, pid: str, *, item_id: Any = "same", **over: Any) -> dict:
    """A ``workspace_item_references`` row for a principle (item_id = the id)."""
    return ref_row(
        n=n,
        ref_id=over.pop("ref_id", f"principle:{pid}"),
        domain="principles",
        item_id=pid if item_id == "same" else item_id,
        **over,
    )


def _supabase(*refs: dict, principles: list[dict] | None = None):
    return base_supabase(
        official_principles=[ROW_P2, ROW_P343] if principles is None else principles,
        workspace_item_references=list(refs),
    )


# =========================================================================== #
# 1. Migration 212
# =========================================================================== #

_MIGRATION = (
    Path(__file__).resolve().parents[2]
    / "shared" / "db" / "migrations"
    / "212_workspace_item_references_principles_domain.sql"
)
# The CHECK verified live on 2026-11-07 (migration 136).
_LIVE_DOMAINS = {
    "regulations", "compliance", "cases", "circulars", "articles", "regulation_docs",
}


def _check_domains() -> set[str]:
    sql = _MIGRATION.read_text(encoding="utf-8")
    check = re.search(r"CHECK \(domain IN \((.*?)\)\)", sql, re.S)
    assert check, "212 must define the domain CHECK"
    return set(re.findall(r"'([a-z_]+)'", check.group(1)))


def test_migration_212_widens_the_live_check_by_principles_only() -> None:
    """A dropped live domain would fail every batch carrying it — the migration
    102 failure mode, aimed the other way."""
    assert _check_domains() == _LIVE_DOMAINS | {"principles"}


def test_migration_212_is_idempotent_and_never_scans_under_the_write_lock() -> None:
    sql = _MIGRATION.read_text(encoding="utf-8")
    assert "DROP CONSTRAINT IF EXISTS workspace_item_references_domain_check" in sql
    assert ")) NOT VALID;" in sql
    assert "VALIDATE CONSTRAINT workspace_item_references_domain_check" in sql


@pytest.mark.parametrize("domain", sorted(_LIVE_DOMAINS | {"principles"}))
def test_every_check_domain_has_its_own_stub_type(domain: str) -> None:
    """§9 trap 5: a domain lands in the CHECK, ``by_domain`` and
    ``_stub_reference`` together. A stub that falls back to ``regulation`` for
    anything but ``regulations`` renders through the wrong frontend arm."""
    stub = references_service._stub_reference(
        {"n": 1, "domain": domain, "ref_id": "x:1", "relevance": "medium"}
    )
    assert stub.domain == domain
    if domain != "regulations":
        assert stub.source_type != "regulation"


# =========================================================================== #
# 2. reference_resolver — free, keyless, still fail-closed
# =========================================================================== #


def test_principle_ref_resolves_free_with_no_shelf_key() -> None:
    resolved = run(
        reference_resolver.resolve_ref(None, f"principle:{P2_ID}", domain="principles")
    )
    assert resolved is not None
    assert resolved.as_tuple() == ("principle", "")
    assert resolved.always_free is True
    assert resolved.free_reason == "official_principle"
    # The reveal falls back to the source view's own title.
    assert resolved.title == ""
    assert resolved.parent_regulation_id is None
    assert run(
        reference_resolver.resolve_ref_id(None, f"principle:{P2_ID}")
    ) == ("principle", "")


def test_principle_resolution_reads_no_table() -> None:
    """Nothing is charged, so there is nothing to look up — a down table must not
    turn a free principle into a refusal."""
    supabase = _supabase()
    supabase.fail_tables.add("official_principles")
    resolved = run(reference_resolver.resolve_ref(supabase, f"principle:{P2_ID}"))
    assert resolved is not None and resolved.always_free is True


def test_prefixless_principle_row_falls_back_to_its_domain() -> None:
    resolved = run(reference_resolver.resolve_ref(None, P2_ID, domain="principles"))
    assert resolved is not None and resolved.content_type == "principle"


def test_principle_item_id_wins_over_a_damaged_ref_tail() -> None:
    resolved = run(
        reference_resolver.resolve_ref(
            None, "principle:not-a-uuid", domain="principles", item_id=P2_ID
        )
    )
    assert resolved is not None and resolved.always_free is True


@pytest.mark.parametrize(
    "ref_id, domain",
    [
        ("principle:not-a-uuid", "principles"),
        ("principle:", "principles"),
        ("", "principles"),
        ("principle:" + P2_ID[:-1], None),
    ],
)
def test_malformed_principle_refs_fail_closed(ref_id: str, domain: Any) -> None:
    assert run(reference_resolver.resolve_ref(None, ref_id, domain=domain)) is None


# =========================================================================== #
# 3. references_service — the list read
# =========================================================================== #


def test_list_builds_the_principle_card_from_one_batched_read(monkeypatch) -> None:
    """Two principles — one keyed by ``item_id``, one by its ``ref_id`` alone —
    come back as full cards from ONE ``official_principles`` read."""
    reads: list[list[str]] = []
    real_fetch = ura_principles.fetch_principles_by_id

    def _spy(supabase, ids):
        reads.append(sorted(ids))
        return real_fetch(supabase, ids)

    monkeypatch.setattr(ura_principles, "fetch_principles_by_id", _spy)
    supabase = _supabase(
        _principle_row(1, P2_ID),
        _principle_row(2, P343_ID, item_id=None),
    )

    payload = run(references_service.fetch_item_references_payload(supabase, WI_A))

    assert reads == [sorted([P2_ID, P343_ID])]
    first, second = payload
    assert first["source_type"] == "official_principle"
    assert first["domain"] == "principles"
    assert first["ref_id"] == f"principle:{P2_ID}"
    assert first["title"] == P2_TITLE
    assert first["regulation_title"] == "الهيئة القضائية العليا"
    assert first["entity_name"] == "الهيئة القضائية العليا"
    assert first["decision_line"] == P2_DECISION
    assert first["url"] == SOURCE_URL
    # Snippet = the rule itself (build_snippet's principle branch).
    assert first["snippet"] == ROW_P2["text"]
    assert first["relevance"] == "high"
    assert first["has_source"] is True
    # No library page for a principle — its exit is the PDF on the card.
    assert first["library_url"] is None
    # Phase C: never a body in the list.
    assert first["source_view"] is None

    assert second["title"] == "المبدأ رقم 343 — إحياء الموات"
    assert second["decision_line"] == "القرار 614/6 بتاريخ 13/11/1421هـ"
    assert second["has_source"] is True
    # Never the stored ``content`` (Arabic-Indic digits, codes).
    assert "٢" not in str(payload)


def test_a_vanished_principle_is_an_official_principle_stub() -> None:
    supabase = _supabase(_principle_row(1, P_GONE_ID), _principle_row(2, P2_ID))
    payload = run(references_service.fetch_item_references_payload(supabase, WI_A))

    stub, card = payload
    assert stub["source_type"] == "official_principle"
    assert stub["domain"] == "principles"
    assert stub["title"] == references_service._STUB_TITLE
    assert stub["has_source"] is False
    assert card["title"] == P2_TITLE and card["has_source"] is True


def test_a_failed_principle_read_degrades_to_stubs_not_a_500() -> None:
    supabase = _supabase(_principle_row(1, P2_ID))
    supabase.fail_tables.add("official_principles")
    payload = run(references_service.fetch_item_references_payload(supabase, WI_A))
    assert [(e["source_type"], e["has_source"]) for e in payload] == [
        ("official_principle", False)
    ]


def test_a_malformed_ref_never_reaches_the_uuid_batch(monkeypatch) -> None:
    """``official_principles.id`` is a uuid column: one bad tail inside ``in_``
    would 400 the batch and stub EVERY principle on the panel."""
    reads: list[list[str]] = []

    def _spy(_supabase, ids):
        reads.append(list(ids))
        return {}

    monkeypatch.setattr(ura_principles, "fetch_principles_by_id", _spy)
    supabase = _supabase(
        _principle_row(1, P2_ID),
        _principle_row(2, "x", item_id=None, ref_id="principle:not-a-uuid"),
    )
    run(references_service.fetch_item_references(supabase, WI_A))
    assert reads == [[P2_ID]]


def test_principles_mix_with_the_other_domains_in_n_order() -> None:
    supabase = _supabase(
        ref_row(n=1, ref_id=f"circular:{CIRC_ID}", domain="circulars", item_id=CIRC_ID),
        _principle_row(2, P2_ID),
    )
    refs = run(references_service.fetch_item_references(supabase, WI_A))
    assert [(r.n, r.domain) for r in refs] == [(1, "circulars"), (2, "principles")]


# =========================================================================== #
# 4. references_service — the reveal build
# =========================================================================== #


def test_reveal_builds_the_principle_source_view() -> None:
    supabase = _supabase()
    view = run(
        references_service.build_reference_source_view(supabase, _principle_row(1, P2_ID))
    )
    assert isinstance(view, PrincipleSourceView)
    assert view.source_type == "official_principle"
    assert view.title == P2_TITLE
    assert view.chapter == "الأصول والقواعد"
    assert view.text == ROW_P2["text"]
    assert view.decisions == [
        {"body": "الهيئة القضائية العليا", "decision_no": "254", "date_hijri": "13/10/1391"}
    ]
    assert view.pages == [33]
    assert view.url == SOURCE_URL


def test_reveal_of_a_vanished_principle_is_none() -> None:
    supabase = _supabase()
    assert run(
        references_service.build_reference_source_view(supabase, _principle_row(1, P_GONE_ID))
    ) is None


# =========================================================================== #
# 5. The reveal endpoint — free, unshelved, unlinked
# =========================================================================== #


def test_principle_reveal_is_free_writes_nothing_and_has_no_balance(monkeypatch) -> None:
    supabase = _supabase(_principle_row(1, P2_ID))
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)
    # The REAL build, not the harness stub: the body must come from the table.
    monkeypatch.setattr(
        workspace_api,
        "build_reference_source_view",
        references_service.build_reference_source_view,
    )

    res = client.get(_url())
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["domain"] == "principles"
    assert body["source_view"]["source_type"] == "official_principle"
    assert body["source_view"]["text"] == ROW_P2["text"]
    assert body["unlocked"]["content_type"] == "principle"
    assert body["unlocked"]["content_id"] == ""
    assert body["unlocked"]["charged"] is False
    assert body["unlocked"]["reason"] == "open"
    assert body["unlocked"]["title"] == P2_TITLE
    assert body["balance"] is None
    assert body["library_url"] is None
    assert res.headers["cache-control"] == "private, no-store"

    # No ledger row, no quota read, no «مكتبتي» shelf write.
    assert supabase.tables["library_unlocks"] == []
    assert not any(name == "get_user_quota_state" for name, _ in supabase.rpc_calls)
    assert uses == []


def test_a_vanished_principle_reveal_404s_at_no_cost(monkeypatch) -> None:
    supabase = _supabase(_principle_row(1, P_GONE_ID))
    uses = _install_fake_library_items(monkeypatch)
    client = _client(monkeypatch, supabase)
    monkeypatch.setattr(
        workspace_api,
        "build_reference_source_view",
        references_service.build_reference_source_view,
    )

    res = client.get(_url())
    assert res.status_code == 404
    assert supabase.tables["library_unlocks"] == []
    assert uses == []


# =========================================================================== #
# 6. references_service — the write path
# =========================================================================== #


@dataclass
class _Result:
    data: list[dict] = field(default_factory=list)


class _InsertRecorder:
    def __init__(self) -> None:
        self.batches: list[list[dict]] = []

    def insert(self, payload):
        self.batches.append(payload if isinstance(payload, list) else [payload])
        return self

    def execute(self) -> _Result:
        return _Result(self.batches[-1] if self.batches else [])


class _WriteSupabase:
    def __init__(self) -> None:
        self.refs = _InsertRecorder()

    def table(self, name: str):
        if name == "workspace_item_references":
            return self.refs
        raise AssertionError(f"a principle must cost no lookup at write time ({name})")


def _principle_ref(n: int, ref_id: str) -> Reference:
    return Reference(
        n=n,
        source_type="official_principle",
        regulation_title="الهيئة القضائية العليا",
        title=P2_TITLE,
        snippet="",
        relevance="high",
        ref_id=ref_id,
        domain="principles",
    )


def test_persist_mints_item_id_from_the_principle_ref() -> None:
    supabase = _WriteSupabase()
    ura = principle_from_row(ROW_P2, relevance="high")

    written = references_service.persist_item_references(
        supabase,
        WI_A,
        [_principle_ref(1, f"principle:{P2_ID}")],
        [ura],
        cited_numbers=[1],
        ref_to_sub_queries={1: [0, 2]},
    )

    assert written == 1
    (row,) = supabase.refs.batches[0]
    assert row["domain"] == "principles"
    assert row["ref_id"] == f"principle:{P2_ID}"
    assert row["item_id"] == P2_ID
    assert row["used"] is True
    assert row["sub_queries"] == [0, 2]
    # Word count from the aggregator view the LLM grounded against.
    assert row["content_word_count"] > len(ROW_P2["text"].split())


@pytest.mark.parametrize(
    "ref_id",
    ["principle:not-a-uuid", P2_ID, f"case:{P2_ID}"],
)
def test_persist_never_mints_an_item_id_from_a_bad_principle_ref(ref_id: str) -> None:
    """No bare-uuid fallback: only the ``principle:`` prefix names the table.
    The row still writes (``ref_id`` satisfies ``has_key``) with item_id NULL."""
    supabase = _WriteSupabase()
    references_service.persist_item_references(
        supabase, WI_A, [_principle_ref(1, ref_id)], None, [], {}
    )
    (row,) = supabase.refs.batches[0]
    assert row["item_id"] is None
    assert row["ref_id"] == ref_id


# =========================================================================== #
# 7. The other backend readers
# =========================================================================== #


def test_blog_reveal_flag_pairs_the_principle_prefix_with_its_domain() -> None:
    derive = public_blog_service._derive_has_source
    assert derive({"ref_id": f"principle:{P2_ID}", "domain": "principles"}) is True
    # Charged by one dispatcher, refused by the other — never offered.
    assert derive({"ref_id": f"principle:{P2_ID}", "domain": "cases"}) is False
    assert derive({"ref_id": "principle:not-a-uuid", "domain": "principles"}) is False
    assert derive({"ref_id": P2_ID, "domain": "principles"}) is False


class _NoTables:
    def table(self, name: str):
        raise AssertionError(f"no lookup expected, got {name}")

    def rpc(self, name: str, _params: dict):
        raise AssertionError(f"no rpc expected, got {name}")


def test_a_principle_has_no_library_page_and_costs_no_lookup() -> None:
    rows = [_principle_row(1, P2_ID), _principle_row(2, P343_ID, item_id=None)]
    assert library_items_service._public_page_urls_for_reference_rows(_NoTables(), rows) == {}
    assert library_items_service._public_page_url(_NoTables(), "principle", P2_ID) is None
    assert library_items_service._public_page_url(_NoTables(), "principle", "") is None


def test_record_use_never_shelves_a_principle() -> None:
    """``library_items_content_type_valid`` does not know ``principle``; even a
    non-empty id must not reach the RPC (it would fail twice per reveal)."""
    supabase = base_supabase()
    run(library_items_service.record_use(supabase, USER_A, "principle", P2_ID))
    run(library_items_service.record_use(supabase, USER_A, "principle", ""))
    assert supabase.rpc_calls == []


def test_retrieval_artifacts_fallback_strips_the_principle_prefix() -> None:
    assert retrieval_artifacts_service._strip_ref_prefix(f"principle:{P2_ID}") == P2_ID


def test_editorial_context_trim_cuts_a_principle_rule() -> None:
    from backend.app.api.deepsearch_api import service as ds_service

    result = {"domain": "principles", "ref_id": f"principle:{P2_ID}", "text": "ن" * 500}
    assert ds_service._trim_result_bodies(result, 100) is True
    assert len(result["text"]) < 500
    assert result["text"].startswith("ن" * 100)
