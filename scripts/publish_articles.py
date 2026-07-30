"""Opt-in publisher for SEO public-library مادة (article) pages.

مادة pages are OPT-IN: the operator personally selects which مواد of a regulation
get a public ``/regulations/{reg}/{article}`` page. The DEFAULT is ZERO published —
the ``seo_articles`` index (built by ``scripts/build_seo_article_index.py``) holds
EVERY مادة, but a مادة only gains a public page once it is PUBLISHED here.

Publishing = setting a slug on the مادة's ``seo_item_meta`` SIDECAR row
(``content_type='article'``, ``content_id='{regulation_id}#{article_no}'``,
``slug='المادة-{N}'``). The anon مادة endpoint requires that slugged sidecar row
to exist (else 404), the doc-page ``article_index`` lists only slugged مواد, and
the articles sitemap emits only slugged مواد whose parent regulation is also
published — so this one flip is authoritative the instant it lands. The upsert is
a MERGE keyed on ``(content_type, content_id)`` — only ``slug``/``updated_at`` are
touched; any ``seo_tier``/``gate_override`` override on the row SURVIVES (gating
via ``scripts/set_gate.py`` and publishing here are independent).

Run from the repo root.

Usage:
  # publish مواد 74, 80, 84 of نظام العمل (creates their public pages)
  python scripts/publish_articles.py --reg نظام-العمل --articles 74,80,84

  # unpublish (clear the slug → the pages 404 again)
  python scripts/publish_articles.py --reg نظام-العمل --articles 80 --unpublish

  # list the currently-published مواد of a regulation
  python scripts/publish_articles.py --reg نظام-العمل --list

Env (service-role Supabase key + revalidate wiring — same as set_gate.py):
  SUPABASE_URL / SUPABASE_SERVICE_KEY  — via shared.config / shared.db.client.
  PUBLIC_WEB_URL                       — frontend origin (for printed URLs +
                                         best-effort ISR revalidation).
  REVALIDATE_SECRET                    — shared secret for /api/revalidate; when
                                         set, each affected URL (+ the parent doc
                                         page) is revalidated best-effort. Missing
                                         → revalidation is SKIPPED (warned); the DB
                                         publish still happens.
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

# Make the repo root importable when run directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# Windows consoles default to cp1252, which can't encode Arabic — force UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # noqa: BLE001
    pass

import httpx

from shared.config import get_settings
from shared.db.client import get_supabase_client


def _resolve_regulation(client, reg_slug: str) -> str | None:
    """Resolve a PUBLISHED regulation slug → its ``regulation_id`` (sidecar
    ``content_id``), or ``None`` when no published regulation carries that slug.

    A مادة can only be published under a regulation that is itself published (its
    articles-sitemap URL and doc-page links both need the parent reg slug)."""
    res = (
        client.table("seo_item_meta")
        .select("content_id, slug")
        .eq("content_type", "regulation")
        .eq("slug", reg_slug)
        .not_.is_("slug", "null")
        .limit(1)
        .execute()
    )
    rows = res.data or []
    return rows[0].get("content_id") if rows else None


def _seo_articles(client, reg_id: str, article_nos: list[int]) -> dict[int, str]:
    """Return ``{article_no: slug}`` for the requested مواد that EXIST in
    ``seo_articles`` for this regulation (the publish validation source)."""
    out: dict[int, str] = {}
    # Chunk the IN lookup (URL-length safety) though the demo sets are tiny.
    for i in range(0, len(article_nos), 100):
        chunk = article_nos[i : i + 100]
        res = (
            client.table("seo_articles")
            .select("article_no, slug")
            .eq("regulation_id", reg_id)
            .in_("article_no", chunk)
            .execute()
        )
        for r in res.data or []:
            no = r.get("article_no")
            slug = r.get("slug")
            if no is not None and slug:
                out[int(no)] = slug
    return out


def _published_articles(client, reg_id: str) -> list[tuple[int, str]]:
    """Currently-published مواد of a regulation as ``[(article_no, slug), ...]``
    ordered by number — the slugged article sidecar rows for the regulation."""
    rows: list[dict] = []
    offset = 0
    page = 1000
    while True:
        res = (
            client.table("seo_item_meta")
            .select("content_id, slug")
            .eq("content_type", "article")
            .like("content_id", f"{reg_id}#%")
            .not_.is_("slug", "null")
            .order("content_id")
            .range(offset, offset + page - 1)
            .execute()
        )
        batch = res.data or []
        rows.extend(batch)
        if len(batch) < page:
            break
        offset += page

    out: list[tuple[int, str]] = []
    for r in rows:
        cid = r.get("content_id") or ""
        slug = r.get("slug")
        if not slug or "#" not in cid:
            continue
        suffix = cid.rsplit("#", 1)[1]
        if suffix.isdigit():
            out.append((int(suffix), slug))
    out.sort(key=lambda x: x[0])
    return out


def _url(base: str, reg_slug: str, art_slug: str) -> str:
    """The public مادة URL (encoded), for printing + revalidation."""
    return (
        f"{base}/regulations/{urllib.parse.quote(reg_slug, safe='')}"
        f"/{urllib.parse.quote(art_slug, safe='')}"
    )


def _revalidate(path: str, settings) -> None:
    """POST the frontend's on-demand ISR revalidate route (best-effort).

    Mirrors ``scripts/set_gate.py``:
      POST {PUBLIC_WEB_URL}/api/revalidate
        header  x-revalidate-secret: {REVALIDATE_SECRET}
        json    {"path": "<path>"}
    Missing REVALIDATE_SECRET → skip. Network / non-2xx → warn, never raise.
    """
    secret = os.environ.get("REVALIDATE_SECRET")
    if not secret:
        return  # summarized once by the caller
    url = f"{settings.PUBLIC_WEB_URL}/api/revalidate"
    try:
        resp = httpx.post(
            url,
            headers={"x-revalidate-secret": secret},
            json={"path": path},
            timeout=15.0,
        )
    except Exception as e:  # noqa: BLE001
        print(f"  WARNING: revalidate POST for {path} failed: {e}")
        return
    if 200 <= resp.status_code < 300:
        print(f"  revalidated {path} (HTTP {resp.status_code})")
    else:
        print(f"  WARNING: revalidate {path} → HTTP {resp.status_code}")


def _parse_articles(raw: str | None) -> list[int]:
    """Parse ``--articles 74,80,84`` → ``[74, 80, 84]`` (deduped, order-preserved).
    Rejects a non-integer or non-positive token loudly."""
    if not raw:
        return []
    seen: dict[int, None] = {}
    for tok in raw.split(","):
        tok = tok.strip()
        if not tok:
            continue
        if not tok.isdigit() or int(tok) <= 0:
            raise SystemExit(f"ERROR: invalid article number {tok!r} (want positive ints)")
        seen[int(tok)] = None
    return list(seen)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Publish / unpublish / list SEO library مادة pages (opt-in)."
    )
    ap.add_argument("--reg", required=True, help="PUBLISHED regulation slug (e.g. نظام-العمل)")
    ap.add_argument(
        "--articles",
        default=None,
        help="comma-separated article numbers, e.g. 74,80,84 (publish/unpublish only)",
    )
    ap.add_argument(
        "--unpublish",
        action="store_true",
        help="clear the slug on the given مواد (their pages 404 again)",
    )
    ap.add_argument(
        "--list",
        dest="list_only",
        action="store_true",
        help="list the regulation's currently-published مواد and exit",
    )
    args = ap.parse_args()

    reg_slug = (args.reg or "").strip()
    if not reg_slug:
        ap.error("--reg must be a non-empty regulation slug")

    settings = get_settings()
    base = settings.PUBLIC_WEB_URL
    client = get_supabase_client()

    reg_id = _resolve_regulation(client, reg_slug)
    if not reg_id:
        raise SystemExit(
            f"ERROR: no PUBLISHED regulation with slug {reg_slug!r} "
            f"(a مادة can only be published under a published regulation)."
        )

    # --- list mode ---------------------------------------------------------
    if args.list_only:
        published = _published_articles(client, reg_id)
        print(f"\nPublished مواد of {reg_slug} ({reg_id}): {len(published)}")
        for no, slug in published:
            print(f"  المادة {no:<5} {_url(base, reg_slug, slug)}")
        if not published:
            print("  (none — publish some with --articles)")
        print()
        return

    # --- publish / unpublish -----------------------------------------------
    article_nos = _parse_articles(args.articles)
    if not article_nos:
        ap.error("--articles is required (e.g. --articles 74,80,84) unless --list")

    # Validate: every requested مادة must exist in seo_articles for this reg.
    existing = _seo_articles(client, reg_id, article_nos)
    missing = [n for n in article_nos if n not in existing]
    if missing:
        raise SystemExit(
            f"ERROR: these مواد are not in seo_articles for {reg_slug}: "
            f"{', '.join(str(m) for m in missing)}. Build the index first "
            f"(scripts/build_seo_article_index.py) or check the numbers."
        )

    now = datetime.now(timezone.utc).isoformat()
    action = "unpublish" if args.unpublish else "publish"
    print(f"\n{action} {len(article_nos)} مادة on {reg_slug} ({reg_id}):\n")

    affected_urls: list[str] = []
    for no in article_nos:
        art_slug = existing[no]
        content_id = f"{reg_id}#{no}"
        if args.unpublish:
            # Clear the slug on the existing sidecar row (update-only: nothing to
            # do if no row exists). Never touches seo_tier / gate_override.
            client.table("seo_item_meta").update(
                {"slug": None, "updated_at": now}
            ).eq("content_type", "article").eq("content_id", content_id).execute()
            print(f"  المادة {no:<5} unpublished (slug cleared)")
        else:
            # Merge-upsert: set only slug + updated_at; preserve any override cols.
            client.table("seo_item_meta").upsert(
                {
                    "content_type": "article",
                    "content_id": content_id,
                    "slug": art_slug,
                    "updated_at": now,
                },
                on_conflict="content_type,content_id",
            ).execute()
            url = _url(base, reg_slug, art_slug)
            affected_urls.append(url)
            print(f"  المادة {no:<5} published → {url}")

    # Best-effort ISR revalidation: each affected مادة URL + the parent doc page
    # (its article_index changed). Skipped (with one warning) when the secret is
    # unset — the DB flip is already live and pages refresh at the next ISR window.
    doc_path = f"/regulations/{reg_slug}"
    revalidate_paths = (
        [f"/regulations/{reg_slug}/{existing[n]}" for n in article_nos] + [doc_path]
    )
    if os.environ.get("REVALIDATE_SECRET"):
        print("\nRevalidating (best-effort):")
        for p in revalidate_paths:
            _revalidate(p, settings)
    else:
        print(
            "\nWARNING: REVALIDATE_SECRET not set — skipping ISR revalidation. "
            "The DB flip is live; pages refresh at the next ISR window."
        )

    print()


if __name__ == "__main__":
    main()
