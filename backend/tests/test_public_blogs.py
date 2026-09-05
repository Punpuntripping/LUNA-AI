"""Public blog wing tests (.claude/plans/blog_subjects.md steps 2 + 3).

Covers the anonymous read surface and the versioned write path:

    GET /api/v1/public/blogs                    the gallery feed
    GET /api/v1/public/blogs/subjects           the browse vocabulary
    GET /api/v1/public/blogs/subjects/{slug}    one subject's blogs
    GET /api/v1/public/blogs/{slug}             one blog, CURRENT version

    public_blog_service.insert_public_blog / append_version / set_public
                       / attach_subjects / assert_slug_available
                       / extract_headline

No live DB. ``FakeDB`` is a small in-memory PostgREST stand-in that ENFORCES the
four unique indexes this table declares — the three from migration 153,
including the partial ``idx_public_blogs_current`` (one live version per
``root_id``), plus ``idx_public_blogs_job`` from migration 156 (one editorial
job publishes at most one blog). That is what makes
``test_append_version_leaves_exactly_one_current`` — and the duplicate-publish
tests in ``test_editorial_publishing.py``, which drive this same fake — real
assertions rather than restatements of the code under test: a botched flip, or a
second publish from one job, raises here the way it would in Postgres.
"""
from __future__ import annotations

from typing import Any, Optional

import pytest

from backend.app.errors import LunaHTTPException
from backend.app.services import public_blog_service as svc


# ---------------------------------------------------------------------------
# Fake PostgREST
# ---------------------------------------------------------------------------


class _Result:
    def __init__(self, data: Any, count: Optional[int] = None) -> None:
        self.data = data
        self.count = count


class _Query:
    def __init__(self, db: "FakeDB", table: str) -> None:
        self._db = db
        self._table = table
        self._op = "select"
        self._payload: Any = None
        self._filters: list[tuple] = []
        self._orders: list[tuple[str, bool]] = []
        self._limit: Optional[int] = None
        self._range: Optional[tuple[int, int]] = None
        self._count: Optional[str] = None
        self._on_conflict: Optional[str] = None
        self._ignore_duplicates = False

    # -- builders ---------------------------------------------------------
    def select(self, *_cols: Any, count: Optional[str] = None) -> "_Query":
        self._op = "select"
        self._count = count
        return self

    def insert(self, payload: Any) -> "_Query":
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: Any) -> "_Query":
        self._op = "update"
        self._payload = payload
        return self

    def upsert(
        self,
        payload: Any,
        on_conflict: Optional[str] = None,
        ignore_duplicates: bool = False,
    ) -> "_Query":
        self._op = "upsert"
        self._payload = payload
        self._on_conflict = on_conflict
        self._ignore_duplicates = ignore_duplicates
        return self

    def delete(self) -> "_Query":
        self._op = "delete"
        return self

    def eq(self, col: str, val: Any) -> "_Query":
        self._filters.append(("eq", col, val))
        return self

    def is_(self, col: str, val: Any) -> "_Query":
        self._filters.append(("is", col, val))
        return self

    def in_(self, col: str, vals: Any) -> "_Query":
        self._filters.append(("in", col, list(vals)))
        return self

    def order(self, col: str, desc: bool = False) -> "_Query":
        self._orders.append((col, desc))
        return self

    def limit(self, n: int) -> "_Query":
        self._limit = int(n)
        return self

    def range(self, start: int, end: int) -> "_Query":
        self._range = (int(start), int(end))
        return self

    # -- execution --------------------------------------------------------
    def _matches(self, row: dict) -> bool:
        for kind, col, val in self._filters:
            if kind == "eq" and row.get(col) != val:
                return False
            if kind == "is":
                if val == "null" and row.get(col) is not None:
                    return False
            if kind == "in" and row.get(col) not in val:
                return False
        return True

    def _checked(self, rollback: list[dict]) -> None:
        """Run the index checks; restore the pre-statement rows if one fires."""
        try:
            self._db._check_indexes(self._table)
        except Exception:
            self._db.tables[self._table] = rollback
            raise

    def execute(self) -> _Result:
        rows = self._db.tables.setdefault(self._table, [])
        self._db.calls.append((self._op, self._table, list(self._filters)))
        # Cheap: only the write branches consult it, and these tables hold a
        # handful of rows.
        rollback = [dict(r) for r in rows] if self._op != "select" else []

        if self._op == "select":
            hits = [r for r in rows if self._matches(r)]
            for col, desc in reversed(self._orders):
                hits.sort(key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
            total = len(hits)
            if self._range is not None:
                start, end = self._range
                hits = hits[start : end + 1]
            elif self._limit is not None:
                hits = hits[: self._limit]
            return _Result(
                [dict(r) for r in hits], total if self._count == "exact" else None
            )

        if self._op in ("insert", "upsert"):
            payload = self._payload
            incoming = payload if isinstance(payload, list) else [payload]
            written: list[dict] = []
            for item in incoming:
                if self._op == "upsert" and self._on_conflict:
                    keys = [k.strip() for k in self._on_conflict.split(",")]
                    dupe = next(
                        (
                            r
                            for r in rows
                            if all(r.get(k) == item.get(k) for k in keys)
                        ),
                        None,
                    )
                    if dupe is not None:
                        if self._ignore_duplicates:
                            continue  # PostgREST returns zero rows for an ignored conflict
                        dupe.update(item)
                        written.append(dict(dupe))
                        continue
                row = self._db._with_defaults(self._table, dict(item))
                rows.append(row)
                written.append(dict(row))
            # ⚠ A REFUSED STATEMENT LEAVES NOTHING BEHIND. Postgres rolls the
            # whole INSERT back when a unique index rejects it; a fake that
            # appended first and only then raised would leave the duplicate
            # sitting in the table, and every "exactly one row" assertion
            # downstream would be measuring the fake's debris rather than the
            # constraint. That is not hypothetical — it made the migration-156
            # duplicate-publish tests fail against CORRECT code.
            self._checked(rollback)
            return _Result(written)

        if self._op == "update":
            rollback = [dict(r) for r in rows]
            hits = [r for r in rows if self._matches(r)]
            for r in hits:
                r.update(self._payload or {})
            self._checked(rollback)
            return _Result([dict(r) for r in hits])

        if self._op == "delete":
            hits = [r for r in rows if self._matches(r)]
            self._db.tables[self._table] = [r for r in rows if not self._matches(r)]
            return _Result([dict(r) for r in hits])

        raise AssertionError(f"unsupported op {self._op}")


class _PostgrestError(RuntimeError):
    """Stands in for postgrest-py's APIError: a message plus a SQLSTATE."""

    def __init__(self, message: str, code: str = "") -> None:
        super().__init__(message)
        self.code = code


class _UniqueViolation(_PostgrestError):
    """Stands in for Postgres 23505 as PostgREST surfaces it."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="23505")


class _Rpc:
    """``append_public_blog_version()`` (migration 155) as ONE transaction.

    The real function locks the current version ``FOR UPDATE``, demotes it and
    inserts N+1 as current inside a single implicit transaction. The fake models
    the property that matters to the service layer: on any constraint failure
    the whole thing rolls back, so a caller can never observe a half-applied
    flip. ``rpc_calls`` records invocations so a test can assert the service
    made exactly ONE round trip.
    """

    def __init__(self, db: "FakeDB", name: str, params: dict) -> None:
        self._db = db
        self._name = name
        self._params = params

    def execute(self) -> _Result:
        db = self._db
        db.rpc_calls.append((self._name, dict(self._params)))
        if db.rpc_error is not None:
            raise db.rpc_error
        assert self._name == "append_public_blog_version", self._name
        p = self._params
        rows = db.tables["public_blogs"]

        cur = next(
            (
                r
                for r in rows
                if r.get("root_id") == p["p_root_id"]
                and r.get("is_current")
                and r.get("deleted_at") is None
            ),
            None,
        )
        if cur is None:
            raise _PostgrestError(
                f"no current version for root_id {p['p_root_id']}", code="P0002"
            )

        rollback = [dict(r) for r in rows]
        try:
            cur["is_current"] = False
            new = db._with_defaults(
                "public_blogs",
                {
                    "blog_id": f"version-{len(rows) + 1}",
                    "root_id": cur["root_id"],
                    "version_no": int(cur["version_no"]) + 1,
                    "is_current": True,
                    "revision_note": p.get("p_revision_note"),
                    "slug": cur["slug"],                       # PERMANENT
                    "title": p.get("p_title") or cur["title"],
                    "type": p.get("p_type") or cur["type"],
                    "question_text": cur["question_text"],
                    "content_md": p["p_content_md"],
                    "references_json": cur["references_json"],  # VERBATIM
                    "subtype": cur.get("subtype"),
                    "source_item_id": cur.get("source_item_id"),
                    "author_user_id": cur["author_user_id"],
                    "confidence": p.get("p_confidence") or cur.get("confidence"),
                    "is_public": cur["is_public"],
                    "is_published": cur["is_published"],
                    "view_count": 0,
                    # migration 158 — both columns added by 157 are carried
                    # FORWARD by the RPC, not re-stamped by the caller. Without
                    # this the fake would let a rewrite silently reset an
                    # approved article to `pending` and drop the generation
                    # record from v2 on, which is the exact bug 158 exists for.
                    "review_status": cur.get("review_status"),
                    "generation_context": cur.get("generation_context"),
                },
            )
            rows.append(new)
            db._check_indexes("public_blogs")
        except Exception:
            db.tables["public_blogs"] = rollback   # the transaction aborts
            raise
        return _Result(dict(new))


class FakeDB:
    """In-memory stand-in for the three tables, WITH migration 153's indexes."""

    _DEFAULTS = {
        "public_blogs": {
            "version_no": 1,
            "is_current": True,
            "is_public": True,
            "is_published": True,
            "view_count": 0,
            "deleted_at": None,
            "job_id": None,
            "references_json": [],
            # migration 157 — the column DEFAULT, mirrored. Nothing in the
            # service filters on `review_status`, so this only ever shows up in
            # a write assertion.
            "review_status": "pending",
            "generation_context": None,
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        },
        "blog_subjects": {"is_active": True, "sort_rank": 0, "description_ar": None},
        "public_blog_subjects": {},
    }

    def __init__(self) -> None:
        self.tables: dict[str, list[dict]] = {
            "public_blogs": [],
            "blog_subjects": [],
            "public_blog_subjects": [],
        }
        self.calls: list[tuple] = []
        self.rpc_calls: list[tuple] = []
        self.rpc_error: Optional[BaseException] = None

    def table(self, name: str) -> _Query:
        return _Query(self, name)

    def rpc(self, name: str, params: dict) -> _Rpc:
        return _Rpc(self, name, params)

    def _with_defaults(self, table: str, row: dict) -> dict:
        return {**self._DEFAULTS.get(table, {}), **row}

    def _check_indexes(self, table: str) -> None:
        if table != "public_blogs":
            return
        rows = self.tables["public_blogs"]

        # idx_public_blogs_current — UNIQUE(root_id) WHERE is_current AND NOT deleted
        live = [r for r in rows if r.get("is_current") and r.get("deleted_at") is None]
        roots = [r.get("root_id") for r in live]
        if len(roots) != len(set(roots)):
            raise _UniqueViolation(
                "duplicate key value violates unique constraint "
                "\"idx_public_blogs_current\""
            )

        # idx_public_blogs_slug — UNIQUE(slug) WHERE is_current AND NOT deleted
        slugs = [r.get("slug") for r in live]
        if len(slugs) != len(set(slugs)):
            raise _UniqueViolation(
                "duplicate key value violates unique constraint "
                "\"idx_public_blogs_slug\""
            )

        # idx_public_blogs_version — UNIQUE(root_id, version_no), no predicate
        versions = [(r.get("root_id"), r.get("version_no")) for r in rows]
        if len(versions) != len(set(versions)):
            raise _UniqueViolation(
                "duplicate key value violates unique constraint "
                "\"idx_public_blogs_version\""
            )

        # idx_public_blogs_job — UNIQUE(job_id) WHERE job_id IS NOT NULL AND
        # NOT deleted (migration 156). One editorial job publishes at most one
        # BLOG. ⚠ Versions carry NULL here — append_public_blog_version does not
        # copy the column — so this constrains v1 rows only, which is why the
        # NULLs have to be filtered out rather than counted as duplicates.
        jobs = [
            r.get("job_id")
            for r in rows
            if r.get("job_id") is not None and r.get("deleted_at") is None
        ]
        if len(jobs) != len(set(jobs)):
            raise _UniqueViolation(
                "duplicate key value violates unique constraint "
                "\"idx_public_blogs_job\""
            )

    # -- seeding ----------------------------------------------------------
    def seed_subjects(self) -> None:
        """The three seeded by migration 154 — labels copied, never retyped."""
        for sid, slug, label, rank in (
            ("s-work", "work-law", "نظام العمل", 10),
            ("s-note", "promissory-note", "سند الأمر", 20),
            ("s-saud", "saudization", "السعودة", 30),
        ):
            self.tables["blog_subjects"].append(
                self._with_defaults(
                    "blog_subjects",
                    {
                        "subject_id": sid,
                        "slug": slug,
                        "label_ar": label,
                        "sort_rank": rank,
                    },
                )
            )

    def seed_blog(self, **over: Any) -> dict:
        blog_id = over.pop("blog_id", "blog-1")
        row = self._with_defaults(
            "public_blogs",
            {
                "blog_id": blog_id,
                "root_id": over.pop("root_id", blog_id),
                "slug": "حقوق-العامل-عند-إنهاء-العقد",
                "title": "حقوق العامل عند إنهاء العقد",
                "type": "laws_explanation",
                "question_text": "سؤال مجهول الهوية",
                "content_md": "## أولاً: القاعدة\nنص.",
                "references_json": [{"n": 1, "title": "مرجع"}],
                "author_user_id": "editorial-bot",
                **over,
            },
        )
        self.tables["public_blogs"].append(row)
        return row


ARABIC_SLUG = "حقوق-العامل-عند-إنهاء-العقد"

# A rewritten body, as the SEO agent would hand it over: headline on the first
# line (plan §6). Written as a triple-quoted literal on purpose — no escapes.
REWRITE_MD = """# عنوان مُحسَّن

نص مُعاد كتابته."""

SECOND_VERSION_MD = """# النسخة الثانية

نص النسخة الثانية."""


# ---------------------------------------------------------------------------
# Route inventory + ordering
# ---------------------------------------------------------------------------


def test_public_blog_routes_registered() -> None:
    from backend.app.main import create_app

    app = create_app()
    paths = [getattr(r, "path", "") for r in app.routes]
    for p in (
        "/api/v1/public/blogs",
        "/api/v1/public/blogs/subjects",
        "/api/v1/public/blogs/subjects/{slug}",
        "/api/v1/public/blogs/{slug}",
    ):
        assert p in paths, p

    # The literal `subjects` segment must be matched BEFORE `{slug}`, or the
    # subject index would be read as a blog slug.
    assert paths.index("/api/v1/public/blogs/subjects") < paths.index(
        "/api/v1/public/blogs/{slug}"
    )


def test_the_legacy_blog_posts_gallery_is_gone() -> None:
    """`api/blog.py` used to declare `GET /public/blogs` too. It was DELETED
    rather than shadowed: routing is by registration order but the OpenAPI
    document is keyed by PATH, so a surviving duplicate would have left /docs
    and every generated client describing a handler that can never run."""
    from backend.app.main import create_app

    app = create_app()
    gallery = [
        r
        for r in app.routes
        if getattr(r, "path", "") == "/api/v1/public/blogs"
        and "GET" in (getattr(r, "methods", None) or set())
    ]
    assert len(gallery) == 1
    assert gallery[0].endpoint.__module__ == "backend.app.api.public_blogs"

    documented = app.openapi()["paths"]["/api/v1/public/blogs"]["get"]["responses"][
        "200"
    ]["content"]["application/json"]["schema"]
    assert documented["$ref"].endswith("PublicBlogListResponse")


def test_the_legacy_gallery_service_function_is_gone() -> None:
    from backend.app.services import blog_service

    assert not hasattr(blog_service, "list_public_blogs")
    assert "list_public_blogs" not in blog_service.__all__


def test_blog_card_models_survive_for_the_frontend() -> None:
    """`BlogCardPublic` / `PublicBlogsResponse` stay in responses.py: the
    frontend still imports those TYPES until step 4 rewrites app/blog/page.tsx.
    Deleting the route is not deleting the shape."""
    from backend.app.models import responses

    assert hasattr(responses, "BlogCardPublic")
    assert hasattr(responses, "PublicBlogsResponse")


def test_detail_response_model_declares_every_key() -> None:
    """`response_model` STRIPS undeclared keys — a dropped `is_public` would
    silently stop the frontend from ever setting robots: noindex."""
    from backend.app.models.responses import PublicBlogDetailResponse

    assert {
        "is_public",
        "slug",
        "title",
        "type",
        "subjects",
        "content_md",
        "references",
        "question_text",
        "created_at",
        "updated_at",
    } <= set(PublicBlogDetailResponse.model_fields)


# ---------------------------------------------------------------------------
# Headline extract + strip (plan §6, "The H1 / title contract")
# ---------------------------------------------------------------------------


def test_h1_extraction_fills_title_and_strips_the_line() -> None:
    body = (
        "# متى يسقط الحق في المطالبة بالأجر؟\n"
        "\n"
        "يواجه كثير من العاملين معضلة عملية.\n"
        "\n"
        "## أولاً: النظام لا يمنع\n"
        "نص القسم."
    )
    title, out = svc.extract_headline(body)
    assert title == "متى يسقط الحق في المطالبة بالأجر؟"
    assert out.startswith("يواجه كثير من العاملين")
    assert "# متى يسقط" not in out
    # `##` section headings are the TOC entries — they must survive.
    assert "## أولاً: النظام لا يمنع" in out


def test_supplied_title_wins_but_h1_is_still_stripped() -> None:
    body = "# عنوان من الوكيل\n\nنص المقال."
    title, out = svc.extract_headline(body, title="عنوان من الطلب")
    assert title == "عنوان من الطلب"
    assert "عنوان من الوكيل" not in out
    assert out == "نص المقال."


def test_h1_only_stripped_from_the_first_line() -> None:
    body = "مقدمة بلا عنوان.\n\n# ليس عنواناً رئيسياً\n\nنص."
    title, out = svc.extract_headline(body, title="عنوان")
    assert title == "عنوان"
    assert "# ليس عنواناً رئيسياً" in out


def test_no_title_and_no_h1_is_a_clean_400() -> None:
    with pytest.raises(LunaHTTPException) as exc:
        svc.extract_headline("نص بلا عنوان.")
    assert exc.value.status_code == 400
    assert exc.value.detail == "لا يمكن نشر مدونة بدون عنوان"


def test_h2_is_never_mistaken_for_a_headline() -> None:
    extracted, body = svc.split_headline("## أولاً\nنص.")
    assert extracted is None
    assert body == "## أولاً\nنص."


# ---------------------------------------------------------------------------
# Mint-time slug refusal (plan §3)
# ---------------------------------------------------------------------------


def test_reserved_subjects_slug_is_refused() -> None:
    db = FakeDB()
    db.seed_subjects()
    with pytest.raises(LunaHTTPException) as exc:
        svc.assert_slug_available(db, "subjects")
    assert exc.value.status_code == 400
    assert "محجوز" in exc.value.detail


def test_slug_colliding_with_a_subject_slug_is_refused() -> None:
    db = FakeDB()
    db.seed_subjects()
    with pytest.raises(LunaHTTPException) as exc:
        svc.assert_slug_available(db, "work-law")
    assert exc.value.status_code == 400
    assert "موضوعاً" in exc.value.detail
    # The refusal is a real vocabulary lookup, not a shape guess.
    assert any(t == "blog_subjects" for _op, t, _f in db.calls)


def test_any_ascii_kebab_slug_is_refused() -> None:
    """Migration 153's CHECK forbids the shape outright — refuse it cleanly
    rather than letting PostgREST raise a constraint error at insert time."""
    db = FakeDB()
    db.seed_subjects()
    with pytest.raises(LunaHTTPException) as exc:
        svc.assert_slug_available(db, "some-future-subject")
    assert exc.value.status_code == 400


def test_duplicate_live_slug_is_a_409() -> None:
    db = FakeDB()
    db.seed_subjects()
    db.seed_blog()
    with pytest.raises(LunaHTTPException) as exc:
        svc.assert_slug_available(db, ARABIC_SLUG)
    assert exc.value.status_code == 409


def test_arabic_slug_is_accepted_and_trimmed() -> None:
    db = FakeDB()
    db.seed_subjects()
    assert svc.assert_slug_available(db, f"  {ARABIC_SLUG}  ") == ARABIC_SLUG


# ---------------------------------------------------------------------------
# insert_public_blog — v1
# ---------------------------------------------------------------------------


def _insert_v1(db: FakeDB, **over: Any) -> dict:
    kwargs: dict[str, Any] = {
        "slug": ARABIC_SLUG,
        "blog_type": "laws_explanation",
        "question_text": "سؤال مجهول الهوية",
        "content_md": "# عنوان المقال\n\nنص المقال.",
        "author_user_id": "editorial-bot",
        "references_json": [{"n": 2, "title": "مرجع"}],
    }
    kwargs.update(over)
    return svc.insert_public_blog(db, **kwargs)


def test_v1_sets_blog_id_equal_to_root_id() -> None:
    """⚠ The caller mints the uuid: root_id self-references blog_id, so v1 is
    its own root (migration 153's header)."""
    db = FakeDB()
    db.seed_subjects()
    row = _insert_v1(db)

    assert row["blog_id"] == row["root_id"]
    assert row["version_no"] == 1
    assert row["is_current"] is True
    # Inverted default vs blog_posts (D17): a public blog is open on arrival.
    assert row["is_public"] is True
    # The headline moved into `title` and left the body.
    assert row["title"] == "عنوان المقال"
    assert row["content_md"] == "نص المقال."
    assert row["slug"] == ARABIC_SLUG


def test_v1_refuses_an_unknown_type() -> None:
    db = FakeDB()
    db.seed_subjects()
    with pytest.raises(LunaHTTPException) as exc:
        _insert_v1(db, blog_type="opinion")
    assert exc.value.status_code == 400
    assert db.tables["public_blogs"] == []


def test_v1_can_land_unlisted() -> None:
    """`publish_public=false` still lands a row — just not in the gallery."""
    db = FakeDB()
    db.seed_subjects()
    row = _insert_v1(db, is_public=False)
    assert row["is_public"] is False
    assert row["is_published"] is True


# ---------------------------------------------------------------------------
# append_version — the versioned rewrite
# ---------------------------------------------------------------------------


def test_append_version_leaves_exactly_one_current() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)

    v2 = svc.append_version(
        db,
        v1["root_id"],
        content_md=REWRITE_MD,
        revision_note="seo: حقوق العامل",
    )

    rows = db.tables["public_blogs"]
    assert len(rows) == 2
    current = [r for r in rows if r["is_current"] and r["deleted_at"] is None]
    assert len(current) == 1
    assert current[0]["blog_id"] == v2["blog_id"]
    assert current[0]["version_no"] == 2
    assert v2["is_current"] is True

    # ONE round trip. Migration 155 does the demote + insert in a single
    # transaction, so the service must not be flipping rows itself any more.
    assert [c[0] for c in db.rpc_calls] == ["append_public_blog_version"]
    assert not [c for c in db.calls if c[0] == "update" and c[1] == "public_blogs"]

    # The slug is PERMANENT across versions — there is no redirect layer.
    assert v2["slug"] == v1["slug"] == ARABIC_SLUG
    # A rewrite MAY change the title.
    assert v2["title"] == "عنوان مُحسَّن"
    assert v2["content_md"] == "نص مُعاد كتابته."   # the H1 was stripped
    assert v2["revision_note"] == "seo: حقوق العامل"


def test_append_version_carries_references_verbatim() -> None:
    """D18: the citation set of a published blog is CLOSED."""
    db = FakeDB()
    db.seed_subjects()
    refs = [{"n": 2, "title": "أ"}, {"n": 5, "title": "ب"}]
    v1 = _insert_v1(db, references_json=refs)

    v2 = svc.append_version(db, v1["root_id"], content_md=REWRITE_MD)
    assert v2["references_json"] == refs


def test_append_version_carries_title_forward_when_the_rewrite_has_no_h1() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    v2 = svc.append_version(db, v1["root_id"], content_md="نص بلا عنوان.")
    assert v2["title"] == v1["title"]
    assert v2["content_md"] == "نص بلا عنوان."


def test_append_version_is_rejected_for_an_unknown_root() -> None:
    """plpgsql raises no_data_found (P0002); PostgREST maps it to 404."""
    db = FakeDB()
    with pytest.raises(LunaHTTPException) as exc:
        svc.append_version(db, "no-such-root", content_md=REWRITE_MD)
    assert exc.value.status_code == 404


def test_the_partial_unique_index_is_actually_enforced_by_the_fake() -> None:
    """Guard on the guard: if FakeDB stopped enforcing
    idx_public_blogs_current, the test above would pass vacuously."""
    db = FakeDB()
    db.seed_blog()
    with pytest.raises(_UniqueViolation):
        db.seed_blog(blog_id="blog-2", root_id="blog-1", version_no=2)
        db._check_indexes("public_blogs")


def test_a_unique_violation_still_surfaces_as_409() -> None:
    """The row lock serializes concurrent appends, but a brand-new blog INSERT
    racing for the same slug is not covered by it. The index failing loudly IS
    the guarantee — never swallow it."""
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    db.rpc_error = _UniqueViolation(
        'duplicate key value violates unique constraint "idx_public_blogs_slug"'
    )

    with pytest.raises(LunaHTTPException) as exc:
        svc.append_version(db, v1["root_id"], content_md=REWRITE_MD)
    assert exc.value.status_code == 409


def test_an_aborted_append_leaves_no_debris() -> None:
    """Migration 155 runs in ONE transaction, so a failed flip rolls back whole
    — there is no orphan non-current row for the service to compensate for,
    which is why append_version has no rollback logic any more."""
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    db.rpc_error = _PostgrestError("server disconnected", code="")

    with pytest.raises(LunaHTTPException) as exc:
        svc.append_version(db, v1["root_id"], content_md=REWRITE_MD)
    assert exc.value.status_code == 500
    assert len(db.tables["public_blogs"]) == 1
    assert db.tables["public_blogs"][0]["is_current"] is True


def test_a_constraint_failure_inside_the_rpc_rolls_the_demote_back() -> None:
    """A stray row already owns version 2, so the INSERT trips
    idx_public_blogs_version — and the demote that preceded it in the same
    transaction must not survive."""
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    db.tables["public_blogs"].append(
        db._with_defaults(
            "public_blogs",
            {
                "blog_id": "stray",
                "root_id": v1["root_id"],
                "version_no": 2,
                "is_current": False,
                "slug": ARABIC_SLUG,
                "title": "عنوان",
                "type": "compliance",
                "question_text": "سؤال",
                "content_md": "نص",
                "author_user_id": "editorial-bot",
            },
        )
    )

    with pytest.raises(LunaHTTPException) as exc:
        svc.append_version(db, v1["root_id"], content_md=REWRITE_MD)
    assert exc.value.status_code == 409
    live = [r for r in db.tables["public_blogs"] if r["is_current"]]
    assert [r["blog_id"] for r in live] == [v1["blog_id"]]


# ---------------------------------------------------------------------------
# set_public — retraction (D11)
# ---------------------------------------------------------------------------


def test_set_public_flips_only_the_current_version() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.append_version(db, v1["root_id"], content_md=REWRITE_MD)

    svc.set_public(db, v1["root_id"], False)

    rows = {r["version_no"]: r for r in db.tables["public_blogs"]}
    assert rows[2]["is_public"] is False
    assert rows[1]["is_public"] is True     # the retired version is untouched
    # Delist ONLY — never a delete, never an unpublish.
    assert rows[2]["deleted_at"] is None
    assert rows[2]["is_published"] is True


def test_set_public_on_an_unknown_root_is_404() -> None:
    db = FakeDB()
    with pytest.raises(LunaHTTPException) as exc:
        svc.set_public(db, "no-such-root", False)
    assert exc.value.status_code == 404


# ---------------------------------------------------------------------------
# Subjects — attach / detach / vocabulary
# ---------------------------------------------------------------------------


def test_attach_subjects_is_idempotent() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)

    assert svc.attach_subjects(db, v1["root_id"], ["work-law", "saudization"]) == [
        "work-law",
        "saudization",
    ]
    svc.attach_subjects(db, v1["root_id"], ["work-law"])
    assert len(db.tables["public_blog_subjects"]) == 2


def test_unknown_subject_slug_is_a_400_not_a_silent_drop() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    with pytest.raises(LunaHTTPException) as exc:
        svc.attach_subjects(db, v1["root_id"], ["work-law", "tax-law"])
    assert exc.value.status_code == 400
    assert "tax-law" in exc.value.detail
    assert db.tables["public_blog_subjects"] == []


def test_set_subjects_replaces_and_validates_before_deleting() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.attach_subjects(db, v1["root_id"], ["work-law", "saudization"])

    with pytest.raises(LunaHTTPException):
        svc.set_subjects(db, v1["root_id"], ["nope"])
    assert len(db.tables["public_blog_subjects"]) == 2  # nothing unfiled

    svc.set_subjects(db, v1["root_id"], ["promissory-note"])
    kept = {r["subject_id"] for r in db.tables["public_blog_subjects"]}
    assert kept == {"s-note"}


def test_detach_subject() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.attach_subjects(db, v1["root_id"], ["work-law", "saudization"])
    svc.detach_subject(db, v1["root_id"], "work-law")
    assert {r["subject_id"] for r in db.tables["public_blog_subjects"]} == {"s-saud"}


def test_subject_vocabulary_counts_only_gallery_visible_blogs() -> None:
    db = FakeDB()
    db.seed_subjects()
    live = _insert_v1(db)
    retracted = _insert_v1(db, slug="مقال-مسحوب", is_public=False)
    svc.attach_subjects(db, live["root_id"], ["work-law"])
    svc.attach_subjects(db, retracted["root_id"], ["work-law", "saudization"])

    subjects = {s["slug"]: s for s in svc.list_subjects(db)}
    assert subjects["work-law"]["blog_count"] == 1
    assert subjects["saudization"]["blog_count"] == 0
    assert subjects["promissory-note"]["blog_count"] == 0
    # Ordered by sort_rank — the operator's manual hint.
    assert [s["slug"] for s in svc.list_subjects(db)] == [
        "work-law",
        "promissory-note",
        "saudization",
    ]
    assert subjects["work-law"]["label_ar"] == "نظام العمل"


def test_inactive_subject_is_invisible() -> None:
    db = FakeDB()
    db.seed_subjects()
    db.tables["blog_subjects"][0]["is_active"] = False
    assert "work-law" not in {s["slug"] for s in svc.list_subjects(db)}
    assert svc.get_subject_by_slug(db, "work-law") is None


# ---------------------------------------------------------------------------
# Read surface
# ---------------------------------------------------------------------------


def test_gallery_lists_only_current_public_published_undeleted() -> None:
    db = FakeDB()
    db.seed_subjects()
    live = _insert_v1(db)
    svc.attach_subjects(db, live["root_id"], ["work-law"])
    _insert_v1(db, slug="مقال-مسحوب", is_public=False)
    _insert_v1(db, slug="مسودة-غير-منشورة", is_published=False)
    db.seed_blog(blog_id="deleted", slug="مقال-محذوف", deleted_at="2026-09-01T00:00:00+00:00")

    cards = svc.list_gallery(db)
    assert [c["slug"] for c in cards] == [ARABIC_SLUG]
    card = cards[0]
    assert card["type"] == "laws_explanation"
    assert card["subjects"] == [{"slug": "work-law", "label_ar": "نظام العمل"}]
    assert card["snippet"]  # the body never ships on a card
    assert "content_md" not in card


def test_gallery_serves_the_current_version_only() -> None:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.append_version(db, v1["root_id"], content_md=SECOND_VERSION_MD)
    cards = svc.list_gallery(db)
    assert len(cards) == 1
    assert cards[0]["title"] == "النسخة الثانية"


def test_get_by_slug_serves_a_retracted_blog() -> None:
    """⚠ Retraction delists; the direct link keeps working (plan §5/§7). The
    returned is_public=false is what drives robots: noindex."""
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.attach_subjects(db, v1["root_id"], ["work-law"])
    svc.set_public(db, v1["root_id"], False)

    assert svc.list_gallery(db) == []          # gone from the gallery
    blog = svc.get_by_slug(db, ARABIC_SLUG)    # still readable by link
    assert blog is not None
    assert blog["is_public"] is False
    assert blog["title"] == "عنوان المقال"
    assert blog["content_md"] == "نص المقال."
    assert blog["question_text"] == "سؤال مجهول الهوية"
    # Both reader-facing keys are backfilled on read (``normalize_frozen_references``):
    # this entry carries no ``ref_id``, so the reveal endpoint could not resolve a
    # body for it (no «عرض المصدر») and it names no library page (no «افتح في ريحان»).
    assert blog["references"] == [
        {"n": 2, "title": "مرجع", "has_source": False, "library_url": None}
    ]
    assert blog["subjects"] == [{"slug": "work-law", "label_ar": "نظام العمل"}]


def test_get_by_slug_refuses_an_unpublished_draft() -> None:
    db = FakeDB()
    db.seed_subjects()
    _insert_v1(db, slug="مسودة", is_published=False)
    assert svc.get_by_slug(db, "مسودة") is None


def test_get_by_slug_bumps_the_view_counter() -> None:
    db = FakeDB()
    db.seed_subjects()
    _insert_v1(db)
    svc.get_by_slug(db, ARABIC_SLUG)
    assert db.tables["public_blogs"][0]["view_count"] == 1


def test_get_by_slug_strips_a_stored_source_view() -> None:
    """A frozen source body on an anon page would mint an unmetered mirror of
    corpus text — the reveal affordance survives, the body does not."""
    db = FakeDB()
    db.seed_subjects()
    _insert_v1(
        db,
        references_json=[{"n": 1, "title": "مرجع", "source_view": {"body": "نص كامل"}}],
    )
    blog = svc.get_by_slug(db, ARABIC_SLUG)
    assert blog["references"][0]["source_view"] is None
    assert blog["references"][0]["has_source"] is True


def test_subject_feed_lists_that_subjects_blogs_newest_first() -> None:
    db = FakeDB()
    db.seed_subjects()
    old = _insert_v1(db, slug="مقال-قديم")
    db.tables["public_blogs"][-1]["created_at"] = "2026-01-01T00:00:00+00:00"
    new = _insert_v1(db, slug="مقال-جديد")
    db.tables["public_blogs"][-1]["created_at"] = "2026-08-01T00:00:00+00:00"
    other = _insert_v1(db, slug="مقال-آخر")

    svc.attach_subjects(db, old["root_id"], ["work-law"])
    svc.attach_subjects(db, new["root_id"], ["work-law"])
    svc.attach_subjects(db, other["root_id"], ["saudization"])

    subject = svc.get_subject_by_slug(db, "work-law")
    total, cards = svc.list_blogs_for_subject(db, subject["subject_id"])
    assert total == 2
    assert [c["slug"] for c in cards] == ["مقال-جديد", "مقال-قديم"]


def test_subject_feed_is_empty_for_an_unfiled_subject() -> None:
    db = FakeDB()
    db.seed_subjects()
    _insert_v1(db)
    subject = svc.get_subject_by_slug(db, "promissory-note")
    assert svc.list_blogs_for_subject(db, subject["subject_id"]) == (0, [])


# ---------------------------------------------------------------------------
# HTTP surface — anonymous, and every key survives `response_model`
# ---------------------------------------------------------------------------


def _client(db: FakeDB):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.app.api import public_blogs as api
    from backend.app.deps import get_supabase
    from backend.app.errors import luna_exception_handler

    app = FastAPI()
    app.add_exception_handler(LunaHTTPException, luna_exception_handler)
    app.include_router(api.router, prefix="/api/v1")
    app.dependency_overrides[get_supabase] = lambda: db
    return TestClient(app)


def _seeded() -> FakeDB:
    db = FakeDB()
    db.seed_subjects()
    v1 = _insert_v1(db)
    svc.attach_subjects(db, v1["root_id"], ["work-law"])
    return db


def test_http_gallery_is_anonymous() -> None:
    r = _client(_seeded()).get("/api/v1/public/blogs")
    assert r.status_code == 200
    body = r.json()
    assert [b["slug"] for b in body["blogs"]] == [ARABIC_SLUG]
    assert body["blogs"][0]["type"] == "laws_explanation"
    assert body["blogs"][0]["subjects"] == [
        {"slug": "work-law", "label_ar": "نظام العمل"}
    ]


def test_http_subject_vocabulary() -> None:
    r = _client(_seeded()).get("/api/v1/public/blogs/subjects")
    assert r.status_code == 200
    subjects = {s["slug"]: s for s in r.json()["subjects"]}
    assert subjects["work-law"]["blog_count"] == 1
    assert subjects["work-law"]["label_ar"] == "نظام العمل"


def test_http_subject_feed_and_unknown_subject_404() -> None:
    client = _client(_seeded())
    r = client.get("/api/v1/public/blogs/subjects/work-law")
    assert r.status_code == 200
    assert r.json()["subject"]["blog_count"] == 1
    assert len(r.json()["blogs"]) == 1

    missing = client.get("/api/v1/public/blogs/subjects/tax-law")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "الموضوع غير موجود"


def test_http_detail_declares_is_public_and_survives_the_response_model() -> None:
    db = _seeded()
    r = _client(db).get(f"/api/v1/public/blogs/{ARABIC_SLUG}")
    assert r.status_code == 200
    body = r.json()
    assert body["is_public"] is True
    # The backfilled keys must survive ``response_model`` too — ``references``
    # is typed ``list[dict]`` precisely so they pass through.
    assert body["references"] == [
        {"n": 2, "title": "مرجع", "has_source": False, "library_url": None}
    ]
    assert body["question_text"] == "سؤال مجهول الهوية"
    assert body["content_md"] == "نص المقال."
    assert body["updated_at"]


def test_http_detail_serves_a_retracted_blog_with_is_public_false() -> None:
    db = _seeded()
    root = db.tables["public_blogs"][0]["root_id"]
    svc.set_public(db, root, False)

    client = _client(db)
    assert client.get("/api/v1/public/blogs").json()["blogs"] == []
    r = client.get(f"/api/v1/public/blogs/{ARABIC_SLUG}")
    assert r.status_code == 200
    assert r.json()["is_public"] is False


def test_http_detail_unknown_slug_404_in_arabic() -> None:
    r = _client(_seeded()).get("/api/v1/public/blogs/لا-يوجد")
    assert r.status_code == 404
    assert r.json()["detail"] == "المدونة غير موجودة"


def test_http_subjects_segment_is_not_read_as_a_blog_slug() -> None:
    """`/public/blogs/subjects` must hit the vocabulary, never `{slug}`."""
    db = _seeded()
    db.tables["public_blogs"][0]["slug"] = "subjects"  # even if a row claimed it
    r = _client(db).get("/api/v1/public/blogs/subjects")
    assert r.status_code == 200
    assert "subjects" in r.json()
