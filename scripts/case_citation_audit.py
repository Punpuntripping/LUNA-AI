"""Weekly case-law citation audit (plan ``.claude/plans/reranker_decider_engine.md``, step 8 / D14).

Do the court rulings that deep_search answers cite actually support the sentence they are
attached to? Each week a sample of published deep_search answers that cite judgments is
split into the sentences carrying a ruling citation, every cited ruling gets its one-line
subject (``cases.court`` + ``cases.short_summary``, as in the case answer A/B), and blind
Sonnet judges (Claude Code sub-agents) say per citation: supported / not supported / unclear.

    sample   read-only Supabase queries → agents_reports/case_citation_audit/<run>/
             batch_<AID>.json (one answer per judge), judge_prompt.md, _key.json, _meta.json
    tally    verdict_<AID>.json → agents_reports/case_citation_audit/<run>.md, one row in
             agents_reports/case_citation_audit/log.jsonl, and an ``ALERT:`` line when the
             thresholds are crossed

Where the data comes from (production, SELECT only):
    workspace_items            agent_family='deep_search', not deleted, never edited
                               (prev_content_md IS NULL); content_md is the published answer
                               and its ``[n]`` / ``[n,m]`` tokens are the citations
    workspace_item_references  (wi_id, n, domain, ref_id): n anchors the [n] tokens;
                               domain='cases' + ref_id 'case:<case_ref>' = a cited judgment
    cases                      case_ref → court, short_summary (the ruling's subject line)
    llm_calls                  agent='deep_search.reranker.case' in the same conversation at
                               the answer's time → which reranker engine served the turn
                               (qwen3.5-flash, or liquid/d1-ultra / typesafe/jev-1.14 once
                               phase B is on). Kept in _key.json only, so judges stay blind.
    users, lawyer_cases        only to classify the account (real user / editorial bot /
                               internal @rayhanai.com, excluded) and to collect known names
                               to mask. No email or name is ever written.

Metrics (``tally``):
    mis-citation rate   cited rulings that do not support their sentence ÷ all cited rulings.
                        The unit is a ruling within one answer; it counts as unsupported if
                        ANY sentence it is attached to was judged not supported.
    answer share        answers with at least one unsupported ruling ÷ answers.
    Also reported: the per-citation rate, the unclear share, and splits by engine and source.

Alert (after phase B, plan D14): a non-baseline run whose rate is more than 1.5× the stored
baseline, or whose answer share is above 25% (``--share-max``). Compare only with a baseline
made by THIS job and the same ``PROMPT_VERSION`` (two judge groups flagged the same qwen
answers 12% and 23%, so cross-setup numbers mean nothing).

Masking: every text written (batches, report) goes through ``_mask`` first, the case A/B
pages' approach made automatic: ``[رقم]`` for numbers (the PDPL detector in
``shared/privacy/codec.py`` for 5+ digit identifiers, any 7+ digit run, and any number after
«رقم» that is not an article/decree number), ``[اسم]`` for names (the account's own name and
its lawyer_case name/parties, «X بن Y» chains, honorific + name, company names after
«شركة/مؤسسة/…», Latin proper names, plus an optional ``--names-file``). Then a leak check
refuses to write anything if a 7+ digit run, a PDPL candidate or a known name survives.
Names are heuristic: a name with no anchor («أحمد العتيبي» alone) can pass. That is why the
outputs stay in the git-ignored agents_reports/ and the routine relays numbers only.

Baseline, once, on qwen-era answers BEFORE ``CASE_RERANKER_ENGINE=decider`` (a bigger, steadier
sample; every turn so far is qwen-era):

    python scripts/case_citation_audit.py sample --label baseline-qwen --days 30 --limit 40
    (judge every batch as in step 3 below)
    python scripts/case_citation_audit.py tally --run <date>-baseline-qwen

Weekly routine (Claude Code ``/schedule``; the owner sets it up, this script schedules nothing):

    0. Environment: repo checkout with backend/requirements.txt installed; SUPABASE_URL,
       SUPABASE_SERVICE_KEY (the sb_secret_ key; the local .env one is stale since 2026-10-22),
       SUPABASE_ANON_KEY, SUPABASE_JWT_SECRET (``shared.config`` requires them). Locally:
       export SUPABASE_SERVICE_KEY="$(railway variables --service luna-backend --kv | grep '^SUPABASE_SERVICE_KEY=' | cut -d= -f2-)"
       Never print the value.
    1. PYTHONIOENCODING=utf-8 python scripts/case_citation_audit.py sample
       → last 7 days, up to 20 answers, label ``weekly``; prints ``RUN=<run>`` and the batch list.
    2. Nothing to judge (``answers=0``) → report "no case-law answers this week" and stop.
    3. For every agents_reports/case_citation_audit/<run>/batch_<AID>.json start one
       ``general-purpose`` agent with ``model: sonnet`` whose prompt is the text of
       <run>/judge_prompt.md with ``<AID>`` replaced; at most 10 at a time. Each writes
       <run>/verdict_<AID>.json. Re-run any judge whose verdict file is missing or invalid.
    4. PYTHONIOENCODING=utf-8 python scripts/case_citation_audit.py tally --run <run>
       (in a fresh cloud checkout log.jsonl is gone, because agents_reports/ is git-ignored:
       add ``--baseline-rate R --baseline-share S`` from the baseline run's SUMMARY line).
    5. Send the owner the ``SUMMARY:`` line, plus every ``ALERT:`` line when there are any.
       Numbers only: never paste batch, report or verdict text anywhere outside this machine.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")
os.environ.setdefault("LOGFIRE_SEND_TO_LOGFIRE", "false")
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

OUT = REPO / "agents_reports" / "case_citation_audit"
LOG_PATH = OUT / "log.jsonl"
PROMPT_VERSION = "v1"            # bump on any change to JUDGE_PROMPT or SUBJECT_CHARS; baselines don't carry over
SUBJECT_CHARS = 220              # the case answer A/B's one-line subject length
RATE_X, SHARE_MAX = 1.5, 0.25    # plan D14 alert thresholds
CASE_RERANKER = "deep_search.reranker.case"
BOT_PREFIX, INTERNAL_DOMAIN = "editorial-bot@", "@rayhanai.com"

_CITE_RE = re.compile(r"\[(\d+(?:\s*[,،]\s*\d+)*)\]")   # = aggregator/postvalidator._CITATION_RE
_THINK_RE = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.S | re.I)
_SENT_SPLIT = re.compile(r"(?<=[.!?؟؛])\s+")
_LEAD_CITES = re.compile(r"^((?:\[\d+(?:\s*[,،]\s*\d+)*\]\s*)+[.،,؛]?)\s*")
_LIST_MARK = re.compile(r"^\s*(?:>\s*)*(?:[-*+•]|\d{1,2}[.)]|[٠-٩]{1,2}[.)])\s+")


def _jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _sb():
    from shared.db.client import get_supabase_client

    return get_supabase_client()


def _chunks(xs: list, n: int = 100):
    for i in range(0, len(xs), n):
        yield xs[i:i + n]


# ------------------------------------------------------------------ masking

NUM_RE = re.compile(r"[0-9٠-٩]{7,}")   # the case A/B pages' rule; also the leak check
NUM_MASK, NAME_MASK, MAIL_MASK = "[رقم]", "[اسم]", "[بريد]"
_AR = "ء-غف-يً-ْٰ"   # letters + harakat, so «استناداً» is one token
_TOK = rf"[{_AR}]+"
_NB, _NA = rf"(?<![{_AR}])", rf"(?![{_AR}])"   # Arabic word boundaries
_TANWEEN = re.compile("[ً-ٍ]")   # «استناداً», «تعويضاً»: an adverb/accusative, never a name
_STOP = set("""و في من على عن إلى الى أن إن أنه انه بأن لأن الذي التي الذين اللذين قد لم لن لا ما هو هي هم
كان كانت يكون تكون عند بعد قبل مع بين حيث كما أو ثم حتى إذا اذا إلا الا غير كل بعض هذا هذه ذلك تلك ضد بشأن
بخصوص لدى لصالح وفق وفقا قام قامت تقدم تقدمت طالب طالبت ادعى ادعت أقام أقامت له لها لهم فيه فيها منه منها
عليه عليها إليه إليها به بها""".split())
_ADJ = set("""السعودي السعودية الأجنبي الأجنبية الوافد الوافدة المذكور المذكورة المشار المعني المعنية المدعي
المدعية المدعى المتوفى المتوفاة المورث الموكل الموكلة المقيم المقيمة المتهم المتهمة العامل العاملة""".split())
_HONORIFIC = ("السيد", "السيدة", "الأستاذ", "الأستاذة", "الاستاذ", "الاستاذة", "الشيخ", "الدكتور", "الدكتورة",
              "المدعو", "المدعوة", "المواطن", "المواطنة", "المقيم", "المقيمة")
# Only nouns that are nearly always followed by a proper name when they carry no «ال»; «محل/مركز/معرض/مجموعة»
# are left out on purpose (محل النزاع، مركز قانوني، في معرض حديثه، مجموعة أحكام).
_ORG = ("شركة", "مؤسسة", "مصنع", "مكتب", "مستوصف", "متجر", "جمعية", "مصرف", "بنك", "منشأة", "مطعم", "فندق", "مستشفى")
_ORG_GENERIC = set("""ذات مساهمة تضامن توصية أجنبية وطنية تأمين مقاولات عقارية قابضة ناشئة واحدة أخرى مهنية محدودة
سعودية خليجية خاصة عامة حكومية تجارية صناعية مدرجة عائلية شقيقة تابعة مختلطة فردية محاماة تحكيم استشارات استثمار
تمويل وساطة نقل شحن خدمات طيران سياحة سفر صيانة تقنية حراسة أمن توظيف استقدام عمل أهلية خيرية قائمة كبرى صغيرة
متوسطة مملوكة مسجلة حديثة جديدة قديمة معينة إسلامي إسلامية تجاري مركزي طبي طبية صحي صحية تدريب تعليمي تعليمية
مالية مالي مقاولة تشغيل توريد تطوير عقار ناقلة أم فرعية فرع عمومية تعاونية قانونية قانوني محاسبة هندسية""".split())
_EXEMPT_BEFORE_RAQM = set("""المادة مادة المواد الفقرة فقرة البند بند المرسوم مرسوم الملكي ملكي الوزراء الوزاري
الوزارية التعميم تعميم النظام نظام اللائحة لائحة الأمر أمر الجدول جدول الملحق ملحق الصفحة صفحة""".split())

_CHAIN_RE = re.compile(rf"{_NB}(?:عبد\s*)?{_TOK}(?:\s+(?:بن|بنت)\s+(?:عبد\s*)?{_TOK})+(?:\s+آل\s+{_TOK}|\s+ال{_TOK}ي)?{_NA}")
_AAL_RE = re.compile(rf"{_NB}آل\s+(?!إلى|الى|إلي|الي|اليه|إليه){_TOK}{_NA}")
_HON_RE = re.compile(rf"{_NB}(?:{'|'.join(_HONORIFIC)})\s+({_TOK}(?:\s+{_TOK}){{0,2}}){_NA}")
_ORG_QUOTED_RE = re.compile(rf"{_NB}((?:{'|'.join(_ORG)})\s*)([«\"“(])([^»\"”)\n]{{1,60}})([»\"”)])")
_ORG_RE = re.compile(rf"{_NB}(?:{'|'.join(_ORG)})\s+({_TOK}(?:\s+{_TOK})?){_NA}")
_LATIN_RE = re.compile(r"\b[A-Z][A-Za-z&.'\-]+(?:\s+[A-Z][A-Za-z&.'\-]+)+\b")
_RAQM_RE = re.compile(r"(?<![ء-ي])(رقم|برقم|بالرقم|الرقم|رقمه|رقمها|رقمي|رقمهم)\s*[:(（]?\s*"
                      r"([0-9٠-٩](?:[0-9٠-٩/\\\-]|\s(?=[0-9٠-٩]))*)\)?")


def _name_tokens(span: str) -> int:
    """How many leading tokens after an honorific to mask: the first name, then only family nisbas
    («العتيبي»). A plain second word may be a verb («السيد أحمد يطالب»), so it stays."""
    toks = span.split()
    if not toks or toks[0] in _STOP or toks[0] in _ADJ or toks[0].startswith("ال") or _TANWEEN.search(toks[0]):
        return 0
    n = 1
    for t in toks[1:]:
        if not (t.startswith("ال") and t.endswith("ي")) or t in _ADJ:
            break
        n += 1
    return n


def _is_ar(k: str) -> bool:
    return bool(re.search(f"[{_AR}]", k))


def _has(text: str, k: str) -> bool:
    if _is_num(k):
        return len(k) >= 4 and bool(re.search(rf"(?<![0-9٠-٩]){re.escape(k)}(?![0-9٠-٩])", text))
    return bool(re.search(rf"{_NB}{re.escape(k)}{_NA}", text)) if _is_ar(k) else k in text


def _mask_known(text: str, known: list[str]) -> str:
    for k in known:   # longest first, so a full name goes before its parts
        if _is_num(k):   # a case number: digit boundaries, never a short run that could be a [n] citation
            text = re.sub(rf"(?<![0-9٠-٩]){re.escape(k)}(?![0-9٠-٩])", NUM_MASK, text) if len(k) >= 4 else text
        else:
            text = re.sub(rf"{_NB}{re.escape(k)}{_NA}", NAME_MASK, text) if _is_ar(k) else text.replace(k, NAME_MASK)
    return text


def _is_num(k: str) -> bool:
    return bool(re.fullmatch(r"[0-9٠-٩/\\\-\s]+", k))


def _mask(text: str, known: list[str] = ()) -> str:
    """Names → [اسم], identifying numbers → [رقم], emails → [بريد]. Citation tokens [n] stay."""
    from shared.privacy.codec import _apply, _detect, normalize_digits

    s = _mask_known(text or "", list(known))
    emails, nums = _detect(normalize_digits(s))   # the PDPL detector: 5+ digit ids, not dates/money
    s = _apply(s, [(a, b, MAIL_MASK) for a, b, _ in emails] + [(a, b, NUM_MASK) for a, b, _ in nums])
    s = NUM_RE.sub(NUM_MASK, s)

    def raqm(m: re.Match) -> str:   # «المادة رقم 70», «قرار مجلس الوزراء رقم 59» stay; «القضية رقم 123/1445» goes
        before = re.split(r"[،,.;؛:\[\]()]", s[:m.start()])[-1].split()[-2:]
        return m.group(0) if any(w in _EXEMPT_BEFORE_RAQM for w in before) else f"{m.group(1)} {NUM_MASK}"

    s = _RAQM_RE.sub(raqm, s)
    s = _CHAIN_RE.sub(NAME_MASK, s)
    s = _AAL_RE.sub(NAME_MASK, s)

    def hon(m: re.Match) -> str:
        n = _name_tokens(m.group(1))
        if not n:
            return m.group(0)
        toks = m.group(1).split()
        head = m.group(0)[: m.start(1) - m.start(0)]
        return head + NAME_MASK + (" " + " ".join(toks[n:]) if toks[n:] else "")

    s = _HON_RE.sub(hon, s)

    def org_q(m: re.Match) -> str:
        inner = m.group(3)
        if not re.search(rf"[{_AR}A-Za-z]{{2}}", inner) or re.search(r"ذ\.?\s?م\.?\s?م|المدعى|المدعي|المستأنف|الطرف", inner):
            return m.group(0)
        return f"{m.group(1)}{m.group(2)}{NAME_MASK}{m.group(4)}"

    s = _ORG_QUOTED_RE.sub(org_q, s)

    def org(m: re.Match) -> str:
        toks = m.group(1).split()
        keep = []
        for t in toks:
            if (t.startswith("ال") or t in _ORG_GENERIC or t in _STOP or _TANWEEN.search(t)
                    or t.startswith(("ل", "ب") if keep else ("بال", "لل", "وال", "فال", "كال"))
                    or (not keep and len(t) > 3 and t[0] in "بلوفك" and t[1:] in _ORG_GENERIC | _STOP)
                    # «ألزم شركة بدفع…», «قرار بنك بمنع…», «شركة بتسليم…»: ب/ل + a verbal noun, not a name
                    or (not keep and t[0] in "بل" and (len(t) <= 5 or t[1:].startswith(("ت", "إ", "ا", "أ"))))):
                break   # «شركة التأمين», «شركة ذات…», «شركة نماء للتجارة» → only «نماء»
            keep.append(t)
        if not keep:
            return m.group(0)
        head = m.group(0)[: m.start(1) - m.start(0)]
        rest = toks[len(keep):]
        return head + NAME_MASK + (" " + " ".join(rest) if rest else "")

    s = _ORG_RE.sub(org, s)
    return _LATIN_RE.sub(NAME_MASK, s)


def _leaks(text: str, known: list[str]) -> list[str]:
    """Shape-only problems left after masking (never the values)."""
    from shared.privacy.codec import audit

    out = [f"digits:{len(m)}" for m in NUM_RE.findall(text)]
    out += [f"pdpl-{h.kind}:{h.length}" for h in audit(text, set())]
    out += [f"known-name:{len(k)}" for k in known if _has(text, k)]
    return out


def _strings(x) -> list[str]:
    if isinstance(x, str):
        return [x]
    if isinstance(x, dict):
        return [s for v in x.values() for s in _strings(v)]
    if isinstance(x, list):
        return [s for v in x for s in _strings(v)]
    return []


_NOT_A_NAME = _STOP | _ORG_GENERIC | set(_ORG) | {"عبد", "بنت", "قضية", "دعوى", "مطالبة", "ضد", "المدعي", "المدعى", "عليه"}


def _known_names(user: dict, lcase: dict | None, extra: list[str]) -> list[str]:
    """Strings to mask for this answer, longest first: the account holder's name and the parties of
    the lawyer_case the answer belongs to (whole and word by word), the case's name and number
    (whole only: a case name is free text such as «مطالبة مالية»), and ``--names-file`` entries."""
    people = _strings([user.get(f) for f in ("full_name_ar", "full_name_en", "preferred_name")])
    people += _strings((lcase or {}).get("parties")) + extra
    whole = _strings([(lcase or {}).get("case_name"), (lcase or {}).get("case_number")])
    out = set()
    for v in people + whole:
        v = re.sub(r"\s+", " ", v or "").strip()
        if len(v) >= 3:
            out.add(v)
    for v in people:
        out.update(t for t in (v or "").split() if len(t) >= 3 and t not in _NOT_A_NAME)
    return sorted(out, key=len, reverse=True)


def _clip(sentence: str, limit: int = 900) -> str:
    """Cut a long sentence (a wide table row) to ``limit`` chars without losing its last citation token."""
    if len(sentence) <= limit:
        return sentence
    ends = [m.end() for m in _CITE_RE.finditer(sentence)]
    end = max(ends[-1] if ends else limit, min(limit, len(sentence)))
    return "…" + sentence[max(0, end - limit):end] if end > limit else sentence[:limit] + "…"


# ------------------------------------------------------------------ sentences


def _units(md: str):
    """(section, before, sentence) for every sentence/table row of the answer body."""
    body = re.sub(r"```.*?```", "", _THINK_RE.sub("", md or ""), flags=re.S)
    section, prev = "", ""
    for raw in body.splitlines():
        line = re.sub(r"^(?:>\s*)+", "", raw.strip()).replace("**", "")
        if not line:
            prev = ""
            continue
        if line.startswith("#"):
            section, prev = line.lstrip("#").strip(), ""
            continue
        if line.startswith("|"):
            if re.fullmatch(r"[|:\-\s]+", line):
                continue
            row = " | ".join(c.strip() for c in line.strip("|").split("|"))
            yield section, "", row
            continue
        sents = [x.strip() for x in _SENT_SPLIT.split(_LIST_MARK.sub("", line)) if x.strip()]
        merged: list[str] = []
        for x in sents:   # citation tokens placed after the full stop belong to the previous sentence
            m = _LEAD_CITES.match(x)
            if m and merged:
                merged[-1] += " " + m.group(1).strip()
                x = x[m.end():].strip()
            if x:
                merged.append(x)
        for x in merged:
            yield section, prev, x
            prev = x


def _cited(sentence: str) -> list[int]:
    seen: list[int] = []
    for g in _CITE_RE.findall(sentence):
        for p in re.split(r"[,،]", g):
            if p.strip().isdigit() and int(p) not in seen:
                seen.append(int(p))
    return seen


# ------------------------------------------------------------------ sample


def _ts(s: str) -> dt.datetime:
    d = dt.datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def _round_robin(pool: list[dict], seed: str) -> list[dict]:
    """Seeded order that takes one answer per account per round, so one heavy user can't fill the sample."""
    h = lambda x: hashlib.md5(f"{seed}:{x}".encode()).hexdigest()  # noqa: E731
    by_user: dict[str, list[dict]] = collections.defaultdict(list)
    for w in sorted(pool, key=lambda w: h(w["item_id"])):
        by_user[w["user_id"]].append(w)
    users = sorted(by_user, key=h)
    out = []
    while any(by_user.values()):
        for u in users:
            if by_user[u]:
                out.append(by_user[u].pop(0))
    return out


def _subject(c: dict) -> str:
    """The ruling's one-line subject: short_summary, else (≈2.2k rulings have none) the summary's
    first lines, without its markdown headings («# ملخص القضية case_…») and bullets."""
    text = c.get("short_summary") or c.get("summary") or ""
    lines = [re.sub(r"^\s*[-*•]\s*", "", l).strip() for l in text.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    return re.sub(r"\s+", " ", " ".join(lines))[:SUBJECT_CHARS]


def _engines(sb, w: dict) -> list[str]:
    """Models on the case-reranker ledger rows of this turn (nearest to the answer, same conversation)."""
    t = _ts(w["created_at"])
    rows = (sb.table("llm_calls").select("model,created_at").eq("conversation_id", w["conversation_id"])
            .eq("agent", CASE_RERANKER).gte("created_at", (t - dt.timedelta(minutes=30)).isoformat())
            .lte("created_at", (t + dt.timedelta(minutes=5)).isoformat()).execute().data)
    if not rows:
        return []
    near = min(abs((_ts(r["created_at"]) - t).total_seconds()) for r in rows)
    return sorted({r["model"] for r in rows if abs((_ts(r["created_at"]) - t).total_seconds()) <= near + 5})


def cmd_sample(args) -> None:
    before = _ts(args.before) if args.before else dt.datetime.now(dt.timezone.utc)
    since = before - dt.timedelta(days=args.days)
    run = args.run or before.date().isoformat() + ("" if args.label == "weekly" else f"-{args.label}")
    run_dir = OUT / run
    if run_dir.exists() and any(run_dir.glob("verdict_*.json")) and not args.force:
        sys.exit(f"{run_dir.relative_to(REPO)} already has verdicts; pass --run <other> or --force")
    extra = [l.strip() for l in Path(args.names_file).read_text(encoding="utf-8").splitlines()
             if l.strip()] if args.names_file else []
    sb = _sb()

    wis, page = [], 0
    while True:
        rows = (sb.table("workspace_items").select("item_id,user_id,conversation_id,case_id,created_at")
                .eq("agent_family", "deep_search").is_("deleted_at", "null").is_("prev_content_md", "null")
                .gte("created_at", since.isoformat()).lt("created_at", before.isoformat())
                .order("created_at").range(page * 1000, page * 1000 + 999).execute().data)
        wis += rows
        if len(rows) < 1000:
            break
        page += 1
    with_cases: set[str] = set()
    for ch in _chunks([w["item_id"] for w in wis]):
        with_cases |= {r["wi_id"] for r in sb.table("workspace_item_references").select("wi_id")
                       .eq("domain", "cases").eq("used", True).in_("wi_id", ch).execute().data}
    pool = [w for w in wis if w["item_id"] in with_cases]
    users: dict[str, dict] = {}
    for ch in _chunks(sorted({w["user_id"] for w in pool})):
        for u in sb.table("users").select("user_id,email,full_name_ar,full_name_en,preferred_name").in_("user_id", ch).execute().data:
            email = (u.pop("email") or "").lower()
            u["source"] = "bot" if email.startswith(BOT_PREFIX) else "internal" if email.endswith(INTERNAL_DOMAIN) else "user"
            users[u["user_id"]] = u
    src = lambda w: users.get(w["user_id"], {}).get("source", "user")  # noqa: E731
    drop = {"internal"} | ({"bot"} if args.no_bot else set())
    pool = [w for w in pool if src(w) not in drop]
    by_src = collections.Counter(src(w) for w in pool)
    print(f"window {since:%Y-%m-%d %H:%M} → {before:%Y-%m-%d %H:%M} UTC: {len(wis)} deep_search answers, "
          f"{len(pool)} cite judgments ({dict(by_src)})", flush=True)

    batches, key = [], {}
    for w in _round_robin(pool, run):
        if len(batches) >= args.limit:
            break
        wi = sb.table("workspace_items").select("content_md").eq("item_id", w["item_id"]).single().execute().data
        refs = {r["n"]: r for r in sb.table("workspace_item_references").select("n,domain,ref_id")
                .eq("wi_id", w["item_id"]).execute().data}
        lcase = None
        if w.get("case_id"):
            got = sb.table("lawyer_cases").select("case_name,case_number,parties").eq("case_id", w["case_id"]).execute().data
            lcase = got[0] if got else None
        # the bot's account name is not client data, and its words could hit ordinary text
        known = _known_names({} if src(w) == "bot" else users.get(w["user_id"], {}), lcase, extra)
        cites, ruling_ns = [], []
        for section, prev, sent in _units(wi["content_md"]):
            ns = [n for n in _cited(sent) if refs.get(n, {}).get("domain") == "cases"]
            if not ns:
                continue
            cites.append({"c": f"c{len(cites) + 1}", "section": _mask(section, known)[:160],
                          "before": _mask(prev, known)[-300:], "sentence": _clip(_mask(sent, known)), "rulings": ns})
            ruling_ns += [n for n in ns if n not in ruling_ns]
        if not cites:
            continue
        case_refs = [refs[n]["ref_id"].split(":", 1)[1] for n in ruling_ns]
        info = {c["case_ref"]: c for c in sb.table("cases").select("case_ref,court,short_summary,summary")
                .in_("case_ref", case_refs).execute().data}
        rulings = {str(n): _mask(f"{info[cr].get('court') or ''} — {_subject(info[cr])}" if cr in info
                                 else "(الحكم غير موجود في قاعدة الأحكام)", known)
                   for n, cr in zip(ruling_ns, case_refs)}
        aid = f"a{len(batches) + 1:02d}"
        batch = {"answer": aid, "rulings": rulings, "citations": cites}
        leaks = _leaks(json.dumps(batch, ensure_ascii=False), known)
        if leaks:
            sys.exit(f"{aid}: unmasked strings remain {leaks[:10]} — nothing written")
        batches.append(batch)
        key[aid] = {"item_id": w["item_id"], "created_at": w["created_at"], "source": src(w),
                    "engines": _engines(sb, w), "rulings": len(rulings),
                    "pairs": sum(len(c["rulings"]) for c in cites)}
        print(f"  {aid} {key[aid]['source']:<4} rulings={len(rulings):>2} pairs={key[aid]['pairs']:>3} "
              f"engines={'+'.join(key[aid]['engines']) or 'unknown'}", flush=True)

    run_dir.mkdir(parents=True, exist_ok=True)
    for old in list(run_dir.glob("batch_*.json")) + list(run_dir.glob("verdict_*.json")):
        old.unlink()   # --force re-sample: stale batches/verdicts would mix two samples
    for b in batches:
        (run_dir / f"batch_{b['answer']}.json").write_text(json.dumps(b, ensure_ascii=False, indent=1), encoding="utf-8")
    (run_dir / "_key.json").write_text(json.dumps(key, indent=1), encoding="utf-8")
    meta = {"run": run, "label": args.label, "since": since.isoformat(), "before": before.isoformat(),
            "limit": args.limit, "pool": len(pool), "pool_by_source": dict(by_src), "answers": len(batches),
            "bot_included": not args.no_bot, "prompt_version": PROMPT_VERSION,
            "sampled_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}
    (run_dir / "_meta.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    (run_dir / "judge_prompt.md").write_text(JUDGE_PROMPT.replace("<RUN_DIR>", str(run_dir)), encoding="utf-8")
    print(f"answers={len(batches)} rulings={sum(k['rulings'] for k in key.values())} "
          f"pairs={sum(k['pairs'] for k in key.values())} → {run_dir.relative_to(REPO)}")
    for b in batches:
        print(f"BATCH {run_dir / ('batch_' + b['answer'] + '.json')}")
    print(f"RUN={run}")


# ------------------------------------------------------------------ judge prompt

JUDGE_PROMPT = """# Blind judge prompt: case-law citation audit (""" + PROMPT_VERSION + """)

Start one `general-purpose` agent with `model: sonnet` per batch file. Replace `<AID>` (for
example `a07`) before sending. Adapted from `agents_reports/reranker_model_ab/judge_prompt_case_ab.md`,
reduced to one answer and to the citation check alone.

---

You are a senior Saudi litigation lawyer acting as a BLIND checker of the court-ruling citations in ONE AI-written legal answer.

Input file: <RUN_DIR>\\batch_<AID>.json
Read the WHOLE file with the Read tool (pass allow_large: true). Read no other file in that folder. It is one JSON object:
- "answer": the answer id.
- "rulings": map from citation number to "<court> — <one-line subject of the ruling>".
- "citations": a list, one per sentence that cites a ruling. Each has "c" (id), "section" (the heading it sits under), "before" (the previous sentence, context only), "sentence" (the sentence as written, with its [n] citation tokens) and "rulings" (the ruling numbers cited in this sentence that you must check). Any other [n] in the sentence points to a regulation or another source: ignore it.
«[اسم]», «[رقم]» and «[بريد]» are masked names, numbers and emails. Never count a mask as an error.

For every (citation, ruling) pair, decide whether the ruling, as its one-line subject describes it, supports the part of the sentence its [n] is attached to (the clause just before the token, read with the whole sentence and the context sentence):
- "supported": the subject shows the ruling decided that point, or a point that directly carries it: the same legal mechanism and an outcome consistent with what the sentence says. When the sentence clearly presents the ruling as an analogy or a contrary example, it is supported if the ruling fits that role as described.
- "not_supported": the subject is about a different legal mechanism or a different kind of dispute, it points the other way, or the sentence attributes to the ruling a holding, outcome, fact, court or detail that the subject does not show.
- "unclear": the subject is too thin to tell either way. Use it sparingly, never as a polite "not_supported".
Judge each pair on its own. Do not reward or punish the rest of the answer, its length or its style, and do not try to guess which system wrote it.

Write exactly this JSON to <RUN_DIR>\\verdict_<AID>.json with the Write tool, one entry per pair in file order:
{"answer": "<AID>", "judgments": [{"c": "c1", "n": 28, "verdict": "supported", "why": "..."}]}
"verdict" is one of "supported", "not_supported", "unclear". "why" is one short line in English that names no party, person or company. It must be valid JSON. When done, reply with only one line: "<AID>: <supported>/<not_supported>/<unclear>".
"""


# ------------------------------------------------------------------ tally


def _norm_verdict(v: str) -> str:
    v = re.sub(r"[\s\-]+", "_", (v or "").strip().lower())
    return "not_supported" if v in ("not_supported", "unsupported", "no", "notsupported") else \
        "supported" if v in ("supported", "yes") else "unclear"


def _wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if not n:
        return 0.0, 0.0
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def _pct(k: int, n: int) -> str:
    if not n:
        return "—"
    lo, hi = _wilson(k, n)
    return f"{k}/{n} = {k / n:.1%} (95% CI {lo:.0%}–{hi:.0%})"


def cmd_tally(args) -> None:
    run_dir = OUT / args.run
    meta = json.loads((run_dir / "_meta.json").read_text(encoding="utf-8"))
    key = json.loads((run_dir / "_key.json").read_text(encoding="utf-8"))
    batches = {p.stem[6:]: json.loads(p.read_text(encoding="utf-8")) for p in sorted(run_dir.glob("batch_*.json"))}
    pairs, bad_files = {}, []   # (aid, c, n) → verdict | None
    for aid, b in batches.items():
        for c in b["citations"]:
            for n in c["rulings"]:
                pairs[(aid, c["c"], int(n))] = None
        vf = run_dir / f"verdict_{aid}.json"
        if not vf.exists():
            continue
        try:
            got = json.loads(vf.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            bad_files.append(aid)
            continue
        whys = {}
        for j in got.get("judgments", []):
            k = (aid, str(j.get("c")), int(j.get("n", -1)))
            if k in pairs:
                pairs[k] = _norm_verdict(j.get("verdict"))
                whys[k] = _mask(str(j.get("why", "")))[:300]
        b["_why"] = whys
    judged = {k: v for k, v in pairs.items() if v}
    missing = len(pairs) - len(judged)
    complete = missing == 0 and not bad_files

    # ruling = one cited judgment within one answer; unsupported if any of its sentences is
    ruling: dict[tuple[str, int], str] = {}
    for (aid, _c, n), v in judged.items():
        cur = ruling.get((aid, n))
        ruling[(aid, n)] = "not_supported" if "not_supported" in (cur, v) else "supported" if "supported" in (cur, v) else "unclear"
    answers = sorted({aid for aid, _ in ruling})
    bad_answers = sorted({aid for (aid, _), v in ruling.items() if v == "not_supported"})
    r_bad = sum(1 for v in ruling.values() if v == "not_supported")
    r_unclear = sum(1 for v in ruling.values() if v == "unclear")
    c_bad = sum(1 for v in judged.values() if v == "not_supported")
    rate = r_bad / len(ruling) if ruling else 0.0
    share = len(bad_answers) / len(answers) if answers else 0.0

    def split(field: str) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for aid in answers:
            g = "+".join(key[aid].get(field) or ["unknown"]) if field == "engines" else key[aid].get(field, "?")
            d = out.setdefault(g, {"answers": 0, "bad_answers": 0, "rulings": 0, "unsupported": 0})
            d["answers"] += 1
            d["bad_answers"] += aid in bad_answers
            rs = [v for (a, _), v in ruling.items() if a == aid]
            d["rulings"] += len(rs)
            d["unsupported"] += sum(1 for v in rs if v == "not_supported")
        return out

    by_engine, by_source = split("engines"), split("source")
    log = [r for r in _jsonl(LOG_PATH) if r.get("run") != meta["run"]]
    base = None
    if args.baseline_rate is not None:
        base = {"run": "(command line)", "rate": args.baseline_rate, "share": args.baseline_share}
    else:
        cands = [r for r in log if r.get("label", "").startswith("baseline") and r.get("complete")
                 and r.get("prompt_version") == PROMPT_VERSION and (not args.baseline_run or r["run"] == args.baseline_run)]
        base = cands[-1] if cands else None
    is_base = meta["label"].startswith("baseline")
    alerts, notes = [], []
    if not is_base:
        if base and base.get("rate") and rate > RATE_X * base["rate"]:
            alerts.append(f"mis-citation rate {rate:.1%} > {RATE_X}× baseline {base['rate']:.1%} ({base['run']})")
        if share > args.share_max:
            alerts.append(f"answers with a mis-cited ruling {share:.0%} > {args.share_max:.0%}")
        if not base:
            notes.append("no baseline from this job yet: the rate alert cannot fire (run the baseline-qwen sample first)")
    if base and base.get("share") is not None and base["share"] > args.share_max:
        notes.append(f"the baseline's own answer share is {base['share']:.0%}, above the {args.share_max:.0%} line, "
                     "so the share alert fires whenever the engine is merely as good as qwen")
    if is_base and any(e != "qwen3.5-flash" for k in key.values() for e in k.get("engines") or []):
        notes.append("this baseline contains turns served by a non-qwen case reranker")
    if not complete:
        notes.append(f"incomplete: {missing} pair(s) without a verdict, unreadable verdict files: {bad_files or 'none'}")
    if len(ruling) < 30:
        notes.append(f"small sample ({len(ruling)} cited rulings): read the CI, not the point value")

    row = {"run": meta["run"], "label": meta["label"], "since": meta["since"], "before": meta["before"],
           "answers": len(answers), "answers_with_unsupported": len(bad_answers), "share": round(share, 4),
           "rulings": len(ruling), "unsupported": r_bad, "rate": round(rate, 4), "unclear": r_unclear,
           "pairs": len(judged), "pairs_unsupported": c_bad, "engines": {k: v["answers"] for k, v in by_engine.items()},
           "sources": {k: v["answers"] for k, v in by_source.items()}, "baseline_run": (base or {}).get("run"),
           "alert": bool(alerts), "complete": complete, "prompt_version": PROMPT_VERSION, "judge": "sonnet",
           "tallied_at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")}

    md = [f"# Case-law citation audit: {meta['run']}", "",
          f"Label `{meta['label']}` · answers from {meta['since'][:16]} to {meta['before'][:16]} UTC · "
          f"{len(answers)} of {meta['pool']} answers that cited judgments · judge prompt {PROMPT_VERSION} (Sonnet) · "
          f"plan `.claude/plans/reranker_decider_engine.md` step 8 / D14. Names and numbers are masked.", "",
          "| Measure | Value |", "|---|---|",
          f"| **Mis-citation rate** (cited rulings not supporting their sentence ÷ cited rulings) | {_pct(r_bad, len(ruling))} |",
          f"| **Answers with at least one** | {_pct(len(bad_answers), len(answers))} |",
          f"| Per citation (sentence × ruling) | {_pct(c_bad, len(judged))} |",
          f"| Rulings judged unclear | {_pct(r_unclear, len(ruling))} |", ""]
    if is_base:
        md += ["**This run is a baseline.** Later weekly runs compare against it.", ""]
    elif base:
        ratio = f"{rate / base['rate']:.2f}×" if base.get("rate") else "—"
        md += [f"Baseline `{base['run']}`: rate {base['rate']:.1%}, share {base.get('share') or 0:.0%}. "
               f"This run: {ratio} the baseline rate (alert above {RATE_X}×) and share {share:.0%} (alert above {args.share_max:.0%}).", ""]
    md += [f"**{'ALERT' if alerts else 'OK'}**" + (": " + "; ".join(alerts) if alerts else ""), ""]
    md += [f"- Note: {n}" for n in notes] + ([""] if notes else [])
    for title, groups in (("Case-reranker engine", by_engine), ("Source", by_source)):
        md += [f"## By {title.lower()}", "", f"| {title} | Answers | With a mis-citation | Cited rulings | Unsupported |",
               "|---|---|---|---|---|"]
        md += [f"| {g} | {d['answers']} | {d['bad_answers']} | {d['rulings']} | {_pct(d['unsupported'], d['rulings'])} |"
               for g, d in sorted(groups.items())] + [""]
    md += ["## Unsupported citations", ""]
    for aid in bad_answers:
        b = batches[aid]
        md.append(f"### {aid} ({key[aid]['source']}, {'+'.join(key[aid]['engines']) or 'unknown'})")
        by_n: dict[int, list[str]] = collections.defaultdict(list)
        for c in b["citations"]:
            for n in c["rulings"]:
                k = (aid, c["c"], int(n))
                if pairs.get(k) == "not_supported":
                    by_n[int(n)] += [f"  - {c['c']}: {c['sentence'][:400]}", f"    - judge: {b.get('_why', {}).get(k, '')}"]
        for n, lines in by_n.items():
            md += [f"- **[{n}]** {b['rulings'][str(n)]}"] + lines
        md.append("")
    report = "\n".join(md)
    leaks = _leaks(report, [])
    if leaks:
        sys.exit(f"report has unmasked strings {leaks[:10]} — nothing written")
    (OUT / f"{meta['run']}.md").write_text(report, encoding="utf-8")
    if complete or args.allow_partial:
        LOG_PATH.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in log + [row]), encoding="utf-8")
    else:
        print(f"NOT LOGGED (incomplete; judge the missing batches, or pass --allow-partial)")
    for n in notes:
        print(f"NOTE: {n}")
    print(f"SUMMARY: run={meta['run']} label={meta['label']} answers={len(answers)} rulings={len(ruling)} "
          f"rate={rate:.4f} share={share:.4f} unclear={r_unclear} engines={row['engines']} "
          f"baseline={(base or {}).get('run')} complete={complete}")
    for a in alerts:
        print(f"ALERT: {a}")
    print(f"report → {(OUT / (meta['run'] + '.md')).relative_to(REPO)}")


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sample")
    s.add_argument("--days", type=int, default=7)
    s.add_argument("--before", help="window end, UTC date or ISO time (default now); e.g. the phase-B switch date for a qwen baseline")
    s.add_argument("--limit", type=int, default=20)
    s.add_argument("--label", default="weekly", help="'weekly', or 'baseline-qwen' for the qwen-era baseline")
    s.add_argument("--run", help="run folder name (default <date> or <date>-<label>)")
    s.add_argument("--no-bot", action="store_true", help="leave out the editorial bot's answers")
    s.add_argument("--names-file", help="extra names to mask, one per line (keep the file outside the repo)")
    s.add_argument("--force", action="store_true", help="re-sample a run folder that already has verdicts")
    t = sub.add_parser("tally")
    t.add_argument("--run", required=True)
    t.add_argument("--baseline-run", help="compare with this logged baseline run (default: latest baseline)")
    t.add_argument("--baseline-rate", type=float, help="baseline rate when log.jsonl is not available (cloud checkout)")
    t.add_argument("--baseline-share", type=float)
    t.add_argument("--share-max", type=float, default=SHARE_MAX)
    t.add_argument("--allow-partial", action="store_true", help="log even if some pairs have no verdict")
    args = ap.parse_args()
    {"sample": cmd_sample, "tally": cmd_tally}[args.cmd](args)


if __name__ == "__main__":
    main()
