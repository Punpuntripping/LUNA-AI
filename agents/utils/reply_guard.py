"""Nothing about how we work reaches a user (plan artifact_analyzer.md §4.10).

The prompts carry ``NO_INTERNALS_RULE``. This is the check behind them, applied
once in ``handle_message`` so every family and every future reply passes through
it. It sits on the event stream, so what is saved is what was shown.

Two classes on purpose:

- Identifiers are removed. Nothing a user should read looks like ``deep_search``
  or a UUID, so removing one cannot damage a legal sentence.
- Arabic process words are only logged. «الجامع» and «المخطط» are ordinary words
  too, and rewriting legal prose by pattern is a worse failure than the leak.

``WI-N`` is NOT internal and must stay: it is printed on the card button
(``MessageBubble.tsx``), and the reply text may name it.
"""
from __future__ import annotations

import logging
import re
from typing import Any

from shared.observability import get_logfire

logger = logging.getLogger(__name__)
_logfire = get_logfire()

NO_INTERNALS_RULE = (
    "**Never describe how we work.** Speak about what we found, not how we got it. "
    "Never name an internal component or step, and never say what was used, cited, "
    "dropped, ranked or skipped. Write «وجدنا مصدرًا متعلقًا بسؤالك», never «الجامع لم "
    "يستخدم هذا المصدر». The card labels WI-1, WI-2 are visible to the user and are fine."
)

_IDENTIFIER_RE = re.compile(
    r"\(?\b(?:deep_search|simple_search|agent_search|agent_writing|workspace_items?"
    r"|ura_id|ura_json|chunk_id|ref_id|wi_seq|llm_calls|query_restatement"
    r"|planner_brief|planner_decider|artifact_analyzer|item_analyzer|reg_compliance"
    r"|aggregator|reranker)\b\)?",
    re.IGNORECASE,
)
_UUID_RE = re.compile(
    r"\(?\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b\)?",
    re.IGNORECASE,
)
# Bracketed only: a bare S3 can be a form or a plate in a real answer.
_ANALYZER_LABEL_RE = re.compile(r"\[[RS]\d{1,3}\]")

_PROCESS_WORDS = (
    "الجامع",
    "المجمّع",
    "المجمع",
    "معيد الترتيب",
    "إعادة الترتيب",
    "الموجّه",
    "المخطِّط",
    "لم يستخدم هذا المصدر",
    "لم يُستخدم هذا المصدر",
)

_SPACE_BEFORE_MARK_RE = re.compile(r"[ \t]+([.،؛:؟!)])")
_DOUBLE_SPACE_RE = re.compile(r"[ \t]{2,}")
_EMPTY_PARENS_RE = re.compile(r"\(\s*\)")


def _remove(pattern: re.Pattern[str], text: str, hits: list[str]) -> str:
    def drop(match: re.Match[str]) -> str:
        hits.append(match.group(0).strip("()"))
        return ""

    return pattern.sub(drop, text)


def scrub_reply(text: str, hits: list[str] | None = None) -> str:
    """``text`` without identifiers. Unchanged, byte for byte, when there are none."""
    if not text:
        return text
    found: list[str] = []
    cleaned = text
    for pattern in (_IDENTIFIER_RE, _UUID_RE, _ANALYZER_LABEL_RE):
        cleaned = _remove(pattern, cleaned, found)
    if not found:
        return text
    if hits is not None:
        hits.extend(found)
    cleaned = _EMPTY_PARENS_RE.sub("", cleaned)
    cleaned = _SPACE_BEFORE_MARK_RE.sub(r"\1", cleaned)
    cleaned = _DOUBLE_SPACE_RE.sub(" ", cleaned)
    # A removal must not add an edge space; a word stream's own spacing stays.
    if not text[0].isspace():
        cleaned = cleaned.lstrip()
    if not text[-1].isspace():
        cleaned = cleaned.rstrip()
    return cleaned


class ReplyGuard:
    """One per turn. ``check`` sees every event on its way to the client."""

    def __init__(self, conversation_id: str) -> None:
        self._conversation_id = conversation_id
        self._text: list[str] = []
        self._hits: list[str] = []

    def _scrub(self, text: Any) -> Any:
        return scrub_reply(text, self._hits) if isinstance(text, str) else text

    def check(self, event: dict) -> dict:
        """Never raises: a guard must not be able to cost a user their answer."""
        try:
            return self._check(event)
        except Exception:  # noqa: BLE001
            logger.warning("reply_guard failed on an event", exc_info=True)
            return event

    def _check(self, event: dict) -> dict:
        kind = event.get("type")
        if kind == "token":
            text = event.get("text")
            self._text.append(text or "")
            cleaned = self._scrub(text)
            return event if cleaned == text else {**event, "text": cleaned}
        if kind in ("agent_question", "follow_up"):
            question = event.get("question")
            cleaned = self._scrub(question)
            return event if cleaned == question else {**event, "question": cleaned}
        if kind == "next_steps":
            items = [
                {**item, "label": self._scrub(item.get("label")), "prompt": self._scrub(item.get("prompt"))}
                for item in event.get("items") or []
            ]
            return {**event, "items": items}
        if kind == "done":
            self._report()
        return event

    def _report(self) -> None:
        text = "".join(self._text)
        words = [w for w in _PROCESS_WORDS if w in text]
        if self._hits:
            _logfire.warn(
                "reply_guard.removed",
                conversation_id=self._conversation_id,
                terms=sorted(set(self._hits)),
            )
        if words:
            _logfire.info(
                "reply_guard.process_words",
                conversation_id=self._conversation_id,
                words=words,
            )
