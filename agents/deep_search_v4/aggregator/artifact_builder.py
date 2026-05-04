"""Artifact builder for the aggregator_v2 agent.

Produces a frontend-ready `Artifact` from the LLM synthesis body plus the
pre-numbered reference list. The artifact's `content` is the full rendered
markdown shown in the right-hand panel of the Luna UI; `references_json`
powers the interactive citation popovers.
"""
from __future__ import annotations

from .models import AggregatorInput, Artifact, Reference

_TITLE_MAX = 80
_REF_LABEL_MAX = 110  # Reference list lines — truncate long section paths


def _shorten_label(label: str, max_chars: int = _REF_LABEL_MAX) -> str:
    """Truncate a reference label at a word boundary; adds ellipsis if cut.

    Section paths often carry multiple pipe-separated parents ("الباب الثاني ... |
    الباب الثالث ..."). Keep the first parent only to preserve readability.
    """
    if "|" in label:
        # Keep text up through the first segment; drop extra parents.
        head, _, _ = label.partition("|")
        label = head.strip()
    if len(label) <= max_chars:
        return label
    cut = label.rfind(" ", 0, max_chars + 1)
    if cut <= 0:
        cut = max_chars
    return label[:cut].rstrip() + "…"


def render_reference_block(references: list[Reference]) -> str:
    """Render the `## المراجع` markdown block.

    Returns an empty string if the list is empty (so callers can safely join
    without stray headings).
    """
    if not references:
        return ""

    lines: list[str] = ["## المراجع", ""]
    # Preserve caller ordering but emit by `n` so the displayed numbering
    # always matches the inline `(n)` citations.
    for ref in sorted(references, key=lambda r: r.n):
        lines.append(f"{ref.n}. {_shorten_label(ref.render_label())}")
    return "\n".join(lines)


def _build_title(original_query: str) -> str:
    """First `_TITLE_MAX` chars of the query, with an ellipsis if truncated."""
    q = (original_query or "").strip()
    if len(q) <= _TITLE_MAX:
        return q
    return q[:_TITLE_MAX] + "…"


def build_artifact(
    agg_input: AggregatorInput,
    synthesis_md: str,
    references: list[Reference],
    confidence: str,
    disclaimer_ar: str,
    prompt_key: str,
    model_used: str,
) -> Artifact:
    """Build the full frontend artifact.

    - title: first 80 chars of `agg_input.original_query` + `…` if truncated
    - content: synthesis_md + reference block + disclaimer (all markdown)
    - references_json: `[ref.model_dump() for ref in references]`
    - metadata: prompt_key, model_used, confidence, ref_count, cited_count
    """
    body = (synthesis_md or "").rstrip()
    ref_block = render_reference_block(references)
    disclaimer = (disclaimer_ar or "").strip()

    parts: list[str] = [body]
    if ref_block:
        parts.append(ref_block)
    if disclaimer:
        parts.append("---\n\n" + disclaimer)
    content = "\n\n".join(parts)

    # cited_count = distinct reference numbers actually referenced inline.
    # We approximate by scanning the synthesis body for `(n)` / `(n,m)` tokens
    # belonging to the supplied reference numbers. The validator produces the
    # authoritative count; this metadata mirror is best-effort.
    ref_numbers = {ref.n for ref in references}
    cited: set[int] = set()
    if body and ref_numbers:
        import re

        for match in re.finditer(r"\(([\d,\s]+)\)", body):
            for tok in match.group(1).split(","):
                tok = tok.strip()
                if tok.isdigit():
                    n = int(tok)
                    if n in ref_numbers:
                        cited.add(n)

    metadata: dict = {
        "prompt_key": prompt_key,
        "model_used": model_used,
        "confidence": confidence,
        "ref_count": len(references),
        "cited_count": len(cited),
    }

    return Artifact(
        kind="legal_synthesis",
        title=_build_title(agg_input.original_query),
        content=content,
        references_json=[ref.model_dump() for ref in references],
        metadata=metadata,
    )
