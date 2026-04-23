"""Run logger for the aggregator_v2 agent.

Writes per-run outputs under:
    {base_logs_dir}/query_{id}/{log_id}/

Default base is `agents/deep_search_v3/aggregator/logs/` — mirrors the layout
of `reg_search/logs/` but lives under the aggregator package so downstream
tooling, cleanup, and archival stay scoped to this agent.

For replay harnesses, an arbitrary `base_logs_dir` can be passed in — useful
when rerunning synthesis against a captured reranker snapshot outside of the
normal reg_search pipeline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import (
        AggregatorInput,
        AggregatorOutput,
        Reference,
        ValidationReport,
    )

logger = logging.getLogger(__name__)

# Default base: agents/deep_search_v3/aggregator/reports/
DEFAULT_BASE_LOGS_DIR = Path(__file__).resolve().parent / "reports"


def sanitize_variant(text: str) -> str:
    """Make a string safe for filesystem use: allow a-z, 0-9, dot, dash, underscore."""
    import re
    t = re.sub(r"[^A-Za-z0-9._-]+", "-", text.strip())
    return t.strip("-") or "_default"


class AggregatorLogger:
    """Per-run logger for aggregator outputs.

    Directory layout:
        {base_logs_dir}/query_{id}/{log_id}/{variant}/

    `variant` exists so multiple (model, prompt) configs can run against the
    SAME reranker snapshot without overwriting each other — essential for A/B.
    Leave `variant=None` for legacy-style single-config runs; the dir collapses
    to `{base}/query_{id}/{log_id}/` in that case.
    """

    def __init__(
        self,
        query_id: int,
        log_id: str,
        variant: str | None = None,
        base_logs_dir: Path | None = None,
    ) -> None:
        """Create a logger.

        Args:
            query_id: Numeric query ID (matches `query_{id}` top-level folder).
            log_id: Timestamp directory name, e.g. "20260416_144941".
            variant: Sub-folder under the timestamp dir used to distinguish
                A/B runs (e.g. "qwen3.6-plus__prompt_1"). If None, output lands
                directly in the timestamp dir (legacy layout).
            base_logs_dir: Root logs dir. Defaults to
                `agents/deep_search_v3/aggregator/logs/`.
        """
        self.query_id = query_id
        self.log_id = log_id
        self.variant = sanitize_variant(variant) if variant else None
        self.base_logs_dir = Path(base_logs_dir) if base_logs_dir else DEFAULT_BASE_LOGS_DIR
        self.aggregator_dir.mkdir(parents=True, exist_ok=True)

    # -----------------------------------------------------------------------
    # Paths
    # -----------------------------------------------------------------------

    @property
    def aggregator_dir(self) -> Path:
        """Directory for this run's artifacts.

        With variant: `.../query_N/log_id/variant/`
        Without:       `.../query_N/log_id/`
        """
        base = self.base_logs_dir / f"query_{self.query_id}" / self.log_id
        return base / self.variant if self.variant else base

    # -----------------------------------------------------------------------
    # Core writers
    # -----------------------------------------------------------------------

    def write_synthesis(self, synthesis_md: str, reference_block_md: str) -> Path:
        """Write the final synthesis to synthesis.md.

        Format: synthesis body + blank line + reference block.
        """
        path = self.aggregator_dir / "synthesis.md"
        body = (synthesis_md or "").rstrip()
        ref_block = (reference_block_md or "").strip()
        content = body + "\n\n" + ref_block if ref_block else body + "\n"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            logger.info("Aggregator synthesis saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save aggregator synthesis: %s", e)
        return path

    def write_references(self, references: list[Reference]) -> Path:
        """Serialize references to references.json."""
        path = self.aggregator_dir / "references.json"
        try:
            payload = [ref.model_dump() for ref in references]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Aggregator references saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save aggregator references: %s", e)
        return path

    def write_validation(
        self,
        validation: ValidationReport,
        prompt_key: str,
        model_used: str,
    ) -> Path:
        """Write validation.json with the full report plus run metadata."""
        path = self.aggregator_dir / "validation.json"
        try:
            payload = {
                "prompt_key": prompt_key,
                "model_used": model_used,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "report": validation.model_dump() if validation else None,
            }
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            logger.info("Aggregator validation saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save aggregator validation: %s", e)
        return path

    def write_llm_raw(self, stage: str, raw_output: str) -> Path:
        """Write raw LLM text output to llm_raw_{stage}.txt.

        Useful for debugging when structured validation fails.
        `stage` should be one of: 'draft' | 'critique' | 'rewrite' | 'single'.
        """
        path = self.aggregator_dir / f"llm_raw_{stage}.txt"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(raw_output or "", encoding="utf-8")
            logger.info("Aggregator raw LLM (%s) saved -> %s", stage, path)
        except Exception as e:
            logger.warning("Failed to save aggregator raw LLM (%s): %s", stage, e)
        return path

    def write_thinking(self, thinking_block: str) -> Path:
        """Write the stripped <thinking> block to thinking.md."""
        path = self.aggregator_dir / "thinking.md"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(thinking_block or "", encoding="utf-8")
            logger.info("Aggregator thinking saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save aggregator thinking: %s", e)
        return path

    def write_prompt(
        self,
        prompt_key: str,
        system_prompt: str,
        user_message: str,
        stage: str = "single",
    ) -> Path:
        """Write the exact system prompt + rendered user message to prompt_{stage}.md.

        One file per LLM call — for DCR chains, call this with
        stage='draft' | 'critique' | 'rewrite'. Single-shot uses 'single'.
        Fallback calls use 'fallback_single'.

        The content captures every byte sent to the model so runs are exactly
        reproducible from the log alone.
        """
        path = self.aggregator_dir / f"prompt_{stage}.md"
        lines: list[str] = [
            f"# Aggregator prompt — stage={stage}",
            "",
            f"**prompt_key:** `{prompt_key}`",
            "",
            "## System prompt (instructions)",
            "",
            "```",
            (system_prompt or "").rstrip(),
            "```",
            "",
            "## User message",
            "",
            "```",
            (user_message or "").rstrip(),
            "```",
            "",
        ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("Aggregator prompt (%s) saved -> %s", stage, path)
        except Exception as e:
            logger.warning("Failed to save aggregator prompt (%s): %s", stage, e)
        return path

    # -----------------------------------------------------------------------
    # Human-readable run summary
    # -----------------------------------------------------------------------

    def write_run_summary(
        self,
        agg_input: AggregatorInput,
        output: AggregatorOutput,
        duration_s: float,
    ) -> Path:
        """Write run.md — human-readable summary with timing, prompt, refs, validation."""
        path = self.aggregator_dir / "run.md"

        validation = output.validation
        ref_count = len(output.references)
        cited_count = (
            len(validation.cited_numbers) if validation and validation.cited_numbers else 0
        )
        validation_status = (
            ("PASS" if validation.passed else "FAIL") if validation else "N/A"
        )

        lines: list[str] = []
        lines.append(f"# aggregator — query_{self.query_id}/{self.log_id}")
        lines.append("")
        lines.append("| | |")
        lines.append("|---|---|")
        lines.append(f"| **Duration** | {duration_s:.1f}s |")
        lines.append(f"| **Prompt** | {output.prompt_key} |")
        lines.append(f"| **Model** | {output.model_used or '—'} |")
        lines.append(f"| **References** | {ref_count} |")
        lines.append(f"| **Cited** | {cited_count} |")
        lines.append(f"| **Validation** | {validation_status} |")
        lines.append(f"| **Confidence** | {output.confidence} |")
        lines.append("")

        # Original query
        lines.append("## Original Query")
        lines.append(f"> {agg_input.original_query}")
        lines.append("")

        # Sub-queries
        if agg_input.sub_queries:
            lines.append("## Sub-queries")
            for i, sq in enumerate(agg_input.sub_queries, 1):
                qtext = getattr(sq, "query", "") or ""
                lines.append(f"{i}. {qtext}")
            lines.append("")

        # Validation detail block
        lines.append("## Validation")
        if validation:
            lines.append(f"- passed: {str(validation.passed).lower()}")
            lines.append(f"- dangling_citations: {validation.dangling_citations}")
            lines.append(f"- unused_references: {validation.unused_references}")
            lines.append(f"- ungrounded_snippets: {validation.ungrounded_snippets}")
            lines.append(f"- arabic_only_ok: {str(validation.arabic_only_ok).lower()}")
            lines.append(f"- structure_ok: {str(validation.structure_ok).lower()}")
            lines.append(f"- gap_honesty_ok: {str(validation.gap_honesty_ok).lower()}")
            lines.append(
                f"- sub_query_coverage: {validation.sub_query_coverage:.2f}"
            )
            if validation.notes:
                lines.append("- notes:")
                for note in validation.notes:
                    lines.append(f"  - {note}")
        else:
            lines.append("- (no validation report)")
        lines.append("")

        # Gaps surfaced to user
        if output.gaps:
            lines.append("## Gaps")
            for i, g in enumerate(output.gaps, 1):
                lines.append(f"{i}. {g}")
            lines.append("")

        # File index — include whatever actually landed on disk.
        lines.append("## Files")
        lines.append("- [synthesis.md](synthesis.md)")
        lines.append("- [references.json](references.json)")
        lines.append("- [validation.json](validation.json)")
        lines.append("- [thinking.md](thinking.md)")
        try:
            for p in sorted(self.aggregator_dir.glob("prompt_*.md")):
                lines.append(f"- [{p.name}]({p.name})")
            for p in sorted(self.aggregator_dir.glob("llm_raw_*.txt")):
                lines.append(f"- [{p.name}]({p.name})")
        except Exception:  # noqa: BLE001
            pass

        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n".join(lines), encoding="utf-8")
            logger.info("Aggregator run summary saved -> %s", path)
        except Exception as e:
            logger.warning("Failed to save aggregator run summary: %s", e)
        return path
