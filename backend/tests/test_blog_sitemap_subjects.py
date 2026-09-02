"""Blog sitemap feeds + the slug-keyed source reveal + the `can_access_blog`
retirement — `.claude/plans/blog_subjects.md` steps 6 and 9 (backend half).

Three things are under test, and they are here together because they are the
three places the public blog wing stopped being `blog_posts`:

    library_service.sitemap_blog_urls            → public_blogs SLUGS, not tokens
    library_service.sitemap_blog_subject_urls    → the `blog-subjects` section
    GET /public/blogs/{slug}/references/{n}/source   → the reveal, re-keyed
    §8: users.can_access_blog is no longer read by anything

No live DB. `FakeDB` is imported from `test_public_blogs` rather than re-faked —
it already enforces migration 153's three unique indexes, which is what lets a
"non-current version" fixture here be a real two-row history instead of a prop.
"""
from __future__ import annotations

import urllib.parse
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from backend.app.deps import get_supabase
from backend.app.middleware.route_limits import library_rate_limit
from backend.app.services import library_service, public_blog_service
# `_body` strips a function's docstring before a source assertion. Imported
# rather than re-written: Python 3.13 dedents `__doc__`, so the obvious
# `src.replace(fn.__doc__, "")` silently does nothing and the assertion passes
# on prose. One implementation, one place to be wrong.
from backend.tests.test_blog_snapshot_gating import _body
from backend.tests.test_public_blogs import ARABIC_SLUG, FakeDB

BASE = "https://rayhanai.com"

# A second Arabic slug, for the "two blogs, one subject" fixtures. Copied in the
# shape D4 mints — Arabic, hyphenated — never an ASCII transliteration, which
# migration 153's CHECK would reject as a SUBJECT slug in disguise.
ARABIC_SLUG_2 = "شروط-سند-الأمر"


# ---------------------------------------------------------------------------
# 1. The `blog` section — public_blogs slugs, never blog_posts tokens
# ---------------------------------------------------------------------------


def test_the_blog_feed_emits_public_blog_slugs() -> None:
    db = FakeDB()
    db.seed_blog(updated_at="2026-09-02T10:00:00+00:00")

    urls, total_pages = library_service.sitemap_blog_urls(db, BASE)

    assert total_pages == 1
    assert len(urls) == 1
    # Percent-encoded in `<loc>` like every other Arabic-slug wing, and decoding
    # once must give back the slug itself — not a token, not an id.
    assert urllib.parse.unquote(urls[0]["loc"]) == f"{BASE}/blog/{ARABIC_SLUG}"
    assert "%" in urls[0]["loc"]


def test_the_blog_feed_never_touches_blog_posts() -> None:
    """The 99 legacy share links are unlisted by design and §7 makes them
    `noindex`. A sitemap listing a page that marks itself noindex is the
    "Submitted URL marked noindex" self-contradiction — so the table switch and
    the robots switch are one decision, and this asserts half of it."""
    db = FakeDB()
    db.seed_blog()

    library_service.sitemap_blog_urls(db, BASE)

    assert [c for c in db.calls if c[1] == "public_blogs"]
    assert not [c for c in db.calls if c[1] == "blog_posts"]


def test_lastmod_is_the_current_versions_updated_at() -> None:
    """A rewrite appends a version and bumps `updated_at`. That IS a freshness
    signal — the bytes at the URL really did change — so the feed reports it."""
    db = FakeDB()
    db.seed_blog(
        blog_id="v1",
        root_id="root-1",
        version_no=1,
        is_current=False,
        updated_at="2026-09-01T00:00:00+00:00",
    )
    db.seed_blog(
        blog_id="v2",
        root_id="root-1",
        version_no=2,
        is_current=True,
        updated_at="2026-09-30T12:00:00+00:00",
    )

    urls, _ = library_service.sitemap_blog_urls(db, BASE)

    assert len(urls) == 1, "a superseded version is not a second URL"
    assert urls[0]["lastmod"] == "2026-09-30T12:00:00+00:00"


@pytest.mark.parametrize(
    "over",
    [
        pytest.param({"is_current": False}, id="superseded-version"),
        pytest.param({"is_public": False}, id="retracted"),
        pytest.param({"is_published": False}, id="unpublished-draft"),
        pytest.param({"deleted_at": "2026-09-02T00:00:00+00:00"}, id="deleted"),
    ],
)
def test_the_blog_feed_excludes_everything_the_gallery_excludes(over: dict) -> None:
    """One predicate, four ways to fail it. Retraction is the sharp one: it
    flips `is_public` and NOTHING else, precisely so the URL keeps resolving
    while it leaves every index — a feed that kept listing it would keep
    resubmitting an article the operator pulled."""
    db = FakeDB()
    db.seed_blog(blog_id="hidden", root_id="hidden", **over)

    urls, total_pages = library_service.sitemap_blog_urls(db, BASE)

    assert urls == []
    assert total_pages == 1  # never 0 — the section still serves a valid urlset


def test_the_blog_feed_pages() -> None:
    db = FakeDB()
    for i in range(3):
        db.seed_blog(
            blog_id=f"b{i}",
            root_id=f"b{i}",
            slug=f"{ARABIC_SLUG}-{i}",
            created_at=f"2026-09-0{i + 1}T00:00:00+00:00",
        )

    page1, total_pages = library_service.sitemap_blog_urls(db, BASE, 1, page_size=2)
    page2, _ = library_service.sitemap_blog_urls(db, BASE, 2, page_size=2)
    past_end, _ = library_service.sitemap_blog_urls(db, BASE, 9, page_size=2)

    assert total_pages == 2
    assert len(page1) == 2 and len(page2) == 1
    assert past_end == []


# ---------------------------------------------------------------------------
# 2. The `blog-subjects` section — the >=1 contract
# ---------------------------------------------------------------------------


def _attach(db: FakeDB, root_id: str, subject_id: str) -> None:
    db.tables["public_blog_subjects"].append(
        {"root_id": root_id, "subject_id": subject_id}
    )


def test_a_subject_with_no_public_blog_is_absent_from_the_feed() -> None:
    """⚠ THE CONTRACT, not an optimization. The vocabulary is seeded ahead of
    the content it will carry (3 today, ~100 planned), so most subjects sit at
    zero for months. `SITEMAP_SECTIONS`' own comment records why `courts` was
    removed: *a listed section with an empty urlset is a file Google refetches
    hourly to learn nothing.* An empty subject page is that same file, one URL
    at a time."""
    db = FakeDB()
    db.seed_subjects()
    db.seed_blog(root_id="root-1", blog_id="root-1")
    _attach(db, "root-1", "s-work")

    urls, total_pages = library_service.sitemap_blog_subject_urls(db, BASE)

    assert total_pages == 1
    assert [u["loc"] for u in urls] == [f"{BASE}/blog/work-law"]
    # The other two seeded subjects exist and are active — they are simply empty.
    assert len(public_blog_service.list_subjects(db)) == 3


def test_an_entirely_empty_vocabulary_yields_an_empty_but_valid_feed() -> None:
    db = FakeDB()
    db.seed_subjects()

    urls, total_pages = library_service.sitemap_blog_subject_urls(db, BASE)

    assert urls == []
    assert total_pages == 1


def test_a_subject_whose_only_blog_was_retracted_drops_out() -> None:
    """Delisting a blog delists its subject page too, when that blog was the
    only thing on it — otherwise retraction leaves a listed URL rendering an
    empty list, which is the worst of both postures."""
    db = FakeDB()
    db.seed_subjects()
    db.seed_blog(root_id="root-1", blog_id="root-1", is_public=False)
    _attach(db, "root-1", "s-work")

    urls, _ = library_service.sitemap_blog_subject_urls(db, BASE)

    assert urls == []


def test_an_inactive_subject_is_absent_even_with_public_blogs() -> None:
    """Retiring a subject is `is_active=false` (never a delete — the join FK is
    RESTRICT), and `get_subject_by_slug` 404s it. The feed must agree with the
    page: listing it would submit a URL that 404s."""
    db = FakeDB()
    db.seed_subjects()
    db.tables["blog_subjects"][0]["is_active"] = False   # work-law
    db.seed_blog(root_id="root-1", blog_id="root-1")
    _attach(db, "root-1", "s-work")

    urls, _ = library_service.sitemap_blog_subject_urls(db, BASE)

    assert urls == []


def test_the_subject_feed_lists_every_subject_that_qualifies() -> None:
    db = FakeDB()
    db.seed_subjects()
    db.seed_blog(root_id="root-1", blog_id="root-1")
    db.seed_blog(root_id="root-2", blog_id="root-2", slug=ARABIC_SLUG_2)
    _attach(db, "root-1", "s-work")
    _attach(db, "root-1", "s-saud")   # D2 — many-to-many
    _attach(db, "root-2", "s-note")

    urls, _ = library_service.sitemap_blog_subject_urls(db, BASE)

    # sort_rank order (10/20/30), the vocabulary's own order — stable across
    # fetches so a page cannot re-shuffle under a crawler.
    assert [u["loc"] for u in urls] == [
        f"{BASE}/blog/work-law",
        f"{BASE}/blog/promissory-note",
        f"{BASE}/blog/saudization",
    ]
    assert all(u["lastmod"] is None for u in urls)


# ---------------------------------------------------------------------------
# 3. The dispatcher — GET /public/library/sitemap/{section}
# ---------------------------------------------------------------------------


def _sitemap_client(db: FakeDB) -> TestClient:
    from backend.app.main import create_app

    app = create_app()
    app.dependency_overrides[get_supabase] = lambda: db
    app.dependency_overrides[library_rate_limit] = lambda: None
    return TestClient(app, client=("8.8.8.8", 51000))


def test_the_blog_subjects_section_is_wired_into_the_dispatcher() -> None:
    db = FakeDB()
    db.seed_subjects()
    db.seed_blog(root_id="root-1", blog_id="root-1")
    _attach(db, "root-1", "s-work")

    res = _sitemap_client(db).get("/api/v1/public/library/sitemap/blog-subjects")

    assert res.status_code == 200, res.text
    body = res.json()
    assert [u["loc"].split("/blog/")[-1] for u in body["urls"]] == ["work-law"]
    assert body["page"] == 1 and body["total_pages"] == 1
    assert res.headers["cache-control"] == "public, max-age=3600"


def test_the_blog_section_serves_slugs_through_the_dispatcher() -> None:
    db = FakeDB()
    db.seed_blog()

    body = _sitemap_client(db).get("/api/v1/public/library/sitemap/blog").json()

    assert len(body["urls"]) == 1
    assert urllib.parse.unquote(body["urls"][0]["loc"]).endswith(f"/blog/{ARABIC_SLUG}")


def test_an_unknown_section_still_404s_in_arabic() -> None:
    """The new branch must not have widened the dispatcher: `blog-subject`
    (singular, a plausible typo) is not a section."""
    res = _sitemap_client(FakeDB()).get("/api/v1/public/library/sitemap/blog-subject")

    assert res.status_code == 404
    assert "القسم غير موجود" in res.text


# ---------------------------------------------------------------------------
# 4. The slug-keyed source reveal — anon still gets a 402, never a 401
# ---------------------------------------------------------------------------


class _Resolved:
    """A `ResolvedRef` stand-in: resolution needs the corpus, entitlement does
    not, and it is entitlement under test here."""

    content_type = "regulation"
    content_id = "cccc3333-3333-4333-8333-333333333333"
    title = "نظام العمل"
    parent_regulation_id = None
    article_no = None
    always_free = False
    free_reason = ""


def _reveal_client(monkeypatch, db: FakeDB, resolved: Optional[Any] = _Resolved()):
    from backend.app.api import blog as blog_api
    from backend.app.main import create_app

    async def _resolve_ref(_supabase, _ref_id, domain="", item_id=None):
        return resolved

    monkeypatch.setattr(blog_api, "resolve_ref", _resolve_ref)

    app = create_app()
    app.dependency_overrides[get_supabase] = lambda: db
    app.dependency_overrides[library_rate_limit] = lambda: None
    return TestClient(app)


def _seed_cited_blog(db: FakeDB) -> None:
    db.seed_blog(
        root_id="root-1",
        blog_id="root-1",
        references_json=[
            {"n": 1, "ref_id": "reg:cccc3333", "domain": "regulation", "title": "مرجع"}
        ],
    )


def _reveal_url(slug: str = ARABIC_SLUG, n: int = 1) -> str:
    return f"/api/v1/public/blogs/{urllib.parse.quote(slug, safe='')}/references/{n}/source"


def test_an_anonymous_reader_gets_402_not_401(monkeypatch) -> None:
    """⚠ THE INVARIANT THAT CARRIED OVER FROM THE TOKEN ROUTE. A 401 on a public
    page trips the frontend's global redirect-to-login and throws the reader off
    the article. The refusal has to be a 402 «سجّل مجاناً» card that leaves them
    where they are — and it must carry no source bytes at all."""
    db = FakeDB()
    _seed_cited_blog(db)

    res = _reveal_client(monkeypatch, db).get(_reveal_url())

    assert res.status_code == 402, res.text
    body = res.json()
    assert body["reason"] == "anonymous"
    assert "source_view" not in body
    assert res.headers["cache-control"] == "private, no-store"


def test_the_reveal_does_not_bump_the_view_count(monkeypatch) -> None:
    """A reference click is not a page view. `get_by_slug` increments the
    counter; the reveal deliberately does not go through it."""
    db = FakeDB()
    _seed_cited_blog(db)

    _reveal_client(monkeypatch, db).get(_reveal_url())

    assert db.tables["public_blogs"][0]["view_count"] == 0
    assert not [c for c in db.calls if c[0] == "update"]


def test_an_unknown_slug_404s_in_the_wings_own_words(monkeypatch) -> None:
    db = FakeDB()
    _seed_cited_blog(db)

    res = _reveal_client(monkeypatch, db).get(_reveal_url(slug="لا-يوجد"))

    assert res.status_code == 404
    assert "المدونة غير موجودة" in res.text


def test_an_unknown_citation_number_404s(monkeypatch) -> None:
    db = FakeDB()
    _seed_cited_blog(db)

    res = _reveal_client(monkeypatch, db).get(_reveal_url(n=9))

    assert res.status_code == 404
    assert "المرجع غير موجود" in res.text


def test_a_retracted_blogs_sources_stay_reachable(monkeypatch) -> None:
    """Retraction delists; it does not delete. The article keeps resolving for
    anyone holding the link, so its sources must too — otherwise «عرض المصدر»
    breaks on a page that still renders."""
    db = FakeDB()
    db.seed_blog(
        root_id="root-1",
        blog_id="root-1",
        is_public=False,
        references_json=[{"n": 1, "ref_id": "reg:cccc3333", "domain": "regulation"}],
    )

    res = _reveal_client(monkeypatch, db).get(_reveal_url())

    # 402 (the anon meter), NOT 404 — the reveal found the blog.
    assert res.status_code == 402, res.text


def test_an_unpublished_draft_is_not_reachable(monkeypatch) -> None:
    """`is_published=false` is the confidence gate holding an article back. That
    one IS a 404 — it has never been readable by anyone, sources included."""
    db = FakeDB()
    db.seed_blog(
        root_id="root-1",
        blog_id="root-1",
        is_published=False,
        references_json=[{"n": 1, "ref_id": "reg:cccc3333", "domain": "regulation"}],
    )

    res = _reveal_client(monkeypatch, db).get(_reveal_url())

    assert res.status_code == 404


def test_get_references_by_slug_distinguishes_missing_from_uncited() -> None:
    """`None` (no such blog → 404) and `[]` (a blog that cites nothing → 404 on
    the reference, not on the article) are different answers."""
    db = FakeDB()
    db.seed_blog(root_id="root-1", blog_id="root-1", references_json=[])

    assert public_blog_service.get_references_by_slug(db, ARABIC_SLUG) == []
    assert public_blog_service.get_references_by_slug(db, "لا-يوجد") is None
    assert public_blog_service.get_references_by_slug(db, "") is None


def test_a_frozen_source_view_is_stripped_before_it_reaches_a_reader() -> None:
    """Same defence the article body gets: a stored `source_view` on this wing
    would be an unmetered, anon-readable mirror of full corpus text — the exact
    hole the reveal meter exists to close.

    The BODY goes; the FACT that a body exists stays (`has_source=True`,
    `source_view=None`), so «عرض المصدر» still renders and the reader reveals it
    through the meter instead of losing the affordance."""
    db = FakeDB()
    db.seed_blog(
        root_id="root-1",
        blog_id="root-1",
        references_json=[
            {"n": 1, "ref_id": "reg:x", "source_view": {"content": "النص الكامل"}}
        ],
    )

    refs = public_blog_service.get_references_by_slug(db, ARABIC_SLUG)

    assert refs is not None
    assert refs[0]["source_view"] is None
    assert refs[0]["has_source"] is True
    assert "النص الكامل" not in str(refs)


# ---------------------------------------------------------------------------
# 5. §8 — `can_access_blog` is retired as a gate
# ---------------------------------------------------------------------------


def test_the_curation_gate_helper_is_gone() -> None:
    from backend.app.services import blog_service

    assert not hasattr(blog_service, "user_can_access_blog")
    assert "user_can_access_blog" not in blog_service.__all__


def test_publishing_is_owner_scoped_and_nothing_else() -> None:
    """The gate never granted the power it looked like it granted: reaching
    another user's post is blocked by OWNERSHIP, which no user-facing flag can
    lift. Removing it changes who can publish THEIR OWN post, and nothing else."""
    from backend.app.api import blog

    src = _body(blog.publish_blog_public)
    assert "can_access_blog" not in src
    assert "403" not in src
    assert "غير مصرح لك بالنشر في المدونة العامة" not in src
    # Still owner-scoped: the service call is keyed by the caller's user_id.
    assert "get_user_id" in src and "set_post_public" in src


def test_my_blogs_no_longer_advertises_a_curation_flag() -> None:
    from backend.app.api import blog
    from backend.app.models.responses import MyBlogsResponse

    assert "can_publish_public" not in MyBlogsResponse.model_fields
    # ⚠ Pydantic IGNORES an unknown kwarg rather than raising, so a handler
    # still passing `can_publish_public=` would drop it silently and this model
    # check alone would not notice.
    assert "can_publish_public" not in _body(blog.list_my_blogs)


def test_the_openapi_document_stops_promising_the_flag() -> None:
    """`response_model` strips undeclared keys, so a client still reading
    `can_publish_public` would get `undefined` rather than an error. The
    generated contract has to say so out loud."""
    from backend.app.main import create_app

    schema = create_app().openapi()["components"]["schemas"]["MyBlogsResponse"]

    assert "can_publish_public" not in (schema.get("properties") or {})
    assert "posts" in schema["properties"]


def test_nothing_in_the_backend_reads_the_column_any_more() -> None:
    """The COLUMN stays in the DB, dormant — dropping it is a migration with no
    upside and a real drift risk (`conversations.case_id` is the precedent). But
    no code may read it.

    Looks for the QUOTED column name, which is the only way PostgREST can be
    asked for it (`select("can_access_blog")`, `row["can_access_blog"]`). Prose
    about the retirement is expected and must stay — several docstrings explain
    why the flag is gone, and a bare-substring scan would forbid the
    explanation along with the code."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2] / "backend" / "app"
    offenders = [
        str(p.relative_to(root))
        for p in root.rglob("*.py")
        if any(
            q in p.read_text(encoding="utf-8")
            for q in ('"can_access_blog"', "'can_access_blog'")
        )
    ]

    assert offenders == []
