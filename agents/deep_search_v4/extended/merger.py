"""«بحث موسّع» — merge the searches' reports into ONE answer (plan §4.6, §4.9 c; D4, D14, D19).

Port of the experiments' merger (``run_full_trace.py`` / ``run_angle_merge.py``):

- **code** builds ONE shared reference list from every report (deduped by ``ref_id``,
  else by type + source + title), each reference WITH its excerpt (D19 — the 11-21
  merges mostly lacked them), and rewrites every report's ``[n]`` to that list;
- the model (slot ``extended_merger`` = the aggregator's policy) gets the user's message,
  the conversation, the planner's ``understanding``, every document WHOLE as the searches
  read it (marked text + its summary, labelled analysis), the shared references and the
  reports — each with its confidence, its gaps and its angle cards (§4.9 b layout);
- a validator rejects any citation outside the shared list (``ModelRetry``); code then
  renumbers the answer's citations 1..k in order of first use and keeps only those
  references; the merged confidence never exceeds the highest report's (§4.6).

Rules added for the build (§4.6, §6): never call a document signed when its signature
fields are blank; a cover email is a cover email, not the notice it covers. The list of
the reports the answer was built from is written by code at publish time (D14).

وضع السرية: the reports and the references are already in the encoded space (the
searches' aggregator output / public corpus text); the view's text (documents, titles,
summaries, angle cards) and the planner's understanding are encoded here with the turn
codec. The output stays encoded — ``publish`` decodes before storing, like today's
publisher. Ledger row ``deep_search.extended_merger``. Raises on failure (the caller
falls back to the first usable report, :func:`single_report_answer`).
"""
from __future__ import annotations

import html
import logging
import re
import time
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any, Literal

from pydantic import BaseModel, Field
from pydantic_ai import Agent, ModelRetry, RunContext, TextOutput
from pydantic_ai.usage import RunUsage, UsageLimits

from agents.utils.structured_output import make_json_salvager
from agents.utils.tracking import track_stage
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

from .models import ExtendedPlan, ExtendedSearchReport, MergedAnswer, PlannerView, Resolution
from .runner import WHOLE_FILE_PER_DOC_CAP, WHOLE_FILE_TOTAL_CAP, whole_file_items

logger = logging.getLogger(__name__)

MODEL_SLOT = "extended_merger"
LEDGER_STAGE = "deep_search.extended_merger"
MERGER_LIMITS = UsageLimits(output_tokens_limit=OUTPUT_TOKENS_CAP, request_limit=4)

_CITE = re.compile(r"\[(\d+(?:\s*[,،]\s*\d+)*)\]")
_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}

_NO_TEXT_NOTE = "(لم يُتح نص هذا المستند — اعتمد على الملخص)"
_OVER_TOTAL_NOTE = "(لم يُرفق النص لتجاوز حد السياق — اعتمد على الملخص)"


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


def _attr(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


# --------------------------------------------------------------------------- LLM output


class MergerLLMOutput(BaseModel):
    synthesis_md: str = Field(..., description="The merged answer, Arabic markdown with [n] citations from <references>.")
    used_refs: list[int] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    confidence: Literal["high", "medium", "low"]
    resolved: list[Resolution] = Field(default_factory=list)


_RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(synthesis_md, used_refs, gaps, confidence, resolved) only — no text outside the JSON. "
    "The `synthesis_md` value must be in Arabic."
)


# --------------------------------------------------------------------------- prompt


def _strengths_block() -> str:
    """§4.9 (c) — appended after the 11-21 rules."""
    return """\
## Strengths and weaknesses (نقاط القوة ونقاط الضعف)
Each `<report>` carries, in `<angles>`, the angles it researched: `strengthen` and `attack` angles are the user's strengths; `answer` angles are the opponent's strong points the user must answer; each angle states what weakens it. The angles are machine analysis — never quote them as the file.
1. When the user's question is about a position — theirs, the opponent's, or the case's chances — organise the answer around it: the user's strengths, each with its legal basis and the sections it rests on; then the weaknesses and the opponent's strong points, each followed by its answer — or marked as open, with what would decide it. When the user asked for something else (a memo, a reply, a deadline), follow that shape and keep the strengths and weaknesses inside it.
2. Keep every strength and every weakness that any report established, with its citations. Drop one only when the file's text or the references show it is wrong, and record that in `resolved`. Never drop a weakness because another report did not mention it.
3. Where reports disagree on a strength, a weakness or its answer, settle it against `<case_documents>` (the file's own text) and `<references>` — never by majority and never by an angle's `strength` label — and record it in `resolved`.
4. A weakness no report answered stays in the answer, marked open, and goes to `gaps`."""


def merger_system_prompt() -> str:
    """The merger's instructions (English; the answer is Arabic)."""
    from agents.deep_search_v4.aggregator.prompts import _SHARED_ROLE_AR

    return f"""{_SHARED_ROLE_AR}

## This task: merge parallel research reports into ONE answer

The user's matter was researched as several searches run at the same time, each on some angles of it (an argument that strengthens the user's position, one that attacks the other side, or the other side's strong point the user must answer). Each search read every document the user sent, whole. You receive their reports in `<reports>`. Every report's citations were renumbered by code into ONE shared list, `<references>`: the same `[n]` means the same source in every report, and each reference carries its `<excerpt>` so you can check what it says. You write the single answer the user receives instead of the separate reports.

1. **Organize by the user's question, not by report.** Never write "the first report says…". Open with `## الخلاصة` — a direct answer in a few sentences. Then sections shaped by what the user asked (a request about their position is answered through their strengths, the other side's likely defences with the answer to each, the evidence they hold and what is missing, and the next steps; a request for a memo or a reply is answered with the arguments it should make, in order). Do not copy the reports' headings.
2. **Keep everything that matters.** Every distinct finding, legal rule with its `[n]`, issue found in the user's documents, defence and answer, and practical step that any report gives must survive. Remove only repetition: when reports make the same point, write it once and keep the citations that support it (at most 4 numbers per tag).
3. **Settle contradictions — never silently.** Where reports disagree (how far a rule reaches, what weight a piece of evidence carries, whether a requirement is met, a deadline, an amount or a date), check the user's documents in `<case_documents>` and the sources in `<references>`. Adopt the position they support and say why in one sentence. If they do not settle it, present both positions with their basis and say what would decide it. Record every disagreement in `resolved`.
4. **Facts about the user's documents come from `<case_documents>` only.** The `<text>` of each is the user's own document word for word (the lines 〔D…·S… · …〕 are section markers added by code, not part of the documents): quote from it exactly. Its `<summary>` is an automated analysis — a lead to check against the text, never the documents' words, never quoted as them. When a report states a fact about the documents that the text contradicts, follow the text and correct it.
5. **Read each document for what it is.** Never call a document signed, approved or in force when its signature or approval fields are blank, or when it says it takes effect only once signed — say it is unsigned. A cover email or covering letter is a cover email: it is not the notice, contract or decision it covers, and what it says about that document is not that document's text.
6. **Nothing new.** Add no source, article, ruling, amount or date that is in neither the reports nor the case documents. Do not cite a reference for a claim the reports did not support with it.
7. **Length:** complete, without repetition — normally shorter than the reports put together.
8. `gaps`: merge the reports' gaps (`<gaps>` inside each report) without repeats; drop a gap another report closed. `confidence`: judge the merged answer against the user's question as a whole — never higher than the highest `confidence` of the reports you merged.
9. Do not list the reports you used and do not name them — code adds that list after the answer.

{_strengths_block()}

## Citation rules (binding)
- Cite with the shared numbers from `<references>` only, in square brackets after the sentence they support: `[3]`, `[3,7]`. Western digits only. At most 4 numbers in one tag.
- `[n]` is reserved for references. Article numbers are written in prose: «المادة 471».
- Do not add a references section or a legal disclaimer to `synthesis_md` — both are added by code.

## Output schema
Return one JSON object: {{"synthesis_md": "…", "used_refs": [1, 3], "gaps": ["…"], "confidence": "high | medium | low", "resolved": [{{"topic": "…", "reports": [1, 2], "decision": "…"}}]}}
`resolved[].reports` are the `n` of the `<report>`s that disagreed. All prose in Arabic.
"""


# --------------------------------------------------------------------------- shared references (code)


def _ref_dict(ref: Any) -> dict[str, Any]:
    if isinstance(ref, dict):
        return dict(ref)
    try:
        return ref.model_dump(mode="json")
    except Exception:  # noqa: BLE001
        return dict(getattr(ref, "__dict__", {}) or {})


def _ref_key(ref: dict[str, Any]) -> tuple:
    if ref.get("ref_id"):
        return ("id", str(ref["ref_id"]))
    return ("t", str(ref.get("source_type") or ""), str(ref.get("regulation_title") or ""), str(ref.get("title") or ""))


def cited_numbers(text: str) -> list[int]:
    """Every ``[n]`` / ``[n,m]`` number in ``text``, in order of first use, once each."""
    seen: list[int] = []
    for m in _CITE.finditer(text or ""):
        for x in re.split(r"\s*[,،]\s*", m.group(1)):
            n = int(x)
            if n not in seen:
                seen.append(n)
    return seen


def remap_citations(text: str, mapping: dict[int, int]) -> str:
    """Rewrite every citation tag through ``mapping``; unknown numbers are dropped, a tag
    left empty is removed."""

    def sub(m: re.Match) -> str:
        new: list[int] = []
        for x in (int(v) for v in re.split(r"\s*[,،]\s*", m.group(1))):
            if x in mapping and mapping[x] not in new:
                new.append(mapping[x])
        return f"[{','.join(map(str, new))}]" if new else ""

    return _CITE.sub(sub, text or "")


def build_shared_references(
    reports: Sequence[ExtendedSearchReport],
) -> tuple[list[dict[str, Any]], list[dict[int, int]]]:
    """ONE reference list over ``reports`` (plan order) + each report's ``old n → shared n``.

    Deduped by ``ref_id`` (else type + source + title); the first report to carry a
    reference gives its entry — and a later one fills an empty excerpt (D19)."""
    shared: list[dict[str, Any]] = []
    index: dict[tuple, int] = {}
    maps: list[dict[int, int]] = []
    for report in reports:
        mapping: dict[int, int] = {}
        for raw in getattr(report.agg_output, "references", None) or []:
            ref = _ref_dict(raw)
            try:
                old = int(ref.get("n"))
            except (TypeError, ValueError):
                continue
            key = _ref_key(ref)
            if key not in index:
                shared.append(ref)
                index[key] = len(shared)
            elif not shared[index[key] - 1].get("snippet") and ref.get("snippet"):
                shared[index[key] - 1]["snippet"] = ref["snippet"]
            mapping[old] = index[key]
        maps.append(mapping)
    return shared, maps


def renumber(text: str, shared: Sequence[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Renumber ``text``'s citations 1..k by first use; the cited references, renumbered."""
    order = [n for n in cited_numbers(text) if 1 <= n <= len(shared)]
    fmap = {old: new for new, old in enumerate(order, 1)}
    return remap_citations(text, fmap), [{**shared[old - 1], "n": fmap[old]} for old in order]


def clamp_confidence(confidence: str, reports: Sequence[ExtendedSearchReport]) -> str:
    """``confidence`` capped at the highest report confidence (§4.6)."""
    ranks = [
        _CONFIDENCE_RANK[c]
        for c in (str(getattr(r.agg_output, "confidence", "") or "") for r in reports)
        if c in _CONFIDENCE_RANK
    ]
    own = _CONFIDENCE_RANK.get(str(confidence or ""), 1)
    if ranks:
        own = min(own, max(ranks))
    return {v: k for k, v in _CONFIDENCE_RANK.items()}[own]


def _dedupe(items: Sequence[str]) -> list[str]:
    out: list[str] = []
    for s in items:
        t = str(s or "").strip()
        if t and t not in out:
            out.append(t)
    return out


def _report_gaps(report: ExtendedSearchReport) -> list[str]:
    from agents.deep_search_v4.aggregator.runner import _MODEL_FAILURE_GAP_AR

    return [
        g for g in _dedupe(getattr(report.agg_output, "gaps", None) or [])
        if g != "no_references_after_reranker" and g != _MODEL_FAILURE_GAP_AR
    ]


def single_report_answer(report: ExtendedSearchReport) -> MergedAnswer:
    """One report as the answer — when only one search succeeded (no merger) or the
    merger failed. Its own citations and references, unchanged."""
    agg = report.agg_output
    return MergedAnswer(
        synthesis_md=(getattr(agg, "synthesis_md", "") or "").strip(),
        references=[_ref_dict(r) for r in getattr(agg, "references", None) or []],
        gaps=_report_gaps(report),
        confidence=str(getattr(agg, "confidence", "medium") or "medium")
        if str(getattr(agg, "confidence", "")) in _CONFIDENCE_RANK else "medium",
        resolved=[],
        report_titles=[report.request.title],
    )


# --------------------------------------------------------------------------- the merger's input


def _encode(text: str) -> str:
    from .runner import _encode as encode

    return encode(text)


def angle_lines(report: ExtendedSearchReport, view: PlannerView) -> list[str]:
    """The report's angles, one line each — the §4.9 b line the search's brief carried
    (``brief.angle_line``; view text, unencoded)."""
    from .brief import angle_line

    by_id = {a.id: a for a in view.angles}
    return [angle_line(by_id[aid]) for aid in report.request.angles if aid in by_id]


def _documents_xml(view: PlannerView) -> str:
    """Every document as the searches read it: encoded marked text (capped like the
    searches: per document / in total) + its encoded summary."""
    items = {s.item_id: s for s in whole_file_items(view)}
    used = 0
    blocks: list[str] = []
    for doc in view.documents:
        snap = items.get(str(doc.item_id))
        text = (snap.content_md if snap is not None else "") or ""
        summary = (snap.summary if snap is not None else "") or ""
        title = (snap.title if snap is not None else "") or ""
        if not text.strip():
            body = _NO_TEXT_NOTE
        else:
            cut = text[:WHOLE_FILE_PER_DOC_CAP]
            if used + len(cut) > WHOLE_FILE_TOTAL_CAP:
                body = _OVER_TOTAL_NOTE
            else:
                used += len(cut)
                body = cut
                if len(text) > len(cut):
                    body += f"\n[انتهى المعروض: {len(cut)} حرف من أصل {len(text)} — بقية الوثيقة لم تُعرض]"
        blocks.append(
            f'  <case_document id="{_attr(doc.id)}" doc_type="{_attr(doc.doc_type)}" title="{_attr(title)}">\n'
            f"    <summary>{_esc(summary)}</summary>\n"
            f"    <text>{_esc(body)}</text>\n"
            "  </case_document>"
        )
    return "<case_documents>\n" + "\n".join(blocks) + "\n</case_documents>"


def reference_xml(n: int, ref: dict[str, Any]) -> str:
    """One shared reference WITH its excerpt (D19)."""
    bits = [
        f'<reference n="{n}" type="{_attr(ref.get("source_type", ""))}">',
        f"  <title>{_esc(ref.get('title', ''))}</title>",
    ]
    if ref.get("regulation_title") and ref["regulation_title"] != ref.get("title"):
        bits.append(f"  <source>{_esc(ref['regulation_title'])}</source>")
    if ref.get("case_number"):
        bits.append(f"  <case_number>{_esc(ref['case_number'])}</case_number>")
    if ref.get("hijri_year"):
        bits.append(f"  <year>{_esc(ref['hijri_year'])}</year>")
    excerpt = ref.get("snippet") or ref.get("decision_line") or ""
    if excerpt:
        bits.append(f"  <excerpt>{_esc(excerpt)}</excerpt>")
    bits.append("</reference>")
    return "\n".join(bits)


def build_merger_input(
    reports: Sequence[ExtendedSearchReport],
    *,
    query: str,
    recent_messages: Any,
    plan: ExtendedPlan,
    view: PlannerView,
    shared: Sequence[dict[str, Any]],
    maps: Sequence[dict[int, int]],
) -> str:
    """The merger's user message. ``reports`` = the usable reports, plan order;
    ``shared`` / ``maps`` from :func:`build_shared_references` over them."""
    from .view import recent_pairs

    conv = "\n".join(
        f'  <message role="{_attr(role)}">{_esc(content)}</message>'
        for role, content in recent_pairs(recent_messages, query=query)
    ) or "  (none)"
    refs = "\n".join(reference_xml(i, r) for i, r in enumerate(shared, 1))
    rep_blocks: list[str] = []
    for i, (report, mapping) in enumerate(zip(reports, maps), 1):
        agg = report.agg_output
        body = remap_citations(getattr(agg, "synthesis_md", "") or "", mapping)
        angles = "\n".join(_encode(line) for line in angle_lines(report, view))
        rep_blocks.append(
            f'  <report n="{i}" title="{_attr(_encode(report.request.title))}" '
            f'confidence="{_attr(getattr(agg, "confidence", "") or "")}">\n'
            f"    <angles>\n{_esc(angles)}\n    </angles>\n"
            f"    <body>\n{_esc(body)}\n    </body>\n"
            f"    <gaps>{_esc(' | '.join(_report_gaps(report)))}</gaps>\n"
            "  </report>"
        )
    return (
        f"<query>{_esc(query)}</query>\n\n"
        f"<conversation>\n{conv}\n</conversation>\n\n"
        f"<planner_understanding>{_esc(_encode(plan.understanding))}</planner_understanding>\n\n"
        f"{_documents_xml(view)}\n\n"
        f"<references>\n{refs}\n</references>\n\n"
        "<reports>\n" + "\n".join(rep_blocks) + "\n</reports>"
    )


# --------------------------------------------------------------------------- the agent


def _merger_model() -> Any:
    from agents.utils.agent_models import get_agent_model

    return get_agent_model(MODEL_SLOT)


def create_merger_agent(n_shared: int, model: Any = None) -> Agent[None, MergerLLMOutput]:
    """The merger agent; its validator rejects citations outside 1..``n_shared``."""
    agent: Agent[None, MergerLLMOutput] = Agent(
        model if model is not None else _merger_model(),
        name="extended_merger",
        output_type=[MergerLLMOutput, TextOutput(make_json_salvager(MergerLLMOutput, retry_msg=_RETRY_MSG))],
        instructions=merger_system_prompt(),
        retries=2,
        output_retries=2,
    )

    @agent.output_validator
    def _known_refs(ctx: RunContext[None], out: MergerLLMOutput) -> MergerLLMOutput:
        bad = sorted({n for n in cited_numbers(out.synthesis_md) if not 1 <= n <= n_shared})
        if bad:
            raise ModelRetry(
                f"Citations {bad} are not in <references> (1..{n_shared}). Use only those numbers."
            )
        if not out.synthesis_md.strip():
            raise ModelRetry("`synthesis_md` is empty. Write the merged answer in Arabic.")
        return out

    return agent


def _record(span: Any, result: Any) -> None:
    try:
        span.record_run(result, slot=MODEL_SLOT)
    except Exception:  # noqa: BLE001 - the LUNA_TRACK_DISABLE handle has no record_run
        pass


def _persist_fakes(supabase: Any, user_id: str | None) -> None:
    if supabase is None or not user_id:
        return
    try:
        from backend.app.services.masking_service import active_codec, persist_new_mappings

        persist_new_mappings(supabase, user_id, active_codec())
    except Exception:  # noqa: BLE001
        logger.warning("extended_merger: persisting new masking fakes failed", exc_info=True)


def _strip_thinking(text: str) -> str:
    try:
        from agents.deep_search_v4.aggregator.postvalidator import strip_thinking_block

        return strip_thinking_block(text or "").strip()
    except Exception:  # noqa: BLE001
        return (text or "").strip()


async def merge_reports(
    reports: Sequence[ExtendedSearchReport],
    *,
    query: str,
    recent_messages: Any,
    plan: ExtendedPlan,
    view: PlannerView,
    supabase: Any = None,
    user_id: str | None = None,
    conversation_id: str | None = None,
) -> MergedAnswer:
    """Merge the usable reports (``ok``, plan order) into one answer.

    ``query`` — the message the searches ran on (encoded, as they got it);
    ``recent_messages`` — the turn's encoded ``ChatMessageSnapshot`` list (or
    ``{"role", "content"}`` dicts). ``supabase`` / ``user_id`` (optional): persist the
    masking fakes minted for the merger's input before the model runs.
    Raises ``ValueError`` with fewer than 2 usable reports (the wiring publishes the
    single report instead — :func:`single_report_answer`) and re-raises a model failure.
    """
    usable = sorted((r for r in reports if r.ok and r.agg_output is not None), key=lambda r: r.index)
    if len(usable) < 2:
        raise ValueError(f"extended_merger: needs at least 2 usable reports, got {len(usable)}")

    shared, maps = build_shared_references(usable)
    message = build_merger_input(
        usable, query=query, recent_messages=recent_messages, plan=plan, view=view,
        shared=shared, maps=maps,
    )
    _persist_fakes(supabase, user_id)
    n_shared = len(shared)
    agent = create_merger_agent(n_shared)

    t0 = time.perf_counter()
    spent = RunUsage()
    with track_stage(
        LEDGER_STAGE,
        conversation_id=conversation_id or None,
        agent_family="deep_search",
        reports=len(usable),
        shared_refs=n_shared,
        refs_with_excerpt=sum(1 for r in shared if r.get("snippet") or r.get("decision_line")),
        input_chars=len(message),
    ) as span:
        try:
            result = await agent.run(message, usage_limits=MERGER_LIMITS, usage=spent)
        except BaseException:
            _record(span, SimpleNamespace(usage=lambda: spent))  # a failed run still bills
            raise
        _record(span, result)
        out: MergerLLMOutput = result.output
        text, refs = renumber(_strip_thinking(out.synthesis_md), shared)
        confidence = clamp_confidence(out.confidence, usable)
        resolved = [
            Resolution(
                topic=r.topic,
                reports=[n for n in r.reports if 1 <= n <= len(usable)],
                decision=r.decision,
            )
            for r in out.resolved
            if (r.topic or "").strip() or (r.decision or "").strip()
        ]
        try:
            span.set(
                seconds=round(time.perf_counter() - t0, 1),
                references=len(refs),
                confidence=confidence,
                confidence_model=out.confidence,
                resolved=len(resolved),
                synthesis_chars=len(text),
            )
        except Exception:  # noqa: BLE001
            pass

    logger.info(
        "extended_merger: %d reports → %d chars, %d/%d references, confidence=%s (model %s), %d resolved",
        len(usable), len(text), len(refs), n_shared, confidence, out.confidence, len(resolved),
    )
    return MergedAnswer(
        synthesis_md=text,
        references=refs,
        gaps=_dedupe(out.gaps),
        confidence=confidence,  # type: ignore[arg-type]
        resolved=resolved,
        report_titles=[r.request.title for r in usable],
    )


__all__ = [
    "LEDGER_STAGE",
    "MODEL_SLOT",
    "MergerLLMOutput",
    "angle_lines",
    "build_merger_input",
    "build_shared_references",
    "cited_numbers",
    "clamp_confidence",
    "create_merger_agent",
    "merge_reports",
    "merger_system_prompt",
    "reference_xml",
    "remap_citations",
    "renumber",
    "single_report_answer",
]
