"""The Completion Search agent: one tier_2 model, lookup tools, handles out (plan §4.2).

Every tool is word match only, records no search fee and cannot ask the user. Each
call for a search is one ATTEMPT; ``CompletionDeps.begin`` refuses a fourth attempt and
any attempt at a search that is already settled. The model's output selects handles —
it has no field that could carry legal text.
"""
from __future__ import annotations

import re
from typing import Any

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.usage import UsageLimits

from agents.completion_search.confidence import (
    HIGH,
    LOW,
    MAX_PHRASE_ARTICLES,
    GateResult,
    article_confidence,
    document_confidence,
    phrase_confidence,
    title_gate,
)
from agents.completion_search.deps import CompletionDeps
from agents.completion_search.lookups import LawLookup, clean_case_number, find_phrase
from agents.completion_search.models import (
    MAX_CANDIDATE_TITLES,
    MAX_ROUNDS,
    MAX_SEARCHES,
    HelperDecision,
    Pin,
)
from agents.completion_search.prompts import SYSTEM_PROMPT
from agents.utils.agent_models import ModelPolicy, get_agent_model
from agents.utils.usage_limits import OUTPUT_TOKENS_CAP

SLOT = "completion_search"

LIMITS = UsageLimits(
    output_tokens_limit=OUTPUT_TOKENS_CAP,   # the one shared cap — agents/utils/usage_limits.py
    request_limit=10,
    tool_calls_limit=MAX_SEARCHES * MAX_ROUNDS + 4,
)

_LAW_HANDLE_RE = re.compile(r"^L\d{1,3}$", re.I)
_SERVICE_WORD_RE = re.compile(r"^\s*(?:ال)?خدم[ةه]\s+")
_EXCERPT_CHARS = 120
_CONFIDENCE_AR = {"high": "ثقة عالية", "medium": "ثقة متوسطة", "low": "ثقة منخفضة"}
_LAST_ATTEMPT_AR = " (هذه آخر محاولة لهذا البحث.)"


def _excerpt(text: str, limit: int = _EXCERPT_CHARS) -> str:
    flat = " ".join((text or "").split())
    return flat if len(flat) <= limit else flat[:limit].rstrip() + "…"


def _titles_line(titles: list[str]) -> str:
    return "؛ ".join(f"«{t}»" for t in titles if t)


async def _law_for(deps: CompletionDeps, search_id: str, regulation: str) -> tuple[LawLookup | None, bool, str]:
    """Resolve the ``regulation`` argument: a name in words, or an ``L…`` handle.

    Returns ``(lookup, as_asked, message)``. ``message`` is set when there is no law to
    work with; the tool returns it as is. ``as_asked`` is False for a handle (the model
    chose among candidates) and for a name the request did not write.
    """
    raw = (regulation or "").strip()
    sid = deps.sid(search_id)
    tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
    if not raw:
        return None, False, "اكتب اسم النظام كما ورد في الطلب، أو رمز نظام (L…) من نتيجة سابقة."
    if _LAW_HANDLE_RE.match(raw):
        handle = deps.get(raw)
        if handle is None or handle.law is None:
            return None, False, f"الرمز {raw} ليس رمز نظام معروفًا. استخدم رمزًا أعادته أداة."
        return handle.law, False, ""

    lookup = await deps.library.find_law(raw)
    state = lookup.gate.state
    if state in ("exact", "best"):
        return lookup, deps.as_asked(raw), ""
    if state == "several":
        lines = []
        titles = []
        for cand in lookup.gate.shown[:MAX_CANDIDATE_TITLES]:
            one = LawLookup(GateResult("exact" if cand.exact else "best", cand, (cand,)), None, lookup.note)
            hid = deps.add_law(sid, cand.title, one)
            label = f" — {cand.label}" if cand.label else ""
            lines.append(f"{hid} — «{cand.title}»{label}")
            titles.append(cand.title)
        deps.hints[sid] = titles
        return None, False, (
            f"الاسم «{raw}» يحتمل أكثر من نظام:\n" + "\n".join(lines)
            + "\nإن كان أحدها هو المقصود بوضوح فأعد النداء واضعًا رمزه في خانة النظام، وإلا فلا تختر."
            + tail
        )
    near = [c.title for c in lookup.gate.shown[:MAX_CANDIDATE_TITLES]]
    deps.missing.add(sid)
    hint = f" أقرب العناوين في المكتبة: {_titles_line(near)}." if near else ""
    return None, False, f"لا يوجد في المكتبة نظام بالاسم «{raw}».{hint}{tail}"


def create_completion_agent(
    model_override: ModelPolicy | str | None = None,
    *,
    model: Any = None,
) -> Agent[CompletionDeps, HelperDecision]:
    """Build the agent. ``model`` hands in a ready pydantic-ai model (tests, evaluation)."""
    agent: Agent[CompletionDeps, HelperDecision] = Agent(
        model if model is not None else get_agent_model(SLOT, model_override),
        name="completion_search",
        deps_type=CompletionDeps,
        output_type=HelperDecision,
        instructions=SYSTEM_PROMPT,
        retries=2,
    )

    @agent.tool
    async def find_regulation(ctx: RunContext[CompletionDeps], search_id: str, name: str) -> str:
        """See which law or regulation a name means. Returns law handles (L1, L2 …), no text.

        Use it only when you must settle the law before anything else; `get_article` and
        `find_in_regulation` already resolve the law's name themselves.

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            name: the law's name, as the request wrote it on the first attempt.
        """
        deps = ctx.deps
        refusal = deps.begin(search_id)
        if refusal:
            return refusal
        lookup, _as_asked, message = await _law_for(deps, search_id, name)
        if lookup is None:
            return message
        law = lookup.law
        deps.missing.discard(deps.sid(search_id))
        hid = deps.add_law(search_id, law.title, lookup)
        kind = "تطابق تام للاسم" if lookup.gate.state == "exact" else "أقرب تطابق — تحقّق أنه المقصود"
        return f"{hid} — «{law.title}» — {kind}"

    @agent.tool
    async def get_article(
        ctx: RunContext[CompletionDeps], search_id: str, regulation: str, article_number: str
    ) -> str:
        """Find ONE article by its number inside a named law. Returns a text handle (T…).

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            regulation: the law's name as the request wrote it, or a law handle (L…) from an earlier result.
            article_number: the number as the request wrote it — «56», «٥٦» or «السادسة والخمسون».
        """
        deps = ctx.deps
        sid = deps.sid(search_id)
        refusal = deps.begin(sid)
        if refusal:
            return refusal
        lookup, as_asked, message = await _law_for(deps, sid, regulation)
        if lookup is None:
            return message
        law = lookup.law
        deps.missing.discard(sid)
        number = (article_number or "").strip()
        texts = await deps.library.article(lookup, number)
        if not texts:
            if not await deps.library.has_articles(lookup):
                deps.finish(sid)
                return f"«{law.title}» موجود في المكتبة، لكن نصوص مواده غير متوفرة فيها. لا نص يُعاد لهذا البحث."
            if lookup.gate.state == "exact" and as_asked:
                # The law is the one the request named; the number is simply not in it.
                deps.finish(sid)
                return f"المادة {number} غير موجودة في «{law.title}». لا تُعد المحاولة لهذا البحث."
            tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
            return f"المادة {number} غير موجودة في «{law.title}».{tail}"
        confidence = article_confidence(
            title_state=lookup.gate.state, as_asked=as_asked, in_force=lookup.in_force
        )
        lines = []
        for t in texts:
            note = " ".join(x for x in (t.note, "" if lookup.in_force else "تنبيه: هذا النص ليس نظامًا ساريًا (مشروع أو ملغى).") if x)
            pin = Pin(
                key=f"article:{t.article_id or t.regulation_id + ':' + t.number}",
                source_type="article",
                title=f"المادة {t.number} من {t.regulation_title}",
                source_title=t.regulation_title,
                text=t.content,
                confidence=confidence,
                note=note,
                regulation_id=t.regulation_id,
                article_id=t.article_id,
                article_number=t.number,
            )
            hid = deps.add_pin(sid, pin)
            lines.append(
                f"{hid} — المادة {t.number} من «{t.regulation_title}» — {_CONFIDENCE_AR[confidence]} — "
                f"أولها: «{_excerpt(t.content)}»"
            )
        return "\n".join(lines)

    @agent.tool
    async def find_in_regulation(
        ctx: RunContext[CompletionDeps],
        search_id: str,
        regulation: str,
        phrase: str,
        is_definition: bool = False,
    ) -> str:
        """Find the articles of a named law that hold a word or a phrase. Returns text handles (T…).

        Tashkeel is ignored. Search for the law's own wording: for a definition pass the TERM
        itself («البيع») with is_definition=true, not the word «تعريف».

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            regulation: the law's name as the request wrote it, or a law handle (L…).
            phrase: the word or phrase to find inside the law's articles.
            is_definition: true when the request asks where the law defines this term.
        """
        deps = ctx.deps
        sid = deps.sid(search_id)
        refusal = deps.begin(sid)
        if refusal:
            return refusal
        lookup, as_asked, message = await _law_for(deps, sid, regulation)
        if lookup is None:
            return message
        law = lookup.law
        deps.missing.discard(sid)
        wanted = (phrase or "").strip()
        tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
        if not wanted:
            return "اكتب الكلمة أو العبارة المطلوب البحث عنها." + tail
        hits = find_phrase(await deps.library.articles(lookup), wanted)
        whole = [h for h in hits if h.whole]
        if not hits:
            return (
                f"العبارة «{wanted}» لم ترد في مواد «{law.title}». "
                "جرّب لفظ النظام نفسه: الاسم بدل الفعل، أو بلا «ال»، أو عبارة أقصر." + tail
            )
        if not whole:
            numbers = "، ".join(h.article.number for h in hits[:MAX_PHRASE_ARTICLES])
            return (
                f"«{wanted}» وردت جزءًا من كلمات أطول فقط في «{law.title}» (المواد: {numbers}). "
                "لا نص مؤكدًا؛ جرّب عبارة أدق." + tail
            )
        definitions = [h for h in whole if h.definition_like]
        if is_definition and len(definitions) != 1:
            # Several articles open with the term («البيع بشرط…», «البيع عقد…»): the one that
            # goes on to say what the term IS is the definition.
            strong = [h for h in definitions if h.defines]
            if len(strong) == 1:
                definitions = strong
        confidence = phrase_confidence(
            title_state=lookup.gate.state, as_asked=as_asked, whole_hits=len(whole),
            definition_hits=len(definitions), is_definition=is_definition,
            phrase_as_asked=deps.as_asked(wanted), in_force=lookup.in_force,
        )
        if confidence == LOW:
            numbers = "، ".join(h.article.number for h in whole[:12])
            return (
                f"«{wanted}» وردت في {len(whole)} مادة من «{law.title}» (منها: {numbers}) — أكثر مما يصلح نتيجة. "
                "ضيّق العبارة." + tail
            )
        ordered = (definitions + [h for h in whole if h not in definitions]) if is_definition else whole
        single_definition = is_definition and len(definitions) == 1
        lines = []
        for h in ordered[:MAX_PHRASE_ARTICLES]:
            # For a definition request only the article that defines the term is sure;
            # the other articles merely mention it.
            level = confidence
            if single_definition and len(whole) > 1 and h not in definitions and confidence == HIGH:
                level = "medium"
            t = h.article
            pin = Pin(
                key=f"article:{t.article_id or t.regulation_id + ':' + t.number}",
                source_type="article",
                title=f"المادة {t.number} من {t.regulation_title}",
                source_title=t.regulation_title,
                text=t.content,
                confidence=level,
                note="" if lookup.in_force else "تنبيه: هذا النص ليس نظامًا ساريًا (مشروع أو ملغى).",
                regulation_id=t.regulation_id,
                article_id=t.article_id,
                article_number=t.number,
            )
            hid = deps.add_pin(sid, pin)
            mark = " — تعرّف المصطلح" if h in definitions else ""
            lines.append(f"{hid} — المادة {t.number} من «{t.regulation_title}»{mark} — «{h.excerpt}»")
        head = f"«{wanted}» وردت في {len(whole)} مادة من «{law.title}» — {_CONFIDENCE_AR[confidence]}:"
        return head + "\n" + "\n".join(lines)

    async def _document(deps: CompletionDeps, search_id: str, kind: str, words: str, owner: str) -> str:
        sid = deps.sid(search_id)
        refusal = deps.begin(sid)
        if refusal:
            return refusal
        noun = "تعميم" if kind == "circular" else "خدمة"
        wanted = (words or "").strip()
        if kind == "service":
            # «خدمة إصدار وكالة» — the word «خدمة» is not part of the service's name.
            wanted = _SERVICE_WORD_RE.sub("", wanted).strip()
        tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
        if not wanted:
            return f"اكتب عنوان ال{noun} أو كلماته." + tail
        if kind == "circular":
            candidates = await deps.library.circulars(wanted, owner or "")
        else:
            candidates = await deps.library.services(wanted, owner or "")
        gate = title_gate(candidates)
        if gate.state in ("exact", "best"):
            doc = gate.winner
            confidence = document_confidence(title_state=gate.state, as_asked=deps.as_asked(wanted))
            text = await deps.library.open_text(kind, doc.id, doc.title)
            if not text:
                return f"تعذّر فتح نص ال{noun} «{doc.title}»." + tail
            pin = Pin(
                key=f"{kind}:{doc.id}", source_type=kind, title=doc.title, source_title=doc.label,
                text=text, confidence=confidence,
                **({"circular_id": doc.id} if kind == "circular" else {"service_id": doc.id}),
            )
            hid = deps.add_pin(sid, pin)
            owner_part = f" — {doc.label}" if doc.label else ""
            return f"{hid} — «{doc.title}»{owner_part} — {_CONFIDENCE_AR[confidence]}"
        titles = [c.title for c in gate.shown[:MAX_CANDIDATE_TITLES]]
        if gate.state == "several":
            deps.hints[sid] = titles
            listed = "\n".join(
                f"- «{c.title}»" + (f" — {c.label}" if c.label else "") for c in gate.shown[:MAX_CANDIDATE_TITLES]
            )
            return (
                f"أكثر من {noun} يطابق «{wanted}»:\n{listed}\n"
                "إن كان أحدها هو المقصود بوضوح فأعد النداء بعنوانه كما هو مكتوب، وأضف الجهة إن عُرفت." + tail
            )
        hint = f" أقرب العناوين: {_titles_line(titles)}." if titles else ""
        return f"لا يوجد {noun} بهذه الكلمات «{wanted}».{hint}{tail}"

    @agent.tool
    async def find_circular(
        ctx: RunContext[CompletionDeps], search_id: str, words: str, issuer: str = ""
    ) -> str:
        """Find a circular (تعميم) by its title words. Returns a text handle (T…) or candidate titles.

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            words: the circular's title, or the words the request gives for it.
            issuer: the issuing body, when the request names it.
        """
        return await _document(ctx.deps, search_id, "circular", words, issuer)

    @agent.tool
    async def find_service(
        ctx: RunContext[CompletionDeps], search_id: str, name: str, provider: str = ""
    ) -> str:
        """Find a government service by its name. Returns a text handle (T…) or candidate names.

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            name: the service's name as the request wrote it.
            provider: the body that provides it, when the request names it.
        """
        return await _document(ctx.deps, search_id, "service", name, provider)

    @agent.tool
    async def find_case(
        ctx: RunContext[CompletionDeps], search_id: str, case_number: str, court: str = "", year: str = ""
    ) -> str:
        """Find a court ruling by its case number. Returns a text handle (T…) or the rulings that share the number.

        Only by NUMBER. A ruling cannot be found here by its subject or by the parties.

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            case_number: the case or judgment number exactly as the request wrote it.
            court: the court, when the request names it — it settles between rulings that share a number.
            year: the year (Hijri or Gregorian), when the request gives it.
        """
        deps = ctx.deps
        sid = deps.sid(search_id)
        refusal = deps.begin(sid)
        if refusal:
            return refusal
        number = (case_number or "").strip()
        tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
        if not clean_case_number(number):
            deps.finish(sid)
            return "هذا ليس رقم قضية صالحًا للبحث. البحث عن الأحكام هنا بالرقم فقط."
        found = await deps.library.cases(number, court or "", year or "")
        if not found:
            return f"لا يوجد في المكتبة حكم برقم القضية «{number}»." + tail
        if len(found) > 1:
            deps.hints[sid] = [c.title for c in found[:MAX_CANDIDATE_TITLES]]
            deps.hint_level[sid] = "medium"
            listed = "\n".join(f"- {c.title}" for c in found[:MAX_CANDIDATE_TITLES])
            more = f" (وعددها {len(found)})" if len(found) > MAX_CANDIDATE_TITLES else ""
            return (
                f"أكثر من حكم يحمل رقم القضية «{number}»{more}:\n{listed}\n"
                "إن ذكر الطلب المحكمة أو السنة فأعد النداء بهما لتحديد الحكم، وإلا فلا تختر." + tail
            )
        doc = found[0]
        text = await deps.library.open_text("case", doc.id, doc.title)
        if not text:
            return f"تعذّر فتح نص «{doc.title}»." + tail
        confidence = document_confidence(title_state="exact", as_asked=deps.as_asked(clean_case_number(number)))
        pin = Pin(key=f"case:{doc.id}", source_type="case", title=doc.title, source_title=doc.label,
                  text=text, confidence=confidence, case_id=doc.id)
        hid = deps.add_pin(sid, pin)
        return f"{hid} — {doc.title} — {_CONFIDENCE_AR[confidence]}"

    @agent.tool
    async def find_principle(
        ctx: RunContext[CompletionDeps], search_id: str, number: str = "", words: str = ""
    ) -> str:
        """Find a judicial principle (مبدأ قضائي) by its number, or by its chapter and words.

        Args:
            search_id: the id of the search this attempt belongs to, e.g. S1.
            number: the principle's number, when the request gives one.
            words: a few words of the principle or its chapter title, when there is no number.
        """
        deps = ctx.deps
        sid = deps.sid(search_id)
        refusal = deps.begin(sid)
        if refusal:
            return refusal
        tail = _LAST_ATTEMPT_AR if deps.last_round(sid) else ""
        number, words = (number or "").strip(), (words or "").strip()
        if not number and not words:
            return "اكتب رقم المبدأ، أو كلمات من نصه أو من عنوان بابه." + tail
        found = await deps.library.principles(number, words)
        gate = title_gate(found)
        if gate.state not in ("exact", "best"):
            titles = [c.title for c in gate.shown[:MAX_CANDIDATE_TITLES]]
            if gate.state == "several":
                deps.hints[sid] = titles
                listed = "\n".join(f"- {t}" for t in titles)
                return f"أكثر من مبدأ يطابق:\n{listed}\nاذكر رقم المبدأ، أو كلمات أدق من نصه." + tail
            return ("لا يوجد مبدأ قضائي بهذا الرقم." if number else "لا يوجد مبدأ قضائي بهذه الكلمات.") + tail
        doc = gate.winner
        confidence = document_confidence(
            title_state=gate.state, as_asked=deps.as_asked(number) if number else False
        )
        pin = Pin(key=f"principle:{doc.id}", source_type="principle", title=doc.title, source_title=doc.label,
                  text=doc.extra.get("text") or "", confidence=confidence,
                  principle_no=str(doc.extra.get("principle_no") or ""))
        hid = deps.add_pin(sid, pin)
        return f"{hid} — {doc.title} — {_CONFIDENCE_AR[confidence]} — أوله: «{_excerpt(pin.text)}»"

    @agent.output_validator
    async def _check(ctx: RunContext[CompletionDeps], value: HelperDecision) -> HelperDecision:
        deps = ctx.deps
        for search in value.searches:
            texts = 0
            for raw in search.selected:
                handle = deps.get(raw)
                if handle is None:
                    raise ModelRetry(
                        f"الرمز {raw!r} غير معروف. ضع في selected الرموز التي أعادتها الأدوات فقط (T… أو L…)."
                    )
                texts += int(handle.pin is not None)
            if search.status == "found" and not texts:
                raise ModelRetry(
                    f"البحث {search.search_id} حالته found بلا رمز نص (T…). اختر رمز النص، أو غيّر الحالة."
                )
        return value

    return agent


_AGENT: Agent[CompletionDeps, HelperDecision] | None = None


def get_completion_agent() -> Agent[CompletionDeps, HelperDecision]:
    """The process-wide agent (built on first use)."""
    global _AGENT
    if _AGENT is None:
        _AGENT = create_completion_agent()
    return _AGENT


__all__ = ["LIMITS", "SLOT", "create_completion_agent", "get_completion_agent"]
