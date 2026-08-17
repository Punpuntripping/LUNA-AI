"""Case-B eval — the carry + the identity bridge + the guard-rails.

Covers task items 1, 2 and 5:

* the carry itself, per supported page_type (kind / title-vs-H1 / summary /
  metadata / bounded body);
* ``metadata.simple_search_object`` → ``runner.resolved_from_attachment`` →
  ``ResolvedObject.ref_id()`` per §6.1a;
* dedup, unsupported-type refusal, zero OCR pages, the 15-item cap exemption,
  ownership isolation.

Read-only against production modules — it imports and CALLS them, never edits
them. Everything it writes lands in the single ``[EVAL-CASE-B]`` conversation,
which ``case_b_teardown.py`` hard-deletes.

    python agents/simple_search/eval/case_b_carry.py
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[3]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001
    pass

from dotenv import load_dotenv

load_dotenv()

from agents.simple_search.eval import case_b_fixtures as FX  # noqa: E402
from agents.simple_search.models import ResolvedObject  # noqa: E402
from agents.simple_search.runner import resolved_from_attachment  # noqa: E402
from backend.app.services import library_item_service as LIS  # noqa: E402
from backend.app.services.ask_service import MAX_CONTEXT_CHARS, fetch_grounding  # noqa: E402
from backend.app.services.message_service import _estimate_ocr_pages  # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402
from shared.seo.judgment_naming import judgment_subject  # noqa: E402

RESULTS: list[dict[str, Any]] = []


def check(name: str, ok: bool, detail: Any = "") -> bool:
    RESULTS.append({"check": name, "ok": bool(ok), "detail": detail})
    print(("  PASS  " if ok else "  FAIL  ") + name)
    if not ok:
        print(f"        → {detail}")
    return bool(ok)


# --------------------------------------------------------------------------- #
# Scratch conversation
# --------------------------------------------------------------------------- #


def ensure_scratch_conversation(sb) -> str:
    rows = (
        sb.table("conversations")
        .select("conversation_id")
        .eq("user_id", FX.USER_ID)
        .eq("title_ar", FX.SCRATCH_CONVO_TITLE)
        .is_("deleted_at", "null")
        .limit(1)
        .execute()
    ).data or []
    if rows:
        return str(rows[0]["conversation_id"])
    new = (
        sb.table("conversations")
        .insert({"user_id": FX.USER_ID, "title_ar": FX.SCRATCH_CONVO_TITLE})
        .execute()
    ).data
    return str(new[0]["conversation_id"])


# --------------------------------------------------------------------------- #
# The public page H1, computed from the SAME source the public page reads.
# --------------------------------------------------------------------------- #


def public_h1(sb, page_type: str) -> str:
    """What the public page prints as its <h1>."""
    if page_type == "regulation":
        # frontend/app/regulations/[slug]/page.tsx:190 → doc.title
        # library_service.py:2901 → clean_title || title
        r = (
            sb.table("regulations_v2")
            .select("clean_title, title")
            .eq("id", FX.REG_LABOR_ID)
            .limit(1)
            .execute()
        ).data[0]
        return (r.get("clean_title") or r.get("title") or "").strip()

    if page_type == "article":
        # frontend/app/regulations/[slug]/[article]/page.tsx:55,179
        #   heading = `${doc.article_label} من ${doc.regulation.title}`
        a = (
            sb.table("seo_articles")
            .select("article_label, article_no")
            .eq("id", FX.ART_SEO_ID)
            .limit(1)
            .execute()
        ).data[0]
        r = (
            sb.table("regulations_v2")
            .select("clean_title, title")
            .eq("id", FX.REG_LABOR_ID)
            .limit(1)
            .execute()
        ).data[0]
        label = a.get("article_label") or f"المادة {a.get('article_no')}"
        return f"{label} من {(r.get('clean_title') or r.get('title') or '').strip()}"

    if page_type == "judgment":
        # frontend/app/judgments/[slug]/page.tsx:229 → doc.subject
        # library_service.py:4974 → judgment_subject(row)
        c = (
            sb.table("cases")
            .select("court, court_level, case_number, judgment_number, "
                    "date_hijri, short_summary, summary, facts, ruling")
            .eq("id", FX.JUDGMENT_CASE_ID)
            .limit(1)
            .execute()
        ).data[0]
        return judgment_subject(c)

    if page_type == "blog":
        # frontend/app/blog/[token]/page.tsx:12-16 → postHeadline(post)
        p = (
            sb.table("blog_posts")
            .select("title, question_text, display_mode")
            .eq("token", FX.BLOG_TOKEN)
            .limit(1)
            .execute()
        ).data[0]
        if (p.get("display_mode") or "") == "title":
            return (p.get("title") or p.get("question_text") or "ريحان").strip()
        return (p.get("question_text") or p.get("title") or "ريحان").strip()

    raise ValueError(page_type)


# --------------------------------------------------------------------------- #
# 1 + 2 — the carry and the identity bridge
# --------------------------------------------------------------------------- #

EXPECTED_REF: dict[str, str | None] = {
    "regulation": f"regdoc:{FX.REG_LABOR_ID}",
    "article": f"article:{FX.ART_V2_ID}",
    "judgment": f"case:{FX.JUDGMENT_CASE_REF}",
    "blog": None,   # no simple_search level — must degrade to the searcher
}

PAGE_IDS: dict[str, str] = {
    "regulation": FX.REG_LABOR_SLUG,
    "article": FX.ART_PAGE_ID,
    "judgment": FX.JUDGMENT_SLUG,
    "blog": FX.BLOG_TOKEN,
}


def run_carry(sb, convo_id: str) -> dict[str, dict]:
    carried: dict[str, dict] = {}
    for page_type, page_id in PAGE_IDS.items():
        print(f"\n── carry: {page_type} ({page_id})")
        item, dup = LIS.create_library_item(
            sb,
            user_id=FX.USER_ID,
            conversation_id=convo_id,
            page_type=page_type,
            page_id=page_id,
        )
        carried[page_type] = item

        check(f"[{page_type}] kind == 'references'", item.get("kind") == "references",
              item.get("kind"))
        check(f"[{page_type}] first carry is not a dup", dup is False, dup)

        h1 = public_h1(sb, page_type)
        title = (item.get("title") or "").strip()
        check(f"[{page_type}] title == public page H1", title == h1,
              {"wi_title": title, "page_h1": h1})

        summary = (item.get("summary") or "").strip()
        check(f"[{page_type}] summary pre-filled", bool(summary),
              {"len": len(summary), "head": summary[:90]})

        meta = item.get("metadata") or {}
        check(f"[{page_type}] metadata.source_page_type",
              meta.get("source_page_type") == page_type, meta.get("source_page_type"))
        check(f"[{page_type}] metadata.source_page_id",
              meta.get("source_page_id") == page_id, meta.get("source_page_id"))

        body = fetch_grounding(sb, page_type, page_id) or ""
        content = item.get("content_md") or ""
        check(f"[{page_type}] grounding body <= MAX_CONTEXT_CHARS ({MAX_CONTEXT_CHARS})",
              len(body) <= MAX_CONTEXT_CHARS, len(body))
        check(f"[{page_type}] content_md bounded (frame + body)",
              len(content) <= MAX_CONTEXT_CHARS + 400,
              {"content_chars": len(content), "body_chars": len(body)})

        # ── the identity bridge ────────────────────────────────────────────
        ss = meta.get("simple_search_object")
        obj = resolved_from_attachment(item)
        expected = EXPECTED_REF[page_type]
        if expected is None:
            check(f"[{page_type}] no simple_search level → resolved_from_attachment None",
                  obj is None and ss is None, {"obj": obj, "ss_object": ss})
        else:
            if obj is None:
                check(f"[{page_type}] round-trips to a ResolvedObject", False,
                      {"metadata.simple_search_object": ss})
            else:
                check(f"[{page_type}] round-trips to a ResolvedObject",
                      isinstance(obj, ResolvedObject), type(obj).__name__)
                check(f"[{page_type}] ref_id() == {expected}",
                      obj.ref_id() == expected,
                      {"actual": obj.ref_id(), "expected": expected,
                       "level": obj.level, "ss_object": ss})
    return carried


def verify_judgment_ref_against_row(sb) -> None:
    """The judgment ref MUST be the case_ref, never cases.id."""
    row = (
        sb.table("cases").select("id, case_ref")
        .eq("id", FX.JUDGMENT_CASE_ID).limit(1).execute()
    ).data[0]
    obj = ResolvedObject(level="judgment", case_id=row["id"], case_ref=row["case_ref"])
    check("judgment ref_id keys on cases.case_ref (not cases.id)",
          obj.ref_id() == f"case:{row['case_ref']}"
          and row["id"] not in obj.ref_id(),
          {"ref_id": obj.ref_id(), "cases.id": row["id"], "case_ref": row["case_ref"]})


# --------------------------------------------------------------------------- #
# 5 — the rest
# --------------------------------------------------------------------------- #


def run_dedup(sb, convo_id: str, carried: dict[str, dict]) -> None:
    print("\n── dedup (second carry of the same page)")
    before = (
        sb.table("workspace_items").select("item_id", count="exact")
        .eq("conversation_id", convo_id).is_("deleted_at", "null").execute()
    ).count
    item, dup = LIS.create_library_item(
        sb, user_id=FX.USER_ID, conversation_id=convo_id,
        page_type="regulation", page_id=FX.REG_LABOR_SLUG,
    )
    after = (
        sb.table("workspace_items").select("item_id", count="exact")
        .eq("conversation_id", convo_id).is_("deleted_at", "null").execute()
    ).count
    check("dedup: already_attached=True", dup is True, dup)
    check("dedup: same item_id returned",
          item.get("item_id") == carried["regulation"].get("item_id"),
          {"second": item.get("item_id"), "first": carried["regulation"].get("item_id")})
    check("dedup: nothing written", before == after, {"before": before, "after": after})


def run_refusals(sb, convo_id: str) -> None:
    print("\n── unsupported page types")
    for page_type in FX.UNSUPPORTED_TYPES:
        before = (
            sb.table("workspace_items").select("item_id", count="exact")
            .eq("conversation_id", convo_id).is_("deleted_at", "null").execute()
        ).count
        detail, status, raised = "", 0, False
        try:
            LIS.create_library_item(
                sb, user_id=FX.USER_ID, conversation_id=convo_id,
                page_type=page_type, page_id="أي-شيء",
            )
        except Exception as exc:  # noqa: BLE001
            raised = True
            detail = str(getattr(exc, "detail", exc))
            status = int(getattr(exc, "status_code", 0) or 0)
        after = (
            sb.table("workspace_items").select("item_id", count="exact")
            .eq("conversation_id", convo_id).is_("deleted_at", "null").execute()
        ).count
        arabic = any("؀" <= ch <= "ۿ" for ch in detail)
        check(f"[{page_type}] refused with 400", raised and status == 400,
              {"status": status, "detail": detail})
        check(f"[{page_type}] refusal message is Arabic", arabic, detail)
        check(f"[{page_type}] created NOTHING", before == after,
              {"before": before, "after": after})


def run_ocr_zero(sb, carried: dict[str, dict]) -> None:
    print("\n── OCR quota")
    ids = [c["item_id"] for c in carried.values()]
    pages = _estimate_ocr_pages(sb, ids)
    check("carried library items project 0 OCR pages", pages == 0,
          {"item_ids": ids, "pages": pages})


def run_cap_exemption(sb, convo_id: str) -> None:
    print("\n── the 15-item cap")
    counted = (
        sb.table("workspace_items").select("item_id", count="exact")
        .eq("conversation_id", convo_id)
        .in_("kind", ["agent_search", "agent_writing", "note"])
        .is_("deleted_at", "null").execute()
    ).count
    refs = (
        sb.table("workspace_items").select("item_id", count="exact")
        .eq("conversation_id", convo_id).eq("kind", "references")
        .is_("deleted_at", "null").execute()
    ).count
    check("carried items do not consume the counted-kind budget",
          counted == 0 and refs >= 4, {"counted_kinds": counted, "references": refs})


def run_ownership(sb, convo_id: str) -> None:
    print("\n── ownership isolation")
    other = str(uuid.uuid4())
    status, detail, raised = 0, "", False
    try:
        LIS.create_library_item(
            sb, user_id=other, conversation_id=convo_id,
            page_type="regulation", page_id=FX.REG_ENFORCE_SLUG,
        )
    except Exception as exc:  # noqa: BLE001
        raised = True
        status = int(getattr(exc, "status_code", 0) or 0)
        detail = str(getattr(exc, "detail", exc))
    check("a non-owner cannot carry into this conversation (404)",
          raised and status == 404, {"status": status, "detail": detail})


def main() -> int:
    sb = get_supabase_client()
    convo_id = ensure_scratch_conversation(sb)
    print(f"scratch conversation: {convo_id}")

    carried = run_carry(sb, convo_id)
    verify_judgment_ref_against_row(sb)
    run_dedup(sb, convo_id, carried)
    run_refusals(sb, convo_id)
    run_ocr_zero(sb, carried)
    run_cap_exemption(sb, convo_id)
    run_ownership(sb, convo_id)

    out = {
        "conversation_id": convo_id,
        "carried_item_ids": {k: v["item_id"] for k, v in carried.items()},
        "results": RESULTS,
        "passed": sum(1 for r in RESULTS if r["ok"]),
        "failed": sum(1 for r in RESULTS if not r["ok"]),
    }
    path = Path(__file__).with_name("case_b_carry_results.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n{out['passed']} passed · {out['failed']} failed → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
