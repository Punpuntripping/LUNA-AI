"""Read and write ``attachment_citation_scans`` / ``attachment_citations`` (migration 231).

Plan: ``.claude/plans/completion_search.md`` §5. Rows hang on the FILE
(``user_files.file_id``), like the segment maps: every function here takes the
conversation's item id and turns it into the file key the same way the segmenter's
store does, so an attachment's citations follow its file into every conversation it
is attached to.

Also the library reads the decision layer needs (``load_facts``) and the one read
delivery needs (``load_pins``): the pinned citations of some items with the official
text of their articles, read at delivery time so it is always the library's current
wording.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence

from agents.memory.attachment_segmenter.store import _file_key, _file_keys
from agents.memory.citation_pins.resolve import (
    DocFacts,
    RegFact,
    all_number_keys,
    candidate_regulations,
)
from agents.tool_repository.citation_scan import ScanResult, TitleIndex

logger = logging.getLogger(__name__)

SCANS_TABLE = "attachment_citation_scans"
CITATIONS_TABLE = "attachment_citations"
_REGS_TABLE = "regulations_v2"
_ARTICLES_TABLE = "articles_v2"
_INSERT_BATCH = 200
_ID_BATCH = 100


class StoreError(RuntimeError):
    """The scan could not be written."""


def _data(resp: Any) -> list[dict]:
    return list(getattr(resp, "data", None) or [])


def _chunks(values: Sequence[str], size: int = _ID_BATCH) -> Iterable[list[str]]:
    for i in range(0, len(values), size):
        yield list(values[i:i + size])


# --------------------------------------------------------------------------- #
# The scan row
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ScanState:
    content_chars: int
    scan_rule_version: str
    pinned: int


def scan_state(supabase: Any, item_id: str) -> ScanState | None:
    """The stored scan of the item's file, or ``None``. Raises on a failed read —
    "could not read" must not be taken for "never scanned"."""
    key = _file_key(supabase, item_id, strict=True)
    rows = _data(
        supabase.table(SCANS_TABLE).select("content_chars,scan_rule_version,pinned")
        .eq("item_id", key).limit(1).execute()
    )
    if not rows:
        return None
    return ScanState(int(rows[0].get("content_chars") or -1), str(rows[0].get("scan_rule_version") or ""),
                     int(rows[0].get("pinned") or 0))


def write_scan(supabase: Any, scan_row: Mapping[str, Any], citation_rows: Sequence[Mapping[str, Any]]) -> str:
    """Replace the scan and citations of the item's file. Returns the file key.

    The scan row is deleted first (its citations go with it through the cascade), then
    both are written again. Raises :class:`StoreError`.
    """
    try:
        key = _file_key(supabase, str(scan_row["item_id"]), strict=True)
    except Exception as exc:  # noqa: BLE001
        raise StoreError(f"file lookup: {type(exc).__name__}") from exc
    try:
        supabase.table(SCANS_TABLE).delete().eq("item_id", key).execute()
        supabase.table(SCANS_TABLE).insert({**scan_row, "item_id": key}).execute()
    except Exception as exc:  # noqa: BLE001
        raise StoreError(f"scan row: {type(exc).__name__}") from exc
    rows = [{**r, "item_id": key} for r in citation_rows]
    try:
        for i in range(0, len(rows), _INSERT_BATCH):
            supabase.table(CITATIONS_TABLE).insert(rows[i:i + _INSERT_BATCH]).execute()
    except Exception as exc:  # noqa: BLE001
        try:
            supabase.table(SCANS_TABLE).delete().eq("item_id", key).execute()
        except Exception:  # noqa: BLE001
            logger.warning("citation_pins: could not remove a half-written scan %s", key)
        raise StoreError(f"citation rows: {type(exc).__name__}") from exc
    return key


# --------------------------------------------------------------------------- #
# Library facts for the decision layer
# --------------------------------------------------------------------------- #


def load_facts(supabase: Any, scan: ScanResult, titles: TitleIndex) -> DocFacts:
    """What the library holds for the laws ONE document names: status, whether the law
    has article text, the ids of the articles it cites, and executive regulations."""
    reg_ids = sorted(candidate_regulations(scan, titles))
    if not reg_ids:
        return DocFacts(titles=titles)
    regs: dict[str, RegFact] = {}
    for chunk in _chunks(reg_ids):
        for r in _data(supabase.table(_REGS_TABLE).select("id,title,clean_title,status_class").in_("id", chunk).execute()):
            regs[str(r["id"])] = RegFact(
                str(r["id"]), (r.get("clean_title") or r.get("title") or "").strip(), r.get("status_class") or "",
            )

    executive_of: dict[str, list[str]] = {}
    wants_executive = any(m.exec_reg for m in scan.citing) or any(law.exec_of for law in scan.named_laws)
    if wants_executive:
        for chunk in _chunks(reg_ids):
            rows = _data(
                supabase.table(_REGS_TABLE).select("id,title,clean_title,status_class,parent_law_id")
                .in_("parent_law_id", chunk).eq("doc_type_bucket", "executive_regulation").execute()
            )
            for r in rows:
                rid = str(r["id"])
                regs[rid] = RegFact(rid, (r.get("clean_title") or r.get("title") or "").strip(),
                                    r.get("status_class") or "")
                executive_of.setdefault(str(r["parent_law_id"]), []).append(rid)

    all_regs = sorted(regs)
    keys = all_number_keys([n for m in scan.citing for n in m.numbers])
    articles: dict[tuple[str, str], str] = {}
    if keys:
        for reg_chunk in _chunks(all_regs, 40):
            for key_chunk in _chunks(keys, 80):
                rows = _data(
                    supabase.table(_ARTICLES_TABLE).select("id,regulation_id,article_number")
                    .in_("regulation_id", reg_chunk).in_("article_number", key_chunk).execute()
                )
                for r in rows:
                    articles.setdefault((str(r["regulation_id"]), str(r["article_number"]).strip()), str(r["id"]))

    # A law none of whose cited numbers was found may simply have no article text at all.
    with_hits = {reg for reg, _ in articles}
    for rid in all_regs:
        if rid in with_hits:
            continue
        probe = _data(supabase.table(_ARTICLES_TABLE).select("id").eq("regulation_id", rid).limit(1).execute())
        if not probe:
            fact = regs[rid]
            regs[rid] = RegFact(fact.id, fact.title, fact.status_class, has_articles=False)
    return DocFacts(titles=titles, regs=regs, articles=articles, executive_of=executive_of)


def reg_status(supabase: Any, reg_ids: Iterable[str]) -> dict[str, str]:
    """``{regulation id: status_class}`` for the laws the agent fetched from. Raises on
    a failed read — "could not read" must not be taken for "not in force"."""
    out: dict[str, str] = {}
    for chunk in _chunks(sorted({str(i) for i in reg_ids if i})):
        for r in _data(supabase.table(_REGS_TABLE).select("id,status_class").in_("id", chunk).execute()):
            out[str(r["id"])] = r.get("status_class") or ""
    return out


# --------------------------------------------------------------------------- #
# What delivery and the card read
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PinnedArticle:
    """One official article tied to one place in an attachment."""

    item_id: str             # the conversation's item the caller asked about
    char_start: int
    char_end: int
    sentence_start: int
    sentence_end: int
    article_id: str
    regulation_id: str
    article_number: str
    regulation_title: str
    text: str                # the official wording, read now

    @property
    def title(self) -> str:
        return f"المادة {self.article_number} من {self.regulation_title}"


def load_pins(supabase: Any, item_ids: Iterable[str], *, user_id: str | None = None) -> dict[str, list[PinnedArticle]]:
    """``{item_id: pinned articles in document order}`` for the given items.

    Never raises: on any failed read an item simply has no pins (its sections are then
    delivered without pinned text — the helper's fallback).
    """
    ids = list(dict.fromkeys(str(i) for i in item_ids if i))
    if not ids:
        return {}
    try:
        from agents.utils.citation_pin_flags import citation_agent_enabled

        # The agent's switch off = exactly the code-only behaviour: what it pinned is not delivered either.
        code_only = not citation_agent_enabled()
        keys = _file_keys(supabase, ids, user_id=user_id)
        by_file: dict[str, list[dict]] = {}
        for chunk in _chunks(sorted(set(keys.values()))):
            query = (
                supabase.table(CITATIONS_TABLE)
                .select("item_id,seq,char_start,char_end,sentence_start,sentence_end,article_number,regulation_id,article_id")
                .in_("item_id", chunk).eq("status", "pinned")
            )
            if code_only:
                query = query.eq("decided_by", "code")
            rows = _data(query.order("item_id").order("seq").execute())
            for r in rows:
                by_file.setdefault(str(r["item_id"]), []).append(r)
        return hydrate_pins(supabase, {item_id: by_file.get(keys.get(item_id, item_id), []) for item_id in ids})
    except Exception as exc:  # noqa: BLE001
        logger.warning("citation_pins: pin read failed (%s) — sections go without pinned text", type(exc).__name__)
        return {}


def hydrate_pins(supabase: Any, rows_by_item: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, list[PinnedArticle]]:
    """Pinned citation rows → ``PinnedArticle`` objects carrying the official text.

    ``rows_by_item`` maps an item id to its pinned rows (stored ones, or rows a
    preview has just computed and not stored). The text is read from the library
    NOW. A row whose article is no longer in the library is dropped. Raises on a
    failed read — ``load_pins`` is the never-raising caller.
    """
    article_ids = sorted({
        str(r["article_id"]) for rows in rows_by_item.values() for r in rows if r.get("article_id")
    })
    texts: dict[str, dict] = {}
    for chunk in _chunks(article_ids):
        for a in _data(supabase.table(_ARTICLES_TABLE).select("id,regulation_id,article_number,content")
                       .in_("id", chunk).execute()):
            texts[str(a["id"])] = a
    reg_ids = sorted({str(a["regulation_id"]) for a in texts.values() if a.get("regulation_id")})
    titles: dict[str, str] = {}
    for chunk in _chunks(reg_ids):
        for r in _data(supabase.table(_REGS_TABLE).select("id,title,clean_title").in_("id", chunk).execute()):
            titles[str(r["id"])] = (r.get("clean_title") or r.get("title") or "").strip()

    out: dict[str, list[PinnedArticle]] = {}
    for item_id, rows in rows_by_item.items():
        pins: list[PinnedArticle] = []
        for r in rows:
            article = texts.get(str(r.get("article_id") or ""))
            content = ((article or {}).get("content") or "").strip()
            if not content:
                continue                      # the article left the library — no longer pinned
            reg_id = str(article.get("regulation_id") or r.get("regulation_id") or "")
            pins.append(PinnedArticle(
                item_id=str(item_id), char_start=int(r["char_start"]), char_end=int(r["char_end"]),
                sentence_start=int(r["sentence_start"]), sentence_end=int(r["sentence_end"]),
                article_id=str(r["article_id"]), regulation_id=reg_id,
                article_number=str(article.get("article_number") or r.get("article_number") or ""),
                regulation_title=titles.get(reg_id, ""), text=content,
            ))
        if pins:
            out[str(item_id)] = pins
    return out


__all__ = [
    "CITATIONS_TABLE",
    "PinnedArticle",
    "SCANS_TABLE",
    "ScanState",
    "StoreError",
    "hydrate_pins",
    "load_facts",
    "load_pins",
    "reg_status",
    "scan_state",
    "write_scan",
]
