"""Editorial query cap — the prompt block and the ceiling.

Shared by BOTH executor expanders (``reg_compliance_search`` and
``case_search``), which is the whole point: an editorial job submitted with
``cap: 2`` asks for at most 2 queries from *every* expander it runs, so the
block and the ceiling must be one implementation, not two that drift.

Where the number comes from: the ``cap`` field on a Blog-Post API request,
carried on ``PinnedPlan.cap`` → ``RetrievalConfig.expander_query_cap`` →
``FullLoopDeps`` → each executor's ``LoopState.expander_query_cap``. Every hop
is ``int | None``, default ``None``. Spec: ``.claude/plans/expander_query_cap.md``
and the Blog-Post API protocol §11.1.

⚠ **This is an API-only knob.** ``PinnedPlan`` is constructed in exactly one
place in the codebase — the headless editorial endpoint — so no in-app turn can
mint a cap. With ``cap=None`` nothing here runs: no block is rendered and no
clamp installs, and the expander user message is byte-identical to what it was
before this module existed (a test on each executor asserts it).

Three rules underneath this module, all easy to get wrong:

1. **The block goes in the USER message, never the system prompt.** The system
   prompt is the DashScope prefix-cache key; appending a per-run block to it
   would miss the cache on every expander call in the product, in-app runs
   included, to serve a field that only editorial jobs set.
2. **The block goes LAST.** It overrides the prompt's own "Number of queries"
   guidance for this run, and an instruction that overrides another has to be
   read after it.
3. **The prompt asks, the code enforces, and there is no floor.** The block asks
   for *at most* ``cap`` and tells the model the ordering matters; the clamp
   truncates anything past ``cap`` in emitted order. Coming in under ``cap`` is
   a legitimate outcome and is left alone — inventing filler queries to reach a
   number would spend a real search on a question the expander did not think
   worth asking.

The wire refuses ``cap < 2`` (a 400 at the router). That floor was set for the
sectioned case path's old ≥2-channel rescue, which is gone
(``.claude/plans/case_expander_prompt_4.md`` D8); the floor itself is unchanged.

This module is pure stdlib (``logging`` only) so both executors can import it
without pulling anything new into their import graph.
"""
from __future__ import annotations

import logging
from typing import Sequence, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# The exact block appended to the expander user message. English, like the
# expander prompts themselves. ⚠ Rendered ONLY when a cap is pinned — with
# ``cap=None`` the user message must stay byte-identical to what it was before
# this feature existed (a test on each executor asserts it).
_CAP_BLOCK = """\
---
## Query count for this run — capped by the editor

Produce **at most {cap}** queries in this run. This caps the "Number of queries" guidance
in your instructions for this run only.
Order your queries by importance: if you emit more than {cap}, only the first {cap} are used."""


def render_cap_block(cap: int) -> str:
    """Render the query-cap block for an expander user message.

    ``cap`` is a ceiling, not a target: the block says *at most*, so an expander
    that judges the question settled in one query has answered correctly. There
    is deliberately no "no fewer" here — see rule 3 in the module docstring.

    The returned string carries no leading or trailing blank line; the caller
    joins it onto the message with the separator its own builder already uses.
    """
    return _CAP_BLOCK.format(cap=cap)


def clamp_queries(
    items: Sequence[T],
    cap: int,
    *,
    executor: str = "",
) -> tuple[list[T], list[T]]:
    """Truncate an expander's emitted queries to ``cap``, in emitted order.

    Returns ``(kept, dropped)``. ``dropped`` is empty whenever the expander
    stayed inside the ceiling, which is the ordinary case — the block does the
    work and this is the guarantee behind it.

    **Emitted order is the priority order.** The block tells the model to order
    by importance and warns that only the first ``cap`` are used, so truncating
    from the tail is the model's own ranking being honoured. Any other rule —
    dropping the longest, sampling, re-ranking — makes which angle of the
    question survives a coin toss. That holds on every path, the sectioned
    case path included: its channels follow the decision points, so a head
    that lands in one channel is the model's choice, not a defect to repair.

    ``executor`` only names the caller in the log line.
    """
    kept = list(items[:cap])
    dropped = list(items[cap:])
    if not dropped:
        return (kept, [])

    logger.info(
        "%s expander emitted %d > %d — kept %d",
        executor or "expander", len(items), cap, cap,
    )
    return (kept, dropped)


__all__ = [
    "render_cap_block",
    "clamp_queries",
]
