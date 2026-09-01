"""Reranker model A/B — replay recorded reg-family reranker calls on two models.

The two reranker slots moved from ``qwen3.5-flash`` to ``qwen3.7-flash``
(``agents/utils/agent_models.py`` ``_RERANKER``). This script measures the swap
against real traffic instead of a synthetic prompt: it lifts recorded reranker
LLM calls out of the ``agents_reports/agentic_monitor/*/llm_calls/`` dumps and
re-runs each one, byte-identical, on both models.

Fidelity rules (the point is to isolate the MODEL as the only variable):

* The recorded **system instructions** are replayed verbatim — not
  ``get_reranker_prompt()``. The prompts have moved since these turns were
  captured; replaying today's prompt would confound a model delta with a prompt
  delta.
* The recorded **user message** (sub-query + rendered candidate blocks) is
  replayed verbatim, so both models see the identical candidate set with the
  identical ``[Cn]`` labels.
* The live agent's ``output_type`` union (``RegRerankerClassification`` +
  ``TextOutput`` JSON salvager), ``retries``, ``UsageLimits`` and
  ``model_settings`` (``enable_thinking`` + 15k ``thinking_budget``) are
  reproduced from ``reg_compliance_search/reranker.py``.
* Each arm binds ONE concrete model, never the ``FallbackModel`` chain — a
  silent fallback to deepseek would be scored as the qwen arm's result and hide
  exactly the structured-output reliability regression we are testing for.

Three result columns come out of it:

  ``recorded``  the historical qwen3.5-flash output stored in the dump
  ``qwen3.5``   a fresh qwen3.5-flash run today (controls for nondeterminism
                and for any provider-side drift since the capture)
  ``qwen3.7``   the candidate

Usage::

    python -m scripts.reranker_model_ab extract --n 24
    python -m scripts.reranker_model_ab run
    python -m scripts.reranker_model_ab compare
    python -m scripts.reranker_model_ab downstream --refresh
"""

from __future__ import annotations

import argparse
import asyncio
import collections
import json
import random
import re
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

MONITOR_DIR = REPO / "agents_reports" / "agentic_monitor"
OUT_DIR = REPO / "agents_reports" / "reranker_model_ab"
CASES_PATH = OUT_DIR / "cases.jsonl"
RUNS_PATH = OUT_DIR / "runs.jsonl"

# Reranker stages that emit the RegRerankerClassification schema. case_search
# has its own schema and is excluded — mixing schemas would make the keep-set
# metrics incomparable.
_REG_STAGE_RE = re.compile(r"(reg_reranker|reg_search_reranker|compliance_reranker|compliance_search_reranker)")

BASELINE_MODEL = "qwen3.5-flash"
CANDIDATE_MODEL = "qwen3.7-flash"

# Mirrors reg_compliance_search/reranker.py RERANKER_LIMITS + model_settings.
_OUTPUT_TOKENS_LIMIT = 25_000
_REQUEST_LIMIT = 3
_THINKING_BUDGET = 15_000


# ---------------------------------------------------------------- extraction


@dataclass
class Case:
    """One recorded reranker call, replayable."""

    case_id: str
    convo: str
    call_no: str
    stage: str
    recorded_model: str
    system_prompt: str
    user_message: str
    n_candidates: int
    labels: list[str]
    recorded: dict[str, Any]
    recorded_tokens: dict[str, int]
    recorded_duration_s: float | None


def _parse_header(text: str) -> dict[str, Any]:
    head: dict[str, Any] = {}
    m = re.search(r"^- stage: (.+)$", text, re.M)
    head["stage"] = m.group(1).strip() if m else ""
    m = re.search(r"duration: ([\d.]+)s", text)
    head["duration_s"] = float(m.group(1)) if m else None
    m = re.search(r"^- tokens: in=(\d+) out=(\d+) reasoning=(\d+)", text, re.M)
    head["tokens"] = (
        {"input": int(m.group(1)), "output": int(m.group(2)), "reasoning": int(m.group(3))}
        if m
        else {"input": 0, "output": 0, "reasoning": 0}
    )
    return head


def _section(text: str, start: str, end: str | None) -> str:
    i = text.find(start)
    if i < 0:
        return ""
    i += len(start)
    j = text.find(end, i) if end else -1
    return text[i:j] if j >= 0 else text[i:]


def _json_after(marker: str, blob: str) -> Any | None:
    """Parse the first JSON value following ``marker`` inside ``blob``."""
    i = blob.find(marker)
    if i < 0:
        return None
    tail = blob[i + len(marker):].lstrip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(tail)
        return obj
    except ValueError:
        return None


def _recorded_output(out_blob: str) -> dict[str, Any] | None:
    """Pull the reranker's structured result out of the recorded assistant turn.

    The live agent has a two-member output union, so the recorded turn is either
    a ``final_result`` tool_call (arguments = the JSON) or a text part the
    salvager rescued. Both shapes appear in the corpus.
    """
    msg = _json_after("#### [assistant]", out_blob)
    if not isinstance(msg, dict):
        return None
    for part in msg.get("parts", []):
        if part.get("type") == "tool_call" and part.get("name") == "final_result":
            args = part.get("arguments")
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except ValueError:
                    continue
            if isinstance(args, dict):
                return args
    for part in msg.get("parts", []):
        if part.get("type") == "text":
            body = (part.get("content") or "").strip()
            m = re.search(r"\{.*\}", body, re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except ValueError:
                    continue
    return None


def parse_dump(path: Path) -> Case | None:
    text = path.read_text(encoding="utf-8")
    head = _parse_header(text)

    sys_blob = _section(text, "## System instructions", "## Input messages")
    try:
        sys_parts = json.loads(sys_blob.strip())
    except ValueError:
        return None
    system_prompt = "\n\n".join(
        p.get("content", "") for p in sys_parts if isinstance(p, dict)
    ).strip()

    in_blob = _section(text, "## Input messages", "## Output messages")
    user_msg = _json_after("#### [user]", in_blob)
    if not isinstance(user_msg, dict):
        return None
    user_text = "\n\n".join(
        p.get("content", "")
        for p in user_msg.get("parts", [])
        if isinstance(p, dict) and p.get("type") == "text"
    ).strip()
    if not system_prompt or not user_text:
        return None

    # Most of the dump corpus predates the keep-only reranker: those turns emit
    # the legacy `decisions: [{label, action: keep|drop|unfold}]` schema, which
    # today's RegRerankerClassification cannot represent. They are skipped by
    # design — a case is only replayable if its recorded output is on the schema
    # the live agent still uses.
    recorded = _recorded_output(_section(text, "## Output messages", None))
    if not isinstance(recorded, dict) or "keeps" not in recorded:
        return None

    labels = sorted(set(re.findall(r"^### \[(C\d+)\]", user_text, re.M)))
    if not labels:
        return None

    stem = path.stem
    call_no = stem.split("_", 1)[0]
    m = re.match(r"\d+_(.+?)_(qwen|deepseek)", stem)
    stage = m.group(1) if m else head.get("stage", "")

    return Case(
        case_id=f"{path.parent.parent.name}:{call_no}",
        convo=path.parent.parent.name,
        call_no=call_no,
        stage=stage,
        recorded_model="qwen3.5-flash" if "qwen3_5" in stem else stem,
        system_prompt=system_prompt,
        user_message=user_text,
        n_candidates=len(labels),
        labels=labels,
        recorded=recorded,
        recorded_tokens=head["tokens"],
        recorded_duration_s=head["duration_s"],
    )


def cmd_extract(args: argparse.Namespace) -> None:
    paths = [
        p
        for p in MONITOR_DIR.glob("**/llm_calls/*reranker*.md")
        if _REG_STAGE_RE.search(p.stem) and "qwen3_5" in p.stem
    ]
    print(f"candidate dumps (reg-family, qwen3.5-flash): {len(paths)}")

    cases: list[Case] = []
    skipped = 0
    for p in paths:
        try:
            c = parse_dump(p)
        except Exception as exc:  # noqa: BLE001 - a malformed dump must not abort the sweep
            print(f"  ! parse error {p.name}: {exc}")
            c = None
        if c is None:
            skipped += 1
            continue
        cases.append(c)
    print(f"parsed {len(cases)}  skipped {skipped}")

    # Stratify: round-robin across conversations so one long convo cannot own
    # the sample, and drop trivially small candidate sets (<4 blocks) where a
    # keep-set diff carries almost no signal.
    cases = [c for c in cases if c.n_candidates >= 4]
    by_convo: dict[str, list[Case]] = {}
    for c in cases:
        by_convo.setdefault(c.convo, []).append(c)
    rng = random.Random(args.seed)
    for lst in by_convo.values():
        rng.shuffle(lst)

    picked: list[Case] = []
    convos = sorted(by_convo)
    while len(picked) < args.n and any(by_convo[k] for k in convos):
        for k in convos:
            if by_convo[k] and len(picked) < args.n:
                picked.append(by_convo[k].pop())

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with CASES_PATH.open("w", encoding="utf-8") as fh:
        for c in picked:
            fh.write(json.dumps(asdict(c), ensure_ascii=False) + "\n")
    print(
        f"wrote {len(picked)} cases -> {CASES_PATH.relative_to(REPO)} "
        f"({len({c.convo for c in picked})} conversations, "
        f"{sum(c.n_candidates for c in picked)} candidate blocks)"
    )


def _paths(args: argparse.Namespace) -> tuple[Path, Path]:
    """Resolve (cases, runs) jsonl paths, honouring --cases/--runs overrides."""
    c = getattr(args, "cases", None)
    r = getattr(args, "runs", None)
    return (OUT_DIR / c if c else CASES_PATH, OUT_DIR / r if r else RUNS_PATH)



# ------------------------------------------------------------------- replay


@dataclass
class RunResult:
    case_id: str
    model: str
    ok: bool
    error: str = ""
    duration_s: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    requests: int = 0
    salvaged: bool = False
    #: True when the first pass kept 0 of N>0 and the production zero-keep
    #: reconsider note was replayed. ``output`` is then the SECOND pass.
    nudged: bool = False
    first_pass_keeps: int | None = None
    output: dict[str, Any] = field(default_factory=dict)


# ------------------------------------------------------------ prompt patches
#
# An arm may be written ``model@patch`` (e.g. ``qwen3.7-flash@widen_medium``).
# The patch rewrites the replayed system prompt before the run, so a prompt
# recalibration can be measured with the SAME cases and the SAME downstream
# ground truth as the plain model arms.
#
# Every anchor below is byte-identical in the recorded prompt and in today's
# live ``get_reranker_prompt("prompt_1")`` (verified 2026-08-31), so a patch that
# applies here ports verbatim into
# ``agents/deep_search_v4/reg_compliance_search/prompts.py``.

_A_KEEPWORTHY = (
    "A chunk is keep-worthy when the system scope applies to the query **and** "
    "the chunk summary carries directly useful legal material."
)
_A_IFFAIL = "If **either** gate fails but the chunk is still useful → `medium`."
_A_SCARCITY = (
    "**Scarcity:** `high` is scarce — typically about **1–3 high keeps per "
    "sub-query**. If you find yourself marking many chunks `high`, you are "
    "miscalibrating; downgrade to `medium`."
)
_A_SAMEPARENT = 'If the only overlap is "same parent law" → drop.'
_A_SHORTLIST = (
    "A short `keeps` list is valid — never add or pad entries, and never drop a "
    "deserving chunk just to make the list shorter."
)


#: The v2 edit pairs (old, new), shared by the patch and by the live-prompt
#: reversal in :func:`_live_prompt_base`.
_V2_EDIT_PAIRS: list[tuple[str, str]] = []

def _patch_widen_medium(prompt: str) -> str:
    """Widen the `medium` band; leave the `high` two-gate test untouched.

    Diagnosis this encodes (see agents_reports/reranker_model_ab/report):
    qwen3.7-flash's recall collapse is almost entirely in `medium` — it kept 31
    high / 21 medium where qwen3.5-flash kept 40 high / 66 medium, and 10 of the
    11 answer-bearing chunks it lost were `medium`. So `high` scarcity is NOT
    the problem and is deliberately left alone; what 3.7 does wrong is apply the
    `high` bar to the whole keep/drop decision and treat "not the governing
    rule" as "drop".

    The five edits: state the drop/keep asymmetry explicitly, define `medium` as
    the generous band, scope the scarcity number to the `high` tier and add a
    total-keeps expectation, stop the "same parent law" rule from forcing a drop
    when it should only block `high`, and name zero-keeps-on-a-full-candidate-set
    as the miscalibration it is.
    """
    out = prompt
    for old, new in (
        (_A_KEEPWORTHY,
         "A chunk is keep-worthy when the system scope applies to the query "
         "**and** the chunk carries legal material the aggregator could actually "
         "use — either as the governing rule (`high`) or as supporting material "
         "that helps state, qualify, or situate the answer (`medium`).\n\n"
         "**The keep bar is asymmetric.** The aggregator can ignore a weak chunk "
         "it received; it cannot cite one you dropped. When a candidate sits on "
         "the genuine borderline between `medium` and drop, **keep it as "
         "`medium`**. Reserve dropping for candidates that are off-scope or "
         "off-topic — not for candidates that are merely not the governing rule."),
        (_A_IFFAIL,
         "If **either** gate fails but the chunk is still useful → `medium`. "
         "Read \"useful\" broadly: `medium` is the **generous** band. A chunk "
         "earns `medium` when it supports the answer without deciding it — the "
         "definitions, scope clauses, procedures, penalties, obligations and "
         "adjacent provisions the aggregator needs to state the rule accurately "
         "and completely. Failing the two gates is a reason to downgrade to "
         "`medium`, **not** a reason to drop."),
        (_A_SCARCITY,
         "**Scarcity:** `high` is scarce — typically about **1–3 high keeps per "
         "sub-query**. If you find yourself marking many chunks `high`, you are "
         "miscalibrating; downgrade to `medium` — downgrade, do not drop.\n\n"
         "Scarcity governs the **`high` tier only**; it is not a budget on your "
         "total keeps. Across both tiers a typical sub-query yields about **2–5 "
         "keeps**. Returning 0–1 keeps from a candidate set of ten or more "
         "almost always means you applied the `high` bar to the whole keep "
         "decision."),
        (_A_SAMEPARENT,
         'If the only overlap is "same parent law" → it is not `high`; keep it '
         "`medium` when it still carries material the aggregator could use for "
         "this sub-query, and drop it only when it does not."),
        (_A_SHORTLIST,
         "A short `keeps` list is valid — never add or pad entries, and never "
         "drop a deserving chunk just to make the list shorter. But returning "
         "zero keeps while the candidate set still holds on-scope material is a "
         "miscalibration, not rigour."),
    ):
        assert old in out, f"prompt patch anchor missing: {old[:60]!r}"
        out = out.replace(old, new, 1)
    return out



def _patch_widen_medium_v2(prompt: str) -> str:
    """``widen_medium`` plus a harder push on total keep volume.

    v1 moved qwen3.7-flash from 1.18 to 1.91 keeps/call and downstream recall
    from 0.59 to 0.85 (vs qwen3.5-flash's 0.89) — but still under the 2.41
    keeps/call the baseline produces, with 7 of 44 runs still returning zero.
    v2 raises the stated total-keep band and puts a floor check on the
    small-list case, without touching the `high` two-gate test.
    """
    out = _patch_widen_medium(prompt)
    for old, new in (
        ("Across both tiers a typical sub-query yields about **2–5 keeps**.",
         "Across both tiers a typical sub-query yields about **3–6 keeps**."),
        ("Returning 0–1 keeps from a candidate set of ten or more almost always "
         "means you applied the `high` bar to the whole keep decision.",
         "Returning 0–1 keeps from a candidate set of ten or more almost always "
         "means you applied the `high` bar to the whole keep decision. Before "
         "you finalise a list of fewer than two keeps, re-read the candidates "
         "once against the `medium` bar specifically — not the two-gate `high` "
         "test — and keep every one that is on-scope and carries usable "
         "material. Only a genuinely off-scope or off-topic candidate set "
         "justifies a near-empty list."),
    ):
        assert old in out, f"v2 anchor missing: {old[:60]!r}"
        out = out.replace(old, new, 1)
    return out



def _live_prompt_base() -> str:
    """Today's live reg reranker prompt, with the v2 recalibration REMOVED.

    ``prompts.py`` already carries the recalibration (applied 2026-08-31), so the
    pre-recalibration control has to be reconstructed by reversing the five
    edits. Asserting each reversal keeps this honest if the prompt moves again.
    """
    from agents.deep_search_v4.reg_compliance_search.prompts import get_reranker_prompt

    out = get_reranker_prompt("prompt_1")
    patched = _patch_widen_medium_v2  # for the anchors' NEW text
    # Reverse by re-deriving: patch(base) == live, so undo each replacement.
    for old, new in _V2_EDIT_PAIRS:
        assert new in out, f"live prompt missing recalibrated text: {new[:60]!r}"
        out = out.replace(new, old, 1)
    return out


def _patch_live_base(_: str) -> str:
    """Ignore the recorded prompt; replay against the live prompt WITHOUT v2."""
    return _live_prompt_base()


def _patch_live_v2(_: str) -> str:
    """Ignore the recorded prompt; replay against the live prompt WITH v2."""
    from agents.deep_search_v4.reg_compliance_search.prompts import get_reranker_prompt

    return get_reranker_prompt("prompt_1")



def _record_v2_pairs() -> None:
    """Populate ``_V2_EDIT_PAIRS`` by diffing a probe prompt through the patch.

    Each anchor is replaced independently by the patch chain, so patching a
    probe that contains all five (separated by a unique marker) and splitting on
    that marker recovers the (old, new) pairs exactly — no literal duplication.
    """
    sep = "\n@@PAIR@@\n"
    olds = [_A_KEEPWORTHY, _A_IFFAIL, _A_SCARCITY, _A_SAMEPARENT, _A_SHORTLIST]
    news = _patch_widen_medium_v2(sep.join(olds)).split(sep)
    assert len(news) == len(olds), "v2 patch changed the probe's separator structure"
    _V2_EDIT_PAIRS.clear()
    _V2_EDIT_PAIRS.extend(zip(olds, news))


_record_v2_pairs()


PROMPT_PATCHES = {
    "widen_medium": _patch_widen_medium,
    "widen_medium_v2": _patch_widen_medium_v2,
    # Replay against TODAY'S live prompt instead of the recorded one. Lower
    # fidelity (the recorded user messages predate the circular/service/repeal
    # sections the live prompt describes) but it answers a different question:
    # does the recalibration behave the same inside the real, larger prompt?
    "live_base": _patch_live_base,
    "live_v2": _patch_live_v2,
}


def _split_arm(arm: str) -> tuple[str, str | None]:
    """``"qwen3.7-flash@widen_medium"`` -> ``("qwen3.7-flash", "widen_medium")``."""
    model, _, patch = arm.partition("@")
    return model, (patch or None)


def _arm_prompt(case: dict, arm: str) -> str:
    _, patch = _split_arm(arm)
    prompt = case["system_prompt"]
    return PROMPT_PATCHES[patch](prompt) if patch else prompt


def _build_agent(arm: str, system_prompt: str):
    """Build the replay agent for one arm (``model`` or ``model@patch``)."""
    model_key, _ = _split_arm(arm)
    from pydantic_ai import Agent, TextOutput

    from agents.deep_search_v4.reg_compliance_search.models import (
        RegRerankerClassification,
    )
    from agents.deep_search_v4.reg_compliance_search.reranker import (
        _REG_RERANKER_RETRY_MSG,
    )
    from agents.model_registry import create_model
    from agents.utils.structured_output import make_json_salvager

    return Agent(
        create_model(model_key),
        name=f"ab_{arm}",
        output_type=[
            RegRerankerClassification,
            TextOutput(
                make_json_salvager(
                    RegRerankerClassification, retry_msg=_REG_RERANKER_RETRY_MSG
                )
            ),
        ],
        instructions=system_prompt,
        retries=2,
        model_settings={
            "extra_body": {
                "enable_thinking": True,
                "thinking_budget": _THINKING_BUDGET,
            },
        },
    )


def _zero_keep_note(n_candidates: int) -> str:
    """The production zero-keep reconsider note, verbatim.

    ``reg_compliance_search/reranker.py`` appends this and re-runs ONCE when a
    pass keeps 0 of N>0 candidates. Replaying it here matters: without it a model
    that is merely trigger-happy about empty verdicts looks far worse than it
    behaves in the deployed loop.
    """
    return (
        f"\n\n---\nNote: you classified 0 of {n_candidates} candidates "
        f"as keep. If truly none apply, return an empty `keeps` list — "
        f"otherwise reconsider and keep the chunks whose parent system "
        f"scope governs the sub-query."
    )


async def _run_one(case: dict[str, Any], model_key: str, sem: asyncio.Semaphore) -> RunResult:
    from pydantic_ai.usage import UsageLimits

    async with sem:
        agent = _build_agent(model_key, _arm_prompt(case, model_key))
        limits = UsageLimits(
            output_tokens_limit=_OUTPUT_TOKENS_LIMIT, request_limit=_REQUEST_LIMIT
        )
        t0 = time.perf_counter()
        try:
            res = await agent.run(case["user_message"], usage_limits=limits)
            first_keeps = len(res.output.keeps)
            nudged = False
            if first_keeps == 0 and case["n_candidates"] > 0:
                # Production replays the whole call with the note appended.
                nudged = True
                res = await agent.run(
                    case["user_message"] + _zero_keep_note(case["n_candidates"]),
                    usage_limits=limits,
                )
        except Exception as exc:  # noqa: BLE001 - a model failure IS a datapoint
            dt = time.perf_counter() - t0
            print(f"  x {case['case_id']:<48} {model_key:<14} FAIL {type(exc).__name__}")
            return RunResult(
                case_id=case["case_id"],
                model=model_key,
                ok=False,
                error=f"{type(exc).__name__}: {exc}"[:400],
                duration_s=round(dt, 3),
            )
        dt = time.perf_counter() - t0
        usage = res.usage()
        details = getattr(usage, "details", None) or {}
        # The salvager path finalises as text, so no final_result tool_call is
        # present in the message history — that is how we detect it fired.
        salvaged = not any(
            getattr(p, "part_kind", "") == "tool-call"
            and getattr(p, "tool_name", "") == "final_result"
            for m in res.all_messages()
            for p in getattr(m, "parts", [])
        )
        out = res.output.model_dump()
        print(
            f"  . {case['case_id']:<48} {model_key:<14} "
            f"{dt:6.1f}s keeps={len(out.get('keeps', []))}/{case['n_candidates']}"
            f"{' NUDGED' if nudged else ''}{' SALVAGED' if salvaged else ''}"
        )
        return RunResult(
            case_id=case["case_id"],
            model=model_key,
            ok=True,
            duration_s=round(dt, 3),
            input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(usage, "output_tokens", 0) or 0),
            reasoning_tokens=int(details.get("reasoning_tokens", 0) or 0),
            requests=int(getattr(usage, "requests", 0) or 0),
            salvaged=salvaged,
            nudged=nudged,
            first_pass_keeps=first_keeps,
            output=out,
        )


async def _run_all(cases: list[dict], models: list[str], concurrency: int) -> list[RunResult]:
    sem = asyncio.Semaphore(concurrency)
    tasks = [_run_one(c, m, sem) for m in models for c in cases]
    return list(await asyncio.gather(*tasks))


def cmd_run(args: argparse.Namespace) -> None:
    cases_path, runs_path = _paths(args)
    cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        cases = cases[: args.limit]
    models = args.models.split(",")
    print(f"replaying {len(cases)} cases x {len(models)} models = {len(cases) * len(models)} calls")
    t0 = time.perf_counter()
    results = asyncio.run(_run_all(cases, models, args.concurrency))
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    mode = "a" if args.append else "w"
    with runs_path.open(mode, encoding="utf-8") as fh:
        for r in results:
            fh.write(json.dumps(asdict(r), ensure_ascii=False) + "\n")
    print(
        f"wall {time.perf_counter() - t0:.1f}s  "
        f"ok={sum(r.ok for r in results)}/{len(results)} -> {runs_path.relative_to(REPO)}"
    )


# ---------------------------------------------------------------- comparison


def _norm_label(raw: Any) -> str:
    """Normalise a keep label the way the live reranker does.

    ``reg_compliance_search/reranker.py`` strips brackets/whitespace before the
    ``by_label`` lookup, because both model generations sometimes copy the label
    in its header form (``[C7]``). Normalising identically here keeps the A/B
    measuring retrieval decisions rather than label cosmetics — the raw form is
    counted separately as a prompt-adherence signal.
    """
    return str(raw or "").strip().strip("[]").strip()


def _label_ord(label: str) -> int:
    m = re.match(r"C(\d+)$", label)
    return int(m.group(1)) if m else 10**6


def _keepmap(out: dict[str, Any]) -> dict[str, str]:
    """label -> relevance tier, for one reranker output."""
    m: dict[str, str] = {}
    for k in out.get("keeps") or []:
        if isinstance(k, dict) and k.get("label"):
            m[_norm_label(k["label"])] = str(k.get("relevance", "")).strip()
    return m


def _bracketed(out: dict[str, Any]) -> int:
    """How many keeps in this output used the raw ``[Cn]`` header form."""
    return sum(
        1
        for k in (out.get("keeps") or [])
        if isinstance(k, dict) and str(k.get("label", "")).strip().startswith("[")
    )


def _unknown_labels(out: dict[str, Any], valid: list[str]) -> list[str]:
    """Keeps that name a label not in the candidate set — silently dropped live."""
    ok = set(valid)
    return [
        _norm_label(k.get("label"))
        for k in (out.get("keeps") or [])
        if isinstance(k, dict) and _norm_label(k.get("label")) not in ok
    ]


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    return len(a & b) / len(a | b) if (a | b) else 1.0


def _price(model: str) -> tuple[float, float]:
    from agents.model_registry import get_model_config

    cfg = get_model_config(model)
    return float(cfg.input_price or 0.0), float(cfg.output_price or 0.0)


def cmd_compare(args: argparse.Namespace) -> None:
    base = getattr(args, "baseline", BASELINE_MODEL)
    cand = getattr(args, "candidate", CANDIDATE_MODEL)
    cases_path, runs_path = _paths(args)
    cases = {
        c["case_id"]: c
        for c in (json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip())
    }
    runs: dict[tuple[str, str], dict] = {}
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        runs[(r["case_id"], r["model"])] = r  # later lines win (re-runs)

    models = sorted({k[1] for k in runs})
    rows: list[dict[str, Any]] = []
    for cid, case in cases.items():
        rec = _keepmap(case["recorded"])
        row: dict[str, Any] = {
            "case_id": cid,
            "stage": case["stage"],
            "n": case["n_candidates"],
            "recorded_keeps": len(rec),
            "recorded_sufficient": bool(case["recorded"].get("sufficient")),
        }
        for m in models:
            r = runs.get((cid, m))
            if not r:
                continue
            km = _keepmap(r.get("output") or {})
            row[m] = {
                "ok": r["ok"],
                "error": r.get("error", ""),
                "keeps": len(km),
                "labels": km,
                "sufficient": bool((r.get("output") or {}).get("sufficient")),
                "duration_s": r["duration_s"],
                "in": r["input_tokens"],
                "out": r["output_tokens"],
                "reasoning": r["reasoning_tokens"],
                "requests": r["requests"],
                "salvaged": r["salvaged"],
                "nudged": r.get("nudged", False),
                "first_pass_keeps": r.get("first_pass_keeps"),
                "j_recorded": round(_jaccard(set(km), set(rec)), 3),
                "bracketed": _bracketed(r.get("output") or {}),
                "unknown": _unknown_labels(r.get("output") or {}, case["labels"]),
            }
        if base in row and cand in row:
            a = set(row[base]["labels"])
            b = set(row[cand]["labels"])
            row["j_pair"] = round(_jaccard(a, b), 3)
            row["only_35"] = sorted(a - b, key=_label_ord)
            row["only_37"] = sorted(b - a, key=_label_ord)
            row["tier_flips"] = sorted(
                lbl
                for lbl in (a & b)
                if row[base]["labels"][lbl] != row[cand]["labels"][lbl]
            )
        rows.append(row)

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    (OUT_DIR / "comparison.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- aggregate ----
    print("\n" + "=" * 92)
    print(f"{'metric':<34}" + "".join(f"{m:>19}" for m in models))
    print("=" * 92)

    def agg(fn, fmt="{:.2f}", only_ok=True):
        cells = []
        for m in models:
            vals = [fn(r[m]) for r in rows if m in r and (r[m]["ok"] or not only_ok)]
            vals = [v for v in vals if v is not None]
            cells.append(fmt.format(sum(vals) / len(vals)) if vals else "-")
        return "".join(f"{c:>19}" for c in cells)

    n_rows = len(rows)
    print(f"{'cases':<34}" + "".join(
        f"{sum(1 for r in rows if m in r):>19}" for m in models))
    print(f"{'  succeeded':<34}" + "".join(
        f"{sum(1 for r in rows if m in r and r[m]['ok']):>19}" for m in models))
    print(f"{'  salvager fired':<34}" + "".join(
        f"{sum(1 for r in rows if m in r and r[m].get('salvaged')):>19}" for m in models))
    print(f"{'  >1 request (retry)':<34}" + "".join(
        f"{sum(1 for r in rows if m in r and r[m].get('requests', 0) > 1):>19}" for m in models))
    print(f"{'  zero-keep 1st pass -> nudged':<34}" + "".join(
        f"{sum(1 for r in rows if m in r and r[m].get('nudged')):>19}" for m in models))
    print(f"{'  still zero after nudge':<34}" + "".join(
        f"{sum(1 for r in rows if m in r and r[m].get('nudged') and r[m]['keeps'] == 0):>19}" for m in models))
    print(f"{'  keeps using raw [Cn] form':<34}" + "".join(
        f"{sum(r[m].get('bracketed', 0) for r in rows if m in r):>19}" for m in models))
    print(f"{'  keeps naming unknown label':<34}" + "".join(
        f"{sum(len(r[m].get('unknown', [])) for r in rows if m in r):>19}" for m in models))
    print("-" * 92)
    print(f"{'avg keeps / call':<34}" + agg(lambda d: d["keeps"]))
    avg_n = sum(r["n"] for r in rows) / n_rows if n_rows else 0
    print(f"{'avg candidates / call':<34}" + f"{avg_n:>19.2f}")
    print(f"{'avg keep rate':<34}" + "".join(
        f"{(sum(r[m]['keeps'] for r in rows if m in r and r[m]['ok']) / max(1, sum(r['n'] for r in rows if m in r and r[m]['ok']))):>19.3f}"
        for m in models))
    print(f"{'sufficient=true rate':<34}" + agg(lambda d: 1.0 if d["sufficient"] else 0.0, "{:.3f}"))
    print(f"{'Jaccard vs recorded 3.5':<34}" + agg(lambda d: d["j_recorded"], "{:.3f}"))
    print("-" * 92)
    print(f"{'avg latency (s)':<34}" + agg(lambda d: d["duration_s"], "{:.1f}"))
    print(f"{'avg input tokens':<34}" + agg(lambda d: d["in"], "{:.0f}"))
    print(f"{'avg output tokens':<34}" + agg(lambda d: d["out"], "{:.0f}"))
    print(f"{'avg reasoning tokens':<34}" + agg(lambda d: d["reasoning"], "{:.0f}"))

    costs = []
    for m in models:
        pin, pout = _price(m)
        tot = 0.0
        k = 0
        for r in rows:
            if m in r and r[m]["ok"]:
                tot += (r[m]["in"] * pin + (r[m]["out"] + r[m]["reasoning"]) * pout) / 1e6
                k += 1
        costs.append(tot / k if k else 0.0)
    print(f"{'avg cost / call (USD)':<34}" + "".join(f"{c:>19.6f}" for c in costs))
    if len(costs) == 2 and costs[0]:
        print(f"{'  cost ratio (cand/base)':<34}" + f"{costs[1] / costs[0]:>19.3f}")

    pairs = [r for r in rows if "j_pair" in r]
    if pairs:
        print("-" * 92)
        print(f"{'pairwise keep-set Jaccard':<34}"
              f"{sum(r['j_pair'] for r in pairs) / len(pairs):>19.3f}")
        print(f"{'identical keep sets':<34}"
              f"{sum(1 for r in pairs if r['j_pair'] == 1.0):>13}/{len(pairs)}")
        print(f"{'sufficient flag agrees':<34}"
              f"{sum(1 for r in pairs if r[base]['sufficient'] == r[cand]['sufficient']):>13}/{len(pairs)}")
        print(f"{'kept only by 3.5 (total)':<34}"
              f"{sum(len(r['only_35']) for r in pairs):>19}")
        print(f"{'kept only by 3.7 (total)':<34}"
              f"{sum(len(r['only_37']) for r in pairs):>19}")
        print(f"{'relevance-tier flips (total)':<34}"
              f"{sum(len(r['tier_flips']) for r in pairs):>19}")
    print("=" * 92)

    print("\nper-case keep sets (n = candidates)")
    print(f"{'case':<50}{'n':>4}{'rec':>5}{'3.5':>5}{'3.7':>5}{'J':>7}  divergence")
    for r in sorted(rows, key=lambda x: x.get("j_pair", 1.0)):
        if "j_pair" not in r:
            continue
        div = []
        if r["only_35"]:
            div.append("-3.7 drops " + ",".join(r["only_35"]))
        if r["only_37"]:
            div.append("+3.7 adds " + ",".join(r["only_37"]))
        if r["tier_flips"]:
            div.append("tier " + ",".join(r["tier_flips"]))
        print(
            f"{r['case_id']:<50}{r['n']:>4}{r['recorded_keeps']:>5}"
            f"{r[base]['keeps']:>5}{r[cand]['keeps']:>5}"
            f"{r['j_pair']:>7.2f}  {'; '.join(div)}"
        )
    print(f"\nfull detail -> {(OUT_DIR / 'comparison.json').relative_to(REPO)}")


# --------------------------------------------------- downstream ground truth


def _downstream_path(cases_path: Path) -> Path:
    """DB snapshot path, scoped to its cases file.

    One shared snapshot silently clobbers another corpus's ground truth when you
    ``--refresh`` a different cases file, which scores the previous corpus as
    0/0. Deriving the name from the cases file keeps corpora independent.
    """
    return OUT_DIR / f"downstream_{cases_path.stem}.json"


def _norm_ws(s: Any) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()


def cmd_downstream(args: argparse.Namespace) -> None:
    """Score both arms against what the aggregator ACTUALLY used.

    Keep-set agreement says nothing about whether a dropped chunk mattered. The
    DB knows: ``reranker_runs`` holds each run's kept/dropped candidates with
    real ``ref_id``s, ``retrieval_artifacts`` maps a run's ``ura_id`` to the
    published workspace item, and ``workspace_item_references.used`` says which
    references the final answer actually leaned on.

    Two things this must get right:

    * **Union, not per-run.** deep_search fans out ~8 sub-queries and UNIONS the
      keeps. A chunk dropped in one run but kept in another still reaches the
      aggregator, so per-run counting overstates the loss. Scoring is per
      conversation, over the union.
    * **Survivorship bias, stated not hidden.** The ``used`` set can only contain
      chunks the PRODUCTION qwen3.5-flash reranker surfaced, so a candidate model
      earns no credit for keeping a different-but-equivalent chunk. It is a
      recall floor for the candidate, not a neutral gold set.

    Labels are ephemeral per run, so replay labels map to ``ref_id`` by chunk
    TITLE over the run's full candidate set (kept + dropped). Titles ambiguous
    within a run are skipped rather than guessed.
    """
    from shared.db.client import get_supabase_client

    cases_path, runs_path = _paths(args)
    cases = [json.loads(l) for l in cases_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    convos = sorted({c["convo"].replace("convo_", "") for c in cases})

    snapshot = _downstream_path(cases_path)
    if args.refresh or not snapshot.exists():
        sb = get_supabase_client()
        ra = []
        for i in range(0, len(convos), 50):
            ra += (sb.table("retrieval_artifacts")
                     .select("ura_id,conversation_id,artifact_id")
                     .in_("conversation_id", convos[i:i + 50]).execute().data)
        ura_ids = [r["ura_id"] for r in ra]
        art_ids = [r["artifact_id"] for r in ra if r["artifact_id"]]
        payload = {
            "ra": ra,
            "runs": [], "refs": [],
        }
        for i in range(0, len(ura_ids), 50):
            payload["runs"] += (sb.table("reranker_runs")
                                  .select("run_id,ura_id,agent_family,sub_query_index,"
                                          "sub_query_text,kept_results,dropped_results,sufficient")
                                  .in_("ura_id", ura_ids[i:i + 50]).execute().data)
        for i in range(0, len(art_ids), 50):
            payload["refs"] += (sb.table("workspace_item_references")
                                  .select("wi_id,ref_id,n,relevance,used,sub_queries")
                                  .in_("wi_id", art_ids[i:i + 50]).execute().data)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        snapshot.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    d = json.loads(snapshot.read_text(encoding="utf-8"))

    ura2conv = {r["ura_id"]: r["conversation_id"] for r in d["ra"]}
    ura2art = {r["ura_id"]: r["artifact_id"] for r in d["ra"]}
    # workspace_item_references.ref_id carries a domain prefix ("reg:<uuid>");
    # reranker_runs stores the bare uuid.
    wiref = {(w["wi_id"], w["ref_id"].split(":", 1)[-1]): w for w in d["refs"]}
    runidx = {}
    for r in d["runs"]:
        runidx.setdefault((ura2conv[r["ura_id"]], _norm_ws(r["sub_query_text"])), []).append(r)

    runs = {}
    for line in runs_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            runs[(r["case_id"], r["model"])] = r

    models = [getattr(args, "baseline", BASELINE_MODEL),
              getattr(args, "candidate", CANDIDATE_MODEL)]
    uni = {}
    covered = set()
    ambiguous = 0
    for c in cases:
        conv = c["convo"].replace("convo_", "")
        if c.get("label_refs"):
            # DB-reconstructed case: the label -> ref_id map was recorded at
            # build time, so the join is exact — no title matching, no ambiguity.
            cand_map = dict(c["label_refs"])

            def refs_for(labels, _m=cand_map):
                return {_m[l] for l in labels if l in _m}

            u = uni.setdefault(c["artifact_id"], {**{m: set() for m in models},
                                                   "all": set(), "rec": set(),
                                                   "art": set(), "conv": conv})
            for m in models:
                u[m] |= refs_for(set(_keepmap(runs[(c["case_id"], m)].get("output") or {})))
            u["rec"] |= refs_for({_norm_label(k["label"]) for k in c["recorded"]["keeps"]})
            u["all"] |= refs_for(set(cand_map))
            u["art"].add(c["artifact_id"])
            # Record this case's own run so the "recoverable from an unreplayed
            # run" column means what it says (db case_ids are "db:<run_id[:8]>").
            covered.add(c["case_id"].split(":", 1)[1])
            continue
        sq = _norm_ws(c["user_message"].split("\n")[1])
        lst = runidx.get((conv, sq))
        if not lst:
            continue
        run = lst[0]
        covered.add(run["run_id"])
        cand, dup = {}, set()
        for e in (run["kept_results"] or []) + (run["dropped_results"] or []):
            t = _norm_ws(e.get("title"))
            if t in cand and cand[t] != e["ref_id"]:
                dup.add(t)
            cand[t] = e["ref_id"]
        ambiguous += len(dup)
        lab2title = {m.group(1): _norm_ws(m.group(2))
                     for m in re.finditer(r"^### \[(C\d+)\]\s*(.*)$", c["user_message"], re.M)}

        def refs_for(labels):
            return {cand[lab2title[l]] for l in labels
                    if l in lab2title and lab2title[l] in cand and lab2title[l] not in dup}

        u = uni.setdefault(ura2art[run["ura_id"]], {**{m: set() for m in models},
                                                     "all": set(), "rec": set(),
                                                     "art": set(), "conv": conv})
        for m in models:
            u[m] |= refs_for(set(_keepmap(runs[(c["case_id"], m)].get("output") or {})))
        u["rec"] |= refs_for({_norm_label(k["label"]) for k in c["recorded"]["keeps"]})
        u["all"] |= refs_for(set(lab2title))
        u["art"].add(ura2art[run["ura_id"]])
        covered.add(run["run_id"])

    unreplayed = [r for r in d["runs"]
                  if r["run_id"] not in covered and r["run_id"][:8] not in covered]
    print(f"runs replayed: {len(covered)}/{len(d['runs'])}  "
          f"(ambiguous titles skipped: {ambiguous})")

    print("\n" + "=" * 96)
    print("UNION PER CONVERSATION - what the aggregator would actually have received")
    print("=" * 96)
    hdr = f"{'conversation':<15}{'cands':>6}{'USED':>6}{'rec':>6}"
    hdr += "".join(f"{m.replace('qwen', ''):>8}" for m in models)
    hdr += f"{'used&base':>11}{'used&cand':>11}"
    print(hdr)
    tot = collections.Counter()
    missed = []
    for key, u in sorted(uni.items(), key=lambda kv: str(kv[1].get("conv", kv[0]))):
        conv = str(u.get("conv", key))
        art = next(iter(u["art"]))
        used = {x for x in u["all"] if wiref.get((art, x), {}).get("used")}
        tot["used"] += len(used)
        tot["rec"] += len(u["rec"] & used)
        for m in models:
            tot[m] += len(u[m] & used)
            tot["kept_" + m] += len(u[m])
        for x in sorted(used - u[models[1]]):
            w = wiref[(art, x)]
            missed.append((str(u.get("conv", conv))[:12], x[:8], w["relevance"], w["n"],
                           any(e["ref_id"] == x
                               for r in unreplayed if ura2conv[r["ura_id"]] == conv
                               for e in (r["kept_results"] or []) + (r["dropped_results"] or [])),
                           x in u[models[0]]))
        row = f"{str(u.get('conv', conv))[:13]:<15}{len(u['all']):>6}{len(used):>6}{len(u['rec']):>6}"
        row += "".join(f"{len(u[m]):>8}" for m in models)
        row += f"{len(u[models[0]] & used):>11}{len(u[models[1]] & used):>11}"
        print(row)
    print("-" * 96)
    n_used = tot["used"] or 1
    print(f"\nrecall of USED refs - recorded       {tot['rec']:>2}/{tot['used']} = {tot['rec'] / n_used:.2f}")
    for m in models:
        print(f"recall of USED refs - {m:<14} {tot[m]:>2}/{tot['used']} = {tot[m] / n_used:.2f}"
              f"   (kept {tot['kept_' + m]} distinct chunks, "
              f"precision proxy {tot[m] / max(1, tot['kept_' + m]):.2f})")

    if missed:
        print(f"\nUSED chunks {models[1]} would have missed ({len(missed)}):")
        print(f"  {'conv':<14}{'ref':<10}{'rel':<8}{'n':>3}  {'in unreplayed run?':<20}base kept it?")
        for m in missed:
            print(f"  {m[0]:<14}{m[1]:<10}{m[2]:<8}{m[3]:>3}  {str(m[4]):<20}{m[5]}")
        print(f"  -> recoverable from an unreplayed run: {sum(1 for m in missed if m[4])}")
        print(f"  -> lost head-to-head (base kept it in the SAME run): "
              f"{sum(1 for m in missed if m[5])}")

    mix = collections.Counter()
    for _key, u in uni.items():
        art = next(iter(u["art"]))
        for x in u["all"]:
            w = wiref.get((art, x))
            if w and w["used"]:
                mix[w["relevance"]] += 1
    print(f"\nrelevance mix of USED refs: {dict(mix)}")
    print("  (scoring recall against `high` keeps alone understates the loss - "
          "the aggregator cites plenty of `medium`.)")


# ---------------------------------------------------- DB-reconstructed cases
#
# The dump corpus is exact but tiny (5 conversations, 27 gold refs) because only
# 52 of 355 local dumps postdate the keep-only contract. The DB holds far more:
# ~389 usable URAs / 3493 runs / ~3986 answer-bearing refs. `extract_db`
# reconstructs replayable cases from `reranker_runs` by re-rendering each stored
# candidate through the PRODUCTION renderer (`_make_block` +
# `build_reranker_user_message`), so block shape and wording are the real thing.
#
# What is NOT recoverable, and how it is handled:
#   * candidate ORDER and per-row RRF are not stored. Ordering by kept-then-
#     dropped would hand the answer to the model, so order is a deterministic
#     hash of (ura_id, ref_id) — stable, identical across arms, uncorrelated
#     with the verdict. RRF is a synthetic descending sequence.
#   * the precise/simple split follows production's rule (top-5 CHUNK rows
#     precise) applied to that reconstructed order.
# So a reconstructed case is a fair A/B input but NOT a byte-replay of the
# original turn. `validate_db` measures the cost of that by scoring the
# reconstruction against the exact dumps on the conversations they share.
#
# One thing reconstruction does BETTER: the label -> ref_id map is recorded at
# build time, so the downstream join is exact instead of title-matched.

_SYNTH_RRF_TOP = 0.90
_SYNTH_RRF_STEP = 0.03
_PRECISE_BAND = 5          # mirrors reg_compliance_search/search.py
_MIN_FETCH_RATIO = 0.8     # corpus churn retires chunks; skip gutted runs


def _stable_order(ura_id: str, ref_id: str) -> str:
    import hashlib

    return hashlib.md5(f"{ura_id}:{ref_id}".encode()).hexdigest()


def cmd_extract_db(args: argparse.Namespace) -> None:
    from agents.deep_search_v4.reg_compliance_search.prompts import (
        build_reranker_user_message,
    )
    from agents.deep_search_v4.reg_compliance_search.reranker import (
        _CHUNK_SOURCE_TYPES,
        _make_block,
    )
    from agents.deep_search_v4.reg_compliance_search.unfold_reranker import CHUNK_SELECT
    from shared.db.client import get_supabase_client

    sb = get_supabase_client()

    ra = (sb.table("retrieval_artifacts")
            .select("ura_id,conversation_id,artifact_id,created_at")
            .not_.is_("artifact_id", "null").is_("deleted_at", "null")
            .order("created_at", desc=True).limit(args.uras).execute().data)
    ura_ids = [r["ura_id"] for r in ra]
    art_of = {r["ura_id"]: r["artifact_id"] for r in ra}
    conv_of = {r["ura_id"]: r["conversation_id"] for r in ra}
    print(f"URAs considered: {len(ra)}")

    # Only URAs whose workspace item actually used something can score recall.
    refs = []
    for i in range(0, len(ura_ids), 50):
        art_batch = [art_of[u] for u in ura_ids[i:i + 50]]
        refs += (sb.table("workspace_item_references")
                   .select("wi_id,ref_id,used").in_("wi_id", art_batch).execute().data)
    used_by_art: dict[str, set] = {}
    for w in refs:
        if w["used"]:
            used_by_art.setdefault(w["wi_id"], set()).add(w["ref_id"].split(":", 1)[-1])
    keep_uras = [u for u in ura_ids if used_by_art.get(art_of[u])]
    print(f"URAs with >=1 used ref: {len(keep_uras)}  "
          f"(gold used refs: {sum(len(used_by_art[art_of[u]]) for u in keep_uras)})")

    runs = []
    for i in range(0, len(keep_uras), 50):
        runs += (sb.table("reranker_runs")
                   .select("run_id,ura_id,agent_family,sub_query_index,sub_query_text,"
                           "sub_query_rationale,kept_results,dropped_results,sufficient")
                   .in_("ura_id", keep_uras[i:i + 50])
                   .eq("agent_family", args.family).execute().data)
    print(f"{args.family} runs: {len(runs)}")

    # Two sampling modes.
    #   default        — shuffle runs and take `--n` of them. Broad sub-query
    #                    diversity, but each conversation contributes only a
    #                    fraction of its runs, so the per-conversation UNION is
    #                    incomplete and absolute recall is understated (equally
    #                    for every arm, so relative comparison still holds).
    #   --complete-uras N — take EVERY run of N conversations. The union then
    #                    matches what the aggregator really received, which is
    #                    what the downstream recall metric is defined against.
    if args.complete_uras:
        chosen = keep_uras[: args.complete_uras]
        runs = [r for r in runs if r["ura_id"] in chosen]
        runs.sort(key=lambda r: (r["ura_id"], r["sub_query_index"]))
        target = len(runs)
        print(f"complete-URA mode: {len(chosen)} conversations, {len(runs)} runs")
    else:
        random.Random(args.seed).shuffle(runs)
        target = args.n or len(runs)

    cases: list[dict] = []
    skipped = collections.Counter()
    for run in runs:
        if len(cases) >= target:
            break
        cands = (run["kept_results"] or []) + (run["dropped_results"] or [])
        if len(cands) < args.min_candidates:
            skipped["too_few_candidates"] += 1
            continue
        kept_ids = {c["ref_id"] for c in (run["kept_results"] or [])}
        rel_of = {c["ref_id"]: c.get("relevance") or "medium"
                  for c in (run["kept_results"] or [])}
        by_ref = {c["ref_id"]: c for c in cands}
        chunk_ids = [c["ref_id"] for c in cands
                     if c.get("source_type") in _CHUNK_SOURCE_TYPES]
        rows = {}
        for i in range(0, len(chunk_ids), 50):
            for r in (sb.table("chunks_v2").select(CHUNK_SELECT)
                        .in_("id", chunk_ids[i:i + 50]).execute().data):
                rows[r["id"]] = r
        if not chunk_ids or len(rows) / len(chunk_ids) < _MIN_FETCH_RATIO:
            skipped["corpus_rows_retired"] += 1
            continue

        ordered = sorted(rows, key=lambda rid: _stable_order(run["ura_id"], rid))
        blocks, label_refs = [], {}
        for i, ref_id in enumerate(ordered):
            label = f"C{i + 1}"
            row = dict(rows[ref_id])
            row["source_type"] = by_ref[ref_id].get("source_type") or "regulation"
            mode = "precise" if i < _PRECISE_BAND else "simple"
            rrf = round(_SYNTH_RRF_TOP - i * _SYNTH_RRF_STEP, 4)
            try:
                blocks.append(_make_block(sb, row, label, mode, rrf)["markdown"])
            except Exception:  # noqa: BLE001 - one bad row must not kill the sweep
                skipped["render_error"] += 1
                continue
            label_refs[label] = ref_id
        if len(label_refs) < args.min_candidates:
            skipped["too_few_rendered"] += 1
            continue

        md = f"## نتائج البحث — {len(blocks)} مقطعاً\n\n" + "\n\n".join(blocks)
        user_msg = build_reranker_user_message(
            run["sub_query_text"], run["sub_query_rationale"] or "", md,
        )
        recorded_keeps = [
            {"label": lb, "relevance": rel_of.get(rid, "medium"),
             "reasoning": by_ref[rid].get("reasoning", ""), "satisfies_axes": []}
            for lb, rid in label_refs.items() if rid in kept_ids
        ]
        cases.append({
            "case_id": f"db:{run['run_id'][:8]}",
            "convo": f"convo_{conv_of[run['ura_id']]}",
            "call_no": str(run["sub_query_index"]),
            "stage": run["agent_family"],
            "recorded_model": "qwen3.5-flash",
            "source": "db",
            "ura_id": run["ura_id"],
            "artifact_id": art_of[run["ura_id"]],
            "system_prompt": _live_prompt_base(),
            "user_message": user_msg,
            "n_candidates": len(label_refs),
            "labels": sorted(label_refs, key=_label_ord),
            "label_refs": label_refs,
            "recorded": {
                "sufficient": bool(run["sufficient"]), "query_axes": [],
                "keeps": recorded_keeps, "summary_note": "",
            },
            "recorded_tokens": {"input": 0, "output": 0, "reasoning": 0},
            "recorded_duration_s": None,
        })

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / args.out
    with out.open("w", encoding="utf-8") as fh:
        for c in cases:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    print(f"skipped: {dict(skipped)}")
    print(f"wrote {len(cases)} cases -> {out.relative_to(REPO)} "
          f"({len({c['convo'] for c in cases})} conversations, "
          f"{sum(c['n_candidates'] for c in cases)} candidate blocks)")



def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract", help="lift replayable cases out of the monitor dumps")
    e.add_argument("--n", type=int, default=24)
    e.add_argument("--seed", type=int, default=7)
    e.set_defaults(fn=cmd_extract)

    ed = sub.add_parser(
        "extract_db",
        help="reconstruct replayable cases from reranker_runs (much larger N)",
    )
    ed.add_argument("--family", default="reg_search")
    ed.add_argument("--uras", type=int, default=200, help="most recent URAs to scan")
    ed.add_argument("--n", type=int, default=0, help="cap the emitted cases (0 = all)")
    ed.add_argument("--min-candidates", type=int, default=6)
    ed.add_argument("--out", default="cases_db.jsonl")
    ed.add_argument("--seed", type=int, default=7)
    ed.add_argument("--complete-uras", type=int, default=0,
                    help="replay EVERY run of the N most recent conversations "
                         "(complete per-conversation unions)")
    ed.set_defaults(fn=cmd_extract_db)

    r = sub.add_parser("run", help="replay the cases on each model")
    r.add_argument("--models", default=f"{BASELINE_MODEL},{CANDIDATE_MODEL}")
    r.add_argument("--concurrency", type=int, default=6)
    r.add_argument("--limit", type=int, default=0)
    r.add_argument("--append", action="store_true")
    r.add_argument("--cases", default=None, help="alternate cases jsonl (e.g. cases_db.jsonl)")
    r.add_argument("--runs", default=None, help="alternate runs jsonl")
    r.set_defaults(fn=cmd_run)

    c = sub.add_parser("compare", help="score the replays against each other")
    c.add_argument("--baseline", default=BASELINE_MODEL)
    c.add_argument("--candidate", default=CANDIDATE_MODEL)
    c.add_argument("--cases", default=None)
    c.add_argument("--runs", default=None)
    c.set_defaults(fn=cmd_compare)

    dn = sub.add_parser(
        "downstream",
        help="score both arms against what the aggregator actually USED (DB ground truth)",
    )
    dn.add_argument("--refresh", action="store_true", help="re-pull the DB snapshot")
    dn.add_argument("--baseline", default=BASELINE_MODEL)
    dn.add_argument("--candidate", default=CANDIDATE_MODEL)
    dn.add_argument("--cases", default=None)
    dn.add_argument("--runs", default=None)
    dn.set_defaults(fn=cmd_downstream)

    args = ap.parse_args()
    args.fn(args)


if __name__ == "__main__":
    main()
