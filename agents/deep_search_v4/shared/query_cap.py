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

The wire refuses ``cap < 2`` (a 400 at the router), so ``clamp_queries`` always
has at least two kept slots to work with and the sectioned path's ≥2-channel
rescue below is always reachable.

This module is pure stdlib (``logging`` only) so both executors can import it
without pulling anything new into their import graph.
"""
from __future__ import annotations

import logging
from typing import Callable, Sequence, TypeVar

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
    channel_of: Callable[[T], str] | None = None,
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
    question survives a coin toss.

    ``channel_of`` is the sectioned case path's one exception. That prompt
    requires at least two channels (principle / facts / basis), and a flat
    ``items[:cap]`` can land entirely inside one of them. When it does and a
    different channel exists among the dropped queries, the LAST kept query —
    the least important one by the model's own ordering — is swapped for the
    FIRST dropped query carrying a new channel. The invariant survives at the
    cost of the cheapest query, and the count never changes. Left ``None``
    (both flat paths) the swap never runs.

    ``executor`` only names the caller in the log line.
    """
    kept = list(items[:cap])
    dropped = list(items[cap:])
    if not dropped:
        return (kept, [])

    # ≥2-channel rescue — sectioned case path only. Needs at least two kept
    # slots to be worth anything: at cap == 1 the invariant is unreachable and
    # swapping would only throw away the top-ranked query. The wire's minimum
    # of 2 means that case does not arise in production, but the guard stays —
    # CLI and monitor paths can set a cap by hand.
    if channel_of is not None and len(kept) >= 2:
        kept_channels = {channel_of(q) for q in kept}
        if len(kept_channels) == 1:
            for i, candidate in enumerate(dropped):
                if channel_of(candidate) not in kept_channels:
                    kept[-1], dropped[i] = dropped[i], kept[-1]
                    logger.info(
                        "%s expander clamp: swapped the last kept query for a "
                        "'%s' one — the >=2-channel rule survives the truncation",
                        executor or "expander",
                        channel_of(kept[-1]),
                    )
                    break

    logger.info(
        "%s expander emitted %d > %d — kept %d",
        executor or "expander", len(items), cap, cap,
    )
    return (kept, dropped)


__all__ = [
    "render_cap_block",
    "clamp_queries",
]
