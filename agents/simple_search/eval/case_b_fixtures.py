"""Live fixtures for the Case-B eval — REAL slugs, verified against
``dwgghvxogtwyaxmbgjod`` on 2026-08-16.

Every id here was read live; nothing is invented. If a slug stops resolving the
harness fails loudly rather than silently degrading, which is the point.
"""
from __future__ import annotations

# The eval account (task brief).
USER_ID = "c5f4cff0-0517-43f0-af59-a9905deab22c"
SCRATCH_CONVO_TITLE = "[EVAL-CASE-B]"

# ── regulations ──────────────────────────────────────────────────────────────
# seo_item_meta.slug → regulations_v2.id
REG_LABOR_SLUG = "نظام-العمل"
REG_LABOR_ID = "da51024f-a713-48e7-af87-b6a541f055e4"
REG_LABOR_TITLE = "نظام العمل"          # regulations_v2.title (clean_title IS NULL)

REG_ENFORCE_SLUG = "نظام-التنفيذ"
REG_ENFORCE_ID = "a49cc765-8023-4d21-88dd-27b6e720efe8"
REG_ENFORCE_TITLE = "نظام التنفيذ"

# ── article ──────────────────────────────────────────────────────────────────
# page_id is the composite '{reg_slug}/{article_slug}' the مادة page sends.
ART_PAGE_ID = f"{REG_LABOR_SLUG}/المادة-5"
ART_SEO_ID = "f5c507dc-be22-4259-958a-adc759bb902b"   # seo_articles.id
ART_V2_ID = "528c9dd2-fb71-5afd-ae6c-5dd26d54832a"    # articles_v2.id  ← ref target
ART_NUMBER = "5"
ART_LABEL = "المادة 5"

# ── judgment ─────────────────────────────────────────────────────────────────
JUDGMENT_SLUG = "نزاع-بين-شريكين-في-شركة-ذات-مسؤولية-محدودة-fi-4470682912"
JUDGMENT_CASE_ID = "cea98fd5-b256-4a0e-9675-b550ef920c7e"
JUDGMENT_CASE_REF = "17642_fi_4470682912"

# ── blog ─────────────────────────────────────────────────────────────────────
BLOG_TOKEN = "9687fb4ce6f579329bdaefa420a1ac8f"
BLOG_TITLE = "أثر إصلاح السيارة على دعوى تعويض تأمينية"

# Types that must REFUSE (no fetch_grounding grounder — §8 "Coverage today").
UNSUPPORTED_TYPES = ("circular", "service", "form")

__all__ = [n for n in dir() if n.isupper()]
