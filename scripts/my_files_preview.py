"""«ملفاتي» preview — what the /files endpoints and the pane would serve one user.

Test tool for ``.claude/plans/my_files.md`` (Phase 1). It calls the REAL
service functions the routes call (``backend/app/services/files_service.py``)
with the service-role client the backend uses, and writes their output as JSON
for review. READ-ONLY:

- only ``list_library_files`` / ``get_library_file`` and the pane's list read
  are called — never ``hide_library_file`` or ``match_library_file``;
- the pane read skips ``wi_preview_service.note_served`` (an analytics write);
- the ``MY_FILES_ENABLED`` flag is bypassed (the services do not read it);
- it prints counts only — never document text, summaries or filenames.

Usage, from the worktree root::

    python scripts/my_files_preview.py --user <users.user_id> --out <DIR>
        [--pane-conversation <conversation_id>]

Writes into ``--out``:

- ``files.json`` — ``GET /files`` (``list_library_files``);
- ``file_<item_id>.json`` — ``GET /files/{item_id}`` for every listed file;
- with ``--pane-conversation``: ``pane_workspace.json`` — ``{items, total}`` in
  the exact shape ``GET /conversations/{id}/workspace`` returns (the new
  ``summary_preview`` included), and a ``file_<item_id>.json`` for each of that
  conversation's attachments.

The output holds customer documents, so ``--out`` is refused when it sits
inside any git repository.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import shutil
import subprocess
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# Request URLs and bodies must not reach the console (httpx logs every request
# line at INFO once anything configures logging).
for _name in ("httpx", "httpcore", "hpack", "postgrest", "supabase"):
    logging.getLogger(_name).setLevel(logging.WARNING)


# --------------------------------------------------------------------------- #
# Guards
# --------------------------------------------------------------------------- #


def _inside_git_repository(path: Path) -> bool:
    """True when ``path`` (existing or not) lies inside a git work tree or a
    ``.git`` directory. Walks the parents for a ``.git`` entry (a directory, or
    the file a linked worktree has), then asks git itself from the nearest
    existing ancestor."""
    target = path.resolve()
    chain = [target, *target.parents]
    if any((p / ".git").exists() or p.name == ".git" for p in chain):
        return True
    existing = next((p for p in chain if p.exists()), None)
    if existing is None or shutil.which("git") is None:
        return False
    for flag in ("--is-inside-work-tree", "--is-inside-git-dir"):
        try:
            res = subprocess.run(
                ["git", "-C", str(existing), "rev-parse", flag],
                capture_output=True, text=True, timeout=15,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if res.returncode == 0 and res.stdout.strip() == "true":
            return True
    return False


def _uuid(value: str, what: str) -> str:
    try:
        return str(uuid.UUID(value))
    except (ValueError, AttributeError, TypeError):
        raise SystemExit(f"--{what}: not a uuid")


# --------------------------------------------------------------------------- #
# Output
# --------------------------------------------------------------------------- #


def _write(out: Path, name: str, payload: Any) -> None:
    (out / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


def _write_file_detail(sb: Any, user_id: str, item_id: str, out: Path) -> dict | None:
    """``file_<item_id>.json`` from the real ``get_library_file``, validated
    through the route's response model. None when it is not the user's."""
    from backend.app.models.responses import LibraryFileDetail
    from backend.app.services import files_service

    detail = files_service.get_library_file(sb, user_id, item_id)
    if detail is None:
        return None
    payload = LibraryFileDetail.model_validate(detail).model_dump(mode="json")
    _write(out, f"file_{item_id}.json", payload)
    return payload


# --------------------------------------------------------------------------- #
# Library
# --------------------------------------------------------------------------- #


def _library(sb: Any, user_id: str, out: Path) -> None:
    from backend.app.models.responses import LibraryFileListResponse
    from backend.app.services import files_service

    listing = LibraryFileListResponse.model_validate(files_service.list_library_files(sb, user_id))
    _write(out, "files.json", listing.model_dump(mode="json"))
    files = listing.files
    print(
        f"library: {len(files)} file(s) listed of {listing.total} "
        f"({sum(1 for f in files if f.has_sections)} with sections, "
        f"{sum(f.copies - 1 for f in files)} folded extra copies, "
        f"{sum(1 for f in files if not f.summary_preview)} without a summary preview)"
    )
    print("type counts: " + ", ".join(f"{k}={v}" for k, v in sorted(listing.type_counts.items())))

    written = sections = 0
    for f in files:
        detail = _write_file_detail(sb, user_id, f.item_id, out)
        if detail is not None:
            written += 1
            sections += detail.get("sections") is not None
    print(f"library details: wrote {written} file_*.json ({sections} with sections)")


# --------------------------------------------------------------------------- #
# Pane
# --------------------------------------------------------------------------- #


def _auth_id(sb: Any, user_id: str) -> str:
    """``users.auth_id`` for ``user_id`` — the pane's list function takes the
    caller's auth id, exactly as the route passes it."""
    resp = sb.table("users").select("auth_id").eq("user_id", user_id).limit(1).execute()
    rows = list(getattr(resp, "data", None) or [])
    if not rows or not rows[0].get("auth_id"):
        raise SystemExit("--user: no such user")
    return str(rows[0]["auth_id"])


async def _pane_payload(sb: Any, user_id: str, conversation_id: str) -> dict:
    """``GET /conversations/{id}/workspace`` minus its analytics write: the
    same list function, the same preview lock, the same response model."""
    from backend.app.api.workspace import _to_response
    from backend.app.models.responses import WorkspaceItemListResponse
    from backend.app.services import wi_preview_service, workspace_service

    auth_id = _auth_id(sb, user_id)
    # The route's defaults: limit=100, offset=0.
    items, total = workspace_service.list_workspace_items_by_conversation(
        sb, auth_id, conversation_id, limit=100, offset=0,
    )
    previews = await wi_preview_service.previews_for(sb, items)
    response = WorkspaceItemListResponse(
        items=[_to_response(i, previews.get(str(i.get("item_id") or ""))) for i in items],
        total=total,
    )
    return response.model_dump(mode="json")


def _pane(sb: Any, user_id: str, conversation_id: str, out: Path) -> None:
    payload = asyncio.run(_pane_payload(sb, user_id, conversation_id))
    _write(out, "pane_workspace.json", payload)
    items = payload.get("items") or []
    attachments = [i for i in items if i.get("kind") == "attachment"]
    print(
        f"pane: {len(items)} item(s) of {payload.get('total')} "
        f"({len(attachments)} attachment(s), "
        f"{sum(1 for i in attachments if i.get('summary_preview'))} with a summary preview)"
    )
    written = skipped = 0
    for item in attachments:
        if _write_file_detail(sb, user_id, str(item.get("item_id")), out) is not None:
            written += 1
        else:
            skipped += 1
    print(f"pane details: wrote {written} file_*.json, {skipped} not the user's")


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


def main() -> None:
    # Console text stays ASCII: a Windows console (cp1252) cannot print Arabic.
    ap = argparse.ArgumentParser(
        description="My-files preview: what GET /files, GET /files/{id} and the pane would "
        "serve one user. Read-only; writes JSON to --out (outside every git repository)."
    )
    ap.add_argument("--user", required=True, help="users.user_id (not the auth id)")
    ap.add_argument("--out", required=True, help="output directory - must be outside every git repository")
    ap.add_argument("--pane-conversation", help="also write that conversation's pane (workspace list)")
    args = ap.parse_args()

    user_id = _uuid(args.user, "user")
    conversation_id = _uuid(args.pane_conversation, "pane-conversation") if args.pane_conversation else None

    out = Path(args.out).expanduser()
    if _inside_git_repository(out):
        raise SystemExit(
            "--out is inside a git repository; the output holds customer documents - "
            "choose a directory outside every repository"
        )
    out.mkdir(parents=True, exist_ok=True)

    from backend.app.errors import LunaHTTPException
    from shared.db.client import get_supabase_client

    sb = get_supabase_client()
    try:
        _library(sb, user_id, out)
        if conversation_id:
            _pane(sb, user_id, conversation_id, out)
    except LunaHTTPException as exc:
        raise SystemExit(f"stopped: HTTP {exc.status_code} ({exc.code.value})")
    except Exception as exc:  # noqa: BLE001 — never echo a message that may carry data
        raise SystemExit(f"stopped: {type(exc).__name__}")
    print(f"output: {out.resolve()}")


if __name__ == "__main__":
    main()
