"""``regulations_v2.doc_type_raw`` → reference-card type chip.

Reg reference cards used to be labelled with a blanket ``نظام`` regardless of
what the document actually is — the corpus holds 21 distinct types (لائحة,
تنظيم, دليل, مواصفة قياسية, لائحة فنية, …) and لائحة alone outnumbers نظام.
This pins the whole projection chain:

    regulations_v2.doc_type_raw
      -> RegURAResult.doc_type          (ura/enrich._enrich_regulations)
      -> ReferenceView.doc_type         (RegURAResult.for_reference)
      -> Reference.doc_type             (aggregator/preprocessor)
      -> ReferencePanel type chip       (frontend, falls back to نظام on "")

The corpus sentinel ``غير محدد`` must normalise to ``""`` so the UI shows its
generic label rather than printing "unspecified" on a card.
"""
from __future__ import annotations

import pytest

from agents.deep_search_v4.aggregator.preprocessor import _reference_from_ura
from agents.deep_search_v4.ura import enrich
from agents.deep_search_v4.ura.schema import RegURAResult

_CHUNK_ID = "11111111-1111-1111-1111-111111111111"
_REG_ID = "22222222-2222-2222-2222-222222222222"


def test_doc_type_label_normalises_sentinel_and_whitespace():
    assert enrich._doc_type_label("لائحة") == "لائحة"
    assert enrich._doc_type_label("  لائحة تنفيذية  ") == "لائحة تنفيذية"
    # Corpus sentinel + genuinely absent values both read as "no type".
    assert enrich._doc_type_label(enrich._DOC_TYPE_UNSPECIFIED) == ""
    assert enrich._doc_type_label(None) == ""
    assert enrich._doc_type_label("") == ""


def _patch_reg_fetches(monkeypatch: pytest.MonkeyPatch, doc_type_raw) -> None:
    """Stub the three batched reads ``_enrich_regulations`` makes."""
    monkeypatch.setattr(
        enrich, "_fetch_chunks",
        lambda *a, **kw: {
            _CHUNK_ID: {
                "id": _CHUNK_ID,
                "regulation_id": _REG_ID,
                "content": "نص المادة",
                "context": "",
                "owns": {},
            }
        },
    )
    monkeypatch.setattr(
        enrich, "_fetch_regulations",
        lambda *a, **kw: {
            _REG_ID: {
                "id": _REG_ID,
                "clean_title": "لائحة العمل",
                "scope": "",
                "landing_url": "https://example.test/reg",
                "pdf_url": "",
                "doc_type_raw": doc_type_raw,
            }
        },
    )
    monkeypatch.setattr(enrich, "_fetch_cross_refs", lambda *a, **kw: {})


@pytest.mark.asyncio
async def test_doc_type_reaches_the_reference(monkeypatch: pytest.MonkeyPatch):
    """A typed regulation labels its card with its own type, not نظام."""
    _patch_reg_fetches(monkeypatch, "لائحة تنفيذية")
    shell = RegURAResult(
        ref_id=f"reg:{_CHUNK_ID}", source_type="reg_chunk", relevance="high",
    )

    await enrich._enrich_regulations([shell], supabase=object())

    assert shell.doc_type == "لائحة تنفيذية"
    assert shell.for_reference().doc_type == "لائحة تنفيذية"
    assert _reference_from_ura(1, shell).doc_type == "لائحة تنفيذية"


@pytest.mark.asyncio
async def test_unspecified_doc_type_leaves_the_reference_blank(
    monkeypatch: pytest.MonkeyPatch,
):
    """``غير محدد`` must not reach the card — the panel falls back to نظام."""
    _patch_reg_fetches(monkeypatch, enrich._DOC_TYPE_UNSPECIFIED)
    shell = RegURAResult(
        ref_id=f"reg:{_CHUNK_ID}", source_type="reg_chunk", relevance="high",
    )

    await enrich._enrich_regulations([shell], supabase=object())

    assert shell.doc_type == ""
    assert _reference_from_ura(1, shell).doc_type == ""


@pytest.mark.asyncio
async def test_doc_type_stays_out_of_the_aggregator_view(
    monkeypatch: pytest.MonkeyPatch,
):
    """Display-only: the synthesis prompt surface must not gain a field, or the
    prompt-cache prefix shifts for every reg reference."""
    _patch_reg_fetches(monkeypatch, "دليل")
    shell = RegURAResult(
        ref_id=f"reg:{_CHUNK_ID}", source_type="reg_chunk", relevance="high",
    )

    await enrich._enrich_regulations([shell], supabase=object())

    assert "doc_type" not in shell.for_aggregator(1).model_dump()
