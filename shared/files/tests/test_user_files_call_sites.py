"""Pinned: every production file that queries ``workspace_items`` (my_files_phase2.md §A, §F).

A new call site fails this test on purpose. Answer one question before adding it
here: does it read an ATTACHMENT's content (title, text, summary, type,
metadata, storage path)? If yes, it must go through ``shared.files.store``
(``with_file_id`` + ``overlay_files``, or ``content_chars_for_items``) — see
§C4 — and it belongs under ``READS_THROUGH_FILES``. If it only lists pointers or
never touches an attachment, add it under ``NO_ATTACHMENT_CONTENT`` with the
reason.
"""
from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parents[3]

#: Files that read (or write) attachment content — each must use shared.files.
READS_THROUGH_FILES: dict[str, str] = {
    "agents/attachment_agent/context.py": "S2 load_attachment_rows (with_file_id + overlay_files); lengths, "
                                          "maps and pages through the segmenter's files-aware loaders",
    "agents/memory/agent.py": "S2 _load_compaction_context, resummarize_dirty_items",
    "agents/memory/angle_mapper/group.py": "S2 item_titles (titles of the attachments a map leaves out); "
                                           "pointer_rows reads pointer columns only; lengths, maps and text "
                                           "through the segmenter's files-aware loaders",
    "agents/memory/attachment_segmenter/bundles.py": "S2 message_members, _item_row",
    "agents/memory/attachment_segmenter/runner.py": "S2 _load_item, _load_items",
    "agents/memory/attachment_segmenter/store.py": "S2 _content_lengths (content_chars)",
    "agents/memory/item_analyzer/runner.py": "S2 _load_workspace_items",
    "agents/memory/ocr_extractor/runner.py": "S2 candidates / has_unread; S3 status + text",
    "agents/memory/summarize.py": "S2 fetch; S3 attempt marker + attachment summary",
    "agents/memory/template_ingester/runner.py": "S2 _load_content_md",
    "agents/orchestrator.py": "S2 _load_attached_items (the other reads list pointers / non-attachments)",
    "agents/router/context.py": "S2 _load_workspace_item_summaries",
    "agents/tool_repository/unfold_workspace_item.py": "S2 _fetch_item",
    "agents/writer_planner/walkers.py": "S2 _fetch_workspace_items_sync",
    "backend/app/services/angle_maps_service.py": "S2 _item_rows (titles / types of a map's documents, "
                                                  "with_file_id + overlay_files); lengths via content_chars_for_items; "
                                                  "section text through the segmenter's files-aware loader",
    "backend/app/services/attachment_cleanup.py": "S3 retention purge (its scan stays until contract)",
    "backend/app/services/file_refs_service.py": "_load_items (a cited file's title / text, with_file_id + "
                                                 "overlay_files); section rows through the segmenter's loader",
    "backend/app/services/files_service.py": "«الملفات» — the user_files path behind the read switch",
    "backend/app/services/message_service.py": "S2 _attachment_page_counts (the rest lists pointers)",
    "backend/app/services/upload_reconciler.py": "S3 promote / soft-delete (its scan stays until contract)",
    "backend/app/services/workspace_service.py": "S2 list / get; S3 finalize / cancel / legacy promote",
    "backend/app/services/writer_planner_context.py": "S2 load_writer_planner_context",
    "scripts/angle_map_preview.py": "S2 _load_rows (dev tool; _by_prefix / _conversation_ids list pointers)",
    "scripts/my_files_type_backfill.py": "S3 apply; its label read is a one-off dev tool (A6)",
    "shared/files/flags.py": "the schema probe",
    "shared/files/store.py": "the module itself",
    "shared/attachments/derived.py": "S3 the derived-wording fix (attachment_agent.md §3.3): the file's title / "
                                     "summary through load_files + patch_file; its items' own copies by item",
}

#: Files that query the table but never read an attachment's content.
NO_ATTACHMENT_CONTENT: dict[str, str] = {
    "agents/artifact_editor/agent.py": "never an attachment",
    "agents/base/context.py": "never an attachment",
    "agents/memory/artifact_analyzer/context.py": "never an attachment",
    "agents/memory/angle_mapper/reanalysis.py": "pointers only: the other items of a file (item_id, file_id)",
    "agents/deep_search_v4/extended/offer.py": "pointers only: the conversation's attachment item_ids (one planner)",
    "agents/memory/convo_memory_writer/runner.py": "lists pointers (titles)",
    "agents/router/router.py": "lists pointers (list_workspace_items tool)",
    "agents/tool_repository/completion_search_tool.py": "the pinned_texts card only (its entries)",
    "agents/tool_repository/edit_artifact.py": "never an attachment",
    "agents/tool_repository/edit_supabase_md.py": "never an attachment",
    "agents/tool_repository/save_memo.py": "never an attachment",
    "agents/utils/reading_corrections.py": "never an attachment",
    "agents/writer/lock.py": "never an attachment",
    "agents/writer/publisher.py": "lists pointers (deleted_at by item)",
    "agents/writer_planner/runner.py": "the pinned_texts card's id and WI number only",
    "backend/app/api/deepsearch_api/service.py": "never an attachment",
    "backend/app/services/blog_service.py": "never an attachment",
    "backend/app/services/context_service.py": "never an attachment",
    "backend/app/services/library_item_service.py": "never an attachment",
    "backend/app/services/memory_md_service.py": "never an attachment",
    "backend/app/services/summary_sweeper.py": "contract (Phase 4): a second scan on user_files",
    "backend/app/services/workspace_context.py": "reads attachments but has no live caller (A2)",
    "shared/workspace/pinned_texts.py": "reads and writes the pinned_texts card only",
    "scripts/backfill_compaction_summaries.py": "dev tool (A6) — update at contract",
    "scripts/build_judgment_slugs.py": "deep-search items only",
    "scripts/citation_pins_preview.py": "dev tool: item → file id only; the text is read from user_files",
    "scripts/case_citation_audit.py": "deep-search items only",
    "scripts/e2e_segmentation_review.py": "dev tool (A6) — update at contract",
    "scripts/eval_artifact_analyzer.py": "agent_search items only",
    "scripts/segmentation_preview.py": "dev tool (A6) — update at contract",
    "scripts/user_files_parity.py": "parity tool: lists attachment ids (+ name/size/type for the match "
                                    "call) to drive the real loaders under both switches",
}

#: User text corrections (attachment_agent.md §3.3, D12/D15) are an OVERLAY: content_md is
#: never rewritten (its length drives segment-map / angle-map freshness), so every
#: reader above decides. A reader that hands an attachment's TEXT to a model or a
#: person applies the file's corrections …
TEXT_CORRECTIONS_APPLIED: dict[str, str] = {
    "agents/attachment_agent/context.py": "the agent's outline and read tools (its own package)",
    "agents/memory/attachment_segmenter/runner.py": "the model's message only (_for_model); the stored rows are "
                                                    "cut from the REAL text, content_chars stays real",
    "agents/memory/summarize.py": "the summary input; _source_length stays the REAL length",
    "agents/memory/template_ingester/runner.py": "_load_content_md, before the codec",
    "agents/orchestrator.py": "_load_attached_items (planner <attached_items>, case documents, simple search, "
                              "legacy writer context, extended fallback)",
    "agents/router/context.py": "the no-summary fallback, before its 1,500 / 6,000 cut",
    "agents/tool_repository/unfold_workspace_item.py": "unfold_parts (planner / writer planner), before the codec",
    "agents/writer_planner/walkers.py": "_fetch_workspace_items_sync — the writer's whole-text bodies",
    "backend/app/services/angle_maps_service.py": "_map_out — the AngleSectionDialog section text",
    "backend/app/services/file_refs_service.py": "_section_text — the file-ref popup text (a report's "
                                                 "[F1·S7] chip), after the positions cut it",
    "backend/app/services/files_service.py": "the opened file's text (viewer + Copy) and _sections "
                                             "(which also applies the section promotions)",
    "backend/app/services/workspace_service.py": "list routes; get_workspace_item(text_corrections=True) for "
                                                 "the GET route only (not the ownership gate)",
}

#: … or says why it must not (positions, lengths, freshness) or need not (no text).
TEXT_CORRECTIONS_NOT_APPLIED: dict[str, str] = {
    "agents/memory/agent.py": "compaction reads summaries only; resummarize_dirty_items compares the REAL "
                              "length with summary_source_length (a corrected length would loop)",
    "agents/memory/angle_mapper/group.py": "lengths, maps and paragraph fit on the REAL text; the mapper's "
                                           "prompt overlay lives in angle_mapper/runner.py",
    "agents/memory/attachment_segmenter/bundles.py": "membership and lengths only",
    "agents/memory/attachment_segmenter/store.py": "content_chars — map freshness",
    "agents/memory/item_analyzer/runner.py": "no live caller (removed from the pipeline 2026-06-08)",
    "agents/memory/ocr_extractor/runner.py": "writes the text the overlay sits on",
    "backend/app/services/attachment_cleanup.py": "retention purge, no text shown",
    "backend/app/services/message_service.py": "page counts only",
    "backend/app/services/upload_reconciler.py": "promote / soft-delete, no text shown",
    "backend/app/services/writer_planner_context.py": "summaries only, never content_md",
    "scripts/angle_map_preview.py": "dev tool",
    "scripts/my_files_type_backfill.py": "dev tool (labels)",
    "shared/files/flags.py": "the schema probe",
    "shared/files/store.py": "the loader itself — the overlay is the reader's choice",
    "shared/attachments/derived.py": "writes the derived wording (title / summary) at fix time — not a reader",
}

#: Section readers that never query workspace_items themselves but hand out an
#: attachment's text: they apply the overlay too.
SECTION_READERS_APPLYING: dict[str, str] = {
    "agents/utils/attachment_sections.py": "deep-search + writer sections, before _encode_sections",
    "agents/deep_search_v4/extended/view.py": "«بحث شامل» marked text, AFTER marking (offsets stay real)",
}

#: Code that works on POSITIONS or LENGTHS of the original text — it must never see
#: corrected text (citation pins, page offsets, freshness).
NEVER_CORRECTED: tuple[str, ...] = (
    "agents/memory/attachment_segmenter/store.py",
    "agents/memory/attachment_segmenter/bundles.py",
    "agents/memory/agent.py",
    "agents/memory/citation_pins",
)

_OVERLAY_IMPORT = re.compile(r"from shared\.attachments(?:\.(?:overlay|text_corrections))? import")
_OVERLAY_CALL = re.compile(
    r"\b(?:correct_rows|correct_text|corrections_for_rows|corrections_for_items|corrections_for_item_ids|"
    r"load_text_corrections|apply_text_corrections|apply_to_sections)\("
)

_CALL = re.compile(r"""\.table\(\s*["']workspace_items["']\s*\)""")
_CONST = re.compile(r"""^\s*_?[A-Z_]*TABLE[A-Z_]*\s*(?::\s*\w+\s*)?=\s*["']workspace_items["']""", re.M)
_EMBED = re.compile(r"""["'][^"'\n]*\bworkspace_items(?:!\w+)?\(""")
_SKIP_PARTS = {"node_modules", ".venv", "__pycache__", "frontend", "mobile", ".git", "tests", "tests_ura", "eval"}


def _call_sites() -> set[str]:
    out: set[str] = set()
    for top in ("agents", "backend", "shared", "scripts"):
        for p in (REPO / top).rglob("*.py"):
            rel = p.relative_to(REPO)
            if _SKIP_PARTS & set(rel.parts) or p.name.startswith("test_"):
                continue
            text = p.read_text(encoding="utf-8", errors="ignore")
            if _CALL.search(text) or _CONST.search(text) or _EMBED.search(text):
                out.add(rel.as_posix())
    return out


def test_every_workspace_items_call_site_is_classified():
    found = _call_sites()
    known = set(READS_THROUGH_FILES) | set(NO_ATTACHMENT_CONTENT)
    new = sorted(found - known)
    assert not new, (
        "New workspace_items call site(s): " + ", ".join(new) + ". Does it read an attachment's "
        "content? If yes, read through shared.files.store (with_file_id + overlay_files, "
        "my_files_phase2.md §C4) and list it in READS_THROUGH_FILES; otherwise list it in "
        "NO_ATTACHMENT_CONTENT with the reason."
    )
    gone = sorted(known - found)
    assert not gone, "No longer query workspace_items — remove from the pins: " + ", ".join(gone)


def test_message_service_embed_is_the_pointer_list():
    """The one embed of workspace_items (chips: title, kind, metadata) lists pointers."""
    text = (REPO / "backend/app/services/message_service.py").read_text(encoding="utf-8")
    embeds = re.findall(r"workspace_items\(([^)]*)\)", text)
    assert embeds == ["title, kind, metadata"]


def test_readers_go_through_shared_files():
    for rel in READS_THROUGH_FILES:
        text = (REPO / rel).read_text(encoding="utf-8")
        if rel.startswith("shared/files/"):
            continue
        assert re.search(r"from shared\.files import (store|flags)", text), rel
        assert re.search(r"files_(store|flags)\.\w+\(", text), rel


def test_every_attachment_reader_decides_on_text_corrections():
    """attachment_agent.md §3.3: a new attachment reader must say whether the user's text
    fixes reach what it reads — and why not when they must not."""
    applied, skipped = set(TEXT_CORRECTIONS_APPLIED), set(TEXT_CORRECTIONS_NOT_APPLIED)
    assert not applied & skipped, sorted(applied & skipped)
    undecided = sorted(set(READS_THROUGH_FILES) - applied - skipped)
    assert not undecided, (
        "Attachment reader(s) with no text-correction decision: " + ", ".join(undecided) + ". If it "
        "hands the text to a model or a person, apply shared.attachments.overlay.correct_rows (or "
        "text_corrections.*) on the REAL text before any encoding and list it in TEXT_CORRECTIONS_APPLIED; "
        "otherwise list it in TEXT_CORRECTIONS_NOT_APPLIED with the reason."
    )
    assert not (applied | skipped) - set(READS_THROUGH_FILES), sorted((applied | skipped) - set(READS_THROUGH_FILES))


def test_readers_that_apply_text_corrections_call_the_overlay():
    for rel in {**TEXT_CORRECTIONS_APPLIED, **SECTION_READERS_APPLYING}:
        text = (REPO / rel).read_text(encoding="utf-8")
        assert _OVERLAY_IMPORT.search(text), rel
        assert _OVERLAY_CALL.search(text), rel


def test_position_and_length_code_never_reads_corrected_text():
    for rel in NEVER_CORRECTED:
        path = REPO / rel
        files = [path] if path.is_file() else [
            p for p in path.rglob("*.py") if "tests" not in p.relative_to(REPO).parts
        ]
        assert files, rel
        for p in files:
            text = p.read_text(encoding="utf-8")
            assert "shared.attachments.overlay" not in text and "shared.attachments.text_corrections" not in text, p
