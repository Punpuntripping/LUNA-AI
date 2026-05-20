"""One-shot smoke test for agents/memory/artifact_summarizer.

Sends a tiny Arabic legal text through the summarizer and prints the result.
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

_SAMPLE_QUERY = "ما عقوبة تعاطي المخدرات في النظام السعودي؟"
_SAMPLE_TITLE = "إجابة بحث عميق — عقوبات المخدرات"
_SAMPLE_BODY = """\
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


async def main() -> int:
    deps = build_artifact_summary_deps()
    input = ArtifactSummaryInput(
        original_query=_SAMPLE_QUERY,
        content_md=_SAMPLE_BODY,
        title=_SAMPLE_TITLE,
        kind="agent_search",
    )
    output = await run_artifact_summary(input, deps)

    print("=" * 60)
    print("ARTIFACT SUMMARIZER SMOKE TEST")
    print("=" * 60)
    print(f"model_used      : {output.model_used!r}")
    print(f"tokens_in       : {output.tokens_in}")
    print(f"tokens_out      : {output.tokens_out}")
    print(f"tokens_reasoning: {output.tokens_reasoning}")
    print(f"fallback_used   : {output.fallback_used}")
    print("-" * 60)
    print(output.summary_md)
    print("=" * 60)

    if not output.summary_md.strip():
        print("FAIL: empty summary", file=sys.stderr)
        return 1
    if output.fallback_used:
        print("WARN: fallback path was used (LLM call failed)", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
