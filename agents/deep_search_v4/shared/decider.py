"""Decider engine — OpenRouter Decisions API for the deep_search rerankers.

Plan: ``.claude/plans/reranker_decider_engine.md`` (build step 2).

Decision models (``liquid/d1-ultra``, ``typesafe/jev-1.14``) don't generate
text: they take a ``state`` plus typed questions (``score`` / ``noul`` /
``choice``) and return calibrated answers, billed on input tokens only. Both
rerankers send ONE request per sub-query (D8) and call :func:`decide`; the
reranker-specific wording and keep rules live in each executor's
``decider_rules.py``. This module holds no reranker wording.

Fallback (D1/D2): each model in ``models`` gets one try plus one retry (8 s
timeout each), then the next one. A 402 stops the chain (every decider bills
the same OpenRouter account). 400/404/413 are not retried. ``None`` means every
decider failed and the caller runs its qwen path.

Response answers keep the API's shape: ``answers[key]["score"]`` for a
``score`` question, ``answers[key]["noul"]`` for a ``noul`` question,
``answers[key]["probabilities"]`` for a ``choice`` question.

Token guard: a request the model can't hold is skipped without a call (the
fallback reason says so) — see ``_CONTEXT_GUARD`` for the measured ratios.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DECISIONS_URL = "https://openrouter.ai/api/alpha/decisions"
_TIMEOUT_S = 8.0          # per attempt, total (D2)
_ATTEMPTS = 2             # one try + one retry per model (D2)
_RETRY_DELAY_S = 0.5      # pause before the retry (tests set 0)

# Non-200 status → what happens next. Anything not listed (429, 5xx, 408, …)
# is retried once, then the next model.
_STOP_CHAIN = frozenset({401, 402})                 # the key / account every decider shares → qwen
_NO_RETRY = frozenset({400, 403, 404, 413, 422})    # this model refuses this request → next model

# Which answer field must be present per question type (strict: an API shape
# change reads as a failure and falls back, plan "Risks · Alpha API").
_ANSWER_FIELD = {"score": "score", "noul": "noul", "choice": "probabilities"}

# Token guard: model → (chars per token, skip above this many estimated tokens).
# chars = len(state) + len(json.dumps(questions, ensure_ascii=False)) — the
# per-candidate instructions are most of the input. Measured 2026-10-26 against
# the API's ``usage.input_tokens`` on the saved A/B requests, rebuilt exactly as
# the harnesses sent them:
#   d1-ultra — 1,127 requests (reg cases_30 batch 247, case_ab v1+v2 532,
#     case_ab2 v2 348): min 3.62, medians 3.74–3.91; split fit: Arabic state
#     3.52, English questions 3.99. Largest: 13,685 tokens.
#   jev-1.14 — 779 requests (reg 247, case_ab v1+v2 532): min 1.97, medians
#     2.15–2.92; split fit: state 1.60, questions 3.43. Largest: 21,520 tokens.
# Each ratio sits under BOTH halves of its fit, so any state/question mix is
# over-estimated, never under. Models not listed run unguarded.
_CONTEXT_GUARD: dict[str, tuple[float, int]] = {
    "liquid/d1-ultra": (3.3, 28_000),       # context 32,768
    "typesafe/jev-1.14": (1.5, 240_000),    # context 262,144
}

_client: httpx.AsyncClient | None = None
_client_loop: asyncio.AbstractEventLoop | None = None


@dataclass
class DeciderReply:
    """One successful decider answer for one sub-query."""

    model: str                      # configured id, e.g. "liquid/d1-ultra" — picks the thresholds
    model_served: str               # what the API served, e.g. "liquid/d1-ultra-20261015"
    answers: dict[str, dict[str, Any]]
    usage_entry: dict[str, Any]     # see plan step 2 "Usage entry"
    fallback_from: list[str] = field(default_factory=list)  # one short reason per failed model


def parse_models(raw: str | None) -> list[str]:
    """Split a ``*_RERANKER_DECIDERS`` comma list into model ids (order kept)."""
    return [m.strip() for m in (raw or "").split(",") if m.strip()]


def _api_key() -> str | None:
    from shared.config import get_settings

    return get_settings().OPENROUTER_RERANKER_API_KEY or None


def _make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT_S)


def _get_client() -> httpx.AsyncClient:
    """The shared client, created on first use. Re-created when the running loop
    changes (pooled connections can't cross loops: CLI scripts, tests)."""
    global _client, _client_loop
    loop = asyncio.get_running_loop()
    if _client is None or _client.is_closed or _client_loop is not loop:
        _client, _client_loop = _make_client(), loop
    return _client


def _guard(model: str) -> tuple[float, int] | None:
    """The token guard for ``model`` — a dated id (``…-20261015``) uses its alias's."""
    for key, guard in _CONTEXT_GUARD.items():
        if model == key or model.startswith(key + "-"):
            return guard
    return None


def estimate_tokens(state: str, questions: dict[str, dict[str, Any]], model: str) -> int | None:
    """Conservative input-token estimate for ``model``; ``None`` when it has no guard."""
    guard = _guard(model)
    if guard is None:
        return None
    chars = len(state) + len(json.dumps(questions, ensure_ascii=False))
    return math.ceil(chars / guard[0])


def _answered(answer: Any, qtype: Any) -> bool:
    """True when ``answer`` carries a usable value for a question of ``qtype``."""
    if not isinstance(answer, dict):
        return False
    name = _ANSWER_FIELD.get(qtype)
    if name is None:  # a type this module doesn't know: any non-empty answer
        return bool(answer)
    value = answer.get(name)
    if qtype == "choice":
        return isinstance(value, dict) and bool(value)
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


async def _post(body: dict[str, Any], key: str) -> tuple[int | None, dict[str, Any] | None, str, str]:
    """One request → ``(status, payload, reason, detail)``.

    ``status`` is ``None`` for a timeout / transport error; ``payload`` is set
    only for a 200 with a JSON object body. ``reason`` is the short fallback
    text, ``detail`` extra context for the log line.
    """
    try:
        r = await asyncio.wait_for(
            _get_client().post(DECISIONS_URL, json=body, headers={"Authorization": f"Bearer {key}"}),
            _TIMEOUT_S,
        )
    except (asyncio.TimeoutError, httpx.TimeoutException):
        return None, None, "timeout", ""
    except httpx.HTTPError as exc:
        return None, None, type(exc).__name__, str(exc)[:200]
    if r.status_code != 200:
        return r.status_code, None, str(r.status_code), r.text[:200]
    try:
        payload = r.json()
    except ValueError:
        return 200, None, "bad json", r.text[:200]
    if not isinstance(payload, dict):
        return 200, None, "bad json", r.text[:200]
    return 200, payload, "", ""


def _fail(fallback_from: list[str], model: str, reason: str, detail: str = "") -> None:
    fallback_from.append(f"{model}: {reason}")
    logger.warning("decider fallback — %s: %s%s", model, reason, f" ({detail})" if detail else "")


async def decide(
    state: str,
    questions: dict[str, dict[str, Any]],
    *,
    required: list[str],
    models: list[str],
) -> DeciderReply | None:
    """Ask each decider in ``models`` order; ``None`` when all fail (caller runs qwen).

    ``required`` = question keys that must all come back answered; a missing or
    null answer counts as a failure of that model.
    """
    key = _api_key()
    if not key or not models:
        return None  # D6: no dedicated key → no decider, no HTTP call

    chars = len(state) + len(json.dumps(questions, ensure_ascii=False))
    fallback_from: list[str] = []
    for model in models:
        guard = _guard(model)
        if guard is not None and chars / guard[0] > guard[1]:
            _fail(fallback_from, model, f"tokens>{guard[1] // 1000}k", f"~{math.ceil(chars / guard[0])} est")
            continue

        body = {"model": model, "state": state, "questions": questions,
                "provider": {"zdr": True, "data_collection": "deny"}}
        reason = detail = ""
        requests = input_tokens = 0
        for attempt in range(_ATTEMPTS):
            if attempt:
                await asyncio.sleep(_RETRY_DELAY_S)
            requests += 1
            status, payload, reason, detail = await _post(body, key)
            if payload is not None:
                # A 200 is billed even when the answer is unusable, so the retry
                # on the same model adds its tokens to the same usage entry.
                input_tokens += int((payload.get("usage") or {}).get("input_tokens") or 0)
                answers = payload.get("answers")
                if not isinstance(answers, dict):
                    reason = "no answers"
                    continue
                missing = [k for k in required
                           if not _answered(answers.get(k), (questions.get(k) or {}).get("type"))]
                if missing:
                    reason = f"missing {missing[0]}" + (f" +{len(missing) - 1}" if len(missing) > 1 else "")
                    continue
                return DeciderReply(
                    model=model,
                    model_served=str(payload.get("model") or model),
                    answers=answers,
                    usage_entry={
                        "agent": "reranker",
                        "model": model,
                        "reranker_round": 1,
                        "requests": requests,
                        "input_tokens": input_tokens,
                        "output_tokens": 0,
                        "total_tokens": input_tokens,
                        "engine": "decider",
                        "fallback_from": list(fallback_from),
                    },
                    fallback_from=fallback_from,
                )
            if status in _STOP_CHAIN:
                _fail(fallback_from, model, reason, detail)
                logger.warning("decider chain stopped on %s (shared account/key) — caller runs qwen", status)
                return None
            if status in _NO_RETRY:
                break
        _fail(fallback_from, model, reason, detail)

    logger.warning("decider: every model failed (%s) — caller runs qwen", "; ".join(fallback_from))
    return None
