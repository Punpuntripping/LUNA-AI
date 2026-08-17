"""Router A/B re-run — SETUP: create the `[AB-RERUN]` scratch conversation and
carry نظام العمل into it as a `kind='references'` library page.

Identical carry to the one the original Case-B A/B measured
(`create_library_item(page_type='regulation', page_id='نظام-العمل')`), so the
only thing that differs between the two measurements is the router prompt.

    .venv/Scripts/python.exe agents/simple_search/eval/ab_setup.py
"""
from __future__ import annotations

import json
from pathlib import Path

from ab_common import (  # noqa: E402
    REG_LABOR_SLUG, USER_ID, ensure_scratch_conversation, hr, service_client, short,
)

from backend.app.services import library_item_service as LIS  # noqa: E402


def main() -> int:
    sb = service_client()
    hr("SETUP — [AB-RERUN] scratch conversation + the carried نظام العمل")

    convo_id = ensure_scratch_conversation(sb)
    print(f"conversation_id: {convo_id}")

    row, already = LIS.create_library_item(
        sb,
        user_id=USER_ID,
        conversation_id=convo_id,
        page_type="regulation",
        page_id=REG_LABOR_SLUG,
    )
    md = row.get("metadata") or {}
    info = {
        "conversation_id": convo_id,
        "item_id": str(row.get("item_id")),
        "kind": row.get("kind"),
        "title": row.get("title"),
        "already_attached": already,
        "summary_chars": len(row.get("summary") or ""),
        "content_chars": len(row.get("content_md") or ""),
        "has_simple_search_object": "simple_search_object" in md,
        "simple_search_object": md.get("simple_search_object"),
    }
    for k, v in info.items():
        print(f"  {k}: {short(str(v), 160)}")

    # Sanity: the carry must render into the router's context as a references
    # item — that is the whole variable under test.
    assert row.get("kind") == "references", row.get("kind")
    assert (row.get("title") or "").strip() == "نظام العمل", row.get("title")

    Path(__file__).with_name("ab_setup.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("\nsetup ok → ab_setup.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
