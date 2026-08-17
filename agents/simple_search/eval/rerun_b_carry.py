"""Case-B RE-RUN — the carry, the identity bridge, and the guard-rails.

Reuses `case_b_carry`'s assertion bodies verbatim (they take an explicit
``convo_id``, so nothing but the conversation changes) and adds the
corpus-scale title verification the brief asks for — the fix lane's headline
numbers (judgments 10,000/10,000 → 0, mid-word cuts 460 → 0, articles 800/800
→ 2/800) are re-measured here from the corpus, not taken on report.

**One oracle correction, declared.** `case_b_carry.public_h1`'s ``blog`` branch
computes ``postHeadline`` (``blog/[token]/page.tsx:13``), which feeds ``<title>``
and OG — NOT the rendered heading. Re-read live in this lane:
``PublicAnswerView.tsx:76-77,135`` renders ``h1 = title || question_text`` and
``BlogArticleView.tsx:94,136`` renders ``title || question_text`` — so the H1
chain is ``title → question_text`` in both views. The original eval's F-7 was
therefore a false positive against a wrong oracle (plan §13e correction #2,
confirmed independently here). This driver patches the blog branch and reports
BOTH readings so the correction is auditable.

    .venv/Scripts/python.exe agents/simple_search/eval/rerun_b_carry.py
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rerun_b_common import (  # noqa: E402
    SCRATCH_TITLE, USER_ID, ensure_scratch_conversation, hr, service_client, short,
    use_rerun_scratch,
)

use_rerun_scratch()

from agents.simple_search.eval import case_b_carry as CB  # noqa: E402
from agents.simple_search.eval import case_b_fixtures as FX  # noqa: E402
from backend.app.services import library_item_service as LIS  # noqa: E402
from shared.seo.judgment_naming import judgment_subject  # noqa: E402

_ORIG_PUBLIC_H1 = CB.public_h1

CORPUS: list[dict[str, Any]] = []


def public_h1(sb, page_type: str) -> str:
    """`case_b_carry.public_h1` with the blog branch pointed at the real <h1>."""
    if page_type != "blog":
        return _ORIG_PUBLIC_H1(sb, page_type)
    p = (
        sb.table("blog_posts").select("title, question_text, display_mode")
        .eq("token", FX.BLOG_TOKEN).limit(1).execute()
    ).data[0]
    # PublicAnswerView.tsx:76-77  →  heading = title || question_text
    # BlogArticleView.tsx:94      →  title   = title || question_text
    return ((p.get("title") or "").strip() or (p.get("question_text") or "").strip())


# --------------------------------------------------------------------------- #
# Corpus-scale title verification (the fix lane's headline numbers)
# --------------------------------------------------------------------------- #


def _page(sb, table: str, cols: str, *, size: int, limit: int, **eq):
    out: list[dict] = []
    start = 0
    while start < limit:
        q = sb.table(table).select(cols)
        for k, v in eq.items():
            q = q.eq(k, v)
        rows = (q.range(start, start + size - 1).execute()).data or []
        out.extend(rows)
        if len(rows) < size:
            break
        start += size
    return out[:limit]


def verify_judgment_titles(sb, sample: int = 1000) -> None:
    """WI title vs the /judgments H1, over a real slice of the published corpus.

    Both sides are computed from the SAME row: the H1 is ``judgment_subject``
    (`library_service.py:4974` → `judgments/[slug]/page.tsx` `doc.subject`), the
    WI title is what ``resolve_title`` returns for a judgment — i.e.
    ``_truncate_title(judgment_subject(row))``.
    """
    hr(f"CORPUS — judgment title vs page H1 (sample {sample})")
    # "Published" for a wing = it has a public page, i.e. a slug in the sidecar
    # (`library_service._sitemap_feed:363-368`; `indexable` gates INDEXING, not
    # publication — see the court-sections note).
    total = (
        sb.table("seo_item_meta").select("content_id", count="exact")
        .eq("content_type", "judgment").not_.is_("slug", "null").execute()
    ).count
    ids: list[str] = []
    start = 0
    while len(ids) < sample:
        rows = (
            sb.table("seo_item_meta").select("content_id")
            .eq("content_type", "judgment").not_.is_("slug", "null")
            .order("content_id").range(start, start + 999).execute()
        ).data or []
        ids.extend(str(r["content_id"]) for r in rows if r.get("content_id"))
        if len(rows) < 1000:
            break
        start += 1000
    ids = ids[:sample]
    print(f"published judgments in the corpus: {total} · sampled: {len(ids)}")

    diverge = 0
    midword = 0
    capped = 0
    checked = 0
    examples: list[dict] = []
    for i in range(0, len(ids), 100):
        rows = (
            sb.table("cases")
            .select("id, court, court_level, case_number, judgment_number, "
                    "date_hijri, short_summary, summary, facts, ruling")
            .in_("id", ids[i:i + 100]).execute()
        ).data or []
        for row in rows:
            checked += 1
            h1 = (judgment_subject(row) or "").strip()
            wi = LIS._truncate_title(h1)
            if wi != h1:
                capped += 1
                if not wi.endswith("…"):
                    midword += 1
            if wi != h1:
                diverge += 1
                if len(examples) < 3:
                    examples.append({"id": row["id"], "h1": h1, "wi": wi})
    print(f"  checked            : {checked}")
    print(f"  title != page H1   : {diverge}")
    print(f"  hit the 150 cap    : {capped}")
    print(f"  cut WITHOUT «…»    : {midword}")
    for e in examples:
        print(f"    e.g. {e['id']}\n         h1={e['h1']!r}\n         wi={e['wi']!r}")
    CORPUS.append({"leg": "judgment_titles", "checked": checked, "diverging": diverge,
                   "capped": capped, "midword_cuts": midword, "examples": examples})
    CB.check(f"judgments: WI title == page H1 on all {checked} sampled",
             diverge == 0, {"diverging": diverge, "examples": examples})
    CB.check("judgments: no mid-word cut (every truncation marked «…»)",
             midword == 0, midword)


def verify_article_titles(sb, sample: int = 800) -> None:
    """WI title vs the مادة page H1 «{article_label} من {regulation.title}»."""
    hr(f"CORPUS — article title vs page H1 (sample {sample})")
    arts = _page(sb, "seo_articles", "id, article_label, article_no, regulation_id",
                 size=1000, limit=sample)
    reg_ids = sorted({str(a["regulation_id"]) for a in arts if a.get("regulation_id")})
    titles: dict[str, str] = {}
    for i in range(0, len(reg_ids), 100):
        rows = (
            sb.table("regulations_v2").select("id, clean_title, title")
            .in_("id", reg_ids[i:i + 100]).execute()
        ).data or []
        for r in rows:
            titles[str(r["id"])] = (r.get("clean_title") or r.get("title") or "").strip()

    diverge, midword, checked = 0, 0, 0
    examples: list[dict] = []
    for a in arts:
        reg_title = titles.get(str(a.get("regulation_id") or ""), "")
        label = (a.get("article_label") or "").strip() or (
            f"المادة {a.get('article_no')}" if a.get("article_no") is not None else ""
        )
        if not label or not reg_title:
            continue
        checked += 1
        h1 = f"{label} من {reg_title}"            # page: [article]/page.tsx:55,179-181
        wi = LIS._truncate_title(h1)              # WI:   _title_article + resolve_title
        if wi != h1:
            diverge += 1
            if not wi.endswith("…"):
                midword += 1
            if len(examples) < 3:
                examples.append({"id": a["id"], "h1": h1, "wi": wi})
    print(f"  checked          : {checked}")
    print(f"  title != page H1 : {diverge}  (all cap-driven; see examples)")
    for e in examples:
        print(f"    e.g. {e['id']}\n         h1={e['h1']!r}\n         wi={e['wi']!r}")
    CORPUS.append({"leg": "article_titles", "checked": checked, "diverging": diverge,
                   "midword_cuts": midword, "examples": examples})
    CB.check(f"articles: divergence <= 5 of {checked} (fix lane measured 2/800)",
             diverge <= 5, {"diverging": diverge, "examples": examples})
    CB.check("articles: no mid-word cut", midword == 0, midword)


def verify_blog_titles(sb) -> None:
    """Every live post: WI title vs the rendered <h1> (title → question_text)."""
    hr("CORPUS — blog title vs rendered <h1> (all live posts)")
    posts = (
        sb.table("blog_posts").select("token, title, question_text, display_mode")
        .is_("deleted_at", "null").limit(500).execute()
    ).data or []
    h1_match = head_match = 0
    for p in posts:
        title = (p.get("title") or "").strip()
        qtext = (p.get("question_text") or "").strip()
        wi = LIS._truncate_title(title or qtext)
        h1 = title or qtext                                   # the rendered heading
        headline = (title or qtext) if (p.get("display_mode") or "") == "title" \
            else (qtext or title)                             # postHeadline (<title>/OG)
        h1_match += int(wi == LIS._truncate_title(h1))
        head_match += int(wi == LIS._truncate_title(headline))
    print(f"  live posts                    : {len(posts)}")
    print(f"  WI title == rendered <h1>     : {h1_match}/{len(posts)}")
    print(f"  WI title == postHeadline      : {head_match}/{len(posts)}  "
          f"(the ORIGINAL eval's oracle — F-7 was measured against this)")
    CORPUS.append({"leg": "blog_titles", "posts": len(posts),
                   "h1_match": h1_match, "postheadline_match": head_match})
    CB.check("blog: WI title == the rendered <h1> on every live post",
             h1_match == len(posts), {"matched": h1_match, "of": len(posts)})


def verify_identity_shapes(sb) -> None:
    """The identity bridge across EVERY ``page_id`` shape — pure resolution.

    Deliberately calls ``resolve_page_identity`` rather than carrying: after the
    dedup fix a second shape of an already-carried object returns the EXISTING
    row, whose metadata would mask whether this shape resolves on its own.
    """
    hr("IDENTITY BRIDGE — every accepted page_id shape (no writes)")
    shapes: list[tuple[str, str, str, str | None]] = [
        ("regulation · slug", "regulation", FX.REG_LABOR_SLUG, f"regdoc:{FX.REG_LABOR_ID}"),
        ("regulation · raw uuid", "regulation", FX.REG_LABOR_ID, f"regdoc:{FX.REG_LABOR_ID}"),
        ("article · composite slug", "article", FX.ART_PAGE_ID, f"article:{FX.ART_V2_ID}"),
        ("article · seo_articles uuid", "article", FX.ART_SEO_ID, f"article:{FX.ART_V2_ID}"),
        ("article · '{reg_id}#{no}' gate key", "article", f"{FX.REG_LABOR_ID}#5",
         f"article:{FX.ART_V2_ID}"),
        ("article · BARE slug (ambiguous by design)", "article", "المادة-5", None),
        ("judgment · slug", "judgment", FX.JUDGMENT_SLUG, f"case:{FX.JUDGMENT_CASE_REF}"),
        ("judgment · raw cases uuid", "judgment", FX.JUDGMENT_CASE_ID,
         f"case:{FX.JUDGMENT_CASE_REF}"),
        ("blog · token", "blog", FX.BLOG_TOKEN, None),
    ]
    keys: dict[str, list[str]] = {}
    rows: list[dict] = []
    for label, ptype, pid, expected in shapes:
        ident = LIS.resolve_page_identity(sb, ptype, pid)
        obj = ident.obj
        ref = None
        if obj:
            from agents.simple_search.models import ResolvedObject

            ref = ResolvedObject(**{**obj, "title": "x"}).ref_id()
        rows.append({"label": label, "page_type": ptype, "page_id": pid,
                     "expected_ref_id": expected, "ref_id": ref, "key": ident.key})
        keys.setdefault(f"{ptype}:{ident.key}", []).append(label)
        print(f"  {label}\n      key={ident.key}  ref_id={ref}  expected={expected}")
        CB.check(f"bridge · {label}", ref == expected,
                 {"got": ref, "expected": expected, "key": ident.key})

    # The dedup key must collapse every spelling of ONE object into one bucket.
    art_keys = {r["key"] for r in rows if r["page_type"] == "article"
                and r["ref_id"] is not None}
    reg_keys = {r["key"] for r in rows if r["page_type"] == "regulation"}
    jud_keys = {r["key"] for r in rows if r["page_type"] == "judgment"}
    CB.check("dedup key: 3 article spellings → ONE key", len(art_keys) == 1, art_keys)
    CB.check("dedup key: 2 regulation spellings → ONE key", len(reg_keys) == 1, reg_keys)
    CB.check("dedup key: 2 judgment spellings → ONE key", len(jud_keys) == 1, jud_keys)
    CORPUS.append({"leg": "identity_shapes", "shapes": rows,
                   "article_keys": sorted(art_keys), "regulation_keys": sorted(reg_keys),
                   "judgment_keys": sorted(jud_keys)})


def verify_live_dedup(sb, convo_id: str) -> None:
    """Carry every shape for real and count the rows they produce."""
    hr("DEDUP — 9 carries of 4 distinct objects")
    carries = [
        ("regulation", FX.REG_LABOR_SLUG), ("regulation", FX.REG_LABOR_ID),
        ("article", FX.ART_PAGE_ID), ("article", FX.ART_SEO_ID),
        ("article", f"{FX.REG_LABOR_ID}#5"),
        ("judgment", FX.JUDGMENT_SLUG), ("judgment", FX.JUDGMENT_CASE_ID),
        ("blog", FX.BLOG_TOKEN), ("blog", FX.BLOG_TOKEN.upper()),
    ]
    for ptype, pid in carries:
        item, dup = LIS.create_library_item(
            sb, user_id=USER_ID, conversation_id=convo_id,
            page_type=ptype, page_id=pid,
        )
        print(f"  {ptype:<11} {short(pid, 46):<48} dup={dup}  {item['item_id']}")
    rows = (
        sb.table("workspace_items").select("item_id, title, metadata")
        .eq("conversation_id", convo_id).eq("kind", "references")
        .is_("deleted_at", "null").execute()
    ).data or []
    print(f"\n  references rows in the conversation: {len(rows)}")
    for r in rows:
        md = r.get("metadata") or {}
        print(f"    «{short(r.get('title') or '', 60)}»  key={md.get('source_page_key')}")
    CORPUS.append({"leg": "dedup", "carries": len(carries), "rows": len(rows),
                   "keys": [(r.get("metadata") or {}).get("source_page_key") for r in rows]})
    CB.check(f"dedup: {len(carries)} carries of 4 objects → 4 rows",
             len(rows) == 4, {"rows": len(rows),
                              "titles": [r.get("title") for r in rows]})


def main() -> int:
    sb = service_client()
    convo_id = ensure_scratch_conversation(sb)
    hr(f"CASE-B RE-RUN · carry — scratch {convo_id} «{SCRATCH_TITLE}»")

    CB.public_h1 = public_h1  # the declared oracle correction (blog H1)

    carried = CB.run_carry(sb, convo_id)
    CB.verify_judgment_ref_against_row(sb)
    CB.run_dedup(sb, convo_id, carried)
    CB.run_refusals(sb, convo_id)
    CB.run_ocr_zero(sb, carried)
    CB.run_cap_exemption(sb, convo_id)
    CB.run_ownership(sb, convo_id)

    verify_identity_shapes(sb)
    verify_live_dedup(sb, convo_id)
    verify_judgment_titles(sb)
    verify_article_titles(sb)
    verify_blog_titles(sb)

    out = {
        "conversation_id": convo_id,
        "carried_item_ids": {k: v["item_id"] for k, v in carried.items()},
        "corpus": CORPUS,
        "results": CB.RESULTS,
        "passed": sum(1 for r in CB.RESULTS if r["ok"]),
        "failed": sum(1 for r in CB.RESULTS if not r["ok"]),
    }
    path = Path(__file__).with_name("rerun_b_carry_results.json")
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    hr(f"{out['passed']} passed · {out['failed']} failed → {path.name}")
    for r in CB.RESULTS:
        if not r["ok"]:
            print(f"  FAIL  {r['check']}\n        {short(str(r['detail']), 300)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
