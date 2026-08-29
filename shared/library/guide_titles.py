"""Service-guide titles — the CHANNEL rewrite, and the one place it is composed.

A guide's corpus title (`service_guides.title`) is pipeline-owned and reads
«الدليل الشامل: {الخدمة} في السعودية». The locale tail is the weakest 12
characters on the page: 445 of 533 titles carry it, so as a search keyword it
distinguishes a guide from nothing at all — every sibling has it too.

What a reader actually searches for is the CHANNEL: «الاطلاع على قضايا المنشأة
في بوابة ناجز», «إصدار رخصة بناء في منصة بلدي». That is also the word the guide
itself leads with. So the tail is replaced by the channel, and where a guide has
no branded channel, by the issuing entity — a real distinguisher either way.

⚠ WHY THE CHANNEL DOES NOT COME FROM ``services.service_url``
--------------------------------------------------------------
It is the obvious source and it is WRONG. 124 of وزارة العدل's 130 guides are
delivered through ناجز, but only 10 of them carry a `najiz.sa` service_url —
the other 114 sit on `moj.gov.sa`, the ministry's own domain. Deriving from the
host would have named the ministry on all 114 and missed the brand entirely, on
the exact example this rewrite exists to fix. The body knows: it prints
«**قناة التقديم:** بوابة ناجز (ناجز أعمال)» and names ناجز throughout.

⚠ AND WHY IT IS NOT A REGEX OVER «قناة التقديم» EITHER
-------------------------------------------------------
That field exists on only 247 of 533 guides, and its values are free text:
«بوابة ناجز الإلكترونية (najiz.sa).», «منصة "بلدي" الإلكترونية», «منصة بلدي
(النظام الموحد لوزارة البلديات والإسكان والمجتمعات)» — plus non-answers like
«الموقع الرسمي للصندوق» and «إلكتروني (افتراضي)», which name no brand at all.
Extraction is a judgement call, so `scripts/build_guide_channels.py` asks a
tier_2 model and this module is the pure half: normalisation, the shape gate,
the generic-phrase denylist, and the grounding check that keeps a hallucinated
brand out of a published `<title>`.

THIS MODULE IS PURE. No DB, no network, no LLM — so the script, the backend and
the tests all compose a title the same way, and `compose_guide_title` can be
exercised over the whole corpus offline.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Optional

# ─── The locale tail ──────────────────────────────────────────────────────────
# ORDER MATTERS: the long form is a superset of the short one only in meaning,
# not in text, but listing the longest first keeps this honest if a future tail
# ever nests. Measured live 2026-08-25: 445 titles end «في السعودية», 2 end
# «في المملكة العربية السعودية», 86 carry no locale tail at all.
_LOCALE_TAILS: tuple[str, ...] = (
    "في المملكة العربية السعودية",
    "في السعودية",
)

# The classifier words a channel name leads with. Stripped only to find the
# BRAND for the grounding check — never removed from the label itself, because
# «ناجز» alone reads as a bare word where «بوابة ناجز» reads as a place.
_CHANNEL_CLASSIFIERS: tuple[str, ...] = (
    "بوابة",
    "منصة",
    "تطبيق",
    "موقع",
    "نظام",
    "خدمة",
)

# ⚠ PHRASES THAT ARE NOT A BRAND. The corpus is full of these in the «قناة
# التقديم» slot, and every one of them, dropped into a title, produces
# «… في الموقع الإلكتروني» — strictly worse than the «في السعودية» it replaced,
# because it is both generic AND longer. The model is told to return null for
# these; this list is the second gate, because a denylist that only lives in a
# prompt is not a gate.
_GENERIC_CHANNELS: frozenset[str] = frozenset(
    {
        "الموقع الرسمي",
        "الموقع الإلكتروني",
        "الموقع الالكتروني",
        "البوابة الإلكترونية",
        "البوابة الالكترونية",
        "بوابة الخدمات الإلكترونية",
        "بوابة الخدمات الالكترونية",
        "الخدمات الإلكترونية",
        "الخدمات الالكترونية",
        "الخدمات",
        "المنصة الإلكترونية",
        "المنصة الالكترونية",
        "الموقع",
        "البوابة",
        "المنصة",
        "التطبيق",
        "إلكتروني",
        "الكتروني",
        "إلكترونيًا",
        "إلكترونيا",
        "الكترونيا",
        "حضوري",
        "افتراضي",
        # ⚠ THE VAGUE POSSESSIVE FAMILY. These name a thing by describing whose
        # it is, which is not a name. Listed as regression anchors — the
        # structural rule in ``_is_generic`` is what actually catches the family,
        # because its members are unbounded («… للبرنامج», «… للوزارة»,
        # «… للهيئة», «… للصندوق», «… للمركز», one per issuing body).
        "المنصة الإلكترونية للبرنامج",
        "البوابة الإلكترونية للوزارة",
        "الموقع الرسمي للصندوق",
        "الموقع الرسمي للهيئة",
        "البوابة الإلكترونية للهيئة",
    }
)

# ─── The structural test: does the label contain a NAME at all? ───────────────
# A branded channel is a PROPER NOUN with a classifier in front — «بوابة ناجز»,
# «منصة بلدي», «تطبيق صحتي». A vague one is built entirely out of common
# administrative vocabulary — «المنصة الإلكترونية للبرنامج», «بوابة الخدمات
# الإلكترونية لوزارة الموارد البشرية». So: strip the classifier, and if EVERY
# token that remains is a common word, nothing was named and the label is
# rejected.
#
# This is the generalisation of the denylist above. The possessive forms alone
# are one per government body — enumerating them is a losing game, and the one
# that gets missed is published as «… في المنصة الإلكترونية للبرنامج».
_COMMON_TOKENS: frozenset[str] = frozenset(
    {
        # classifiers and their definite forms
        "بوابه", "منصه", "موقع", "نظام", "تطبيق", "خدمه", "خدمات", "صفحه",
        "حساب", "قناه", "رابط", "برنامج", "مركز", "بنك", "صندوق", "هيئه",
        "وزاره", "مؤسسه", "ديوان", "امانه", "بلديه", "جهه", "ادارة", "اداره",
        # descriptive adjectives that never distinguish anything here
        "الكترونيه", "الكتروني", "رسمي", "رسميه", "وطني", "وطنيه", "موحد",
        "موحده", "عام", "عامه", "سعودي", "سعوديه", "حكومي", "حكوميه",
        "جديد", "جديده", "الشامل", "شامل", "التابع", "تابع", "خاص", "خاصه",
        "مستفيد", "المستفيد", "افراد", "اعمال", "الذاتيه", "ذاتيه",
        # the words the possessive constructions lean on
        "الموارد", "البشريه", "موارد", "بشريه", "التنميه", "الاجتماعيه",
        "تنميه", "اجتماعيه",
    }
)

# Prefixes stripped before a token is checked against ``_COMMON_TOKENS``.
# ORDER MATTERS: the longest first, or «للوزارة» loses only its «ل» and is
# compared as «لوزاره», which is in no list.
_TOKEN_PREFIXES: tuple[str, ...] = ("وال", "بال", "لل", "ال", "و", "ب", "ل")


def _token_variants(token: str) -> set[str]:
    """Every form a token might be recognised under — the folded word itself,
    plus each single leading article/preposition removed.

    ⚠ A SET, NOT ONE COMMITTED ANSWER. Returning a single "stripped" form is the
    obvious shape and it is wrong: «بوابة» folds to «بوابه», which begins with
    the preposition «ب», so committing to the strip yields «وابه» — the
    classifier stops matching the classifier list, is never removed, and
    «بوابة الخدمات الإلكترونية» sails through the structural rule as a brand.
    Arabic prefixes are ambiguous with word-initial letters, so the only safe
    move is to ask whether ANY reading is a known word.
    """
    t = _fold(token)
    variants = {t}
    for prefix in _TOKEN_PREFIXES:
        if t.startswith(prefix) and len(t) > len(prefix) + 1:
            variants.add(t[len(prefix) :])
    return variants


def _is_common(token: str) -> bool:
    return bool(_token_variants(token) & _COMMON_TOKENS)

_MAX_CHANNEL_WORDS = 4
_MAX_CHANNEL_CHARS = 32

# A parenthetical is ALWAYS a gloss in this corpus — a domain («(najiz.sa)»), a
# sub-brand («(ناجز أعمال)») or an explanation. None of them belong in a title.
_PAREN_RE = re.compile(r"[（(\[][^）)\]]*[）)\]]")
# Trailing sentence punctuation the corpus sprinkles on the field value.
_TRAILING_PUNCT_RE = re.compile(r"[\s\.،,;:؛\-–—]+$")
_LEADING_PUNCT_RE = re.compile(r"^[\s\.،,;:؛\-–—]+")
_QUOTES_RE = re.compile(r"[\"'«»“”„‟‘’]")
_WS_RE = re.compile(r"\s+")

# Tatweel + Arabic diacritics: stripped for COMPARISON only, never from output.
_DIACRITICS_RE = re.compile(r"[ـً-ْٰۖ-ۭ]")


def _fold(text: str) -> str:
    """Comparison form: NFKC, diacritics gone, alef/ya/ta-marbuta unified.

    Used by the denylist and the grounding check ONLY. A guide body writes
    «ناجز» and a model may answer «نَاجِز»; treating those as different would
    reject a correct answer. Never use this on a value that will be displayed —
    it destroys the harakah that `shared/library/entities.py` warns about.
    """
    folded = unicodedata.normalize("NFKC", text or "")
    folded = _DIACRITICS_RE.sub("", folded)
    folded = (
        folded.replace("أ", "ا")
        .replace("إ", "ا")
        .replace("آ", "ا")
        .replace("ى", "ي")
        .replace("ة", "ه")
    )
    return _WS_RE.sub(" ", folded).strip().lower()


def _is_generic(text: str) -> bool:
    """True when ``text`` names no brand — bare, or a classifier glued to one.

    Shared by ``normalize_channel`` and ``channel_shape_error`` ON PURPOSE. They
    used to disagree: normalisation stripped a trailing «الإلكترونية»
    unconditionally, so «الخدمات الإلكترونية» arrived at the gate as «الخدمات»,
    which was not on the denylist BY THAT SPELLING and sailed through as a
    "brand". The wing would have published «… في الخدمات». One predicate, asked
    at both ends, is what stops a cleanup step from manufacturing an answer.
    """
    folded = _fold(text)
    if not folded:
        return True
    generics = {_fold(g) for g in _GENERIC_CHANNELS}
    if folded in generics:
        return True

    tokens = [t for t in folded.split() if t]
    # Drop the leading classifier, definite or not («بوابة» / «البوابة»).
    classifiers = {_fold(c) for c in _CHANNEL_CLASSIFIERS}
    while tokens and (_token_variants(tokens[0]) & classifiers):
        tokens = tokens[1:]

    if not tokens:
        return True
    if " ".join(tokens) in generics:
        return True

    # THE STRUCTURAL RULE: a brand follows its classifier IMMEDIATELY —
    # «منصة بلدي», «بوابة ناجز», «تطبيق صحتي». So if the very next word is
    # common administrative vocabulary, the label is DESCRIBING a portal, not
    # naming one, and everything after that word is more description.
    #
    # Checking the FIRST token rather than ALL of them is what catches «منصة
    # الخدمات التجارية»: «التجارية» is not a common word by itself, so an
    # all-tokens test passed it as a brand — but «الخدمات» in the name slot has
    # already given the game away.
    if _is_common(tokens[0]):
        return True
    return all(_is_common(t) for t in tokens)


def normalize_channel(raw: Optional[str]) -> str:
    """A raw «قناة التقديم» value or model answer → a title-ready label.

    Drops the gloss in parentheses, the quote marks, the trailing full stop and
    the redundant «الإلكترونية» — «بوابة ناجز الإلكترونية (najiz.sa).» becomes
    «بوابة ناجز». Returns ``""`` for anything that is empty once cleaned, which
    the caller treats as "no channel" and falls back on the entity.

    Deliberately NOT diacritic-folding: this string is DISPLAYED. Folding is for
    comparison only (`_fold`).
    """
    text = _QUOTES_RE.sub("", raw or "")
    text = _PAREN_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    text = _LEADING_PUNCT_RE.sub("", text)
    text = _TRAILING_PUNCT_RE.sub("", text)

    # «الإلكترونية» / «الالكترونية» carries no information once the brand is
    # named — every one of these channels is electronic; that is what a channel
    # IS here. Stripped only as a trailing word, so «الخدمات الإلكترونية» (which
    # is generic and gets rejected below) is not silently turned into
    # «الخدمات».
    # ⚠ ONLY when a brand survives it. Stripping unconditionally turns the
    # generic «الخدمات الإلكترونية» into «الخدمات», which reads like a name and
    # is not on the denylist under that spelling — see ``_is_generic``.
    for suffix in ("الإلكترونية", "الالكترونية", "الإلكتروني", "الالكتروني"):
        if text.endswith(" " + suffix):
            candidate = text[: -(len(suffix) + 1)].rstrip()
            if not _is_generic(candidate):
                text = candidate
            break

    # ⚠ THE TAIL CAN COME BACK IN THROUGH THE CHANNEL. A live proposal was
    # «بوابة ادرس في السعودية», which composes to
    # «… طلب منحة داخلية في بوابة ادرس في السعودية» — the very tail this rewrite
    # exists to remove, reintroduced one field to the left. Strip it here, where
    # the label is built, rather than trusting every caller to notice.
    text = strip_locale_tail(text)
    return _TRAILING_PUNCT_RE.sub("", text).strip()


def channel_shape_error(channel: str) -> Optional[str]:
    """``None`` when ``channel`` is usable as a title tail, else why not.

    The gates, in the order they catch things: non-empty · at most
    ``_MAX_CHANNEL_WORDS`` words · at most ``_MAX_CHANNEL_CHARS`` characters ·
    no Latin (a domain or an English product name that slipped the parenthetical
    strip) · not a generic phrase, bare or classifier-prefixed.
    """
    text = (channel or "").strip()
    if not text:
        return "empty"

    words = text.split()
    if len(words) > _MAX_CHANNEL_WORDS:
        return f"too many words ({len(words)} > {_MAX_CHANNEL_WORDS})"
    if len(text) > _MAX_CHANNEL_CHARS:
        return f"too long ({len(text)} > {_MAX_CHANNEL_CHARS} chars)"
    if re.search(r"[A-Za-z]", text):
        return "contains Latin characters (a domain or an English name)"
    if re.search(r"\d", text):
        return "contains digits"

    if _is_generic(text):
        return f"generic phrase, names no brand: «{text}»"
    return None


def channel_brand(channel: str) -> str:
    """The distinguishing part of a channel label — the classifier removed.

    «بوابة ناجز» → «ناجز». Used for the grounding check: the CLASSIFIER is ours
    (we normalise «موقع ناجز» and «بوابة ناجز» to one shape), so requiring it to
    appear verbatim in the body would reject correct answers. The brand is the
    part the body must actually contain.

    STACKED classifiers are peeled all the way down: a live proposal was «بوابة
    نظام معين», whose brand is «معين» — stopping after one strip leaves «نظام
    معين», which folds differently from «معين» and survives
    ``canonicalize_channels`` as a SEPARATE portal. One system, two names on the
    hub.
    """
    text = (channel or "").strip()
    changed = True
    while changed:
        changed = False
        for classifier in _CHANNEL_CLASSIFIERS:
            if text.startswith(classifier + " "):
                remainder = text[len(classifier) + 1 :].strip()
                # Never peel down to nothing — «منصة الخدمات» must keep a brand
                # slot for ``_is_generic`` to judge.
                if remainder:
                    text = remainder
                    changed = True
                break
    return text


def channel_is_grounded(channel: str, guide_md: Optional[str]) -> bool:
    """True when the guide body actually names this channel.

    THE ANTI-HALLUCINATION GATE, and the reason this rewrite is safe to publish
    unreviewed on 533 pages. A model asked «which portal delivers this service?»
    will happily answer «بوابة أبشر» for a service it has merely seen next to
    أبشر in training data. A brand that does not appear in OUR OWN authored body
    is not evidence of anything, so it is discarded and the guide falls back to
    its entity — the boring answer, but never a false one.

    Compared in folded form so harakah and alef spelling cannot cause a false
    negative.
    """
    brand = channel_brand(channel)
    if not brand:
        return False
    return _fold(brand) in _fold(guide_md or "")


def canonicalize_channels(labels: "list[str]") -> "dict[str, str]":
    """One portal, one spelling — a label → canonical-label map over a whole run.

    ⚠ WHY THIS EXISTS. The extractor is asked one guide at a time and has no
    memory between calls, so the SAME portal comes back under whichever
    classifier that guide's body happened to use. Measured over the live 533 on
    2026-08-25: بلدي arrived as «منصة بلدي» ×125, «بوابة بلدي» ×8 and «تطبيق
    بلدي» ×2; ناجز as «بوابة ناجز» ×121 and «منصة ناجز» ×3; معين as «منصة معين»,
    «نظام معين» AND «منصة مُعين» — three spellings of one system, one of them
    differing only by a damma. Shipped as-is, a reader browsing the hub sees the
    same portal named three ways and the BM25 index splits its own term.

    The vote is by BRAND (`channel_brand`, folded), so the classifier is what
    gets normalised and a genuine sub-brand keeps its own identity: «بلدي» and
    «بلدي أعمال» fold differently and stay two portals.

    Winner = most frequent surface form, ties broken by the label with the most
    frequent classifier and then lexicographically. DETERMINISTIC — the same
    input list always yields the same map, so a dry-run is a preview of the
    apply rather than a sample of it.
    """
    counts: dict[str, dict[str, int]] = {}
    for raw in labels:
        label = (raw or "").strip()
        if not label:
            continue
        key = _fold(channel_brand(label))
        if not key:
            continue
        counts.setdefault(key, {})
        counts[key][label] = counts[key].get(label, 0) + 1

    mapping: dict[str, str] = {}
    for variants in counts.values():
        winner = sorted(variants.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        for label in variants:
            mapping[label] = winner
    return mapping


MIN_ATTESTATIONS = 3
"""How many guides must name a brand as their channel, WITHOUT it appearing in
their own title, before it counts as a real portal. Three is the smallest number
that cannot be produced by one guide's vocabulary echoing twice."""


def attested_channels(labels: "list[str]") -> "frozenset[str]":
    """The folded brands that the corpus itself vouches for as real portals.

    ⚠ THE SIGNAL THAT SEPARATES A PORTAL FROM A RECYCLED SERVICE NAME.
    ``brand_already_in_title`` knows that a channel appearing in its own guide's
    title must not be appended — but it cannot tell WHY, and the two whys need
    opposite handling:

      * «إصدار ترخيص صناعي» → «منصة صناعي» is INVENTED out of the title. There is
        no such portal; the guide should fall back to its issuing entity.
      * «… في السعودية عبر ناجز» → «بوابة ناجز» is CORRECT and already stated.
        The title should simply be left alone — appending the ministry instead
        gives «… عبر ناجز في وزارة العدل», which is clumsy and drops the brand
        the reader was looking for.

    Told apart by counting: pass the labels from guides where the brand was NOT
    in the title, and a real portal shows up many times (ناجز 108, بلدي 130)
    while an invented one shows up never. Measured live 2026-08-26.
    """
    counts: dict[str, int] = {}
    for raw in labels:
        brand = _fold(channel_brand(raw or ""))
        if brand:
            counts[brand] = counts.get(brand, 0) + 1
    return frozenset(b for b, n in counts.items() if n >= MIN_ATTESTATIONS)


def brand_already_in_title(channel: str, title: Optional[str]) -> bool:
    """True when the channel's brand already appears in the guide's own title.

    ⚠ THE GATE THE GROUNDING CHECK CANNOT BE. Grounding asks "is this word in the
    body?" — and the service's OWN NAME is always in the body, so a model that
    recycles it as a portal passes. Measured on the live apply 2026-08-25, that
    produced «إصدار ترخيص صناعي في منصة صناعي», «خدمة إصدار رخصة فال … في منصة
    فال», «القبول الموحد … في منصة قبول», «منصة خبير للتدريب التعاوني في منصة
    خبير» — a "channel" invented out of the title it was about to be appended to.
    27 of 533 titles were affected.

    It also catches the honest version of the same shape: a title that ALREADY
    names its channel («… في السعودية عبر ناجز») must not have it appended a
    second time.

    Substring, not word-boundary: Arabic glues prefixes to words, so «بلدي»
    inside «البلديات» matches. That is the SAFE direction to err — the
    consequence is declining to append, never appending something wrong.
    """
    brand = channel_brand(channel)
    if not brand:
        return False
    return _fold(brand) in _fold(title or "")


def strip_locale_tail(title: str) -> str:
    """The corpus title with a trailing «في السعودية» removed, if present.

    Anchored at the END only. One live title carries «في السعودية» MID-string
    («… للمحامي في السعودية: عرض قائمة …») and must not be touched — the phrase
    there is part of the sentence, not a tail.
    """
    text = (title or "").strip()
    for tail in _LOCALE_TAILS:
        if text.endswith(tail):
            return text[: -len(tail)].rstrip()
    return text


# ⚠ STRUCTURAL FILLER INSIDE AN ENTITY NAME — words that carry no identity.
# `أمانة محافظة جدة` and `أمانة جدة` are the same body; a corpus title writes the
# short form and the canonical `provider_name` writes the long one, so plain
# containment does not see the repetition. Removing ONLY these words can never
# turn one body's name into another's — every distinguishing token survives — so
# a short form is safe to test for. See ``_label_short_forms``.
_ENTITY_FILLER: frozenset[str] = frozenset(
    {"محافظة", "منطقة", "العامة", "العام", "الوطني", "الوطنية", "الملكية",
     "السعودية", "السعودي"}
)


def _label_short_forms(label: str) -> list[str]:
    """The label plus the shorter ways the corpus writes the SAME body.

    Built by dropping structural filler (``_ENTITY_FILLER``) — one word at a
    time and then all of them — never a distinguishing token. «أمانة محافظة جدة»
    yields «أمانة جدة»; «الهيئة العامة للأوقاف» yields «الهيئة للأوقاف», which
    no title says, so it costs nothing and matches nothing.
    """
    words = (label or "").split()
    if not words:
        return []
    forms = {label}
    filler_at = [i for i, w in enumerate(words) if w in _ENTITY_FILLER]
    for i in filler_at:
        forms.add(" ".join(words[:i] + words[i + 1 :]))
    if len(filler_at) > 1:
        forms.add(" ".join(w for w in words if w not in _ENTITY_FILLER))
    # A form that lost every distinguishing word would match anything.
    return [f for f in forms if len(f.split()) >= 2]


def compose_guide_title(corpus_title: str, label: Optional[str]) -> str:
    """``corpus_title`` with its locale tail replaced by «في {label}».

    ``label`` is the channel where the guide has one and the issuing entity
    otherwise; this function does not care which, and deliberately has no
    opinion on it — the choice is made once, at build time, and stored.

    THE PREFIX IS UNTOUCHED. The title keeps «الدليل الشامل: …» exactly as the
    corpus wrote it, because the frontend's `guideDisplayTitle` rewrites that
    prefix to the «بالصور» form and would stop matching if this function
    normalised it. One rewrite per title, at opposite ends of the string.

    Three ways this returns the title unchanged, all of them anti-stutter:
      * no ``label`` — nothing to say, so the locale tail STAYS (a title ending
        in a bare «…» would be worse than the generic tail it replaced);
      * the title already CONTAINS the label, anywhere — «حجز موعد إلكتروني في
        وزارة الموارد البشرية والتنمية الاجتماعية» names its own ministry, and
        appending the entity again would double it. ⚠ OR A SHORTER FORM OF IT:
        five live titles said «… في أمانة جدة» while the canonical
        ``provider_name`` is «أمانة محافظة جدة», so containment on the full label
        missed a body the title had already named and shipped «المواعيد في أمانة
        جدة في أمانة محافظة جدة». ``_label_short_forms`` drops the structural
        filler and never a distinguishing token, so this cannot match a
        DIFFERENT body;
      * the title already contains the label's BRAND — «… عبر ناجز» must not
        become «… عبر ناجز في بوابة ناجز».

    ⚠ CONTAINMENT, NOT ``endswith``. The first version of this function checked
    only the end of the string, and 27 live titles stuttered because the brand
    sat one preposition earlier («… في السعودية عبر ناجز») or in the middle
    («بلدي أعمال في منصة بلدي أعمال»). The entity fallback needs it just as
    much: «لوحة التحكم في وزارة البلديات والإسكان» would otherwise have the
    same ministry appended to it.
    """
    text = (corpus_title or "").strip()
    lab = (label or "").strip()
    if not text or not lab:
        return text

    stripped = strip_locale_tail(text)
    if not stripped:
        return text

    folded_title = _fold(stripped)
    if any(_fold(form) in folded_title for form in _label_short_forms(lab)):
        return stripped
    if brand_already_in_title(lab, stripped):
        return stripped

    return f"{stripped} في {lab}"


__all__ = [
    "MIN_ATTESTATIONS",
    "attested_channels",
    "brand_already_in_title",
    "canonicalize_channels",
    "channel_brand",
    "channel_is_grounded",
    "channel_shape_error",
    "compose_guide_title",
    "normalize_channel",
    "strip_locale_tail",
]
