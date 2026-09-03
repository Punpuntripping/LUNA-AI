"""«عرض المصدر» on the `public_blogs` wing — the `has_source` flag.

THE BUG THIS PINS (live, 2026-09-03)
------------------------------------
`ReferencePanel` gates the metered reveal on

    canReveal = (!!itemId || !!blogToken) && ref?.has_source === true

On a published `public_blogs` article the token half was fine — the slug is
passed as `sourceKey` — and the flag half was **absent**: all 15 references of
both live articles came back with no `has_source` key at all, so the affordance
never rendered. Clicking `[n]` flashed the card and offered only the outbound
link. Meanwhile the reveal endpoint could resolve every one of those references
(`ref_id='reg:<uuid>'`, `domain='regulations'`) — a body was buildable the whole
time. Nothing was refused; the button was simply missing.

Two holes, two fixes, both pinned here:

* **the write** — `deepsearch_api.service._publish_to_public_blog` dumped
  `Reference` models straight to JSON, and a `Reference` carries no
  `has_source`. It now freezes the flag from `resolvable_ns`, the SAME set
  `fetch_item_references_payload` turns into the key for the in-app panel and
  for the legacy `blog_posts` snapshot. (That half lives in
  `test_editorial_publishing.py`, next to the publish fixture.)
* **the read** — two articles are already live without the key, so
  `public_blog_service.normalize_frozen_references` derives it from the entry's
  own shape. The derivation must never claim more than the reveal endpoint will
  deliver, which is what §4 below actually exercises.

⚠ `has_source` is NOT an entitlement signal. An anonymous reader must still SEE
the button and get the 402 «سجّل مجاناً» card on click (§5). An earlier pass on
the legacy wing set the flag False to "hide" the reveal from anon readers, which
deleted a feature instead of metering it.
"""
from __future__ import annotations

import asyncio

import pytest

from backend.app.services import public_blog_service as svc
from backend.app.services import reference_resolver, references_service
from backend.app.services.blog_service import strip_frozen_source_views

# The row-backed PostgREST fake, seeded with the corpus rows the resolver reads.
# Reused rather than re-faked so this file cannot disagree with the resolver's
# own suite about what "resolvable" means.
from backend.tests.test_reference_source import (
    ART_ID,
    CASE_ID,
    CHUNK_MULTI,
    CIRC_ID,
    REG_ID,
    SVC_ID,
    base_supabase,
)

# The «افتح في ريحان» resolver's own corpus fake — it COUNTS round-trips, which
# is what makes the batching bound below a real assertion. Its ids are its own;
# aliased so the two fakes can never be crossed by accident.
from backend.tests.test_reference_library_links import (
    CASE_ID as LINK_CASE_ID,
    CHUNK_ID as LINK_CHUNK_ID,
    GUIDE_ID as LINK_GUIDE_ID,
    REG_ID as LINK_REG_ID,
    SVC_ID as LINK_SVC_ID,
    corpus,
    guide,
    meta,
)


def run(coro):
    return asyncio.run(coro)


def _body(fn) -> str:
    """A function's source with its DOCSTRING removed.

    Every assertion below is about what the code DOES, and these docstrings
    legitimately name the very things being asserted absent (`_derive_has_source`
    explains why it never consults `resolve_access`). Matching prose would make
    the tests pass or fail on how carefully the behaviour was documented.

    ⚠ Cut by AST line numbers, not by ``src.replace(fn.__doc__, "")`` — Python
    3.13 dedents ``__doc__``, so it is no longer a substring of the source and
    the replace silently does nothing. Same helper, same reason, as
    ``test_blog_snapshot_gating``.
    """
    import ast
    import inspect
    import textwrap

    src = inspect.getsource(fn)
    node = ast.parse(textwrap.dedent(src)).body[0]
    first = node.body[0] if getattr(node, "body", None) else None
    if (
        isinstance(first, ast.Expr)
        and isinstance(first.value, ast.Constant)
        and isinstance(first.value.value, str)
    ):
        lines = src.splitlines()
        del lines[first.lineno - 1 : first.end_lineno]
        return "\n".join(lines)
    return src


def _entry(**over) -> dict:
    """A frozen `references_json` entry, in the shape the live articles carry.

    Note what is NOT here: `item_id`. `Reference` has no such field, so a frozen
    blog entry never carries one — which is why the reveal resolves from
    `ref_id` + `domain` alone, and why the derivation may only use those two.
    """
    base = {
        "n": 1,
        "source_type": "chunk",
        "title": "نظام العمل",
        "snippet": "مقتطف",
        "relevance": "high",
        "ref_id": f"reg:{CHUNK_MULTI}",
        "domain": "regulations",
        "source_view": None,
    }
    base.update(over)
    return base


# ===========================================================================
# 1. The read-time backfill
# ===========================================================================


def test_a_live_shaped_entry_gets_has_source_true() -> None:
    """The exact shape measured on the live payload: `reg:<uuid>` /
    `regulations` / no `has_source`. A body IS buildable for it, so the panel
    must offer «عرض المصدر»."""
    out = svc.normalize_frozen_references([_entry()])
    assert out[0]["has_source"] is True


def test_an_entry_with_no_ref_id_reports_false() -> None:
    """Nothing to resolve ⇒ nothing to reveal. Promising a button here would
    hand the reader an unlock we cannot deliver."""
    out = svc.normalize_frozen_references([_entry(ref_id="")])
    assert out[0]["has_source"] is False


def test_a_frozen_flag_is_never_overridden() -> None:
    """A publish-time flag came from the enrichment itself — strictly better
    information than anything the entry's shape can show. The backfill fills a
    hole; it never second-guesses."""
    live_shape = _entry()
    assert svc.normalize_frozen_references([{**live_shape, "has_source": False}])[0][
        "has_source"
    ] is False
    assert svc.normalize_frozen_references(
        [{**live_shape, "ref_id": "", "has_source": True}]
    )[0]["has_source"] is True


def test_a_present_but_null_flag_is_derived_not_kept() -> None:
    """`null` is not an answer. The client tests `has_source === true`, so a
    stored null reads as "no reveal" — the same missing-button outcome as an
    absent key. A stored `False`, by contrast, IS an answer and survives above."""
    out = svc.normalize_frozen_references([_entry(has_source=None)])
    assert out[0]["has_source"] is True


def test_a_prefixless_ref_id_reports_false() -> None:
    """⚠ CONSERVATIVE ON PURPOSE. `resolve_ref` would fall back to `domain` and
    resolve this — and then CHARGE — but the shell builders re-parse the literal
    `reg:` prefix and would find nothing to build, so the reader would pay for a
    «تعذّر عرض هذا المصدر». Never claim a shape only half the path accepts."""
    out = svc.normalize_frozen_references([_entry(ref_id=CHUNK_MULTI)])
    assert out[0]["has_source"] is False


def test_a_prefix_that_disagrees_with_the_domain_reports_false() -> None:
    """`resolve_ref` dispatches on the PREFIX; `build_reference_source_view`
    dispatches on the DOMAIN. An entry whose two disagree is charged by the
    first and refused by the second."""
    out = svc.normalize_frozen_references(
        [_entry(ref_id=f"case:{CASE_ID}", domain="regulations")]
    )
    assert out[0]["has_source"] is False


def test_a_non_uuid_tail_reports_false() -> None:
    out = svc.normalize_frozen_references([_entry(ref_id="reg:not-a-uuid")])
    assert out[0]["has_source"] is False


def test_a_case_ref_is_not_a_uuid_and_still_reports_true() -> None:
    """`case:<case_ref>` carries the court's own reference string, which
    `_enrich_cases` looks up by — the one family whose tail must NOT be uuid
    shaped. Requiring a uuid here would silently kill every judgment reveal."""
    out = svc.normalize_frozen_references(
        [_entry(ref_id="case:case-ref-1", domain="cases", source_type="case")]
    )
    assert out[0]["has_source"] is True


@pytest.mark.parametrize(
    "ref_id, domain",
    [
        (f"circular:{CIRC_ID}", "circulars"),
        (f"article:{ART_ID}", "articles"),
        (f"regdoc:{REG_ID}", "regulation_docs"),
    ],
)
def test_every_other_citation_family_resolves_by_shape(ref_id, domain) -> None:
    out = svc.normalize_frozen_references([_entry(ref_id=ref_id, domain=domain)])
    assert out[0]["has_source"] is True


def test_compliance_without_a_service_id_reports_false() -> None:
    """⚠ The one family a FROZEN entry can never reveal.
    `_build_compliance_shells` has no ref_id fallback — the `compliance:<sha1>`
    hash is not a service handle — and a frozen `Reference` carries no
    `item_id`, so there is no body to build. No charge is at stake (services are
    `always_free`), but the button would open on an Arabic error."""
    out = svc.normalize_frozen_references(
        [_entry(ref_id="compliance:deadbeefcafebabe", domain="compliance")]
    )
    assert out[0]["has_source"] is False


def test_compliance_WITH_a_service_id_reports_true() -> None:
    """If a future writer ever freezes `item_id`, the body becomes buildable and
    the derivation must follow the endpoint, not a hardcoded family ban."""
    out = svc.normalize_frozen_references(
        [
            _entry(
                ref_id="compliance:deadbeefcafebabe",
                domain="compliance",
                item_id=SVC_ID,
            )
        ]
    )
    assert out[0]["has_source"] is True


def test_an_unknown_domain_reports_false() -> None:
    out = svc.normalize_frozen_references(
        [_entry(ref_id="podcast:1", domain="podcasts")]
    )
    assert out[0]["has_source"] is False


def test_malformed_input_is_safe() -> None:
    assert svc.normalize_frozen_references(None) == []
    assert svc.normalize_frozen_references([]) == []
    assert svc.normalize_frozen_references(["nope", 7, None]) == []


def test_the_citation_mesh_survives_the_backfill() -> None:
    """§1.3 puts citation lists in the NEVER-gated class — the backfill adds one
    key and touches nothing else."""
    out = svc.normalize_frozen_references([_entry()])[0]
    assert {k: v for k, v in out.items() if k != "has_source"} == _entry()


def test_a_stored_source_view_is_still_stripped_and_still_offers_the_reveal() -> None:
    """The strip runs FIRST and its own `has_source=True` wins: an entry that
    carried a body is exactly an entry whose body can be rebuilt."""
    out = svc.normalize_frozen_references(
        [_entry(source_view={"source_type": "chunk", "content": "النص الكامل" * 500})]
    )
    assert out[0]["source_view"] is None
    assert out[0]["has_source"] is True
    assert "النص الكامل" not in str(out)


# ===========================================================================
# 2. Both read paths run it — the article and its reveal cannot drift
# ===========================================================================


def test_both_public_read_paths_normalize() -> None:
    """`get_by_slug` feeds the panel; `get_references_by_slug` feeds the reveal.
    One projection, or the panel starts offering buttons the endpoint refuses."""
    import inspect

    for fn in (svc.get_by_slug, svc.get_references_by_slug):
        assert "normalize_frozen_references" in inspect.getsource(fn), fn.__name__


def test_the_normalizer_still_strips_frozen_source_views() -> None:
    """The gating half must not be lost while adding the flag: a stored
    `source_view` on an anon page is an unmetered mirror of corpus text."""
    import inspect

    assert "strip_frozen_source_views" in inspect.getsource(
        svc.normalize_frozen_references
    )


# ===========================================================================
# 3. The LEGACY wing is untouched  (blog_posts — 99 links in the wild)
# ===========================================================================


def test_legacy_stripper_still_ignores_entries_with_no_source_view() -> None:
    """PIN. `strip_frozen_source_views` passes a `source_view is None` entry
    through IDENTICALLY — same object, no added key. That behaviour is the hole
    this bug fell into, and it is still the right behaviour for `blog_posts`:
    the legacy wing gets its flag at publish time from
    `fetch_item_references_payload`. Teaching the shared stripper to derive
    would change the legacy snapshot's shape for no reason."""
    modern = {"n": 1, "title": "x", "source_view": None, "has_source": True}
    assert strip_frozen_source_views([modern]) == [modern]

    bare = {"n": 2, "title": "y"}
    out = strip_frozen_source_views([bare])
    assert out == [bare]
    assert "has_source" not in out[0]


def test_legacy_stripper_still_synthesises_true_for_a_carried_body() -> None:
    legacy = {"n": 1, "source_view": {"content": "نص"}}
    out = strip_frozen_source_views([legacy])
    assert out[0]["source_view"] is None
    assert out[0]["has_source"] is True


# ===========================================================================
# 4. The derivation AGREES with the reveal endpoint
# ===========================================================================
#
# The endpoint asks two questions, in this order:
#
#   1. `resolve_ref(ref_id, domain, item_id)` — non-None or the reader is
#      refused. This is the leg that runs BEFORE `resolve_access`, so a failure
#      here costs the reader nothing.
#   2. `build_reference_source_view(row)` — dispatches on `domain`, and its
#      per-domain shell builder re-derives the source id from the row. This leg
#      runs AFTER the charge, so a failure here is the expensive one.
#
# A `True` from the derivation must survive BOTH. The id extractors below are
# the pure front half of leg 2 and are exactly what an item_id-less frozen row
# has to get past.

_ID_EXTRACTOR = {
    "regulations": references_service._reg_chunk_id_from_row,
    "cases": references_service._case_ref_from_row,
    "circulars": references_service._circular_id_from_row,
    "articles": references_service._article_id_from_row,
    "regulation_docs": references_service._regdoc_id_from_row,
    # compliance has no extractor at all — `_build_compliance_shells` reads
    # `item_id` directly and drops the row when it is absent.
    "compliance": lambda row: str(row.get("item_id") or ""),
}

_SHAPES = [
    (f"reg:{CHUNK_MULTI}", "regulations", None),
    ("case:case-ref-1", "cases", None),
    (f"circular:{CIRC_ID}", "circulars", None),
    (f"article:{ART_ID}", "articles", None),
    (f"regdoc:{REG_ID}", "regulation_docs", None),
    ("compliance:deadbeefcafebabe", "compliance", None),
    ("compliance:deadbeefcafebabe", "compliance", SVC_ID),
    # Malformed / mismatched shapes.
    ("", "regulations", None),
    (CHUNK_MULTI, "regulations", None),              # prefix-less
    ("reg:not-a-uuid", "regulations", None),
    (f"case:{CASE_ID}", "regulations", None),        # prefix ≠ domain
    (f"reg:{CHUNK_MULTI}", "podcasts", None),        # unknown domain
]


@pytest.mark.parametrize("ref_id, domain, item_id", _SHAPES)
def test_a_true_flag_is_a_promise_the_endpoint_keeps(ref_id, domain, item_id) -> None:
    """🚨 THE INVARIANT. `has_source=True` ⇒ the reveal can resolve AND build.

    A True the endpoint then refuses sells the reader an unlock we cannot
    deliver — and for a chargeable family it sells it after taking the payment.
    """
    entry = _entry(ref_id=ref_id, domain=domain)
    if item_id:
        entry["item_id"] = item_id

    derived = svc.normalize_frozen_references([entry])[0]["has_source"]
    if not derived:
        return  # False is always safe; the next test guards against over-refusal

    supabase = base_supabase()
    row = {
        "n": 1,
        "ref_id": entry.get("ref_id") or "",
        "domain": entry.get("domain") or "",
        "item_id": entry.get("item_id"),
    }

    resolved = run(
        reference_resolver.resolve_ref(
            supabase, row["ref_id"], domain=row["domain"], item_id=row["item_id"]
        )
    )
    assert resolved is not None, f"has_source=True but resolve_ref refuses: {row}"

    extractor = _ID_EXTRACTOR.get(row["domain"])
    assert extractor is not None, f"has_source=True on a domain the reveal cannot build: {row}"
    assert extractor(row), (
        f"has_source=True but the shell builder finds no source id: {row}"
    )


@pytest.mark.parametrize("ref_id, domain, item_id", _SHAPES)
def test_a_false_flag_is_never_merely_pessimistic(ref_id, domain, item_id) -> None:
    """The other direction: a False must be JUSTIFIED — at least one of the two
    legs really would fail. Without this, "return False always" would pass the
    invariant above while deleting the feature all over again."""
    entry = _entry(ref_id=ref_id, domain=domain)
    if item_id:
        entry["item_id"] = item_id

    if svc.normalize_frozen_references([entry])[0]["has_source"]:
        return

    supabase = base_supabase()
    row = {
        "n": 1,
        "ref_id": entry.get("ref_id") or "",
        "domain": entry.get("domain") or "",
        "item_id": entry.get("item_id"),
    }
    resolved = run(
        reference_resolver.resolve_ref(
            supabase, row["ref_id"], domain=row["domain"], item_id=row["item_id"]
        )
    )
    extractor = _ID_EXTRACTOR.get(row["domain"])
    assert resolved is None or extractor is None or not extractor(row), (
        f"has_source=False on a reference the reveal would have served: {row}"
    )


def test_the_uncoverable_axis_is_EXISTENCE_and_it_fails_before_the_charge() -> None:
    """⚠ The one disagreement a shape check cannot close, stated as a test.

    A `reg:<uuid>` whose chunk was re-chunked away still LOOKS resolvable, so the
    derivation says True and `resolve_ref` then returns None. Closing that would
    cost one DB round-trip per citation on an anonymous, uncached page read, and
    it would still be a TOCTOU — the publish-time flag has the identical exposure
    (a source can vanish after the snapshot is frozen).

    It is the safe direction to be wrong in, and this is why: the existence check
    lives in `resolve_ref`, which runs BEFORE `resolve_access`. The reader gets a
    refusal card and is never charged for the missing body."""
    import inspect

    from backend.app.api import blog

    entry = _entry()
    assert svc.normalize_frozen_references([entry])[0]["has_source"] is True

    gone = base_supabase(chunks_v2=[])
    assert run(
        reference_resolver.resolve_ref(
            gone, entry["ref_id"], domain=entry["domain"], item_id=None
        )
    ) is None

    src = _body(blog.reveal_reference_source)
    assert src.index("resolve_ref(") < src.index("resolve_access("), (
        "the unresolvable check moved AFTER the charge — a vanished source would "
        "now cost the reader an unlock for a body that cannot be served"
    )


# ===========================================================================
# 5. The meter is NOT weakened
# ===========================================================================


def test_has_source_is_not_an_entitlement_signal() -> None:
    """It says whether a body EXISTS, never who may read it. The read path takes
    no user at all, so the flag cannot vary by reader — anon and paid see the
    same button, and the 402 «سجّل مجاناً» comes from the reveal endpoint."""
    import inspect

    for fn in (svc.get_by_slug, svc.get_references_by_slug, svc.normalize_frozen_references):
        params = inspect.signature(fn).parameters
        assert not any(
            p in params for p in ("user_id", "current_user", "auth_id")
        ), fn.__name__

    src = _body(svc._derive_has_source)
    for forbidden in ("resolve_access", "user_id", "plan", "quota", "subscription"):
        assert forbidden not in src, forbidden


def test_the_reveal_still_meters_per_reader() -> None:
    """Guard the fix against being 'completed' by opening the source instead."""
    import inspect

    from backend.app.api import blog

    src = _body(blog.reveal_reference_source)
    assert "surface=\"reference\"" in src or "surface='reference'" in src
    assert "resolve_access" in src
    assert "library_refusal_response" in src
    # The 402-not-401 rule: anon reaches this route through OPTIONAL auth.
    sig = inspect.signature(blog.get_public_blog_reference_source)
    assert sig.parameters["current_user"].default.dependency.__name__ == (
        "get_current_user_optional"
    )


# ===========================================================================
# 6. library_url — «افتح في ريحان», the OTHER key the snapshot used to drop
# ===========================================================================
#
# Same root cause as `has_source`, same symptom: the editorial path captured less
# than the legacy path from the same data. Visible on the live page — the two
# compliance citations have an EMPTY `landing_url` AND no `library_url`, so their
# cards offer the reader nothing at all. A citation a reader cannot act on in any
# way is worse than one that costs an unlock.
#
# ⚠ NAVIGATION, NOT A METERED UNLOCK. It is a path to a page that enforces its
# own access tier, so it is resolved for free, for every card, for anonymous
# readers, and it is NEVER charged and NEVER guessed. No published page ⇒ no
# link — a button into a 404 is strictly worse than no button.


def _reg_entry(n: int = 1) -> dict:
    return _entry(n=n, ref_id=f"reg:{LINK_CHUNK_ID}", domain="regulations")


def _normalizer_call_args(fn) -> int:
    """How many positional args a read path passes to the normalizer.

    Parsed, not string-matched, so the assertion survives reformatting.
    """
    import ast
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and getattr(node.func, "id", "") == "normalize_frozen_references"
        ):
            return len(node.args)
    return -1


def test_a_frozen_entry_with_no_library_url_gets_one_resolved() -> None:
    """The backfill for the two rows already published. `reg:<chunk>` collapses
    to its نظام — the same collapse the in-app panel applies."""
    supabase = corpus(seo_item_meta=[meta("regulation", LINK_REG_ID, "nizam-al-amal")])
    out = svc.normalize_frozen_references([_reg_entry()], supabase)
    assert out[0]["library_url"] == "/regulations/nizam-al-amal"


def test_an_unpublished_source_gets_the_key_but_no_link() -> None:
    """No sidecar slug ⇒ no page ⇒ no link. Never a hub fallback, never a guess.

    The KEY is still stamped so the client reads one shape whichever era the row
    was frozen in — `fetch_item_references_payload` stamps `None` the same way."""
    supabase = corpus(seo_item_meta=[])
    out = svc.normalize_frozen_references([_reg_entry()], supabase)
    assert out[0]["library_url"] is None
    assert "library_url" in out[0]


def test_the_two_live_compliance_refs_correctly_get_no_link() -> None:
    """⚠ The measured case, and the honest outcome.

    A frozen `compliance:` entry carries a sha1 DIGEST of `service_ref` and no
    `item_id` — a digest cannot be inverted, so there is no service handle and
    therefore no guide to link to. The resolver has deliberately NO ref_id
    fallback for this wing. Those two references end up with no reveal AND no
    in-app link, which is the truth about them, not a regression."""
    supabase = corpus(
        service_guides=[guide(LINK_SVC_ID)],
        seo_item_meta=[meta("compliance", LINK_GUIDE_ID, "isdar-sak")],
    )
    out = svc.normalize_frozen_references(
        [_entry(ref_id="compliance:366e370492f6afbd", domain="compliance")], supabase
    )
    assert out[0]["library_url"] is None
    assert out[0]["has_source"] is False


def test_a_frozen_null_library_url_is_KEPT_not_re_resolved() -> None:
    """⚠ ASYMMETRIC WITH `has_source`, on purpose.

    A stored `null` here is a REAL answer — `fetch_item_references_payload`
    stamps `None` for every reference with no published page, which is the common
    case. Re-resolving it would spend the sidecar lookup on every read of every
    article forever. (`has_source` has no such convention: it is a bool or it is
    absent, so a null there means "never computed" and IS derived.)"""
    supabase = corpus(seo_item_meta=[meta("regulation", LINK_REG_ID, "nizam-al-amal")])
    out = svc.normalize_frozen_references(
        [{**_reg_entry(), "has_source": True, "library_url": None}], supabase
    )
    assert out[0]["library_url"] is None
    assert supabase.queries == []


def test_a_frozen_library_url_is_never_overridden() -> None:
    supabase = corpus(seo_item_meta=[meta("regulation", LINK_REG_ID, "other-slug")])
    out = svc.normalize_frozen_references(
        [{**_reg_entry(), "has_source": True, "library_url": "/regulations/frozen"}],
        supabase,
    )
    assert out[0]["library_url"] == "/regulations/frozen"
    assert supabase.queries == []


def test_an_article_published_after_the_fix_costs_ZERO_round_trips() -> None:
    """The backfill is for the two legacy rows, not a per-read tax. Every entry
    frozen by the fixed publish path carries the key, so nothing is looked up."""
    supabase = corpus(seo_item_meta=[meta("regulation", LINK_REG_ID, "nizam-al-amal")])
    frozen = [
        {**_reg_entry(n=i), "has_source": True, "library_url": "/regulations/x"}
        for i in range(1, 8)
    ]
    out = svc.normalize_frozen_references(frozen, supabase)
    assert [r["library_url"] for r in out] == ["/regulations/x"] * 7
    assert supabase.queries == []


def test_the_backfill_is_batched_not_per_reference() -> None:
    """Bounded round-trips whatever the citation count — 15 on the live articles.
    A per-reference resolution would put an N-query loop on an anonymous,
    uncached, `force-dynamic` page read."""
    supabase = corpus(seo_item_meta=[meta("regulation", LINK_REG_ID, "nizam-al-amal")])
    out = svc.normalize_frozen_references(
        [_reg_entry(n=i) for i in range(1, 16)], supabase
    )
    assert all(r["library_url"] == "/regulations/nizam-al-amal" for r in out)
    # One chunk→نظام hop, then ONE sidecar call for the whole wing.
    assert supabase.queries == ["chunks_v2", "seo_item_meta"]


def test_the_reveal_read_does_not_pay_for_links_it_never_renders() -> None:
    """`get_references_by_slug` passes no client, so a citation CLICK costs no
    sidecar lookup: the reveal endpoint resolves its own `library_url` after
    unlocking. The keys that decide what the panel OFFERS are still computed
    identically on both paths — that is the part that must not drift."""
    out = svc.normalize_frozen_references([_reg_entry()])
    assert "library_url" not in out[0]
    assert out[0]["has_source"] is True


def test_the_article_read_resolves_links_and_the_reveal_read_does_not() -> None:
    """Pins the cost boundary: which read hands the client over, and which does
    not. Parsed from the AST, so reformatting cannot silently flip it."""
    assert _normalizer_call_args(svc.get_by_slug) == 2
    assert _normalizer_call_args(svc.get_references_by_slug) == 1


def test_a_blocked_sidecar_costs_a_link_never_the_article() -> None:
    """Fail-soft, like every other resolution on this page. The reader loses one
    button; the article still renders."""

    class _Exploding:
        def table(self, _name):
            raise RuntimeError("sidecar down")

    out = svc.normalize_frozen_references([_reg_entry()], _Exploding())
    assert out[0]["library_url"] is None
    assert out[0]["has_source"] is True


def test_the_library_url_derivation_is_the_libraries_own() -> None:
    """One derivation, not two. The link must come from
    `library_items_service`'s resolver — the same one
    `fetch_item_references_payload` reaches through its async wrapper — so the
    blog card and the in-app panel can never disagree about where a citation
    lives, or invent a URL shape between them."""
    src = _body(svc._library_urls_for_entries)
    assert "_public_page_urls_for_reference_rows" in src
    assert "/regulations" not in src, "a URL shape is being built here"
    assert "seo_item_meta" not in src, "a second slug lookup is being built here"


def test_library_url_is_free_navigation_never_a_charge() -> None:
    """It must never touch the meter: no entitlement call, no ledger, no user."""
    import inspect

    src = _body(svc._library_urls_for_entries)
    for forbidden in ("resolve_access", "library_unlocks", "record_use", "user_id"):
        assert forbidden not in src, forbidden
    params = inspect.signature(svc._library_urls_for_entries).parameters
    assert "user_id" not in params and "current_user" not in params
