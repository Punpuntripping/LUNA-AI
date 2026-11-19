"""Type backfill for old attachments — one cheap call per 10 files, from their summaries.

Plan: ``.claude/plans/my_files.md`` §6 (owner, 2026-11-19: "one agent … 1 for every 10
docs … instead of wasting money"). Files uploaded before 2026-10-27 have no
``metadata.doc_type``, so «الملفات» cannot arrange them by type. This labels them WITHOUT
re-reading the documents: the model sees each file's title and its stored summary (or, when
there is no summary, the first characters of its text) and returns one type per file, ten
files per call.

The labels and their definitions are taken from the attachment summarizer's own prompt, so
a type means exactly what it means for new uploads. The model is the summarizer's
(``get_agent_model("artifact_summarizer")``, tier_2). What the model reads is masked the way
``agents/memory/summarize.py`` masks it (per-user codec; the fakes are kept in memory only).
No ledger scope is opened: nothing is charged to any user.

Modes (cwd = repo root)::

    python scripts/my_files_type_backfill.py --validate 120      # accuracy on typed files
    python scripts/my_files_type_backfill.py --out DIR           # dry run on untyped files
    python scripts/my_files_type_backfill.py --apply --yes       # write metadata.doc_type
    python scripts/my_files_type_backfill.py --apply --yes --from-json FILE   # write a reviewed dry run

``--validate N`` takes N files that ALREADY have a type and a summary, labels them from the
summary alone, and reports how often the label equals the stored one. It never writes.
The default is a dry run: it prints counts and, with ``--out`` (a folder outside every git
repository), saves ``{item_id: doc_type}``. Only ``--apply --yes`` writes, and only
``metadata.doc_type`` on rows that still have none.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pydantic import BaseModel, Field  # noqa: E402
from pydantic_ai import Agent  # noqa: E402
from pydantic_ai.usage import UsageLimits  # noqa: E402

from agents.memory.artifact_summarizer.models import DOC_TYPES, DocType  # noqa: E402
from agents.memory.artifact_summarizer.prompts import SYSTEM_PROMPT_ATTACHMENT_AR  # noqa: E402
from agents.memory.summarize import _enc, _summarize_codec  # noqa: E402
from agents.utils.agent_models import get_agent_model  # noqa: E402
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP  # noqa: E402
from shared.files import store as files_store  # noqa: E402

BATCH = 10
SUMMARY_CAP = 3_000
TEXT_HEAD = 1_500
_PAGE = 200

_START = "### 4) Document type (`doc_type`)"
_END = "## Style"


def type_definitions() -> str:
    """The summarizer prompt's own type list, so both label by one definition."""
    a = SYSTEM_PROMPT_ATTACHMENT_AR.find(_START)
    b = SYSTEM_PROMPT_ATTACHMENT_AR.find(_END, a)
    if a < 0 or b < 0:
        raise SystemExit("the summarizer prompt no longer has its doc_type section — update this script")
    return SYSTEM_PROMPT_ATTACHMENT_AR[a + len(_START):b].strip()


INSTRUCTIONS = (
    "You label uploaded legal documents by type for the Luna legal system. Each numbered "
    "entry below is ONE document: its title and its summary (when a document has no summary "
    "you get the beginning of its extracted text instead). Give every entry exactly one "
    "label from the list. Judge only from what the entry says; never guess a type the entry "
    "does not support — use `other` then.\n\n"
    "## Labels\n" + type_definitions() + "\n\n"
    "## Output\n"
    "Return `results`: one item per entry, with `n` (the entry's number) and `doc_type` "
    "(one label from the list, exactly as written). Every number exactly once, nothing else."
)


class TypeOf(BaseModel):
    n: int
    doc_type: DocType


class BatchTypes(BaseModel):
    results: list[TypeOf] = Field(default_factory=list)


def build_agent() -> Agent[None, BatchTypes]:
    return Agent(
        get_agent_model("artifact_summarizer"),
        name="my_files_type_backfill",
        output_type=BatchTypes,
        instructions=INSTRUCTIONS,
        retries=1,
    )


LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=2)


def _inside_git(path: Path) -> bool:
    p = path.resolve()
    return any((q / ".git").exists() for q in [p, *p.parents])


def fetch_rows(sb: Any, *, typed: bool, user_id: str | None) -> list[dict]:
    """Live attachments with text; ``typed`` picks those with / without a doc_type."""
    out: list[dict] = []
    start = 0
    while True:
        q = (
            sb.table("workspace_items")
            .select("item_id, user_id, title, summary, metadata")
            .eq("kind", "attachment")
            .is_("deleted_at", "null")
            .not_.is_("content_md", "null")
            .neq("content_md", "")
            .order("created_at")
            .range(start, start + _PAGE - 1)
        )
        if user_id:
            q = q.eq("user_id", user_id)
        rows = q.execute().data or []
        for r in rows:
            has = bool((r.get("metadata") or {}).get("doc_type"))
            if has == typed:
                out.append(r)
        if len(rows) < _PAGE:
            return out
        start += _PAGE


def text_heads(sb: Any, ids: list[str]) -> dict[str, str]:
    """First characters of the text for files that have no summary (few)."""
    heads: dict[str, str] = {}
    for i in range(0, len(ids), 20):
        res = sb.table("workspace_items").select("item_id, content_md").in_("item_id", ids[i:i + 20]).execute()
        for r in res.data or []:
            heads[r["item_id"]] = (r.get("content_md") or "")[:TEXT_HEAD]
    return heads


def entry(n: int, row: dict, head: str, codec: Any) -> str:
    title = _enc(codec, str(row.get("title") or ""))
    summary = (row.get("summary") or "").strip()
    if summary:
        body = "Summary:\n" + _enc(codec, summary[:SUMMARY_CAP])
    else:
        body = "(no summary) Beginning of the text:\n" + _enc(codec, head)
    return f"[{n}] Title: {title}\n{body}"


async def label(sb: Any, rows: list[dict], concurrency: int) -> tuple[dict[str, str], dict[str, int]]:
    agent = build_agent()
    no_summary = [r["item_id"] for r in rows if not (r.get("summary") or "").strip()]
    heads = text_heads(sb, no_summary) if no_summary else {}
    codecs: dict[str, Any] = {}

    def codec_for(uid: str) -> Any:
        if uid not in codecs:
            codecs[uid] = _summarize_codec(sb, uid)
        return codecs[uid]

    sem = asyncio.Semaphore(concurrency)
    found: dict[str, str] = {}
    usage = Counter()

    async def run_batch(batch: list[dict]) -> list[dict]:
        text = "\n\n".join(
            entry(i + 1, r, heads.get(r["item_id"], ""), codec_for(str(r.get("user_id") or "")))
            for i, r in enumerate(batch)
        )
        async with sem:
            try:
                res = await agent.run(text, usage_limits=LIMITS)
            except Exception as exc:  # noqa: BLE001
                usage["failed_calls"] += 1
                print(f"  batch failed: {type(exc).__name__}", file=sys.stderr)
                return batch
        u = res.usage
        if not hasattr(u, "input_tokens"):  # older pydantic-ai: a method
            u = u()
        usage["calls"] += 1
        usage["tokens_in"] += int(u.input_tokens or 0)
        usage["tokens_out"] += int(u.output_tokens or 0)
        seen: set[int] = set()
        for t in res.output.results:
            if 1 <= t.n <= len(batch) and t.n not in seen and t.doc_type in DOC_TYPES:
                seen.add(t.n)
                found[batch[t.n - 1]["item_id"]] = t.doc_type
        return [r for i, r in enumerate(batch) if (i + 1) not in seen]

    batches = [rows[i:i + BATCH] for i in range(0, len(rows), BATCH)]
    left = [r for rest in await asyncio.gather(*(run_batch(b) for b in batches)) for r in rest]
    if left:  # one more pass for entries the model skipped
        again = [left[i:i + BATCH] for i in range(0, len(left), BATCH)]
        left = [r for rest in await asyncio.gather(*(run_batch(b) for b in again)) for r in rest]
    usage["unresolved"] = len(left)
    usage["no_summary_inputs"] = len(no_summary)
    return found, dict(usage)


def apply(sb: Any, found: dict[str, str]) -> Counter:
    """Write ``metadata.doc_type`` on rows that still have none. Through
    ``write_attachment`` (my_files_phase2.md §C5): with the files write switch
    off this is the plain item update; on, only ``doc_type`` is merged into
    ``user_files`` and the item follows by trigger."""
    done = Counter()
    for item_id, doc_type in found.items():
        row = (
            sb.table("workspace_items").select("metadata, user_id")
            .eq("item_id", item_id).maybe_single().execute()
        )
        data = getattr(row, "data", None) or {}
        meta = dict(data.get("metadata") or {})
        if meta.get("doc_type"):
            done["already_typed"] += 1
            continue
        meta["doc_type"] = doc_type
        files_store.write_attachment(
            sb, item_id,
            metadata=meta,
            metadata_patch={"doc_type": doc_type},
            owner_id=data.get("user_id"),
        )
        done["written"] += 1
    return done


async def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--validate", type=int, default=0, help="check accuracy on N already-typed files; never writes")
    ap.add_argument("--user", default="", help="only this users.user_id")
    ap.add_argument("--out", default="", help="folder OUTSIDE every git repository for the result JSON")
    ap.add_argument("--apply", action="store_true", help="write metadata.doc_type (needs --yes)")
    ap.add_argument("--from-json", default="", help="apply the labels saved by an earlier dry run instead of labelling again")
    ap.add_argument("--yes", action="store_true")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--seed", type=int, default=7)
    args = ap.parse_args()

    out_dir = Path(args.out) if args.out else None
    if out_dir and _inside_git(out_dir):
        raise SystemExit("--out is inside a git repository; choose a folder outside it")
    if args.apply and (args.validate or not args.yes):
        raise SystemExit("--apply needs --yes and cannot be combined with --validate")

    from shared.db.client import get_supabase_client

    sb = get_supabase_client()

    if args.validate:
        rows = [r for r in fetch_rows(sb, typed=True, user_id=args.user or None) if (r.get("summary") or "").strip()]
        random.Random(args.seed).shuffle(rows)
        rows = rows[: args.validate]
        found, usage = await label(sb, rows, args.concurrency)
        agree = Counter()
        pairs = Counter()
        for r in rows:
            stored = r["metadata"]["doc_type"]
            got = found.get(r["item_id"])
            if got is None:
                agree["unresolved"] += 1
            elif got == stored:
                agree["same"] += 1
            else:
                agree["different"] += 1
                pairs[f"{stored} -> {got}"] += 1
        print(json.dumps({"mode": "validate", "files": len(rows), "agreement": dict(agree),
                          "differences": dict(pairs.most_common()), "usage": usage}, ensure_ascii=False, indent=1))
        return

    rows = fetch_rows(sb, typed=False, user_id=args.user or None)
    if args.from_json:
        # Write exactly what a reviewed dry run produced: no model call, nothing re-guessed.
        saved = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        untyped = {r["item_id"] for r in rows}
        found = {k: v for k, v in saved.items() if k in untyped and v in DOC_TYPES}
        usage = {"calls": 0, "from_json": len(saved), "skipped_not_untyped_now": len(saved) - len(found)}
    else:
        found, usage = await label(sb, rows, args.concurrency)
    report: dict[str, Any] = {
        "mode": "apply" if args.apply else "dry-run", "untyped_files": len(rows),
        "labelled": len(found), "types": dict(Counter(found.values()).most_common()), "usage": usage,
    }
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "type_backfill.json").write_text(json.dumps(found, ensure_ascii=False, indent=1), encoding="utf-8")
    if args.apply:
        report["written"] = dict(apply(sb, found))
    print(json.dumps(report, ensure_ascii=False, indent=1))  # counts only — never document text


if __name__ == "__main__":
    asyncio.run(main())
