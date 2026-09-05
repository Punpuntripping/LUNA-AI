"""`public_blogs.review_status` + `generation_context` — the WRITE side (157/158).

WHY THIS SUITE EXISTS
---------------------
Every invariant below is invisible at runtime. `generation_context` is written
by a background job, read by nobody, and rendered nowhere: a publish that stops
writing it, writes a half-filled object, or leaks it into a public payload
returns exactly the same 200 with exactly the same article. There is no page to
look at and no log line that would differ.

The four that would hurt, and where each is pinned:

1. **A leak.** Migration 153 gives anon a row-level SELECT policy on
   `public_blogs`, and RLS filters ROWS, not COLUMNS — the only thing keeping
   `generation_context` off the public wire is the column-level REVOKE plus the
   fact that no response model and no `select()` projection names it. This
   object carries verbatim corpus bodies (26 kept chunk/case bodies on the live
   articles); on a public read it is an unmetered corpus feed on a public table.
   §5 asserts absence STRUCTURALLY — against the response models' JSON schema
   and the service's projections — so adding the field anywhere fails here
   rather than in a scrape.

2. **A half-filled snapshot.** The forensic rows this is built from are written
   BEST-EFFORT (`agent_search.publisher._persist_forensics` swallows its own
   failures), so "missing" is a normal state, not an error. An object that
   records the article under an empty `aggregator_input` reads as "the
   aggregator had no input" — strictly worse than a null, because a null is
   legible. §2 pins the honest null, in all four ways it can arise.

3. **A publish lost to provenance.** The article is the product and this column
   is a footnote; a capture failure that propagates fails a job that is holding
   a finished article. §2 drives a raising DB read and a raising builder
   through the real publish path.

4. **An unbounded row.** Measured live 2026-09-05, this object is **263 kB and
   330 kB** raw for a 9 kB and a 12 kB article — already past the 154 kB peak
   migration 157 quotes, because an editorial run fans out wider than a chat
   turn. §4 pins that the guard trims the retrieval BODIES and keeps the
   structure, in that order: a context that lost its shape cannot be read at
   all, while one with shortened bodies still says what was retrieved, for which
   sub-query, and why it was kept.

⚠ `review_status` enforcement is NOT built and is not tested here. Nothing reads
the column; §1 pins only that a new row carries an honest value and that a
version does not re-stamp one.

No live DB / Redis / LLM. `FakeDB` comes from `test_public_blogs` (it enforces
migration 153's unique indexes and rolls refused writes back) and now also
mirrors 157's column defaults and 158's carry-forward.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any, Optional
from unittest.mock import AsyncMock

import pytest

from backend.app.api.deepsearch_api import service
from backend.app.services import public_blog_service as pbs
from shared.config import get_settings

from backend.tests.test_public_blogs import FakeDB

# ---------------------------------------------------------------------------
# Fixtures + builders
# ---------------------------------------------------------------------------

WI_ID = "wi-gen-1"
URA_ID = "ura-gen-1"

ORIGINAL_QUERY = "ما موقف قاضي التنفيذ من الدفع بالسداد النقدي بعد صدور أمر التنفيذ؟"
DESCRIBE_QUERY = "المستخدم يسأل عن الدفع بالسداد أمام قاضي التنفيذ وطرق إثباته."

# The article as the editorial aggregator writes it — headline on the FIRST line
# (plan §6's H1 contract). `insert_public_blog` strips that line out of the
# published body; the frozen draft must keep it.
ARTICLE_MD = """# الدفع بسداد قيمة السند لأمر

المدين يدفع بالسداد نقداً [1].

## الخلاصة

خلاصة."""


class _Gen:
    """Stand-in for ``HeadlessResult`` — only these four attrs are read."""

    def __init__(self, workspace_item_id: Optional[str] = WI_ID, content_text: str = ""):
        self.conversation_id = "conv-1"
        self.workspace_item_id = workspace_item_id
        self.assistant_message_id = "msg-1"
        self.content_text = content_text


def _result(idx: int, *, tier: str = "high", body_chars: int = 300) -> dict:
    """One URA result shell, shaped like the regulation domain's."""
    return {
        "ref_id": f"reg:{idx:08d}-0000-0000-0000-000000000000",
        "domain": "regulations",
        "relevance": tier,
        "source_type": "chunk",
        "reg_title": "نظام التنفيذ",
        "reg_scope": "نطاق النظام",
        "reasoning": f"سبب الإبقاء رقم {idx}",
        "chunk_content": "ن" * body_chars,
        "chunk_context": "س" * 40,
        "cross_refs": [
            {
                "target_type": "article",
                "target_reg_title": "نظام المرافعات",
                "target_number": 12,
                "content": "ح" * body_chars,
            }
        ],
        "appears_in_sub_queries": [0],
        "landing_url": "/regulations/nizam-al-tanfith",
    }


def _ura(*, n_high: int = 2, n_medium: int = 1, body_chars: int = 300) -> dict:
    return {
        "schema_version": "3.0",
        "log_id": "20260905_120000",
        "original_query": ORIGINAL_QUERY,
        "produced_at": "2026-09-05T12:00:00+00:00",
        "produced_by": {"reg_search": True, "compliance_search": False, "case_search": False},
        "sector_filter": ["قضائي"],
        "sub_queries": [
            {
                "index": 0,
                "query": "الدفع بالسداد أمام قاضي التنفيذ",
                "domain": "regulations",
                "rationale": "تفكيكي — يغطي الدفع بالسداد",
                "sufficient": True,
                "summary_note": "النتائج كافية",
                "kept_count": n_high + n_medium,
                "dropped_count": 4,
            }
        ],
        "high_results": [
            _result(i, tier="high", body_chars=body_chars) for i in range(n_high)
        ],
        "medium_results": [
            _result(100 + i, tier="medium", body_chars=body_chars)
            for i in range(n_medium)
        ],
        "dropped": [],
    }


def _reranker_rows() -> list[dict]:
    return [
        {
            "ura_id": URA_ID,
            "agent_family": "reg_search",
            "sub_query_index": 0,
            "sub_query_text": "الدفع بالسداد أمام قاضي التنفيذ",
            "sub_query_rationale": "تفكيكي — يغطي الدفع بالسداد",
            "kept_results": [{"ref_id": "kept-1", "title": "المادة الثالثة"}],
            "dropped_results": [
                {"ref_id": "dropped-1", "title": "فصل غير ذي صلة", "drop_reason": "llm"}
            ],
            "sufficient": True,
            "summary_note": "النتائج كافية",
        }
    ]


def _seed_forensics(db: FakeDB, *, ura: Optional[dict] = None, runs: Any = "default") -> None:
    if ura is not None:
        db.tables["retrieval_artifacts"] = [
            {
                "ura_id": URA_ID,
                "artifact_id": WI_ID,
                "ura_json": ura,
                "schema_version": "3.0",
                "high_count": len(ura.get("high_results") or []),
                "medium_count": len(ura.get("medium_results") or []),
                "produced_by": ura.get("produced_by"),
                "duration_ms": None,
                "deleted_at": None,
                "created_at": "2026-09-05T12:00:05+00:00",
            }
        ]
    rows = _reranker_rows() if runs == "default" else (runs or [])
    db.tables["reranker_runs"] = list(rows)


@pytest.fixture
def _settings(monkeypatch):
    get_settings.cache_clear()
    monkeypatch.setenv("EDITORIAL_BOT_USER_ID", "bot-user-1")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://rayhanai.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def publish(monkeypatch, _settings):
    """Drive the REAL ``_publish_to_public_blog`` against a FakeDB.

    Only the workspace-item load and the reference-payload fetch are stubbed
    (FakeDB models the blog tables, not ``workspace_items``). The forensic reads
    go through the actual ``_load_retrieval_artifact`` / ``_load_reranker_runs``
    against seeded fake tables, so the projections, filters and the ordering are
    exercised rather than restated.
    """

    def _run(
        db: FakeDB,
        *,
        workspace_item_id: Optional[str] = WI_ID,
        content_md: str = ARTICLE_MD,
        wi_metadata: Optional[dict] = None,
        describe_query: str = DESCRIBE_QUERY,
        references: Optional[list[dict]] = None,
        job: Optional[dict] = None,
        cfg: Optional[dict] = None,
    ):
        metadata = {
            "subtype": "legal_synthesis",
            "confidence": "high",
            "detail_level": "medium",
            "prompt_key": "prompt_editorial_case",
            "model_used": "qwen3.6-plus",
            "ref_count": 3,
            "cited_count": 2,
        }
        metadata.update(wi_metadata or {})
        monkeypatch.setattr(
            service,
            "_load_workspace_item",
            lambda _sb, _iid: {
                "item_id": _iid,
                "content_md": content_md,
                "title": "عنوان البطاقة",
                "metadata": metadata,
                "kind": "agent_search",
                "describe_query": describe_query,
            },
        )
        payload = references if references is not None else [
            {"n": 1, "ref_id": "reg:00000000-0000-0000-0000-000000000000",
             "title": "مرجع", "relevance": "high", "has_source": True,
             "library_url": None, "source_view": None},
        ]
        monkeypatch.setattr(
            service, "fetch_item_references_payload", AsyncMock(return_value=payload)
        )
        full_job = {
            "job_id": "job-gen-1",
            "question": "سؤال مجهول الهوية عن التنفيذ",
            "title": None,
            "subtype": "marketing_telegram",
            "publish_policy": "auto",
            "min_confidence": "medium",
        }
        full_job.update(job or {})
        full_cfg = {
            "type": "judicial_research",
            "subjects": [],
            "slug": None,
            "publish_public": True,
            "editorial_voice": True,
            "mode": "case_search",
            "support": None,
        }
        full_cfg.update(cfg or {})
        gen = _Gen(
            workspace_item_id=workspace_item_id,
            content_text="" if workspace_item_id else content_md,
        )
        return asyncio.run(service._publish_to_public_blog(db, full_job, gen, full_cfg))

    return _run


def _stored(db: FakeDB) -> dict:
    rows = db.tables["public_blogs"]
    assert len(rows) == 1, rows
    return rows[0]


def _bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))


# ===========================================================================
# 1. review_status — state only, stamped explicitly, never re-stamped
# ===========================================================================


def test_a_new_article_is_written_pending(publish) -> None:
    """Nobody has read it yet, and saying otherwise would be a lie about a human
    decision that did not happen.

    ⚠ This does NOT gate anything: no read predicate in `public_blog_service`
    filters on `review_status` (migration 157 is explicit that enforcement is
    unbuilt), so a `pending` row is publicly visible exactly like an `approved`
    one. The value is a record, not a gate.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    publish(db)
    assert _stored(db)["review_status"] == "pending"


def test_review_status_is_stamped_by_the_service_not_left_to_the_column_default(
) -> None:
    """The value a new article carries has to be readable at the call site.

    Left to the DB default it lives only in a migration, and the day the default
    changes every caller changes with it silently.
    """
    db = FakeDB()
    # Strip the fake's own column default, so the ONLY thing that can put a
    # value on this row is the INSERT payload. With the default in place a
    # service that never named the column would look identical.
    db._DEFAULTS = {
        **FakeDB._DEFAULTS,
        "public_blogs": {
            k: v
            for k, v in FakeDB._DEFAULTS["public_blogs"].items()
            if k != "review_status"
        },
    }
    row = pbs.insert_public_blog(
        db,
        slug="حقوق-العامل",
        blog_type="laws_explanation",
        question_text="س",
        content_md="# عنوان\n\nنص.",
        author_user_id="bot",
    )
    assert row["review_status"] == pbs.DEFAULT_REVIEW_STATUS == "pending"


def test_an_unknown_review_status_is_coerced_not_raised() -> None:
    """A 23514 here would fail the whole publish and lose a finished article over
    a metadata field nothing reads. Loud log, article kept."""
    db = FakeDB()
    row = pbs.insert_public_blog(
        db,
        slug="حقوق-العامل",
        blog_type="laws_explanation",
        question_text="س",
        content_md="# عنوان\n\nنص.",
        author_user_id="bot",
        review_status="rejected",     # not in migration 157's CHECK
    )
    assert row["review_status"] in pbs.REVIEW_STATUSES
    assert row["review_status"] == "pending"


def test_both_new_columns_are_carried_forward_by_the_rpc_never_re_stamped(
    publish,
) -> None:
    """v1 ONLY (migration 158).

    `append_public_blog_version` copies `review_status` and `generation_context`
    off the current version. A caller that re-stamped them would reset an
    approved article to `pending` on every SEO rewrite and drop the generation
    record exactly when an editor most needs it — so `append_version` must not
    even know the columns exist.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    result = publish(db)
    v1_context = _stored(db)["generation_context"]
    assert v1_context is not None

    pbs.append_version(db, result.root_id, content_md="# نسخة ثانية\n\nنص جديد.")

    assert len(db.rpc_calls) == 1
    name, params = db.rpc_calls[0]
    assert name == "append_public_blog_version"
    assert set(params) == {
        "p_root_id", "p_content_md", "p_title", "p_revision_note",
        "p_type", "p_confidence",
    }, "append_version must not pass either column — the RPC carries them"

    v2 = next(r for r in db.tables["public_blogs"] if r["version_no"] == 2)
    assert v2["generation_context"] == v1_context
    assert v2["review_status"] == "pending"


# ===========================================================================
# 2. Best-effort — an honest null, and never a failed publish
# ===========================================================================


def test_the_context_is_null_when_the_forensic_rows_never_landed(publish) -> None:
    """`_persist_forensics` swallows its own failures, so a missing
    `retrieval_artifacts` row is a NORMAL state.

    Null, not a shell: with no URA there is no aggregator input to record, and
    an object carrying only the article — which is already on the row — under an
    empty `aggregator_input` reads as "the aggregator had no input".
    """
    db = FakeDB()
    _seed_forensics(db, ura=None, runs=[])       # table exists, no rows
    result = publish(db)
    row = _stored(db)
    assert row["generation_context"] is None
    assert result.root_id                        # the publish still succeeded
    assert row["content_md"]


def test_a_raising_forensic_read_still_publishes_the_article(publish, monkeypatch) -> None:
    """The article is the product; this column is provenance."""
    def _boom(_sb, _iid):
        raise RuntimeError("postgrest exploded")

    monkeypatch.setattr(service, "_load_retrieval_artifact", _boom)
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    result = publish(db)
    assert _stored(db)["generation_context"] is None
    assert result.is_published is True


def test_a_raising_context_builder_still_publishes_the_article(publish, monkeypatch) -> None:
    """The envelope is around the WHOLE capture, not just the DB read — a
    serialization surprise inside the builder must land the same way."""
    async def _boom(*_a, **_k):
        raise ValueError("unserializable")

    monkeypatch.setattr(service, "_build_generation_context", _boom)
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    result = publish(db)
    assert _stored(db)["generation_context"] is None
    assert result.root_id


def test_the_chat_only_route_writes_null(publish) -> None:
    """No workspace item ⇒ no `artifact_id` to key the forensics on."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    publish(db, workspace_item_id=None)
    assert _stored(db)["generation_context"] is None


def test_missing_reranker_runs_do_not_void_the_context(publish) -> None:
    """The URA ALONE reconstructs the aggregator's input — that is exactly what
    `AggregatorInput.from_ura` does, rebuilding `sub_queries` from
    `ura.sub_queries` and filling each one from the two tiers.

    The reranker rows only add per-sub-query keep/drop forensics on top, so
    their absence is recorded, not fatal.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura(), runs=[])
    publish(db)
    ctx = _stored(db)["generation_context"]
    assert ctx is not None
    assert ctx["captured_from"]["reranker_runs"] == 0
    assert "aggregator_input.sub_queries[].kept_results" in ctx["unavailable"]
    assert ctx["aggregator_input"]["sub_queries"][0]["query"]


def test_what_the_pipeline_never_persists_is_named_not_omitted(publish) -> None:
    """A reader must be able to tell "not captured" from "was empty".

    `context_blocks` is rendered into the aggregator's user message and dropped
    — not on the URA, not on the workspace item, not in any forensic table.
    `gaps` reaches the CLI and the log renderer and no DB column at all. Both
    are unrecoverable at editorial-publish time, so both are declared.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    publish(db)
    ctx = _stored(db)["generation_context"]
    assert "aggregator_input.context_blocks" in ctx["unavailable"]
    assert "first_draft.gaps" in ctx["unavailable"]
    assert "context_blocks" not in ctx["aggregator_input"]
    assert "gaps" not in ctx["first_draft"]


# ===========================================================================
# 3. What is actually frozen
# ===========================================================================


def test_v1_carries_the_first_draft_and_the_aggregator_input(publish) -> None:
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=2, n_medium=1))
    publish(db)
    ctx = _stored(db)["generation_context"]

    assert ctx["schema_version"] == service.GENERATION_CONTEXT_SCHEMA
    assert ctx["captured_at"]

    draft = ctx["first_draft"]
    assert draft["confidence"] == "high"
    assert draft["prompt_key"] == "prompt_editorial_case"
    assert draft["model_used"] == "qwen3.6-plus"
    assert draft["used_refs"] == [
        {"n": 1, "ref_id": "reg:00000000-0000-0000-0000-000000000000"}
    ]

    agg = ctx["aggregator_input"]
    assert agg["original_query"] == ORIGINAL_QUERY
    assert agg["question_text"] == "سؤال مجهول الهوية عن التنفيذ"
    assert agg["describe_query"] == DESCRIBE_QUERY
    assert agg["detail_level"] == "medium"
    assert agg["produced_by"] == {
        "reg_search": True, "compliance_search": False, "case_search": False
    }
    assert agg["sector_filter"] == ["قضائي"]
    assert agg["editorial"]["mode"] == "case_search"
    assert agg["editorial"]["support"] is None       # tri-state survives (§5)
    assert len(agg["results"]) == 3
    assert [r["relevance"] for r in agg["results"]] == ["high", "high", "medium"]

    assert ctx["captured_from"] == {
        "workspace_item_id": WI_ID, "ura_id": URA_ID, "reranker_runs": 1
    }


def test_the_frozen_draft_keeps_the_H1_the_published_body_strips(publish) -> None:
    """§6's H1 contract cuts the headline out of `content_md` so the article
    hero does not double-render it. The DRAFT is what the aggregator wrote, so
    it keeps the line — otherwise this column stores the edited body under a
    name that claims otherwise."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    publish(db)
    row = _stored(db)
    assert row["content_md"].startswith("المدين يدفع")
    assert row["generation_context"]["first_draft"]["content_md"] == ARTICLE_MD
    assert row["generation_context"]["first_draft"]["content_md"].startswith("# ")


def test_the_reranker_forensics_are_merged_onto_the_sub_query_by_index(publish) -> None:
    """`reranker_runs.sub_query_index` and `ura.sub_queries[].index` share ONE
    global scheme (reg, then compliance, then case), which is what lets the two
    be joined with no key of their own."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    publish(db)
    sq = _stored(db)["generation_context"]["aggregator_input"]["sub_queries"][0]
    assert sq["index"] == 0
    assert sq["agent_family"] == "reg_search"
    assert sq["kept_results"] == [{"ref_id": "kept-1", "title": "المادة الثالثة"}]
    assert sq["dropped_results"][0]["ref_id"] == "dropped-1"
    assert sq["summary_note"] == "النتائج كافية"       # the URA's own field survives


def test_the_snapshot_survives_the_forensic_rows_it_was_built_from(publish) -> None:
    """🚨 SNAPSHOT, NEVER A POINTER.

    `retrieval_artifacts` is keyed to a `workspace_items` row, and the entire
    design of `public_blogs` says a published article must outlive its workspace
    item. Storing ids to resolve later would make the article's provenance
    expire with the workspace — so the bodies are COPIED IN.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura(body_chars=300))
    publish(db)
    ctx = _stored(db)["generation_context"]

    db.tables["retrieval_artifacts"] = []
    db.tables["reranker_runs"] = []

    body = ctx["aggregator_input"]["results"][0]["chunk_content"]
    assert body == "ن" * 300
    assert ctx["aggregator_input"]["sub_queries"][0]["kept_results"]


# ===========================================================================
# 4. The size guard
# ===========================================================================


def test_a_context_within_budget_is_stored_whole_and_flagged_untruncated(
    publish,
) -> None:
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=2, n_medium=1, body_chars=300))
    publish(db)
    ctx = _stored(db)["generation_context"]
    assert ctx["truncated"] is False
    assert ctx["truncation"]["steps"] == []
    assert ctx["truncation"]["within_budget"] is True
    assert ctx["aggregator_input"]["results"][0]["chunk_content"] == "ن" * 300


def test_an_oversized_context_is_trimmed_and_says_so(publish) -> None:
    """~40 results x 8k of body — the shape a case-heavy fan-out actually takes
    (the live articles are 263 kB and 330 kB raw)."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=20, n_medium=20, body_chars=8_000))
    publish(db)
    ctx = _stored(db)["generation_context"]

    assert ctx["truncated"] is True
    assert ctx["truncation"]["steps"]
    assert ctx["truncation"]["raw_bytes"] > service._CONTEXT_BUDGET_BYTES
    assert ctx["truncation"]["within_budget"] is True
    assert _bytes(ctx) <= service._CONTEXT_BUDGET_BYTES


def test_the_guard_trims_the_bodies_and_keeps_the_structure(publish) -> None:
    """The ORDER is the point. A context that has lost its shape cannot be read
    at all; one with shortened bodies still says what was retrieved, for which
    sub-query, and why it was kept.
    """
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=20, n_medium=20, body_chars=8_000))
    publish(db)
    ctx = _stored(db)["generation_context"]
    agg = ctx["aggregator_input"]

    # Structure intact: every result, every sub-query, every identity.
    assert len(agg["results"]) == 40
    assert len(agg["sub_queries"]) == 1
    assert all(r["ref_id"] for r in agg["results"])
    assert all(r["reg_title"] == "نظام التنفيذ" for r in agg["results"])
    assert all(r["reasoning"] for r in agg["results"])
    assert agg["original_query"] == ORIGINAL_QUERY

    # Bodies cut, and marked as cut.
    bodies = [r["chunk_content"] for r in agg["results"]]
    assert all(len(b) < 8_000 for b in bodies)
    assert any(b.endswith(service._TRUNC_MARK) for b in bodies)

    # The ladder never reached the rungs that give up structure.
    steps = ctx["truncation"]["steps"]
    assert "results_stubbed" not in steps
    assert "first_draft@4000" not in steps
    assert steps[0].startswith("medium_bodies@"), steps


def test_the_medium_tier_is_cut_before_the_high_tier(publish) -> None:
    """The reranker already said which tier it trusted; the aggregator weighted
    them the same way, so the guard gives up the cheaper one first."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=12, n_medium=12, body_chars=8_000))
    publish(db)
    ctx = _stored(db)["generation_context"]
    steps = ctx["truncation"]["steps"]
    assert steps, "this fixture must overflow the budget"
    # Every width is applied to medium before high.
    for i, step in enumerate(steps):
        if step.startswith("high_bodies@"):
            width = step.split("@")[1]
            assert f"medium_bodies@{width}" in steps[:i], steps


def test_the_first_draft_survives_the_worst_case(publish) -> None:
    """The retrieval is what gets cut. The draft is the one thing this column
    exists to keep past a rewrite, so it is the LAST rung."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura(n_high=40, n_medium=40, body_chars=8_000))
    publish(db)
    ctx = _stored(db)["generation_context"]
    assert ctx["first_draft"]["content_md"] == ARTICLE_MD
    assert ctx["first_draft"]["used_refs"]


def test_the_rejected_candidates_go_before_anything_the_aggregator_saw() -> None:
    """`dropped_results` are the one part of this object the aggregator provably
    never saw. `dropped_count` survives so the shape of the rejection stays
    legible."""
    ctx = {
        "aggregator_input": {
            "results": [],
            "sub_queries": [
                {"index": 0, "dropped_count": 3,
                 "dropped_results": [{"ref_id": "x"}], "kept_results": [{"ref_id": "k"}]}
            ],
        }
    }
    assert service._drop_reranker_dropped_results(ctx) is True
    sq = ctx["aggregator_input"]["sub_queries"][0]
    assert sq["dropped_results"] == []
    assert sq["kept_results"] == [{"ref_id": "k"}]
    assert sq["dropped_count"] == 3
    # Idempotent — a second pass reports "nothing left to give".
    assert service._drop_reranker_dropped_results(ctx) is False


def test_nested_cross_reference_bodies_are_trimmed_too() -> None:
    """`cross_refs[].content` and the case domain's
    `referenced_regulations[].reference_content` are resolved مادة bodies — up
    to ten per result. Cutting only the top-level fields would leave the biggest
    remaining chunk of corpus text untouched."""
    result = {
        "chunk_content": "أ" * 100,
        "cross_refs": [{"content": "ب" * 100}],
        "referenced_regulations": [{"reference_content": "ج" * 100, "content": "د" * 100}],
    }
    assert service._trim_result_bodies(result, 10) is True
    assert result["cross_refs"][0]["content"].startswith("ب" * 10)
    assert result["cross_refs"][0]["content"].endswith(service._TRUNC_MARK)
    assert result["referenced_regulations"][0]["reference_content"].endswith(
        service._TRUNC_MARK
    )
    assert result["referenced_regulations"][0]["content"].endswith(service._TRUNC_MARK)


# ===========================================================================
# 5. NEVER READER-FACING — asserted structurally
# ===========================================================================

_PUBLIC_MODELS = (
    "PublicBlogCard",
    "PublicBlogListResponse",
    "PublicBlogDetailResponse",
    "PublicBlogSubject",
    "PublicBlogSubjectRef",
    "PublicBlogSubjectsResponse",
    "PublicBlogSubjectFeedResponse",
)

_FORBIDDEN = ("generation_context", "review_status")


@pytest.mark.parametrize("model_name", _PUBLIC_MODELS)
def test_no_public_response_model_can_carry_either_column(model_name: str) -> None:
    """🚨 Migration 153 gives anon a row-level SELECT policy on `public_blogs`,
    and RLS filters ROWS, not COLUMNS. The column-level REVOKE is one half of
    the guard; the other half is that nothing on the way out names the field.

    Asserted against the JSON SCHEMA rather than `model_fields`, so a field
    added to a NESTED model — the shape that would slip past a flat check —
    fails here too.
    """
    from backend.app.models import responses

    model = getattr(responses, model_name)
    schema = json.dumps(model.model_json_schema(), ensure_ascii=False)
    for name in _FORBIDDEN:
        assert name not in schema, f"{model_name} exposes {name}"


def test_the_detail_response_drops_the_column_even_when_the_row_carries_it() -> None:
    """`response_model` STRIPS undeclared keys — the belt to the schema's
    braces, exercised on a row that actually holds the object."""
    from backend.app.models.responses import PublicBlogDetailResponse

    payload = PublicBlogDetailResponse(
        is_public=True,
        slug="حقوق-العامل",
        title="عنوان",
        type="laws_explanation",
        content_md="نص",
        references=[],
        question_text="س",
        created_at="2026-09-05T00:00:00+00:00",
        generation_context={"first_draft": {"content_md": "سري"}},
        review_status="pending",
    ).model_dump()
    assert "generation_context" not in payload
    assert "review_status" not in payload


def test_no_read_projection_names_the_column() -> None:
    """Every `select()` in `public_blog_service` names its columns — none of
    them may name this one. A `select("*")` anywhere on this table would both
    leak it and pull a quarter-megabyte per gallery card."""
    for fields in (pbs._CARD_FIELDS, pbs._DETAIL_FIELDS, pbs._JOB_LOOKUP_FIELDS):
        assert "generation_context" not in fields
        assert "*" not in fields


def test_the_job_result_payload_carries_neither_column(publish) -> None:
    """`BlogJobResult` is echoed to the marketing caller and stored on the job
    row, which is service-authed but not service-role — and it is the only thing
    that leaves this module holding article data."""
    db = FakeDB()
    _seed_forensics(db, ura=_ura())
    result = publish(db)
    dumped = json.dumps(result.model_dump(mode="json"), ensure_ascii=False)
    for name in _FORBIDDEN:
        assert name not in dumped
