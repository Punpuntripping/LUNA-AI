"""Two-shot smoke test for agents/memory/artifact_summarizer.

Runs:
1. **useful content** — a tiny Arabic legal text → expect a real 3-section
   coverage summary.
2. **crappy content** — placeholder/test text → expect the agent to declare
   the artifact useless, NOT fabricate a fake summary.

Run from repo root: ``python -m scripts.smoke_artifact_summarizer``.
"""
from __future__ import annotations

import asyncio
import io
import sys

# Windows console defaults to cp1252 — force UTF-8 so Arabic output prints.
if hasattr(sys.stdout, "buffer"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", line_buffering=True)

from agents.memory.artifact_summarizer import (
    ArtifactSummaryInput,
    build_artifact_summary_deps,
    run_artifact_summary,
)

# ---------- Case 1: useful content ----------
USEFUL_QUERY = "ما عقوبة تعاطي المخدرات في النظام السعودي؟"
USEFUL_TITLE = "إجابة بحث عميق — عقوبات المخدرات"
USEFUL_BODY = """\
## الخلاصة

تنظّم اللائحة التنفيذية لنظام مكافحة المخدرات والمؤثرات العقلية آلية الإثبات
المخبري بدقة، حيث يُحدّد وزير الصحة المختبرات المعتمدة لإجراء التحاليل على
العينات المأخوذة من المتهمين، ويُشترط أن يعتمد التحليل خبيران مختصان لإثبات
نتيجة الكشف عن كُنّة المادة المضبوطة ونسبة خطورتها [1].

## العقوبات البديلة

في الجانب العقابي، أقرّ النظام بديلاً علاجياً عن العقوبة السالبة للحرية في
حالات معينة؛ فيجوز بدلاً من توقيع العقوبة النصّية إلزام متعاطي المواد
المخدرة أو المؤثرات العقلية ممن يثبت إدمانه بمراجعة عيادة نفسية متخصصة
لمساعدته على الإقلاع [2].

## الفجوات

لا تتضمن المراجع المتاحة سوابق قضائية من ديوان المظالم، ولا تتطرق إلى
التمييز الشرعي بين عقوبة الحدّ للخمر وعقوبة التعزير للمواد المخدرة.
"""

# ---------- Case 2: crappy content ----------
CRAP_QUERY = "ما عقوبة تعاطي المخدرات؟"
CRAP_TITLE = "نتيجة اختبار"
CRAP_BODY = "محتوى اختبار البحث — placeholder. لا يوجد نصّ قانوني فعلي هنا."


async def _run_one(label: str, describe_query: str, title: str, content_md: str) -> int:
    deps = build_artifact_summary_deps()
    output = await run_artifact_summary(
        ArtifactSummaryInput(
            describe_query=describe_query,
            content_md=content_md,
            title=title,
            kind="agent_search",
        ),
        deps,
    )

    print("=" * 60)
    print(f"CASE: {label}")
    print("=" * 60)
    print(f"model_used      : {output.model_used!r}")
    print(f"tokens_in       : {output.tokens_in}")
    print(f"tokens_out      : {output.tokens_out}")
    print(f"tokens_reasoning: {output.tokens_reasoning}")
    print(f"fallback_used   : {output.fallback_used}")
    print("-" * 60)
    print(output.summary_md)
    print("=" * 60)
    print()

    if not output.summary_md.strip():
        print(f"FAIL ({label}): empty summary", file=sys.stderr)
        return 1
    return 0


async def main() -> int:
    rc1 = await _run_one("USEFUL CONTENT", USEFUL_QUERY, USEFUL_TITLE, USEFUL_BODY)
    rc2 = await _run_one("CRAPPY CONTENT", CRAP_QUERY, CRAP_TITLE, CRAP_BODY)
    return rc1 or rc2


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
