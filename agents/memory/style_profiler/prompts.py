"""Prompts + renderer for the style_profiler.

``SYSTEM_PROMPT_AR`` is static (one cached prefix). ``build_user_message``
carries the per-run data: the previous profile and the user's recent messages
(already masked by the runner). ``render_profile_md`` produces the ≤600-char
block the unified responder appends at the END of its dynamic tail.
"""
from __future__ import annotations

import json
from typing import Any

from .models import StyleProfile

MAX_RENDERED_CHARS = 600
# Per-message clip for the LLM input — style shows in the first few lines;
# long pasted documents would only add cost and identifier surface.
MESSAGE_CLIP_CHARS = 600

SYSTEM_PROMPT_AR = """\
أنت «محلّل الأسلوب» في ريحان، وكيل ذاكرة داخلي لا يتحدث مع المستخدم.
مهمتك: قراءة رسائل المستخدم الأخيرة واستخلاص **أسلوبه في الكتابة فقط** — لا مضمون قضاياه —
ليحاكيه مساعد الرد في الإجابات القادمة.

أخرج الحقول التالية:
- register: مستوى اللغة الغالب: «فصحى رسمية» أو «عربية بيضاء» أو «سعودية دارجة» أو «مختلط».
- length_pref: الطول الذي يبدو أنه يفضّله للإجابة: short أو medium أو long.
  (رسائل مقتضبة وطلبات «باختصار» ← short؛ طلب تفصيل وشرح ← long؛ غير ذلك ← medium.)
- writes_as: counsel إذا كتب بصفته محامياً أو مستشاراً عن غيره («موكلي»، «عميلنا»)،
  party إذا كتب عن أمره الشخصي («عقدي»، «صاحب العمل فصلني»)، وإلا unknown.
- terms: حتى 8 مصطلحات نظامية يستعملها المستخدم **بلفظه حرفياً** (مثل «الشرط الجزائي» لا «غرامة التأخير»)
  ليستعملها الرد بنفس اللفظ. كل مصطلح 30 حرفاً على الأكثر.
- notes: ملاحظة أسلوبية قصيرة (200 حرف على الأكثر): النبرة، هل يحب النقاط أم الفقرات، هل يكتب بالإنجليزية أحياناً…

قواعد صارمة (حماية البيانات):
- ممنوع منعاً باتاً إدراج أي أسماء أشخاص أو جهات أو شركات، أو أرقام بأي صيغة، أو تواريخ، أو مبالغ،
  أو مدن وعناوين، أو بريد إلكتروني، أو أي وقائع من القضايا. الملف يصف **كيف** يكتب لا **عمّا** يكتب.
- المصطلحات مصطلحات نظامية عامة فقط، لا عبارات تكشف وقائع.
- قد تظهر في الرسائل أرقام أو عناوين بريد مموّهة؛ تجاهلها تماماً.
- إذا أُعطيت ملفاً سابقاً فحدّثه: أبقِ ما زال صحيحاً، وغيّر ما تغيّر بوضوح في الرسائل الأحدث.
- إذا كانت الرسائل قليلة أو غير كافية فاختر القيم المحايدة (مختلط / medium / unknown) ولا تخمّن.

أخرج النتيجة عبر أداة الإخراج فقط، أو ككائن JSON واحد بهذه الحقول دون أي نص خارجه.
"""


def build_user_message(messages: list[str], previous_profile: dict | None) -> str:
    """Per-run user message. ``messages`` are oldest-first and already masked."""
    parts: list[str] = []
    if previous_profile:
        try:
            prev = StyleProfile.model_validate(previous_profile).model_dump()
            parts.append("الملف السابق:\n" + json.dumps(prev, ensure_ascii=False))
        except Exception:  # noqa: BLE001 — a malformed stored profile is just ignored
            pass
    else:
        parts.append("الملف السابق: لا يوجد.")

    lines = []
    for i, body in enumerate(messages, start=1):
        text = " ".join((body or "").split())
        if not text:
            continue
        if len(text) > MESSAGE_CLIP_CHARS:
            text = text[:MESSAGE_CLIP_CHARS] + "…"
        lines.append(f"[{i}] {text}")
    parts.append("رسائل المستخدم الأخيرة (الأقدم أولاً):\n" + "\n".join(lines))
    return "\n\n".join(parts)


_REGISTER_HINT = {
    "فصحى رسمية": "فصحى رسمية — أجب بفصحى رسمية رصينة.",
    "عربية بيضاء": "عربية بيضاء — أجب بعربية واضحة غير متكلّفة.",
    "سعودية دارجة": "سعودية دارجة — أجب بعربية مبسّطة قريبة من لغته دون تكلّف، مع بقاء المصطلحات النظامية دقيقة.",
    "مختلط": "مختلط — وازن بين الفصحى المبسّطة ولغته.",
}
_LENGTH_HINT = {
    "short": "قصيرة ومباشرة",
    "medium": "متوسطة",
    "long": "مفصّلة",
}
_WRITES_AS_HINT = {
    "counsel": "محامٍ/مستشار عن موكل",
    "party": "صاحب الشأن نفسه",
    "unknown": "غير محدد",
}


def render_profile_md(profile: StyleProfile | dict[str, Any]) -> str:
    """Render the profile as a ≤600-char block for the responder's dynamic tail.

    Shrinks by dropping terms from the end, then the notes, then hard-truncates
    — the bound is a hard contract, the content is best-effort.
    """
    if not isinstance(profile, StyleProfile):
        profile = StyleProfile.model_validate(profile or {})

    terms = list(profile.terms)
    notes = profile.notes

    def _build(terms_: list[str], notes_: str) -> str:
        lines = [
            "〔أسلوب المستخدم — للمحاكاة فقط، لا تذكره للمستخدم〕",
            f"- اللغة: {_REGISTER_HINT[profile.register]}",
            f"- طول الإجابة المفضّل: {_LENGTH_HINT[profile.length_pref]}",
            f"- يكتب بصفته: {_WRITES_AS_HINT[profile.writes_as]}",
        ]
        if terms_:
            lines.append("- مصطلحاته (استعملها بلفظها): " + "، ".join(f"«{t}»" for t in terms_))
        if notes_:
            lines.append(f"- ملاحظات: {notes_}")
        return "\n".join(lines)

    md = _build(terms, notes)
    while len(md) > MAX_RENDERED_CHARS and terms:
        terms.pop()
        md = _build(terms, notes)
    if len(md) > MAX_RENDERED_CHARS:
        md = _build(terms, "")
    if len(md) > MAX_RENDERED_CHARS:
        md = md[: MAX_RENDERED_CHARS - 1] + "…"
    return md
