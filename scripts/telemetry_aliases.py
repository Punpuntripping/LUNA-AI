"""Canonical old↔new telemetry-label alias map for the reg_search rename.

Wave 4 renamed the merged retrieval loop ``reg_search`` → ``reg_compliance``
(D16). The change lands at the rename date; historical ``llm_calls`` rows and
Logfire spans written before it keep the OLD labels. Any ops/cost tool that
groups or filters by slot / ledger-stage / span name must treat the old and new
labels as ONE series — this module is the single source of that mapping so the
consumers don't each keep their own copy.

Scope of the rename (folded into ONE series):
    slot        reg_search_expander    ↔ reg_compliance_expander
    slot        reg_search_reranker    ↔ reg_compliance_reranker
    slot        reg_search_aggregator  ↔ reg_compliance_aggregator
    ledger stg  deep_search.expansion.reg ↔ deep_search.expansion.reg_compliance
    ledger stg  deep_search.reranker.reg  ↔ deep_search.reranker.reg_compliance
    span        deep_search.phase.reg     ↔ deep_search.phase.reg_compliance

NOT aliased — historical *compliance executor* labels stay their OWN series
(the standalone compliance executor was a distinct pipeline; its rows are not
reg_compliance rows):
    deep_search.expansion.compliance, deep_search.reranker.compliance,
    deep_search.phase.compliance, compliance_search_expander,
    compliance_search_reranker.

The executor-FAMILY key ``"reg_search"`` (produced_by / agent_family /
retrieval_artifacts persistence) is a deliberate data-contract keep and is NOT
renamed — it is intentionally absent from these maps.
"""
from __future__ import annotations

# --- old → new (canonicalization direction) --------------------------------
SLOT_ALIASES: dict[str, str] = {
    "reg_search_expander": "reg_compliance_expander",
    "reg_search_reranker": "reg_compliance_reranker",
    "reg_search_aggregator": "reg_compliance_aggregator",
}

STAGE_ALIASES: dict[str, str] = {
    "deep_search.expansion.reg": "deep_search.expansion.reg_compliance",
    "deep_search.reranker.reg": "deep_search.reranker.reg_compliance",
}

SPAN_ALIASES: dict[str, str] = {
    "deep_search.phase.reg": "deep_search.phase.reg_compliance",
}

# One merged old → new map over every label kind.
LABEL_ALIASES: dict[str, str] = {**SLOT_ALIASES, **STAGE_ALIASES, **SPAN_ALIASES}

# new → old (reverse), for expanding a query written in the new vocabulary back
# over historical rows.
LABEL_ALIASES_REVERSE: dict[str, str] = {v: k for k, v in LABEL_ALIASES.items()}

# Rename stem — the substring that moved. Used for prefix-style filters.
_STEM_PAIRS: tuple[tuple[str, str], ...] = (("reg_search", "reg_compliance"),)

# The deep_search per-phase Logfire spans, in display order — BOTH the current
# (reg_compliance) and the historical (reg, compliance) labels so a per-turn
# report renders whichever the turn actually emitted. ``case`` is unchanged.
DEEP_SEARCH_PHASE_SPANS: tuple[str, ...] = (
    "deep_search.phase.reg_compliance",  # current
    "deep_search.phase.reg",             # historical (pre-rename)
    "deep_search.phase.compliance",      # historical (retired compliance executor)
    "deep_search.phase.case",
)


def canonical_label(label: str) -> str:
    """Map a historical (pre-rename) label to its current canonical form.

    Leaves everything else — including the historical *compliance* labels, which
    are their own series — unchanged.
    """
    return LABEL_ALIASES.get(label, label)


def label_variants(label: str) -> list[str]:
    """Return ``[label]`` plus its cross-rename twin (old⇄new), if any.

    Both directions, so a caller holding either the old or the new label reads
    the whole series. Historical compliance labels have no twin and return just
    themselves.
    """
    out = [label]
    twin = LABEL_ALIASES.get(label) or LABEL_ALIASES_REVERSE.get(label)
    if twin and twin not in out:
        out.append(twin)
    return out


def agent_prefix_variants(prefix: str) -> list[str]:
    """Expand an ``agent`` LIKE-prefix to also match the other side of the
    reg_search↔reg_compliance rename.

    A prefix filter on the old stem already matches the new one (``reg_search``
    is a prefix of nothing new, but ``...reg`` is a prefix of ``...reg_compliance``);
    the load-bearing case is the reverse — a filter written with the NEW stem
    must still reach pre-rename rows. Prefixes with no known stem return just
    themselves.
    """
    prefix = (prefix or "").strip()
    if not prefix:
        return []
    out = [prefix]
    for old, new in _STEM_PAIRS:
        if old in prefix:
            alt = prefix.replace(old, new)
        elif new in prefix:
            alt = prefix.replace(new, old)
        else:
            continue
        if alt not in out:
            out.append(alt)
    return out


__all__ = [
    "SLOT_ALIASES",
    "STAGE_ALIASES",
    "SPAN_ALIASES",
    "LABEL_ALIASES",
    "LABEL_ALIASES_REVERSE",
    "DEEP_SEARCH_PHASE_SPANS",
    "canonical_label",
    "label_variants",
    "agent_prefix_variants",
]
