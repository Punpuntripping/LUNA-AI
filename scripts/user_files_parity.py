"""Phase-2 parity checks — files in their own table (``.claude/plans/my_files_phase2.md`` §F).

READ-ONLY. Two modes (cwd = repo root)::

    python scripts/user_files_parity.py --sql                 # the 222 self-check queries
    python scripts/user_files_parity.py --sql --print-sql     # print them (no DB access)
    python scripts/user_files_parity.py --agents [--user U] [--conversation C] [--limit N]
                                        [--out FILE.json] [--baseline FILE.json]

``--sql`` runs, in a READ ONLY transaction over ``SUPABASE_DB_URL`` (psycopg 3 or
psycopg2 must be installed; the URL is never printed):
  counts  per user, all and live: attachment items vs files (owner decision D14)
  fields  every attachment item vs its file: the columns agents read
  links   an attachment without a file, or a non-attachment with one
Each must return zero rows. Ids only are printed. The per-user count is a Phase-2
check: from Phase 3 a library upload is a file with no conversation item.

``--agents`` calls the REAL loaders for every conversation that has a live
attachment, once with the files read switch forced OFF and once forced ON
(``shared.files.flags.override``), and compares SHA-256 digests of each result's
canonical JSON. It prints ``conversation  function  equal|DIFFERENT  lengths``
and never prints text. With the schema not there yet (before 222) only the OFF
pass runs. ``--out`` saves ``{conversation: {function: {off, off_len, on, on_len}}}``
(digests and lengths only) to a file OUTSIDE every git repository; ``--baseline``
compares this run's OFF digests with a saved file (run before 222 and after it,
switch off both times: they must match). ``file_id`` — the field Phase 2 adds to
the «الملفات» responses — is left out of their digests.

Functions covered: the router's item summaries and their rendered fence, the
history (``_load_recent_messages(full=True)``), ``_load_attached_items`` and the
case-document blocks (plain and segmented with ``load_segment_maps``), the
writer's ``build_analyzed_items_direct`` and ``load_writer_planner_context``,
``unfold_item`` per attachment, ``_load_compaction_context``, the item
analyzer's loader, the pane (``list_workspace_items_by_conversation`` →
``_to_response``), and «الملفات» list / detail / match per user.

Nothing here writes or calls a model: segmented delivery reads the stored maps
directly (no helper wait, so no segmenter run).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from shared.files import flags as files_flags  # noqa: E402

PAGE = 1_000

SQL_COUNTS = """
SELECT user_id, a.n AS items, b.n AS files, a.live AS items_live, b.live AS files_live
  FROM (SELECT user_id, count(*) AS n, count(*) FILTER (WHERE deleted_at IS NULL) AS live
          FROM public.workspace_items WHERE kind = 'attachment' GROUP BY 1) a
  FULL JOIN
       (SELECT user_id, count(*) AS n, count(*) FILTER (WHERE deleted_at IS NULL) AS live
          FROM public.user_files GROUP BY 1) b USING (user_id)
 WHERE a.n IS DISTINCT FROM b.n OR a.live IS DISTINCT FROM b.live
"""

SQL_FIELDS = """
SELECT wi.item_id, wi.file_id
  FROM public.workspace_items wi JOIN public.user_files f ON f.file_id = wi.file_id
 WHERE wi.kind = 'attachment'
   AND (wi.user_id, wi.title, COALESCE(wi.content_md, ''), wi.summary, wi.summary_source_length,
        wi.summary_updated_at, COALESCE(wi.metadata, '{}'::jsonb), wi.storage_path, wi.document_id,
        wi.created_at, wi.deleted_at, wi.word_count, wi.metadata->>'doc_type')
       IS DISTINCT FROM
       (f.user_id, f.title, f.content_md, f.summary, f.summary_source_length, f.summary_updated_at,
        f.metadata, f.storage_path, f.document_id, f.created_at, f.deleted_at, f.word_count, f.doc_type)
"""

SQL_LINKS = """
SELECT item_id, kind, file_id
  FROM public.workspace_items
 WHERE (kind = 'attachment' AND file_id IS NULL) OR (kind <> 'attachment' AND file_id IS NOT NULL)
"""

QUERIES: tuple[tuple[str, str], ...] = (("counts", SQL_COUNTS), ("fields", SQL_FIELDS), ("links", SQL_LINKS))


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


def _inside_git(path: Path) -> bool:
    p = path.resolve()
    return any((q / ".git").exists() for q in [p, *p.parents])


def _check_out_path(raw: str) -> Path:
    out = Path(raw)
    if _inside_git(out if out.exists() and out.is_dir() else out.parent):
        raise SystemExit("--out is inside a git repository; choose a path outside it")
    return out


# ---------------------------------------------------------------------------
# Digests
# ---------------------------------------------------------------------------


def _plain(obj: Any) -> Any:
    """JSON-able form of a loader result (pydantic models, dataclasses, tuples)."""
    if hasattr(obj, "model_dump"):
        return _plain(obj.model_dump(mode="json"))
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return _plain(dataclasses.asdict(obj))
    if isinstance(obj, dict):
        return {str(k): _plain(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_plain(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_plain(v) for v in obj)
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    return str(obj)


def _without_file_id(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: _without_file_id(v) for k, v in obj.items() if k != "file_id"}
    if isinstance(obj, list):
        return [_without_file_id(v) for v in obj]
    return obj


def canonical(obj: Any) -> str:
    """A string is digested as is (a rendered prompt fence); anything else as
    sorted-key compact JSON."""
    if isinstance(obj, str):
        return obj
    return json.dumps(_plain(obj), sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def digest(obj: Any) -> tuple[str, int]:
    text = canonical(obj)
    return hashlib.sha256(text.encode("utf-8")).hexdigest(), len(text)


@dataclasses.dataclass(frozen=True)
class _Failed:
    error: str


# ---------------------------------------------------------------------------
# --sql
# ---------------------------------------------------------------------------


def _connect(url: str) -> Any:
    try:
        import psycopg  # type: ignore[import-not-found]

        return psycopg.connect(url)
    except ImportError:
        pass
    try:
        import psycopg2  # type: ignore[import-not-found]

        return psycopg2.connect(url)
    except ImportError:
        raise SystemExit(
            "--sql needs a PostgreSQL driver (pip install 'psycopg[binary]'), or run "
            "--sql --print-sql and paste the queries into the SQL editor"
        )


def run_sql(*, print_only: bool = False, connect: Any = None) -> int:
    if print_only:
        for name, sql in QUERIES:
            print(f"-- {name} (expects 0 rows)\n{sql.strip()};\n")
        return 0
    from shared.config import get_settings

    url = (get_settings().SUPABASE_DB_URL or "").strip()
    if not url:
        raise SystemExit("SUPABASE_DB_URL is not set")
    conn = (connect or _connect)(url)
    bad = 0
    try:
        cur = conn.cursor()
        cur.execute("BEGIN READ ONLY")
        for name, sql in QUERIES:
            cur.execute(sql)
            rows = cur.fetchall()
            bad += len(rows)
            print(f"{name:7s} {len(rows)} row(s){'' if rows else ' — ok'}")
            for row in rows[:50]:
                print("   ", "  ".join("" if v is None else str(v) for v in row))   # ids and counts only
        cur.execute("ROLLBACK")
    finally:
        conn.close()
    return 0 if bad == 0 else 1


# ---------------------------------------------------------------------------
# --agents
# ---------------------------------------------------------------------------


def _rows(resp: Any) -> list[dict]:
    return [r for r in (getattr(resp, "data", None) or []) if isinstance(r, dict)]


def conversations_with_attachments(
    sb: Any, *, user_id: Optional[str] = None, conversation_id: Optional[str] = None,
) -> list[tuple[str, str]]:
    """``(conversation_id, user_id)`` of every conversation with a live attachment."""
    out: dict[tuple[str, str], None] = {}
    start = 0
    while True:
        q = (
            sb.table("workspace_items")
            .select("conversation_id, user_id, item_id")
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
        )
        if user_id:
            q = q.eq("user_id", user_id)
        if conversation_id:
            q = q.eq("conversation_id", conversation_id)
        batch = _rows(q.order("item_id").range(start, start + PAGE - 1).execute())
        for r in batch:
            if r.get("conversation_id") and r.get("user_id"):
                out[(str(r["conversation_id"]), str(r["user_id"]))] = None
        if len(batch) < PAGE:
            return list(out)
        start += PAGE


def _attachments(sb: Any, conversation_id: str, user_id: str) -> list[dict]:
    return _rows(
        sb.table("workspace_items")
        .select("item_id, metadata")
        .eq("conversation_id", conversation_id)
        .eq("user_id", user_id)
        .eq("kind", "attachment")
        .is_("deleted_at", "null")
        .order("created_at")
        .execute()
    )


def _auth_id(sb: Any, user_id: str) -> Optional[str]:
    rows = _rows(sb.table("users").select("auth_id").eq("user_id", user_id).limit(1).execute())
    return str(rows[0]["auth_id"]) if rows and rows[0].get("auth_id") else None


@contextmanager
def _no_helper_waits() -> Iterator[None]:
    """``build_analyzed_items_direct`` with segmented delivery would wait on the
    segmenter helper — which can START it (a model call and a write). Here the
    stored maps are read directly instead; everything else is the real code."""
    from agents.memory.attachment_segmenter.store import load_segment_maps
    from agents.utils import attachment_delivery as delivery
    from agents.writer_planner import walkers

    original = walkers._attachment_bodies

    async def read_only(selected_ids, wi_index, deps, role_assignments=None):
        if not delivery.delivery_enabled(getattr(deps, "user_id", None)):
            return {}
        roles = role_assignments or {}
        contents = {
            iid: str(wi_index[iid].get("content_md") or "")
            for iid in selected_ids
            if iid in wi_index
            and str(wi_index[iid].get("kind") or "") == "attachment"
            and roles.get(iid, "source") in walkers._SEGMENTED_ROLES
        }
        ids = [i for i, c in contents.items() if c.strip()]
        maps = load_segment_maps(deps.supabase, ids, user_id=deps.user_id)
        sections = {
            str(i): delivery.sections_from_rows(getattr(m, "segments", None) or [])
            for i, m in maps.items()
        }
        bodies, _stats = delivery.writer_bodies(contents, {k: v for k, v in sections.items() if v})
        return bodies

    walkers._attachment_bodies = read_only
    try:
        yield
    finally:
        walkers._attachment_bodies = original


def snapshot_conversation(
    sb: Any, conversation_id: str, user_id: str, auth_id: Optional[str],
) -> dict[str, Any]:
    """Every covered loader's result for one conversation, in the CURRENT switch
    state. The async loaders are each driven by ``asyncio.run``."""
    import agents.memory.agent as memory
    import agents.orchestrator as orch
    import agents.router.context as rctx
    from agents.deep_search_v4.shared.case_documents import (
        build_case_document_blocks,
        build_case_document_blocks_segmented,
    )
    from agents.memory.attachment_segmenter.store import load_segment_maps
    from agents.memory.item_analyzer.runner import _load_workspace_items
    from agents.tool_repository.unfold_workspace_item import unfold_item
    from agents.utils.attachment_delivery import sections_from_rows
    from agents.utils.router_context_render import render_workspace_items_fence
    from agents.writer_planner.walkers import build_analyzed_items_direct
    from backend.app.services.writer_planner_context import load_writer_planner_context

    att_ids = [str(r["item_id"]) for r in _attachments(sb, conversation_id, user_id)]
    out: dict[str, Any] = {}

    def take(name: str, fn: Any) -> Any:
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001
            value = _Failed(type(exc).__name__)
        out[name] = value
        return value

    summaries = take("router._load_workspace_item_summaries",
                     lambda: rctx._load_workspace_item_summaries(sb, conversation_id, user_id))
    if not isinstance(summaries, _Failed):
        take("router_context_render.render_workspace_items_fence",
             lambda: render_workspace_items_fence(summaries[0]))
    take("orchestrator._load_recent_messages(full=True)",
         lambda: orch._load_recent_messages(sb, conversation_id, user_id=user_id, full=True))
    attached = take("orchestrator._load_attached_items",
                    lambda: orch._load_attached_items(sb, att_ids, user_id, conversation_id))
    if not isinstance(attached, _Failed):
        take("case_documents.build_case_document_blocks",
             lambda: build_case_document_blocks(attached, att_ids))
        maps = take("attachment_segmenter.load_segment_maps",
                    lambda: load_segment_maps(sb, att_ids, user_id=user_id))
        if not isinstance(maps, _Failed):
            sections = {
                str(i): sections_from_rows(getattr(m, "segments", None) or []) for i, m in maps.items()
            }
            take("case_documents.build_case_document_blocks_segmented",
                 lambda: build_case_document_blocks_segmented(attached, att_ids, sections))
    deps = SimpleDeps(supabase=sb, user_id=user_id, conversation_id=conversation_id)
    with _no_helper_waits():
        take("walkers.build_analyzed_items_direct",
             lambda: asyncio.run(build_analyzed_items_direct(att_ids, {}, deps)))
    take("writer_planner_context.load_writer_planner_context",
         lambda: asyncio.run(load_writer_planner_context(sb, user_id, conversation_id)))
    for iid in att_ids:
        take(f"unfold.unfold_item[{iid}]", lambda iid=iid: unfold_item(sb, iid, user_id))
    take("memory._load_compaction_context",
         lambda: memory._load_compaction_context(sb, conversation_id, user_id))
    take("item_analyzer._load_workspace_items",
         lambda: asyncio.run(_load_workspace_items(sb, att_ids, user_id=user_id)))
    if auth_id:
        def pane() -> Any:
            from backend.app.api.workspace import _to_response
            from backend.app.services.workspace_service import list_workspace_items_by_conversation

            items, total = list_workspace_items_by_conversation(sb, auth_id, conversation_id)
            return [_to_response(i) for i in items], total

        take("workspace_service.list_workspace_items_by_conversation->_to_response", pane)
    return out


@dataclasses.dataclass
class SimpleDeps:
    supabase: Any
    user_id: str
    conversation_id: str
    emit_sse: Any = None


def snapshot_files(sb: Any, user_id: str, attachments: list[tuple[str, dict]]) -> dict[str, Any]:
    """«الملفات» for one user: the list, and per attachment the detail and the
    same-file match (``file_id``, Phase 2's additive field, left out)."""
    from backend.app.services import files_service as fs

    out: dict[str, Any] = {}

    def take(name: str, fn: Any) -> None:
        try:
            out[name] = _without_file_id(_plain(fn()))
        except Exception as exc:  # noqa: BLE001
            out[name] = _Failed(type(exc).__name__)

    take("files_service.list_library_files", lambda: fs.list_library_files(sb, user_id))
    for conversation_id, row in attachments:
        iid = str(row["item_id"])
        meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
        take(f"files_service.get_library_file[{iid}]", lambda iid=iid: fs.get_library_file(sb, user_id, iid))
        take(
            f"files_service.match_library_file[{iid}]",
            lambda meta=meta, conversation_id=conversation_id: fs.match_library_file(
                sb, user_id,
                filename=str(meta.get("filename") or ""),
                size_bytes=meta.get("file_size_bytes") or 0,
                mime_type=str(meta.get("mime_type") or ""),
                conversation_id=conversation_id,
            ),
        )
    return out


def _digests(results: dict[str, Any]) -> dict[str, tuple[str, int]]:
    return {
        name: ((f"error:{value.error}", 0) if isinstance(value, _Failed) else digest(value))
        for name, value in results.items()
    }


def _both_ways(sb: Any, fn: Any, *, schema: bool) -> tuple[dict, Optional[dict]]:
    with files_flags.override(read=False, write=False):
        off = _digests(fn())
    if not schema:
        return off, None
    with files_flags.override(read=True, write=False):
        on = _digests(fn())
    return off, on


def run_agents(
    sb: Any,
    *,
    user_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    limit: int = 0,
    out_path: Optional[Path] = None,
    baseline_path: Optional[Path] = None,
) -> int:
    files_flags.reset_probe()
    schema = files_flags.schema_ready(sb)
    if not schema:
        print("schema not ready (no user_files / workspace_items.file_id): OFF pass only")
    pairs = conversations_with_attachments(sb, user_id=user_id, conversation_id=conversation_id)
    if limit:
        pairs = pairs[:limit]
    baseline = json.loads(baseline_path.read_text(encoding="utf-8")) if baseline_path else None

    report: dict[str, dict[str, dict]] = {}
    different = 0

    def record(key: str, off: dict, on: Optional[dict]) -> None:
        nonlocal different
        report.setdefault(key, {})
        for name, (d_off, n_off) in off.items():
            entry: dict[str, Any] = {"off": d_off, "off_len": n_off}
            verdict = ""
            if on is not None:
                d_on, n_on = on.get(name, ("missing", 0))
                entry.update(on=d_on, on_len=n_on)
                same = d_on == d_off and not d_off.startswith("error:")
                verdict = "equal" if same else "DIFFERENT"
                different += 0 if same else 1
                lengths = f"{n_off} {n_on}"
            else:
                lengths = f"{n_off}"
            if baseline is not None:
                before = (baseline.get(key) or {}).get(name, {}).get("off")
                same_base = before == d_off
                verdict += (" " if verdict else "") + ("baseline-equal" if same_base else "baseline-DIFFERENT")
                different += 0 if same_base else 1
            report[key][name] = entry
            print(f"{key}  {name}  {verdict or 'off-only'}  {lengths}")

    by_user: dict[str, list[tuple[str, dict]]] = {}
    for conv, uid in pairs:
        auth_id = _auth_id(sb, uid)
        off, on = _both_ways(
            sb, lambda conv=conv, uid=uid, auth_id=auth_id: snapshot_conversation(sb, conv, uid, auth_id),
            schema=schema,
        )
        record(conv, off, on)
        by_user.setdefault(uid, []).extend((conv, r) for r in _attachments(sb, conv, uid))
    for uid, atts in by_user.items():
        off, on = _both_ways(sb, lambda uid=uid, atts=atts: snapshot_files(sb, uid, atts), schema=schema)
        record(f"user:{uid}", off, on)

    if out_path is not None:
        out_path.write_text(json.dumps(report, indent=1, sort_keys=True), encoding="utf-8")
        print(f"digests written: {len(report)} key(s)")
    print(f"{len(pairs)} conversation(s), {len(by_user)} user(s), {different} difference(s)")
    return 0 if different == 0 else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--sql", action="store_true", help="the 222 count / field / link checks")
    mode.add_argument("--agents", action="store_true", help="what each agent loader returns, switch off vs on")
    ap.add_argument("--print-sql", action="store_true", help="with --sql: print the queries, touch nothing")
    ap.add_argument("--user", default="", help="only this users.user_id")
    ap.add_argument("--conversation", default="", help="only this conversation_id")
    ap.add_argument("--limit", type=int, default=0, help="at most N conversations")
    ap.add_argument("--out", default="", help="digest file OUTSIDE every git repository")
    ap.add_argument("--baseline", default="", help="a previous --out file to compare the OFF digests with")
    args = ap.parse_args(argv)
    try:
        sys.stdout.reconfigure(errors="replace")   # a Windows console is not UTF-8
    except (AttributeError, ValueError):
        pass

    if args.sql:
        return run_sql(print_only=args.print_sql)

    out_path = _check_out_path(args.out) if args.out else None
    baseline_path = Path(args.baseline) if args.baseline else None
    from shared.db.client import get_supabase_client

    return run_agents(
        get_supabase_client(),
        user_id=args.user or None,
        conversation_id=args.conversation or None,
        limit=args.limit,
        out_path=out_path,
        baseline_path=baseline_path,
    )


if __name__ == "__main__":
    raise SystemExit(main())
