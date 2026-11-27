"""Text corrections on an uploaded file — an overlay, never a rewrite (D12, D14, D15).

Plan: ``.claude/plans/attachment_agent.md`` §3.3. Table: ``attachment_text_corrections``
(migration 234), keyed by the FILE (``user_files.file_id``), so a fix follows the
file into every conversation it is attached to (D15).

``user_files.content_md`` is never rewritten: its length drives segment-map and
angle-map freshness, so a rewrite would re-segment the file, renumber its sections,
break its threads and make «بحث شامل» refuse it. Instead every reader that opts in
calls :func:`apply_text_corrections` on the whole text, or :func:`apply_to_sections`
on the sections it hands out.

A fix replaces EVERY occurrence of ``wrong`` in the file (D14), oldest fix first.
Before one is stored the caller runs, in this order:

1. :func:`decode_for_store` on ``wrong`` and on ``right`` (the agent read masked
   text; the table holds REAL text — refuse when a fake would remain);
2. :func:`check_text_fix` against the file's text WITH its existing corrections
   applied (what the agent read), and the sections' bounds in that text when the
   file has a map;
3. :func:`save_text_correction` with ``occurrences = check.count``.

Nothing here raises: loads return ``{}``, writes return ``None`` / ``False``, and
every failure is logged with ids and the exception CLASS NAME only — never the text
(PostgREST errors can echo row values).
"""
from __future__ import annotations

import bisect
import dataclasses
import logging
import unicodedata
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

from ._backoff import Backoff

logger = logging.getLogger(__name__)

TABLE = "attachment_text_corrections"

#: After a failed read, skip reads for this long (``_backoff.py``): every file read
#: goes through the loader, so a missing table must not cost a request each time.
RETRY_AFTER_S = 60.0
_backoff = Backoff(RETRY_AFTER_S)

#: Longest ``wrong`` / ``right`` accepted: a fix is a word or a short phrase. The
#: table's CHECK is looser (500) so this can be tuned without a migration.
MAX_FIX_CHARS = 200

#: Ids per ``in.(…)`` filter (the ``shared.files.store`` batch size).
ID_BATCH = 100

#: Letters that attach in front of an Arabic word (و ف ب ل ك س and the article):
#: a fix of «محمد» also fixes «ومحمد» / «لمحمد» / «والمحمد». Any other letter in
#: front means the match sits inside another word.
_PROCLITICS = frozenset({
    "و", "ف", "ب", "ل", "ك", "س",
    "ال", "وال", "فال", "بال", "كال", "لل", "ولل", "فلل",
    "وب", "فب", "ول", "فل", "وك", "فك", "وس", "فس",
})

_COLUMNS = "id, file_id, wrong_text, right_text, occurrences, created_at"

#: Reasons shown to the user by the router (Arabic). They never quote the text:
#: a decoded number in a reason would reach a model unmasked.
REASON_EMPTY_WRONG = "لم يُحدَّد النص الخاطئ المراد تصحيحه."
REASON_EMPTY_RIGHT = "لم يُحدَّد النص الصحيح."
REASON_SAME = "النص الصحيح مطابق للنص الموجود، فلا يوجد ما يُصحَّح."
REASON_NEWLINE = "التصحيح يكون داخل سطر واحد؛ لا يمكن أن يمتد عبر سطرين."
REASON_TOO_LONG = f"التصحيح أطول من {MAX_FIX_CHARS} حرف؛ يُصحَّح الاسم أو الكلمة أو العبارة القصيرة فقط."
REASON_NOT_FOUND = "لم يُعثر على النص المراد تصحيحه في الملف بهذه الصيغة حرفاً بحرف."
REASON_INSIDE_WORD = (
    "النص المراد تصحيحه يظهر داخل كلمات أخرى أطول منه، فتصحيحه في كل المواضع يغيّر تلك الكلمات؛ "
    "اكتب الكلمة كاملة كما وردت في الملف."
)
REASON_CROSSES_JOIN = (
    "النص المراد تصحيحه يقع على حدّ بين قسمين أو صفحتين من الملف؛ "
    "صحّح جزءاً منه لا يعبر هذا الحد."
)


@dataclass(frozen=True)
class TextCorrection:
    """One active fix of a file: every ``wrong`` becomes ``right``."""

    id: str
    file_id: str
    wrong: str
    right: str
    occurrences: int = 0
    created_at: str | None = None


@dataclass(frozen=True)
class FixCheck:
    """What :func:`check_text_fix` decided. ``count``: occurrences in the whole
    text (0 when refused before counting); ``reason``: Arabic, empty when ``ok``."""

    ok: bool
    count: int = 0
    reason: str = ""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _data(resp: Any) -> list[dict]:
    data = getattr(resp, "data", None)
    if isinstance(data, dict):
        return [data]
    return [r for r in (data or []) if isinstance(r, dict)]


def _unique(ids: Iterable[Any]) -> list[str]:
    return list(dict.fromkeys(str(i) for i in ids or [] if i))


def _batches(ids: list[str]) -> Iterable[list[str]]:
    for i in range(0, len(ids), ID_BATCH):
        yield ids[i:i + ID_BATCH]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pair(c: Any) -> tuple[str, str]:
    """``(wrong, right)`` of a :class:`TextCorrection` or a row / dict."""
    if isinstance(c, Mapping):
        wrong = c.get("wrong", c.get("wrong_text"))
        right = c.get("right", c.get("right_text"))
    else:
        wrong, right = getattr(c, "wrong", None), getattr(c, "right", None)
    return str(wrong or ""), str(right if right is not None else "")


def _created(c: Any) -> str:
    v = c.get("created_at") if isinstance(c, Mapping) else getattr(c, "created_at", None)
    return str(v or "")


def _in_order(corrections: Iterable[Any]) -> list[Any]:
    # Stable: equal (or missing) timestamps keep the caller's order.
    return sorted(list(corrections or []), key=_created)


def _is_mark(ch: str) -> bool:
    return unicodedata.category(ch) == "Mn"   # tashkeel and other combining marks


def _inside_word(text: str, start: int, end: int) -> bool:
    """The match ``text[start:end]`` continues a longer word: a letter or digit
    right after it (combining marks skipped), or letters right before it that are
    not a proclitic (و، ب، ل، ال…)."""
    j = end
    while j < len(text) and _is_mark(text[j]):
        j += 1
    if j < len(text) and text[j].isalnum():
        return True
    i = start
    while i > 0 and (text[i - 1].isalnum() or _is_mark(text[i - 1])):
        i -= 1
    prefix = "".join(ch for ch in text[i:start] if not _is_mark(ch))
    return bool(prefix) and prefix not in _PROCLITICS


def _occurrences(text: str, wrong: str) -> list[int]:
    """Start offsets of the non-overlapping matches ``str.replace`` would change."""
    out: list[int] = []
    i = text.find(wrong)
    while i != -1:
        out.append(i)
        i = text.find(wrong, i + len(wrong))
    return out


# ---------------------------------------------------------------------------
# Pure: apply and check
# ---------------------------------------------------------------------------


def apply_text_corrections(text: str, corrections: Iterable[Any] | None) -> str:
    """``text`` with every correction applied: each ``wrong`` → ``right``, every
    occurrence, exact match, oldest correction first (``created_at``). Pure.
    Accepts :class:`TextCorrection` objects or rows / dicts (``wrong`` / ``right``
    or the table's ``wrong_text`` / ``right_text``). No corrections → ``text``."""
    if not text or not corrections:
        return text
    for c in _in_order(corrections):
        wrong, right = _pair(c)
        if wrong and wrong != right:
            text = text.replace(wrong, right)
    return text


_TEXT_KEYS = ("text", "content_md")


def _section_with_text(section: Any, corrections: list[Any]) -> Any:
    if isinstance(section, Mapping):
        out = dict(section)
        for key in _TEXT_KEYS:
            if isinstance(out.get(key), str):
                out[key] = apply_text_corrections(out[key], corrections)
        return out
    if dataclasses.is_dataclass(section) and not isinstance(section, type):
        names = {f.name for f in dataclasses.fields(section)}
        update = {
            k: apply_text_corrections(getattr(section, k), corrections)
            for k in _TEXT_KEYS if k in names and isinstance(getattr(section, k), str)
        }
        return dataclasses.replace(section, **update) if update else section
    fields = getattr(type(section), "model_fields", None)
    if fields is not None and hasattr(section, "model_copy"):
        update = {
            k: apply_text_corrections(getattr(section, k), corrections)
            for k in _TEXT_KEYS if k in fields and isinstance(getattr(section, k, None), str)
        }
        return section.model_copy(update=update) if update else section
    return section


def apply_to_sections(sections: Iterable[Any], corrections: Iterable[Any] | None) -> list[Any]:
    """Each section with :func:`apply_text_corrections` on its text — ``text`` and /
    or ``content_md`` — for loaders that hand out section slices. Works on dicts
    (copied), dataclasses (``Section`` of ``attachment_delivery``, via ``replace``)
    and pydantic models (``StoredSegment``, via ``model_copy``); anything else is
    passed through. Positions (``char_start`` / ``char_end``) are left as they are:
    they point into the ORIGINAL text. Pure; the input is not modified."""
    corrections = list(corrections or [])
    if not corrections:
        return list(sections or [])
    return [_section_with_text(s, corrections) for s in sections or []]


def check_text_fix(
    full_text: str,
    section_bounds: Sequence[tuple[int, int]] | None,
    wrong: str,
    right: str,
) -> FixCheck:
    """May ``wrong`` → ``right`` be stored for this file?

    ``full_text`` — the file's whole text WITH its existing corrections applied
    (what the agent read). ``section_bounds`` — ``(char_start, char_end)`` of each
    section of the file's segment map, as offsets in ``full_text`` (the caller
    maps the stored offsets, which are in the original text), or ``None`` when the
    file has no map. ``wrong`` / ``right`` — already decoded
    (:func:`decode_for_store`).

    Refused (``ok=False``, reason in Arabic, never quoting the text): an empty
    ``wrong`` or ``right``; the two equal; a newline in either; either longer than
    :data:`MAX_FIX_CHARS`; ``wrong`` not in the text; a match that sits inside a
    longer word (fixing every occurrence would change that word too); a match that
    STRADDLES a section boundary (a section start or end falls strictly inside it:
    the section readers, which hand out each section's own text, would miss it).
    A match outside every section — a page letterhead a joined section leaves
    out — is fine: the whole-text readers fix it, the section readers never show it.
    ``count`` = occurrences in ``full_text`` (non-overlapping, as replaced).
    """
    wrong = wrong if isinstance(wrong, str) else ""
    right = right if isinstance(right, str) else ""
    if not wrong.strip():
        return FixCheck(False, 0, REASON_EMPTY_WRONG)
    if not right.strip():
        return FixCheck(False, 0, REASON_EMPTY_RIGHT)
    if wrong == right:
        return FixCheck(False, 0, REASON_SAME)
    if any(ch in s for s in (wrong, right) for ch in ("\n", "\r", "\u2028", "\u2029")):
        return FixCheck(False, 0, REASON_NEWLINE)
    if len(wrong) > MAX_FIX_CHARS or len(right) > MAX_FIX_CHARS:
        return FixCheck(False, 0, REASON_TOO_LONG)
    text = full_text or ""
    starts = _occurrences(text, wrong)
    if not starts:
        return FixCheck(False, 0, REASON_NOT_FOUND)
    count = len(starts)
    if any(_inside_word(text, s, s + len(wrong)) for s in starts):
        return FixCheck(False, count, REASON_INSIDE_WORD)
    if section_bounds:
        edges = sorted({int(b) for bound in section_bounds for b in bound})
        for s in starts:
            e = s + len(wrong)
            k = bisect.bisect_right(edges, s)
            if k < len(edges) and edges[k] < e:
                return FixCheck(False, count, REASON_CROSSES_JOIN)
    return FixCheck(True, count, "")


# ---------------------------------------------------------------------------
# Masking: the table stores REAL text
# ---------------------------------------------------------------------------

_ACTIVE: Any = object()


def decode_for_store(text: str, *, source: str = "", codec: Any = _ACTIVE) -> str | None:
    """``text`` (what the agent wrote, from masked input) with its fakes decoded,
    ready to store — or ``None`` when a fake would remain (refuse the write: the
    table is store-real, and a fake that cannot be decoded later is lost data).

    ``codec`` — default: the turn's active codec
    (``backend.app.services.masking_service.active_codec()``); no codec (outside a
    masked turn, or ``None`` passed) → ``text`` unchanged. ``source`` — the masked
    text the agent read (its input), passed to ``codec.leaves_fake`` so a number it
    read unmasked is not mistaken for a damaged fake. The leftover-fake check runs
    only while masking is on (as ``conversation_memory`` does: with it off no fake
    reached the model); decoding always runs. Decoding = ``decode_text(codec, text,
    emit=False)``. Any error → ``None``."""
    if not text:
        return text
    if codec is _ACTIVE:
        try:
            from backend.app.services.masking_service import active_codec

            codec = active_codec()
        except Exception as exc:  # noqa: BLE001 — no backend (a script): nothing was masked
            logger.debug("attachments: no active codec (%s)", type(exc).__name__)
            codec = None
    if codec is None:
        return text
    try:
        if getattr(codec, "enabled", True) and codec.leaves_fake(text, source or ""):
            return None
        return codec.decode(text).text
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: decode_for_store failed (%s)", type(exc).__name__)
        return None


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def _correction(row: Mapping[str, Any]) -> TextCorrection:
    try:
        occurrences = int(row.get("occurrences") or 0)
    except (TypeError, ValueError):
        occurrences = 0
    return TextCorrection(
        id=str(row.get("id") or ""),
        file_id=str(row.get("file_id") or ""),
        wrong=str(row.get("wrong_text") or ""),
        right=str(row.get("right_text") or ""),
        occurrences=occurrences,
        created_at=str(row.get("created_at") or "") or None,
    )


def load_text_corrections(
    supabase: Any, file_ids: Iterable[Any], *, user_id: Any = None
) -> dict[str, list[TextCorrection]]:
    """``{file_id: [active corrections, oldest first]}`` — files without one are
    left out. One request per :data:`ID_BATCH` files; ``user_id`` also filters by
    owner (defence in depth — the client is service_role). Never raises: a failed
    read returns ``{}`` (readers then show the uncorrected text), logs once and
    skips reads for :data:`RETRY_AFTER_S`."""
    ids = _unique(file_ids)
    if not ids or supabase is None or _backoff.down():
        return {}
    out: dict[str, list[TextCorrection]] = {}
    try:
        for batch in _batches(ids):
            q = (
                supabase.table(TABLE)
                .select(_COLUMNS)
                .in_("file_id", batch)
                .is_("reverted_at", "null")
            )
            if user_id:
                q = q.eq("user_id", str(user_id))
            for row in _data(q.order("created_at").execute()):
                c = _correction(row)
                if c.file_id and c.wrong:
                    out.setdefault(c.file_id, []).append(c)
    except Exception as exc:  # noqa: BLE001
        _backoff.failed()
        logger.warning("attachments: text corrections not read for %d file(s) (%s)", len(ids), type(exc).__name__)
        return {}
    return {fid: _in_order(cs) for fid, cs in out.items()}


def reset_backoff() -> None:
    """Forget an earlier failed read (tests; a manual retry)."""
    _backoff.reset()


def file_keys_for_items(supabase: Any, item_ids: Iterable[Any], *, user_id: Any = None) -> dict[str, str]:
    """``{item_id: file_id}`` — ``shared.files.store.file_ids_for_items``, never
    raising: an item that links no file, or a failed lookup, keeps its own id (the
    file's first item has ``file_id = item_id``; the segment store does the same)."""
    ids = _unique(item_ids)
    if not ids:
        return {}
    try:
        from shared.files import store as files_store

        files = files_store.file_ids_for_items(supabase, ids, user_id=user_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: file lookup failed (%s) — item ids used", type(exc).__name__)
        files = {}
    return {i: str(files.get(i, i)) for i in ids}


def corrections_for_items(
    supabase: Any, item_ids: Iterable[Any], *, user_id: Any = None
) -> dict[str, list[TextCorrection]]:
    """:func:`load_text_corrections` keyed by the CONVERSATION item ids a reader
    holds (each item → its file). Items whose file has none are left out. Never
    raises.

    A READER helper, gated like every overlay reader (``flags.edits_enabled``): the
    attachment agent's flag off for ``user_id``, no ``user_id``, or the read backoff
    down → ``{}`` with no request at all (not even the file lookup)."""
    from .flags import edits_enabled

    if not edits_enabled(user_id) or _backoff.down():
        return {}
    keys = file_keys_for_items(supabase, item_ids, user_id=user_id)
    if not keys:
        return {}
    by_file = load_text_corrections(supabase, keys.values(), user_id=user_id)
    return {item: by_file[fid] for item, fid in keys.items() if fid in by_file}


# ---------------------------------------------------------------------------
# Writes (service-role client)
# ---------------------------------------------------------------------------


def save_text_correction(
    supabase: Any,
    *,
    file_id: str,
    user_id: str,
    wrong: str,
    right: str,
    occurrences: int,
    evidence: str,
    conversation_id: str | None = None,
    message_id: str | None = None,
) -> str | None:
    """Store one fix; its id, or ``None`` (bad input or a failed write — logged).
    ``wrong`` / ``right`` must already be decoded and checked
    (:func:`decode_for_store`, :func:`check_text_fix`); this re-checks only the
    shape the table's CHECKs enforce (non-empty, different, one line). ``evidence``
    — the user's words that asked for it (decoded by the caller). Never raises."""
    wrong = wrong if isinstance(wrong, str) else ""
    right = right if isinstance(right, str) else ""
    if (
        not file_id or not user_id or not wrong.strip() or not right.strip() or wrong == right
        or "\n" in wrong or "\n" in right or "\r" in wrong or "\r" in right
    ):
        logger.warning("attachments: text correction for file %s not storable (shape)", file_id or "-")
        return None
    try:
        n = max(1, int(occurrences))
    except (TypeError, ValueError):
        n = 1
    row = {
        "id": str(uuid.uuid4()),
        "file_id": str(file_id),
        "user_id": str(user_id),
        "wrong_text": wrong,
        "right_text": right,
        "occurrences": n,
        "evidence": str(evidence or ""),
        "conversation_id": str(conversation_id) if conversation_id else None,
        "message_id": str(message_id) if message_id else None,
    }
    try:
        supabase.table(TABLE).insert(row).execute()
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: text correction not stored for file %s (%s)", file_id, type(exc).__name__)
        return None
    return row["id"]


def revert_text_correction(supabase: Any, correction_id: str, user_id: str) -> bool:
    """Mark one of the user's active fixes reverted (the row stays, for the record).
    ``True`` when a row changed; ``False`` when none matched (wrong id, not the
    owner, already reverted) or on a failed write. Never raises."""
    if not correction_id or not user_id:
        return False
    try:
        resp = (
            supabase.table(TABLE)
            .update({"reverted_at": _now()})
            .eq("id", str(correction_id))
            .eq("user_id", str(user_id))
            .is_("reverted_at", "null")
            .execute()
        )
        return bool(_data(resp))
    except Exception as exc:  # noqa: BLE001
        logger.warning("attachments: text correction %s not reverted (%s)", correction_id, type(exc).__name__)
        return False


__all__ = [
    "FixCheck",
    "MAX_FIX_CHARS",
    "TABLE",
    "TextCorrection",
    "apply_text_corrections",
    "apply_to_sections",
    "check_text_fix",
    "corrections_for_items",
    "decode_for_store",
    "file_keys_for_items",
    "load_text_corrections",
    "reset_backoff",
    "revert_text_correction",
    "save_text_correction",
]
