"""Adversarial fixtures — queries built to land BETWEEN the rules.

The earlier fixture sets test the rules as written; this set hunts the seams.
Built with the user 2026-08-16 by asking, per decision surface, "what query
would the system misplace?" — and two of its entries were confirmed as real
bugs before any eval ran (the dropped ``[n]`` in Case-C previews, and باب
missing from the router's addressable set; both fixed the same day).

Shape: plain data, no LLM here. Each fixture:

- ``id``       — stable handle, prefixed by surface (``hair-``, ``bab-``, …)
- ``surface``  — which decision this attacks (see the class dict below)
- ``q``        — the query, verbatim, as a user would type it
- ``setup``    — what must be attached/open for the query to make sense
- ``expect``   — the CORRECT behaviour (route, fan-out shape, or refusal)
- ``trap``     — the specific wrong behaviour this fixture exists to catch
- ``status``   — CONFIRMED (verified against code/corpus) | PREDICTED

Drivers: the routing surfaces run through ``run_router`` (LLM, costs money —
keep repeats ≥3); the searcher surfaces run through ``run_simple_search`` with
scripted models where the trap is structural, real models where it is judgment.
Money fixtures (``unlock-*``) touch the live ledger — account for and clean up
every row, per the house eval discipline.
"""
from __future__ import annotations

SURFACES = {
    "hairline":   "one token away from a rule just written — the dangerous-pair class",
    "bab":        "the باب/فصل addressability hole (router + searcher, fixed 2026-08-16)",
    "case_c":     "Case-C phrasing the candidate previews could not match",
    "corpus":     "traps that only exist because of how the corpus is shaped",
    "unlock":     "money edges — charging on mis-resolution, multi-unlock turns",
    "state":      "pause-slot and resume collisions",
    "control":    "over-correction guards — must KEEP answering directly/cheaply",
}

FIXTURES: list[dict] = [
    # ── hairline ────────────────────────────────────────────────────────────
    {
        "id": "hair-01", "surface": "hairline",
        "q": "اش الحكمين اللي في WI-2؟",
        "setup": "WI-2 = agent_search item citing ≥2 rulings",
        "expect": "simple_search; fan-out 2; two synthesizers, two replies",
        "trap": "the new integrative-abort over-fires on 'two documents + one "
                "question' — but this is TWO INDEPENDENT lookups. One word from "
                "«قارن الحكمين». If this aborts to deep_search, the guard ate a "
                "legitimate case.",
        "status": "PREDICTED",
    },
    {
        "id": "hair-02", "surface": "hairline",
        "q": "اعطيني الحكمين اللي في WI-2 وايش الفرق بينهم",
        "setup": "same as hair-01",
        "expect": "deep_search (the comparison half governs — a compound request "
                  "is integrative if ANY part needs the documents held together)",
        "trap": "the searcher fans out on the first half and silently drops "
                "«وايش الفرق بينهم» — two summaries, no difference stated, and "
                "the user cannot tell their question was half-answered.",
        "status": "PREDICTED",
    },
    {
        "id": "hair-03", "surface": "hairline",
        "q": "قارن المادة 77 بالمادة 78 من نظام العمل",
        "setup": "none",
        "expect": "deep_search per the router's comparison gate (correct but "
                  "expensive — D5 grouping means ONE synthesizer holds both "
                  "articles and could have compared them; recorded trade-off)",
        "trap": "router and searcher disagree: if the router lets it through, "
                "the searcher must NOT abort it (same document ⇒ not 'across "
                "more than one document'). Either outcome must answer the "
                "comparison; the failure is a fan-out into two agents.",
        "status": "CONFIRMED",  # the rule conflict is in the prompts as written
    },
    {
        "id": "hair-04", "surface": "hairline",
        # MEASURED 2026-08-16 (adv_routing): the original bare form («وش تقول
        # المادة 77 عن التعويض؟») CANNOT test this trap — with no law named it
        # exercises the known bare-article behaviour (router asks «من أي
        # نظام؟», 0/3 here, 3/6 in §13f) and never reaches the qualifier rule.
        # The law-named form below went simple_search 3/3: the qualifier rule
        # does NOT over-fire on an article. Trap REFUTED; kept as a regression
        # sentinel in this corrected form.
        "id_note": "was the bare form; corrected after adv_routing run 1",
        "q": "وش تقول المادة 77 من نظام العمل عن التعويض؟",
        "setup": "none",
        "expect": "simple_search — a مادة is the atom; nothing exists below it "
                  "to search. «عن التعويض» is focus, not narrowing.",
        "trap": "the Test-1 qualifier rule fires by its letter («عن …» = "
                "narrowing inside a document) and sends an article lookup to "
                "deep_search.",
        "status": "CONFIRMED",  # measured 3/3 PASS in the corrected form
    },
    # ── باب ─────────────────────────────────────────────────────────────────
    {
        "id": "bab-01", "surface": "bab",
        "q": "اعطيني الباب الثالث من نظام العمل",
        "setup": "none",
        "expect": "simple_search → searcher resolves the PARENT نظام (no باب "
                  "resolver exists) → whole-reg unfold → synthesizer answers "
                  "about that باب specifically, not a general overview",
        "trap": "pre-fix: the router's closed set had no باب → deep_search for "
                "a query the family was designed to serve. Post-fix: the "
                "synthesizer gives a whole-نظام overview instead of the باب.",
        "status": "CONFIRMED",
    },
    {
        "id": "bab-02", "surface": "bab",
        "q": "اش يقول الفصل الثاني من اللائحة التنفيذية لنظام المنافسات والمشتريات الحكومية؟",
        "setup": "none",
        "expect": "simple_search (routing measured 3/3 ✅); searcher resolves "
                  "the parent لائحة; answer scoped to الفصل الثاني",
        "trap": "same as bab-01 via «الفصل» (4,343 chunk titles); plus the "
                "lookalike hazard — اللائحة must not resolve to the نظام itself. "
                "RESOLUTION LANDMINE, verified in SQL 2026-08-16: BOTH targets "
                "exist only under MALFORMED titles — the نظام as «نظام المنافسات "
                "و المشتريات الحكومية» (detached waw, id 90d782fa) and the "
                "لائحة as «الائحة التنفيذية لنظام المنافسات» (missing a ل in "
                "«اللائحة», id 30219dce). The «ال»-stripping normalizer treats "
                "«الائحة» and «اللائحة» DIFFERENTLY («ائحة» vs «لائحة»), so the "
                "لائحة row is reachable only via the «نظام المنافسات» token. The "
                "routing lane's probe concluded 'no row exists' — refuted; the "
                "rows hide behind their own typos.",
        "status": "CONFIRMED",
    },
    {
        "id": "bab-03", "surface": "bab",
        "q": "اعطيني الباب العشرين من نظام العمل",  # نظام العمل has 16 أبواب — العاشر EXISTS (premise fixed after adv_family run)
        "setup": "pick a نظام whose باب count is known and < 10",
        "expect": "the synthesizer says the باب does not exist in the document",
        "trap": "renumbering/guessing — presenting some other section as الباب "
                "العاشر. The prompt forbids it; nothing has ever tested it.",
        "status": "PREDICTED",
    },
    # ── Case C phrasing ────────────────────────────────────────────────────
    {
        "id": "casec-01", "surface": "case_c",
        "q": "افتح المصدر رقم 3",
        "setup": "one attached agent_search WI with ≥3 refs",
        "expect": "the searcher selects the candidate whose preview carries [3]",
        "trap": "pre-fix CONFIRMED: previews dropped the [n] entirely — the "
                "user's panel numbers had no counterpart in the candidate "
                "lines. Post-fix: [n] is prefixed; this fixture pins it.",
        "status": "CONFIRMED",
    },
    {
        "id": "casec-02", "surface": "case_c",
        "q": "وش يقول المرجع [2]؟",
        "setup": "same as casec-01",
        "expect": "selects [2]'s candidate; answers from the OPENED source, "
                  "not the snippet",
        "trap": "router answers directly from the manifest snippet (the "
                "13b-eval failure in its bracket-number costume).",
        "status": "PREDICTED",
    },
    {
        "id": "casec-03", "surface": "case_c",
        "q": "افتح الحكم الثاني في القائمة",
        "setup": "WI with ≥2 case refs",
        "expect": "either the ref whose PANEL position is second, or ask_user — "
                  "never a silent guess keyed to internal candidate order",
        "trap": "candidate order and panel order are not guaranteed identical; "
                "an ordinal pick from the wrong ordering opens (and CHARGES) "
                "the wrong ruling.",
        "status": "PREDICTED",
    },
    {
        "id": "casec-04", "surface": "case_c",
        "q": "اش الحكم اللي في المذكرة؟",
        "setup": "an agent_writing WI whose refs were projected from a search WI "
                 "(writer publisher copies ref rows with source_wi/source_n)",
        "expect": "Case-C join works over the writing WI's copied refs",
        "trap": "the candidate collector only ever ran against agent_search "
                "items in testing; writing items' copied rows (nullable "
                "item_id? domain passthrough?) have never been through it.",
        "status": "PREDICTED",
    },
    {
        "id": "casec-05", "surface": "case_c",
        "q": "اعطيني تفاصيل حكم المغاسل",
        "setup": "TWO attached WIs, both citing the same مغاسل ruling",
        "expect": "one candidate after identity dedup → one synthesizer, one unlock",
        "trap": "the same ruling arrives as two candidates (one per WI); if "
                "dedup keys miss (case_ref vs cases.id), it fans out twice and "
                "charges twice for one document.",
        "status": "PREDICTED",
    },
    # ── corpus shape ───────────────────────────────────────────────────────
    {
        "id": "corpus-01", "surface": "corpus",
        "q": "اش قال الحكم الابتدائي والاستئنافي في قضية المحاصة؟",
        "setup": "a cases row carrying both ruling and appeal_* columns",
        "expect": "ONE document (the cases row holds both stages) → one "
                  "synthesizer covering both; NOT an integrative abort",
        "trap": "'two rulings' in the user's head = one row in the corpus. The "
                "abort rule fires on phantom plurality, or the searcher "
                "resolves two candidates for one row.",
        "status": "CONFIRMED",  # schema fact: appeal_* live on the same row
    },
    {
        "id": "corpus-02", "surface": "corpus",
        "q": "اعطيني المواد من 77 إلى 90 من نظام العمل",
        "setup": "none",
        "expect": "one document (D5) — but 14 article resolves vs "
                  "tool_calls_limit=10. Correct behaviour: resolve the parent "
                  "نظام once (like باب) or resolve a subset and SAY so.",
        "trap": "the searcher burns its tool budget mid-range and the turn "
                "dies, or silently serves 10 of 14 articles as if complete.",
        "status": "CONFIRMED",  # limit=10 vs 14 calls is arithmetic
    },
    {
        "id": "corpus-03", "surface": "corpus",
        "q": "وش يقول نظام الشركات القديم؟",
        "setup": "none",
        "expect": "resolve the current نظام الشركات but SAY the corpus holds "
                  "the current version; do not silently serve it as 'القديم'",
        "trap": "the resolver has no temporal sense — «القديم» drops out of "
                "coverage and the current law is served as if it were the old "
                "one. status_class exists and is unused here.",
        "status": "PREDICTED",
    },
    {
        "id": "corpus-04", "surface": "corpus",
        "q": "واللي بعدها؟",
        "setup": "previous turn opened المادة 77 من نظام العمل (history window "
                 "carries it)",
        "expect": "resolves المادة 78 of the same نظام via history",
        "trap": "needs arithmetic on article_number — fine for '77', undefined "
                "for '77-1' or '77 مكرر'. Also the history window must "
                "actually carry the referent (the F7 class).",
        "status": "PREDICTED",
    },
    {
        "id": "corpus-05", "surface": "corpus",
        "q": "وش يقول نظام المعاملات المدينة؟",  # typo: المدنية→المدينة. (Original «نظام العلم» is an EXACT title hit — the flag law — premise fixed after adv_family run)
        "setup": "none",
        "expect": "resolves نظام المعاملات المدنية (coverage survives one wrong "
                  "letter) or asks — NEVER a confident open of an unrelated "
                  "document",
        "trap": "typo lands inside another law's title-space; the whole "
                "fixture corpus contains zero misspellings, so tolerance is "
                "unmeasured.",
        "status": "PREDICTED",
    },
    # ── unlock / money ─────────────────────────────────────────────────────
    {
        "id": "unlock-01", "surface": "unlock",
        "q": "(any Case-A judgment lookup that resolves the WRONG ruling first)",
        "setup": "scripted: searcher resolves ruling X; synthesizer rejects "
                 "('not the one meant'); loop resolves ruling Y",
        "expect": "DESIGN QUESTION, surfaced not assumed: X's unlock was "
                  "charged at unfold time and there is no refund path. Fixture "
                  "asserts the CURRENT behaviour (2 unlocks) and flags it.",
        "trap": "the user pays for our mis-resolution and nothing in the "
                "reply says so.",
        "status": "CONFIRMED",  # charge-at-unfold + no refund path, by code
    },
    {
        "id": "unlock-02", "surface": "unlock",
        "q": "اعطيني تفاصيل الأحكام الثلاثة اللي في WI-2",
        "setup": "WI-2 cites ≥3 rulings; none previously unlocked",
        "expect": "3 unlocks in one message — legitimate, but the reply should "
                  "surface it («فُتحت ثلاثة أحكام من رصيدك»)",
        "trap": "silent multi-unlock: on /judgments each unlock is an explicit "
                "click; here one sentence spends three with no acknowledgment.",
        "status": "CONFIRMED",  # per-judgment charge in fan-out, by code
    },
    {
        "id": "unlock-03", "surface": "unlock",
        "q": "same as unlock-02",
        "setup": "quota state: exactly 2 unlocks remaining",
        "expect": "2 rulings open, 1 refuses with the Arabic quota line; the "
                  "reply distinguishes the served from the refused",
        "trap": "partial fan-out has never been rendered — three half-replies "
                "where one is a refusal could read as a system error.",
        "status": "PREDICTED",
    },
    # ── state collisions ───────────────────────────────────────────────────
    {
        "id": "state-01", "surface": "state",
        "q": "اش هي المادة 5؟   (bare — searcher will want ask_user)",
        "setup": "a deep_search planner pause is ALREADY OPEN on this "
                 "conversation (ask_user, unresolved)",
        "expect": "defined behaviour — either the searcher declines to pause "
                  "and answers/asks inline, or the old pause is resolved "
                  "first. NOT two pause rows, NOT a clobbered planner pause.",
        "trap": "find_open_pause holds ONE slot per conversation (§9 trap 10, "
                "documented, never tested). A searcher pause on top of a "
                "planner pause corrupts whichever resume comes second.",
        "status": "CONFIRMED",  # single-slot design is in paused_runs.py
    },
    {
        "id": "state-02", "surface": "state",
        "q": "خلاص، افتح نظام العمل   (replying to «أي حكم تقصد؟»)",
        "setup": "searcher paused on ask_user about ruling disambiguation",
        "expect": "the resume abandons the ruling question and serves the "
                  "regulation lookup — a redirect, not a forced answer",
        "trap": "the resumed searcher force-matches the reply against the old "
                "candidate list («لم أجد حكماً بهذا الاسم») instead of "
                "noticing the user changed the request.",
        "status": "PREDICTED",
    },
    # ── over-correction controls ───────────────────────────────────────────
    {
        "id": "ctrl-01", "surface": "control",
        "q": "اش رقم القضية اللي في WI-2؟",
        "setup": "WI-2 cites one ruling; the number is printed on its card",
        "expect": "router answers DIRECTLY — the number is card metadata, "
                  "dispatching a specialist for it is pure waste",
        "trap": "the 'a summary is not a reason to answer' rule over-fires "
                "onto metadata questions the card fully answers.",
        "status": "PREDICTED",
    },
    {
        "id": "ctrl-02", "surface": "control",
        "q": "هل الحكم اللي فتحته لي نهائي؟",
        "setup": "previous simple_search turn published a card holding the "
                 "FULL ruling text",
        "expect": "router unfolds that WI and answers directly — the card IS "
                  "a report now, and the full text is inside it. No second "
                  "dispatch, no second unlock.",
        "trap": "re-dispatching simple_search re-opens (and possibly "
                "re-meters) a ruling whose full text the conversation "
                "already holds.",
        "status": "PREDICTED",
    },
]


def by_surface(surface: str) -> list[dict]:
    return [f for f in FIXTURES if f["surface"] == surface]


__all__ = ["FIXTURES", "SURFACES", "by_surface"]
