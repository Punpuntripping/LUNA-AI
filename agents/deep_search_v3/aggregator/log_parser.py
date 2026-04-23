"""Parse reg_search logs into AggregatorInput for replay/testing.

This module lets us replay the aggregator against pre-existing reg_search runs
without having to re-invoke the expander/search/reranker pipeline (saves API $).

The reg_search logs live under:
    agents/deep_search_v3/reg_search/logs/query_N/TIMESTAMP/
        run.md
        run.json
        expander_prompt_X/...
        search/...
        reranker/
            round_R_qI_<slug>.md   -- one per sub-query per round
            summary.json

We reconstruct RerankerQueryResult objects by parsing each reranker markdown
file, plus pull the original user query out of run.md.

In addition, load_aggregator_input_from_ura() converts a UnifiedRetrievalArtifact
into an AggregatorInput -- the forward path used by run_full_loop(). The
markdown-based load_aggregator_input_from_run() remains as the replay fallback.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

from agents.deep_search_v3.aggregator.models import AggregatorInput
from agents.deep_search_v3.reg_search.models import (
    RerankedResult,
    RerankerQueryResult,
)

if TYPE_CHECKING:
    from agents.deep_search_v3.ura.schema import UnifiedRetrievalArtifact


# ---------------------------------------------------------------------------
# Reranker markdown parsing
# ---------------------------------------------------------------------------


# Header-level fields at the top of a reranker file, written as "**Name:** value"
_HEADER_FIELDS = {
    "Query",
    "Rationale",
    "Sufficient",
    "Results kept",
    "Dropped",
    "Classification rounds",
    "DB unfolds",
    "Summary",
}


_BOLD_FIELD_RE = re.compile(r"^\*\*([^*]+):\*\*\s*(.*)$")


# Per-result Arabic metadata rows: "- **النظام:** ..." etc.
_RESULT_META_FIELDS = {
    "النظام": "regulation_title",
    "الباب": "section_title",
    "رقم المادة": "article_num",
    "Reasoning": "reasoning",
}


# Relevance Arabic -> English literal
_RELEVANCE_MAP = {
    "عالية": "high",
    "متوسطة": "medium",
}


# Matches the "### N. [مادة|باب/فصل] <title> (صلة: <Arabic>)" per-result heading
_RESULT_HEADER_RE = re.compile(
    r"^###\s+(\d+)\.\s*\[(مادة|باب/فصل)\]\s*(.+?)\s*\(صلة:\s*(\S+)\)\s*$"
)


def _classify_source_type(marker: str) -> str:
    if marker == "مادة":
        return "article"
    # "باب/فصل"
    return "section"


def _join_paragraphs(lines: list[str]) -> str:
    """Join a list of '> ...' blockquote lines, preserving paragraph breaks.

    Blank lines in the source (i.e. the stripped line is empty) become paragraph
    breaks in the output. Consecutive non-blank '> ' lines are joined by newline
    so original line breaks are kept.
    """
    out: list[str] = []
    current: list[str] = []
    for raw in lines:
        s = raw.rstrip()
        if s == "":
            if current:
                out.append("\n".join(current))
                current = []
            continue
        # Strip leading "> " or ">" marker
        if s.startswith("> "):
            current.append(s[2:])
        elif s.startswith(">"):
            current.append(s[1:])
        else:
            current.append(s)
    if current:
        out.append("\n".join(current))
    return "\n\n".join(p for p in out if p)


def _is_block_break(line: str) -> bool:
    """True if line starts a new block that terminates a multi-line field."""
    s = line.lstrip()
    if s.startswith("### ") or s.startswith("## "):
        return True
    m = _BOLD_FIELD_RE.match(s)
    if m and m.group(1).strip() in _HEADER_FIELDS:
        return True
    return False


def parse_reranker_file(md_path: Path) -> RerankerQueryResult:
    """Parse a single reranker/round_X_qN_*.md file into RerankerQueryResult."""
    text = md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    # -- 1) Parse header fields ---------------------------------------------
    header: dict[str, str] = {}
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        if line.startswith("## Kept Results") or line.startswith("### "):
            break
        m = _BOLD_FIELD_RE.match(line.strip())
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip()
            if key == "Summary":
                # Summary may be multi-line — collect until next block break
                buf = [val] if val else []
                j = i + 1
                while j < n and not _is_block_break(lines[j]):
                    buf.append(lines[j].rstrip())
                    j += 1
                header["Summary"] = "\n".join(x for x in buf).strip()
                i = j
                continue
            header[key] = val
        i += 1

    # -- 2) Find "## Kept Results (N)" and then parse each "### N." block ---
    # i may still point to "## Kept Results" or the first "### " header.
    # Skip any "## Kept Results" line.
    while i < n and not lines[i].lstrip().startswith("### "):
        i += 1

    results: list[RerankedResult] = []
    while i < n:
        line = lines[i]
        m = _RESULT_HEADER_RE.match(line.strip())
        if not m:
            i += 1
            continue
        _idx, type_marker, title, rel_ar = m.groups()
        source_type = _classify_source_type(type_marker)
        relevance = _RELEVANCE_MAP.get(rel_ar.strip(), "medium")

        regulation_title = ""
        section_title = ""
        article_num: str | None = None
        reasoning = ""
        content = ""

        j = i + 1
        # Parse metadata bullets: "- **key:** value" until blank line or next block
        while j < n:
            s = lines[j].rstrip()
            if s.startswith("### ") or s.startswith("## "):
                break
            if s.startswith("- **"):
                # "- **key:** value"
                mm = re.match(r"^-\s*\*\*([^*]+):\*\*\s*(.*)$", s)
                if mm:
                    key = mm.group(1).strip()
                    val = mm.group(2).strip()
                    # Reasoning field may continue across wrapped lines until
                    # next bullet or blockquote.
                    if key == "Reasoning":
                        buf = [val] if val else []
                        k = j + 1
                        while k < n:
                            nxt = lines[k].rstrip()
                            if (
                                nxt == ""
                                or nxt.startswith("- **")
                                or nxt.startswith("> ")
                                or nxt.startswith(">")
                                or nxt.startswith("### ")
                                or nxt.startswith("## ")
                            ):
                                break
                            buf.append(nxt)
                            k += 1
                        reasoning = " ".join(x for x in buf).strip()
                        j = k
                        continue
                    if key == "النظام":
                        regulation_title = val
                    elif key == "الباب":
                        section_title = val
                    elif key == "رقم المادة":
                        article_num = val or None
                    j += 1
                    continue
            if s.startswith("> ") or s.startswith(">") or s == "":
                # Blockquote content begins — collect until next "### ", "## ",
                # bullet, or EOF. Preserve blank lines inside the quote for
                # paragraph breaks.
                content_lines: list[str] = []
                k = j
                while k < n:
                    nxt = lines[k].rstrip()
                    if nxt.startswith("### ") or nxt.startswith("## "):
                        break
                    if nxt.startswith("- **"):
                        break
                    content_lines.append(nxt)
                    k += 1
                content = _join_paragraphs(content_lines)
                j = k
                break
            # Unknown line — skip
            j += 1

        # For sections, the "title" is the section path itself
        if source_type == "section" and not section_title:
            section_title = title

        results.append(
            RerankedResult(
                source_type=source_type,  # type: ignore[arg-type]
                title=title,
                content=content,
                article_num=article_num,
                article_context="",
                references_content="",
                regulation_title=regulation_title,
                section_title=section_title,
                section_summary="",
                relevance=relevance,  # type: ignore[arg-type]
                reasoning=reasoning,
            )
        )
        i = j if j > i else i + 1

    # -- 3) Assemble RerankerQueryResult ------------------------------------
    def _as_int(v: str, default: int = 0) -> int:
        try:
            return int(v.strip())
        except (ValueError, AttributeError):
            return default

    sufficient_raw = header.get("Sufficient", "False").strip().lower()
    sufficient = sufficient_raw in ("true", "1", "yes")

    return RerankerQueryResult(
        query=header.get("Query", "").strip(),
        rationale=header.get("Rationale", "").strip(),
        sufficient=sufficient,
        results=results,
        dropped_count=_as_int(header.get("Dropped", "0")),
        summary_note=header.get("Summary", "").strip(),
        unfold_rounds=_as_int(header.get("Classification rounds", "0")),
        total_unfolds=_as_int(header.get("DB unfolds", "0")),
    )


# ---------------------------------------------------------------------------
# run.md parsing
# ---------------------------------------------------------------------------


_FOCUS_HEADING_RE = re.compile(r"^##\s+Focus\s*$")
_NEXT_HEADING_RE = re.compile(r"^##\s+")


def parse_run_md(run_md_path: Path) -> dict:
    """Parse run.md header table + Focus block.

    Returns dict with:
      - focus: str (text of '## Focus' '> ' block, joined)
      - expander_prompt_key: str
      - aggregator_prompt_key: str
    """
    text = run_md_path.read_text(encoding="utf-8")
    lines = text.splitlines()

    expander_key = ""
    aggregator_key = ""

    # Parse table rows of shape "| **Name** | `value` |" or "| **Name** | value |"
    row_re = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*\|\s*(.+?)\s*\|\s*$")
    for line in lines:
        m = row_re.match(line)
        if not m:
            continue
        key = m.group(1).strip()
        val = m.group(2).strip().strip("`")
        if key == "Expander prompt":
            expander_key = val
        elif key == "Aggregator prompt":
            aggregator_key = val

    # Locate "## Focus" and gather blockquote lines until next "## "
    focus_lines: list[str] = []
    i = 0
    n = len(lines)
    while i < n:
        if _FOCUS_HEADING_RE.match(lines[i].strip()):
            i += 1
            while i < n and not _NEXT_HEADING_RE.match(lines[i]):
                focus_lines.append(lines[i])
                i += 1
            break
        i += 1

    focus = _join_paragraphs(focus_lines).strip()

    return {
        "focus": focus,
        "expander_prompt_key": expander_key,
        "aggregator_prompt_key": aggregator_key,
    }


# ---------------------------------------------------------------------------
# Run-dir assembly + discovery
# ---------------------------------------------------------------------------


_RERANKER_FILE_RE = re.compile(r"^round_(\d+)_q(\d+)_.*\.md$")


def _natural_sort_key(path: Path) -> tuple[int, int]:
    """Extract (round, q_index) from a reranker file name for sorting."""
    m = _RERANKER_FILE_RE.match(path.name)
    if not m:
        return (9999, 9999)
    return (int(m.group(1)), int(m.group(2)))


def load_aggregator_input_from_run(
    run_dir: Path,
    query_id: int,
    domain: str = "regulations",
) -> AggregatorInput:
    """Load an entire reg_search run directory as an AggregatorInput."""
    run_dir = Path(run_dir)
    run_md = run_dir / "run.md"
    meta = parse_run_md(run_md) if run_md.exists() else {
        "focus": "",
        "expander_prompt_key": "prompt_1",
        "aggregator_prompt_key": "prompt_1",
    }

    reranker_dir = run_dir / "reranker"
    sub_query_files: list[Path] = []
    if reranker_dir.is_dir():
        sub_query_files = sorted(
            (p for p in reranker_dir.iterdir() if _RERANKER_FILE_RE.match(p.name)),
            key=_natural_sort_key,
        )

    sub_queries: list[RerankerQueryResult] = []
    for f in sub_query_files:
        try:
            sub_queries.append(parse_reranker_file(f))
        except Exception as e:  # pragma: no cover - defensive
            # Skip malformed files but keep going; bubble up name for debugging.
            print(f"[log_parser] failed to parse {f.name}: {e}")

    timestamp = run_dir.name

    return AggregatorInput(
        original_query=meta["focus"],
        sub_queries=sub_queries,
        domain=domain,  # type: ignore[arg-type]
        session_id=timestamp,
        query_id=query_id,
        log_id=timestamp,
        prompt_key=meta.get("aggregator_prompt_key") or "prompt_1",
    )


_QUERY_DIR_RE = re.compile(r"^query_(\d+)$")
_TIMESTAMP_RE = re.compile(r"^\d{8}_\d{6}$")


def discover_runs(
    base_logs_dir: Path,
    query_filter: int | None = None,
) -> list[tuple[int, Path]]:
    """Discover all reg_search run directories under base_logs_dir.

    Returns a list of (query_id, run_dir) tuples, sorted by query_id then
    timestamp ascending.
    """
    base_logs_dir = Path(base_logs_dir)
    out: list[tuple[int, Path]] = []
    if not base_logs_dir.is_dir():
        return out

    for qdir in sorted(base_logs_dir.iterdir()):
        if not qdir.is_dir():
            continue
        m = _QUERY_DIR_RE.match(qdir.name)
        if not m:
            continue
        qid = int(m.group(1))
        if query_filter is not None and qid != query_filter:
            continue
        for ts_dir in sorted(qdir.iterdir()):
            if not ts_dir.is_dir():
                continue
            if not _TIMESTAMP_RE.match(ts_dir.name):
                continue
            out.append((qid, ts_dir))

    out.sort(key=lambda t: (t[0], t[1].name))
    return out


# ---------------------------------------------------------------------------
# URA -> AggregatorInput
# ---------------------------------------------------------------------------


def _ura_reg_result_to_reranked(u_res) -> RerankedResult:
    """Rebuild a RerankedResult from a URAResult (regulations domain)."""
    meta = u_res.metadata or {}
    source_type = u_res.source_type if u_res.source_type in ("article", "section") else "article"

    # db_id from ref_id prefix "reg:{uuid}"
    db_id = ""
    rid = u_res.ref_id or ""
    if rid.startswith("reg:"):
        db_id = rid[4:]

    article_num = meta.get("article_num")
    if article_num is not None and not isinstance(article_num, str):
        article_num = str(article_num)

    return RerankedResult(
        source_type=source_type,  # type: ignore[arg-type]
        title=u_res.title,
        content=u_res.content,
        article_num=article_num,
        article_context=meta.get("article_context", "") or "",
        references_content=meta.get("references_content", "") or "",
        regulation_title=meta.get("regulation_title", "") or "",
        section_title=meta.get("section_title", "") or "",
        section_summary=meta.get("section_summary", "") or "",
        relevance=u_res.relevance if u_res.relevance in ("high", "medium") else "medium",  # type: ignore[arg-type]
        reasoning=u_res.reasoning or "",
        db_id=db_id,
    )


def load_aggregator_input_from_ura(
    ura: "UnifiedRetrievalArtifact",
    prompt_key: str = "prompt_1",
) -> AggregatorInput:
    """Convert a UnifiedRetrievalArtifact into AggregatorInput.

    Splits URA results back into:
      - sub_queries: list[RerankerQueryResult] -- regulations domain only.
        Each reg URAResult is placed in every sub-query listed in its
        appears_in_sub_queries field, so the aggregator's preprocessor sees
        the same sub-query -> results distribution it would get from a live
        run.
      - compliance_results: ComplianceURASlice -- compliance domain only.
    """
    from agents.deep_search_v3.compliance_search.models import ComplianceURASlice

    reg_results = [r for r in (ura.results or []) if r.domain == "regulations"]
    compliance_results = [r for r in (ura.results or []) if r.domain == "compliance"]

    # Group reg results by sub-query index (from appears_in_sub_queries).
    sq_meta_by_index: dict[int, dict] = {}
    for sq in ura.sub_queries or []:
        idx = sq.get("index")
        if idx is None:
            continue
        sq_meta_by_index[int(idx)] = sq

    sq_results: dict[int, list[RerankedResult]] = {}
    for u_res in reg_results:
        reranked = _ura_reg_result_to_reranked(u_res)
        targets = u_res.appears_in_sub_queries or [0]
        for sq_idx in targets:
            sq_results.setdefault(int(sq_idx), []).append(reranked)

    # Preserve sub-query order from the URA's sub_queries list when present,
    # else fall back to ascending index order.
    if sq_meta_by_index:
        ordered_indices = [
            i for i in sorted(sq_meta_by_index.keys())
            if sq_meta_by_index[i].get("domain", "regulations") != "compliance"
        ]
    else:
        ordered_indices = sorted(sq_results.keys())

    # Include indices that only appear via results (defensive).
    for i in sorted(sq_results.keys()):
        if i not in ordered_indices:
            ordered_indices.append(i)

    sub_queries: list[RerankerQueryResult] = []
    for sq_idx in ordered_indices:
        meta = sq_meta_by_index.get(sq_idx, {})
        sub_queries.append(
            RerankerQueryResult(
                query=meta.get("query", ""),
                rationale=meta.get("rationale", ""),
                sufficient=bool(meta.get("sufficient", True)),
                results=list(sq_results.get(sq_idx, [])),
                dropped_count=int(meta.get("dropped_count", 0)),
                summary_note=meta.get("summary_note", "") or "",
            )
        )

    compliance_slice = None
    if compliance_results:
        compliance_slice = ComplianceURASlice(
            results=[
                {
                    "ref_id": r.ref_id,
                    "domain": "compliance",
                    "source_type": r.source_type,
                    "title": r.title,
                    "content": r.content,
                    "metadata": dict(r.metadata or {}),
                    "relevance": r.relevance,
                    "reasoning": r.reasoning,
                    "appears_in_sub_queries": list(r.appears_in_sub_queries or []),
                    "rrf_max": r.rrf_max,
                    "triggered_by_ref_ids": list(r.triggered_by_ref_ids or []),
                    "cross_references": list(r.cross_references or []),
                }
                for r in compliance_results
            ],
            queries_used=[
                sq.get("query", "")
                for sq in ura.sub_queries or []
                if sq.get("domain") == "compliance"
            ],
        )

    domain: str = "regulations"
    if reg_results and compliance_results:
        domain = "multi"
    elif compliance_results and not reg_results:
        domain = "compliance"

    return AggregatorInput(
        original_query=ura.original_query,
        sub_queries=sub_queries,
        domain=domain,  # type: ignore[arg-type]
        session_id=ura.log_id,
        query_id=ura.query_id,
        log_id=ura.log_id,
        prompt_key=prompt_key,
        compliance_results=compliance_slice,
    )
