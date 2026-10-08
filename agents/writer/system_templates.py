"""System templates (قالب عام) — read-only templates shipped in the repo.

Source of truth: ``agents/writer/templates/<subtype>/<name>.md``. Each file opens
with a small front-matter block::

    ---
    title: نموذج صحيفة دعوى أمام المحكمة الجزائية
    subtype: statement_of_claim
    court: criminal            # optional — any extra scalar key is kept in ``meta``
    sources:                   # optional — our blog posts the template draws on
      - https://rayhanai.com/blog/...
    ---

Only ``title`` is required; ``subtype`` defaults to the parent folder name. The
catalog is generic on purpose — the planner sees each template's front-matter,
nothing here hard-codes a court list or a template name.

IDs are ``uuid5`` of the relative path, so a link stored on an old draft keeps
resolving across deploys as long as the file keeps its path.

Users can HIDE a system template (``user_hidden_templates``, migration 173) —
:func:`visible_system_templates` filters those out. Nothing here writes to the DB.
"""
from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).parent / "templates"

# Fixed namespace — changing it re-keys every system template (breaks old links).
_NAMESPACE = uuid.UUID("6f1d3c7e-3b0e-4f43-9a5e-2f6c1b8e7a10")

_FRONT_MATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n?", re.DOTALL)
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


@dataclass(frozen=True)
class SystemTemplate:
    template_id: str
    slug: str               # relative path without extension, e.g. "memo/default"
    title: str
    subtype: str
    court: str | None
    sources: tuple[str, ...]
    body_md: str            # front-matter stripped; drafting comments kept
    meta: dict[str, Any] = field(default_factory=dict)


def system_template_id(slug: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, slug))


def _parse_front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Minimal YAML subset: ``key: value`` scalars and ``key:`` + ``- item`` lists."""
    m = _FRONT_MATTER_RE.match(text)
    if not m:
        return {}, text
    data: dict[str, Any] = {}
    current_list: str | None = None
    for raw in m.group(1).splitlines():
        line = raw.split(" #", 1)[0].rstrip()
        if not line.strip():
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current_list is not None:
            data[current_list].append(stripped[2:].strip().strip("'\""))
            continue
        key, sep, value = line.partition(":")
        if not sep:
            continue
        key, value = key.strip(), value.strip().strip("'\"")
        if value:
            data[key] = value
            current_list = None
        else:
            data[key] = []
            current_list = key
    return data, text[m.end():]


def _load_file(path: Path) -> SystemTemplate | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        logger.warning("system_templates: cannot read %s", path, exc_info=True)
        return None
    fm, body = _parse_front_matter(text)
    slug = path.relative_to(TEMPLATES_DIR).with_suffix("").as_posix()
    title = str(fm.get("title") or "").strip()
    if not title or not body.strip():
        logger.warning("system_templates: %s has no title/body — skipped", slug)
        return None
    sources = fm.get("sources") or []
    if isinstance(sources, str):
        sources = [sources]
    court = fm.get("court")
    return SystemTemplate(
        template_id=system_template_id(slug),
        slug=slug,
        title=title,
        subtype=str(fm.get("subtype") or path.parent.name),
        court=str(court) if court else None,
        sources=tuple(str(s) for s in sources if s),
        body_md=body.strip(),
        meta={
            k: v for k, v in fm.items()
            if k not in {"title", "subtype", "court", "sources"} and isinstance(v, str)
        },
    )


@lru_cache(maxsize=1)
def load_system_templates() -> tuple[SystemTemplate, ...]:
    """Every system template on disk, sorted by slug. Cached for the process."""
    if not TEMPLATES_DIR.is_dir():
        return ()
    out = [t for p in sorted(TEMPLATES_DIR.rglob("*.md")) if (t := _load_file(p))]
    return tuple(out)


def get_system_template(template_id: str) -> SystemTemplate | None:
    for t in load_system_templates():
        if t.template_id == template_id:
            return t
    return None


def is_system_template_id(template_id: str) -> bool:
    return get_system_template(template_id) is not None


def load_hidden_template_ids(supabase: Any, user_id: str) -> set[str]:
    """System template ids this user hid. Never raises — errors → empty set."""
    try:
        res = (
            supabase.table("user_hidden_templates")
            .select("template_id")
            .eq("user_id", user_id)
            .execute()
        )
    except Exception:
        logger.warning("system_templates: hidden-list load failed user=%s", user_id, exc_info=True)
        return set()
    return {str(r["template_id"]) for r in (getattr(res, "data", None) or []) if r.get("template_id")}


def visible_system_templates(supabase: Any, user_id: str) -> list[SystemTemplate]:
    hidden = load_hidden_template_ids(supabase, user_id)
    return [t for t in load_system_templates() if t.template_id not in hidden]


def strip_drafting_comments(md: str) -> str:
    """Remove ``<!-- … -->`` drafting guidance (templates carry it for the writer)."""
    if "<!--" not in md:
        return md
    cleaned = _HTML_COMMENT_RE.sub("", md)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


__all__ = [
    "SystemTemplate",
    "TEMPLATES_DIR",
    "get_system_template",
    "is_system_template_id",
    "load_hidden_template_ids",
    "load_system_templates",
    "strip_drafting_comments",
    "system_template_id",
    "visible_system_templates",
]
