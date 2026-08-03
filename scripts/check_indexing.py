"""Per-URL Google index status for the public library, via the URL Inspection API.

Answers the question the Search Console UI cannot answer at scale: WHICH of our
published URLs are actually in Google's index, and for the ones that are not,
WHY. The UI inspects one URL at a time and its Page Indexing report lags by up to
~10 days; this hits the same backing API the UI uses, so every verdict is
CURRENT, and the daily quota (2,000 inspections per property) covers the whole
published corpus in one run.

URL SOURCE — the live sitemaps, by default. `https://rayhanai.com/sitemap.xml` is
fetched, every child <sitemap> is followed, and the union of <loc> values is the
work list. That means this script can never drift out of sync with what we
actually publish: if a wing is in sample mode, or judgments are held back by the
PDPL gate, the run reflects exactly that. `--section` limits to named children
(`regulations`, `compliance`, …); `--urls-file` overrides the source entirely
with a newline-delimited list.

WHAT IT REPORTS — per URL, straight from `indexStatusResult`:
  * coverage_state — the human-readable status ("Submitted and indexed",
    "Discovered - currently not indexed", "URL is unknown to Google", …). This is
    the field that actually answers "indexed or not, and why not".
  * verdict        — PASS / PARTIAL / FAIL / NEUTRAL.
  * last_crawl     — when Googlebot last fetched it (None = never crawled).
  * google_canonical vs user_canonical — a MISMATCH here is the classic silent
    killer: Google picked a different canonical than we declared, so our URL is
    indexed under someone else's address. Called out explicitly in the summary.
  * robots_state, fetch_state, indexing_state — why a fetch failed, if it did.

Results stream to a JSONL file as they arrive, and a re-run SKIPS any URL already
present in that file. The quota is 2,000/day, so a corpus larger than that is
resumed across days simply by re-running with the same `--out`; `--force`
re-checks everything instead.

AUTH — a Google Cloud service account with the Search Console API enabled:
  1. Google Cloud console → create (or pick) a project → enable
     "Google Search Console API".
  2. IAM → Service Accounts → create one → Keys → Add key → JSON → download.
  3. Search Console → the rayhanai.com property → Settings → Users and
     permissions → Add user → paste the service account's `client_email` →
     permission "Full". THIS STEP IS THE ONE PEOPLE MISS; without it every
     inspection returns 403.
  4. Point this script at the JSON: `--key-file path.json`, or set
     GSC_SERVICE_ACCOUNT_FILE.

`--site` defaults to `sc-domain:rayhanai.com`, which is the Domain-property form.
A URL-prefix property would instead be `https://rayhanai.com/`.

Run from the repo root:
  python scripts/check_indexing.py --key-file gsc-sa.json
  python scripts/check_indexing.py --section regulations,compliance
  python scripts/check_indexing.py --limit 50 --out reports/idx.jsonl
  python scripts/check_indexing.py --summary-only          # re-print from JSONL
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable, Optional

INSPECT_ENDPOINT = "https://searchconsole.googleapis.com/v1/urlInspection/index:inspect"
SCOPE = "https://www.googleapis.com/auth/webmasters.readonly"

DEFAULT_SITEMAP = "https://rayhanai.com/sitemap.xml"
DEFAULT_SITE = "sc-domain:rayhanai.com"
DEFAULT_OUT = "agents_reports/indexing/url_index_status.jsonl"

# Documented ceilings, per property: 2,000 inspections/day and 600/minute. We pace
# well under the per-minute limit because a 429 costs more time than the delay
# saves, and the daily cap is the one that actually binds on a big corpus.
DAILY_QUOTA = 2000
DEFAULT_DELAY_S = 0.15


# --------------------------------------------------------------------------
# URL discovery — read the live sitemaps
# --------------------------------------------------------------------------

def _fetch(url: str, timeout: int = 30) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "luna-index-checker/1"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="replace")


def _locs(xml: str) -> list[str]:
    return [m.strip() for m in re.findall(r"<loc>\s*([^<]+?)\s*</loc>", xml)]


def sitemap_urls(index_url: str, sections: Optional[set[str]]) -> list[str]:
    """Follow the sitemap index and return the union of child <loc> values.

    A child whose trailing path segment is not in `sections` is skipped when
    `sections` is given. Children that fail to fetch are reported and skipped
    rather than aborting the run — a single unreachable wing should not stop an
    audit of the others.
    """
    index_xml = _fetch(index_url)
    children = _locs(index_xml)
    if not children:  # a flat sitemap, not an index
        return _locs(index_xml)

    urls: list[str] = []
    for child in children:
        name = urllib.parse.urlparse(child).path.rstrip("/").rsplit("/", 1)[-1]
        if sections and name not in sections:
            continue
        try:
            found = _locs(_fetch(child))
        except Exception as e:  # noqa: BLE001
            print(f"  ! {name}: fetch failed ({e}) — skipped", file=sys.stderr)
            continue
        print(f"  {name}: {len(found)} URLs")
        urls.extend(found)

    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


# --------------------------------------------------------------------------
# Inspection
# --------------------------------------------------------------------------

def build_session(key_file: str):
    """Authorized session for the Search Console API, from a service-account key."""
    try:
        from google.auth.transport.requests import AuthorizedSession
        from google.oauth2 import service_account
    except ImportError:
        sys.exit(
            "Missing deps. Install with:\n"
            "  pip install google-auth google-auth-httplib2 requests"
        )

    if not key_file or not Path(key_file).is_file():
        sys.exit(
            f"Service-account key not found: {key_file!r}\n"
            "Pass --key-file, or set GSC_SERVICE_ACCOUNT_FILE. See the module "
            "docstring for how to create one and grant it Search Console access."
        )

    creds = service_account.Credentials.from_service_account_file(
        key_file, scopes=[SCOPE]
    )
    return AuthorizedSession(creds)


def inspect(session, url: str, site: str, retries: int = 4) -> dict[str, Any]:
    """Inspect one URL. Returns a flat dict; `error` is set when it could not run.

    Retries on 429 (quota pacing) and 5xx with exponential backoff. A 403 is NOT
    retried — it means the service account was never granted access to the
    property, and retrying just burns time on a permanent misconfiguration.
    """
    body = {"inspectionUrl": url, "siteUrl": site, "languageCode": "ar"}

    for attempt in range(retries):
        try:
            resp = session.post(INSPECT_ENDPOINT, json=body, timeout=60)
        except Exception as e:  # noqa: BLE001
            if attempt == retries - 1:
                return {"url": url, "error": f"network: {e}"}
            time.sleep(2 ** attempt)
            continue

        if resp.status_code == 200:
            r = resp.json().get("inspectionResult", {})
            idx = r.get("indexStatusResult", {})
            return {
                "url": url,
                "coverage_state": idx.get("coverageState"),
                "verdict": idx.get("verdict"),
                "last_crawl": idx.get("lastCrawlTime"),
                "google_canonical": idx.get("googleCanonical"),
                "user_canonical": idx.get("userCanonical"),
                "robots_state": idx.get("robotsTxtState"),
                "fetch_state": idx.get("pageFetchState"),
                "indexing_state": idx.get("indexingState"),
                "sitemaps": idx.get("sitemap"),
            }

        if resp.status_code == 403:
            return {
                "url": url,
                "error": "403 forbidden — is the service account added as a user "
                         "on this Search Console property?",
            }
        if resp.status_code == 429 or resp.status_code >= 500:
            if attempt == retries - 1:
                return {"url": url, "error": f"http {resp.status_code} after retries"}
            time.sleep(2 ** attempt + 1)
            continue

        return {"url": url, "error": f"http {resp.status_code}: {resp.text[:200]}"}

    return {"url": url, "error": "exhausted retries"}


# --------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------

def section_of(url: str) -> str:
    """First path segment — the wing a URL belongs to ('/' for the homepage)."""
    path = urllib.parse.urlparse(url).path.strip("/")
    return path.split("/")[0] if path else "(home)"


def summarize(rows: Iterable[dict[str, Any]]) -> None:
    rows = list(rows)
    if not rows:
        print("No results to summarize.")
        return

    ok = [r for r in rows if not r.get("error")]
    errs = [r for r in rows if r.get("error")]

    indexed = [r for r in ok if r.get("verdict") == "PASS"]
    print("\n" + "=" * 70)
    print(f"INDEXED {len(indexed)} / {len(ok)} inspected"
          f"{f' ({len(errs)} errors)' if errs else ''}")
    print("=" * 70)

    print("\nBy coverage state:")
    for state, n in Counter(
        r.get("coverage_state") or "(unknown)" for r in ok
    ).most_common():
        print(f"  {n:>6}  {state}")

    print("\nBy section (indexed / total):")
    per: dict[str, list[dict]] = defaultdict(list)
    for r in ok:
        per[section_of(r["url"])].append(r)
    for sec in sorted(per, key=lambda s: -len(per[s])):
        got = sum(1 for r in per[sec] if r.get("verdict") == "PASS")
        print(f"  {got:>5} / {len(per[sec]):<5}  /{sec}")

    # Google chose a different canonical than we declared — these are indexed
    # under an address we did not intend, and never show up as "our" URL.
    mism = [
        r for r in ok
        if r.get("google_canonical") and r.get("user_canonical")
        and r["google_canonical"] != r["user_canonical"]
    ]
    if mism:
        print(f"\n⚠ CANONICAL MISMATCH on {len(mism)} URL(s) — Google picked its own:")
        for r in mism[:10]:
            print(f"    ours   : {r['user_canonical']}")
            print(f"    google : {r['google_canonical']}\n")
        if len(mism) > 10:
            print(f"    … and {len(mism) - 10} more (see the JSONL)")

    never = [r for r in ok if not r.get("last_crawl")]
    if never:
        print(f"\nNever crawled: {len(never)} URL(s). Examples:")
        for r in never[:5]:
            print(f"    {urllib.parse.unquote(r['url'])}")

    if errs:
        print(f"\nErrors ({len(errs)}):")
        for msg, n in Counter(r["error"] for r in errs).most_common(5):
            print(f"  {n:>6}  {msg}")


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    out = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


# --------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--key-file", default=os.environ.get("GSC_SERVICE_ACCOUNT_FILE"),
                    help="Service-account JSON key (env: GSC_SERVICE_ACCOUNT_FILE)")
    ap.add_argument("--site", default=DEFAULT_SITE,
                    help=f"Search Console property (default: {DEFAULT_SITE})")
    ap.add_argument("--sitemap", default=DEFAULT_SITEMAP)
    ap.add_argument("--section", help="Comma-separated sitemap children to check")
    ap.add_argument("--urls-file", help="Newline-delimited URLs instead of sitemaps")
    ap.add_argument("--out", default=DEFAULT_OUT, help="JSONL results path")
    ap.add_argument("--limit", type=int, help="Stop after N inspections")
    ap.add_argument("--delay", type=float, default=DEFAULT_DELAY_S)
    ap.add_argument("--force", action="store_true",
                    help="Re-inspect URLs already present in --out")
    ap.add_argument("--summary-only", action="store_true",
                    help="Re-print the summary from --out without calling the API")
    args = ap.parse_args()

    out_path = Path(args.out)
    done = load_jsonl(out_path)

    if args.summary_only:
        summarize(done)
        return

    if args.urls_file:
        urls = [l.strip() for l in Path(args.urls_file).read_text(
            encoding="utf-8").splitlines() if l.strip()]
    else:
        sections = set(s.strip() for s in args.section.split(",")) if args.section else None
        print(f"Reading {args.sitemap} …")
        urls = sitemap_urls(args.sitemap, sections)

    print(f"\n{len(urls)} URL(s) in scope.")

    if not args.force:
        seen = {r["url"] for r in done if "url" in r and not r.get("error")}
        skipped = sum(1 for u in urls if u in seen)
        urls = [u for u in urls if u not in seen]
        if skipped:
            print(f"{skipped} already inspected (resuming; --force to redo).")

    if args.limit:
        urls = urls[: args.limit]
    if len(urls) > DAILY_QUOTA:
        print(f"⚠ {len(urls)} URLs exceeds the {DAILY_QUOTA}/day quota. "
              f"Checking the first {DAILY_QUOTA}; re-run tomorrow to resume.")
        urls = urls[:DAILY_QUOTA]

    if not urls:
        print("Nothing left to inspect.")
        summarize(done)
        return

    session = build_session(args.key_file)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    fresh: list[dict[str, Any]] = []
    with out_path.open("a", encoding="utf-8") as fh:
        for i, url in enumerate(urls, 1):
            row = inspect(session, url, args.site)
            fresh.append(row)
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()

            state = row.get("error") or row.get("coverage_state") or "?"
            print(f"  [{i}/{len(urls)}] {state}")

            # A permissions failure will repeat for every remaining URL — stop
            # and say so rather than emitting hundreds of identical 403s.
            if row.get("error", "").startswith("403"):
                print("\nAborting: " + row["error"], file=sys.stderr)
                break

            time.sleep(args.delay)

    summarize(done + fresh)
    print(f"\nFull results: {out_path}")


if __name__ == "__main__":
    main()
