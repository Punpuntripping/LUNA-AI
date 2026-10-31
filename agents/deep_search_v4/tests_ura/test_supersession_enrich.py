"""``ura/enrich`` — a regulation's standing, from the row and the supersession map.

Pins three things: a law with no pair keeps today's line, a row in transition
carries no ``<status>`` so the static repeal rule can't fire on it, and a DB miss
never turns into a claim about a law's validity. Law names are invented.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date

import pytest

from agents.deep_search_v4.ura import enrich
from agents.deep_search_v4.ura.schema import RegURAResult, UnifiedRetrievalArtifact
from shared.library.reg_status import REPEALED_LABEL_AR
from shared.library.supersession import (
    PERIOD_1,
    Pair,
    Successor,
    SupersessionMap,
)

_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_OLD_ID = "22222222-2222-2222-2222-222222222222"
_NEW_ID = "33333333-3333-3333-3333-333333333333"

_PERIOD_1_DAY = date(2031, 3, 15)
_AFTER_DAY = date(2033, 1, 1)

_LAWS = {
    "nizam": ("نظام السجل البيئي (القديم)", "نظام السجل البيئي"),
    "laiha": (
        "اللائحة التنفيذية لنظام السجل البيئي (القديمة)",
        "اللائحة التنفيذية لنظام السجل البيئي",
    ),
}


@pytest.fixture(params=_LAWS, ids=str)
def law(request) -> tuple[str, str]:
    return _LAWS[request.param]


def _supersessions(old_title: str, new_title: str) -> SupersessionMap:
    return SupersessionMap([
        Pair(
            old_id=_OLD_ID,
            old_ref="ref-old",
            old_title=old_title,
            superseded_on=date(2031, 3, 1),
            period_1_end=date(2031, 6, 1),
            period_2_end=date(2032, 3, 1),
            successors=(
                Successor(
                    title=new_title, reg_ref="ref-new", reg_id=_NEW_ID, note="نص الأحكام"
                ),
            ),
        )
    ])


def _shell(chunk_id: str = _CHUNK_ID) -> RegURAResult:
    return RegURAResult(ref_id=f"reg:{chunk_id}", source_type="reg_chunk", relevance="high")


def _enrich(
    monkeypatch,
    *,
    reg: dict | None,
    supersessions: SupersessionMap,
    today: date = _PERIOD_1_DAY,
    regulation_id: str = _OLD_ID,
) -> RegURAResult:
    """Run the enrichment over one chunk; ``reg=None`` is a failed regulation read."""
    monkeypatch.setattr(
        enrich, "_fetch_chunks",
        lambda *a, **kw: {
            _CHUNK_ID: {
                "id": _CHUNK_ID,
                "regulation_id": regulation_id,
                "content": "نص المادة",
                "context": "",
                "owns": {},
            }
        },
    )
    monkeypatch.setattr(
        enrich, "_fetch_regulations",
        lambda *a, **kw: {} if reg is None else {regulation_id: reg},
    )
    monkeypatch.setattr(enrich, "_fetch_cross_refs", lambda *a, **kw: {})
    monkeypatch.setattr(enrich, "load_supersessions", lambda _s: supersessions)
    monkeypatch.setattr(enrich, "riyadh_today", lambda: today)
    shell = _shell()
    asyncio.run(enrich._enrich_regulations([shell], supabase=object()))
    return shell


def _reg_row(reg_id: str, title: str, *, repealed: bool = False) -> dict:
    return {
        "id": reg_id,
        "clean_title": title,
        "scope": "",
        "landing_url": "",
        "pdf_url": "",
        "doc_type_raw": "",
        "status_class": "cancelled" if repealed else "in_force",
        "status_raw": "لاغي" if repealed else "ساري",
    }


@pytest.mark.parametrize("title", ["نظام الموانئ الجافة", "لائحة الموانئ الجافة"])
def test_no_pair_law_carries_nothing_new(monkeypatch, law, title: str):
    shell = _enrich(
        monkeypatch, reg=_reg_row(_NEW_ID + "x", title), supersessions=_supersessions(*law),
    )

    assert shell.reg_status == ""
    assert shell.reg_version == ""
    assert shell.reg_transitions == []
    item = shell.for_aggregator(1)
    assert item.reg_version == ""
    assert item.reg_transitions == []


@pytest.mark.parametrize("title", ["نظام الموانئ الجافة", "لائحة الموانئ الجافة"])
def test_no_pair_repealed_law_keeps_todays_line(monkeypatch, law, title: str):
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_NEW_ID + "x", title, repealed=True),
        supersessions=_supersessions(*law),
    )

    assert shell.reg_status == f"{REPEALED_LABEL_AR} (النص الأصلي: لاغي)"
    assert shell.reg_version == ""


def test_old_row_in_transition_has_no_status_however_it_is_flagged(monkeypatch, law):
    old_title, new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_OLD_ID, old_title, repealed=True),
        supersessions=_supersessions(old_title, new_title),
    )

    assert shell.reg_title == old_title
    assert shell.reg_status == ""
    assert shell.reg_version == "old"
    [transition] = shell.reg_transitions
    assert (transition.phase, transition.old_title, transition.new_title) == (
        PERIOD_1, old_title, new_title,
    )
    assert transition.note == "نص الأحكام"


def test_new_row_in_transition_is_labelled_new(monkeypatch, law):
    old_title, new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_NEW_ID, new_title),
        supersessions=_supersessions(old_title, new_title),
        regulation_id=_NEW_ID,
    )

    assert shell.reg_status == ""
    assert shell.reg_version == "new"
    assert [t.phase for t in shell.reg_transitions] == [PERIOD_1]


def test_both_fields_reach_the_aggregator_view(monkeypatch, law):
    old_title, new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_OLD_ID, old_title, repealed=True),
        supersessions=_supersessions(old_title, new_title),
    )

    item = shell.for_aggregator(3)

    assert item.reg_version == "old"
    assert item.reg_transitions == shell.reg_transitions


def test_a_finished_transition_is_a_plain_repeal_naming_the_successor(monkeypatch, law):
    old_title, new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_OLD_ID, old_title, repealed=True),
        supersessions=_supersessions(old_title, new_title),
        today=_AFTER_DAY,
    )

    assert shell.reg_status == f"ملغي — حلّ محله {new_title}"
    assert shell.reg_version == ""
    assert shell.reg_transitions == []


def test_a_failed_regulation_read_claims_nothing(monkeypatch, law):
    """The chunk's regulation IS in the map, but the row never arrived."""
    shell = _enrich(monkeypatch, reg=None, supersessions=_supersessions(*law))

    assert shell.reg_status == ""
    assert shell.reg_version == ""
    assert shell.reg_transitions == []


def test_a_missing_chunk_claims_nothing(monkeypatch, law):
    monkeypatch.setattr(enrich, "_fetch_chunks", lambda *a, **kw: {})
    monkeypatch.setattr(enrich, "_fetch_regulations", lambda *a, **kw: {})
    monkeypatch.setattr(enrich, "_fetch_cross_refs", lambda *a, **kw: {})
    monkeypatch.setattr(enrich, "load_supersessions", lambda _s: _supersessions(*law))
    shell = _shell()

    asyncio.run(enrich._enrich_regulations([shell], supabase=object()))

    assert (shell.reg_status, shell.reg_version, shell.reg_transitions) == ("", "", [])


def test_a_failed_map_load_falls_back_to_todays_line(monkeypatch, law):
    """Fail open: exactly what the old row showed before this feature."""
    old_title, _new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_OLD_ID, old_title, repealed=True),
        supersessions=SupersessionMap(),
    )

    assert shell.reg_status == f"{REPEALED_LABEL_AR} (النص الأصلي: لاغي)"
    assert shell.reg_version == ""


def test_the_stored_artifact_carries_the_version_but_never_the_note(monkeypatch, law):
    """``save_retrieval_artifact`` writes ``ura.model_dump()`` to JSONB, and the
    blog ``generation_context`` trim ladder only knows body fields. A note on every
    result would survive it while the statute text around it was cut."""
    old_title, new_title = law
    shell = _enrich(
        monkeypatch,
        reg=_reg_row(_OLD_ID, old_title, repealed=True),
        supersessions=_supersessions(old_title, new_title),
    )

    stored = json.dumps(
        UnifiedRetrievalArtifact(high_results=[shell]).model_dump(), ensure_ascii=False
    )

    assert '"reg_version": "old"' in stored
    assert "reg_transitions" not in stored
    assert "نص الأحكام" not in stored
    # ... while the live object still feeds this turn's block.
    assert shell.for_aggregator(1).reg_transitions == shell.reg_transitions
    assert shell.reg_transitions


def test_the_map_is_read_once_however_many_chunks(monkeypatch, law):
    other_chunk = "44444444-4444-4444-4444-444444444444"
    reg = _reg_row(_NEW_ID + "x", "نظام الموانئ الجافة")
    chunk = {"regulation_id": reg["id"], "content": "نص", "context": "", "owns": {}}
    loads: list[object] = []

    def _load(supabase):
        loads.append(supabase)
        return _supersessions(*law)

    monkeypatch.setattr(
        enrich, "_fetch_chunks",
        lambda *a, **kw: {_CHUNK_ID: {**chunk, "id": _CHUNK_ID}, other_chunk: {**chunk, "id": other_chunk}},
    )
    monkeypatch.setattr(enrich, "_fetch_regulations", lambda *a, **kw: {reg["id"]: reg})
    monkeypatch.setattr(enrich, "_fetch_cross_refs", lambda *a, **kw: {})
    monkeypatch.setattr(enrich, "load_supersessions", _load)

    asyncio.run(
        enrich._enrich_regulations([_shell(), _shell(other_chunk)], supabase=object())
    )

    assert len(loads) == 1
