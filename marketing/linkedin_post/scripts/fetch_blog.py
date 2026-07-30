#!/usr/bin/env python3
"""
fetch_blog.py — Step 1 of the blog → LinkedIn carousel pipeline.

Fetches a published Rayhan blog post from the PUBLIC api (no auth) and writes a
clean `source.md` brief (question + full answer + reference list) into
`decks/<short>/`, ready for the deck-building agent to read.

Usage:
    python scripts/fetch_blog.py <token>
    python scripts/fetch_blog.py https://rayhanai.com/blog/<token>

    <token> is the 32-hex id from the blog URL. The deck folder is named after
    the first 8 chars (the "short" id) — e.g. token ec47e5cc… -> decks/ec47e5cc/.

Env:
    BLOG_API_BASE  override the backend origin
                   (default: https://luna-backend-production-35ba.up.railway.app)
"""
import os
import re
import sys
import json
import pathlib
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parent.parent          # marketing/linkedin_post/
API_BASE = os.environ.get(
    "BLOG_API_BASE",
    "https://luna-backend-production-35ba.up.railway.app",
).rstrip("/")


def token_from_arg(arg: str) -> str:
    m = re.search(r"[0-9a-fA-F]{32}", arg)
    if not m:
        sys.exit(f"could not find a 32-hex blog token in: {arg!r}")
    return m.group(0).lower()


def fetch(token: str) -> dict:
    url = f"{API_BASE}/api/v1/public/blog/{token}"
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        if r.status != 200:
            sys.exit(f"HTTP {r.status} fetching {url}")
        return json.loads(r.read().decode("utf-8"))


def build_brief(token: str, short: str, d: dict) -> str:
    refs = d.get("references") or []
    lines = []
    for r in refs:
        title = r.get("regulation_title") or r.get("entity_name") or r.get("title") or "—"
        art = r.get("article_num")
        sec = r.get("section_title")
        tail = (f" — {sec}" if sec else "") + (f" — المادة {art}" if art else "")
        lines.append(f"- [{r.get('n')}] {title}{tail}")
    reflist = "\n".join(lines) if lines else "- (no references)"
    return (
        f"# مصدر التصميم — بلوق {short}  (token: {token})\n\n"
        f"**رابط البلوق:** https://rayhanai.com/blog/{token}\n"
        f"**العنوان:** {d.get('title')}\n"
        f"**subtype:** {d.get('subtype')}   |   display_mode: {d.get('display_mode')}\n\n"
        f"## السؤال (كما طرحه المستخدم)\n{d.get('question_text')}\n\n"
        f"## الجواب الكامل (content_md)\n{d.get('content_md')}\n\n"
        f"## المراجع الرسمية ({len(refs)})\n{reflist}\n\n"
        f"> ملاحظة: بعض سجلات المراجع تحمل `article_num=null` — أرقام المواد قد ترد\n"
        f"> داخل نص الجواب نفسه؛ استخرجها من المتن عند بناء رقائق المصادر.\n"
    )


def main() -> None:
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    token = token_from_arg(sys.argv[1])
    short = token[:8]
    out_dir = ROOT / "decks" / short
    out_dir.mkdir(parents=True, exist_ok=True)
    data = fetch(token)
    brief = build_brief(token, short, data)
    (out_dir / "source.md").write_text(brief, encoding="utf-8")
    # keep the raw payload too, for reference resolution / debugging
    (out_dir / "source.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"ok: {short}  ({len(data.get('content_md') or '')} chars, "
          f"{len(data.get('references') or [])} refs)")
    print(f"  -> {out_dir / 'source.md'}")


if __name__ == "__main__":
    main()
