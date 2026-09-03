"""Editorial publishing — the blog_subjects.md step-8 contract.

What this suite pins, and why each one is invisible in a type:

1. **``support`` is tri-state end to end.** ``bool = False`` anywhere on the
   path — request model, job row, service signature — collapses "the planner
   decides" into "pinned off" with no error and no log line. Every
   partially-pinned job then silently runs without its support executor and
   returns a thinner article (plan §5 + §11). Nothing about that shows up in a
   response, a log, or a status code.

2. **All four rows of the §5 pinning table.** ``mode``/``support`` set/absent
   in every combination, and the rule underneath them: *a pinned plan ALWAYS
   forces the family; it only SOMETIMES carries a decision.* Conflating the two
   is the easy mistake, and conflating them silently sends editorial jobs
   through the router (which can answer directly, produce no artifact, and
   force the confidence to `low`).

3. **A phase-1 pause has no user to answer it.** ``ask_user`` is registered
   unconditionally on the decider and the prompt urges it in five places. In a
   headless run the pause would strand the job until ``catchup_stuck_jobs``
   reaps it — a job that looks alive for an hour and then fails.

4. **``editorial=True`` actually reaches ``build_retrieval_config``.** Step 7
   built three ``prompt_editorial_*`` keys that were INERT: ``runner.py`` called
   ``build_retrieval_config(decision)`` with no flag, so the editorial prompts
   were unreachable and every "editorial" article would have been written in the
   in-app answering voice. A silent wrong-prompt is indistinguishable from
   success in every response.

5. **v1 sets ``blog_id == root_id``.** The app mints the uuid because
   ``root_id`` self-references ``blog_id``; letting the column default it leaves
   no way to name ``root_id``, and a wrong root_id breaks every later
   marketing call (seo / cards / retract all address the LOGICAL blog).

6. **An unknown subject slug is a 400, never a silent drop.** A blog that
   publishes with no subject is invisible in the browse tree and nobody notices
   until the traffic does not arrive.

7. **Retract flips ONLY the current version**, and only ``is_public``.

No live DB / Redis / LLM. ``FakeDB`` is reused from ``test_public_blogs`` — it
enforces migration 153's three unique indexes, so a botched write raises here
the way it would in Postgres. The planner tests drive the REAL runner over a
``FunctionModel``, so the pause conversion is exercised through the actual
control flow rather than a restatement of it.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional
from unittest.mock import AsyncMock, MagicMock

import pytest

from backend.app.api.deepsearch_api import service
from backend.app.api.deepsearch_api.models import BlogPostJobRequest
from backend.app.api.deepsearch_api.router import _validate_against_db, _validate_request
from backend.app.errors import LunaHTTPException
from backend.app.services import public_blog_service as pbs
from shared.config import get_settings
from shared.seo.judgment_naming import slugify_ar

from backend.tests.test_public_blogs import ARABIC_SLUG, FakeDB


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _req(**over: Any) -> BlogPostJobRequest:
    """A minimally-valid submit body; override any field."""
    base: dict[str, Any] = {
        "idempotency_key": "tg:asklawy:48211",
        "question": "هل يجوز إصلاح المركبة قبل صدور حكم لجنة المنازعات التأمينية؟",
        "type": "judicial_research",
    }
    base.update(over)
    return BlogPostJobRequest(**base)


class _Gen:
    """Stand-in for ``HeadlessResult`` — only these four attrs are read."""

    def __init__(
        self,
        workspace_item_id: Optional[str] = "wi-1",
        content_text: str = "",
    ) -> None:
        self.conversation_id = "conv-1"
        self.workspace_item_id = workspace_item_id
        self.assistant_message_id = "msg-1"
        self.content_text = content_text


class _Ref:
    """Minimal Reference-like object (``.n`` / ``.title`` / ``.relevance``)."""

    def __init__(self, n: int, relevance: str = "high") -> None:
        self.n = n
        self.title = f"مرجع {n}"
        self.relevance = relevance

    def model_dump(self, mode: str = "python") -> dict:
        return {"n": self.n, "title": self.title, "relevance": self.relevance}


# The article as the editorial aggregator writes it: headline on the FIRST line
# (plan §6's H1 contract), ordinal `##` sections, closing on الخلاصة.
ARTICLE_MD = """# إصلاح المركبة قبل صدور حكم لجنة المنازعات التأمينية

يواجه كثير من المؤمَّن لهم معضلة عملية بعد وقوع حادث مروري [1].

## أولاً: النظام لا يمنع الإصلاح المسبق

نص القسم الأول [2].

## الخلاصة

خلاصة المقال."""


@pytest.fixture
def _settings(monkeypatch):
    """Editorial bot + public URL, with the settings cache cleared either side."""
    get_settings.cache_clear()
    monkeypatch.setenv("EDITORIAL_BOT_USER_ID", "bot-user-1")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://rayhanai.com")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def publish(monkeypatch, _settings):
    """Drive ``_publish_to_public_blog`` against a FakeDB.

    The workspace-item load + reference fetch are stubbed (FakeDB models the
    three public-blog tables, not ``workspace_items``), so what is under test is
    the publish path itself: title/slug resolution, both visibility flags, the
    v1 write, and the subject filing.
    """

    def _run(
        db: FakeDB,
        *,
        job: Optional[dict] = None,
        cfg: Optional[dict] = None,
        content_md: str = ARTICLE_MD,
        wi_confidence: str = "high",
        wi_title: Optional[str] = "عنوان البطاقة",
        workspace_item_id: Optional[str] = "wi-1",
        references: Optional[list] = None,
        resolvable: Optional[set] = None,
        library_urls: Optional[dict] = None,
    ):
        monkeypatch.setattr(
            service,
            "_load_workspace_item",
            lambda _sb, _iid: {
                "item_id": _iid,
                "content_md": content_md,
                "title": wi_title,
                "metadata": {"confidence": wi_confidence},
                "kind": "agent_search",
            },
        )
        # The publish path snapshots ``fetch_item_references_payload`` — plain
        # dicts already carrying ``has_source`` and ``library_url`` — which is
        # the SAME builder the legacy ``blog_posts`` share snapshot uses. Tests
        # still author references as ``_Ref`` objects; the payload shape is
        # derived here so a call site only has to say what it cares about.
        #
        # ``resolvable`` defaults to every cited n (the normal case: a citation
        # the panel shows is a citation whose source rebuilt); ``library_urls``
        # defaults to none resolving (the honest default — most cited items have
        # no published library page).
        refs = references if references is not None else [_Ref(1), _Ref(2)]
        resolvable_ns = {r.n for r in refs} if resolvable is None else set(resolvable)
        payload = [
            {
                **r.model_dump(mode="json"),
                "source_view": None,
                "has_source": r.n in resolvable_ns,
                "library_url": (library_urls or {}).get(r.n),
            }
            for r in refs
        ]
        monkeypatch.setattr(
            service,
            "fetch_item_references_payload",
            AsyncMock(return_value=payload),
        )
        full_job = {
            "job_id": "job-1",
            "question": "سؤال مجهول الهوية عن التأمين",
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
            "mode": None,
            "support": None,
        }
        full_cfg.update(cfg or {})
        # On a chat-only route (no workspace item) the body comes from the
        # streamed chat text instead — the WI loader is never called.
        gen = _Gen(
            workspace_item_id=workspace_item_id,
            content_text="" if workspace_item_id else content_md,
        )
        return asyncio.run(
            service._publish_to_public_blog(db, full_job, gen, full_cfg)
        )

    return _run


# ===========================================================================
# 1. The request model — `support` is TRI-STATE
# ===========================================================================


def test_absent_support_survives_the_request_model_as_none() -> None:
    """⚠ THE trap of this whole step (plan §11).

    ``support: bool = False`` would make this assertion read ``is False`` and
    nothing else in the system would notice: the job would run pinned-off, the
    response would look identical, and the only symptom would be a thinner
    article. ``None`` and ``False`` are DIFFERENT REQUESTS.
    """
    assert _req().support is None
    assert _req().support is not False


def test_explicit_support_false_is_preserved_as_false() -> None:
    """The other half of the same contract — an explicit false is a real pin."""
    assert _req(support=False).support is False


def test_explicit_support_true_is_preserved() -> None:
    assert _req(support=True).support is True


def test_absent_mode_survives_as_none_not_a_default() -> None:
    """``null`` means "the planner decides" and must never be coerced."""
    assert _req().mode is None


def test_the_request_model_declares_the_step_8_fields() -> None:
    fields = BlogPostJobRequest.model_fields
    for name in (
        "title", "type", "subjects", "slug",
        "publish_public", "editorial_voice", "mode", "support",
    ):
        assert name in fields, name
    # Field name is load-bearing (marketing reads it) AND the annotation is:
    # Optional[bool] is what makes "not pinned" expressible.
    assert fields["support"].default is None
    assert fields["mode"].default is None
    assert fields["editorial_voice"].default is True
    # ⚠ D17 — a public blog is OPEN by default, matching public_blogs.is_public's
    # own column default. Writing a row into a table called public_blogs IS
    # creating a public blog. A `False` default would make an omitted flag
    # produce an article absent from the gallery AND the sitemap, which is the
    # failure nobody notices until the traffic does not arrive — and it is the
    # exact condition §12.5 says step 8 exists to clear.
    assert fields["publish_public"].default is True


def test_editorial_config_round_trip_preserves_a_null_support() -> None:
    """The tri-state must survive the JSON round trip through the job row.

    ``read_editorial_config`` is where a ``.get("support", False)`` would
    silently eat it — the metadata blob has no schema to catch that.
    """
    cfg = service.editorial_config(_req(mode="case_led"))
    job = {"metadata": {service.EDITORIAL_META_KEY: cfg}}
    read = service.read_editorial_config(job)
    assert read["support"] is None
    assert read["mode"] == "case_led"


def test_editorial_config_round_trip_preserves_an_explicit_false() -> None:
    cfg = service.editorial_config(_req(mode="case_led", support=False))
    read = service.read_editorial_config({"metadata": {service.EDITORIAL_META_KEY: cfg}})
    assert read["support"] is False


def test_a_job_row_with_no_editorial_block_reads_back_as_unpinned() -> None:
    """A legacy / mangled row must degrade to unpinned + UNLISTED.

    ⚠ The asymmetry with the REQUEST default (which is now ``True``) is
    deliberate: a row with no ``_editorial`` block never expressed an intent to
    honour, so the conservative read is the one that cannot publish something
    nobody asked to publish. A real job never reaches this branch — ``_insert_job``
    always writes the block with the value the caller actually got.
    """
    read = service.read_editorial_config({"metadata": {}})
    assert read["mode"] is None
    assert read["support"] is None
    assert read["publish_public"] is False
    assert read["subjects"] == []


def test_an_omitted_publish_public_reaches_the_job_row_as_true() -> None:
    """The default has to survive `editorial_config`, not just the model.

    This is the whole path an omitted flag actually travels: request model →
    job-row blob → read-back → the `is_public` the publisher writes. A default
    that were correct on the model and lost anywhere along here would produce
    the same invisible article.
    """
    cfg = service.editorial_config(_req())
    assert cfg["publish_public"] is True
    assert service.read_editorial_config(
        {"metadata": {service.EDITORIAL_META_KEY: cfg}}
    )["publish_public"] is True


def test_an_explicit_publish_public_false_survives_the_round_trip() -> None:
    """Opting OUT must still work — retract is not the only way to stay unlisted."""
    cfg = service.editorial_config(_req(publish_public=False))
    assert cfg["publish_public"] is False
    assert service.read_editorial_config(
        {"metadata": {service.EDITORIAL_META_KEY: cfg}}
    )["publish_public"] is False


# ===========================================================================
# 2. Validation — Arabic 400s
# ===========================================================================


def _detail(exc_info) -> str:
    return exc_info.value.detail


def test_missing_type_is_a_400_in_arabic() -> None:
    with pytest.raises(LunaHTTPException) as e:
        _validate_request(BlogPostJobRequest(idempotency_key="k", question="س"))
    assert e.value.status_code == 400
    assert "نوع المدونة" in _detail(e)


def test_unknown_type_is_a_400() -> None:
    with pytest.raises(LunaHTTPException) as e:
        _validate_request(_req(type="opinion"))
    assert e.value.status_code == 400
    assert "غير معروف" in _detail(e)


@pytest.mark.parametrize("mode", ["case_led", "reg_compliance_led", "full"])
def test_the_three_modes_pass_validation(mode: str) -> None:
    _validate_request(_req(mode=mode))


def test_a_null_mode_passes_validation() -> None:
    """⚠ ``null`` is VALID — it means "the planner decides"."""
    _validate_request(_req(mode=None))


def test_an_unknown_mode_is_a_400() -> None:
    with pytest.raises(LunaHTTPException) as e:
        _validate_request(_req(mode="compliance_led"))   # the retired 4th mode
    assert e.value.status_code == 400
    assert "mode" in _detail(e)


def test_the_valid_type_vocabulary_is_the_services_own() -> None:
    """One definition of the vocabulary, not two that can drift apart."""
    from backend.app.api.deepsearch_api import router as router_mod

    assert router_mod._BLOG_TYPES is pbs.BLOG_TYPES


# -- DB-backed validation ---------------------------------------------------


def _db_with_subjects() -> FakeDB:
    db = FakeDB()
    db.seed_subjects()
    return db


def test_unknown_subject_slug_is_a_400_not_a_silent_drop() -> None:
    """Plan §5. A silent drop publishes a blog nobody can browse to."""
    db = _db_with_subjects()
    with pytest.raises(LunaHTTPException) as e:
        asyncio.run(_validate_against_db(db, _req(subjects=["work-law", "labor-law"])))
    assert e.value.status_code == 400
    # The offending slug is NAMED — a caller must be able to fix the typo.
    assert "labor-law" in _detail(e)
    assert "work-law" not in _detail(e)


def test_known_subject_slugs_pass() -> None:
    db = _db_with_subjects()
    asyncio.run(_validate_against_db(db, _req(subjects=["work-law", "saudization"])))


def test_a_slug_colliding_with_a_subject_is_a_400() -> None:
    db = _db_with_subjects()
    with pytest.raises(LunaHTTPException) as e:
        asyncio.run(_validate_against_db(db, _req(slug="work-law")))
    assert e.value.status_code == 400


def test_the_reserved_subjects_slug_is_a_400() -> None:
    """``/blog/subjects`` is the subject index — no blog may claim it."""
    db = _db_with_subjects()
    with pytest.raises(LunaHTTPException) as e:
        asyncio.run(_validate_against_db(db, _req(slug="subjects")))
    assert e.value.status_code == 400


def test_an_arabic_slug_passes_the_submit_check() -> None:
    db = _db_with_subjects()
    asyncio.run(_validate_against_db(db, _req(slug=ARABIC_SLUG)))


def test_no_slug_no_title_and_no_subjects_costs_no_db_round_trip() -> None:
    """Nothing to check: with no title the headline — and so the slug — comes
    from the aggregator and is genuinely unknowable at submit time."""
    db = _db_with_subjects()
    before = len(db.calls)
    asyncio.run(_validate_against_db(db, _req()))
    assert len(db.calls) == before


# -- the slug we WOULD mint, checked before the run is paid for -------------


def test_an_english_title_is_rejected_at_SUBMIT_not_after_the_run() -> None:
    """⚠ The operational footgun this closes.

    ``slugify_ar`` over a Latin title yields ASCII kebab-case — the shape
    migration 153 reserves to SUBJECTS — so ``insert_public_blog`` refuses it.
    Correctly, but at the very END: the job has already spent 1–4 minutes and a
    full retrieval budget on a title the operator could have fixed in a second.
    """
    db = _db_with_subjects()
    with pytest.raises(LunaHTTPException) as e:
        asyncio.run(
            _validate_against_db(db, _req(title="Labor Law Explained", slug=None))
        )
    assert e.value.status_code == 400
    # The message must name the TITLE. Every 400 out of assert_slug_available is
    # written for a caller who SENT a slug; this caller sent a title, and those
    # messages would send them hunting for a field they never filled in.
    assert "عنوان" in _detail(e)
    # Nothing was queued — the point is that no pipeline run is paid for.
    assert db.tables["public_blogs"] == []


def test_an_arabic_title_mints_a_valid_slug_and_passes() -> None:
    db = _db_with_subjects()
    asyncio.run(
        _validate_against_db(db, _req(title="حقوق العامل عند إنهاء العقد", slug=None))
    )


def test_the_submit_check_predicts_the_publishers_slug_EXACTLY(publish) -> None:
    """Not an approximation — the two must mint the same string.

    The publisher's title precedence is request title → body H1 → WI title, so a
    supplied title wins there too. If these ever diverged, submit would bless a
    slug publish then refuses, which is worse than not checking at all.
    """
    title = "حقوق العامل عند إنهاء العقد"
    db = _db_with_subjects()
    asyncio.run(_validate_against_db(db, _req(title=title, slug=None)))

    fresh = FakeDB()
    result = publish(fresh, job={"title": title})
    assert result.slug == slugify_ar(title)


def test_a_title_whose_minted_slug_is_already_live_is_a_409_at_submit() -> None:
    """A duplicate is about the SLUG, so that message passes through untouched
    rather than being rewritten as a title problem."""
    db = _db_with_subjects()
    db.seed_blog(blog_id="b1", root_id="b1", slug=ARABIC_SLUG)
    with pytest.raises(LunaHTTPException) as e:
        asyncio.run(
            _validate_against_db(
                db, _req(title="حقوق العامل عند إنهاء العقد", slug=None)
            )
        )
    assert e.value.status_code == 409


def test_an_explicit_slug_short_circuits_the_title_check() -> None:
    """A supplied slug is what publish uses, so the title is irrelevant — an
    English title alongside an Arabic slug must NOT be refused."""
    db = _db_with_subjects()
    asyncio.run(
        _validate_against_db(db, _req(title="Labor Law Explained", slug=ARABIC_SLUG))
    )


# ===========================================================================
# 3. The §5 pinning table
# ===========================================================================


def _pin(**over: Any):
    from agents.deep_search_v4.planner.models import PinnedPlan

    return PinnedPlan(**over)


def test_row1_both_set_is_fully_pinned_and_carries_a_decision() -> None:
    pin = _pin(mode="case_led", support=True)
    assert pin.is_fully_pinned is True
    d = pin.decision()
    assert d is not None
    assert (d.mode, d.support) == ("case_led", True)
    assert d.rationale.startswith("editorial_pin:")
    # §5: query_restatement is empty on the fully-pinned path — the raw query
    # flows downstream verbatim, which is correct for a curated question.
    assert d.query_restatement == ""


def test_row2_mode_only_carries_no_decision_and_overlays_the_mode() -> None:
    from agents.deep_search_v4.planner.models import PlannerDecision

    pin = _pin(mode="case_led", support=None)
    assert pin.is_fully_pinned is False
    assert pin.decision() is None, "phase 1 MUST run when either half is absent"

    phase1 = PlannerDecision(mode="full", support=True, rationale="قرر المخطط")
    out = pin.overlay(phase1)
    assert out.mode == "case_led"          # pinned wins
    assert out.support is True             # phase 1's choice survives
    assert "editorial_pin overlay" in out.rationale


def test_row3_support_only_carries_no_decision_and_overlays_support() -> None:
    from agents.deep_search_v4.planner.models import PlannerDecision

    pin = _pin(mode=None, support=False)
    assert pin.is_fully_pinned is False
    assert pin.decision() is None

    phase1 = PlannerDecision(mode="case_led", support=True, rationale="قرر المخطط")
    out = pin.overlay(phase1)
    assert out.mode == "case_led"          # phase 1's choice survives
    assert out.support is False            # an explicit false IS a pin


def test_row4_unpinned_carries_no_decision_and_overlays_nothing() -> None:
    from agents.deep_search_v4.planner.models import PlannerDecision

    pin = _pin()
    assert pin.is_fully_pinned is False
    assert pin.decision() is None
    phase1 = PlannerDecision(mode="full", support=True, rationale="قرر المخطط")
    assert pin.overlay(phase1) is phase1   # untouched, same object


def test_a_pin_always_forces_the_family_even_when_unpinned() -> None:
    """⚠ The distinction the plan calls the easy mistake.

    An editorial job always wants deep_search — that is not a judgement call —
    so the family is forced whether or not a decision rides along.
    """
    assert _pin().agent_family == "deep_search"
    assert _pin(mode="full", support=False).agent_family == "deep_search"


def test_a_pin_is_headless_by_default() -> None:
    assert _pin().headless is True


# ===========================================================================
# 4. Runner wiring — editorial prompt, skipped phase 1, pause conversion
# ===========================================================================

@pytest.fixture
def planner_deps():
    from agents.deep_search_v4.planner.deps import PlannerDeps

    return PlannerDeps(
        supabase=MagicMock(),
        embedding_fn=AsyncMock(return_value=[0.0] * 4096),
    )


class _RetrievalSpy:
    """Injectable ``run_retrieval`` recording the RetrievalConfig it was handed."""

    def __init__(self) -> None:
        self.calls: list[tuple] = []

    @property
    def call_count(self) -> int:
        return len(self.calls)

    async def __call__(self, query: str, config: Any, deps: Any) -> Any:
        self.calls.append((query, config, deps))
        return _FakeAgg()


class _FakeAgg:
    confidence = "high"
    gaps: list[str] = []
    synthesis_md = "## التركيب\nنص."
    references: list = []


def _model_that(decider_move: Any, response: Any, seen: Optional[list] = None):
    """A FunctionModel that behaves differently for the decider and responder.

    Both planner agents resolve their model through the SAME
    ``agent.get_agent_model`` symbol, so one patch installs one model for both.
    They are told apart by the decider's ``ask_user`` function tool — which is
    exactly the tool this section is about.

    ``seen`` (when given) records ``"decider"`` / ``"responder"`` per call. That
    is what makes "phase 1 was skipped" a real assertion: a completed run alone
    does not prove it, because a headless pin would have CONVERTED a pause and
    completed anyway.
    """
    from pydantic_ai.messages import ModelResponse, ToolCallPart
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    log = seen if seen is not None else []

    def fn(messages, info: AgentInfo) -> ModelResponse:
        tools = {t.name for t in (info.function_tools or [])}
        if "ask_user" in tools:            # → the DECIDER
            log.append("decider")
            if decider_move == "ask_user":
                return ModelResponse(
                    parts=[ToolCallPart(
                        tool_name="ask_user",
                        args={"question": "ما نوع العقد؟"},
                    )]
                )
            return ModelResponse(
                parts=[ToolCallPart(
                    tool_name=info.output_tools[0].name,
                    args=decider_move.model_dump(),   # type: ignore[union-attr]
                )]
            )
        log.append("responder")              # → the RESPONDER
        return ModelResponse(
            parts=[ToolCallPart(
                tool_name=info.output_tools[0].name, args=response.model_dump()
            )]
        )

    return FunctionModel(fn)


@pytest.fixture
def patch_planner_model(monkeypatch):
    def _install(model: Any) -> None:
        monkeypatch.setattr(
            "agents.deep_search_v4.planner.agent.get_agent_model",
            lambda *_a, **_kw: model,
        )

    return _install


@pytest.fixture
def a_response():
    from agents.deep_search_v4.planner.models import PlannerResponse

    return PlannerResponse(chat_summary_md="ملخّص.", suggestion_md="")


@pytest.mark.asyncio
async def test_a_fully_pinned_job_skips_phase_1_entirely(
    patch_planner_model, planner_deps, a_response
) -> None:
    """NO DECIDER CALL IN THE TRACE — the plan IS the request (plan §10 gate).

    ⚠ ``kind == "completed"`` is not enough on its own: a headless pin converts
    a pause and completes too. The call log is what makes this an assertion.
    """
    from agents.deep_search_v4.planner.models import PinnedPlan
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    seen: list[str] = []
    patch_planner_model(_model_that("ask_user", a_response, seen))
    pin = PinnedPlan(mode="case_led", support=True, editorial=True)
    spy = _RetrievalSpy()

    result = await handle_planner_turn(
        "سؤال محرّر", planner_deps,
        decision=pin.decision(), run_retrieval=spy, pinned=pin,
    )

    assert seen == ["responder"], "the decider must never be invoked"
    assert result.kind == "completed"
    assert spy.call_count == 1
    assert result.decision is not None
    assert (result.decision.mode, result.decision.support) == ("case_led", True)
    assert result.decision.rationale.startswith("editorial_pin:")
    # No phase-1 lifecycle pause was ever emitted.
    assert not any(e.get("event") == "planner_paused" for e in planner_deps._events)


@pytest.mark.asyncio
async def test_an_unpinned_job_runs_phase_1(
    patch_planner_model, planner_deps, a_response
) -> None:
    """The other plan path: phase 1 runs and its decision is used as-is."""
    from agents.deep_search_v4.planner.models import PinnedPlan, PlannerDecision
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    decided = PlannerDecision(mode="full", support=True, rationale="قرر المخطط")
    seen: list[str] = []
    patch_planner_model(_model_that(decided, a_response, seen))
    spy = _RetrievalSpy()

    result = await handle_planner_turn(
        "سؤال محرّر", planner_deps,
        decision=None, run_retrieval=spy, pinned=PinnedPlan(editorial=True),
    )

    assert seen == ["decider", "responder"], "phase 1 must run when nothing is pinned"
    assert result.kind == "completed"
    assert result.decision is not None
    assert (result.decision.mode, result.decision.support) == ("full", True)
    assert "editorial_pin" not in result.decision.rationale


@pytest.mark.asyncio
async def test_a_partial_pin_runs_phase_1_then_overlays(
    patch_planner_model, planner_deps, a_response
) -> None:
    from agents.deep_search_v4.planner.models import PinnedPlan, PlannerDecision
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    decided = PlannerDecision(mode="full", support=True, rationale="قرر المخطط")
    patch_planner_model(_model_that(decided, a_response))
    spy = _RetrievalSpy()

    result = await handle_planner_turn(
        "سؤال محرّر", planner_deps,
        decision=None, run_retrieval=spy,
        pinned=PinnedPlan(mode="case_led", support=None),
    )

    assert result.decision is not None
    assert result.decision.mode == "case_led"     # overlaid
    assert result.decision.support is True        # phase 1's, kept
    # The config actually handed to retrieval reflects the OVERLAID decision.
    _q, config, _deps = spy.calls[0]
    assert config.mode == "case_led"


@pytest.mark.asyncio
async def test_a_headless_phase_1_pause_becomes_the_default_decision(
    patch_planner_model, planner_deps, a_response
) -> None:
    """⚠ There is nobody to answer an ``ask_user`` on this path (plan §5).

    Without this the run returns ``kind="paused"`` and the job sits there until
    the boot sweep reaps it — alive-looking for an hour, then failed.
    """
    from agents.deep_search_v4.planner.models import PinnedPlan
    from agents.deep_search_v4.planner.runner import (
        EDITORIAL_PAUSE_REASON,
        handle_planner_turn,
    )

    patch_planner_model(_model_that("ask_user", a_response))
    spy = _RetrievalSpy()

    result = await handle_planner_turn(
        "سؤال غامض", planner_deps,
        decision=None, run_retrieval=spy, pinned=PinnedPlan(headless=True),
    )

    assert result.kind == "completed", "a headless run must never come back paused"
    assert result.decision is not None
    # ``_default_decision``'s exact shape: reg_compliance_led + support off.
    assert (result.decision.mode, result.decision.support) == ("reg_compliance_led", False)
    assert EDITORIAL_PAUSE_REASON in result.decision.rationale
    # and the run CONTINUED — retrieval actually happened.
    assert spy.call_count == 1


@pytest.mark.asyncio
async def test_the_headless_pause_conversion_is_logged_as_an_event(
    patch_planner_model, planner_deps, a_response
) -> None:
    """A degraded plan must be visible; a silent fallback is the bad version."""
    from agents.deep_search_v4.planner.models import PinnedPlan
    from agents.deep_search_v4.planner.runner import (
        EDITORIAL_PAUSE_REASON,
        handle_planner_turn,
    )

    patch_planner_model(_model_that("ask_user", a_response))
    await handle_planner_turn(
        "سؤال غامض", planner_deps,
        decision=None, run_retrieval=_RetrievalSpy(), pinned=PinnedPlan(),
    )
    errors = [e for e in planner_deps._events if e.get("event") == "planner_error"]
    assert any(e.get("error") == EDITORIAL_PAUSE_REASON for e in errors)
    # The pause itself is still recorded — we converted it, we did not hide it.
    assert any(e.get("event") == "planner_paused" for e in planner_deps._events)


@pytest.mark.asyncio
async def test_an_IN_APP_pause_still_pauses(
    patch_planner_model, planner_deps, a_response
) -> None:
    """The conversion must be scoped to the headless path and nothing else.

    An in-app lawyer CAN answer a clarifying question, and silently answering it
    for them with the default plan would be a real product regression.
    """
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    patch_planner_model(_model_that("ask_user", a_response))
    spy = _RetrievalSpy()
    result = await handle_planner_turn(
        "سؤال غامض", planner_deps, decision=None, run_retrieval=spy, pinned=None,
    )
    assert result.kind == "paused"
    assert spy.call_count == 0


@pytest.mark.asyncio
async def test_editorial_voice_reaches_the_aggregator_prompt_key(
    patch_planner_model, planner_deps, a_response
) -> None:
    """⚠ Step 7's three ``prompt_editorial_*`` keys were INERT until this wire.

    ``runner.py`` built its RetrievalConfig with ``build_retrieval_config(decision)``
    and no flag, so an "editorial" job would have been written in the in-app
    answering voice with nothing in any response to say so.
    """
    from agents.deep_search_v4.planner.apply import EDITORIAL_PROMPT_KEYS
    from agents.deep_search_v4.planner.models import PinnedPlan
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    pin = PinnedPlan(mode="case_led", support=True, editorial=True)
    patch_planner_model(_model_that("ask_user", a_response))
    spy = _RetrievalSpy()

    await handle_planner_turn(
        "سؤال محرّر", planner_deps,
        decision=pin.decision(), run_retrieval=spy, pinned=pin,
    )

    _q, config, _deps = spy.calls[0]
    assert config.editorial is True
    assert config.aggregator_prompt_key == EDITORIAL_PROMPT_KEYS["prompt_mode_case"]
    assert config.aggregator_prompt_key == "prompt_editorial_case"


@pytest.mark.asyncio
async def test_editorial_voice_off_keeps_the_in_app_prompt(
    patch_planner_model, planner_deps, a_response
) -> None:
    from agents.deep_search_v4.planner.models import PinnedPlan
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    pin = PinnedPlan(mode="case_led", support=True, editorial=False)
    patch_planner_model(_model_that("ask_user", a_response))
    spy = _RetrievalSpy()
    await handle_planner_turn(
        "سؤال", planner_deps, decision=pin.decision(), run_retrieval=spy, pinned=pin,
    )
    _q, config, _deps = spy.calls[0]
    assert config.editorial is False
    assert config.aggregator_prompt_key == "prompt_mode_case"


@pytest.mark.asyncio
async def test_an_in_app_turn_is_byte_identical_to_before(
    patch_planner_model, planner_deps, a_response
) -> None:
    """``pinned=None`` must leave the in-app path exactly as it was."""
    from agents.deep_search_v4.planner.models import PlannerDecision
    from agents.deep_search_v4.planner.runner import handle_planner_turn

    decided = PlannerDecision(mode="reg_compliance_led", support=True, rationale="ر")
    patch_planner_model(_model_that(decided, a_response))
    spy = _RetrievalSpy()
    await handle_planner_turn("سؤال", planner_deps, run_retrieval=spy, pinned=None)
    _q, config, _deps = spy.calls[0]
    assert config.editorial is False
    assert config.aggregator_prompt_key == "prompt_mode_reg_compliance"


# ===========================================================================
# 4b. Orchestrator wiring — the family is FORCED, unconditionally
# ===========================================================================


def _drain(agen) -> list:
    async def _go():
        return [ev async for ev in agen]

    return asyncio.run(_go())


@pytest.mark.parametrize(
    "pin_kwargs",
    [
        {"mode": "case_led", "support": True},    # fully pinned
        {"mode": "case_led", "support": None},    # partially pinned
        {"mode": None, "support": True},          # partially pinned
        {},                                       # UNPINNED
    ],
    ids=["both", "mode-only", "support-only", "neither"],
)
def test_a_pin_bypasses_the_router_in_every_row_of_the_table(
    monkeypatch, pin_kwargs
) -> None:
    """⚠ *Always* forces the family; only *sometimes* carries a decision.

    The unpinned row is the one that matters: it is the row where "no decision"
    is easy to mistake for "no pin", which would send an editorial job through
    the router. The router can answer directly with a ChatResponse — no
    workspace item, confidence forced to `low`, nothing ever published — and
    nothing in the job result would say why.
    """
    from agents import orchestrator as orch
    from agents.deep_search_v4.planner.models import PinnedPlan

    captured: dict = {}

    async def _fake_dispatch(**kwargs):
        captured.update(kwargs)
        yield {"type": "done"}

    async def _boom(**_kwargs):
        raise AssertionError("the router must not run on a pinned dispatch")
        yield  # pragma: no cover - unreachable, keeps this an async generator

    monkeypatch.setattr(orch, "_dispatch", _fake_dispatch)
    monkeypatch.setattr(orch, "_route", _boom)

    pin = PinnedPlan(task_label="", **pin_kwargs)
    events = _drain(
        orch._handle_message_inner(
            question="هل يجوز إصلاح المركبة قبل الحكم؟",
            user_id="bot-user-1",
            conversation_id="conv-1",
            supabase=MagicMock(),
            codec=None,
            pinned_plan=pin,
        )
    )

    assert events == [{"type": "done"}]
    assert captured["agent_family"] == "deep_search"
    assert captured["pinned_plan"] is pin
    # The raw question IS the query — marketing's questions are curated and
    # self-contained, and the router (which never paraphrases either) is gone.
    assert captured["describe_query"] == "هل يجوز إصلاح المركبة قبل الحكم؟"
    # Nothing to attach, no case, no greeting: this is a throwaway conversation.
    assert captured["attached_item_ids"] == []
    assert captured["target_item_id"] is None
    assert captured["welcome"] is None
    # A router-less dispatch still needs a workspace-item title.
    assert captured["task_label"]


def test_an_unpinned_turn_still_goes_through_the_router(monkeypatch) -> None:
    """The bypass must be scoped to the pin and nothing else."""
    from agents import orchestrator as orch

    async def _fake_route(**_kwargs):
        yield {"type": "routed"}

    async def _boom(**_kwargs):
        raise AssertionError("_dispatch must be reached via the router")
        yield  # pragma: no cover

    monkeypatch.setattr(orch, "_route", _fake_route)
    monkeypatch.setattr(orch, "_dispatch", _boom)
    # The pre-router steps (pause lookup, memory, OCR) are all best-effort and
    # swallow their own failures against a MagicMock supabase.
    monkeypatch.setattr(orch, "_find_awaiting_user", lambda *_a, **_kw: None)

    events = _drain(
        orch._handle_message_inner(
            question="سؤال عادي",
            user_id="u1",
            conversation_id="c1",
            supabase=MagicMock(),
            codec=None,
            pinned_plan=None,
        )
    )
    assert {"type": "routed"} in events


def test_run_deep_search_derives_a_decision_only_from_a_FULL_pin() -> None:
    """The seam ``_run_deep_search`` uses: ``pinned_plan.decision()``.

    Fully pinned ⇒ a PlannerDecision is handed to the runner as ``decision``
    (phase 1 skipped). Anything less ⇒ ``None``, so phase 1 runs and the pin is
    overlaid instead.
    """
    import inspect

    from agents import orchestrator as orch
    from agents.deep_search_v4.planner.models import PinnedPlan

    src = inspect.getsource(orch._run_deep_search)
    assert "pinned_plan.decision()" in src
    assert "if decision is None and pinned_plan is not None" in src

    assert PinnedPlan(mode="full", support=False).decision() is not None
    assert PinnedPlan(mode="full").decision() is None
    assert PinnedPlan(support=False).decision() is None
    assert PinnedPlan().decision() is None


def test_the_whole_pin_signature_chain_accepts_pinned_plan() -> None:
    """One missing keyword anywhere on the chain silently un-pins every job."""
    import inspect

    from agents import orchestrator as orch
    from backend.app.api.deepsearch_api.generate import generate_answer_headless

    for fn in (
        orch.handle_message,
        orch._handle_message_inner,
        orch._dispatch,
        orch._run_deep_search,
    ):
        assert "pinned_plan" in inspect.signature(fn).parameters, fn.__name__

    params = inspect.signature(generate_answer_headless).parameters
    for name in ("mode", "support", "editorial_voice"):
        assert name in params, name
    # ⚠ The default must be None, not False — see the tri-state note above.
    assert params["support"].default is None
    assert params["mode"].default is None


# ===========================================================================
# 5. The direct write into public_blogs (D16)
# ===========================================================================


def test_v1_sets_blog_id_equal_to_root_id(publish) -> None:
    """⚠ The app mints the uuid; ``root_id`` self-references ``blog_id``.

    Letting the column default ``blog_id`` leaves no way to name ``root_id``,
    and a wrong root_id breaks /seo, /cards and /retract — all of which address
    the LOGICAL blog.
    """
    db = FakeDB()
    result = publish(db)
    row = db.tables["public_blogs"][0]
    assert row["blog_id"] == row["root_id"]
    assert row["version_no"] == 1
    assert row["is_current"] is True
    assert result.root_id == row["root_id"]
    assert result.post_id == row["blog_id"]


def test_the_job_writes_no_blog_posts_row(publish) -> None:
    """D16 — the editorial job does NOT flow through the frozen snapshot table."""
    db = FakeDB()
    publish(db)
    assert "blog_posts" not in db.tables
    assert all(t != "blog_posts" for _op, t, _f in db.calls)


def test_the_result_carries_root_id_and_slug_for_the_next_marketing_call(publish) -> None:
    db = FakeDB()
    result = publish(db)
    assert result.root_id
    assert result.slug
    assert result.url == f"https://rayhanai.com/blog/{result.slug}"
    # D17 — a public blog is open; there is no token to hold.
    assert result.token is None


def test_root_id_and_slug_survive_the_result_model_round_trip(publish) -> None:
    """⚠ ``response_model`` strips undeclared keys.

    The result is stored as raw JSON on the job row and re-parsed into
    ``BlogJobResult`` on the poll path — an undeclared key vanishes silently and
    marketing's next call has nothing to address.
    """
    from backend.app.api.deepsearch_api.models import BlogJobResult

    db = FakeDB()
    result = publish(db)
    reparsed = BlogJobResult(**result.model_dump(mode="json"))
    assert reparsed.root_id == result.root_id
    assert reparsed.slug == result.slug
    assert reparsed.is_public == result.is_public


def test_the_headline_is_lifted_into_title_and_stripped_from_the_body(publish) -> None:
    """§6's H1 contract — the hero would otherwise double-render the headline."""
    db = FakeDB()
    result = publish(db)
    row = db.tables["public_blogs"][0]
    assert row["title"] == "إصلاح المركبة قبل صدور حكم لجنة المنازعات التأمينية"
    assert not row["content_md"].lstrip().startswith("# ")
    # `##` section headings — the §4 TOC entries — must survive untouched.
    assert "## أولاً: النظام لا يمنع الإصلاح المسبق" in row["content_md"]
    assert "## الخلاصة" in row["content_md"]
    assert result.title == row["title"]


def test_a_request_title_wins_over_the_h1(publish) -> None:
    db = FakeDB()
    publish(db, job={"title": "عنوان من التسويق"})
    row = db.tables["public_blogs"][0]
    assert row["title"] == "عنوان من التسويق"
    # ...and the H1 line is STILL stripped.
    assert not row["content_md"].lstrip().startswith("# ")


def test_the_slug_is_minted_from_the_title_when_none_was_sent(publish) -> None:
    db = FakeDB()
    result = publish(db, job={"title": "حقوق العامل عند إنهاء العقد"})
    assert result.slug == "حقوق-العامل-عند-إنهاء-العقد"


def test_a_supplied_slug_is_used_verbatim(publish) -> None:
    db = FakeDB()
    result = publish(db, cfg={"slug": ARABIC_SLUG})
    assert result.slug == ARABIC_SLUG


def test_an_ascii_title_cannot_mint_a_subject_shaped_slug(publish) -> None:
    """A blog slug is Arabic by construction (D4) — the ASCII shape is a
    SUBJECT's, and a blog wearing it would be unreachable through the
    dispatcher. Refused with a clean Arabic 400, not an opaque 23514."""
    db = FakeDB()
    with pytest.raises(LunaHTTPException) as e:
        publish(db, job={"title": "Labor Law Explained"}, content_md="نص بلا عنوان.")
    assert e.value.status_code == 400
    assert db.tables["public_blogs"] == []


def test_publish_public_true_lands_listed(publish) -> None:
    db = FakeDB()
    result = publish(db, cfg={"publish_public": True})
    assert db.tables["public_blogs"][0]["is_public"] is True
    assert result.is_public is True


def test_an_omitted_publish_public_lands_LISTED(publish) -> None:
    """End to end on the default: an omitted flag must reach the row as TRUE.

    The cfg here is built by ``editorial_config`` from a body that never
    mentions ``publish_public`` — the same blob ``_insert_job`` would store — so
    this covers the whole path rather than the model default alone.
    """
    db = FakeDB()
    result = publish(db, cfg=service.editorial_config(_req()))
    assert db.tables["public_blogs"][0]["is_public"] is True
    assert result.is_public is True


def test_publish_public_false_lands_UNLISTED_not_hidden(publish) -> None:
    """§5's table: is_public=false is absent from every index but the slug
    still resolves. It is NOT a draft.

    Opting out has to keep working now that the default is ``True`` — retract is
    for taking something back, not the only way to never list it.
    """
    db = FakeDB()
    result = publish(db, cfg=service.editorial_config(_req(publish_public=False)))
    row = db.tables["public_blogs"][0]
    assert row["is_public"] is False
    assert row["is_published"] is True        # still reachable by link
    assert result.is_public is False


def test_low_confidence_is_written_unpublished_whatever_the_request_asked(
    publish,
) -> None:
    """⚠ publish_policy/min_confidence owns ``is_published``, not the request.

    A low-confidence article is the one thing that must be genuinely
    unreachable (404), and asking for publish_public cannot override it.
    """
    db = FakeDB()
    result = publish(db, wi_confidence="low", cfg={"publish_public": True})
    row = db.tables["public_blogs"][0]
    assert row["is_published"] is False
    assert result.is_published is False
    # is_public is orthogonal and still honours the request.
    assert row["is_public"] is True


def test_references_are_frozen_onto_the_row(publish) -> None:
    """D18 — the citation set is CLOSED, which is what bounds the SEO agent."""
    db = FakeDB()
    result = publish(db, references=[_Ref(1), _Ref(2), _Ref(4)])
    row = db.tables["public_blogs"][0]
    assert [r["n"] for r in row["references_json"]] == [1, 2, 4]
    assert result.references.count == 3


def test_the_frozen_references_carry_has_source(publish) -> None:
    """🚨 THE «عرض المصدر» FLAG. Without it the metered reveal does not render.

    `ReferencePanel` gates the affordance on `has_source === true`, and a blog
    reader has no workspace item to probe — so the flag has to be IN the frozen
    snapshot. Dumping `Reference` models straight to JSON (what this path did
    until 2026-09-03) drops it: `Reference` has no such field. Measured on the
    two live articles: 15 references, not one carrying the key, so the button
    never appeared even though the reveal endpoint could serve every one of them.
    """
    db = FakeDB()
    publish(db, references=[_Ref(1), _Ref(2)])
    frozen = db.tables["public_blogs"][0]["references_json"]
    assert [r["has_source"] for r in frozen] == [True, True]


def test_a_reference_whose_source_cannot_be_rebuilt_is_frozen_false(publish) -> None:
    """The flag is the READ's answer, not a constant.

    `resolvable_ns` is the set of `n` whose URA shell was reconstructed — a
    citation whose source row is gone renders as a stub card with nothing to
    reveal, and freezing True for it would offer a button that 404s.
    """
    db = FakeDB()
    publish(db, references=[_Ref(1), _Ref(2), _Ref(4)], resolvable={1, 4})
    frozen = db.tables["public_blogs"][0]["references_json"]
    assert {r["n"]: r["has_source"] for r in frozen} == {1: True, 2: False, 4: True}


def test_the_snapshot_is_built_by_the_PAYLOAD_builder_not_by_dumping_models(
    publish,
) -> None:
    """🚨 THE ROOT CAUSE, pinned.

    `fetch_item_references` returns `Reference` MODELS, and a `Reference` knows
    nothing about `has_source` or `library_url` — both are added by
    `fetch_item_references_payload`, which is what a READER needs and what the
    legacy `blog_posts` share snapshot has always frozen. This path used to
    `model_dump` the models, so the public wing captured strictly less than the
    legacy wing from the same data: measured on the two live articles, 15
    references with neither key.

    Reverting to the model dump would leave almost every other test in this file
    passing, which is exactly why this one names the call.
    """
    import inspect

    src = inspect.getsource(service._publish_to_public_blog)
    assert "fetch_item_references_payload" in src
    # Comments stripped: the block above deliberately NAMES the mistake it
    # replaced, and matching prose would make this pass or fail on how carefully
    # the fix was documented.
    code = "\n".join(
        ln for ln in src.splitlines() if not ln.lstrip().startswith("#")
    )
    assert "model_dump" not in code, (
        "the snapshot is being rebuilt from models again — has_source and "
        "library_url do not survive a Reference.model_dump()"
    )


def test_the_frozen_references_carry_library_url(publish) -> None:
    """«افتح في ريحان» — NAVIGATION, never a charge, never guessed.

    A blog reader has no workspace item, so this link has to be IN the snapshot
    like `has_source` is. Its absence is what left the two compliance citations
    on the live articles with no affordance whatsoever: empty `landing_url`, no
    reveal, no in-app link.
    """
    db = FakeDB()
    publish(
        db,
        references=[_Ref(1), _Ref(2)],
        library_urls={1: "/regulations/nizam-al-amal"},
    )
    frozen = db.tables["public_blogs"][0]["references_json"]
    assert frozen[0]["library_url"] == "/regulations/nizam-al-amal"
    # Present-and-null, never absent: an unresolved reference is a card with no
    # in-app button, and the client reads one shape either way.
    assert frozen[1]["library_url"] is None
    assert "library_url" in frozen[1]


def test_a_reference_with_no_library_page_gets_no_link_not_a_guess(publish) -> None:
    """A button into a 404 is strictly worse than no button. The resolver returns
    keys only for references that actually resolved; nothing in this path may
    synthesise a URL for the rest."""
    db = FakeDB()
    publish(db, references=[_Ref(1)], library_urls={})
    assert db.tables["public_blogs"][0]["references_json"][0]["library_url"] is None


def test_provenance_links_back_to_the_workspace_item(publish) -> None:
    db = FakeDB()
    result = publish(db, workspace_item_id="wi-42")
    assert db.tables["public_blogs"][0]["source_item_id"] == "wi-42"
    assert result.workspace_item_id == "wi-42"


def test_subjects_are_filed_against_the_root_id(publish) -> None:
    """Keyed on the LOGICAL blog so an SEO rewrite never re-files them."""
    db = FakeDB()
    db.seed_subjects()
    result = publish(db, cfg={"subjects": ["work-law", "saudization"]})
    joins = db.tables["public_blog_subjects"]
    assert {j["subject_id"] for j in joins} == {"s-work", "s-saud"}
    assert all(j["root_id"] == result.root_id for j in joins)


def test_a_subject_deactivated_mid_run_does_not_lose_the_article(publish) -> None:
    """⚠ Deliberate: the blog is already written by the time filing runs.

    Failing the job here would leave a published article behind a "failed" job,
    and the catch-up re-drive would then collide with its own slug (409).
    """
    db = FakeDB()
    db.seed_subjects()
    result = publish(db, cfg={"subjects": ["work-law", "does-not-exist"]})
    assert result.root_id                                   # the blog survived
    assert len(db.tables["public_blogs"]) == 1
    assert db.tables["public_blog_subjects"] == []          # nothing half-filed


def test_a_chat_only_route_is_forced_to_low_confidence(publish) -> None:
    """No workspace item ⇒ no documented analysis ⇒ never auto-published.

    A pinned dispatch makes this route nearly unreachable (the router, the one
    thing that can answer without producing an artifact, is bypassed), but the
    gate has to hold if it ever is reached: an article with no evidence behind
    it must not land published.
    """
    db = FakeDB()
    result = publish(
        db, workspace_item_id=None, content_md=ARTICLE_MD, cfg={"publish_public": True}
    )
    assert result.confidence.label == "low"
    assert db.tables["public_blogs"][0]["is_published"] is False


def test_a_body_with_no_headline_and_no_title_is_a_clean_400(publish) -> None:
    """``public_blogs.title`` is NOT NULL. The publisher must refuse in Arabic
    rather than let PostgREST raise a 23502 nobody can act on."""
    db = FakeDB()
    with pytest.raises(LunaHTTPException) as e:
        publish(db, content_md="نص بلا عنوان في السطر الأول.", wi_title=None)
    assert e.value.status_code == 400
    assert db.tables["public_blogs"] == []


def test_the_stored_question_is_the_jobs_anonymized_one(publish) -> None:
    """``public_blogs.question_text`` is NOT NULL and ships in the public JSON —
    store the anonymized question, never a raw one."""
    db = FakeDB()
    result = publish(db, job={"question": "سؤال مجهول الهوية"})
    assert db.tables["public_blogs"][0]["question_text"] == "سؤال مجهول الهوية"
    assert result.question_text == "سؤال مجهول الهوية"


# ===========================================================================
# 5b. One job publishes at most one blog (migration 156)
# ===========================================================================
#
# THE BUG, measured in production 2026-09-02: one POST to /internal/blog-post-jobs
# produced TWO published rows 60 seconds apart, with different slugs:
#
#     34f7e7ef…  فسخ-عقد-الإيجار-التجاري-…
#     411be387…  فسخ-العقود-والشرط-الجزائي-…
#
# `create_or_get_job` had deduped correctly — only ONE job ever existed. The
# duplicate came from the publish step, whose only guard was
# `assert_slug_available`. That guard cannot work here: the slug is derived from
# the aggregator's headline, which is NON-DETERMINISTIC, so a re-drive mints a
# different slug, passes the uniqueness check, and publishes again.
#
# These tests therefore construct the failure explicitly — the second attempt is
# given a DIFFERENT headline — instead of relying on the slug happening to match.
# A test that reused the same body would pass against the old, broken code.


# The same job's second attempt, as the LLM would actually re-write it: a
# different headline, hence a different slug. Nothing about this body collides
# with ARTICLE_MD on any uniqueness check except the job.
REGENERATED_MD = """# فسخ العقود والشرط الجزائي في النظام السعودي

يواجه كثير من المتعاقدين خلافاً حول الشرط الجزائي [1].

## أولاً: القاعدة العامة

نص القسم الأول [2].

## الخلاصة

خلاصة المقال."""


def test_a_redrive_with_a_DIFFERENT_headline_still_yields_one_blog(publish) -> None:
    """⚠ THE REGRESSION TEST FOR THE LIVE BUG.

    Attempt two regenerates the article, gets a different headline, and mints a
    different slug — so ``assert_slug_available`` waves it straight through,
    exactly as it did in production. Only the per-job unique index stops it.
    """
    db = FakeDB()
    first = publish(db, content_md=ARTICLE_MD)
    second = publish(db, content_md=REGENERATED_MD)

    # The two attempts really did want different slugs — otherwise this test
    # would pass for the wrong reason (the slug index catching it).
    assert slugify_ar("فسخ العقود والشرط الجزائي في النظام السعودي") != first.slug

    assert len(db.tables["public_blogs"]) == 1, "one job must publish ONE blog"
    assert second.post_id == first.post_id
    assert second.root_id == first.root_id
    assert second.slug == first.slug
    # The live row is the FIRST article, not the regenerated one.
    assert db.tables["public_blogs"][0]["title"] == first.title


def test_publishing_twice_for_one_job_returns_the_same_post_id(publish) -> None:
    """``post_id`` is stable across re-drives — marketing may already hold it.

    This is the SAME-headline re-drive, the mirror of the test above. It fails
    differently and had to be fixed differently: an identical headline mints an
    identical slug, so ``assert_slug_available`` refuses it — the job 409s
    against **the article it published itself**, and the caller is told the
    publish failed while a live URL sits there. Hence the ordering in
    ``insert_public_blog``: "have I already published?" is asked BEFORE "is this
    slug free?".
    """
    db = FakeDB()
    first = publish(db)
    second = publish(db)          # must not raise 409 against itself
    assert len(db.tables["public_blogs"]) == 1
    assert second.post_id == first.post_id
    assert second.url == first.url
    assert second.is_public == first.is_public
    assert second.is_published == first.is_published


def test_the_job_check_is_asked_BEFORE_the_slug_check(publish) -> None:
    """⚠ Pins the order, not just the outcome.

    Swap the two and this suite still mostly passes — only the same-headline
    re-drive breaks, and it breaks as a 409 that reads like a legitimate slug
    conflict. Assert the question order directly so the trap is visible in the
    source rather than only in one downstream symptom.
    """
    import inspect

    src = inspect.getsource(pbs.insert_public_blog)
    job_check = src.index("get_by_job_id(supabase, job_id)")
    slug_check = src.index("assert_slug_available(supabase, slug)")
    assert job_check < slug_check, (
        "the job's own publication must be checked before the slug, or a "
        "same-headline re-drive 409s against the article it just published"
    )


def test_a_redrive_never_reports_a_slug_conflict_with_itself(publish) -> None:
    """The failure mode the ordering fixes, stated as a behaviour."""
    db = FakeDB()
    publish(db)
    try:
        publish(db)
    except LunaHTTPException as e:  # pragma: no cover - the regression
        pytest.fail(f"re-drive raised {e.status_code} instead of recovering")


def test_the_v1_row_carries_its_job_id(publish) -> None:
    db = FakeDB()
    publish(db, job={"job_id": "job-42"})
    assert db.tables["public_blogs"][0]["job_id"] == "job-42"


def test_two_DIFFERENT_jobs_may_both_publish(publish) -> None:
    """The index is per job, not a global "one blog ever" switch."""
    db = FakeDB()
    a = publish(db, job={"job_id": "job-a"}, content_md=ARTICLE_MD)
    b = publish(db, job={"job_id": "job-b"}, content_md=REGENERATED_MD)
    assert len(db.tables["public_blogs"]) == 2
    assert a.root_id != b.root_id


def test_an_seo_rewrite_is_not_blocked_by_the_job_index(publish) -> None:
    """⚠ Versions carry NULL ``job_id`` — the constraint is "one job → one BLOG",
    not "one job → one version".

    ``append_public_blog_version`` does not copy the column, so if the index were
    written without its ``job_id IS NOT NULL`` predicate (or the fake enforced it
    that way), the FIRST SEO rewrite of every article would fail.
    """
    db = FakeDB()
    result = publish(db, job={"job_id": "job-1"})
    v2 = pbs.append_version(db, result.root_id, content_md=REGENERATED_MD)
    assert v2["version_no"] == 2
    assert v2.get("job_id") is None
    assert len(db.tables["public_blogs"]) == 2


def test_the_job_conflict_is_matched_narrowly() -> None:
    """A different 23505 must NOT be read as the job conflict.

    Mistaking the slug or version index for the job index would hand this job
    somebody else's blog — a far worse failure than the duplicate it replaces.
    """
    from backend.tests.test_public_blogs import _UniqueViolation

    assert pbs._is_job_conflict(
        _UniqueViolation('duplicate key ... "idx_public_blogs_job"')
    )
    assert not pbs._is_job_conflict(
        _UniqueViolation('duplicate key ... "idx_public_blogs_slug"')
    )
    assert not pbs._is_job_conflict(
        _UniqueViolation('duplicate key ... "idx_public_blogs_current"')
    )


def test_the_discarded_attempt_does_not_refile_subjects_wrongly(publish) -> None:
    """A recovered publish must not leave the blog filed under the wrong shelf.

    The second attempt returns EARLY, before subject filing, so the subjects the
    first attempt filed are what stand.
    """
    db = FakeDB()
    db.seed_subjects()
    first = publish(db, cfg={"subjects": ["work-law"]})
    publish(db, content_md=REGENERATED_MD, cfg={"subjects": ["saudization"]})
    joins = db.tables["public_blog_subjects"]
    assert {j["subject_id"] for j in joins} == {"s-work"}
    assert all(j["root_id"] == first.root_id for j in joins)


def test_the_publisher_stamps_post_id_onto_the_job_row(publish) -> None:
    """What makes the cheap re-drive path possible.

    ``post_id`` is otherwise written by the same UPDATE that marks the job
    completed — i.e. never, in the exact crash window this guards.
    """
    db = FakeDB()
    result = publish(db)
    updates = [
        (op, table) for op, table, _f in db.calls
        if table == "blog_post_jobs" and op == "update"
    ]
    assert updates, "the job row is never told what it published"
    assert result.post_id


# -- the fast path: a re-drive costs one read, not a pipeline run -----------


def test_a_redrive_does_not_re_run_generation(monkeypatch, _settings) -> None:
    """⚠ The point of the fast path.

    A restart across an in-flight job re-spawns this worker. If the previous
    attempt already published, regenerating costs a full deep_search run and —
    before migration 156 — minted a second article. Neither should happen.
    """
    db = FakeDB()
    row = db.seed_blog(
        blog_id="b1", root_id="b1", slug=ARABIC_SLUG, job_id="job-1",
        confidence="high", source_item_id="wi-1",
    )
    job = {
        "job_id": "job-1",
        "question": "سؤال",
        "metadata": {},
        "publish_policy": "auto",
        "min_confidence": "medium",
    }

    generated = MagicMock()
    monkeypatch.setattr(service, "get_supabase_client", lambda: db)
    monkeypatch.setattr(service, "_mark_processing", lambda _sb, _jid: job)
    monkeypatch.setattr(service, "generate_answer_headless", generated)

    asyncio.run(service.process_job("job-1"))

    assert generated.call_count == 0, "a re-drive must not regenerate"
    assert len(db.tables["public_blogs"]) == 1
    # ...and the job completed, carrying the row it had already published.
    completions = [
        c for op, table, c in db.calls if table == "blog_post_jobs" and op == "update"
    ]
    assert completions


def test_the_redrive_result_matches_what_the_first_run_returned(publish) -> None:
    """The two completion paths must agree byte for byte on the payload.

    A caller polling across a restart must not see the job change its mind about
    what it produced.
    """
    db = FakeDB()
    first = publish(db)
    row = pbs.get_by_job_id(db, "job-1")
    assert row is not None
    rebuilt = service._result_from_row(row, {"question": "سؤال", "job_id": "job-1"})

    assert rebuilt.post_id == first.post_id
    assert rebuilt.root_id == first.root_id
    assert rebuilt.slug == first.slug
    assert rebuilt.url == first.url
    assert rebuilt.title == first.title
    assert rebuilt.content_md == first.content_md
    assert rebuilt.is_public == first.is_public
    assert rebuilt.is_published == first.is_published
    assert rebuilt.confidence.label == first.confidence.label
    assert rebuilt.references.count == first.references.count
    assert rebuilt.workspace_item_id == first.workspace_item_id


def test_a_rebuilt_chat_only_result_keeps_its_low_confidence_rationale(publish) -> None:
    """``have_workspace_item`` is recovered from ``source_item_id``, not assumed.

    A chat-only route stored NULL there and was forced to ``low``; rebuilding it
    as though it had an artifact would invent a rationale the run never gave.
    """
    db = FakeDB()
    first = publish(db, workspace_item_id=None, content_md=ARTICLE_MD)
    row = pbs.get_by_job_id(db, "job-1")
    rebuilt = service._result_from_row(row, {"question": "س", "job_id": "job-1"})
    assert rebuilt.confidence.label == "low"
    assert rebuilt.confidence.reasons == first.confidence.reasons


def test_get_by_job_id_finds_a_superseded_v1(publish) -> None:
    """⚠ Not filtered on ``is_current``.

    ``job_id`` lives on v1 only, so once an SEO rewrite supersedes it an
    is_current filter would answer "this job never published" about a live
    article — and the re-drive would publish a second one.
    """
    db = FakeDB()
    result = publish(db)
    pbs.append_version(db, result.root_id, content_md=REGENERATED_MD)
    row = pbs.get_by_job_id(db, "job-1")
    assert row is not None
    assert row["is_current"] is False
    assert row["root_id"] == result.root_id


# ===========================================================================
# 6. Retract
# ===========================================================================


def test_retract_flips_only_the_current_version() -> None:
    db = FakeDB()
    db.seed_blog(blog_id="v1", root_id="v1", version_no=1, is_current=False)
    db.seed_blog(
        blog_id="v2", root_id="v1", version_no=2, is_current=True, slug=ARABIC_SLUG
    )
    # An unrelated blog must not be touched.
    db.seed_blog(blog_id="other", root_id="other", slug="مقال-آخر")

    pbs.set_public(db, "v1", False)

    by_id = {r["blog_id"]: r for r in db.tables["public_blogs"]}
    assert by_id["v2"]["is_public"] is False      # the current version
    assert by_id["v1"]["is_public"] is True       # the superseded one, untouched
    assert by_id["other"]["is_public"] is True    # a different blog, untouched


def test_retract_delists_but_does_not_unpublish_or_delete() -> None:
    """The URL keeps resolving — retract is DELISTING, not removal (§5)."""
    db = FakeDB()
    db.seed_blog(blog_id="b1", root_id="b1")
    pbs.set_public(db, "b1", False)
    row = db.tables["public_blogs"][0]
    assert row["is_public"] is False
    assert row["is_published"] is True
    assert row["deleted_at"] is None


def test_retract_on_an_unknown_root_is_a_404_in_arabic() -> None:
    db = FakeDB()
    with pytest.raises(LunaHTTPException) as e:
        pbs.set_public(db, "no-such-root", False)
    assert e.value.status_code == 404
    assert "المدونة" in _detail(e)


def test_the_retract_route_is_registered_under_internal() -> None:
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/internal/public-blogs/{root_id}/retract" in paths


def test_the_retract_route_requires_the_service_key() -> None:
    """Fail-closed auth is the whole security boundary for this surface."""
    from backend.app.api.deepsearch_api.auth import _verify_service_key
    from backend.app.main import create_app

    route = next(
        r for r in create_app().routes
        if getattr(r, "path", "") == "/internal/public-blogs/{root_id}/retract"
    )
    deps = [d.call for d in route.dependant.dependencies]
    assert _verify_service_key in deps


def test_the_retract_route_is_never_owner_scoped() -> None:
    """⚠ Deliberate. The in-app publish/unpublish routes filter by user_id, so a
    moderator hitting editorial-bot's row gets a 404 rather than a 403 — no
    user-facing flag can fix that. The service key is the authority."""
    import inspect

    from backend.app.api.deepsearch_api.router import retract_public_blog

    params = inspect.signature(retract_public_blog).parameters
    assert "user_id" not in params
    assert "current_user" not in params


def test_the_internal_public_blogs_prefix_skips_the_global_rate_limiter() -> None:
    """Same exemption the job routes carry — the service key is the boundary,
    and a retraction is exactly the request you never want deferred."""
    import inspect
    import re

    from backend.app.middleware.rate_limit import RateLimitMiddleware

    src = inspect.getsource(RateLimitMiddleware.dispatch)
    skip = re.search(r"startswith\(\s*\((.*?)\)\s*\)", src, re.S)
    assert skip is not None, "the prefix skip block is gone"
    assert "/internal/public-blogs" in skip.group(1)
    assert "/internal/blog-post-jobs" in skip.group(1)
