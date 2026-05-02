"""Writer prompt variants -- one per subtype.

All prompts share:
    - Arabic-only output rule (CLAUDE.md rule #5)
    - Section-based structured output (WriterSection)
    - Inline citation rule when research_items are provided: cite by (n)
      where n is the index into the merged references[] of the supplied
      agent_search items.
    - No legal disclaimer in body -- appended at render time.
"""
from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .models import WriterInput


def _esc(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=False)


_SHARED_ROLE_AR = """\
أنت كاتب قانوني محترف ضمن منصة لونا للذكاء الاصطناعي القانوني السعودي.
مهمتك صياغة مستندات قانونية مكتملة بالعربية الفصحى، استناداً إلى:
1. طلب المستخدم (الفقرة الأولى من الرسالة).
2. بحث قانوني مرفق (إن وُجد) في قسم <research> -- تحتوي مراجع مرقّمة.
3. ملاحظات وملفات سياق المستخدم في قسم <workspace_context>.

## قواعد عامة

- المستند بالعربية الفصحى المبسّطة، مع الحفاظ على المصطلحات القانونية الدقيقة.
- لا تخترع أنظمة أو مواد أو أسماء جهات. استشهد فقط بما ورد في <research>.
- إن طلب المستخدم صياغة عقد، ضمِّن الأطراف، الموضوع، الالتزامات، البنود، التوقيع.
- إن طلب صياغة مذكّرة، اتبع نمط IRAC أو CRAC حسب طبيعة الطلب.
- لا تُدرج إخلاء المسؤولية القانونية داخل المستند -- يُضاف برمجياً.
- استخدم الاستشهاد الرقمي `(n)` فقط حين توجد <research> ومرجع مطابق فعلاً.
"""


_SUBTYPE_BODIES_AR: dict[str, str] = {
    "contract": """\
## النمط: عقد رسمي

- ابدأ بـ "بسم الله الرحمن الرحيم" ثم عنوان العقد ثم تاريخ ومكان التحرير.
- قسّم العقد إلى: **الأطراف**، **التمهيد**، **الموضوع**، **الالتزامات والشروط**،
  **مدة العقد**، **التعويض والقيمة**، **حلّ النزاعات**، **التوقيعات**.
- استخدم العبارات النمطية للعقود السعودية ("اتفق الطرفان على ما يلي ...").
""",
    "memo": """\
## النمط: مذكّرة قانونية رسمية

اتبع IRAC: المسألة → القاعدة → التطبيق → النتيجة.
كل قسم بعنوان `## ...`. استشهد بكل ادعاء قاعدةَ كان مدعوماً بمرجع.
""",
    "legal_opinion": """\
## النمط: رأي قانوني

- مقدمة موجزة عن طبيعة الاستشارة.
- عرض الوقائع كما قدّمها المستخدم.
- تحليل قانوني مدعّم بالمراجع.
- التوصية النهائية مع التحفظات الصريحة.
""",
    "defense_brief": """\
## النمط: مذكّرة دفاع

- بيانات القضية (رقم، محكمة، أطراف).
- الوقائع.
- الدفوع الشكلية ثم الموضوعية.
- الطلبات الختامية.
""",
    "letter": """\
## النمط: خطاب رسمي

- ترويسة (المرسِل، المرسَل إليه، الموضوع، التاريخ).
- جسم الخطاب (فقرات منظّمة، نبرة احترامية).
- الخاتمة والتوقيع.
""",
    "summary": """\
## النمط: ملخّص

- عناوين موضوعية (## ...).
- نقاط مختصرة، لا تكرار للمصدر.
- إشارة صريحة للملف أو الملاحظة المُستخلَصة منها.
""",
}


_OUTPUT_CONTRACT_AR = """\
## مخطط المخرج

أعِد JSON مطابقاً تماماً لهذا الهيكل (بلا أي نص خارج JSON):

```
{
  "title_ar": "عنوان المستند",
  "sections": [
    {"heading_ar": "## الأطراف", "body_md": "..."},
    {"heading_ar": "## الموضوع", "body_md": "..."}
  ],
  "citations_used": [1, 3, 5],
  "confidence": "high | medium | low",
  "notes_ar": ["نقطة تحتاج مراجعة المستخدم", "..."],
  "chat_summary": "جملة أو جملتان تصفان المستند المُسوَّد — 500 حرف كحد أقصى.",
  "key_findings": [
    "أبرز نقطة يجب على المستخدم مراجعتها",
    "نقطة ثانية",
    "نقطة ثالثة"
  ]
}
```

- `sections` مرتّبة كما ستظهر في المستند النهائي.
- لا تكرّر العنوان الكامل في `sections[0]` -- يُضاف من `title_ar`.
- `citations_used` تشمل فقط الأرقام التي ظهرت كـ `(n)` داخل أي body_md.
- `chat_summary`: وصف موجز للمستند في **500 حرف كحد أقصى صارم**. لا تُعِد صياغة المستند كاملاً.
- `key_findings`: **3 إلى 5 بنود كحد أقصى صارم**. كل بند نقطة تحتاج إلى انتباه المستخدم أو مراجعة. لا تتجاوز 5 بنود بأي حال.
"""


WRITER_PROMPTS: dict[str, str] = {
    sub: f"{_SHARED_ROLE_AR}\n{body}\n{_OUTPUT_CONTRACT_AR}"
    for sub, body in _SUBTYPE_BODIES_AR.items()
}


def get_writer_prompt(subtype: str) -> str:
    """Fetch a system prompt by subtype; raises KeyError on unknown subtypes."""
    if subtype not in WRITER_PROMPTS:
        raise KeyError(
            f"Unknown writer subtype: {subtype!r}. "
            f"Available: {sorted(WRITER_PROMPTS.keys())}"
        )
    return WRITER_PROMPTS[subtype]


def _ws_context_get(ctx: object, key: str, default):
    """Read a field from either a dict or a dataclass-like context block."""
    if ctx is None:
        return default
    if isinstance(ctx, dict):
        return ctx.get(key, default)
    return getattr(ctx, key, default)


def build_writer_user_message(writer_in: "WriterInput") -> str:
    """Render a WriterInput as the LLM user message.

    Layout:
        <user_request>...</user_request>
        <research>
          <ref n="1">...</ref>
          <ref n="2">...</ref>
        </research>
        <workspace_context>
          <note title="...">...</note>
          <attachment title="...">...</attachment>
        </workspace_context>
        <preferences detail_level="..." tone="..." />

    The numbering inside <research> is *global* across all research_items so
    the LLM's `(n)` citations resolve unambiguously.
    """
    lines: list[str] = []

    lines.append("<user_request>")
    lines.append(_esc(writer_in.user_request.strip()))
    lines.append("</user_request>")
    lines.append("")

    if writer_in.research_items:
        lines.append("<research>")
        global_n = 0
        for item in writer_in.research_items:
            refs = (item.get("metadata") or {}).get("references") or []
            for ref in refs:
                global_n += 1
                title = ref.get("title") or ref.get("regulation_title") or ""
                snippet = ref.get("snippet") or ""
                lines.append(f"  <ref n=\"{global_n}\">")
                lines.append(f"    <title>{_esc(title)}</title>")
                lines.append(f"    <snippet>{_esc(snippet)}</snippet>")
                lines.append(f"  </ref>")
        lines.append("</research>")
        lines.append("")

    if writer_in.workspace_context is not None:
        ctx = writer_in.workspace_context
        notes = _ws_context_get(ctx, "notes", []) or []
        attachments = _ws_context_get(ctx, "attachments", []) or []
        if notes or attachments:
            lines.append("<workspace_context>")
            for note in notes:
                title = note.get("title", "") if isinstance(note, dict) else ""
                body = note.get("content_md", "") if isinstance(note, dict) else ""
                lines.append(f"  <note title=\"{_esc(title)}\">")
                lines.append(_esc((body or "").strip()))
                lines.append("  </note>")
            for att in attachments:
                title = att.get("title", "") if isinstance(att, dict) else ""
                extracted = (
                    att.get("extracted_text", "") if isinstance(att, dict) else ""
                )
                lines.append(f"  <attachment title=\"{_esc(title)}\">")
                if extracted:
                    lines.append(_esc(extracted.strip()))
                lines.append("  </attachment>")
            lines.append("</workspace_context>")
            lines.append("")

    lines.append(
        f"<preferences detail_level=\"{_esc(writer_in.detail_level)}\" "
        f"tone=\"{_esc(writer_in.tone)}\" />"
    )
    lines.append("")
    lines.append("اكتب المسوّدة الكاملة وفق المخطط أعلاه.")

    return "\n".join(lines)


__all__ = [
    "WRITER_PROMPTS",
    "get_writer_prompt",
    "build_writer_user_message",
]
