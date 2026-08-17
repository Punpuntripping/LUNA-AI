"""``simple_search`` — the lookup family.

A fourth agent family beside ``deep_search`` / ``writing`` / ``memory``, with a
one-sentence premise: **put ONE legal object in front of the user, cheaply.**
Not "research this question across the corpus" — «افتح لي هذا النظام / هذه
المادة / هذا الحكم». Design: ``.claude/plans/simple_search_family.md``.

The family:

* :mod:`~agents.simple_search.models` — the six-level vocabulary, the resolved
  object, the ladder decision, the unfold result. Pure pydantic.
* :mod:`~agents.simple_search.unfold` — ``unfold(always)``: the deterministic,
  LLM-free path from a resolved object to agent-facing Arabic text under the §5
  token budget.
* :mod:`~agents.simple_search.searcher` — Layer 2. Resolves WHICH object the
  user means; owns ``ask_user``; hands off identity, never content.
* :mod:`~agents.simple_search.synthesizer` — Layer 3. Validates the object,
  answers in Arabic, decides whether a card is warranted, cites.
* :mod:`~agents.simple_search.prompts` — six per-level synthesizer prompts +
  the searcher prompt. Edited HERE, not in ``agents/prompts/*.md``.
* :mod:`~agents.simple_search.runner` — the turn: the 3-cycle shared pool, the
  3-document fan-out, case B's searcher bypass. Entry point
  ``run_simple_search`` (plan §12a C1).
* :mod:`~agents.simple_search.publisher` — the ~60-line sibling publisher.
* ``manual_search`` — the fallback retrieval tool (plan §12a C2), owned by its
  own document; the searcher registers it when present.

Nothing is imported eagerly here. ``unfold`` pulls in ``supabase`` and several
``deep_search_v4`` modules, the agents pull in ``pydantic_ai``, and the pure
model layer must stay importable without any of them.
"""
from __future__ import annotations

__all__ = [
    "models",
    "unfold",
    "prompts",
    "searcher",
    "synthesizer",
    "runner",
    "publisher",
]
