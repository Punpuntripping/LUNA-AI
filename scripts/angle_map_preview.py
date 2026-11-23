"""Angle map preview — what the angle mapper («خريطة الأوجه», angles-v5) makes of a message group.

The owner's review tool for ``.claude/plans/relationship_map.md`` §4.7 / D17. A message
group is the attachments a user sent in ONE message, mapped together in one call (a
message with one file is a group of one; a file with no message link is a group of
one too). The tool runs the REAL angle mapper on each group
(``run_message_angle_map(..., preview=True)``: model call included, masking of what the
model reads included, the user's words included). Nothing else:

- nothing is written to the database (no map, no members, no angles, no masking fakes,
  no llm_calls row — the preview runs detached from any ledger scope, so no user's
  quota is ever charged);
- segment maps are only read: a member without a usable one is left out of its group
  (shown with the reason), a group with none comes back with the runner's outcome;
- the flags are not touched (preview bypasses the angle-map gate); the pending /
  fresh / join rules of the live helper do not apply — the group is mapped now.

Usage, from the worktree root (settings come from its ``.env``)::

    python scripts/angle_map_preview.py --message <uuid-or-prefix> [--message …] --out DIR [--concurrency 2]
    python scripts/angle_map_preview.py --item <uuid-or-prefix> [--item …] --out DIR      (each item's message group)
    python scripts/angle_map_preview.py --conversation <conversation_id> --out DIR       (every message group in it)
    python scripts/angle_map_preview.py --from-json DIR/angle_map_preview.json --out DIR  (re-render, no model call)

``--message`` / ``--item`` take a full id or a prefix of at least 6 hex characters (it
must match exactly one row), or a comma list. The selectors combine; a group selected
twice runs once; order is kept.

Output: ``angle_map_preview.json`` (raw results + what the page needs) and
``angle_map_preview.html`` in ``--out``. Both hold customer documents: ``--out`` must be
OUTSIDE every git repository (the tool refuses otherwise, before any model call).
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import html
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.memory.angle_mapper import PROMPT_VERSION, run_message_angle_map  # noqa: E402
from agents.memory.angle_mapper.group import group_item_ids, message_items  # noqa: E402
from agents.memory.angle_mapper.labels import (  # noqa: E402
    LABEL_AR,
    angle_viewpoint,
    relative_label,
    resolve_ref,
    section_index,
)
from agents.memory.angle_mapper.refs import canonical_doc, canonical_ref, display_ref, doc_of, format_doc  # noqa: E402
from agents.memory.attachment_segmenter import (  # noqa: E402
    PARAGRAPH_RULE_VERSION,
    load_segment_maps,
    paragraphize,
)
from agents.memory.attachment_segmenter.bundles import primary_messages  # noqa: E402
from agents.memory.attachment_segmenter.runner import _load_user_words  # noqa: E402

_COLS = "item_id, user_id, conversation_id, wi_seq, kind, title, content_md, metadata, created_at, deleted_at"
_PREFIX_MIN_HEX = 6
_HEX = frozenset("0123456789abcdef")
JSON_NAME = "angle_map_preview.json"
HTML_NAME = "angle_map_preview.html"
#: A run that produced a map: a preview (nothing stored) or a stored run.
OK_OUTCOMES = ("preview", "stored")
#: The runner result fields the page keeps.
RESULT_FIELDS = ("outcome", "item_id", "message_id", "item_ids", "excluded", "map_id", "output", "unplaced",
                 "stats", "error")


# --------------------------------------------------------------------------- #
# Selection (read-only)
# --------------------------------------------------------------------------- #


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _fmt_uuid(h: str) -> str:
    return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def _hex(token: str, flag: str) -> str:
    h = token.strip().lower().replace("-", "")
    if not h or len(h) > 32 or any(c not in _HEX for c in h):
        raise SystemExit(f"{flag} {token!r}: not a uuid or a hex prefix")
    if len(h) < 32 and len(h) < _PREFIX_MIN_HEX:
        raise SystemExit(f"{flag} {token!r}: a prefix needs at least {_PREFIX_MIN_HEX} hex characters")
    return h


def _by_prefix(sb: Any, table: str, column: str, h: str, extra: str) -> list[dict]:
    """Rows whose uuid ``column`` starts with ``h``. A uuid column takes no LIKE
    through PostgREST, so a prefix becomes the range [prefix000…, prefixfff…]
    (uuid order = hex order)."""
    q = (
        sb.table(table).select(f"{column}, {extra}")
        .gte(column, _fmt_uuid(h.ljust(32, "0"))).lte(column, _fmt_uuid(h.ljust(32, "f")))
    )
    if table == "workspace_items":
        q = q.is_("deleted_at", "null")
    return _data(q.limit(6).execute())


def _resolve_item(sb: Any, token: str) -> str:
    """A full item id, or the one live workspace item whose id starts with ``token``."""
    h = _hex(token, "--item")
    if len(h) == 32:
        return _fmt_uuid(h)
    rows = _by_prefix(sb, "workspace_items", "item_id", h, "kind")
    if len(rows) > 1:
        attachments = [r for r in rows if r.get("kind") == "attachment"]
        if len(attachments) == 1:
            rows = attachments
    if not rows:
        raise SystemExit(f"--item {token!r}: no live workspace item with that id prefix")
    if len(rows) > 1:
        listed = ", ".join(f"{r['item_id']} ({r.get('kind')})" for r in rows)
        raise SystemExit(f"--item {token!r} is ambiguous: {listed}")
    return str(rows[0]["item_id"])


def _resolve_message(sb: Any, token: str) -> str:
    """A full message id, or the one message whose id starts with ``token`` (a user
    message wins over an assistant one)."""
    h = _hex(token, "--message")
    if len(h) == 32:
        return _fmt_uuid(h)
    rows = _by_prefix(sb, "messages", "message_id", h, "role")
    if len(rows) > 1:
        users = [r for r in rows if r.get("role") == "user"]
        if len(users) == 1:
            rows = users
    if not rows:
        raise SystemExit(f"--message {token!r}: no message with that id prefix")
    if len(rows) > 1:
        listed = ", ".join(f"{r['message_id']} ({r.get('role')})" for r in rows)
        raise SystemExit(f"--message {token!r} is ambiguous: {listed}")
    return str(rows[0]["message_id"])


def _conversation_ids(sb: Any, conversation_id: str) -> list[str]:
    """The conversation's live attachments (ids only), in upload order."""
    return [
        str(r["item_id"])
        for r in _data(
            sb.table("workspace_items").select("item_id")
            .eq("conversation_id", conversation_id).eq("kind", "attachment")
            .is_("deleted_at", "null").order("wi_seq").execute()
        )
    ]


def _group(message_id: str | None, item_ids: Iterable[str]) -> dict:
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    return {"key": f"message:{message_id}" if message_id else f"item:{ids[0]}",
            "message_id": message_id, "item_ids": ids}


def _group_of_item(sb: Any, item_id: str) -> dict:
    """The item's message group, resolved the way the live runner does."""
    message_id, ids = group_item_ids(sb, item_id)
    return _group(message_id, ids)


def _group_of_message(sb: Any, message_id: str) -> dict | None:
    ids = message_items(sb, message_id)
    if not ids:
        print(f"skipped: message {message_id} has no attachment sent first with it")
        return None
    return _group(message_id, ids)


def _conversation_groups(sb: Any, conversation_id: str) -> list[dict]:
    """Every message group of the conversation, in the upload order of its first file;
    a file with no message link is a group of its own."""
    ids = _conversation_ids(sb, conversation_id)
    primary = primary_messages(sb, ids)
    out: list[dict] = []
    seen: set[str] = set()
    for item_id in ids:
        msg = primary.get(item_id)
        if msg:
            if msg in seen:
                continue
            seen.add(msg)
            g = _group_of_message(sb, msg)
            if g:
                out.append(g)
        else:
            out.append(_group(None, [item_id]))
    return out


def select_groups(sb: Any, *, messages: Iterable[str] = (), items: Iterable[str] = (),
                  conversations: Iterable[str] = ()) -> list[dict]:
    """The selected groups, in order, each once."""
    groups: list[dict] = []
    for token in _tokens(messages):
        g = _group_of_message(sb, _resolve_message(sb, token))
        if g:
            groups.append(g)
    for token in _tokens(items):
        groups.append(_group_of_item(sb, _resolve_item(sb, token)))
    for conversation_id in _tokens(conversations):
        groups.extend(_conversation_groups(sb, conversation_id))
    unique: dict[str, dict] = {}
    for g in groups:
        unique.setdefault(g["key"], g)
    return list(unique.values())


def _tokens(values: Iterable[str]) -> list[str]:
    return [t.strip() for v in values for t in str(v or "").split(",") if t.strip()]


def _load_rows(sb: Any, item_ids: list[str]) -> list[dict]:
    """The items, files overlaid (my_files_phase2 §C4) like the runners read them, in
    the given order. Deleted rows come back too (the page shows why they are out)."""
    from shared.files import store as files_store

    if not item_ids:
        return []
    rows = _data(
        sb.table("workspace_items").select(files_store.with_file_id(_COLS, supabase=sb))
        .in_("item_id", item_ids).execute()
    )
    rows = files_store.overlay_files(sb, rows)
    by_id = {str(r["item_id"]): r for r in rows}
    return [by_id[i] for i in item_ids if i in by_id]


def _live_attachment(row: dict) -> bool:
    return str(row.get("kind") or "") == "attachment" and not row.get("deleted_at")


def _doc_type(row: dict) -> str:
    md = row.get("metadata") or {}
    return str(md.get("doc_type") or "") if isinstance(md, dict) else ""


def _load_prices(sb: Any) -> None:
    """The app loads the pricing registry at startup; a script must do it itself (a read)."""
    try:
        from shared import pricing

        pricing.load_pricing(sb)
    except Exception as exc:  # noqa: BLE001
        print(f"pricing not loaded ({type(exc).__name__}) — costs show as 0")


# --------------------------------------------------------------------------- #
# Run
# --------------------------------------------------------------------------- #


def _plain(v: Any) -> Any:
    """Pydantic models / dataclasses / containers → JSON-ready values."""
    if hasattr(v, "model_dump"):
        return v.model_dump(mode="json")
    if dataclasses.is_dataclass(v) and not isinstance(v, type):
        return _plain(dataclasses.asdict(v))
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple, set)):
        return [_plain(x) for x in v]
    return v


def _result_dict(res: Any) -> dict:
    """The pinned ``AngleMapRunResult`` fields, whatever class carries them."""
    return {k: _plain(getattr(res, k, None)) for k in RESULT_FIELDS}


def _page_doc(sb: Any, row: dict) -> dict:
    """One document of a group as the page needs it — reads only."""
    iid = str(row["item_id"])
    text = row.get("content_md") or ""
    smap = load_segment_maps(sb, [iid], content_chars={iid: len(text)}).get(iid)
    seg_map, segments = None, []
    if smap is not None:
        d = smap.model_dump()
        segments = [{k: v for k, v in s.items() if k != "content_md"} for s in d.pop("segments")]
        seg_map = {k: d.get(k) for k in ("prompt_version", "paragraph_rule_version", "content_chars",
                                         "documents_in_file", "key_paragraphs", "model", "created_at")}
    return {
        "item_id": iid,
        "wi": f"WI-{row.get('wi_seq')}",
        "kind": str(row.get("kind") or ""),
        "deleted": bool(row.get("deleted_at")),
        "doc_type": _doc_type(row),
        "title": row.get("title") or "",
        "chars": len(text),
        "content_md": text,
        "seg_map": seg_map,
        "segments": segments,
    }


def _page_group(sb: Any, group: dict, rows: list[dict], res: dict) -> dict:
    """The group's page data: its documents (members in D-order, then the ones left
    out), the user's words (read for D1 — what the model saw), the result."""
    by_id = {str(r["item_id"]): r for r in rows}
    members = [i for i in res.get("item_ids") or [] if i in by_id]
    excluded = res.get("excluded") or {}
    docs: list[dict] = []
    for n, item_id in enumerate(members, start=1):
        docs.append({**_page_doc(sb, by_id[item_id]), "doc_index": n, "excluded": None})
    for item_id in group["item_ids"]:
        if item_id in members:
            continue
        reason = excluded.get(item_id) or ("skipped_not_found" if item_id not in by_id else "")
        doc = _page_doc(sb, by_id[item_id]) if item_id in by_id else {
            "item_id": item_id, "wi": "", "kind": "", "deleted": True, "doc_type": "", "title": "", "chars": 0,
            "content_md": "", "seg_map": None, "segments": []}
        docs.append({**doc, "doc_index": None, "excluded": reason})
    first = by_id.get(members[0]) if members else next((r for r in rows if _live_attachment(r)), None)
    return {
        "key": group["key"],
        "message_id": group["message_id"],
        "conversation_id": str((first or {}).get("conversation_id") or ""),
        "user_words": _load_user_words(sb, first) if first else "",
        "documents": docs,
        "result": res,
    }


async def collect(sb: Any, groups: list[dict], *, concurrency: int = 2) -> dict:
    """Run every group in preview, ``concurrency`` at a time. Reads only."""
    sem = asyncio.Semaphore(max(1, concurrency))

    async def one(group: dict) -> dict:
        async with sem:
            rows = await asyncio.to_thread(_load_rows, sb, group["item_ids"])
            live = [str(r["item_id"]) for r in rows if _live_attachment(r)]
            # A live attachment first: the runner reads the group's user from its first id.
            ids = live + [i for i in group["item_ids"] if i not in live]
            try:
                res = _result_dict(await run_message_angle_map(sb, group["message_id"], ids, preview=True))
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 — the runner never raises; keep the other groups if it does
                res = {k: None for k in RESULT_FIELDS} | {
                    "outcome": "error", "item_ids": [], "excluded": {}, "unplaced": [], "stats": {},
                    "error": f"preview: {type(exc).__name__}"}
            page = await asyncio.to_thread(_page_group, sb, group, rows, res)
            print(_line(page), flush=True)
            return page

    t0 = time.perf_counter()
    pages = await asyncio.gather(*(one(g) for g in groups))
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_version": PROMPT_VERSION,
        "paragraph_rule_version": PARAGRAPH_RULE_VERSION,
        "wall_s": round(time.perf_counter() - t0, 1),
        "groups": list(pages),
    }


# --------------------------------------------------------------------------- #
# Derived numbers (all from the saved data — ``--from-json`` re-renders them)
# --------------------------------------------------------------------------- #

KINDS = ("strengthen", "attack", "answer")


def _res(g: dict) -> dict:
    r = g.get("result")
    return r if isinstance(r, dict) else {}


def _out(g: dict) -> dict:
    o = _res(g).get("output")
    return o if isinstance(o, dict) else {}


def _stats(g: dict) -> dict:
    s = _res(g).get("stats")
    return s if isinstance(s, dict) else {}


def _angles(g: dict) -> list[dict]:
    return [a for a in (_out(g).get("angles") or []) if isinstance(a, dict)]


def _members(g: dict) -> list[dict]:
    return sorted((d for d in g.get("documents") or [] if d.get("doc_index")), key=lambda d: d["doc_index"])


def _left_out(g: dict) -> list[dict]:
    return [d for d in g.get("documents") or [] if not d.get("doc_index")]


def _refs(values: Any) -> list[str]:
    return [canonical_ref(v) for v in (values or []) if canonical_ref(v)]


def _cost(g: dict) -> float:
    try:
        return float(_stats(g).get("cost_usd") or 0)
    except (TypeError, ValueError):
        return 0.0


def _secs(g: dict) -> float | None:
    s = _stats(g).get("secs")
    return float(s) if isinstance(s, (int, float)) else None


def _kind_counts(g: dict) -> dict[str, int]:
    return {k: sum(1 for a in _angles(g) if a.get("kind") == k) for k in KINDS}


def _angle_docs(a: dict) -> list[int]:
    return list(dict.fromkeys(d for d in (doc_of(r) for r in _refs(a.get("sections"))) if d is not None))


def _cross(a: dict) -> bool:
    """The angle ties sections of two or more documents (computed from its refs)."""
    return len(_angle_docs(a)) > 1


def _index(g: dict) -> dict[str, dict]:
    """``{ref: section dict}`` over the group's members — ``labels.section_index``."""
    members = _members(g)
    return section_index(
        [{"doc_index": d["doc_index"], "item_id": d["item_id"]} for d in members],
        {d["item_id"]: {"segments": d.get("segments") or []} for d in members},
    )


def _core_refs(g: dict) -> list[str]:
    return [r for r, s in _index(g).items() if s.get("value") == "core"]


def _coverage(g: dict) -> dict[str, list[str]]:
    """Core sections of every document: in an angle / in context / in no_angle with a
    reason / unplaced (the runner's ``unplaced`` — core sections it had to file under
    no_angle itself — plus any core section found nowhere)."""
    out = _out(g)
    in_angle = {r for a in _angles(g) for r in _refs(a.get("sections"))}
    ctx = set(_refs(out.get("context")))
    no = set(_refs([n.get("section") for n in out.get("no_angle") or [] if isinstance(n, dict)]))
    unplaced = set(_refs(_res(g).get("unplaced")))
    cov: dict[str, list[str]] = {"core": _core_refs(g), "angle": [], "context": [], "no_angle": [], "unplaced": []}
    for c in cov["core"]:
        if c in in_angle:
            cov["angle"].append(c)
        elif c in ctx:
            cov["context"].append(c)
        elif c in no and c not in unplaced:
            cov["no_angle"].append(c)
        else:
            cov["unplaced"].append(c)
    return cov


def _bad_refs(g: dict) -> list[str]:
    """Refs in the output that are not a section of the group (the runner drops them —
    any here means the saved data predates that)."""
    index = _index(g)
    out = _out(g)
    refs = [r for a in _angles(g) for r in _refs(a.get("sections"))]
    refs += _refs(out.get("context"))
    refs += [r for x in out.get("gaps") or [] if isinstance(x, dict) for r in _refs(x.get("sections"))]
    refs += _refs([n.get("section") for n in out.get("no_angle") or [] if isinstance(n, dict)])
    return sorted({r for r in refs if r not in index})


def _doc_num(value: Any) -> int | None:
    """``"D2"`` / ``"d2"`` / ``2`` → ``2``; anything else → ``None``."""
    doc = canonical_doc(value)
    return int(doc[1:]) if doc[:1] == "D" and doc[1:].isdigit() else None


def _client(g: dict) -> dict:
    c = _out(g).get("client")
    return c if isinstance(c, dict) else {}


def _total_chars(g: dict) -> int:
    return int(_stats(g).get("total_chars") or sum(d.get("chars") or 0 for d in _members(g)))


def _line(g: dict) -> str:
    res = _res(g)
    kc = _kind_counts(g)
    cov = _coverage(g)
    secs = _secs(g)
    label = (g.get("message_id") or "")[:8] or f"item {g['key'].split(':', 1)[1][:8]}"
    return (
        f"{label:<13} docs={len(_members(g))}+{len(_left_out(g))} {_total_chars(g):>8,} "
        f"{str(res.get('outcome')):<22} angles={len(_angles(g)):>2} s/a/r={kc['strengthen']}/{kc['attack']}/"
        f"{kc['answer']} cross={sum(1 for a in _angles(g) if _cross(a))} "
        f"core={len(cov['angle'])}/{len(cov['context'])}/{len(cov['no_angle'])}/{len(cov['unplaced'])} "
        f"{f'{secs:5.0f}s' if secs is not None else '    -s'} ${_cost(g):.4f}"
        + (f" error={res.get('error')}" if res.get("error") else "")
    )


# --------------------------------------------------------------------------- #
# Page
# --------------------------------------------------------------------------- #

E = html.escape
KIND_AR = {"strengthen": ("يقوّي", "k-str"), "attack": ("يطعن", "k-att"), "answer": ("يجب الرد", "k-ans")}
STRENGTH_AR = {"strong": "قوي", "medium": "متوسط", "weak": "ضعيف"}
#: The angle card's badge for ``ocr_suspect`` (angles-v5).
OCR_SUSPECT_AR = "قد يكون خطأ قراءة"
CONF_AR = {"clear": "واضح", "likely": "مرجّح", "unclear": "غير واضح"}
GAP_AR = {"missing": "ناقص", "contradiction": "تعارض", "unanswered": "بلا رد"}
SIDE_AR = {"claimant": "المدعي", "respondent": "المدعى عليه", "prosecution": "الادعاء العام", "victim": "المجني عليه",
           "intervenor": "المتدخّل", "contract_party": "طرف في العقد", "unknown": "غير محدد"}
TYPE_AR = {"judgment": "صك حكم", "contract": "ملف عقد", "hearing_minutes": "ضبط جلسة", "memo": "مذكرة",
           "claim": "لائحة دعوى", "reference": "نص نظامي", "expert_report": "تقرير خبير", "financial": "سجل مالي",
           "letter": "خطابات", "official_record": "سجل رسمي"}
VAL_AR = {"core": "جوهري", "supporting": "مساند", "metadata": "بيانات", "boilerplate": "قالب", "repeat": "مكرر"}
REASON_AR = {
    "skipped_no_map": "لا خريطة تقسيم", "skipped_old_segments": "تقسيم أقدم من seg-v5.2 (بلا ملّاك)",
    "skipped_paragraph_rule": "الفقرات لم تعد تطابق التقسيم", "skipped_stale": "تغيّر النص بعد التقسيم",
    "skipped_no_sections": "خريطة تقسيم بلا أقسام", "skipped_not_found": "محذوف أو غير موجود",
    "skipped_not_attachment": "ليس مرفقاً", "skipped_other_user": "لمستخدم آخر",
}
LABEL_CLASS = {"own": "sd-own", "opp": "sd-opp", "neutral": "sd-neu"}
_LABEL_FALLBACK = {"own": "الموكّل", "opp": "الخصم", "neutral": "محايد"}

CSS = """
:root { --bg:#fbfaf7; --fg:#1d1d1b; --mut:#6b6a66; --line:#e2dfd8; --card:#ffffff; --acc:#2f6f5e; --warn:#b4442f;
  --chip:#eef3f0; --q:#f3f1ea; --str:#2f6f5e; --att:#9a3b26; --ans:#8a6a12; --core:#2f6f5e; --sup:#7a6a3a;
  --meta:#4b5f8a; --bp:#7d7d7a; --rep:#8a4b7a; --x:#5b4b9a;
  --font-ar:"Noto Naskh Arabic","Amiri","Segoe UI",Tahoma,serif; --font-mono:ui-monospace,"Cascadia Mono",Consolas,monospace; }
@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {
  --bg:#1b1c1e; --fg:#e8e6e1; --mut:#a3a19b; --line:#34363a; --card:#232528; --acc:#7cc4ad; --warn:#e08a75;
  --chip:#2a3330; --q:#2b2a26; --str:#7cc4ad; --att:#e08a75; --ans:#d9b45a; --core:#7cc4ad; --sup:#cdb77a;
  --meta:#93a8d6; --bp:#9a9a96; --rep:#cf98c2; --x:#b3a6e8; color-scheme:dark; } }
:root[data-theme="dark"] {
  --bg:#1b1c1e; --fg:#e8e6e1; --mut:#a3a19b; --line:#34363a; --card:#232528; --acc:#7cc4ad; --warn:#e08a75;
  --chip:#2a3330; --q:#2b2a26; --str:#7cc4ad; --att:#e08a75; --ans:#d9b45a; --core:#7cc4ad; --sup:#cdb77a;
  --meta:#93a8d6; --bp:#9a9a96; --rep:#cf98c2; --x:#b3a6e8; color-scheme:dark; }
* { box-sizing:border-box; }
html { -webkit-text-size-adjust:100%; }
body { background:var(--bg); color:var(--fg); font:15px/1.8 var(--font-ar); margin:0; }
.wrap { max-width:1100px; margin:0 auto; padding:20px max(16px, env(safe-area-inset-right, 0px)) 64px max(16px, env(safe-area-inset-left, 0px)); }
h1 { font-size:22px; margin:4px 0; }
h2 { font-size:18px; margin:34px 0 4px; border-top:2px solid var(--line); padding-top:14px; overflow-wrap:anywhere; }
h3 { font-size:15px; color:var(--acc); margin:16px 0 4px; }
h4 { font-size:16px; margin:4px 0; }
a { color:var(--acc); }
.meta, .mut { color:var(--mut); }
.meta { font-size:13.5px; }
.mono { font-family:var(--font-mono); font-size:12px; }
.note { background:var(--chip); border-radius:8px; padding:8px 12px; font-size:13.5px; margin:8px 0; }
.chip { background:var(--chip); color:var(--acc); border-radius:6px; padding:1px 8px; font-size:13px; white-space:nowrap; }
.stats { display:flex; flex-wrap:wrap; gap:4px 14px; background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 10px; font-size:14px; margin:6px 0; }
.client { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:8px 12px; margin:6px 0; }
.client ul { margin:4px 0 0; }
.conf { font-size:12px; border-radius:5px; padding:0 6px; border:1px solid currentColor; white-space:nowrap; }
.c-clear { color:var(--str); } .c-likely { color:var(--ans); } .c-unclear { color:var(--warn); }
.card { background:var(--card); border:1px solid var(--line); border-inline-start:4px solid var(--line); border-radius:8px; padding:8px 12px; margin:10px 0; min-width:0; }
.card.k-str { border-inline-start-color:var(--str); } .card.k-att { border-inline-start-color:var(--att); } .card.k-ans { border-inline-start-color:var(--ans); }
.card p { margin:4px 0; }
.ch { display:flex; flex-wrap:wrap; gap:6px; align-items:center; font-size:13px; }
.n { font-weight:700; }
.kind { font-weight:700; border-radius:5px; padding:0 7px; color:var(--bg); }
.kind.k-str { background:var(--str); } .kind.k-att { background:var(--att); } .kind.k-ans { background:var(--ans); }
.t-str { color:var(--str); } .t-att { color:var(--att); } .t-ans { color:var(--ans); }
.str { font-size:12px; border-radius:5px; padding:0 6px; background:var(--chip); }
.s-strong { font-weight:700; }
.xdoc { font-size:12px; font-weight:700; border-radius:5px; padding:0 7px; color:var(--x); border:1px solid var(--x); white-space:nowrap; }
.q { background:var(--q); border-radius:6px; padding:4px 8px; margin:6px 0; }
.weak { color:var(--mut); font-size:14px; }
details.box { background:var(--card); border:1px solid var(--line); border-radius:8px; padding:6px 10px; margin:6px 0; }
summary { cursor:pointer; }
ul.secs { list-style:none; padding:0; margin:4px 0; }
ul.secs > li { margin:2px 0; }
ul.secs details > summary { list-style-position:inside; }
ol, ul { margin:4px 0; padding-inline-start:22px; }
li { margin:2px 0; }
.sid { font-family:var(--font-mono); font-size:12.5px; color:var(--acc); white-space:nowrap; }
.dt { color:var(--mut); font-size:13px; }
.v { font-size:11.5px; border-radius:4px; padding:0 5px; border:1px solid currentColor; white-space:nowrap; }
.v-core { color:var(--core); } .v-supporting { color:var(--sup); } .v-metadata { color:var(--meta); } .v-boilerplate { color:var(--bp); } .v-repeat { color:var(--rep); }
.sd { font-size:11.5px; border-radius:4px; padding:0 5px; color:var(--bg); white-space:nowrap; }
.sd-own { background:var(--str); } .sd-opp { background:var(--att); } .sd-neu { background:var(--bp); } .sd-err { background:var(--warn); }
.g { font-size:12.5px; font-weight:700; }
.g-contradiction { color:var(--att); } .g-missing { color:var(--meta); } .g-unanswered { color:var(--ans); }
.flag { color:var(--warn); font-size:12px; border:1px solid var(--warn); border-radius:5px; padding:0 5px; }
.warn { color:var(--warn); }
.st { border-inline-start:3px solid var(--line); background:var(--bg); border-radius:4px; padding:4px 10px; margin:4px 0 8px; }
.st p { margin:2px 0; font-size:14px; overflow-wrap:anywhere; }
.st .pn { font-family:var(--font-mono); font-size:11.5px; color:var(--mut); font-weight:400; }
.st .raw { white-space:pre-wrap; }
.said { white-space:pre-wrap; font-size:14px; overflow-wrap:anywhere; }
.tw { overflow-x:auto; -webkit-overflow-scrolling:touch; }
table { border-collapse:collapse; width:100%; font-size:13.5px; }
td, th { border-bottom:1px solid var(--line); padding:5px 7px; text-align:start; vertical-align:top; }
th { color:var(--mut); font-weight:600; white-space:nowrap; }
td.num { white-space:nowrap; font-variant-numeric:tabular-nums; }
tr.out td { color:var(--mut); }
bdi.ltr { direction:ltr; unicode-bidi:isolate; }
:focus-visible { outline:2px solid var(--acc); outline-offset:2px; }
@media (max-width:640px) { body { font-size:14.5px; } h1 { font-size:19px; } h2 { font-size:16.5px; } .card { padding:8px 10px; } }
"""


def _ltr(text: Any) -> str:
    return f"<bdi class='ltr' dir='ltr'>{E(str(text))}</bdi>"


def _usd(x: float, nd: int = 4) -> str:
    return _ltr(f"${x:.{nd}f}")


def _side_txt(side: str) -> str:
    return f"{E(SIDE_AR.get(side, side))} {_ltr(side)}" if side else ""


class _GroupView:
    """One group's page context: sections by ref across its documents, each
    document's paragraphs, the client."""

    def __init__(self, g: dict) -> None:
        self.g = g
        self.index = _index(g)
        self.docs = {d["doc_index"]: d for d in _members(g)}
        self.paras: dict[int, Any] = {}
        for n, d in self.docs.items():
            seg_map = d.get("seg_map") or {}
            text = d.get("content_md") or ""
            if (seg_map.get("paragraph_rule_version") == PARAGRAPH_RULE_VERSION
                    and int(seg_map.get("content_chars") or -1) == len(text)):
                self.paras[n] = paragraphize(text, doc_type=d.get("doc_type") or None)
        self.client = _client(g)

    def doc_name(self, n: int | None) -> str:
        d = self.docs.get(n) if n else None
        return (d.get("title") or d.get("wi") or format_doc(n)) if d else (format_doc(n) if n else "؟")

    def label(self, ref: str, angle: dict | None) -> str:
        """The الموكّل / الخصم / محايد chip of a section: relative to the angle's side,
        or to the client's — in THAT section's document (``client.by_document``)."""
        section = resolve_ref(self.index, ref)
        if section is None:
            return ""
        try:
            me, party = angle_viewpoint(angle or {}, self.client, ref)
            if me is None:
                return ""
            key = str(relative_label(section, me, party))
        except Exception as exc:  # noqa: BLE001 — show the failure, do not hide it as neutral
            return f"<span class='sd sd-err' title='{E(type(exc).__name__)}'>؟</span> "
        text = LABEL_AR.get(key) if isinstance(LABEL_AR, dict) else None
        return f"<span class='sd {LABEL_CLASS.get(key, 'sd-neu')}'>{E(text or _LABEL_FALLBACK.get(key, key))}</span> "

    def text(self, ref: str) -> str:
        """The section's own words — numbered paragraphs (its document's numbering)
        when the map's rule still holds, else the raw char range of ``content_md``."""
        s = self.index[ref]
        d = self.docs.get(s.get("doc"))
        content = (d or {}).get("content_md") or ""
        fp, tp = int(s.get("from_p") or 0), int(s.get("to_p") or 0)
        cs, ce = int(s.get("char_start") or 0), int(s.get("char_end") or 0)
        ps = self.paras.get(s.get("doc"))
        if ps and 1 <= fp <= tp <= len(ps) and ps[fp - 1].char_start >= cs and ps[tp - 1].char_end <= ce:
            body = "".join(f"<p><b class='pn'>P{p:03d}</b> {E(ps[p - 1].text)}</p>" for p in range(fp, tp + 1))
        else:
            body = f"<p class='raw'>{E(content[cs:ce])}</p>"
        return f"<div class='st'>{body}</div>"

    def head(self, ref: str, angle: dict | None, *, show_label: bool = True) -> str:
        s = resolve_ref(self.index, ref)
        shown = E(display_ref(ref))
        if s is None:
            return f"<span class='sid warn'>{shown}</span> <span class='warn'>ليس قسماً في مستندات الرسالة</span>"
        owner = E(s.get("owner") or "")
        bb = f" · قدّمه: {E(s['brought_by'])}" if s.get("brought_by") else ""
        oc = f" {_ltr(s['owner_class'])}" if s.get("owner_class") else ""
        value = str(s.get("value") or "")
        return (
            f"{self.label(ref, angle) if show_label else ''}<span class='sid'>{shown}</span> "
            f"<span class='dt'>— {E(self.doc_name(s.get('doc')))}</span> "
            f"<span class='v v-{E(value)}'>{E(VAL_AR.get(value, value))}</span> {E(s.get('what') or '')} "
            f"<span class='mut'>— {owner}{bb}{oc}</span>"
        )

    def sec_item(self, ref: str, angle: dict | None, *, show_label: bool = True, extra: str = "") -> str:
        """One section line; its text opens on click."""
        if ref not in self.index:
            return f"<li>{self.head(ref, angle)}{extra}</li>"
        return (f"<li><details><summary>{self.head(ref, angle, show_label=show_label)}{extra}</summary>"
                f"{self.text(ref)}</details></li>")


def _angle_card(view: _GroupView, i: int, a: dict) -> str:
    kind = str(a.get("kind") or "")
    k_ar, k_cls = KIND_AR.get(kind, (kind, ""))
    strength = str(a.get("strength") or "")
    for_side = str(a.get("for_side") or "")
    side_txt = f" {_ltr(for_side)}" if for_side else ""
    docs = _angle_docs(a)
    cross = (f"<span class='xdoc' title='{E('، '.join(format_doc(d) for d in docs))}'>يربط بين المستندات</span>"
             if len(docs) > 1 else "")
    ocr = f"<span class='flag'>{OCR_SUSPECT_AR}</span> " if a.get("ocr_suspect") is True else ""
    secs = "".join(view.sec_item(r, a) for r in _refs(a.get("sections"))) or "<li class='mut'>—</li>"
    return (
        f"<article class='card {k_cls}'><div class='ch'><span class='n'>{i}</span> <span class='kind {k_cls}'>{E(k_ar)}</span> "
        f"<span class='str s-{E(strength)}'>{E(STRENGTH_AR.get(strength, strength or '—'))}</span> {ocr}{cross} "
        f"<span class='mut'>لصالح: {E(a.get('for_party') or '')}{side_txt}</span></div>"
        f"<h4>{E(a.get('title') or '')}</h4><p>{E(a.get('argument') or '')}</p>"
        f"<div class='q'><b>سؤال البحث:</b> {E(a.get('legal_question') or '')}</div>"
        + (f"<p class='weak'><b>ما يضعفه:</b> {E(a['weakness'])}</p>" if a.get("weakness") else "")
        + "<div><b>الأقسام التي يدور عليها</b> <span class='mut'>(الصفة من مالك القسم في التقسيم، بمفتاح الموكّل "
          "في مستند القسم · انقر القسم لنصه)</span>"
        f"<ul class='secs'>{secs}</ul></div></article>"
    )


def _documents_table(g: dict) -> str:
    rows = []
    for d in _members(g):
        segs = d.get("segments") or []
        core = sum(1 for s in segs if s.get("value") == "core")
        dt = d.get("doc_type") or ""
        seg = (d.get("seg_map") or {}).get("prompt_version") or "—"
        rows.append(
            f"<tr><td class='num'><b>{_ltr(format_doc(d['doc_index']))}</b></td><td>{E(d.get('title') or '')}</td>"
            f"<td>{E(TYPE_AR.get(dt, dt or 'غير محدد'))}</td><td class='num'>{d.get('chars', 0):,}</td>"
            f"<td class='num'>{len(segs)} ({core} جوهري)</td><td class='num'>{_ltr(seg)}</td>"
            f"<td class='num'>{_ltr(d['item_id'][:8])} {E(d.get('wi') or '')}</td></tr>"
        )
    for d in _left_out(g):
        reason = str(d.get("excluded") or "")
        rows.append(
            f"<tr class='out'><td class='num'>—</td><td>{E(d.get('title') or '')}</td>"
            f"<td colspan='4'><span class='warn'>خارج المجموعة:</span> {E(REASON_AR.get(reason, reason or '—'))} "
            f"{_ltr(reason) if reason else ''}</td><td class='num'>{_ltr(d['item_id'][:8])} {E(d.get('wi') or '')}</td></tr>"
        )
    return ("<div class='tw'><table><thead><tr><th>المستند</th><th>العنوان</th><th>النوع</th><th>الأحرف</th>"
            "<th>الأقسام</th><th>التقسيم</th><th>المعرّف</th></tr></thead>"
            f"<tbody>{''.join(rows) or '<tr><td colspan=7 class=mut>لا مستندات</td></tr>'}</tbody></table></div>")


def _client_box(view: _GroupView) -> str:
    c = view.client
    if not c:
        return ""
    side = str(c.get("side") or "")
    conf = str(c.get("confidence") or "")
    side_txt = f" · {_side_txt(side)}" if side else ""
    known = side and side != "unknown"
    per_doc = []
    for e in c.get("by_document") or []:
        if not isinstance(e, dict):
            continue
        n = _doc_num(e.get("document"))
        per_doc.append(
            f"<li>في {_ltr(e.get('document') or '')} <span class='dt'>— {E(view.doc_name(n))}</span>: "
            f"{E(e.get('party') or '—')} · {_side_txt(str(e.get('side') or ''))}</li>"
        )
    return (
        f"<div class='client'><b>الموكّل:</b> {E(c.get('party') or '—')}{side_txt} "
        f"<span class='conf c-{E(conf)}'>{E(CONF_AR.get(conf, conf or '—'))}</span>"
        + ("" if known else " <span class='mut'>— الجانب غير محدد: كل وجه يُقرأ من جهة جانبه</span>")
        + f"<div class='mut'>{E(c.get('basis') or '')}</div>"
        + (f"<div><b>صفته في المستندات الأخرى</b> <span class='mut'>(مفاتيح الملّاك نسبية إلى إجراءات كل "
           f"مستند)</span><ul>{''.join(per_doc)}</ul></div>" if per_doc else "")
        + "</div>"
    )


def _group_title(g: dict) -> str:
    if g.get("message_id"):
        return f"رسالة {_ltr(str(g['message_id'])[:8])}"
    return f"ملف بلا رسالة {_ltr(g['key'].split(':', 1)[1][:8])}"


def _group_block(g: dict, idx: int) -> str:
    view = _GroupView(g)
    res = _res(g)
    st = _stats(g)
    out = _out(g)
    cov = _coverage(g)
    bad = _bad_refs(g)
    kc = _kind_counts(g)
    angles = _angles(g)
    members = _members(g)
    cross = sum(1 for a in angles if _cross(a))
    h = [f"<section id='g{idx}'><h2><span class='chip'>{len(members)} مستند</span> {_group_title(g)}</h2>"]
    secs = _secs(g)
    facts = [
        f"{_total_chars(g):,} حرف",
        f"{len(view.index)} قسم ({len(cov['core'])} جوهري)" if view.index else "<span class='warn'>لا مستند بخريطة تقسيم صالحة</span>",
        f"{len(_left_out(g))} خارج المجموعة" if _left_out(g) else "",
        f"{secs:.0f} ث" if secs is not None else "",
        _usd(_cost(g)),
        f"النتيجة {_ltr(res.get('outcome'))}",
    ]
    h.append(f"<p class='meta'>{' · '.join(f for f in facts if f)}</p>")
    tech = [
        f"model {st.get('model') or '—'}", f"prompt {st.get('prompt_version') or '—'}",
        f"attempts {st.get('attempts', '—')}", f"repair {st.get('repair_used', '—')}",
        f"tokens {st.get('input_tokens', 0) or 0:,} in · {st.get('output_tokens', 0) or 0:,} out · "
        f"{st.get('reasoning_tokens', 0) or 0:,} reasoning",
        f"segments {', '.join(st.get('segment_prompt_versions') or []) or '—'}",
        f"refs dropped {st.get('invalid_refs_dropped', 0) or 0}",
    ]
    h.append(f"<p class='meta'>{_ltr(' · '.join(tech))}</p>")
    if res.get("error"):
        h.append(f"<p class='note warn'>خطأ: {_ltr(res['error'])}</p>")
    h.append("<h3>المستندات</h3>" + _documents_table(g))
    uw = (g.get("user_words") or "").strip()
    h.append(f"<details class='box'><summary>ما قاله المستخدم حتى الرفع ({len(uw):,} حرف)</summary>"
             f"<div class='said'>{E(uw) or 'لا شيء — رُفعت الملفات بلا رسالة'}</div></details>")
    h.append(_client_box(view))
    unpl = f" · <b class='warn'>{len(cov['unplaced'])}</b> بلا مكان" if cov["unplaced"] else " · <b>0</b> بلا مكان"
    h.append(
        f"<div class='stats'><span><b>{len(angles)}</b> وجه</span>"
        + "".join(f"<span class='t-{KIND_AR[k][1][2:]}'>{KIND_AR[k][0]}: <b>{kc[k]}</b></span>" for k in KINDS)
        + f"<span>قوية: <b>{sum(1 for a in angles if a.get('strength') == 'strong')}</b></span>"
        + f"<span>يربط بين المستندات: <b>{cross}</b></span>"
        + f"<span>الأقسام الجوهرية: <b>{len(cov['angle'])}</b> في وجه · <b>{len(cov['context'])}</b> سياق · "
          f"<b>{len(cov['no_angle'])}</b> بلا وجه{unpl}</span>"
        + (f"<span class='warn'>مراجع أقسام خاطئة: {E('، '.join(display_ref(r) for r in bad))}</span>" if bad else "")
        + "</div>"
    )
    if angles:
        h.append("<h3>الأوجه</h3>")
        h.extend(_angle_card(view, i, a) for i, a in enumerate(angles, 1))
    elif res.get("outcome") in OK_OUTCOMES:
        h.append("<p class='note'>لا أوجه في مستندات هذه الرسالة.</p>")
    unrelated = [u for u in out.get("unrelated_documents") or [] if isinstance(u, dict)]
    if unrelated:
        items = "".join(
            f"<li><b>{_ltr(u.get('document') or '')}</b> <span class='dt'>— "
            f"{E(view.doc_name(_doc_num(u.get('document'))))}</span>: {E(u.get('why') or '')}</li>"
            for u in unrelated
        )
        h.append(f"<details class='box' open><summary>مستندات من قضية أخرى ({len(unrelated)})</summary>"
                 f"<ul>{items}</ul></details>")
    ctx = _refs(out.get("context"))
    if ctx:
        items = "".join(view.sec_item(r, None) for r in ctx)
        h.append(f"<details class='box'><summary>سياق يبقى أمام كل بحث ({len(ctx)})</summary><ul class='secs'>{items}</ul></details>")
    gaps = [x for x in out.get("gaps") or [] if isinstance(x, dict)]
    if gaps:
        gl = "".join(
            f"<li><b class='g g-{E(str(x.get('kind') or ''))}'>{E(GAP_AR.get(str(x.get('kind')), str(x.get('kind') or '')))}</b> "
            f"<span class='sid'>{E('، '.join(display_ref(r) for r in _refs(x.get('sections'))))}</span>"
            + (" <span class='xdoc'>بين المستندات</span>" if len({doc_of(r) for r in _refs(x.get('sections'))} - {None}) > 1 else "")
            + (" <span class='flag'>قراءة OCR؟</span>" if x.get("ocr_suspect") else "")
            + f" {E(x.get('issue') or '')}</li>"
            for x in gaps
        )
        h.append(f"<details class='box' open><summary>الفجوات ({len(gaps)})</summary><ul>{gl}</ul></details>")
    no_angle = [(canonical_ref(n.get("section")), n) for n in out.get("no_angle") or [] if isinstance(n, dict)]
    unplaced = set(_refs(res.get("unplaced")))
    if no_angle:
        items = "".join(
            view.sec_item(r, None, show_label=False,
                          extra=f" <span class='mut'>· {E(n.get('why') or '')}</span>"
                                + (" <span class='flag'>لم يُصنَّف</span>" if r in unplaced else ""))
            for r, n in no_angle
        )
        h.append(f"<details class='box'><summary>أقسام جوهرية بلا وجه ({len(no_angle)})</summary><ul class='secs'>{items}</ul></details>")
    if unplaced:
        items = "".join(view.sec_item(r, None, show_label=False) for r in _refs(res.get("unplaced")))
        h.append(f"<details class='box' open><summary class='warn'>لم يضعها النموذج بعد الإصلاح — أُضيفت «لم يُصنَّف» "
                 f"({len(unplaced)})</summary><ul class='secs'>{items}</ul></details>")
    lost = [r for r in cov["unplaced"] if r not in {x for x, _ in no_angle}]
    if lost:  # core sections found nowhere at all (not even appended to no_angle)
        items = "".join(view.sec_item(r, None, show_label=False) for r in lost)
        h.append(f"<details class='box' open><summary class='warn'>أقسام جوهرية لا مكان لها ({len(lost)})</summary><ul class='secs'>{items}</ul></details>")
    for n, d in view.docs.items():
        refs = [r for r, s in view.index.items() if s.get("doc") == n]
        if refs:
            items = "".join(view.sec_item(r, None) for r in refs)
            h.append(f"<details class='box'><summary>كل أقسام {_ltr(format_doc(n))} — {E(d.get('title') or '')} "
                     f"({len(refs)})</summary><ul class='secs'>{items}</ul></details>")
    return "".join(h) + "</section>"


def _overview_row(g: dict, idx: int) -> str:
    res = _res(g)
    angles = _angles(g)
    c = _client(g)
    side = str(c.get("side") or "")
    conf = str(c.get("confidence") or "")
    kc = _kind_counts(g)
    cov = _coverage(g)
    secs = _secs(g)
    outcome = str(res.get("outcome") or "")
    cov_txt = f"{len(cov['angle'])}/{len(cov['context'])}/{len(cov['no_angle'])}/" + (
        f"<b class='warn'>{len(cov['unplaced'])}</b>" if cov["unplaced"] else "0"
    )
    client_txt = (f"{E(SIDE_AR.get(side, side))} <span class='conf c-{E(conf)}'>{E(CONF_AR.get(conf, conf))}</span>"
                  if side else "—")
    by_doc = len(c.get("by_document") or [])
    out_n = len(_left_out(g))
    return (
        f"<tr><td><a href='#g{idx}'>{_group_title(g)}</a></td>"
        f"<td class='num'>{len(_members(g))}" + (f" <span class='mut'>+{out_n} خارج</span>" if out_n else "") + "</td>"
        f"<td class='num'>{_total_chars(g):,}</td>"
        f"<td>{client_txt}" + (f" <span class='mut'>+{by_doc} صفة</span>" if by_doc else "") + "</td>"
        f"<td class='num'>{len(angles)} <span class='mut'>({kc['strengthen']} · {kc['attack']} · {kc['answer']})</span></td>"
        f"<td class='num'>{sum(1 for a in angles if _cross(a))}</td>"
        f"<td class='num'>{cov_txt} <span class='mut'>من {len(cov['core'])}</span></td>"
        f"<td class='num'>{f'{secs:.0f} ث' if secs is not None else '—'}</td>"
        f"<td class='num'>{_usd(_cost(g))}</td>"
        f"<td class='{'' if outcome in OK_OUTCOMES else 'warn'}'>{_ltr(outcome)}</td></tr>"
    )


def render(data: dict) -> str:
    groups = data.get("groups") or []
    total = sum(_cost(g) for g in groups)
    slow = max((_secs(g) or 0.0 for g in groups), default=0.0)
    ok = sum(1 for g in groups if _res(g).get("outcome") in OK_OUTCOMES)
    rows = "".join(_overview_row(g, i) for i, g in enumerate(groups))
    body = "".join(_group_block(g, i) for i, g in enumerate(groups))
    return f"""<!doctype html>
<html lang="ar" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>Angle Map Preview</title>
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Noto+Naskh+Arabic:wght@400;600;700&display=swap">
<style>{CSS}</style></head><body><div class="wrap">
<h1>خريطة الأوجه — معاينة لكل رسالة</h1>
<p class="meta">الملفات التي يرسلها المستخدم في رسالة واحدة تُقرأ معاً في نداء واحد (رسالة بملف واحد = مجموعة من ملف واحد)، ويُشار إلى كل قسم بمستنده: {_ltr('D2·S7')} = القسم 7 من المستند الثاني.
الوجه حجة تستحق بحثاً: <span class="t-str">يقوّي</span> موقف الموكّل، أو <span class="t-att">يطعن</span> في الخصم أو في دليل يخدمه، أو نقطة قوة للخصم <span class="t-ans">يجب الرد</span> عليها؛ و<span class="xdoc">يربط بين المستندات</span> حين تأتي أقسامه من أكثر من مستند.
صفة كل قسم (الموكّل / الخصم / محايد) يحسبها الكود من مالك القسم في التقسيم، نسبةً إلى جانب الموكّل في مستند ذلك القسم — أو إلى جانب الوجه حين يكون جانب الموكّل غير محدد.</p>
<p class="meta">التعليمات {_ltr(data.get('prompt_version') or '—')} · الفقرات {_ltr(data.get('paragraph_rule_version') or '—')} · {_ltr(data.get('generated_at') or '')} ·
{len(groups)} مجموعة ({ok} ناجحة) · المجموع {_usd(total)} · أبطأ مجموعة {slow:.0f} ث · الوقت الكلي {data.get('wall_s', 0)} ث</p>
<p class="note">معاينة فقط: نموذج الأوجه شُغّل فعلاً (بما قرأه مقنّعاً وبكلام المستخدم حتى الرفع)، لكن لم يُخزَّن شيء ولم يُحتسب على أي مستخدم. هذه الصفحة تعرض النص الحقيقي ومستندات عملاء — تبقى محلية.</p>
<div class="tw"><table><thead><tr><th>المجموعة</th><th>مستندات</th><th>الأحرف</th><th>الموكّل</th><th>أوجه (يقوّي · يطعن · رد)</th><th>بين المستندات</th><th>جوهري: وجه/سياق/بلا وجه/بلا مكان</th><th>الوقت</th><th>التكلفة</th><th>النتيجة</th></tr></thead>
<tbody>{rows}</tbody></table></div>
{body}
</div></body></html>"""


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _in_repo(path: Path) -> Path | None:
    """The git work tree ``path`` sits in (a ``.git`` dir or file above it), if any."""
    for p in (path, *path.parents):
        if (p / ".git").exists():
            return p
    return None


def _outside_repo(path: str) -> Path:
    """The output holds customer documents: refuse any folder inside a repo."""
    out = Path(path).resolve()
    if out == ROOT or ROOT in out.parents:
        raise SystemExit(f"--out must be OUTSIDE the repo ({ROOT}); use the scratchpad")
    repo = _in_repo(out)
    if repo is not None:
        raise SystemExit(f"--out must be OUTSIDE every git repository ({repo} is one); use the scratchpad")
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--message", action="append", default=[], help="message id or id prefix (≥ 6 hex); repeatable")
    ap.add_argument("--item", action="append", default=[], help="item id or prefix: that item's message group; repeatable")
    ap.add_argument("--conversation", action="append", default=[], help="every message group of this conversation")
    ap.add_argument("--out", required=True, help="a folder OUTSIDE every repo (output holds customer documents)")
    ap.add_argument("--concurrency", type=int, default=2, help="groups mapped at once (default 2)")
    ap.add_argument("--from-json", default="", help="re-render a saved run (no model call)")
    args = ap.parse_args(argv)
    out = _outside_repo(args.out)  # before any model call: a refused --out must not cost a run

    if args.from_json:
        data = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        out.mkdir(parents=True, exist_ok=True)
        (out / HTML_NAME).write_text(render(data), encoding="utf-8")
        print(f"written {out / HTML_NAME}")
        return

    from shared.db.client import get_supabase_client

    sb = get_supabase_client()
    groups = select_groups(sb, messages=args.message, items=args.item, conversations=args.conversation)
    if not groups:
        raise SystemExit("give --message, --item, --conversation or --from-json (nothing selected)")
    _load_prices(sb)
    data = asyncio.run(collect(sb, groups, concurrency=args.concurrency))
    out.mkdir(parents=True, exist_ok=True)
    (out / JSON_NAME).write_text(json.dumps(data, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    (out / HTML_NAME).write_text(render(data), encoding="utf-8")
    total = sum(_cost(g) for g in data["groups"])
    ok = sum(1 for g in data["groups"] if _res(g).get("outcome") in OK_OUTCOMES)
    print(f"total ${total:.4f} over {len(data['groups'])} groups ({ok} ok) · wall {data['wall_s']} s")
    print(f"written {out / HTML_NAME}")


if __name__ == "__main__":
    main()
