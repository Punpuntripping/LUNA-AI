"""Legacy blog snapshots must not be an unmetered mirror of the corpus.

`blog_posts.references_json` is a publish-time snapshot served by the ANONYMOUS
`GET /public/blog/{token}`. Publishes from 2026-07-27 capture no source views
(`fetch_item_references` defaults to `with_source_views=False`), but 95 of the
100 posts published before that still carry full case bodies, chunk content and
uncapped circular text — ~3.4 MB readable with a share link, no account, no
meter. That is a standing bypass around the access-tiers design, so the public
read path strips it.

What must SURVIVE is the never-gated class (plan §1.3): the citation list and its
mesh. Only the source BODY is withheld.
"""
from __future__ import annotations

from backend.app.services.blog_service import strip_frozen_source_views


def _legacy_ref() -> dict:
    return {
        "n": 1,
        "title": "نظام العمل",
        "snippet": "مقتطف قصير",
        "ref_id": "reg:11111111-1111-1111-1111-111111111111",
        "domain": "regulations",
        "landing_url": "/regulations/nizam-al-amal",
        "cross_refs": [{"target_reg_title": "نظام", "target_number": 4}],
        "source_view": {
            "source_type": "chunk",
            "title": "نظام العمل",
            "content": "النص الكامل " * 5000,
        },
    }


def test_legacy_source_view_is_stripped_from_the_public_snapshot():
    out = strip_frozen_source_views([_legacy_ref()])
    assert len(out) == 1
    assert out[0]["source_view"] is None
    # The body must not survive anywhere in the payload.
    assert "النص الكامل" not in str(out)


def test_stripping_keeps_has_source_TRUE_so_the_reveal_stays_offered():
    """Strip the BODY, keep the FACT that a body exists.

    A legacy row that carried a `source_view` is exactly a row whose source CAN
    be rebuilt, so the blog panel must still offer «عرض المصدر» — the reader
    signs in and reveals it through the metered endpoint. An earlier pass set
    this False, which silently DELETED the reveal affordance from every
    pre-2026-07-27 post instead of metering it.
    """
    out = strip_frozen_source_views([_legacy_ref()])
    assert out[0]["has_source"] is True


def test_the_citation_mesh_survives_stripping():
    """§1.3 puts citation lists (the mesh) in the NEVER-gated class. Stripping
    the body must not cost the public page its credibility layer."""
    out = strip_frozen_source_views([_legacy_ref()])[0]
    assert out["n"] == 1
    assert out["title"] == "نظام العمل"
    assert out["snippet"] == "مقتطف قصير"
    assert out["ref_id"] == "reg:11111111-1111-1111-1111-111111111111"
    assert out["domain"] == "regulations"
    assert out["landing_url"] == "/regulations/nizam-al-amal"
    assert out["cross_refs"] == [{"target_reg_title": "نظام", "target_number": 4}]


def test_modern_snapshots_pass_through_untouched():
    modern = {"n": 1, "title": "x", "source_view": None, "has_source": True}
    assert strip_frozen_source_views([modern]) == [modern]


def test_empty_and_malformed_inputs_are_safe():
    assert strip_frozen_source_views(None) == []
    assert strip_frozen_source_views([]) == []
    assert strip_frozen_source_views(["not-a-dict", 7, None]) == []


def test_public_read_path_calls_the_stripper():
    """Guard against the projection being rewired around the filter."""
    import inspect

    from backend.app.services import blog_service

    src = inspect.getsource(blog_service.get_public_post)
    assert "strip_frozen_source_views" in src, (
        "get_public_post no longer strips frozen source views — legacy posts are "
        "an anonymous, unmetered mirror of full corpus text again"
    )


def test_blog_import_copy_strips_too():
    """Importing a pre-cutover post must not mint a fresh snapshot carrying the
    old bodies."""
    import inspect

    from backend.app.services import blog_service

    src = inspect.getsource(blog_service)
    copy_block = src[src.find("source_post_id\": root_id") :][:800]
    assert "strip_frozen_source_views" in copy_block, (
        "the blog-import copy path still copies references_json verbatim"
    )


# ===========================================================================
# The blog reveal endpoint — «عرض المصدر» metered, NOT removed
# ===========================================================================
#
# User correction 2026-07-28: «عرض المصدر» and the [n] preview must behave
# exactly as they always did — the click opens the source — the ONLY change is
# that it now counts. An earlier pass hid the affordance on blog pages entirely,
# which deleted a feature rather than metering it.
#
# A blog reader is not the author, so the workspace reveal endpoint's ownership
# check would 404 them. The post's unguessable token is the capability instead.


def test_the_blog_reveal_route_is_registered():
    from backend.app.main import create_app

    paths = {getattr(r, "path", "") for r in create_app().routes}
    assert "/api/v1/public/blog/{token}/references/{n}/source" in paths


def test_the_blog_reveal_uses_OPTIONAL_auth_so_anon_gets_a_402_not_a_401():
    """D14: a 401 on a public page trips the frontend's global
    redirect-to-login. An anonymous reader must get the 402 «سجّل مجاناً» card
    and stay on the post."""
    import inspect

    from backend.app.api import blog

    sig = inspect.signature(blog.get_blog_reference_source)
    dep = sig.parameters["current_user"].default
    assert getattr(dep, "dependency", None) is not None
    assert dep.dependency.__name__ == "get_current_user_optional"


def test_the_blog_reveal_shares_the_library_rate_limit_budget():
    """One 20/min bucket across every reveal surface (D13.2) — otherwise a
    reader alternates surfaces and buys extra budget."""
    import inspect

    from backend.app.api import blog
    from backend.app.middleware.route_limits import library_rate_limit

    sig = inspect.signature(blog.get_blog_reference_source)
    assert sig.parameters["_rl"].default.dependency is library_rate_limit


def test_the_blog_reveal_is_not_cacheable():
    """Per-user entitlement output must never reach a shared cache."""
    import inspect

    from backend.app.api import blog

    src = inspect.getsource(blog.get_blog_reference_source)
    assert "_SOURCE_CACHE_CONTROL" in src
    assert blog._SOURCE_CACHE_CONTROL == "private, no-store"


def test_the_blog_reveal_charges_against_the_READER_not_the_author():
    """The author published once; every reader pays their own way. Guards the
    obvious wrong turn of resolving entitlement from the post's owner_user_id."""
    import inspect

    from backend.app.api import blog

    src = inspect.getsource(blog.get_blog_reference_source)
    assert "current_user.auth_id" in src
    assert "owner_user_id" not in src, (
        "entitlement is being resolved from the POST OWNER — a reader would "
        "inherit the author's unlocks, and the author would be charged for "
        "strangers' reads"
    )
