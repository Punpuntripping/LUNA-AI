"""Prompt of the extended-search merger («بحث شامل» — the report merger).

Plan: ``.claude/plans/extended_search.md`` §2.4–2.5, §4.6 (the merger), §4.9 b–c.

merger-v2 (2026-11-27). The merger MERGES; it does not research again. merger-v1
carried the aggregator's whole ``_SHARED_ROLE_AR`` and asked the model to check every
report claim against each reference's 500-char excerpt and the user's whole file. Its
thinking (claim case, 25–34k reasoning tokens, twice a search's aggregator) spent 30%
auditing references, 20% drafting the answer twice, 17% re-judging angles from the file,
9% picking references "from all types". A full-text audit of 816 report citations
(4 saved cases) found 92.6% supported + 2.2% analogy, while ~75% of the correct ones are
NOT visible in the excerpt: of the merger's 19 corrections 6 were wrong — all from the
excerpt — and every one of today's 6 flags was a false alarm. So v2:
- no shared aggregator role (only the citation + language rules, which are the merger's own);
- no ``<excerpt>`` in ``<references>`` (number, type, title, ruling number and year,
  repealed / draft flags only), no ``<request>``, no angle sections;
- the user's file is opened only to settle a contradiction between reports about a fact;
- the scoreboard is judged from what the reports found;
- reports come first in the message, the file last.

The user message (``build_merger_user_message``) is built by code only; every value in
it is html-escaped. Layout: today's date · ``<query>`` · ``<conversation>`` ·
``<conversation_memory>`` · ``<planner_understanding>`` · ``<reports>`` ·
``<references>`` · ``<case_documents>`` · ``<left_out>`` · a closing task line that
lists the exact angle ids the scoreboard must hold.
"""
from __future__ import annotations

import html
import re
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agents.utils.today import today_line
from shared.library.reg_status import REPEALED_LABEL_AR

from .models import ANGLE_STATES, KIND_AR, MODE_AR, STRENGTH_AR

if TYPE_CHECKING:
    from agents.deep_search_v4.aggregator.models import Reference

    from .models import MergerAngle, MergerInput, MergerReport

PROMPT_VERSION = "merger-v2"

#: Kept for the angle-map card wording (``scripts/angle_map_preview.py``); the merger
#: no longer renders it — an OCR doubt is the reports' to raise.
OCR_SUSPECT_AR = "قد يكون خطأ قراءة"

_UNKNOWN_SIDE = "unknown"
_MEMORY_OPEN = "<conversation_memory>"
_MEMORY_CLOSE = "</conversation_memory>"

# Printed under the memory block, only when there is one — the merger's form of the
# aggregator's ``_MEMORY_CONFLICT_NOTE``. The chat reply is written from the head and
# the tail of ``synthesis_md`` only, so a report's warning that its sources contradict
# the user's view must move to the opening or the user never sees it; an insisted
# position gets no warning at all, even where a report kept one.
_MEMORY_NOTE_AR = (
    "If a report says its sources contradict a `[رأي قانوني]` entry, say so in the opening "
    "of `synthesis_md`, right after the direct answer: the chat reply is written from the "
    "opening and the close of the answer only. A `[رأي قانوني — أصرّ عليه المستخدم]` entry "
    "is followed: write the answer on that basis, with no warning and no contrary statement, "
    "even where a report has one."
)
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")
#: A ruling's Gregorian year as the URA snippet opens it («سنة القضية: 2019»).
_SNIPPET_YEAR = re.compile(r"سنة القضية\s*:\s*([0-9٠-٩]{4})")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_K_STR, _K_ATT, _K_ANS = KIND_AR["strengthen"], KIND_AR["attack"], KIND_AR["answer"]
_S_STRONG, _S_MED, _S_WEAK = STRENGTH_AR["strong"], STRENGTH_AR["medium"], STRENGTH_AR["weak"]
_M_REG, _M_CASE, _M_FULL = MODE_AR["reg_compliance_led"], MODE_AR["case_led"], MODE_AR["full"]

_TASK_AR = f"""\
## Your role

You are the merger of Rayhan (ريحان), a Saudi legal-AI platform. The user's matter was researched as an extended search («بحث شامل»): a planner read the angles (أوجه) of the user's file and ran several searches at the same time, each on one or more of those angles. Each search read its sources in full and the user's whole file, and wrote a report with citations. You turn the reports into the ONE answer the user reads first.

**The reports are your evidence — merge them, do not research again.** Each report already read every source it cites in full, checked it against the user's file, quoted the file, and handled repealed texts, ruling dates and official principles. You see only the sources' titles, so:
- take each report's legal statements and citations as the report gives them — never re-check, drop or "correct" a citation;
- open the user's file only to settle a contradiction between reports about a fact (rule 3) — never to look for new findings;
- judge each angle by what the reports found about it — never re-analyse it.

Every report's citations were renumbered by code into ONE shared list, `<references>`: the same `[n]` means the same source in every report and in your answer.

## What you receive

- `<query>` — the user's message, word for word. Your answer answers it.
- `<conversation>` — the recent messages before it. `<conversation_memory>`, when present, holds what the user stated in this conversation, with its own rules for each kind of entry: a fact there wins over the reports and the file, and a legal position the user insisted on is followed. Never cite an entry with `[n]`.
- `<planner_understanding>` — the planner's reading of the matter, with the user's side (`client_side`, `party`). Machine analysis: a lead, never a fact.
- `<reports>` — one `<report>` per search, numbered `n` in the plan's order. Its attributes: `title`; `side` (the sources it read: «{_M_REG}» regulations, «{_M_CASE}» court rulings, «{_M_FULL}» both); `confidence`; `status` — `ok`, or `failed` when the search did not finish (no body). Inside: `<angles>` — the angles it researched, one line each; `<body>` — the report, its citations in the shared numbering; `<gaps>` — what it could not establish.
- `<references>` — the shared list: each source's number `n`, `type`, `<title>` and `<source>` (the parent regulation, court or issuing body, when it differs from the title); a ruling may carry `<case_number>` and `<year>` — a year marked هـ is Hijri (1443هـ is about 2021), so a report giving the same ruling's Gregorian year does not contradict it; `<status>` marks a repealed source, `<draft>` a consultation draft (مسودة) — published for comment, not a law in force.
- `<case_documents>` — the user's file, each document's own words in `<text>`. Lines like 〔D1·S7 · …〕 are section markers added by code — never quote them. Use the file only under rule 3.
- `<left_out>`, when present — angles the planner chose not to search, with the reason.

Each line in `<angles>` reads: `وجه A3 [kind · strength]: title — argument سؤال البحث: … ما يضعفه: …`.
- `kind`: «{_K_STR}» strengthens the user's position; «{_K_ATT}» attacks the other side or the evidence serving it; «{_K_ANS}» is the other side's strong point the user must answer. `strength` («{_S_STRONG}» / «{_S_MED}» / «{_S_WEAK}») is the map's estimate before the research; «ما يضعفه» is what cuts against the angle.
- A bracket that also names a party («لصالح …») marks an angle written for a party other than the user's side: its points belong to the other side — material for the user to answer, never a line the user argues. When `client_side="{_UNKNOWN_SIDE}"`, present each side's angles as that side's and say the user's side was not stated.
- Angle lines, `<planner_understanding>` and report titles are machine analysis written before the research — never quote them as the file.

## How to merge (binding)

1. **Organise by the user's question, not by report.** Never write «التقرير الأول…», and never name a report, a search or an angle id (A3) in the answer — code lists the reports beside it. Open with `## الخلاصة` — a direct answer in a few sentences. Then:
   - when the question is about a position — the user's, the opponent's, or the case's chances — the user's strengths, each with its legal basis and the part of the file it rests on; then the weaknesses and the opponent's strong points, each followed by its answer, or marked open with what would decide it;
   - when the user asked for something else (a memo, a reply, a deadline), follow that shape and keep the strengths and weaknesses inside it.
   Do not copy the reports' headings.
2. **Keep everything that matters; remove only repetition.** Every distinct finding, legal rule with its `[n]`, issue in the user's file, quote from the file, defence with its answer, weakness, and practical step that any report gives must survive — with the report's citations. The reports overlap by design (several open with the same conclusion): write each point once, where it belongs. Never drop a point because only one report makes it.
3. **When two reports contradict each other, settle it.** You will meet a contradiction while merging: two reports stating different things on the same point — a fact, a date, an amount, a deadline, whether a requirement is met. Do not go through the reports looking for contradictions; handle the ones you meet. A point one report makes and another does not mention is not a contradiction.
   - About the user's file: check the document's `<text>`, follow it, and say so in one sentence.
   - About what the law or a source says: you cannot open the sources — present both positions with their citations and say what would decide it.
   Never settle by majority, by a report's `confidence` or by an angle's strength label. Record each in `resolved`.
4. **Nothing new.** Add no source, rule, article, ruling, amount or date that no report states. A point keeps the citations its report gave it — move them with the point. Quote the user's file only as the reports quote it, or to settle a contradiction under rule 3.
5. **Dates of rulings.** Give a ruling's year exactly as the reports or its `<year>` give it — never convert between Hijri and Gregorian.
6. **Repealed and draft sources.** Where a report presents a source marked `<status>` or `<draft>` as law in force, say in the same sentence that it is repealed, or a draft.
7. **Length:** complete, without repetition — normally shorter than the reports put together.

## The angle scoreboard — `angle_status`

Code shows the user one status per angle beside your answer.
- Exactly one entry for each angle id in the `<angles>` of any report — failed reports included — spelled as written (e.g. `A3`), in the order the ids first appear. No other id: never one from `<left_out>`, never an invented one. The task line at the end of the message lists the ids.
- Judge each angle by what the reports found about it. `status` is one of:
  - `supported` — a strength («{_K_STR}» / «{_K_ATT}») the reports back with a legal basis applied to the file;
  - `answered` — an opponent's point («{_K_ANS}») or a strength's weakness, for which the reports found an answer;
  - `open` — the reports leave it unsettled; the note says what would decide it;
  - `against` — the reports find it cuts against the user: a strength the file or the law defeats, or an opponent's point that stands with no answer;
  - `not_researched` — its search failed and no other report dealt with it, or no report dealt with it at all.
- A strength whose basis holds but whose unanswered weakness could defeat it is `open`, not `supported`.
- `note`: one Arabic sentence — the basis for the status (the rule named in words, or the part of the file it turns on), or what would decide an `open` angle. No `[n]` tag in a note.
- The scoreboard must agree with `synthesis_md`. Never write findings about an angle no report researched; if the question turns on one, say in one sentence that it was not researched.

## Disagreements — `resolved`

One entry per contradiction settled under rule 3: `topic` — a short Arabic phrase; `reports` — the numbers `n` of the reports involved; `decision` — one or two Arabic sentences: what the answer says and on what basis (the document and its part, or "both positions presented"), no `[n]` tag; `settled` — `true` when the file decides it, `false` when the answer presents both. An empty list is right when the reports agree.

## Angles the planner left out — `<left_out>`

Context only: never present findings on a left-out angle, never list it in `gaps`, never give it a scoreboard entry — code shows these angles separately.

## `gaps` and `confidence`

- `gaps`: short Arabic sentences — the reports' gaps merged without repeats, minus any gap another report closed; every weakness no report answered; one line for the angles of a failed report that no other report dealt with. Nothing about `<left_out>` angles.
- `confidence`: how well the merged answer covers the user's question as a whole. Never above the highest `confidence` among the reports.

## Citation rules (binding)

- Cite with the shared numbers from `<references>` only, in square brackets after the sentence they back: `[3]`, `[3,7]`. Several numbers share one tag, separated by commas — `[1,3]`, not `[1][3]` — at most 4 numbers per tag; spread more across sentences.
- The number inside `[n]` is always a Western digit — `[11]`, never `[١١]`: an Arabic-Indic digit breaks the citation link.
- `[n]` is reserved for references. Article and system numbers are written in prose with no brackets — «المادة 81», not «[81]» and not «(81)». A fact from the user's file is never tagged `[n]`.
- `[n]` tags appear in `synthesis_md` only — never in a `note`, a `decision`, a `topic` or a gap.
- Every number in `used_refs` must appear as `[n]` in `synthesis_md`.
- Do not add a references section («المراجع») or a legal disclaimer — code adds both.

## Output language

Write `synthesis_md`, every `note`, `topic`, `decision` and gap in fluent Modern Standard Arabic; an unavoidable technical term with no accurate Arabic equivalent may stay in English. Write every numeral you add with Western digits `0-9`; a quote from the file keeps its digits as the report quoted it. Refer to a document by what it is («الحكم المرفق»، «لائحة الدعوى المرفقة»), never by a code alias (WI-N, D1, S7). The field names, the JSON syntax, the `status` values and the `[n]` tags stay as written here.
"""

_SCHEMA_AR = """
## Output schema

Return ONE JSON object with these fields, in this order — settle the scoreboard and the contradictions first, then write the answer so that it agrees with them. No text outside the JSON.

```
{
  "angle_status": [{"angle": "A1", "status": "%s", "note": "…"}],
  "resolved": [{"topic": "…", "reports": [1, 2], "decision": "…", "settled": true}],
  "synthesis_md": "## الخلاصة\\n…",
  "used_refs": [1, 3],
  "gaps": ["…"],
  "confidence": "high | medium | low"
}
```
""" % " | ".join(ANGLE_STATES)

MERGER_SYSTEM_PROMPT = _TASK_AR + _SCHEMA_AR


# ---------------------------------------------------------------------------
# File refs — the user's file sections cited as [F3·S7] (aggregator_file_refs.md
# D11–D12, «بحث شامل»): the reports carry the tags their aggregators copied from the
# sections' labels, and the merger keeps each with its point. Applied ONLY when the
# merger input has tagged documents (``MergerInput.file_ref_docs``); otherwise the
# prompt is byte-identical.
# ---------------------------------------------------------------------------

#: Recorded on the deep_search.extended_merger span when the rules are on.
FILE_REFS_PROMPT_VERSION = "merger-file-refs-v1"

_FILE_REFS_SWAPS: tuple[tuple[str, str], ...] = (
    (
        "Lines like 〔D1·S7 · …〕 are section markers added by code — never quote them. "
        "Use the file only under rule 3.\n",
        "Each section starts under a label added by code that opens with its file tag — "
        "〔F3·S7 · …〕; a document shown whole, with no sections, has one label over its text — "
        "〔F4〕. Never quote a label. Use the file only under rule 3.\n",
    ),
    (
        "7. **Length:** complete, without repetition — normally shorter than the reports put together.\n",
        "7. **Length:** complete, without repetition — normally shorter than the reports put together.\n"
        "8. **File tags — keep them with their point.** A report's `[F3·S7]` (or `[F4]` for a whole "
        "document) right after a sentence or a quote points at the part of the user's file the point "
        "rests on; the user sees it as a small number that opens that part. When you keep the point, "
        "keep its tag right after it, copied exactly as the report wrote it; when reports tag the same "
        "point with different sections, keep each distinct tag once, side by side: `[F3·S7][F3·S9]`. "
        "When you settle a contradiction from the file (rule 3), put after your sentence the tag of the "
        "section that holds the words you followed, copied from its label. Never build or change a tag, "
        "never tag a point no report tagged unless rule 3 made you read that section, and never put a "
        "file tag and reference numbers in the same brackets. A file tag is not a legal authority: the "
        "rule still carries its `[n]`.\n",
    ),
    (
        "- `[n]` tags appear in `synthesis_md` only — never in a `note`, a `decision`, a `topic` or a gap.\n",
        "- `[n]` tags appear in `synthesis_md` only — never in a `note`, a `decision`, a `topic` or a gap.\n"
        "- The one other square-bracket form is the file tag (`[F3·S7]`, `[F4]` — rule 8): it is not a "
        "reference, it never goes into `used_refs`, and like `[n]` it appears in `synthesis_md` only.\n",
    ),
    (
        "never by a code alias (WI-N, D1, S7).",
        "never by a code alias (WI-N, F3, S7) in your words — a file tag in square brackets after a "
        "sentence (rule 8) is a pointer, not a name.",
    ),
)


def with_file_refs(prompt: str) -> str:
    """``prompt`` with the file-tag rules: the labels described, rule 8 (keep each
    report's tag with its point), the citation line, the alias rule. Each swap applies
    once; a prompt missing an anchor gets the others."""
    out = prompt
    for old, new in _FILE_REFS_SWAPS:
        if old in out:
            out = out.replace(old, new, 1)
    return out


def merger_system_prompt(*, file_refs: bool = False) -> str:
    """The system prompt; ``file_refs`` — the variant with the file-tag rules."""
    return with_file_refs(MERGER_SYSTEM_PROMPT) if file_refs else MERGER_SYSTEM_PROMPT

RETRY_MSG = (
    "Return the output as a single valid JSON object conforming to the schema "
    "(angle_status, resolved, synthesis_md, used_refs, gaps, confidence — in that order) "
    "only — with no text or <thinking> tag outside the JSON. The `synthesis_md` value "
    "must be in Arabic."
)


# ---------------------------------------------------------------------------
# User message
# ---------------------------------------------------------------------------


def _esc(value: object) -> str:
    """Text content: ``<``, ``>``, ``&`` escaped so a value can never open or close a tag."""
    return html.escape("" if value is None else str(value), quote=False)


def _attr(value: object) -> str:
    """Attribute value: as ``_esc`` plus quotes, so a value can never close the attribute."""
    return html.escape("" if value is None else str(value), quote=True)


def _western(value: object) -> str:
    """Code-derived numbers in Western digits only."""
    return ("" if value is None else str(value)).translate(_ARABIC_INDIC).strip()


def _field(obj: Any, name: str) -> Any:
    """``name`` from a dict or an object (conversation messages come as either)."""
    if isinstance(obj, Mapping):
        return obj.get(name)
    return getattr(obj, name, None)


def scoreboard_angle_ids(reports: Sequence["MergerReport"]) -> list[str]:
    """Every angle id any report carries (failed ones included), first-seen order."""
    seen: dict[str, None] = {}
    for report in reports:
        for angle_id in report.angle_ids or []:
            angle_id = (angle_id or "").strip()
            if angle_id:
                seen.setdefault(angle_id, None)
    return list(seen)


def render_angle_line(angle_id: str, angle: "MergerAngle | None", client_side: str = _UNKNOWN_SIDE) -> str:
    """One angle (unescaped): kind · strength [· لصالح …], title, argument, the research
    question and its weakness. No sections and no OCR flag — the merger judges an angle
    by what the reports found, not by re-reading the file. An unknown id renders by id."""
    if angle is None:
        return f"وجه {angle_id}"
    tags = [KIND_AR.get(angle.kind, angle.kind), STRENGTH_AR.get(angle.strength, angle.strength)]
    side = (angle.for_side or _UNKNOWN_SIDE).strip()
    if side != _UNKNOWN_SIDE and side != (client_side or _UNKNOWN_SIDE):
        tags.append(f"لصالح {(angle.for_party or '').strip() or side}")
    line = f"وجه {angle_id} [{' · '.join(t for t in tags if t)}]: {(angle.title or '').strip()}"
    if (angle.argument or "").strip():
        line += f" — {angle.argument.strip()}"
    if (angle.legal_question or "").strip():
        line += f" سؤال البحث: {angle.legal_question.strip()}"
    if (angle.weakness or "").strip():
        line += f" ما يضعفه: {angle.weakness.strip()}"
    return line


def _render_memory(memory: str) -> list[str]:
    """The memory block. ``render_conversation_memory`` output is already fenced and
    escaped (the aggregator prints it as is); anything else is escaped and fenced here.
    The note on legal positions follows it."""
    memory = (memory or "").strip()
    if not memory:
        return []
    pre_rendered = (
        memory.startswith(_MEMORY_OPEN)
        and memory.endswith(_MEMORY_CLOSE)
        and memory.count(_MEMORY_OPEN) == 1
        and memory.count(_MEMORY_CLOSE) == 1
    )
    block = memory if pre_rendered else f"{_MEMORY_OPEN}\n{_esc(memory)}\n{_MEMORY_CLOSE}"
    return [block, _MEMORY_NOTE_AR, ""]


def _ruling_year(ref: "Reference") -> str:
    """``سنة القضية: …`` for a ruling: its Hijri year when the reference carries one,
    else the Gregorian year the URA snippet opens with. Empty for anything else."""
    hijri = _western(getattr(ref, "hijri_year", "")).rstrip("هـ ").strip()
    if hijri:
        return f"سنة القضية: {hijri}هـ"
    m = _SNIPPET_YEAR.search(getattr(ref, "snippet", "") or "")
    return f"سنة القضية: {_western(m.group(1))}" if m else ""


def _render_reference(n: int, ref: "Reference") -> list[str]:
    """Number, type, title (+ parent source, ruling number and year, repealed / draft
    flag). No excerpt: the merger takes the reports' reading of each source."""
    title = (getattr(ref, "title", "") or "").strip()
    source = (getattr(ref, "regulation_title", "") or "").strip()
    case_number = (getattr(ref, "case_number", "") or "").strip()
    year = _ruling_year(ref)
    status_note = (getattr(ref, "status_note", "") or "").strip()  # in-process only; absent on older Reference

    out = [f'  <reference n="{n}" type="{_attr(getattr(ref, "source_type", ""))}">']
    out.append(f"    <title>{_esc(title)}</title>")
    if source and source != title:
        out.append(f"    <source>{_esc(source)}</source>")
    if case_number:
        out.append(f"    <case_number>{_esc(case_number)}</case_number>")
    if year:
        out.append(f"    <year>{_esc(year)}</year>")
    if status_note:
        tag = "status" if status_note.startswith(REPEALED_LABEL_AR) else "draft"
        out.append(f"    <{tag}>{_esc(status_note)}</{tag}>")
    out.append("  </reference>")
    return out


def _render_report(
    n: int, report: "MergerReport", body: str | None, inp: "MergerInput"
) -> list[str]:
    ok = report.ok
    attrs = [f'n="{n}"', f'title="{_attr((report.title or "").strip())}"']
    side = MODE_AR.get(report.mode or "")
    if side:
        attrs.append(f'side="{_attr(side)}"')
    if ok and getattr(report.output, "confidence", None):
        attrs.append(f'confidence="{_attr(report.output.confidence)}"')
    attrs.append(f'status="{"ok" if ok else "failed"}"')

    out = [f"  <report {' '.join(attrs)}>"]
    angle_ids = [a.strip() for a in report.angle_ids or [] if a and a.strip()]
    if angle_ids:
        out.append("    <angles>")
        for angle_id in angle_ids:
            line = render_angle_line(angle_id, inp.angles.get(angle_id), inp.client_side)
            out.append(f"- {_esc(line)}")
        out.append("    </angles>")

    if ok:
        if (body or "").strip():
            out.extend(["    <body>", _esc(body.strip()), "    </body>"])
        gaps = [g.strip() for g in getattr(report.output, "gaps", None) or [] if g and g.strip()]
        if gaps:
            out.append("    <gaps>")
            out.extend(f"      <gap>{_esc(g)}</gap>" for g in gaps)
            out.append("    </gaps>")
    out.append("  </report>")
    return out


def build_merger_user_message(
    inp: "MergerInput",
    *,
    shared_refs: Sequence["Reference"],
    report_bodies: Sequence[str | None],
) -> str:
    """Render everything the merger reads into its user message.

    ``shared_refs[i]`` is shared reference number ``i + 1`` — its own ``.n`` is the
    number it had in the report it came from and is ignored. ``report_bodies[i]``
    is report ``i``'s ``synthesis_md`` already renumbered to that shared list by
    code (``None`` / empty for a failed report).
    """
    if len(report_bodies) != len(inp.reports):
        raise ValueError(
            f"report_bodies has {len(report_bodies)} entries for {len(inp.reports)} reports"
        )

    lines: list[str] = [today_line(), ""]

    lines += ["<query>", _esc((inp.query or "").strip()), "</query>", ""]

    messages = []
    for m in inp.recent_messages or []:
        content = str(_field(m, "content") or "").strip()
        if content:
            role = str(_field(m, "role") or "").strip()
            messages.append(f'  <message role="{_attr(role)}">{_esc(content)}</message>')
    if messages:
        lines += ["<conversation>", *messages, "</conversation>", ""]

    lines += _render_memory(inp.conversation_memory)

    lines.append(
        f'<planner_understanding client_side="{_attr((inp.client_side or _UNKNOWN_SIDE).strip())}" '
        f'party="{_attr((inp.client_party or "").strip())}">'
    )
    lines += [_esc((inp.understanding or "").strip()), "</planner_understanding>", ""]

    lines.append(f'<reports count="{len(inp.reports)}">')
    for n, (report, body) in enumerate(zip(inp.reports, report_bodies), 1):
        lines += _render_report(n, report, body, inp)
    lines += ["</reports>", ""]

    lines.append(f'<references count="{len(shared_refs)}">')
    for n, ref in enumerate(shared_refs, 1):
        lines += _render_reference(n, ref)
    lines += ["</references>", ""]

    if inp.documents:
        lines.append("<case_documents>")
        for doc in inp.documents:
            attrs = f'ref="{_attr(_western(doc.ref))}" ' if (doc.ref or "").strip() else ""
            attrs += f'title="{_attr((doc.title or "").strip())}"'
            if (doc.doc_type or "").strip():
                attrs += f' doc_type="{_attr(doc.doc_type.strip())}"'
            lines.append(f"  <case_document {attrs}>")
            if (doc.text or "").strip():
                lines += ["    <text>", _esc(doc.text.strip()), "    </text>"]
            lines.append("  </case_document>")
        lines += ["</case_documents>", ""]

    if inp.left_out:
        lines.append("<left_out>")
        for entry in inp.left_out:
            angle = inp.angles.get(entry.angle_id)
            lines.append(f'  <angle id="{_attr(entry.angle_id)}">')
            if angle is not None and (angle.title or "").strip():
                lines.append(f"    <title>{_esc(angle.title.strip())}</title>")
            if (entry.why or "").strip():
                lines.append(f"    <why>{_esc(entry.why.strip())}</why>")
            lines.append("  </angle>")
        lines += ["</left_out>", ""]

    ids = scoreboard_angle_ids(inp.reports)
    scoreboard = (
        f"`angle_status`: exactly one entry for each of these angle ids, in this order — {_esc(', '.join(ids))}."
        if ids
        else "`angle_status`: an empty list (no report carries an angle)."
    )
    cite = (
        f"cite only numbers from `<references>` (1–{len(shared_refs)})"
        if shared_refs
        else "there are no references, so write no `[n]` tag"
    )
    lines += [
        "## Task",
        "Merge the reports above into ONE answer to `<query>`, following the system prompt. "
        f"{scoreboard} Write in Arabic with Western digits `0-9`; {cite}. "
        "Return the JSON object only, its fields in the schema's order.",
    ]
    return "\n".join(lines)


__all__ = [
    "FILE_REFS_PROMPT_VERSION",
    "MERGER_SYSTEM_PROMPT",
    "OCR_SUSPECT_AR",
    "PROMPT_VERSION",
    "RETRY_MSG",
    "build_merger_user_message",
    "merger_system_prompt",
    "render_angle_line",
    "scoreboard_angle_ids",
    "with_file_refs",
]
