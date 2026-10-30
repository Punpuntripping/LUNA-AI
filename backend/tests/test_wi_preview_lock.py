"""WI preview lock — truncation, lock decision, and every backend surface.

Plan: ``.claude/plans/wi_preview_lock.md`` (owner decisions 2026-10-30).

The fixture bodies are SYNTHETIC but REAL-SHAPED: their block structure copies
the line-by-line shape of live ``workspace_items.content_md`` rows read from
prod on 2026-10-30 (line lengths, headings, tables, lists, ``[n]`` density —
a 6.6k deep-search synthesis with a 6-row table, a 7.6k defence brief with
bold-numbered paragraphs and a signature block, a 1.3k simple-search list…).
The wording is generic legal Arabic: no name, number or identifier from any
real item.

No live DB: Supabase is an in-memory fake that honours the filters the code
uses; the flag is switched on by patching ``preview_lock.get_settings``.
"""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import pytest

from shared.workspace import preview_lock as pl
from shared.workspace.preview_lock import (
    LEGACY_WALL_SUFFIX,
    LockPolicy,
    cited_numbers,
    hidden_refs_line_ar,
    is_lockable,
    is_locked,
    preview_budget,
    preview_cut,
)


# ===========================================================================
# Real-shaped bodies
# ===========================================================================

_SENTENCES = [
    "يلتزم صاحب العمل بإبرام عقد مكتوب مع العامل يحدد فيه الأجر ومدة العقد وطبيعة العمل",
    "ويجوز للطرفين الاتفاق على فترة تجربة لا تتجاوز المدة المحددة نظاماً",
    "وتختص المحكمة العمالية بالنظر في المنازعات الناشئة عن عقد العمل",
    "ولا يجوز فسخ العقد دون مكافأة إلا في الحالات المنصوص عليها حصراً",
    "وقد استقر القضاء على أن عبء الإثبات يقع على من يدعي خلاف الظاهر",
    "ويستحق العامل مكافأة نهاية الخدمة وفق المدة التي قضاها في العمل",
    "وتسري أحكام اللائحة التنفيذية على ما لم يرد فيه نص خاص",
    "ويُعد الإخلال بالالتزامات التعاقدية موجباً للتعويض متى ثبت الضرر",
    "وللجهة المختصة إيقاع الغرامة المقررة على المخالف بعد إنذاره",
    "ويشترط لقبول الدعوى أن تُرفع خلال المدة النظامية المحددة",
    "ولا يحول ذلك دون حق المتضرر في المطالبة بالتعويض أمام الجهة القضائية",
    "وتُحسب المدد بالتقويم الميلادي ما لم ينص العقد على غير ذلك",
]
_ENDINGS = [".", ".", "؛", ".", "؟", "."]


class _Gen:
    """Deterministic sentence stream with running ``[n]`` citation markers."""

    def __init__(self, markers: bool = True) -> None:
        self.i = 0
        self.n = 0
        self.markers = markers

    def sentence(self) -> str:
        s = _SENTENCES[self.i % len(_SENTENCES)]
        end = _ENDINGS[self.i % len(_ENDINGS)]
        self.i += 1
        if self.markers and self.i % 2 == 0:
            self.n += 1
            return f"{s} [{self.n}]{end}"
        return f"{s}{end}"

    def para(self, target: int) -> str:
        out = self.sentence()
        while len(out) < target:
            out += " " + self.sentence()
        return out


def _search_with_table() -> str:
    """Deep-search synthesis, ~6.6k: lead, a 6-row table, sections, closing."""
    g = _Gen()
    blocks = [
        g.para(590),
        "## الأحكام الأساسية المنطبقة",
        "| المسألة | الحكم النظامي | المرجع |\n|---|---|---|\n"
        + "\n".join(
            f"| مسألة تعاقدية رقم {k} في العقد | حكمها وفق النظام واللائحة | [{k}] |"
            for k in range(1, 7)
        ),
        g.para(440),
        g.para(330),
        "## من المادة الأولى إلى السادسة: الالتزامات والحقوق",
        g.para(770),
        "## الاجتهاد القضائي",
        g.para(570),
        "## الإجراءات والمدد النظامية",
        g.para(1250),
        "## التوصية العملية",
        g.para(660),
        g.para(260),
    ]
    return "\n\n".join(blocks)


def _search_with_lists() -> str:
    """Deep-search synthesis, ~5.3k: headings + bullet lists + markers."""
    g = _Gen()
    blocks = [g.para(450)]
    for title in ("## الإطار النظامي", "## الشروط", "## الاستثناءات", "## الخلاصة"):
        blocks.append(title)
        blocks.append(g.para(380))
        blocks.append("\n".join(f"- {g.sentence()}" for _ in range(4)))
    blocks.append(g.para(300))
    return "\n\n".join(blocks)


def _writer_defense_brief() -> str:
    """Writer defence brief, ~7.6k: title, parties, bold-numbered paragraphs,
    lists, a signature block on consecutive lines, numbered annexes. No [n]."""
    g = _Gen(markers=False)
    ordinals = ["أولاً", "ثانياً", "ثالثاً", "رابعاً", "خامساً", "سادساً", "سابعاً"]
    blocks = [
        "# مذكرة جوابية مقدمة في الدعوى المقيدة لدى المحكمة المختصة",
        "## بيانات الأطراف",
        "المدعي: [اسم المدعي] — المدعى عليه: [اسم المدعى عليه]",
        "بصفته وكيلاً عن المدعى عليه",
        g.para(680),
        "**موضوع الدعوى:** " + g.para(190),
        "**طلبات المدعي كما وردت في صحيفته:** " + g.para(290),
        "## الوقائع",
    ]
    blocks += [f"**{o}:** " + g.para(220) for o in ordinals[:5]]
    blocks.append("\n".join(f"- {g.sentence()}" for _ in range(5)))
    blocks.append("## الدفوع الشكلية")
    blocks += [g.para(390), g.para(300), g.para(470)]
    blocks.append("## الدفوع الموضوعية")
    blocks += [f"**{k}- {g.sentence()}** " + g.para(380) for k in range(1, 7)]
    blocks.append("## الطلبات")
    blocks.append(g.para(280))
    blocks.append("\n".join(f"- {g.sentence()}" for _ in range(3)))
    blocks.append("## الختام")
    blocks.append("وتفضلوا بقبول فائق الاحترام\nوكيل المدعى عليه\nالمحامي [الاسم]\nالتاريخ: [  /  / 14هـ]")
    blocks.append("## المرفقات")
    blocks.append("1. صورة من العقد محل النزاع\n2. صورة من الإنذار الموجه للمدعي")
    return "\n\n".join(blocks)


def _simple_search() -> str:
    """Simple search, ~1.3k: no headings, one paragraph and a list, two markers."""
    g = _Gen()
    g.n = 0
    lead = g.para(260)
    items = "\n".join(f"{k}. {g.sentence()}" for k in range(1, 10))
    return lead + "\n\n" + items


def _one_big_paragraph() -> str:
    """No structure at all: a single ~5k paragraph (forces a sentence cut)."""
    return _Gen().para(5000)


def _first_block_dominates() -> str:
    """A 3k opening paragraph, then short sections (prefix alone is < 30%)."""
    g = _Gen()
    return "\n\n".join([
        g.para(3000), "## تفاصيل", g.para(300), g.para(300), "## خاتمة", g.para(250),
    ])


def _table_heavy() -> str:
    """Short lead + a 30-row table straddling the budget + paragraphs."""
    g = _Gen()
    table = "| البند | الحكم | الملاحظة |\n|---|---|---|\n" + "\n".join(
        f"| البند {k} من العقد | يسري عليه الحكم العام | انظر المرجع [{k}] |" for k in range(1, 31)
    )
    return "\n\n".join([g.para(200), "## جدول البنود", table, g.para(700), g.para(500)])


def _short() -> str:
    """~900 chars, three paragraphs."""
    g = _Gen()
    return "\n\n".join([g.para(300), g.para(300), g.para(250)])


def _no_punctuation_short() -> str:
    """~360 chars on one line with no sentence terminator at all — a shape that
    exists live (simple-search answers); only the word-boundary cut can serve it."""
    words = "يتعين على المنشأة تسجيل العقد لدى الجهة المختصة خلال المدة المحددة نظاماً"
    out = words
    while len(out) < 360:
        out += " " + words
    return out


REAL_SHAPED = {
    "no_punctuation_short": _no_punctuation_short,
    "search_with_table": _search_with_table,
    "search_with_lists": _search_with_lists,
    "writer_defense_brief": _writer_defense_brief,
    "simple_search": _simple_search,
    "one_big_paragraph": _one_big_paragraph,
    "first_block_dominates": _first_block_dominates,
    "table_heavy": _table_heavy,
    "short": _short,
}

_HEADING_LINE = re.compile(r"^ {0,3}#{1,6}(\s|$)")


def _assert_well_formed(body: str, visible: str) -> None:
    assert body.startswith(visible), "the preview must be a prefix of the stored body"
    # No split marker: nothing like «[1» or «[» dangling at the end.
    assert not re.search(r"\[\d*$", visible)
    assert cited_numbers(visible) <= cited_numbers(body)
    last_line = visible.rsplit("\n", 1)[-1]
    assert not _HEADING_LINE.match(last_line), "never end on a heading"
    assert visible.count("**") % 2 == 0, "no unclosed bold run"
    assert visible == visible.rstrip()
    # A cut inside a table keeps its header + separator + at least one row.
    tail = visible.rsplit("\n\n", 1)[-1]
    if tail.lstrip().startswith("|"):
        rows = tail.split("\n")
        assert len(rows) >= 3 and set(rows[1].replace("|", "").strip()) <= set("-: ")


# ===========================================================================
# 1. The truncation
# ===========================================================================


@pytest.mark.parametrize("name", sorted(REAL_SHAPED))
def test_shown_share_is_between_30_and_40_percent(name):
    body = REAL_SHAPED[name]()
    cut = preview_cut(body)
    ratio = cut.shown_chars / cut.total_chars
    assert 0.30 <= ratio <= 0.40, f"{name}: {cut.shown_chars}/{cut.total_chars} = {ratio:.3f}"
    assert cut.total_chars == len(body)
    assert cut.shown_chars == len(cut.visible)
    assert cut.truncated
    _assert_well_formed(body, cut.visible)


def test_budget_never_exceeds_40_percent_on_any_length():
    """The 40% is a ceiling at EVERY size — round() may not push it over."""
    for total in range(1, 3000):
        assert preview_budget(total) <= 0.40 * total


def test_never_exceeds_40_percent_across_every_prefix_of_a_body():
    """Sweep: every block-count prefix of every fixture stays at or under 40%."""
    for name, make in REAL_SHAPED.items():
        blocks = make().split("\n\n")
        for k in range(1, len(blocks) + 1):
            body = "\n\n".join(blocks[:k])
            cut = preview_cut(body)
            assert cut.shown_chars <= 0.40 * cut.total_chars, (name, k)
            _assert_well_formed(body, cut.visible)


def test_preview_is_deterministic():
    body = _search_with_table()
    assert preview_cut(body) == preview_cut(body)


def test_paragraph_cut_lands_on_a_sentence_end_and_keeps_its_marker():
    body = _one_big_paragraph()
    visible = preview_cut(body).visible
    assert visible[-1] in ".؟!؛"
    # The marker that belongs to the last sentence is inside, never orphaned
    # at the head of the hidden part.
    rest = body[len(visible):].lstrip()
    assert not re.match(r"\[\d+\]", rest)


def test_marker_after_the_full_stop_rides_with_its_sentence():
    body = ("جملة أولى طويلة نسبياً عن الالتزام التعاقدي. [1] " * 40).strip()
    visible = preview_cut(body).visible
    assert visible.endswith("[1]")
    assert not re.match(r"\s*\[\d+\]", body[len(visible):])


def test_list_cut_lands_on_an_item_boundary():
    lead = "مقدمة قصيرة."
    items = "\n".join(f"- البند رقم {k} يتضمن حكماً تفصيلياً في المسألة المعروضة." for k in range(1, 40))
    body = lead + "\n\n" + items
    visible = preview_cut(body).visible
    last = visible.rsplit("\n", 1)[-1]
    assert last.startswith("- ") and last.endswith(".")
    assert body[len(visible)] == "\n"


def test_table_cut_keeps_header_and_separator():
    body = _table_heavy()
    visible = preview_cut(body).visible
    assert "| البند | الحكم | الملاحظة |\n|---|---|---|\n| البند 1" in visible
    assert visible.rsplit("\n", 1)[-1].endswith("|")


def test_trailing_heading_is_dropped():
    g = _Gen()
    body = "\n\n".join([g.para(700), "## قسم لاحق", g.para(400), g.para(900)])
    visible = preview_cut(body).visible
    assert "## قسم لاحق" not in visible
    assert body.index("## قسم لاحق") >= len(visible)


def test_empty_and_whitespace_bodies():
    assert preview_cut("").shown_chars == 0
    assert preview_cut(None).total_chars == 0


def test_cited_numbers_and_hidden_refs_line():
    assert cited_numbers("نص [1] ونص [12][3] و[x] و[ 4]") == {1, 12, 3}
    assert hidden_refs_line_ar(0) == ""
    assert hidden_refs_line_ar(1) == "ومصدر آخر في الجزء المقفل"
    assert hidden_refs_line_ar(2) == "ومصدران آخران في الجزء المقفل"
    assert hidden_refs_line_ar(5) == "و5 مصادر أخرى في الجزء المقفل"
    assert hidden_refs_line_ar(14) == "و14 مصدراً آخر في الجزء المقفل"


def test_legacy_line_matches_the_shared_web_and_app_contract():
    """The appended line is stripped by clients by exact match — pin both."""
    from pathlib import Path

    ts = Path(__file__).resolve().parents[2] / "frontend" / "lib" / "contracts" / "wi-preview.ts"
    src = ts.read_text(encoding="utf-8")
    assert f'WI_PREVIEW_LEGACY_LINE = "{pl.LEGACY_WALL_LINE}"' in src
    assert 'WI_PREVIEW_LEGACY_SUFFIX = "\\n\\n" + WI_PREVIEW_LEGACY_LINE' in src
    assert LEGACY_WALL_SUFFIX == "\n\n" + pl.LEGACY_WALL_LINE
    # No URL, no purchase verb — an old iOS build shows it verbatim.
    assert "http" not in pl.LEGACY_WALL_LINE and "اشترك" not in pl.LEGACY_WALL_LINE


# ===========================================================================
# 2. Which rows lock
# ===========================================================================

FROM = datetime(2026, 11, 5, 9, 0, tzinfo=timezone.utc)
POLICY = LockPolicy(lock_from=FROM)
AFTER = (FROM + timedelta(minutes=5)).isoformat()
BEFORE = (FROM - timedelta(seconds=1)).isoformat()


def _row(**over: Any) -> dict:
    row = {
        "item_id": "it-1",
        "user_id": "u-1",
        "conversation_id": "c-1",
        "kind": "agent_search",
        "created_by": "agent",
        "created_at": AFTER,
        "created_while_paid": False,
        "metadata": {"subtype": "legal_synthesis"},
        "content_md": _search_with_table(),
    }
    row.update(over)
    return row


@pytest.mark.parametrize(
    "over, lockable",
    [
        ({}, True),
        ({"kind": "agent_writing", "metadata": {"subtype": "defense_brief"}}, True),
        ({"kind": "agent_writing", "metadata": {"subtype": "memo"}}, True),
        ({"metadata": {}}, True),                                             # simple search
        ({"kind": "agent_writing", "metadata": {"subtype": "summary"}}, False),   # §9.1
        ({"kind": "agent_writing", "metadata": {"subtype": "memory_file"}}, False),
        ({"kind": "note", "metadata": {"subtype": "statute_package"}}, False),    # §9.1
        ({"kind": "agent_search", "metadata": {"subtype": "statute_package"}}, False),
        ({"kind": "note", "metadata": {"subtype": "memo"}}, False),
        ({"kind": "note", "created_by": "user", "metadata": {}}, False),
        ({"created_by": "user"}, False),                                      # blog import
        ({"kind": "attachment", "created_by": "user"}, False),
        ({"kind": "references", "created_by": "user"}, False),
        ({"kind": "convo_context"}, False),
    ],
)
def test_lockable_kinds(over, lockable):
    assert is_lockable(_row(**over)) is lockable


def test_lock_clauses():
    assert is_locked(_row(), POLICY)
    assert is_locked(_row(created_at=FROM.isoformat()), POLICY)            # at = after
    assert not is_locked(_row(created_at=BEFORE), POLICY)                  # older stays open
    assert not is_locked(_row(created_while_paid=True), POLICY)            # §9.2
    assert not is_locked(_row(), None)                                     # flag off / paid
    assert not is_locked(_row(), POLICY, is_demo=True)
    assert not is_locked(_row(created_at=None), POLICY)
    # A row without the 198 column reads as "not made while paid".
    row = _row()
    row.pop("created_while_paid")
    assert is_locked(row, POLICY)


def _settings(enabled: bool, lock_from: Any = FROM):
    return SimpleNamespace(WI_PREVIEW_LOCK_ENABLED=enabled, WI_PREVIEW_LOCK_FROM=lock_from)


def test_configured_lock_from(monkeypatch):
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(False))
    assert pl.configured_lock_from() is None
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(True, None))
    assert pl.configured_lock_from() is None          # misconfigured → nothing locks
    naive = datetime(2026, 11, 5, 9, 0)
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(True, naive))
    assert pl.configured_lock_from() == FROM


def test_settings_parse_the_env_names(monkeypatch):
    from shared.config import Settings

    monkeypatch.setenv("WI_PREVIEW_LOCK_ENABLED", "true")
    monkeypatch.setenv("WI_PREVIEW_LOCK_FROM", "2026-11-05T09:00:00")
    s = Settings()
    assert s.WI_PREVIEW_LOCK_ENABLED is True
    assert s.WI_PREVIEW_LOCK_FROM == FROM
    monkeypatch.setenv("WI_PREVIEW_LOCK_FROM", "")
    assert Settings().WI_PREVIEW_LOCK_FROM is None
    monkeypatch.delenv("WI_PREVIEW_LOCK_ENABLED")
    monkeypatch.delenv("WI_PREVIEW_LOCK_FROM")
    assert Settings().WI_PREVIEW_LOCK_ENABLED is False


@pytest.fixture
def paid_lookup(monkeypatch):
    """Switch the flag on and script the library paid predicate."""
    import shared.quota as quota

    state = {"paid": False, "calls": 0, "fail": False}

    async def _library_state(_sb, _uid):
        state["calls"] += 1
        if state["fail"]:
            raise RuntimeError("rpc down")
        return SimpleNamespace(is_paid=state["paid"])

    monkeypatch.setattr(quota, "library_state", _library_state)
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(True))
    pl._reset_paid_cache()
    yield state
    pl._reset_paid_cache()


def test_resolve_policy_flag_off_makes_no_lookup(monkeypatch, paid_lookup):
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(False))
    assert asyncio.run(pl.resolve_lock_policy(object(), "u-1")) is None
    assert paid_lookup["calls"] == 0


def test_resolve_policy_free_paid_and_failure(paid_lookup):
    assert asyncio.run(pl.resolve_lock_policy(object(), "u-1")) == POLICY
    paid_lookup["paid"] = True
    pl._reset_paid_cache()
    assert asyncio.run(pl.resolve_lock_policy(object(), "u-1")) is None
    # A failed lookup is treated as free (locked) — and is not cached.
    paid_lookup["fail"] = True
    pl._reset_paid_cache()
    assert asyncio.run(pl.resolve_lock_policy(object(), "u-1")) == POLICY
    calls = paid_lookup["calls"]
    asyncio.run(pl.resolve_lock_policy(object(), "u-1"))
    assert paid_lookup["calls"] == calls + 1


def test_upgrade_unlocks_on_the_next_read_after_the_short_free_ttl(monkeypatch, paid_lookup):
    clock = {"t": 1000.0}
    monkeypatch.setattr(pl.time, "monotonic", lambda: clock["t"])
    assert asyncio.run(pl.user_is_paid(object(), "u-1")) is False
    paid_lookup["paid"] = True                       # the user just upgraded
    clock["t"] += pl._FREE_TTL_S - 1
    assert asyncio.run(pl.user_is_paid(object(), "u-1")) is False   # cached free
    clock["t"] += 2
    assert asyncio.run(pl.user_is_paid(object(), "u-1")) is True    # unlocked
    assert pl._FREE_TTL_S <= 15 < pl._PAID_TTL_S


# ===========================================================================
# 3. Backend surfaces (API, in-memory Supabase)
# ===========================================================================

USER = "aaaaaaaa-0000-4000-8000-00000000000a"
CONV = "11111111-1111-4111-8111-111111111111"
ITEM = "22222222-2222-4222-8222-222222222222"
OPEN_ITEM = "33333333-3333-4333-8333-333333333333"
NOTE = "44444444-4444-4444-4444-444444444444"


class _Res:
    def __init__(self, data: Any, count: int | None = None) -> None:
        self.data = data
        self.count = count


class _Q:
    def __init__(self, sb: "_SB", table: str) -> None:
        self.sb, self.table, self.filters = sb, table, []
        self.single = False
        self.update_payload: dict | None = None
        self.insert_payload: Any = None

    def select(self, *_a, **_k):
        return self

    def eq(self, col, val):
        self.filters.append(lambda r, c=col, v=val: r.get(c) == v)
        return self

    def in_(self, col, vals):
        wanted = set(vals)
        self.filters.append(lambda r, c=col: r.get(c) in wanted)
        return self

    def is_(self, col, val):
        if val == "null":
            self.filters.append(lambda r, c=col: r.get(c) is None)
        return self

    def order(self, *_a, **_k):
        return self

    def range(self, *_a, **_k):
        return self

    def limit(self, *_a, **_k):
        return self

    def maybe_single(self):
        self.single = True
        return self

    def update(self, payload):
        self.update_payload = payload
        return self

    def insert(self, payload, **_k):
        self.insert_payload = payload
        return self

    def execute(self):
        rows = self.sb.tables.setdefault(self.table, [])
        if self.insert_payload is not None:
            self.sb.inserts.append((self.table, self.insert_payload))
            return _Res(self.insert_payload)
        hit = [r for r in rows if all(f(r) for f in self.filters)]
        if self.update_payload is not None:
            self.sb.updates.append((self.table, dict(self.update_payload)))
            for r in hit:
                r.update(self.update_payload)
            return _Res([dict(r) for r in hit])
        if self.single:
            return _Res(dict(hit[0]) if hit else None)
        return _Res([dict(r) for r in hit], count=len(hit))


class _SB:
    def __init__(self, tables: dict) -> None:
        self.tables = tables
        self.updates: list = []
        self.inserts: list = []

    def table(self, name):
        return _Q(self, name)


def _ws_rows() -> list[dict]:
    base = {
        "user_id": USER, "conversation_id": CONV, "case_id": None, "message_id": None,
        "wi_seq": 1, "agent_family": "deep_search", "storage_path": None,
        "document_id": None, "is_visible": True, "feedback": None, "deleted_at": None,
        "updated_at": AFTER, "locked_by_agent_until": None,
    }
    return [
        {**base, "item_id": ITEM, "kind": "agent_writing", "created_by": "agent",
         "title": "مذكرة", "created_at": AFTER, "created_while_paid": False,
         "content_md": _search_with_table(),
         "metadata": {"subtype": "memo", "notes": ["ملاحظة الكاتب"],
                      "citations_used": [{"n": 9, "wi": "WI-1"}],
                      "references": [{"n": 1, "domain": "regulations", "ref_id": "a",
                                      "source_n": 1, "source_wi": "WI-1"},
                                     {"n": 30, "domain": "regulations", "ref_id": "b",
                                      "source_n": 2, "source_wi": "WI-1"}]}},
        {**base, "item_id": OPEN_ITEM, "wi_seq": 2, "kind": "agent_search",
         "created_by": "agent", "title": "بحث قديم", "created_at": BEFORE,
         "created_while_paid": False, "content_md": _search_with_lists(),
         "metadata": {"subtype": "legal_synthesis"}},
        {**base, "item_id": NOTE, "wi_seq": 3, "kind": "note", "created_by": "user",
         "title": "ملاحظة", "created_at": AFTER, "created_while_paid": False,
         "content_md": "ملاحظتي", "metadata": {}},
    ]


def _refs(visible_ns: set[int], hidden_ns: set[int]) -> list[dict]:
    return [
        {"wi_id": ITEM, "item_id": None, "ref_id": f"reg:{n}", "domain": "regulations",
         "n": n, "used": True}
        for n in sorted(visible_ns | hidden_ns)
    ]


@pytest.fixture
def api(monkeypatch, paid_lookup):
    """(client, sb, cut) with the flag ON and the caller on the free plan."""
    from fastapi.testclient import TestClient

    from backend.app.api import blog as blog_api
    from backend.app.api import workspace as workspace_api
    from backend.app.deps import get_current_user, get_supabase
    from backend.app.main import create_app
    from backend.app.services import case_service, workspace_service

    rows = _ws_rows()
    cut = preview_cut(rows[0]["content_md"])
    visible = cited_numbers(cut.visible)
    all_ns = cited_numbers(rows[0]["content_md"])
    sb = _SB({
        "workspace_items": rows,
        "workspace_item_references": _refs(visible, all_ns - visible),
    })

    class _AuthUser:
        auth_id = "auth-test"

    app = create_app()
    app.dependency_overrides[get_current_user] = lambda: _AuthUser()
    app.dependency_overrides[get_supabase] = lambda: sb
    for mod in (workspace_service, workspace_api, blog_api, case_service):
        if hasattr(mod, "get_user_id"):
            monkeypatch.setattr(mod, "get_user_id", lambda _sb, _a: USER)

    async def _payload(_sb, wi_id, used_only=False):
        return [
            {"n": r["n"], "ref_id": r["ref_id"], "domain": r["domain"], "title": f"مرجع {r['n']}"}
            for r in sb.tables["workspace_item_references"] if r["wi_id"] == wi_id
        ]

    monkeypatch.setattr(workspace_api, "fetch_item_references_payload", _payload)
    client = TestClient(app)
    return client, sb, cut, visible, all_ns - visible


def test_list_ships_the_preview_and_leaves_old_and_user_items_whole(api):
    client, _sb, cut, _visible, hidden = api
    res = client.get(f"/api/v1/conversations/{CONV}/workspace")
    assert res.status_code == 200, res.text
    items = {i["item_id"]: i for i in res.json()["items"]}

    locked = items[ITEM]
    assert locked["content_md"] == cut.visible + LEGACY_WALL_SUFFIX
    assert locked["preview"] == {
        "locked": True, "shown_chars": cut.shown_chars,
        "total_chars": cut.total_chars, "hidden_ref_count": len(hidden),
    }
    # Writer metadata that could carry the hidden body is gone / filtered.
    assert "notes" not in locked["metadata"] and "citations_used" not in locked["metadata"]
    assert [r["n"] for r in locked["metadata"]["references"]] == [1]

    assert "preview" not in items[OPEN_ITEM]               # created before FROM
    assert items[OPEN_ITEM]["content_md"] == _search_with_lists()
    assert "preview" not in items[NOTE]                    # the user's own note
    assert items[NOTE]["content_md"] == "ملاحظتي"


def test_get_ships_the_same_preview(api):
    client, *_ = api
    one = client.get(f"/api/v1/workspace/{ITEM}").json()
    listed = {i["item_id"]: i for i in client.get(
        f"/api/v1/conversations/{CONV}/workspace").json()["items"]}[ITEM]
    assert one["content_md"] == listed["content_md"]
    assert one["preview"] == listed["preview"]


def test_paid_user_gets_everything(api, paid_lookup):
    client, *_ = api
    paid_lookup["paid"] = True
    pl._reset_paid_cache()
    item = client.get(f"/api/v1/workspace/{ITEM}").json()
    assert "preview" not in item
    assert item["content_md"] == _search_with_table()


def test_item_made_while_paid_stays_open(api):
    client, sb, *_ = api
    sb.tables["workspace_items"][0]["created_while_paid"] = True
    item = client.get(f"/api/v1/workspace/{ITEM}").json()
    assert "preview" not in item


def test_flag_off_is_byte_identical(api, monkeypatch, paid_lookup):
    client, *_ = api
    monkeypatch.setattr(pl, "get_settings", lambda: _settings(False))
    item = client.get(f"/api/v1/workspace/{ITEM}").json()
    assert "preview" not in item
    assert item["content_md"] == _search_with_table()
    assert item["metadata"]["notes"] == ["ملاحظة الكاتب"]
    refs = client.get(f"/api/v1/workspace/{ITEM}/references").json()
    assert set(refs) == {"references"}
    assert paid_lookup["calls"] == 0


def test_references_list_keeps_only_visible_citations(api):
    client, _sb, _cut, visible, hidden = api
    body = client.get(f"/api/v1/workspace/{ITEM}/references").json()
    assert {r["n"] for r in body["references"]} == visible
    assert body["hidden_count"] == len(hidden)
    assert hidden, "fixture must cite something in the hidden part"


def test_reveal_of_a_hidden_citation_is_a_404_and_charges_nothing(api, monkeypatch):
    client, _sb, _cut, _visible, hidden = api
    from backend.app.api import workspace as workspace_api

    async def _row(_sb, wi_id, n):
        return {"n": n, "ref_id": f"reg:{n}", "domain": "regulations", "item_id": None}

    async def _never(*_a, **_k):  # pragma: no cover — must not be reached
        raise AssertionError("resolve_ref reached for a hidden citation")

    monkeypatch.setattr(workspace_api, "fetch_reference_row", _row)
    monkeypatch.setattr(workspace_api, "resolve_ref", _never)
    res = client.get(f"/api/v1/workspace/{ITEM}/references/{max(hidden)}/source")
    assert res.status_code == 404
    assert "الجزء المقفل" in res.text


def test_patch_content_on_a_locked_item_is_402_and_writes_nothing(api):
    client, sb, cut, *_ = api
    before = sb.tables["workspace_items"][0]["content_md"]
    # The exact thing an old client would autosave: the truncated text.
    res = client.patch(
        f"/api/v1/workspace/{ITEM}", json={"content_md": cut.visible + LEGACY_WALL_SUFFIX},
    )
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "WI_PREVIEW_LOCKED"
    assert sb.updates == []
    assert sb.tables["workspace_items"][0]["content_md"] == before
    # …and the hidden part can never be overwritten by any body at all.
    assert client.patch(f"/api/v1/workspace/{ITEM}", json={"content_md": "x"}).status_code == 402
    assert sb.tables["workspace_items"][0]["content_md"] == before


def test_patch_title_only_still_works_and_answers_with_the_preview(api):
    client, sb, cut, *_ = api
    res = client.patch(f"/api/v1/workspace/{ITEM}", json={"title": "عنوان جديد"})
    assert res.status_code == 200, res.text
    assert sb.updates and "content_md" not in sb.updates[-1][1]
    assert res.json()["content_md"] == cut.visible + LEGACY_WALL_SUFFIX
    assert sb.tables["workspace_items"][0]["content_md"] == _search_with_table()


def test_patch_content_after_upgrade_goes_through(api, paid_lookup):
    client, sb, *_ = api
    paid_lookup["paid"] = True
    pl._reset_paid_cache()
    res = client.patch(f"/api/v1/workspace/{ITEM}", json={"content_md": "نص كامل جديد"})
    assert res.status_code == 200, res.text
    assert sb.tables["workspace_items"][0]["content_md"] == "نص كامل جديد"


def test_share_of_a_locked_item_is_402(api):
    client, sb, *_ = api
    res = client.post(
        f"/api/v1/workspace/{ITEM}/share", json={"display_mode": "title", "title": "مدونة"},
    )
    assert res.status_code == 402
    assert res.json()["error"]["code"] == "WI_PREVIEW_LOCKED"
    assert not [t for t, _p in sb.inserts if t == "blog_posts"]


def test_visibility_and_feedback_answer_with_the_preview(api):
    client, _sb, cut, *_ = api
    for path, body in (("visibility", {"is_visible": False}), ("feedback", {"feedback": "up"})):
        res = client.patch(f"/api/v1/workspace/{ITEM}/{path}", json=body)
        assert res.status_code == 200, res.text
        assert res.json()["content_md"] == cut.visible + LEGACY_WALL_SUFFIX


def test_wi_preview_served_is_emitted_once_per_item(api, monkeypatch):
    from backend.app.services import analytics_service, wi_preview_service

    wi_preview_service._served_local.clear()
    sent: list = []
    monkeypatch.setattr(analytics_service, "insert_events", lambda _sb, rows: sent.extend(rows) or len(rows))
    client, *_ = api
    client.get(f"/api/v1/conversations/{CONV}/workspace", headers={"X-App-Platform": "ios"})
    client.get(f"/api/v1/workspace/{ITEM}")
    # Fire-and-forget on the app's loop: give the task a moment to land.
    import time

    deadline = time.monotonic() + 3
    while not sent and time.monotonic() < deadline:
        time.sleep(0.02)
    time.sleep(0.1)
    served = [r for r in sent if r["event_name"] == "wi_preview_served"]
    assert len(served) == 1
    assert served[0]["props"]["item_id"] == ITEM
    assert served[0]["props"]["platform"] == "ios"
    assert served[0]["user_id"] == USER and served[0]["session_key"] == "server"


def test_server_event_is_not_accepted_from_the_beacon():
    from backend.app.services import analytics_service as a

    assert "wi_preview_served" in a.SERVER_EVENT_NAMES
    assert "wi_preview_served" not in a.EVENT_NAMES
    assert "wi_preview" in a.GATE_KINDS
    rows = a.build_event_rows(
        [{"event_name": "wi_preview_served", "session_key": "s"}],
        client=a.ClientBuckets(None, None, None), user_id=None, user_type="anon",
    )
    assert rows == []
    with pytest.raises(ValueError):
        a.build_server_event_row("nope", user_id=None, props={})


def test_backend_and_router_previews_are_byte_identical(api):
    """The UI and the router unfold cut the same bytes (one shared function)."""
    from agents.tool_repository.unfold_workspace_item import unfold_parts

    client, sb, cut, *_ = api
    ui = client.get(f"/api/v1/workspace/{ITEM}").json()["content_md"]
    parts = unfold_parts(sb, ITEM, USER, lock_policy=POLICY)
    assert parts is not None and parts.lock_header
    assert ui == cut.visible + LEGACY_WALL_SUFFIX
    assert parts.body.startswith(cut.visible.strip())
