"""Tolerant structured-output salvage for reasoning models.

Reasoning-mode models on OpenAI-compatible endpoints (notably
``deepseek-v4-flash`` with ``reasoning_effort=max``) sometimes FINALISE a
structured output as **plain text** instead of calling the output tool: they
emit their chain-of-thought as a ``<thinking>…</thinking>`` block and then dump
the JSON object as the message body. Pydantic AI's default tool-output mode
can't parse that, so it forces a validation retry — which re-sends the entire
(often huge) prompt and roughly doubles cost + latency for that stage.

The fix is additive: add a :class:`pydantic_ai.TextOutput` member to the
agent's ``output_type`` whose coercer salvages the JSON out of that text and
validates it into the target model. The structured tool path stays the
preferred route; this only kicks in when the model emits text.

Observed failure shape (deep_search aggregator, conv ffdf6546)::

    <thinking>
    1. إعادة صياغة السؤال ...
    </thinking>{ "synthesis_md": "...", "used_refs": [...], ... }

So the parser must: drop the thinking block, strip markdown fences, and pull
out the first balanced top-level JSON object (respecting string literals so
braces inside ``synthesis_md`` don't fool the scanner).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Callable, TypeVar

from pydantic import BaseModel, ValidationError
from pydantic_ai import ModelRetry

logger = logging.getLogger(__name__)

M = TypeVar("M", bound=BaseModel)

# Markdown code fences: ```json … ``` or ``` … ```
_FENCE = re.compile(r"(?is)```(?:json)?\s*(.*?)\s*```")
# A thinking block that is a genuine PREFIX before the JSON (anchored at start).
# NB: we must NOT strip ``<thinking>…</thinking>`` that lives *inside* a JSON
# string value (e.g. ``{"synthesis_md": "<thinking>…</thinking>…"}``) — that is
# real content. So thinking is only ever stripped as a leading prefix, and only
# as a fallback after a direct parse attempt.
_THINK_PREFIX = re.compile(r"(?is)^\s*<think(?:ing)?\s*>.*?</think(?:ing)?\s*>")
_THINK_OPEN = re.compile(r"(?is)^\s*<think(?:ing)?\s*>")


def _scan_balanced(s: str) -> str | None:
    """First brace-balanced ``{…}`` from the first ``{``, ignoring braces inside
    JSON string literals. Returns ``None`` if none balances."""
    start = s.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(s)):
        c = s[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[start : i + 1]
    return None


def _candidates(text: str):
    """Yield candidate substrings to try as JSON, in priority order.

    Order matters: parse the text AS-IS first (the brace scanner is string-aware,
    so a ``<thinking>`` block *inside* the JSON survives), then fenced payloads,
    then — only as fallbacks — variants with a leading thinking *prefix* removed.
    """
    s = text.strip()
    yield s                                            # 1. as-is (handles in-string thinking)
    for m in _FENCE.finditer(s):                        # 2. any ```json fenced block
        g = m.group(1).strip()
        if "{" in g:
            yield g
    m = _THINK_PREFIX.match(s)                          # 3. drop a closed leading <thinking>…</thinking>
    if m:
        yield s[m.end():].strip()
    m = _THINK_OPEN.match(s)                            # 4. drop a dangling leading <thinking> (no close)
    if m:
        yield s[m.end():].strip()


def _extract_json_object(text: str) -> str | None:
    """Return the first brace-balanced ``{…}`` in ``text`` that actually parses
    as JSON. Tries the text as-is, then fenced payloads, then with a leading
    thinking prefix stripped. Returns ``None`` when nothing parses — the caller
    then raises ModelRetry, so a genuinely malformed output still retries."""
    if not text:
        return None
    seen: set[str] = set()
    for cand in _candidates(text):
        blob = _scan_balanced(cand)
        if not blob or blob in seen:
            continue
        seen.add(blob)
        try:
            json.loads(blob)
        except json.JSONDecodeError:
            continue
        return blob
    return None


def make_json_salvager(
    model_cls: type[M],
    *,
    retry_msg: str,
) -> Callable[[str], M]:
    """Build a ``TextOutput`` coercer that parses a plain-text JSON emission into
    ``model_cls``.

    Usage::

        from pydantic_ai import TextOutput
        output_type=[MyOutput, TextOutput(make_json_salvager(MyOutput, retry_msg="..."))]

    On any failure (no object found / invalid JSON / schema mismatch) raises
    :class:`ModelRetry` with ``retry_msg`` so Pydantic AI's normal retry loop
    still runs — i.e. this strictly *reduces* retries, never hides a real
    malformed output.
    """

    def _coerce(text: str) -> M:
        blob = _extract_json_object(text)
        if blob is None:
            raise ModelRetry(retry_msg)
        try:
            data = json.loads(blob)
        except json.JSONDecodeError as exc:
            raise ModelRetry(f"{retry_msg} (JSON parse error: {exc})") from exc
        if not isinstance(data, dict):
            raise ModelRetry(retry_msg)
        try:
            out = model_cls.model_validate(data)
        except ValidationError as exc:
            raise ModelRetry(f"{retry_msg} ({exc.error_count()} schema error(s))") from exc
        logger.info("structured_output: salvaged %s from plain-text emission", model_cls.__name__)
        return out

    return _coerce


__all__ = ["make_json_salvager"]
