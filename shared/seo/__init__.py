"""Shared SEO-library derivation helpers (naming, labels).

Kept OUTSIDE ``backend`` and ``scripts`` so both can import the SAME derivation
logic: a title/slug must be derived identically by the publishing script (which
writes the permanent slug into ``seo_item_meta``) and by the read path (which
derives the display title at render time). Any drift between the two would make
a published URL resolve to a page with a different H1.
"""
