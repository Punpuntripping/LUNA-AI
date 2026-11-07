"""monitor/render_ura prints the two new URA domains (official_principles_refs.md).

A principle used to print its header only — no rule, no decisions — so a
monitor dump could not show what the aggregator was handed. The Article 40 pin
(``articles``) had the same gap. Pure rendering, no I/O.
"""
from __future__ import annotations

from agents.deep_search_v4.monitor.render_ura import _render_result_block
from agents.deep_search_v4.ura.principles import principle_from_row
from agents.deep_search_v4.ura.schema import ArticleURAResult

P2_ID = "64052e1b-17d9-511d-8508-ed401e624d1a"
RULE = "الأصل براءة الذمة حتى يثبت ما يوجب الإدانة."


def test_a_principle_block_carries_its_rule_and_decisions() -> None:
    shell = principle_from_row({
        "id": P2_ID,
        "entity_ref": "18329",
        "principle_no": 2,
        "chapter_title": "الأصول والقواعد",
        "text": RULE,
        "citations": [{"body": "الهيئة القضائية العليا", "date_hijri": "13/10/1391",
                       "decision_no": "254"}],
        "issuing_body_names": ["الهيئة القضائية العليا"],
        "source": {"pages": [33], "source_url": "https://drive.google.com/file/d/x/view"},
    })
    md = _render_result_block(1, shell)
    assert md.startswith(f"### [1] principle:{P2_ID} -- principles -- official_principle")
    assert "- **principle_no**: 2" in md
    assert "- **chapter_title**: الأصول والقواعد" in md
    assert "- **entity_ref**: 18329" in md
    assert "- **pages**: [33]" in md
    assert "  - 254 | 13/10/1391 | الهيئة القضائية العليا" in md
    assert f"```\n{RULE}\n```" in md


def test_the_pinned_article_block_carries_its_body() -> None:
    art = ArticleURAResult(
        ref_id="article:6a1c7915-1473-54db-9fb6-3b6b9d4e61a9",
        source_type="article_full",
        relevance="medium",
        article_id="6a1c7915-1473-54db-9fb6-3b6b9d4e61a9",
        article_number="40",
        reg_title="نظام ما",
        content="نص المادة.",
    )
    md = _render_result_block(2, art)
    assert "- **article_number**: 40" in md
    assert "- **reg_title**: نظام ما" in md
    assert "```\nنص المادة.\n```" in md
