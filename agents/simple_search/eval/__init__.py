"""Eval harnesses for the ``simple_search`` family.

Read-only against production modules: these scripts import the real services and
agents and exercise them against live data. They never modify a production
module. Files are prefixed by the case they cover (``case_b_*`` = the library
carrier, plan §8).

Resolution + routing evaluation (``fixtures_resolution`` / ``run_resolution`` /
``fixtures_routing`` / ``run_routing``) covers two axes, kept apart because they
cost different things:

* **Axis 1 — resolution.** Deterministic. Scores the two identity resolvers the
  searcher actually calls — ``fetch_article.resolve_regulation_id`` (ILIKE +
  difflib, ``_MIN_MATCH_SCORE``) and ``manual_search.manual_search_core`` +
  ``decide`` (BM25 ladder, ``_MIN_TITLE_COVERAGE``) — against a hand-labeled
  fixture set. Zero LLM calls, live Supabase reads only. Re-runnable for free.

* **Axis 2 — routing.** Calls the REAL router LLM, so it costs money (tier_2
  flash). Keep the paraphrase counts small.

Run from the repo root::

    python -m agents.simple_search.eval.run_resolution
    python -m agents.simple_search.eval.run_routing --conversation <uuid>
"""
