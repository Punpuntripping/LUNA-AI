"""Build fixtures for the angle-map UI preview (`/dev-preview/angle-map`).

DEV ONLY. Reads one or more `angle_map_preview.json` files written by
`scripts/angle_map_preview.py` (real users' documents — they live OUTSIDE every
repo) and writes, to a folder that must also be outside every repo:

    index.json            the groups, for the harness's picker
    <message8>.json       one group: the files as `GET /files/{item_id}` would
                          return them + the map as the proposed
                          `GET /files/{item_id}/angle-maps` would return it

The section labels (الموكّل / الخصم / محايد) are computed with the backend's own
`agents.memory.angle_mapper.labels`, exactly as the preview page does. A section's
text is its char range of the document's `content_md` (the segment rows carry
offsets, not text).

Run from the repo root:
    .venv/Scripts/python.exe scripts/angle_map_ui_fixture.py \
        <preview_v5/angle_map_preview.json> [<preview_v4/...json> ...] --out <dir>
Earlier inputs win when two files hold the same message.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.memory.angle_mapper.labels import (  # noqa: E402
    angle_viewpoint,
    relative_label,
    resolve_ref,
    section_index,
)

NAME_RE = re.compile(r"^[a-z0-9_-]+$")


def _inside_a_repo(path: Path) -> bool:
    for parent in [path, *path.parents]:
        if (parent / ".git").exists():
            return True
    return False


def _wi_seq(wi: Any) -> int | None:
    m = re.match(r"WI-(\d+)$", str(wi or ""))
    return int(m.group(1)) if m else None


def _int_or_none(v: Any) -> int | None:
    try:
        return int(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _doc_number(ref: str) -> int:
    m = re.match(r"D(\d+)\.S(\d+)$", ref)
    return int(m.group(1)) if m else 0


def _seq_number(ref: str) -> int:
    m = re.match(r"D(\d+)\.S(\d+)$", ref)
    return int(m.group(2)) if m else 0


def _file_detail(doc: dict, conversation_id: str, created_at: str) -> dict:
    """One document as `GET /files/{item_id}` returns it (LibraryFileDetail)."""
    content = doc.get("content_md") or ""
    seg_map = doc.get("seg_map") or {}
    in_file = seg_map.get("documents_in_file") or []
    segments = doc.get("segments") or []

    def doc_index_of(from_p: Any) -> int | None:
        if len(in_file) < 2:
            return None
        p = _int_or_none(from_p) or 0
        for i, d in enumerate(in_file):
            if (_int_or_none(d.get("from_p")) or 0) <= p <= (_int_or_none(d.get("to_p")) or 0):
                return i
        return None

    sections = [
        {
            "seq": s.get("seq"),
            "what": s.get("what") or "",
            "is_metadata": s.get("value") == "metadata",
            "page_from": _int_or_none(s.get("page_from")),
            "page_to": _int_or_none(s.get("page_to")),
            "document_index": doc_index_of(s.get("from_p")),
            "content_md": content[int(s.get("char_start") or 0): int(s.get("char_end") or 0)],
            "owner_class": s.get("owner_class") or None,
            "owner": s.get("owner") or None,
            "brought_by": s.get("brought_by") or None,
        }
        for s in segments
    ]
    pages = [p for p in (_int_or_none(s.get("page_to")) for s in segments) if p]
    title = doc.get("title") or doc.get("wi") or "مستند"
    return {
        "item_id": doc["item_id"],
        "title": title,
        "filename": title,
        "mime_type": "application/pdf",
        "doc_type": doc.get("doc_type") or None,
        "summary_preview": "",
        "page_count": max(pages) if pages else None,
        "chars": len(content),
        "has_sections": bool(sections),
        "original_available": False,
        "original_purged": False,
        "conversation_id": conversation_id,
        "wi_seq": _wi_seq(doc.get("wi")),
        "copies": 1,
        "created_at": seg_map.get("created_at") or created_at,
        "reattach_advised": False,
        "summary": None,
        "content_md": content,
        "documents": [{"name": d.get("name") or ""} for d in in_file] if len(in_file) > 1 else [],
        "sections": sections or None,
    }


def _label(index: dict, ref: str, angle: dict | None, client: dict) -> str | None:
    section = resolve_ref(index, ref)
    if section is None:
        return None
    me, party = angle_viewpoint(angle or {}, client, ref)
    if me is None:
        return None
    return str(relative_label(section, me, party))


def _refs(v: Any) -> list[str]:
    return [str(r) for r in (v or []) if isinstance(r, str) and r]


def _group(g: dict, created_at: str, prompt_version: str) -> dict | None:
    output = ((g.get("result") or {}).get("output")) or None
    if not output:
        return None
    message_id = str(g.get("message_id") or "")
    # Single-file groups made before the message grouping are keyed "item:<uuid>".
    key = (message_id or str(g.get("key") or "").split(":")[-1])[:8].lower()
    if not NAME_RE.match(key):
        return None
    conversation_id = str(g.get("conversation_id") or "")

    docs = g.get("documents") or []
    members = [d for d in docs if d.get("doc_index") and not d.get("excluded")]
    index = section_index(
        [{"doc_index": d["doc_index"], "item_id": d["item_id"]} for d in members],
        {d["item_id"]: {"segments": d.get("segments") or []} for d in members},
    )
    client = output.get("client") or {}

    angles_in = output.get("angles") or []
    gaps_in = output.get("gaps") or []
    used: list[str] = []
    for a in angles_in:
        used.extend(_refs(a.get("sections")))
    for x in gaps_in:
        used.extend(_refs(x.get("sections")))
    used = [r for r in dict.fromkeys(used) if r in index]

    by_number = {int(d["doc_index"]): d for d in members}
    sections = []
    for ref in used:
        s = index[ref]
        doc = by_number.get(_doc_number(ref)) or {}
        content = doc.get("content_md") or ""
        sections.append({
            "ref": ref,
            "document": _doc_number(ref),
            "seq": _seq_number(ref),
            "what": s.get("what") or "",
            "value": s.get("value") or "",
            "owner_class": s.get("owner_class") or None,
            "owner": s.get("owner") or None,
            "brought_by": s.get("brought_by") or None,
            "page_from": _int_or_none(s.get("page_from")),
            "page_to": _int_or_none(s.get("page_to")),
            "side": _label(index, ref, None, client),
            "content_md": content[int(s.get("char_start") or 0): int(s.get("char_end") or 0)],
        })

    angles = []
    for i, a in enumerate(angles_in, start=1):
        refs = [r for r in dict.fromkeys(_refs(a.get("sections"))) if r in index]
        angles.append({
            "seq": i,
            "kind": a.get("kind") or "strengthen",
            "strength": a.get("strength") or "medium",
            "for_side": a.get("for_side") or "",
            "for_party": a.get("for_party") or "",
            "title": a.get("title") or "",
            "argument": a.get("argument") or "",
            "legal_question": a.get("legal_question") or "",
            "weakness": a.get("weakness") or "",
            "ocr_suspect": bool(a.get("ocr_suspect")),
            "sections": [{"ref": r, "side": _label(index, r, a, client)} for r in refs],
        })

    gaps = [
        {
            "kind": x.get("kind") or "missing",
            "issue": x.get("issue") or "",
            "ocr_suspect": bool(x.get("ocr_suspect")),
            "sections": [
                {"ref": r, "side": _label(index, r, None, client)}
                for r in dict.fromkeys(_refs(x.get("sections"))) if r in index
            ],
        }
        for x in gaps_in
    ]

    by_doc = []
    for e in client.get("by_document") or []:
        n = _doc_number(f"{e.get('document')}.S1")
        if n:
            by_doc.append({"document": n, "side": e.get("side") or "", "party": e.get("party") or ""})

    documents = [
        {
            "index": int(d["doc_index"]),
            "item_id": d["item_id"],
            "title": d.get("title") or d.get("wi") or "مستند",
            "doc_type": d.get("doc_type") or None,
            "wi_seq": _wi_seq(d.get("wi")),
        }
        for d in sorted(members, key=lambda d: int(d["doc_index"]))
    ]
    left_out = [
        {
            "item_id": d["item_id"],
            "title": d.get("title") or d.get("wi") or "مستند",
            "reason": str(d.get("excluded") or ""),
        }
        for d in docs
        if d.get("excluded")
    ]

    the_map = {
        "map_id": f"preview-{key}",
        "conversation_id": conversation_id or None,
        "conversation_title": f"المحادثة {conversation_id[:8]}" if conversation_id else None,
        "message_id": message_id or None,
        "created_at": created_at,
        "prompt_version": (output.get("prompt_version") or g.get("prompt_version") or prompt_version),
        "client": {
            "side": client.get("side") or "",
            "party": client.get("party") or "",
            "basis": client.get("basis") or "",
            "confidence": client.get("confidence") or "unclear",
            "by_document": by_doc,
        },
        "documents": documents,
        "left_out": left_out,
        "sections": sections,
        "angles": angles,
        "gaps": gaps,
    }
    files = [_file_detail(d, conversation_id, created_at) for d in docs if d.get("content_md")]
    return {
        "key": key,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "prompt_version": the_map["prompt_version"],
        "files": files,
        "map": the_map,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("inputs", nargs="+", type=Path)
    ap.add_argument("--out", required=True, type=Path)
    args = ap.parse_args()

    out = args.out.resolve()
    if _inside_a_repo(out):
        print(f"refusing --out inside a git repo: {out}", file=sys.stderr)
        return 2
    out.mkdir(parents=True, exist_ok=True)

    seen: set[str] = set()
    index_rows = []
    for path in args.inputs:
        data = json.loads(path.read_text(encoding="utf-8"))
        created_at = data.get("generated_at") or ""
        prompt_version = data.get("prompt_version") or ""
        for g in data.get("groups") or []:
            group = _group(g, created_at, prompt_version)
            if group is None or group["key"] in seen:
                continue
            seen.add(group["key"])
            (out / f"{group['key']}.json").write_text(
                json.dumps(group, ensure_ascii=False), encoding="utf-8"
            )
            m = group["map"]
            kinds = {k: sum(1 for a in m["angles"] if a["kind"] == k) for k in ("strengthen", "attack", "answer")}
            index_rows.append({
                "key": group["key"],
                "prompt_version": group["prompt_version"],
                "documents": len(m["documents"]),
                "angles": len(m["angles"]),
                "kinds": kinds,
                "gaps": len(m["gaps"]),
                "client_confidence": m["client"]["confidence"],
                "first_title": m["documents"][0]["title"] if m["documents"] else "",
                "source": path.parent.name,
            })
    (out / "index.json").write_text(json.dumps({"groups": index_rows}, ensure_ascii=False), encoding="utf-8")
    print(f"{len(index_rows)} groups -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
