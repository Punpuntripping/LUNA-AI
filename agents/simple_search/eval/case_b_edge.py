"""Case-B eval — edge shapes of ``page_id``.

``fetch_grounding``/``_title_article`` accept FOUR article page_id shapes
(composite ``{reg_slug}/{article_slug}``, ``{regulation_id}#{article_no}``, the
``seo_articles`` uuid, a bare article slug) and ``_resolve_content_id`` accepts a
raw uuid for a regulation. ``build_simple_search_object`` is narrower. This
measures exactly where the identity bridge stops resolving — the failure mode is
silent (a carry that "works" but drops back to a Case-A search).

    python agents/simple_search/eval/case_b_edge.py
"""
from __future__ import annotations

import json
import sys
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
from agents.simple_search.runner import resolved_from_attachment  # noqa: E402
from backend.app.services import library_item_service as LIS  # noqa: E402
from shared.db.client import get_supabase_client  # noqa: E402

# (label, page_type, page_id, expected ref_id or None)
SHAPES: list[tuple[str, str, str, str | None]] = [
    ("article · composite slug (what the مادة page sends)",
     "article", FX.ART_PAGE_ID, f"article:{FX.ART_V2_ID}"),
    ("article · seo_articles uuid",
     "article", FX.ART_SEO_ID, f"article:{FX.ART_V2_ID}"),
    ("article · '{regulation_id}#{article_no}' gate-key shape",
     "article", f"{FX.REG_LABOR_ID}#5", f"article:{FX.ART_V2_ID}"),
    ("regulation · raw regulations_v2 uuid",
     "regulation", FX.REG_LABOR_ID, f"regdoc:{FX.REG_LABOR_ID}"),
    ("judgment · raw cases uuid",
     "judgment", FX.JUDGMENT_CASE_ID, f"case:{FX.JUDGMENT_CASE_REF}"),
]


def main() -> int:
    sb = get_supabase_client()
    rows = (
        sb.table("conversations").select("conversation_id")
        .eq("user_id", FX.USER_ID).eq("title_ar", FX.SCRATCH_CONVO_TITLE)
        .is_("deleted_at", "null").limit(1).execute()
    ).data or []
    if not rows:
        print("no [EVAL-CASE-B] conversation — run case_b_carry.py first")
        return 1
    convo_id = str(rows[0]["conversation_id"])
    print(f"scratch conversation: {convo_id}\n")

    out: list[dict[str, Any]] = []
    for label, page_type, page_id, expected in SHAPES:
        row: dict[str, Any] = {"label": label, "page_type": page_type,
                               "page_id": page_id, "expected_ref_id": expected}
        try:
            item, dup = LIS.create_library_item(
                sb, user_id=FX.USER_ID, conversation_id=convo_id,
                page_type=page_type, page_id=page_id,
            )
        except Exception as exc:  # noqa: BLE001
            row.update(carried=False, error=str(getattr(exc, "detail", exc)),
                       status=getattr(exc, "status_code", None))
            out.append(row)
            print(f"  REFUSED  {label}\n           {row['error']}")
            continue

        obj = resolved_from_attachment(item)
        meta = item.get("metadata") or {}
        row.update(
            carried=True, already_attached=dup, item_id=item["item_id"],
            title=item.get("title"),
            has_ss_object=bool(meta.get("simple_search_object")),
            ref_id=(obj.ref_id() if obj else None),
            public_path=meta.get("source_page_path"),
            ok=(obj is not None and obj.ref_id() == expected),
        )
        out.append(row)
        mark = "PASS" if row["ok"] else "FAIL"
        print(f"  {mark}  {label}")
        print(f"        title={row['title']!r}")
        print(f"        simple_search_object={row['has_ss_object']}  "
              f"ref_id={row['ref_id']}  expected={expected}")
        print(f"        source_page_path={row['public_path']!r}")

    path = Path(__file__).with_name("case_b_edge_results.json")
    path.write_text(json.dumps({"conversation_id": convo_id, "shapes": out},
                               ensure_ascii=False, indent=2), encoding="utf-8")
    bad = [r for r in out if r.get("carried") and not r.get("ok")]
    print(f"\n{len(out) - len(bad)} ok · {len(bad)} degraded → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
