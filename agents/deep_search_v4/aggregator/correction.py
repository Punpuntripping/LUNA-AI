"""Per-gate dynamic correction prompts for the aggregator self-correction loop.

When :func:`postvalidator.validate_llm_output` returns ``passed=False``, the
runner asks the same agent (same prompt, same model, same message history)
to patch the *specific* gate violation rather than regenerate the whole
synthesis on a different prompt+model.

The two hard gates that can trigger correction are:
    - ``citation_ok``  — dangling ``[N]`` numbers in synthesis_md
    - ``gap_honesty_ok`` — insufficient sub-queries not surfaced in ``gaps[]``

``arabic_only_ok`` and ``structure_ok`` are SOFT signals only — they ride along
in ``ValidationReport.notes`` but do not trigger correction.

The corrective user message is appended to the prior message history; the
model sees its own prior ``final_result`` and is asked to re-emit it with the
targeted fixes only — structure and content otherwise preserved.

All functions here are pure and side-effect free.
"""
from __future__ import annotations

from .models import (
    AggregatorInput,
    AggregatorLLMOutput,
    Reference,
    ValidationReport,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def build_correction_prompt(
    validation: ValidationReport,
    agg_input: AggregatorInput,
    prev_output: AggregatorLLMOutput,
    references: list[Reference],
) -> str | None:
    """Compose a single Arabic correction message for all failing hard gates.

    Returns ``None`` when no hard gate fired — caller should skip the
    correction turn. Block order is fixed (citation block first, then gap
    block) so failing repro is deterministic.
    """
    blocks: list[str] = []

    if validation.dangling_citations:
        blocks.append(_citation_correction_block(validation, references))

    if not validation.gap_honesty_ok:
        gap_block = _gap_correction_block(agg_input, prev_output)
        if gap_block is not None:
            blocks.append(gap_block)

    if not blocks:
        return None

    intro = (
        "The previous output contained violations in post-synthesis validation. "
        "Re-emit `final_result` with the same structure and content, "
        "correcting only the following aspects — do not rewrite the answer from "
        "scratch. The answer body (`synthesis_md`) stays in Arabic:"
    )
    outro = (
        "\nRespond with a single `final_result` decision containing the corrected "
        "version. Everything else (the sections, the ordering, the style) stays "
        "as it was."
    )

    return intro + "\n\n" + "\n\n".join(blocks) + outro


def failing_gate_names(validation: ValidationReport) -> list[str]:
    """Names of the hard gates that failed — for SSE event payloads.

    Order matches ``build_correction_prompt`` block order.
    """
    failed: list[str] = []
    if validation.dangling_citations:
        failed.append("citation_ok")
    if not validation.gap_honesty_ok:
        failed.append("gap_honesty_ok")
    return failed


# ---------------------------------------------------------------------------
# Per-gate blocks
# ---------------------------------------------------------------------------


def _citation_correction_block(
    validation: ValidationReport,
    references: list[Reference],
) -> str:
    dangling = sorted(validation.dangling_citations)
    valid_ns = sorted(r.n for r in references)
    valid_summary = _format_int_range(valid_ns)
    return (
        "### citation_ok — nonexistent citations\n"
        f"In `synthesis_md` you cited the following numbers, which do not exist in "
        f"`<references>`: {dangling}.\n"
        f"The valid numbers available in `<references>` are: {valid_summary}.\n"
        "Delete these citations from `synthesis_md` or replace them with valid "
        "numbers from the list above. Also update the `used_refs` list so it "
        "contains only numbers that actually exist."
    )


def _gap_correction_block(
    agg_input: AggregatorInput,
    prev_output: AggregatorLLMOutput,
) -> str | None:
    """List the insufficient sub-queries the model didn't already gap-mention.

    Returns ``None`` if the validator's flag was set but we couldn't identify
    a concrete missing sub-query (defensive — shouldn't normally happen).
    """
    sub_queries = agg_input.sub_queries or []
    insufficient = [
        (i, sq) for i, sq in enumerate(sub_queries)
        if not getattr(sq, "sufficient", True)
    ]
    if not insufficient:
        return None

    existing_gaps_blob = " || ".join(prev_output.gaps or [])
    missing: list[str] = []
    for idx, sq in insufficient:
        marker = (getattr(sq, "rationale", "") or getattr(sq, "query", "") or "").strip()
        marker_head = marker[:24]
        if marker_head and marker_head in existing_gaps_blob:
            continue
        domain = getattr(sq, "domain", "") or ""
        label = f"sub_query #{idx + 1}"
        if domain:
            label += f" (domain: {domain})"
        text = (getattr(sq, "query", "") or "").strip()
        text_short = text[:120] + ("…" if len(text) > 120 else "")
        missing.append(f"- {label}: {text_short}")

    if not missing:
        return None

    return (
        "### gap_honesty_ok — incomplete coverage not mentioned in `gaps`\n"
        "The following sub_queries are tagged `insufficient` in the input, but "
        "are not represented in the `gaps` field of your previous output:\n"
        + "\n".join(missing)
        + "\n\nAdd one short item to `gaps` for each of them describing what the "
        "references did not cover — do not repeat the sub_query text verbatim; "
        "describe the gap (in Arabic)."
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _format_int_range(ns: list[int]) -> str:
    """Pretty-print a sorted int list as a contiguous range when possible.

    ``[1, 2, 3]`` → ``"1-3"``; ``[1, 3, 5]`` → ``"1, 3, 5"``.
    """
    if not ns:
        return "(none)"
    if ns == list(range(ns[0], ns[-1] + 1)):
        return f"{ns[0]}-{ns[-1]}" if ns[0] != ns[-1] else str(ns[0])
    return ", ".join(str(n) for n in ns)
