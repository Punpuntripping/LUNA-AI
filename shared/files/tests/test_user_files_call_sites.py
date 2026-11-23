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
    "backend/app/services/attachment_cleanup.py": "S3 retention purge (its scan stays until contract)",
    "backend/app/services/files_service.py": "«الملفات» — the user_files path behind the read switch",
    "backend/app/services/message_service.py": "S2 _attachment_page_counts (the rest lists pointers)",
    "backend/app/services/upload_reconciler.py": "S3 promote / soft-delete (its scan stays until contract)",
    "backend/app/services/workspace_service.py": "S2 list / get; S3 finalize / cancel / legacy promote",
    "backend/app/services/writer_planner_context.py": "S2 load_writer_planner_context",
    "scripts/angle_map_preview.py": "S2 _load_rows (dev tool; _by_prefix / _conversation_ids list pointers)",
    "scripts/my_files_type_backfill.py": "S3 apply; its label read is a one-off dev tool (A6)",
    "shared/files/flags.py": "the schema probe",
    "shared/files/store.py": "the module itself",
}

#: Files that query the table but never read an attachment's content.
NO_ATTACHMENT_CONTENT: dict[str, str] = {
    "agents/artifact_editor/agent.py": "never an attachment",
    "agents/base/context.py": "never an attachment",
    "agents/memory/artifact_analyzer/context.py": "never an attachment",
    "agents/memory/convo_memory_writer/runner.py": "lists pointers (titles)",
    "agents/router/router.py": "lists pointers (list_workspace_items tool)",
    "agents/tool_repository/edit_artifact.py": "never an attachment",
    "agents/tool_repository/edit_supabase_md.py": "never an attachment",
    "agents/tool_repository/save_memo.py": "never an attachment",
    "agents/utils/reading_corrections.py": "never an attachment",
    "agents/writer/lock.py": "never an attachment",
    "agents/writer/publisher.py": "lists pointers (deleted_at by item)",
    "backend/app/api/deepsearch_api/service.py": "never an attachment",
    "backend/app/services/blog_service.py": "never an attachment",
    "backend/app/services/context_service.py": "never an attachment",
    "backend/app/services/library_item_service.py": "never an attachment",
    "backend/app/services/memory_md_service.py": "never an attachment",
    "backend/app/services/summary_sweeper.py": "contract (Phase 4): a second scan on user_files",
    "backend/app/services/workspace_context.py": "reads attachments but has no live caller (A2)",
    "scripts/backfill_compaction_summaries.py": "dev tool (A6) — update at contract",
    "scripts/build_judgment_slugs.py": "deep-search items only",
    "scripts/case_citation_audit.py": "deep-search items only",
    "scripts/e2e_segmentation_review.py": "dev tool (A6) — update at contract",
    "scripts/eval_artifact_analyzer.py": "agent_search items only",
    "scripts/segmentation_preview.py": "dev tool (A6) — update at contract",
    "scripts/user_files_parity.py": "parity tool: lists attachment ids (+ name/size/type for the match "
                                    "call) to drive the real loaders under both switches",
}

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
