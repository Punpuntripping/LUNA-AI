"""Prompt of the extended-search merger («بحث شامل» — the report merger).

Plan: ``.claude/plans/extended_search.md`` §2.4–2.5 and §2.7.3 (what was tested),
§4.6 (the merger), §4.9 b (the angle line each search got) and §4.9 c (the
strengths-and-weaknesses instruction), §6 items 3, 5, 9, 10, 12.

Prior art: the 2026-11-21 angle merge
(``attachment_segmentation/experiments/run_angle_merge.py``, 4/4 OK, settled 3–6
contradictions per file against the file text) — its seven rules are kept here
almost word for word — and the 2026-11-15 merger
(``planner_threads/merger.py``, beat a pooled aggregator 58–44 in a blind review).

Composition: the aggregator's ``_SHARED_ROLE_AR`` (imported, never copied: Arabic,
Western digits, ``[n]`` form, repealed ``<status>``, ruling age, official
principles, quoting the user's documents, ``<conversation_memory>``), then the
merger's own sections. English instructions, Arabic output (repo convention).

The user message (``build_merger_user_message``) is built by code only; every
value in it is html-escaped. Its layout keeps the tested 11-21 order and adds the
new parts: today's date · ``<query>`` · ``<conversation>`` ·
``<conversation_memory>`` · ``<planner_understanding>`` · ``<case_documents>`` ·
``<references>`` · ``<reports>`` · ``<left_out>`` · a closing task line that lists
the exact angle ids the scoreboard must hold.
"""
from __future__ import annotations

import html
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

from agents.deep_search_v4.aggregator.prompts import (
    _MEMORY_CONFLICT_NOTE,
    _SHARED_ROLE_AR,
)
from agents.utils.today import today_line
from shared.library.reg_status import REPEALED_LABEL_AR

from .models import ANGLE_STATES, KIND_AR, MODE_AR, STRENGTH_AR

if TYPE_CHECKING:
    from agents.deep_search_v4.aggregator.models import Reference

    from .models import MergerAngle, MergerInput, MergerReport

PROMPT_VERSION = "merger-v1"

#: The angle-line flag for ``MergerAngle.ocr_suspect`` — the same words the
#: angle-map card shows (``scripts/angle_map_preview.py`` ``OCR_SUSPECT_AR``).
OCR_SUSPECT_AR = "قد يكون خطأ قراءة"

_UNKNOWN_SIDE = "unknown"
_MEMORY_OPEN = "<conversation_memory>"
_MEMORY_CLOSE = "</conversation_memory>"
_ARABIC_INDIC = str.maketrans("٠١٢٣٤٥٦٧٨٩۰۱۲۳۴۵۶۷۸۹", "01234567890123456789")


# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_K_STR, _K_ATT, _K_ANS = KIND_AR["strengthen"], KIND_AR["attack"], KIND_AR["answer"]
_S_STRONG, _S_MED, _S_WEAK = STRENGTH_AR["strong"], STRENGTH_AR["medium"], STRENGTH_AR["weak"]
_M_REG, _M_CASE, _M_FULL = MODE_AR["reg_compliance_led"], MODE_AR["case_led"], MODE_AR["full"]

_TASK_AR = f"""
## This task: merge parallel research reports into ONE answer

In this task the search results reach you through reports. The user's matter was researched as an extended search («بحث شامل»): a planner read the angles (أوجه) of the user's file — the arguments that strengthen the user's position, the ones that attack the other side or the evidence serving it, and the other side's strong points the user must answer — and ran several searches at the same time, each on one or more of those angles. Each search read the user's whole file and every document's summary, and wrote its own report. You write the ONE answer the user reads first.

Every report's citations were renumbered by code into ONE shared list, `<references>`: the same `[n]` means the same source in every report, and in your answer.

## What you receive

- `<query>` — the user's message, word for word. Your answer answers it.
- `<conversation>` — the recent messages before it. `<conversation_memory>`, when present, follows the rules above.
- `<planner_understanding>` — the planner's reading of the matter, with the user's side (`client_side`, `party`). Machine analysis: a lead, never a fact.
- `<case_documents>` — every document of the user's file, each with its number `ref` (`D1`, `D2` …): `<text>` is the document's own words; `<summary>` is machine analysis.
- `<references>` — the shared list. Each `<reference>` has its number `n`, its `type`, its `<title>`, its `<source>` (the parent regulation, court or issuing body, when it differs from the title) and its `<excerpt>` — the start of the source's text, shortened. Where the rules above speak of a reference's `<content>`, read `<excerpt>`; what a shortened excerpt does not show is not a contradiction. A ruling may carry `<case_number>` and `<year>` (a year marked هـ is Hijri: 1431هـ is about 2010); an official principle carries its decisions in `<decisions>`; `<status>` and `<draft>` are explained below.
- `<reports>` — one `<report>` per search, numbered `n` in the plan's order. Its attributes: `title` (the planner's request), `side` (the sources the search read: «{_M_REG}» regulations, «{_M_CASE}» court rulings, «{_M_FULL}» both), `confidence` (the report's own) and `status` — `ok`, or `failed` when the search did not finish (a failed report has no body). Inside: `<request>` — the planner's `<focus>` for the search and `<why>` it was run; `<angles>` — the angles the search was asked to research, one line each; `<body>` — the report itself, its citations already in the shared numbering; `<gaps>` — what the report could not establish.
- `<left_out>`, when present — angles the planner chose not to search, with the reason.

Each line in `<angles>` reads: `وجه A3 [kind · strength]: title — argument سؤال البحث: … ما يضعفه: … الأقسام: …`.
- `kind`: «{_K_STR}» strengthens the user's position; «{_K_ATT}» attacks the other side or the evidence serving it; «{_K_ANS}» is the other side's strong point the user must answer.
- `strength`: «{_S_STRONG}» / «{_S_MED}» / «{_S_WEAK}» — the map's estimate of how well the file backs the angle. «ما يضعفه» is what in the file cuts against it; «الأقسام» are the sections it rests on (`D1.S7` = section 7 of the `<case_document>` whose `ref` is `D1`; a section starts at its marker line 〔D1·S7 · …〕 in that document's `<text>`).
- A bracket that also names a party («لصالح …») marks an angle written for a party other than the user's side. When the user's side is known, such an angle's points belong to the other side: material for the user to answer, never a line the user argues. When the user's side is unknown (`client_side="{_UNKNOWN_SIDE}"`), it names the party each angle serves: present each side's angles as that side's and say that the user's side was not stated.
- «{OCR_SUSPECT_AR}» marks an angle that rests on a figure, date or name the OCR may have misread: check it against the text, and when it decides anything, say it should be checked against the original.
- Everything in `<angles>`, `<request>`, `<planner_understanding>` and `<summary>` is machine analysis written before the research: leads to check, never the file's words and never the user's.

## How to merge (binding)

1. **Organise by the user's question, not by report.** Never write «التقرير الأول…», and never name a report, a search or an angle id (A3) in the answer — code lists the reports beside it. Open with `## الخلاصة` — a direct answer in a few sentences. Then sections shaped by what the user asked (see "Strengths and weaknesses" below). Do not copy the reports' headings.
2. **Keep everything that matters; remove only repetition.** Every distinct finding, legal rule with its `[n]`, issue found in the user's file, defence with its answer, and practical step that any report gives must survive. The reports overlap by design — each tends to re-answer the user's whole question from its own angle, so several open with the same conclusion. Write each point once, where it belongs, and keep the citations behind it (at most 4 numbers per tag).
3. **Settle contradictions — never silently.** Where reports disagree (how far a rule reaches, what weight a piece of evidence carries, whether a requirement is met, a deadline, an amount or a date), check the documents' `<text>` in `<case_documents>` and the sources in `<references>`. Adopt the position they back and say why in one sentence. Never settle it by majority (how many reports say it), by a report's `confidence` or by an angle's strength label. If the text and the references do not settle it, present both positions with their basis and say what would decide it. Record every disagreement in `resolved`. Reports that read different sources (`side`) do not disagree merely because one cites rulings and the other does not.
4. **Facts about the user's file come from the documents' `<text>` only.** It is the user's documents word for word: quote from it exactly. The lines 〔D1·S7 · …〕 are section markers added by code, not the documents' words: never quote them, and point to a passage by its document and its part, as the rules above say — never by a marker number (S7, D1.S7). The `<summary>`, the angle lines, the `<request>` and `<planner_understanding>` are machine analysis — leads, never quoted as the file. When a report states a fact about the file that the text contradicts, follow the text, correct it, and record the correction in `resolved`. (A fact the user stated in `<conversation_memory>` still wins, as the rules above say.)
5. **Nothing new.** Add no source, article, ruling, amount or date that is in neither the reports nor the documents. Never cite a reference for a claim no report backed with it. When a reference's `<excerpt>` contradicts the claim a report cites it for, follow the excerpt.
6. **Length:** complete, without repetition — normally shorter than the reports put together.
7. **Gaps:** merge the reports' `<gaps>` without repeats; drop a gap another report closed.
8. **Two traps in the file.**
   - Never call a document signed when its signature fields are blank, or when it says it takes effect only once signed or approved — unless its text shows the signature. Correct a report that calls such a document signed.
   - A cover email or cover letter is a cover email: it is not the document it forwards or mentions. Never treat it as the notice, contract or decision it covers, and never give the covered document the cover email's date or contents.

## Strengths and weaknesses (نقاط القوة ونقاط الضعف)

Each `<report>` carries, in `<angles>`, the angles it researched: «{_K_STR}» and «{_K_ATT}» angles are the user's strengths; «{_K_ANS}» angles are the opponent's strong points the user must answer; «ما يضعفه» states what weakens an angle. (An angle marked «لصالح …» counts for the party it names — see above.) The angles are machine analysis — never quote them as the file.
1. When the user's question is about a position — theirs, the opponent's, or the case's chances — organise the answer around it: the user's strengths, each with its legal basis and the parts of the file it rests on; then the weaknesses and the opponent's strong points, each followed by its answer — or marked as open, with what would decide it. When the user asked for something else (a memo, a reply, a deadline), follow that shape and keep the strengths and weaknesses inside it.
2. Keep every strength and every weakness that any report established, with its citations. Drop one only when the documents' `<text>` or the references show it is wrong, and record that in `resolved`. Never drop a weakness because another report did not mention it.
3. Where reports disagree on a strength, a weakness or its answer, settle it against `<case_documents>` (the documents' text) and `<references>` — never by majority and never by an angle's strength label — and record it in `resolved`.
4. A weakness no report answered stays in the answer, marked open, and goes to `gaps`.

## The angle scoreboard — `angle_status`

Code shows the user one status per angle beside your answer.
- Write exactly one entry for each angle id that appears in the `<angles>` of any report — failed reports included — spelled exactly as written (e.g. `A3`), in the order the ids first appear in `<reports>`. No other id: never one from `<left_out>`, never an invented one. The task line at the end of the message lists the ids.
- `status` is one of:
  - `supported` — a strength («{_K_STR}» / «{_K_ATT}») the research backs with a legal basis applied to the file;
  - `answered` — an opponent's point («{_K_ANS}») or a strength's weakness, for which the research found an answer;
  - `open` — no source settles it; the note says what would decide it;
  - `against` — the research cuts against the user here: a strength the file or the law defeats, or an opponent's point that stands with a basis and no answer;
  - `not_researched` — its search failed, or no report actually dealt with it.
- A strength whose basis holds but whose unanswered weakness could defeat it is `open`, not `supported`. An angle carried by a failed report is `not_researched` unless another report dealt with it — then judge it by what that report found.
- `note`: one Arabic sentence — the basis for the status (the rule, or the part of the file it turns on), or what would decide an `open` angle; for `not_researched`, that it was not researched. No `[n]` tag in a note: name the rule in words («المادة 77 من نظام العمل»).
- The scoreboard must agree with `synthesis_md`: what the answer establishes is `supported` or `answered`, what it leaves open is `open`, what it says goes against the user is `against`.
- Never write findings in the answer about an angle no report researched. If the user's question turns on one, say in one sentence that it was not researched.

## Disagreements — `resolved`

One entry for every point on which reports disagreed, and for every report claim you corrected or dropped against the documents' text or the references:
- `topic`: a short Arabic phrase naming the point;
- `reports`: the numbers (`n`) of the reports involved;
- `decision`: one or two Arabic sentences — what the answer says and on what basis (the document and its part, or the rule named in words; no `[n]` tag);
- `settled`: `true` when the documents' text or the references decide it; `false` when they do not and the answer presents both positions with what would decide it.
A point one report makes and another does not mention is not a disagreement. An empty list is right when the reports agree.

## Angles the planner left out — `<left_out>`

Context only: what the plan chose not to search, and why. Never present findings on a left-out angle, never list it in `gaps`, and never give it a scoreboard entry — code shows these angles to the user separately. (A finding a report made on its own angles stays, even where it touches the same facts.)

## Repealed and draft sources

`<status>` marks a repealed source — the repeal rule above applies. `<draft>` marks a consultation draft (مسودة): a text a government body published for public comment, not a confirmed issued law. Never present a draft's text as the rule in force: if you cite it, name it a draft in the same sentence, and prefer a reference without `<draft>` that covers the same point; never call a draft repealed or never issued. Where a report presents a repealed or draft source as the law in force, correct it.

## `gaps` and `confidence`

- `gaps`: short Arabic sentences — the reports' gaps merged without repeats, minus any gap another report closed; every weakness no report answered; and one line for the angles of a failed report that no other report dealt with. Nothing about `<left_out>` angles.
- `confidence`: how well the merged answer covers the user's question as a whole. It may never exceed the highest `confidence` among the reports — three `medium` reports give at most `medium`, however well they fit together.

## Citation rules (binding)

- Cite with the shared numbers from `<references>` only, in square brackets after the sentence they back: `[3]`, `[3,7]`. Several numbers share one tag, separated by commas — `[1,3]`, not `[1][3]` — at most 4 numbers per tag; spread more across sentences.
- The number inside `[n]` is always a Western digit — `[11]`, never an Arabic-Indic digit: an Arabic-Indic digit breaks the citation link.
- `[n]` is reserved for references. Article and system numbers are written in prose with no brackets — «المادة 81», not «[81]» and not «(81)».
- `[n]` tags appear in `synthesis_md` only — never in a `note`, a `decision`, a `topic` or a gap.
- Every number in `used_refs` must appear as `[n]` in `synthesis_md`.
- Do not add a references section («المراجع») or a legal disclaimer to `synthesis_md` — both are added by code.

## Output language reminder — binding

Write `synthesis_md`, every `note`, `topic`, `decision` and gap in Arabic as much as you can; an unavoidable technical term with no accurate Arabic equivalent may stay in English. Every numeral uses Western digits `0-9`, never Arabic-Indic digits. The field names, the JSON syntax, the `status` values and the `[n]` tags stay as written here.
"""

_SCHEMA_AR = """
## Output schema

Return ONE JSON object with these fields, in this order — settle the scoreboard and the disagreements first, then write the answer so that it agrees with them. No text outside the JSON.

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

MERGER_SYSTEM_PROMPT = _SHARED_ROLE_AR + _TASK_AR + _SCHEMA_AR

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
    """One angle in the §4.9 b layout (unescaped). An unknown id renders by id only."""
    if angle is None:
        return f"وجه {angle_id}"
    tags = [KIND_AR.get(angle.kind, angle.kind), STRENGTH_AR.get(angle.strength, angle.strength)]
    side = (angle.for_side or _UNKNOWN_SIDE).strip()
    if side != _UNKNOWN_SIDE and side != (client_side or _UNKNOWN_SIDE):
        tags.append(f"لصالح {(angle.for_party or '').strip() or side}")
    if angle.ocr_suspect:
        tags.append(OCR_SUSPECT_AR)
    line = f"وجه {angle_id} [{' · '.join(t for t in tags if t)}]: {(angle.title or '').strip()}"
    if (angle.argument or "").strip():
        line += f" — {angle.argument.strip()}"
    if (angle.legal_question or "").strip():
        line += f" سؤال البحث: {angle.legal_question.strip()}"
    if (angle.weakness or "").strip():
        line += f" ما يضعفه: {angle.weakness.strip()}"
    sections = [s.strip() for s in angle.sections or [] if s and s.strip()]
    if sections:
        line += f" الأقسام: {'، '.join(sections)}"
    return line


def _render_memory(memory: str) -> list[str]:
    """The memory block. ``render_conversation_memory`` output is already fenced and
    escaped (the aggregator prints it as is); anything else is escaped and fenced here."""
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
    return [block, _MEMORY_CONFLICT_NOTE, ""]


def _render_reference(n: int, ref: "Reference") -> list[str]:
    title = (getattr(ref, "title", "") or "").strip()
    source = (getattr(ref, "regulation_title", "") or "").strip()
    case_number = (getattr(ref, "case_number", "") or "").strip()
    year = _western(getattr(ref, "hijri_year", "")).rstrip("هـ ").strip()
    decisions = (getattr(ref, "decision_line", "") or "").strip()
    status_note = (getattr(ref, "status_note", "") or "").strip()  # in-process only; absent on older Reference
    excerpt = (getattr(ref, "snippet", "") or "").strip()

    out = [f'  <reference n="{n}" type="{_attr(getattr(ref, "source_type", ""))}">']
    out.append(f"    <title>{_esc(title)}</title>")
    if source and source != title:
        out.append(f"    <source>{_esc(source)}</source>")
    if case_number:
        out.append(f"    <case_number>{_esc(case_number)}</case_number>")
    # A ruling's excerpt already opens with «سنة القضية: <Gregorian>» on the URA
    # path; a second, Hijri year under the same label would read as a clash.
    if year and "سنة القضية" not in excerpt:
        out.append(f"    <year>سنة القضية: {_esc(year)}هـ</year>")
    if decisions:
        out.append(f"    <decisions>{_esc(decisions)}</decisions>")
    if status_note:
        # The shared role reads <status> as REPEALED; a consultation draft gets its own element.
        tag = "status" if status_note.startswith(REPEALED_LABEL_AR) else "draft"
        out.append(f"    <{tag}>{_esc(status_note)}</{tag}>")
    if excerpt:
        out.append(f"    <excerpt>{_esc(excerpt)}</excerpt>")
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

    out = [f"  <report {' '.join(attrs)}>", "    <request>"]
    if (report.focus or "").strip():
        out.append(f"      <focus>{_esc(report.focus.strip())}</focus>")
    if (report.why or "").strip():
        out.append(f"      <why>{_esc(report.why.strip())}</why>")
    out.append("    </request>")

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

    if inp.documents:
        lines.append("<case_documents>")
        for doc in inp.documents:
            attrs = f'ref="{_attr(_western(doc.ref))}" ' if (doc.ref or "").strip() else ""
            attrs += f'title="{_attr((doc.title or "").strip())}"'
            if (doc.doc_type or "").strip():
                attrs += f' doc_type="{_attr(doc.doc_type.strip())}"'
            lines.append(f"  <case_document {attrs}>")
            if (doc.summary or "").strip():
                lines.append(f"    <summary>{_esc(doc.summary.strip())}</summary>")
            if (doc.text or "").strip():
                lines += ["    <text>", _esc(doc.text.strip()), "    </text>"]
            lines.append("  </case_document>")
        lines += ["</case_documents>", ""]

    lines.append(f'<references count="{len(shared_refs)}">')
    for n, ref in enumerate(shared_refs, 1):
        lines += _render_reference(n, ref)
    lines += ["</references>", ""]

    lines.append(f'<reports count="{len(inp.reports)}">')
    for n, (report, body) in enumerate(zip(inp.reports, report_bodies), 1):
        lines += _render_report(n, report, body, inp)
    lines += ["</reports>", ""]

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
        f"{scoreboard} Write in Arabic with Western digits `0-9` only; {cite}. "
        "Return the JSON object only, its fields in the schema's order.",
    ]
    return "\n".join(lines)


__all__ = [
    "MERGER_SYSTEM_PROMPT",
    "OCR_SUSPECT_AR",
    "PROMPT_VERSION",
    "RETRY_MSG",
    "build_merger_user_message",
    "render_angle_line",
    "scoreboard_angle_ids",
]
