"""Run logger for reg_search agent.

Per-run directory with individual markdown files for each agent and query:

    logs/{log_id}/
        run.json                        # Full machine-readable JSON
        run.md                          # Overview: timeline, rounds, final result
        expander/
            round_1.md                  # Expander output per round
        search/
            round_1_q1_{slug}.md        # Raw search results per query
        aggregator/
            round_1.md                  # Aggregator output per round
"""
from __future__ import annotations

import json
import logging
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic_ai._usage import RunUsage

    from .models import AggregatorOutput, ExpanderOutput, RegSearchResult, RerankerQueryResult

logger = logging.getLogger(__name__)

import os as _os
_default_logs = Path(__file__).resolve().parent / "reports"
_logs_override = _os.environ.get("LUNA_DEEP_SEARCH_LOGS_DIR")
LOGS_DIR = (Path(_logs_override) / "v4_reg_search") if _logs_override else _default_logs
TEST_QUERIES_PATH = Path(__file__).resolve().parent.parent.parent / "test_queries.json"


# ---------------------------------------------------------------------------
# Query resolution — maps query text to query_{id} folder
# ---------------------------------------------------------------------------

def _load_test_queries() -> dict:
    """Load test_queries.json."""
    if TEST_QUERIES_PATH.exists():
        return json.loads(TEST_QUERIES_PATH.read_text(encoding="utf-8"))
    return {"metadata": {}, "queries": []}


def resolve_query_id(query_text: str | None, query_id: int | None = None) -> tuple[int, str]:
    """Resolve a query to its ID and text.

    Args:
        query_text: The query string (may be None if using --query-id).
        query_id: Explicit query ID (from --query-id flag).

    Returns:
        (query_id, query_text) tuple.

    Behavior:
        - If query_id given: look up that ID in test_queries.json, return its text.
        - If query_text given and matches an existing entry: return that ID.
        - If query_text given but no match: append to test_queries.json with next ID.
        - If neither: pick a random query from test_queries.json.
    """
    import random

    data = _load_test_queries()
    queries = data.get("queries", [])

    # Explicit ID
    if query_id is not None:
        for q in queries:
            if q.get("id") == query_id:
                return query_id, q["text"]
        raise ValueError(f"Query ID {query_id} not found in test_queries.json")

    # Random pick (no query text provided)
    if not query_text:
        if not queries:
            raise ValueError("No queries in test_queries.json and no query text provided")
        pick = random.choice(queries)
        return pick["id"], pick["text"]

    # Try to match existing query by text
    for q in queries:
        if q.get("text", "").strip() == query_text.strip():
            return q["id"], q["text"]

    # No match — append as new query
    max_id = max((q.get("id", 0) for q in queries), default=0)
    new_id = max_id + 1
    new_entry = {"id": new_id, "category": "ad-hoc", "text": query_text.strip()}
    queries.append(new_entry)
    data["queries"] = queries
    data.setdefault("metadata", {})["total_queries"] = len(queries)

    TEST_QUERIES_PATH.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("Appended query id=%d to test_queries.json", new_id)

    return new_id, query_text.strip()


def make_log_id(query_id: int) -> str:
    """Create a log_id path: query_{id}/{timestamp}."""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return f"query_{query_id}/{ts}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _slugify(text: str, max_len: int = 20) -> str:
    """Create a filename-safe slug from Arabic/English text."""
    # Keep Arabic letters, ASCII alphanumerics, spaces
    cleaned = re.sub(r"[^\w\s]", "", text, flags=re.UNICODE)
    cleaned = cleaned.strip()[:max_len]
    # Replace whitespace runs with underscore
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned or "query"


def _format_usage(usage: RunUsage) -> str:
    reasoning = usage.details.get("reasoning_tokens", 0) if usage.details else 0
    if reasoning:
        return (
            f"| Requests | Input tokens | Output tokens | Reasoning tokens | Total tokens |\n"
            f"|----------|-------------|---------------|-----------------|-------------|\n"
            f"| {usage.requests} | {usage.input_tokens:,} | {usage.output_tokens:,} | {reasoning:,} | {usage.total_tokens:,} |"
        )
    return (
        f"| Requests | Input tokens | Output tokens | Total tokens |\n"
        f"|----------|-------------|---------------|-------------|\n"
        f"| {usage.requests} | {usage.input_tokens:,} | {usage.output_tokens:,} | {usage.total_tokens:,} |"
    )


def _usage_dict(usage: RunUsage) -> dict:
    d = {
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "total_tokens": usage.total_tokens,
    }
    if usage.details:
        d["details"] = dict(usage.details)
    return d


def _extract_reasoning(messages_json: bytes | None) -> list[str]:
    """Extract reasoning/thinking content from model messages JSON.

    Looks for parts with part_kind 'thinking' or 'reasoning'.
    Returns a list of reasoning text blocks.
    """
    if not messages_json:
        return []
    try:
        messages = json.loads(messages_json)
    except Exception:
        return []

    reasoning_blocks: list[str] = []
    for msg in messages:
        for part in msg.get("parts", []):
            pk = part.get("part_kind", "")
            content = part.get("content", "")
            if pk in ("thinking", "reasoning") and content:
                reasoning_blocks.append(content)
    return reasoning_blocks


QUALITY_LABELS = {"strong": "قوية", "moderate": "متوسطة", "weak": "ضعيفة", "pending": "معلق"}


# ---------------------------------------------------------------------------
# Directory setup
# ---------------------------------------------------------------------------

def create_run_dir(log_id: str) -> Path:
    """Create the base directory for a run. Subdirs created on-demand by save functions."""
    run_dir = LOGS_DIR / log_id
    (run_dir / "search").mkdir(parents=True, exist_ok=True)
    (run_dir / "reranker").mkdir(parents=True, exist_ok=True)
    return run_dir


# ---------------------------------------------------------------------------
# Per-node markdown writers
# ---------------------------------------------------------------------------

def save_expander_md(
    log_id: str,
    round_num: int,
    prompt_key: str,
    system_prompt: str,
    user_message: str,
    output: ExpanderOutput,
    usage: RunUsage,
    messages_json: bytes | None = None,
) -> None:
    """Save expander round markdown."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / f"expander_{prompt_key}" / f"round_{round_num}.md"

    lines: list[str] = []
    lines.append(f"# Expander — Round {round_num}")
    lines.append(f"**Prompt key:** `{prompt_key}`")
    lines.append("")

    # Usage
    lines.append("## Usage")
    lines.append(_format_usage(usage))
    lines.append("")

    # System prompt
    lines.append("## System Prompt")
    lines.append("")
    lines.append(f"```\n{system_prompt}\n```")
    lines.append("")

    # User message
    lines.append("## User Message")
    lines.append("")
    lines.append(user_message)
    lines.append("")

    # Output
    lines.append("## Output")
    lines.append("")
    lines.append(f"**Queries ({len(output.queries)}):**")
    lines.append("")
    for i, q in enumerate(output.queries, 1):
        rationale = output.rationales[i - 1] if i <= len(output.rationales) else ""
        lines.append(f"{i}. {q}")
        if rationale:
            lines.append(f"   > {rationale}")
    lines.append("")

    # Model messages (raw JSON)
    if messages_json:
        lines.append("<details><summary>Model Messages (raw JSON)</summary>")
        lines.append("")
        lines.append("```json")
        try:
            parsed = json.loads(messages_json)
            lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        except Exception:
            lines.append(messages_json.decode("utf-8", errors="replace"))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Expander MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save expander MD: %s", e)

    # Save reasoning to a separate file
    reasoning_blocks = _extract_reasoning(messages_json)
    if reasoning_blocks:
        reasoning_path = path.parent / f"reasoning_round_{round_num}.md"
        r_lines: list[str] = []
        r_lines.append(f"# Reasoning — Expander Round {round_num}")
        r_lines.append(f"**Prompt key:** `{prompt_key}`")
        r_lines.append("")
        for i, block in enumerate(reasoning_blocks, 1):
            if len(reasoning_blocks) > 1:
                r_lines.append(f"## Block {i}")
                r_lines.append("")
            r_lines.append(block)
            r_lines.append("")
        try:
            reasoning_path.write_text("\n".join(r_lines), encoding="utf-8")
            logger.info("Expander reasoning saved -> %s", reasoning_path)
        except Exception as e:
            logger.warning("Failed to save expander reasoning: %s", e)


def save_search_query_md(
    log_id: str,
    round_num: int,
    query_index: int,
    query: str,
    raw_markdown: str,
    result_count: int,
    rationale: str = "",
) -> None:
    """Save individual search query results markdown."""
    slug = _slugify(query)
    run_dir = LOGS_DIR / log_id
    filename = f"round_{round_num}_q{query_index}_{slug}.md"
    path = run_dir / "search" / filename

    lines: list[str] = []
    lines.append(f"# Search — Round {round_num}, Query {query_index}")
    lines.append("")
    lines.append(f"**Query:** {query}")
    if rationale:
        lines.append(f"**Rationale:** {rationale}")
    lines.append(f"**Results:** {result_count}")
    lines.append(f"**Markdown length:** {len(raw_markdown):,} chars")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(raw_markdown)

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Search MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save search MD: %s", e)


def save_reranker_md(
    log_id: str,
    round_num: int,
    query_index: int,
    query_result: RerankerQueryResult,
) -> None:
    """Save reranker per-query markdown (v2: classification + assembled results)."""
    slug = _slugify(query_result.query)
    run_dir = LOGS_DIR / log_id
    filename = f"round_{round_num}_q{query_index}_{slug}.md"
    path = run_dir / "reranker" / filename

    lines: list[str] = []
    lines.append(f"# Reranker — Round {round_num}, Query {query_index}")
    lines.append("")
    lines.append(f"**Query:** {query_result.query}")
    if query_result.rationale:
        lines.append(f"**Rationale:** {query_result.rationale}")
    lines.append(f"**Sufficient:** {query_result.sufficient}")
    lines.append(f"**Results kept:** {len(query_result.results)}")
    lines.append(f"**Dropped:** {query_result.dropped_count}")
    lines.append(f"**Classification rounds:** {query_result.unfold_rounds}")
    lines.append(f"**DB unfolds:** {query_result.total_unfolds}")
    if query_result.summary_note:
        lines.append(f"**Summary:** {query_result.summary_note}")
    lines.append("")

    # Kept results
    if query_result.results:
        lines.append(f"## Kept Results ({len(query_result.results)})")
        lines.append("")
        for i, res in enumerate(query_result.results, 1):
            rel_label = "عالية" if res.relevance == "high" else "متوسطة"
            type_label = "مادة" if res.source_type == "article" else "باب/فصل"
            lines.append(f"### {i}. [{type_label}] {res.title} (صلة: {rel_label})")
            if res.regulation_title:
                lines.append(f"- **النظام:** {res.regulation_title}")
            if res.section_title and res.source_type == "article":
                lines.append(f"- **الباب:** {res.section_title}")
            if res.article_num:
                lines.append(f"- **رقم المادة:** {res.article_num}")
            if res.reasoning:
                lines.append(f"- **Reasoning:** {res.reasoning}")
            if res.content:
                lines.append(f"\n> {res.content[:500]}")
            lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Reranker MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save reranker MD: %s", e)

    # Per-round I/O trace — written when the reranker ran multiple LLM rounds
    # (e.g. round 2 after unfolds). Each file shows the exact markdown the LLM
    # received and the full classification it emitted.
    round_trace: list[dict] = getattr(query_result, "_round_trace", None) or []
    if len(round_trace) > 1 or (len(round_trace) == 1 and round_trace[0].get("unfolds")):
        rounds_dir = run_dir / "reranker" / f"q{query_index}_rounds"
        try:
            rounds_dir.mkdir(parents=True, exist_ok=True)
            for rt in round_trace:
                rn = rt["round_num"]
                # Input: what the LLM received
                inp_path = rounds_dir / f"round_{rn}_input.md"
                inp_lines = [
                    f"# Reranker Input — q{query_index} round {rn}",
                    "",
                    f"**query**: {query_result.query}",
                    f"**rationale**: {query_result.rationale}",
                    "",
                    "## Full user message sent to LLM",
                    "",
                    "```",
                    rt.get("user_msg", ""),
                    "```",
                ]
                inp_path.write_text("\n".join(inp_lines), encoding="utf-8")

                # Output: what the LLM classified
                cls = rt.get("classification", {})
                out_path = rounds_dir / f"round_{rn}_output.md"
                out_lines = [
                    f"# Reranker Output — q{query_index} round {rn}",
                    "",
                    f"- **sufficient**: {cls.get('sufficient')}",
                    f"- **summary_note**: {cls.get('summary_note', '')}",
                    "",
                    "## Decisions",
                    "",
                    "| pos | action | relevance | reasoning |",
                    "|-----|--------|-----------|-----------|",
                ]
                for d in cls.get("decisions", []):
                    r = (d.get("reasoning") or "").replace("|", "\\|").replace("\n", " ")
                    if len(r) > 160:
                        r = r[:160] + "..."
                    out_lines.append(
                        f"| {d.get('position')} | {d.get('action')} "
                        f"| {d.get('relevance', '-')} | {r} |"
                    )

                if rt.get("unfolds"):
                    out_lines += ["", "## Unfolds triggered after this round", ""]
                    for u in rt["unfolds"]:
                        status = f"{u['resulting_blocks']} blocks" if u["resulting_blocks"] else u.get("error", "0 blocks")
                        out_lines.append(f"- `{u['mode']}` on `{u['block_id'][:12]}` → {status}")
                        if u.get("titles"):
                            for t in u["titles"]:
                                out_lines.append(f"  - {t}")

                out_path.write_text("\n".join(out_lines), encoding="utf-8")
        except Exception as e:
            logger.warning("Failed to save reranker round trace: %s", e)


def save_reranker_json(
    log_id: str,
    reranker_results: list[RerankerQueryResult],
) -> None:
    """Save reranker summary JSON with per-query metrics and decision logs."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / "reranker" / "summary.json"

    # Per-query details
    queries: list[dict] = []
    total_articles = 0
    total_sections = 0
    total_dropped = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_requests = 0

    for rr in reranker_results:
        articles_count = sum(1 for r in rr.results if r.source_type == "article")
        sections_count = sum(1 for r in rr.results if r.source_type == "section")
        total_articles += articles_count
        total_sections += sections_count
        total_dropped += rr.dropped_count

        # Usage rounds (stashed by RerankerNode)
        usage_entries = getattr(rr, "_usage_entries", [])
        rounds: list[dict] = []
        for ue in usage_entries:
            rnd = {
                "round": ue.get("reranker_round", 0),
                "input_tokens": ue.get("input_tokens", 0),
                "output_tokens": ue.get("output_tokens", 0),
                "requests": ue.get("requests", 0),
            }
            reasoning = (ue.get("details") or {}).get("reasoning_tokens", 0)
            if reasoning:
                rnd["reasoning_tokens"] = reasoning
            rounds.append(rnd)
            total_input_tokens += rnd["input_tokens"]
            total_output_tokens += rnd["output_tokens"]
            total_requests += rnd["requests"]

        # Decision log with RRF (stashed by RerankerNode)
        decision_log = getattr(rr, "_decision_log", [])
        kept_decisions = [d for d in decision_log if d.get("action") == "keep"]
        dropped_decisions = [d for d in decision_log if d.get("action") == "drop"]
        unfolded_decisions = [d for d in decision_log if d.get("action") == "unfold"]

        query_entry: dict = {
            "query": rr.query,
            "sufficient": rr.sufficient,
            "articles_kept": articles_count,
            "sections_kept": sections_count,
            "dropped": rr.dropped_count,
            "classification_rounds": rr.unfold_rounds,
            "db_unfolds": rr.total_unfolds,
            "rounds": rounds,
            "decisions": {
                "kept": [
                    {"position": d["position"], "rrf": d.get("rrf", 0), "relevance": d.get("relevance", "medium")}
                    for d in kept_decisions
                ],
                "dropped": [
                    {"position": d["position"], "rrf": d.get("rrf", 0)}
                    for d in dropped_decisions
                ],
                "unfolded": [
                    {"position": d["position"], "rrf": d.get("rrf", 0)}
                    for d in unfolded_decisions
                ],
            },
        }
        if rr.summary_note:
            query_entry["summary_note"] = rr.summary_note

        queries.append(query_entry)

    sufficient_count = sum(1 for rr in reranker_results if rr.sufficient)

    summary: dict = {
        "total_queries": len(reranker_results),
        "sufficient_queries": sufficient_count,
        "total_articles_kept": total_articles,
        "total_sections_kept": total_sections,
        "total_dropped": total_dropped,
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "total_requests": total_requests,
        "queries": queries,
    }

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(summary, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Reranker JSON saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save reranker JSON: %s", e)


def save_aggregator_md(
    log_id: str,
    round_num: int,
    prompt_key: str,
    system_prompt: str,
    user_message: str,
    output: AggregatorOutput,
    usage: RunUsage,
    messages_json: bytes | None = None,
) -> None:
    """Save aggregator round markdown."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / f"aggregator_{prompt_key}" / f"round_{round_num}.md"

    qlabel = QUALITY_LABELS.get(output.quality, output.quality)

    lines: list[str] = []
    lines.append(f"# Aggregator — Round {round_num}")
    lines.append(f"**Prompt key:** `{prompt_key}`")
    lines.append(f"**Sufficient:** {output.sufficient}")
    lines.append(f"**Quality:** {output.quality} ({qlabel})")
    lines.append("")

    # Usage
    lines.append("## Usage")
    lines.append(_format_usage(usage))
    lines.append("")

    # Weak axes
    if output.weak_axes:
        lines.append(f"## Weak Axes ({len(output.weak_axes)})")
        lines.append("")
        for i, ax in enumerate(output.weak_axes, 1):
            lines.append(f"{i}. **{ax.reason}**")
            lines.append(f"   suggested: {ax.suggested_query}")
        lines.append("")

    # Citations
    if output.citations:
        lines.append(f"## Citations ({len(output.citations)})")
        lines.append("")
        for i, c in enumerate(output.citations, 1):
            lines.append(f"{i}. [{c.source_type}] **{c.ref}** — {c.title}")
            if c.regulation_title:
                lines.append(f"   النظام: {c.regulation_title}")
            if c.article_num:
                lines.append(f"   المادة: {c.article_num}")
            if c.content_snippet:
                lines.append(f"   > {c.content_snippet[:300]}")
            if c.relevance:
                lines.append(f"   الصلة: {c.relevance}")
        lines.append("")

    # Synthesis
    lines.append(f"## Synthesis ({len(output.synthesis_md)} chars)")
    lines.append("")
    lines.append(output.synthesis_md)
    lines.append("")

    # Reasoning tokens (thinking/reasoning content from model)
    reasoning_blocks = _extract_reasoning(messages_json)
    if reasoning_blocks:
        lines.append(f"## Reasoning ({len(reasoning_blocks)} block(s))")
        lines.append("")
        for i, block in enumerate(reasoning_blocks, 1):
            if len(reasoning_blocks) > 1:
                lines.append(f"### Block {i}")
                lines.append("")
            lines.append(block)
            lines.append("")

    # System prompt
    lines.append("---")
    lines.append("")
    lines.append("<details><summary>System Prompt</summary>")
    lines.append("")
    lines.append(f"```\n{system_prompt}\n```")
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # User message (can be very long — collapse it)
    lines.append("<details><summary>User Message ({:,} chars)</summary>".format(len(user_message)))
    lines.append("")
    lines.append(user_message)
    lines.append("")
    lines.append("</details>")
    lines.append("")

    # Model messages (raw JSON)
    if messages_json:
        lines.append("<details><summary>Model Messages (raw JSON)</summary>")
        lines.append("")
        lines.append("```json")
        try:
            parsed = json.loads(messages_json)
            lines.append(json.dumps(parsed, ensure_ascii=False, indent=2))
        except Exception:
            lines.append(messages_json.decode("utf-8", errors="replace"))
        lines.append("```")
        lines.append("")
        lines.append("</details>")
        lines.append("")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Aggregator MD saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save aggregator MD: %s", e)


# ---------------------------------------------------------------------------
# Run overview + JSON
# ---------------------------------------------------------------------------

def save_run_overview_md(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    expander_prompt_key: str,
    aggregator_prompt_key: str,
    duration_s: float,
    result: RegSearchResult,
    round_summaries: list[dict],
) -> None:
    """Save the run overview markdown."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.md"

    qlabel = QUALITY_LABELS.get(result.quality, result.quality)

    lines: list[str] = []
    lines.append(f"# reg_search — {log_id}")
    lines.append("")
    lines.append(f"| | |")
    lines.append(f"|---|---|")
    lines.append(f"| **Duration** | {duration_s:.1f}s |")
    lines.append(f"| **Quality** | {result.quality} ({qlabel}) |")
    lines.append(f"| **Rounds** | {result.rounds_used} |")
    lines.append(f"| **Queries** | {len(result.queries_used)} |")
    lines.append(f"| **Citations** | {len(result.citations)} |")
    lines.append(f"| **Expander prompt** | `{expander_prompt_key}` |")
    lines.append(f"| **Aggregator prompt** | `{aggregator_prompt_key}` |")
    lines.append("")

    # Focus
    lines.append("## Focus")
    lines.append(f"> {focus_instruction}")
    if user_context:
        lines.append(f">\n> **Context:** {user_context}")
    lines.append("")

    # Timeline
    lines.append("## Timeline")
    lines.append("")
    for rs in round_summaries:
        rn = rs.get("round", "?")
        lines.append(f"### Round {rn}")
        if rs.get("expander_queries"):
            lines.append(f"- **Expander:** {len(rs['expander_queries'])} queries")
            for i, q in enumerate(rs["expander_queries"], 1):
                lines.append(f"  {i}. {q}")
        if rs.get("search_total"):
            lines.append(f"- **Search:** {rs['search_total']} results from {rs.get('search_queries', '?')} queries")
        if rs.get("aggregator_quality"):
            suf = "sufficient" if rs.get("aggregator_sufficient") else "insufficient"
            lines.append(f"- **Aggregator:** {rs['aggregator_quality']} ({suf})")
            if rs.get("weak_axes_count"):
                lines.append(f"  - {rs['weak_axes_count']} weak axes identified")
        lines.append("")

    # All queries
    lines.append("## All Queries")
    lines.append("")
    for i, q in enumerate(result.queries_used, 1):
        lines.append(f"{i}. {q}")
    lines.append("")

    # File index
    lines.append("## Files")
    lines.append("")
    run_dir_path = LOGS_DIR / log_id
    if run_dir_path.exists():
        for sd in sorted(run_dir_path.iterdir()):
            if sd.is_dir():
                for f in sorted(sd.iterdir()):
                    if f.suffix == ".md":
                        lines.append(f"- [{sd.name}/{f.name}]({sd.name}/{f.name})")
    lines.append("")

    try:
        path.write_text("\n".join(lines), encoding="utf-8")
        logger.info("Run overview saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save run overview: %s", e)


def save_run_json(
    log_id: str,
    focus_instruction: str,
    user_context: str,
    expander_prompt_key: str,
    aggregator_prompt_key: str,
    duration_s: float,
    result: RegSearchResult,
    events: list[dict],
    round_summaries: list[dict],
    search_results_log: list[dict] | None = None,
    inner_usage: list[dict] | None = None,
    error: str | None = None,
    query_id: int = 0,
    models: dict[str, str] | None = None,
    thinking_effort: str | None = None,
    step_timings: dict | None = None,
) -> None:
    """Save the enriched JSON log."""
    run_dir = LOGS_DIR / log_id
    path = run_dir / "run.json"

    ts = datetime.now(timezone.utc).isoformat()

    # Cost totals
    total_in = sum(u.get("input_tokens", 0) for u in (inner_usage or []))
    total_out = sum(u.get("output_tokens", 0) for u in (inner_usage or []))
    total_reasoning = sum(
        u.get("details", {}).get("reasoning_tokens", 0) for u in (inner_usage or [])
    )

    log_data: dict[str, Any] = {
        "log_id": log_id,
        "query_id": query_id,
        "timestamp": ts,
        "agent": "reg_search",
        "status": "error" if error else "success",
        "duration_seconds": round(duration_s, 2),
        "models": models or {},
        "thinking_effort": thinking_effort,
        "input": {
            "focus_instruction": focus_instruction,
            "user_context": user_context,
        },
        "prompt_keys": {
            "expander": expander_prompt_key,
            "aggregator": aggregator_prompt_key,
        },
        "result": {
            "quality": result.quality,
            "rounds": result.rounds_used,
            "queries": result.queries_used,
            "citations_count": len(result.citations),
            "summary_md_length": len(result.summary_md),
            "summary_md": result.summary_md,
        },
        "cost": {
            "total_input_tokens": total_in,
            "total_output_tokens": total_out,
            "total_reasoning_tokens": total_reasoning,
            "total_tokens": total_in + total_out,
            "per_agent": inner_usage or [],
        },
        "step_timings": {
            k: round(v, 2) for k, v in (step_timings or {}).items()
        },
        "rounds": round_summaries,
        "search_results_log": search_results_log or [],
        "events": events,
    }

    if error:
        log_data["error"] = error

    try:
        path.write_text(
            json.dumps(log_data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("Run JSON saved -> %s", path)
    except Exception as e:
        logger.warning("Failed to save run JSON: %s", e)
