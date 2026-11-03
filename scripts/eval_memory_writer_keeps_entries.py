"""Does a memory entry stop the real writer dropping what the user said?

The biggest risk in ``.claude/plans/conversation_memory_agent.md`` (§8, §10 step 3).
Three groups on the current code, five runs each, graded in code on the document the
publisher stored:

* ``B_control``: no memory at all. The payment is in the plan only and the previous
  draft left it out. Shows whether an entry does anything beyond the plan.
* ``B_long``: the same request on a 10,000 to 12,000 character document with ``(n)``
  citations, among 20 entries.
* ``C_long``: the same document with the user asking for about half the length. A run
  counts only at 70% of the original length or less. The argument's conclusion, its
  reasoning and the corrected claim total are separate lines, because
  ``_MEMORY_DRAFTING_NOTE`` asks for all three.

* ``D_long``: the rule's opposite. The same document and request as ``C_long``, with
  entries holding sums that must NOT enter the claim: one the user is not claiming, a
  fee paid to someone else, and a separate finished contract with the same defendant.
  The claim and the relief must stay exactly as they were.

An earlier round compared the old and new ``_BLOCK_RULES``. The rule has since moved
into that note, so that comparison no longer applies.

``--groups C_long`` runs one group, and the call cap follows the groups chosen.

    python scripts/eval_memory_writer_keeps_entries.py --env-file /path/to/.env --out run.json
    python scripts/eval_memory_writer_keeps_entries.py --env-file /path/to/.env --out run.json --resume
    python scripts/eval_memory_writer_keeps_entries.py --dry-run --out dry.json
    python scripts/eval_memory_writer_keeps_entries.py --regrade run.json --out regraded.json

``--dry-run`` swaps the writer's model for a script, for checking the harness with
no key and no cost. ``--regrade`` re-runs the grading on saved runs.

The path a run takes is production's: ``handle_writer_planner_turn`` on the fake DB,
so the real walker builds the ``WriterPackage``, the real runner builds the writer's
deps, ``handle_writer_turn`` calls the real ``agent_writer`` chain, and the real
publisher stores the document. Two things are scripted. The planner's model returns a
fixed ``PlannerDecision``, because each group states what the brief holds: it makes no
LLM call and is not a writer call. And ``fetch_item_references`` returns the fixture's
references, because the real one needs corpus tables; the walker's rendering and the
``<refs>`` block stay real. A prior draft gets no ``<refs>`` in production either, so
the long package carries a research item for its ``(n)`` citations to resolve against.

Safety, all from ``scripts/eval_conversation_memory.py``: an in-memory fake DB, dummy
Supabase settings, an empty working directory, ``--env-file`` loading only
``*_API_KEY`` names, DNS limited to the chain's LLM hosts, and values scrubbed from
everything printed or written.

Call budget. ``BUDGET.cap`` is enforced on the HTTP client, so a provider retry
counts: a request that would pass the cap raises before it leaves the machine, and a
request with no answer stays counted, because it may have been billed. There are no
per-cell probes, since each would be a writer call. ``--resume`` carries the count
over from the saved JSON, and a failed run is reported and never run again. A run the
cap stops is named in ``not_run``.

Fixtures are invented. No value may appear in the prompts the writer reads or in the
memory agent's examples: :func:`check_values_are_invented` refuses to run otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import re
import sys
import tempfile
import time
from collections import Counter
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

sys.dont_write_bytecode = True  # no __pycache__ left behind in the worktree
os.environ.setdefault("LOGFIRE_IGNORE_NO_CONFIG", "1")

_ROOT = Path(__file__).resolve().parent.parent
for _path in (str(_ROOT), str(_ROOT / "scripts")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

import eval_conversation_memory as base  # noqa: E402  (imports no agents at module level)

RUNS_PER_GROUP = 5
FAILED_DRAFT_TITLE = "مسوّدة غير مكتملة"  # ``_run_writer``'s placeholder when both tries fail

USER_ID = base.USER_ID
CONVO_ID = base.CONVO_ID
ASSISTANT_MESSAGE_ID = "00000000-0000-4000-8000-000000000003"
ATTACHMENT_ID = "00000000-0000-4000-8000-00000000000a"
DRAFT_ID = "00000000-0000-4000-8000-00000000000b"
RESEARCH_ID = "00000000-0000-4000-8000-00000000000c"
ATTACHMENT_ALIAS = "WI-1"


def _n(value: int) -> str:
    return f"{value:,}"


# --------------------------------------------------------------------------- #
# Fixtures. Invented people, companies, numbers and documents.
# --------------------------------------------------------------------------- #

FIRST_PAYMENT, SECOND_PAYMENT, THIRD_PAYMENT = 32_417, 27_650, 20_455
OLD_TOTAL = FIRST_PAYMENT + SECOND_PAYMENT + THIRD_PAYMENT  # 80,522
OLD_TOTAL_WORDS = "ثمانون ألفاً وخمسمائة واثنان وعشرون ريالاً"
#: The payment the previous draft left out. It adds to the total, so the old total
#: is not a natural subtotal of a corrected table, and seeing it means it stayed.
FOURTH_PAYMENT = 18_735
FOURTH_PAYMENT_DATE = "2026/06/17"
CORRECTED_TOTAL = OLD_TOTAL + FOURTH_PAYMENT  # 99,257
CONTRACT_VALUE = 146_900


@dataclass(frozen=True)
class Research:
    """A search item with used references, so a draft's ``(n)`` can resolve."""

    title: str
    summary: str
    body: str
    references: tuple[tuple[int, str, str], ...]  # (n, title, snippet)


@dataclass(frozen=True)
class Matter:
    parties: tuple[tuple[str, str], ...]
    entries: tuple[tuple[str, str], ...]  # (kind, content) -> M-1, M-2, ...
    attachment_title: str
    attachment_summary: str
    attachment_text: str
    draft_title: str
    draft_summary: str
    sections: tuple[tuple[str, str], ...]  # the previous draft, as the writer returned it
    research: Research | None = None


CLAIM_MATTER = Matter(
    parties=(
        ("لمى الحارثي", "موكّلة المحامي، المدعية"),
        ("مؤسسة مدى الأرجوان للتشطيبات", "المدعى عليها"),
    ),
    entries=(
        ("fact", "المستخدم محامٍ وموكلته لمى الحارثي، والخصم مؤسسة مدى الأرجوان للتشطيبات."),
        ("request", "تريد لمى الحارثي فسخ عقد تشطيب الفيلا واسترداد ما دفعته للمؤسسة."),
        (
            "fact",
            f"سلّمت لمى الحارثي مدير مشروع المؤسسة دفعة نقدية مقدارها {_n(FOURTH_PAYMENT)} ريال "
            f"بتاريخ {FOURTH_PAYMENT_DATE}.",
        ),
        ("request", "ترفع الدعوى أمام المحكمة التجارية في بريدة."),
        ("request", "تُكتب التواريخ في المستندات بالميلادي."),
    ),
    attachment_title="عقد تشطيب الفيلا.pdf",
    attachment_summary=(
        f"عقد تشطيب فيلا في مدينة بريدة بين لمى الحارثي ومؤسسة مدى الأرجوان للتشطيبات "
        f"بقيمة {_n(CONTRACT_VALUE)} ريال ومدة 120 يوماً."
    ),
    attachment_text=(
        "عقد تشطيب\nالطرف الأول: لمى الحارثي\nالطرف الثاني: مؤسسة مدى الأرجوان للتشطيبات\n"
        "الموضوع: تشطيب فيلا الطرف الأول في مدينة بريدة (كهرباء، سباكة، جبس، دهانات).\n"
        f"القيمة الإجمالية: {_n(CONTRACT_VALUE)} ريال تسدد على دفعات بحسب مراحل العمل.\n"
        "المدة: 120 يوماً من تاريخ استلام الموقع.\nتاريخ العقد: 2026/01/18."
    ),
    draft_title="صحيفة دعوى فسخ عقد تشطيب ورد المبالغ المدفوعة",
    draft_summary=(
        f"صحيفة دعوى باسم لمى الحارثي ضد مؤسسة مدى الأرجوان للتشطيبات تطلب فسخ عقد التشطيب "
        f"ورد {_n(OLD_TOTAL)} ريال، وفيها جدول المطالبة."
    ),
    sections=(
        (
            "## بيانات الدعوى",
            "إلى فضيلة رئيس المحكمة التجارية في بريدة\n\n"
            "**الموضوع:** طلب فسخ عقد تشطيب فيلا وإلزام المدعى عليها برد المبالغ التي تسلمتها "
            "من المدعية.\n\n"
            f"**قيمة المطالبة:** {_n(OLD_TOTAL)} ريال ({OLD_TOTAL_WORDS}).",
        ),
        (
            "## بيانات الأطراف",
            "**المدعية:** لمى الحارثي، سعودية الجنسية، ويمثلها وكيلها المحامي [اسم الوكيل].\n\n"
            "**المدعى عليها:** مؤسسة مدى الأرجوان للتشطيبات، سجل تجاري رقم [رقم السجل]، "
            "وعنوانها مدينة بريدة.",
        ),
        (
            "## الوقائع",
            f"1. بتاريخ 2026/01/18 أبرمت المدعية مع المدعى عليها عقد تشطيب لفيلا المدعية في مدينة "
            f"بريدة بقيمة إجمالية قدرها {_n(CONTRACT_VALUE)} ريال (مرفق رقم 1)، على أن تُنجز الأعمال "
            "خلال 120 يوماً من تاريخ استلام الموقع.\n"
            "2. استلمت المدعى عليها الموقع بتاريخ 2026/01/25، ونص العقد على سداد القيمة على دفعات "
            "بحسب مراحل العمل.\n"
            "3. التزمت المدعية بالسداد، وسددت للمدعى عليها الدفعات المبينة في جدول المطالبة أدناه.\n"
            "4. لم تنجز المدعى عليها الأعمال في المدة المتفق عليها، ثم توقفت عن العمل كلياً بتاريخ "
            "2026/07/02 وسحبت عمالتها من الموقع قبل إتمام أعمال الكهرباء والجبس والدهانات.\n"
            "5. وجهت المدعية إلى المدعى عليها إنذاراً بتاريخ 2026/07/20 تطلب فيه استئناف العمل خلال "
            "عشرة أيام، فلم تستجب.",
        ),
        (
            "## جدول المطالبة",
            "| م | التاريخ | البيان | طريقة السداد | المبلغ (ريال) |\n"
            "|---|---|---|---|---|\n"
            f"| 1 | 2026/02/09 | الدفعة الأولى | تحويل بنكي | {_n(FIRST_PAYMENT)} |\n"
            f"| 2 | 2026/03/23 | الدفعة الثانية | تحويل بنكي | {_n(SECOND_PAYMENT)} |\n"
            f"| 3 | 2026/05/04 | الدفعة الثالثة | شيك | {_n(THIRD_PAYMENT)} |\n"
            f"| | | | **الإجمالي** | **{_n(OLD_TOTAL)}** |",
        ),
        (
            "## الأسانيد",
            "1. العقد شريعة المتعاقدين، والتزام المقاول بإنجاز العمل في مدته التزام بتحقيق نتيجة، "
            "فإذا أخل به جاز لصاحب العمل طلب الفسخ بعد الإعذار.\n"
            "2. إذا فُسخ العقد أُعيد المتعاقدان إلى الحالة التي كانا عليها قبل التعاقد، فتلتزم "
            "المدعى عليها برد ما تسلمته.\n"
            "3. ما سددته المدعية ثابت في جدول المطالبة أعلاه.",
        ),
        (
            "## الطلبات",
            "تلتمس المدعية الحكم بما يلي:\n\n"
            "1. فسخ عقد التشطيب المبرم بتاريخ 2026/01/18.\n"
            f"2. إلزام المدعى عليها برد مبلغ {_n(OLD_TOTAL)} ريال إلى المدعية.\n"
            "3. إلزام المدعى عليها بمصاريف الدعوى وأتعاب المحاماة.",
        ),
        ("## قائمة المرفقات", "1. صورة من عقد التشطيب المبرم بتاريخ 2026/01/18."),
    ),
)

# --------------------------------------------------------------------------- #
# The long matter: one document for B_long and C_long. It leaves a payment out of
# its table, and it carries the user's dictated argument with its reasoning.
# --------------------------------------------------------------------------- #

LONG_PAYMENTS = (
    ("2026/01/11", "الدفعة المقدمة عند توقيع العقد", "تحويل بنكي", 192_690),
    ("2026/02/08", "دفعة مرحلة الهيكل والتمديدات الأولية", "تحويل بنكي", 143_275),
    ("2026/03/02", "دفعة مرحلة الجدران واللياسة", "شيك", 118_940),
    ("2026/03/29", "دفعة مرحلة العزل والأرضيات", "تحويل بنكي", 96_385),
    ("2026/04/21", "دفعة مرحلة النجارة والأبواب", "شيك", 87_160),
    ("2026/05/14", "دفعة مرحلة الدهانات الأولى", "تحويل بنكي", 74_530),
)
LONG_OLD_PAID = sum(row[3] for row in LONG_PAYMENTS)  # 712,980
LONG_PAYMENT = 58_347  # the one the previous draft left out
LONG_PAYMENT_DATE = "2026/06/03"
LONG_NEW_PAID = LONG_OLD_PAID + LONG_PAYMENT  # 771,327
LONG_REMEDIATION = 64_215
LONG_ENGINEER_FEES = 12_650
LONG_OLD_GRAND = LONG_OLD_PAID + LONG_REMEDIATION + LONG_ENGINEER_FEES  # 789,845
LONG_NEW_GRAND = LONG_NEW_PAID + LONG_REMEDIATION + LONG_ENGINEER_FEES  # 848,192
LONG_OLD_GRAND_WORDS = "سبعمائة وتسعة وثمانون ألفاً وثمانمائة وخمسة وأربعون ريالاً"
LONG_NEW_GRAND_WORDS = "ثمانمائة وثمانية وأربعون ألفاً ومائة واثنان وتسعون ريالاً"
LONG_CONTRACT_VALUE = 1_284_600

LONG_ARGUMENT_HEADING = "**خامساً: توقيع محضر الاستلام الجزئي لا يسقط حق المدعية في الفسخ.**"
LONG_ARGUMENT_REASONING = (
    "ذلك أن المحضر اقتصر على الأعمال الظاهرة وقت المعاينة، وأن العيوب التي ظهرت بعده كانت مخفية "
    "لا يكشفها الفحص المعتاد، ومن لم يعلم بالعيب لا يُتصور أن يرضى به."
)
LONG_ARGUMENT_PARAGRAPH = (
    f"{LONG_ARGUMENT_HEADING} قد تتمسك المدعى عليها بأن المدعية وقّعت هذا المحضر بتاريخ 2026/04/12 "
    "دون ملاحظات، وأن توقيعها يعد قبولاً منها للأعمال. وهذا التوقيع لا يسقط حق المدعية في الفسخ ولا "
    f"في الرجوع بالعيوب، {LONG_ARGUMENT_REASONING}"
)

LONG_REFERENCES = (
    (1, "الدليل الإرشادي لعقود المقاولات، البند 3", "يلتزم كل متعاقد بتنفيذ ما اشتمل عليه العقد بحسن نية، ولا يجوز لأحدهما التحلل منه بإرادته المنفردة."),
    (2, "الدليل الإرشادي لعقود المقاولات، البند 9", "يلتزم المقاول بإنجاز العمل وفق المواصفات المتفق عليها، والتزامه التزام بتحقيق نتيجة لا ببذل عناية."),
    (3, "الدليل الإرشادي لعقود المقاولات، البند 12", "إذا تأخر المقاول عن الموعد المتفق عليه تأخراً لا يرجى معه الإنجاز جاز لصاحب العمل طلب الفسخ بعد الإعذار."),
    (4, "الدليل الإرشادي لعقود المقاولات، البند 17", "يضمن المقاول ما يطرأ على العمل من عيب ناشئ عن مخالفته المواصفات أو أصول الصنعة."),
    (5, "الدليل الإرشادي لعقود المقاولات، البند 21", "يترتب على الفسخ إعادة المتعاقدين إلى الحالة التي كانا عليها قبل التعاقد، فيرد كل منهما ما تسلمه."),
    (6, "الدليل الإرشادي لعقود المقاولات، البند 24", "يستحق المتضرر تعويضاً عما لحقه من ضرر مباشر بسبب الإخلال، ومنه نفقات إصلاح العمل المعيب."),
)

LONG_RESEARCH_BODY = (
    "# التزامات المقاول وآثار فسخ عقد المقاولة\n\n"
    "## الخلاصة\n"
    "العقد ملزم لطرفيه [1]، والتزام المقاول التزام بتحقيق نتيجة [2]. والتأخر الذي لا يرجى معه الإنجاز "
    "يجيز طلب الفسخ بعد الإعذار [3]، ويضمن المقاول العيب الناشئ عن مخالفة المواصفات [4]. وبالفسخ يعود "
    "المتعاقدان إلى ما كانا عليه فيرد كل منهما ما تسلمه [5]، ويستحق المتضرر التعويض عن الضرر المباشر، "
    "ومنه نفقات إصلاح العمل المعيب [6].\n\n"
    "## ما ينبغي إثباته\n"
    "تاريخ التسليم المتفق عليه، وواقعة الإعذار، والمبالغ المسددة، وتكلفة إصلاح العيوب بتقرير فني مستقل."
)

_LONG_PAYMENT_ROWS = "\n".join(
    f"| {number} | {date} | {label} | {method} | {_n(amount)} |"
    for number, (date, label, method, amount) in enumerate(LONG_PAYMENTS, start=1)
)

LONG_MATTER = Matter(
    parties=(
        ("وجدان العسيري", "موكّلة المحامي، المدعية"),
        ("شركة مسار الجادة للمقاولات", "المدعى عليها"),
    ),
    entries=(
        ("fact", "المستخدم محامٍ وموكلته وجدان العسيري، والخصم شركة مسار الجادة للمقاولات."),
        ("request", "تريد وجدان العسيري فسخ عقد المقاولة واسترداد ما دفعته والتعويض عن معالجة العيوب."),
        ("request", "ترفع الدعوى أمام المحكمة التجارية في أبها."),
        ("request", "تُكتب التواريخ في المستندات بالميلادي."),
        ("fact", "المبنى مخصص لحضانة أطفال في مدينة أبها، ورقم رخصة البناء 88213."),
        ("fact", f"عقد المقاولة مؤرخ 2025/12/21 وقيمته {_n(LONG_CONTRACT_VALUE)} ريال."),
        ("fact", "موعد التسليم النهائي المتفق عليه 2026/05/31."),
        ("fact", "المهندس المقيم من طرف الشركة هو تركي البلوي."),
        (
            "fact",
            f"سلّمت وجدان العسيري المهندس المقيم تركي البلوي دفعة نقدية مقدارها {_n(LONG_PAYMENT)} ريال "
            f"بتاريخ {LONG_PAYMENT_DATE}.",
        ),
        ("fact", "وقّعت وجدان العسيري محضر الاستلام الجزئي بتاريخ 2026/04/12."),
        ("fact", "توقفت الشركة عن العمل كلياً بتاريخ 2026/06/14."),
        ("fact", "ظهر تسرب المياه في الدور الأرضي لأول مرة بتاريخ 2026/06/19."),
        ("fact", "وُجّه الإنذار إلى الشركة بتاريخ 2026/06/25 بمهلة خمسة عشر يوماً، ولم ترد عليه."),
        (
            "fact",
            f"قدّر مكتب سنا التهامي للاستشارات الهندسية تكلفة المعالجة بمبلغ {_n(LONG_REMEDIATION)} ريال "
            f"في تقريره المؤرخ 2026/07/08، وبلغت أتعابه {_n(LONG_ENGINEER_FEES)} ريال.",
        ),
        (
            "legal_position",
            "يرى المستخدم أن توقيع وجدان العسيري محضر الاستلام الجزئي بتاريخ 2026/04/12 لا يسقط حقها في "
            "الفسخ، لأن المحضر اقتصر على الأعمال الظاهرة وقت المعاينة، ولأن العيوب التي ظهرت بعده كانت "
            "مخفية لا يكشفها الفحص المعتاد، ومن لم يعلم بالعيب لا يُتصور أن يرضى به.",
        ),
        (
            "legal_position",
            "يرى المستخدم أن توقف الشركة عن العمل بعد الإنذار إخلال جوهري يبرر الفسخ دون حاجة إلى طلب "
            "التنفيذ العيني أولاً.",
        ),
        ("request", "لا تطلب وجدان العسيري أي فوائد تأخير في هذه الدعوى."),
        ("request", "تُطلب في الصحيفة إلزام الشركة بتسليم المخططات كما نُفذت."),
        ("request", "تُذكر قيمة المطالبة في صدر الصحيفة بالأرقام والحروف."),
        ("request", "لم يُحسم: هل يُضم المكتب الاستشاري المشرف مدعى عليه؟"),
    ),
    attachment_title="عقد مقاولة مبنى الحضانة.pdf",
    attachment_summary=(
        f"عقد مقاولة بين وجدان العسيري وشركة مسار الجادة للمقاولات لإنشاء وتشطيب مبنى حضانة أطفال في "
        f"أبها بقيمة {_n(LONG_CONTRACT_VALUE)} ريال."
    ),
    attachment_text=(
        "عقد مقاولة\nالطرف الأول: وجدان العسيري\nالطرف الثاني: شركة مسار الجادة للمقاولات\n"
        "الموضوع: إنشاء وتشطيب مبنى مخصص لحضانة أطفال في مدينة أبها.\n"
        f"القيمة الإجمالية: {_n(LONG_CONTRACT_VALUE)} ريال تسدد على مراحل بحسب تقدم الأعمال.\n"
        "موعد التسليم النهائي: 2026/05/31.\nتاريخ العقد: 2025/12/21."
    ),
    draft_title="صحيفة دعوى فسخ عقد مقاولة ورد المبالغ المدفوعة والتعويض",
    draft_summary=(
        f"صحيفة دعوى باسم وجدان العسيري ضد شركة مسار الجادة للمقاولات تطلب فسخ العقد ورد "
        f"{_n(LONG_OLD_PAID)} ريال والتعويض، بإجمالي {_n(LONG_OLD_GRAND)} ريال."
    ),
    sections=(
        (
            "## بيانات الدعوى",
            "إلى فضيلة رئيس المحكمة التجارية في أبها\n\n"
            "**الموضوع:** طلب فسخ عقد مقاولة لإنشاء وتشطيب مبنى حضانة أطفال، وإلزام المدعى عليها برد ما "
            "تسلمته من المدعية، وتعويضها عن تكلفة معالجة العيوب وأتعاب المكتب الهندسي.\n\n"
            f"**قيمة المطالبة:** {_n(LONG_OLD_GRAND)} ريال ({LONG_OLD_GRAND_WORDS}).\n\n"
            "**رقم رخصة البناء:** 88213.",
        ),
        (
            "## بيانات الأطراف",
            "**المدعية:** وجدان العسيري، سعودية الجنسية، مالكة المبنى محل العقد، ويمثلها وكيلها المحامي "
            "[اسم الوكيل] بموجب الوكالة رقم [رقم الوكالة].\n\n"
            "**المدعى عليها:** شركة مسار الجادة للمقاولات، سجل تجاري رقم [رقم السجل]، وعنوانها الوطني "
            "[العنوان]، ويمثلها مديرها [اسم المدير].",
        ),
        (
            "## موجز الدعوى",
            f"تعاقدت المدعية مع المدعى عليها على إنشاء وتشطيب مبنى مخصص لحضانة أطفال في مدينة أبها بقيمة "
            f"إجمالية قدرها {_n(LONG_CONTRACT_VALUE)} ريال، وسددت لها على مراحل مبلغ {_n(LONG_OLD_PAID)} "
            "ريال. ولم تسلّم المدعى عليها المبنى في موعده، ثم توقفت عن العمل قبل إنجازه، وبدت في الأعمال "
            "المنفذة عيوب جسيمة أثبتها تقرير هندسي مستقل. وقد أنذرتها المدعية فلم تستجب، فأقامت هذه الدعوى "
            f"طالبة فسخ العقد ورد ما دفعته والتعويض عن الأضرار التي لحقتها، بما مجموعه {_n(LONG_OLD_GRAND)} "
            "ريال.",
        ),
        (
            "## الوقائع",
            "1. بتاريخ 2025/12/21 أبرمت المدعية مع المدعى عليها عقد مقاولة (مرفق رقم 1) التزمت بموجبه "
            "المدعى عليها بإنشاء مبنى مخصص لحضانة أطفال على قطعة الأرض المملوكة للمدعية في مدينة أبها، "
            f"وتشطيبه تشطيباً كاملاً وتسليمه جاهزاً للتشغيل، بقيمة إجمالية قدرها {_n(LONG_CONTRACT_VALUE)} "
            "ريال تُسدد على مراحل بحسب تقدم الأعمال.\n"
            "2. نص العقد على أن تبدأ الأعمال من تاريخ تسليم الموقع، وأن يكون التسليم النهائي للمبنى في موعد "
            "أقصاه 2026/05/31، وأن تتولى المدعى عليها توفير العمالة والمواد والمعدات، وأن تعيّن مهندساً "
            "مقيماً في الموقع يكون مسؤولاً عن متابعة التنفيذ وفق المخططات المعتمدة.\n"
            "3. تسلمت المدعى عليها الموقع بتاريخ 2026/01/04، وعيّنت المهندس تركي البلوي مهندساً مقيماً، "
            "وباشرت الأعمال الأولية ثم أعمال الهيكل والتمديدات.\n"
            "4. التزمت المدعية بالسداد في المواعيد المتفق عليها، فسددت للمدعى عليها الدفعات المبينة في جدول "
            f"الدفعات أدناه دون أن تتأخر عن أي منها، وبلغ مجموع ما سددته {_n(LONG_OLD_PAID)} ريال.\n"
            "5. بتاريخ 2026/04/12 وقّعت المدعية محضر الاستلام الجزئي الخاص بأعمال الجدران واللياسة في الدور "
            "الأرضي، بعد جولة في الموقع برفقة المهندس المقيم.\n"
            "6. تباطأت المدعى عليها في التنفيذ اعتباراً من شهر مايو 2026، وقلّصت عدد العمال في الموقع تقليصاً "
            "ملحوظاً، ولم تسلّم المبنى في الموعد المتفق عليه رغم مطالبات المدعية المتكررة عبر الرسائل وفي "
            "اجتماعات الموقع الدورية.\n"
            "7. بتاريخ 2026/06/14 توقفت المدعى عليها عن العمل كلياً وسحبت عمالتها ومعداتها من الموقع، تاركة "
            "أعمال السباكة النهائية والكهرباء والدهانات والواجهات دون إنجاز.\n"
            "8. بتاريخ 2026/06/19 ظهر تسرب للمياه في الدور الأرضي لأول مرة، ثم تبين بعد فتح أجزاء من الجدران "
            "أن شبكة التمديدات نُفذت على خلاف المخططات المعتمدة وبمواد أقل من المواصفات المتفق عليها.\n"
            "9. بتاريخ 2026/06/25 وجّهت المدعية إلى المدعى عليها إنذاراً كتابياً تطالبها فيه باستئناف العمل "
            "ومعالجة العيوب خلال خمسة عشر يوماً، فلم تستجب ولم ترد على الإنذار حتى تاريخ هذه الصحيفة.\n"
            "10. كلفت المدعية مكتب سنا التهامي للاستشارات الهندسية بمعاينة المبنى، فأصدر تقريره بتاريخ "
            f"2026/07/08، وأثبت فيه العيوب وقدّر تكلفة معالجتها بمبلغ {_n(LONG_REMEDIATION)} ريال، وبلغت "
            f"أتعاب المكتب {_n(LONG_ENGINEER_FEES)} ريال سددتها المدعية.\n"
            "11. لم تسلّم المدعى عليها المخططات كما نُفذت رغم طلب المدعية ذلك، مما يعيق تقدير الأعمال "
            "المتبقية ويحول دون التعاقد مع مقاول آخر لإتمامها على أساس سليم.\n"
            "12. ترتب على ما تقدم تعذر تشغيل الحضانة في الموعد المخطط له، وبقي المبنى غير صالح للانتفاع، ولم "
            "يعد في وسع المدعية الاطمئنان إلى قدرة المدعى عليها على إتمام الأعمال.\n"
            "13. راجعت المدعية الأعمال المنفذة مع المكتب الهندسي، فتبين أن ما أُنجز فعلياً يقل كثيراً عما "
            "تقتضيه الدفعات التي تسلمتها المدعى عليها، وأن جزءاً مما أُنجز لا يصلح للبناء عليه دون إعادته.",
        ),
        (
            "## العيوب التي أثبتها التقرير الهندسي",
            "1. تنفيذ شبكة تمديدات المياه في الدور الأرضي بأنابيب أقل قطراً مما نصت عليه المخططات المعتمدة، "
            "مما تسبب في ضعف الضغط وتسرب المياه عند الوصلات.\n"
            "2. عدم تنفيذ طبقة العزل المائي في دورات المياه على النحو المتفق عليه، وتسرب الرطوبة إلى الجدران "
            "المجاورة.\n"
            "3. تشققات في لياسة الجدران الخارجية للواجهة الشمالية ناتجة عن عدم التزام نسب الخلط المعتمدة.\n"
            "4. ميلان في بلاط الممرات الداخلية يخالف منسوب التصريف المعتمد.\n"
            "5. تركيب لوحات كهربائية فرعية بسعة أقل من الأحمال التصميمية لفصول الحضانة.\n"
            "6. أبواب داخلية من مواد أقل جودة من المادة المنصوص عليها في جدول التشطيبات.\n\n"
            "وخلص التقرير إلى أن هذه العيوب راجعة إلى التنفيذ لا إلى الاستعمال، وأن معالجتها تستلزم إعادة جزء "
            f"من الأعمال وفق المخططات المعتمدة، وقدّر تكلفة ذلك بمبلغ {_n(LONG_REMEDIATION)} ريال.",
        ),
        (
            "## حالة الأعمال عند التوقف",
            "عند توقف المدعى عليها عن العمل كان الدور الأرضي مكتمل الهيكل والجدران مع نقص في التمديدات "
            "النهائية، وكان الدور الأول دون دهانات ولا أرضيات في أغلب غرفه، ولم تُركّب الأبواب الخارجية ولا "
            "أجهزة التكييف، ولم يُنفذ السور الخارجي ولا ساحة اللعب المخصصة للأطفال، ولم تُستكمل متطلبات السلامة "
            "والدفاع المدني اللازمة لترخيص الحضانة. وقد وثّقت المدعية هذه الحالة بالصور في تاريخ التوقف وبعده.",
        ),
        (
            "## محاولات التسوية",
            "قبل إقامة هذه الدعوى سعت المدعية إلى حل النزاع ودياً، فعقدت مع ممثلي المدعى عليها اجتماعين في "
            "الموقع بتاريخي 2026/05/24 و2026/06/08، وعدت فيهما المدعى عليها باستئناف الأعمال وزيادة العمالة، "
            "ثم لم تفِ بما وعدت به. وبعد توقفها عن العمل حاولت المدعية التواصل مع مديرها عبر الهاتف والبريد "
            "الإلكتروني فلم تتلق رداً، ولم يبق أمامها سوى اللجوء إلى القضاء لحفظ حقوقها.",
        ),
        (
            "## جدول الدفعات المسددة",
            "| م | التاريخ | البيان | طريقة السداد | المبلغ (ريال) |\n"
            "|---|---|---|---|---|\n"
            f"{_LONG_PAYMENT_ROWS}\n"
            f"| | | | **الإجمالي** | **{_n(LONG_OLD_PAID)}** |",
        ),
        (
            "## جدول المطالبة",
            "| البند | المبلغ (ريال) |\n"
            "|---|---|\n"
            f"| رد المبالغ المدفوعة | {_n(LONG_OLD_PAID)} |\n"
            f"| تكلفة معالجة العيوب | {_n(LONG_REMEDIATION)} |\n"
            f"| أتعاب المكتب الهندسي | {_n(LONG_ENGINEER_FEES)} |\n"
            f"| **إجمالي المطالبة** | **{_n(LONG_OLD_GRAND)}** |",
        ),
        (
            "## أساس احتساب المطالبة",
            f"تتكون المطالبة من ثلاثة بنود. البند الأول رد المبالغ المدفوعة وقدره {_n(LONG_OLD_PAID)} ريال، "
            "وهو مجموع الدفعات المبينة في جدول الدفعات. والبند الثاني تكلفة معالجة العيوب وقدرها "
            f"{_n(LONG_REMEDIATION)} ريال كما قدرها التقرير الهندسي، ولا تشمل هذه التكلفة الأعمال غير المنجزة "
            "التي تركتها المدعى عليها، وإنما تقتصر على إصلاح ما نُفذ معيباً. والبند الثالث أتعاب المكتب "
            f"الهندسي وقدرها {_n(LONG_ENGINEER_FEES)} ريال، وهي نفقة اضطرت إليها المدعية لإثبات العيوب. وبذلك "
            f"يبلغ إجمالي المطالبة {_n(LONG_OLD_GRAND)} ريال، ولم تُدخل المدعية في مطالبتها أي فوائد تأخير.",
        ),
        (
            "## الاختصاص",
            "تختص المحكمة التجارية بنظر هذه الدعوى لأن المدعى عليها شركة تمارس أعمال المقاولات، ولأن النزاع "
            "نشأ عن عقد مقاولة أبرمته في نطاق نشاطها التجاري. وتختص محكمة أبها مكانياً لأن المبنى محل العقد "
            "يقع في نطاقها ولأن تنفيذ العقد جرى فيها، ولم يتضمن العقد شرطاً يحيل النزاع إلى التحكيم أو إلى "
            "جهة أخرى.",
        ),
        (
            "## الأسانيد",
            "**أولاً: القوة الملزمة للعقد.** العقد ملزم لطرفيه، ويجب تنفيذه بحسن نية وفق ما اشتمل عليه، ولا "
            "يجوز لأحد المتعاقدين التحلل منه بإرادته المنفردة (1). وقد التزمت المدعى عليها بعقد واضح في "
            "موضوعه ومدته وثمنه، فلا تملك أن تتوقف عن تنفيذه أو أن تعدّل مضمونه دون موافقة المدعية، وتوقفها "
            "عن العمل في الموقع قبل إتمامه خروج صريح عما التزمت به.\n\n"
            "**ثانياً: التزام المقاول التزام بتحقيق نتيجة.** لا يبرأ المقاول ببذل العناية، وإنما بإنجاز العمل "
            "وفق المواصفات المتفق عليها (2). وقد ثبت أن المبنى لم يُنجز، وأن ما نُفذ منه جاء مخالفاً للمخططات "
            "المعتمدة، فلا يقبل من المدعى عليها الاحتجاج بما بذلته من جهد أو بما صرفته من تكلفة، ما دامت "
            "النتيجة المتفق عليها لم تتحقق. ويؤكد ذلك أن العقد حدد المواصفات والمخططات تحديداً لا يترك للمقاول "
            "مجالاً للاجتهاد، وأن مخالفتها في التمديدات والعزل واللوحات الكهربائية لا يمكن ردها إلى ظروف الموقع "
            "أو إلى تعليمات صدرت من المدعية، إذ لم تصدر عنها أي تعليمات بتغيير المواصفات.\n\n"
            "**ثالثاً: التأخر عن الموعد وأثره.** إذا تأخر المقاول تأخراً لا يرجى معه الإنجاز جاز لصاحب العمل "
            "طلب الفسخ بعد الإعذار (3). وقد انقضى الموعد المتفق عليه دون تسليم، ثم توقفت المدعى عليها عن "
            "العمل كلياً، ووجّهت إليها المدعية إنذاراً كتابياً انقضت مهلته دون استجابة، فتحقق بذلك شرط الفسخ "
            "على النحو المتفق عليه. ولا يشفع للمدعى عليها ما وعدت به في الاجتماعين المعقودين في الموقع، فقد "
            "انقضت المواعيد التي حددتها لنفسها دون تنفيذ، ثم جاء انسحابها الكامل من الموقع قاطعاً في أنها لن "
            "تتم الأعمال، فلا فائدة من منحها مهلة إضافية.\n\n"
            "**رابعاً: ضمان العيوب.** يضمن المقاول ما يطرأ على العمل من عيب ناشئ عن مخالفته المواصفات أو أصول "
            "الصنعة (4). وقد أثبت التقرير الهندسي أن العيوب راجعة إلى تنفيذ المدعى عليها، فتتحمل تكلفة "
            "معالجتها كاملة، ولا يغير من ذلك أن بعض الأعمال سبق استلامها جزئياً. ويشمل هذا الضمان كل ما أثبته "
            "التقرير من عيوب في شبكة التمديدات والعزل واللياسة والبلاط واللوحات الكهربائية والأبواب، لأنها "
            "جميعاً من صميم الأعمال التي التزمت بها المدعى عليها وقبضت مقابلها.\n\n"
            f"{LONG_ARGUMENT_PARAGRAPH}\n\n"
            "**سادساً: التوقف بعد الإنذار إخلال جوهري.** توقف المدعى عليها عن العمل بعد الإنذار إخلال جوهري "
            "يبرر الفسخ دون حاجة إلى طلب التنفيذ العيني أولاً، إذ لم يعد التنفيذ مجدياً بعد أن تركت الموقع "
            "وامتنعت عن الرد.\n\n"
            "**سابعاً: آثار الفسخ.** يترتب على الفسخ إعادة المتعاقدين إلى الحالة التي كانا عليها قبل التعاقد، "
            f"فيرد كل منهما ما تسلمه (5). وعليه تلتزم المدعى عليها برد ما تسلمته البالغ {_n(LONG_OLD_PAID)} "
            "ريال، كما هو مبين في جدول الدفعات، ولا يحق لها حبس شيء منه مقابل أعمال معيبة لا تنتفع بها "
            "المدعية.\n\n"
            "**ثامناً: التعويض.** يستحق المتضرر تعويضاً عما لحقه من ضرر مباشر بسبب الإخلال، ومنه نفقات إصلاح "
            f"العمل المعيب (6). وقد لحق المدعية ضرر مباشر يتمثل في تكلفة معالجة العيوب البالغة "
            f"{_n(LONG_REMEDIATION)} ريال، وأتعاب المكتب الهندسي البالغة {_n(LONG_ENGINEER_FEES)} ريال، "
            "وهما من النفقات التي ما كانت لتتحملها لولا إخلال المدعى عليها.",
        ),
        (
            "## الضرر اللاحق بالمدعية",
            "لحق بالمدعية من جراء إخلال المدعى عليها ضرر مباشر تمثل فيما تقدم بيانه من نفقات، وتعطل مشروع "
            "الحضانة الذي كانت تعتزم تشغيله مع بداية العام الدراسي، واضطرارها إلى تأجيل قبول الأطفال المسجلين "
            "وإعادة ما استلمته من رسوم التسجيل المبدئية، وتحملها أعباء متابعة الموقع والمراسلات. وتحتفظ المدعية "
            "بحقها في المطالبة بما قد يستجد من أضرار بعد اكتمال تقدير الأعمال المتبقية.",
        ),
        (
            "## الطلبات",
            "تلتمس المدعية من فضيلتكم الحكم بما يلي:\n\n"
            "1. فسخ عقد المقاولة المبرم بتاريخ 2025/12/21.\n"
            f"2. إلزام المدعى عليها برد مبلغ {_n(LONG_OLD_PAID)} ريال إلى المدعية.\n"
            f"3. إلزام المدعى عليها بمبلغ {_n(LONG_REMEDIATION)} ريال تكلفة معالجة العيوب.\n"
            f"4. إلزام المدعى عليها بمبلغ {_n(LONG_ENGINEER_FEES)} ريال أتعاب المكتب الهندسي.\n"
            "5. إلزام المدعى عليها بتسليم المخططات كما نُفذت.\n"
            "6. إلزام المدعى عليها بمصاريف الدعوى وأتعاب المحاماة.",
        ),
        ("## قائمة المرفقات", "1. صورة من عقد المقاولة المبرم بتاريخ 2025/12/21."),
    ),
    research=Research(
        title="التزامات المقاول وآثار فسخ عقد المقاولة",
        summary="بحث في القوة الملزمة لعقد المقاولة والتزام المقاول وضمان العيوب وآثار الفسخ والتعويض.",
        body=LONG_RESEARCH_BODY,
        references=LONG_REFERENCES,
    ),
)

_LONG_INTRO = "صحيفة الدعوى المرفوعة باسم وجدان العسيري ضد شركة مسار الجادة للمقاولات"
_CLAIM_INTRO = "صحيفة الدعوى المرفوعة باسم لمى الحارثي ضد مؤسسة مدى الأرجوان للتشطيبات"


# --------------------------------------------------------------------------- #
# Group D: the same document, with money facts that must stay out of the claim. The
# 58,347 entry goes, because that one rightly changes the total.
# --------------------------------------------------------------------------- #

D_NOT_CLAIMED = 23_486  # the user rules it out, in a separate entry
D_THIRD_PARTY = 17_365  # paid to someone else, no link to this defendant
D_SEPARATE = 31_258  # paid to this defendant, under a separate finished contract
D_SUMS = (D_NOT_CLAIMED, D_THIRD_PARTY, D_SEPARATE)

_E = LONG_MATTER.entries
D_MATTER = replace(
    LONG_MATTER,
    entries=(
        *_E[:4],
        (
            "fact",
            f"دفعت وجدان العسيري لمكتب رواق العقيق العقاري سعياً قدره {_n(D_THIRD_PARTY)} ريال عند "
            "شراء أرض الحضانة في 2025/09/14.",
        ),
        *_E[4:8],
        (
            "fact",
            f"دفعت وجدان العسيري لشركة مسار الجادة {_n(D_NOT_CLAIMED)} ريال بتاريخ 2026/02/22 مقابل "
            "مظلات مواقف إضافية خارج العقد، وتسلمتها كاملة.",
        ),
        *_E[9:13],
        (
            "fact",
            "نفذت شركة مسار الجادة لوجدان العسيري عقد صيانة منفصلاً لمنزلها في 2025/10/05 بمبلغ "
            f"{_n(D_SEPARATE)} ريال، ودفعته لها وانتهى العقد دون خلاف.",
        ),
        *_E[13:16],
        ("request", f"لا تطالب وجدان العسيري في هذه الدعوى بمبلغ {_n(D_NOT_CLAIMED)} ريال قيمة المظلات."),
        *_E[16:],
    ),
)


@dataclass(frozen=True)
class Figures:
    """What a rewrite must do with the money, and a right answer for the self-test."""

    payment: int
    payment_date: str
    earlier: tuple[int, ...]
    corrected: tuple[int, ...]
    old: tuple[int, ...]
    old_words: re.Pattern[str]
    new_row: str
    row_anchor: str
    swaps: tuple[tuple[str, str], ...]


CLAIM_FIGURES = Figures(
    payment=FOURTH_PAYMENT,
    payment_date=FOURTH_PAYMENT_DATE,
    earlier=(FIRST_PAYMENT, SECOND_PAYMENT, THIRD_PAYMENT),
    corrected=(CORRECTED_TOTAL,),
    old=(OLD_TOTAL,),
    old_words=re.compile(r"ثمان(?:ون|ين)\s+الف"),
    new_row=f"| 4 | {FOURTH_PAYMENT_DATE} | الدفعة الرابعة | نقداً | {_n(FOURTH_PAYMENT)} |\n",
    row_anchor="| | | | **الإجمالي**",
    swaps=(
        (OLD_TOTAL_WORDS, "تسعة وتسعون ألفاً ومئتان وسبعة وخمسون ريالاً"),
        (_n(OLD_TOTAL), _n(CORRECTED_TOTAL)),
    ),
)
LONG_FIGURES = Figures(
    payment=LONG_PAYMENT,
    payment_date=LONG_PAYMENT_DATE,
    earlier=tuple(row[3] for row in LONG_PAYMENTS),
    corrected=(LONG_NEW_PAID, LONG_NEW_GRAND),
    old=(LONG_OLD_PAID, LONG_OLD_GRAND),
    old_words=re.compile(
        r"سبع(?:مائة|مئة)\s+(?:و(?:تسعة|تسع)\s+وثمان(?:ون|ين)|واثن[اي]\s+عشر)\s+الف"
    ),
    new_row=f"| 7 | {LONG_PAYMENT_DATE} | دفعة نقدية | نقداً | {_n(LONG_PAYMENT)} |\n",
    row_anchor="| | | | **الإجمالي**",
    swaps=(
        (LONG_OLD_GRAND_WORDS, LONG_NEW_GRAND_WORDS),
        (_n(LONG_OLD_GRAND), _n(LONG_NEW_GRAND)),
        (_n(LONG_OLD_PAID), _n(LONG_NEW_PAID)),
    ),
)


@dataclass(frozen=True)
class Scenario:
    id: str
    title: str
    matter: Matter
    figures: Figures
    memory: bool  # False: no entries, so no block and no drafting note reach the writer
    subtype: str
    edit_mode: str
    user_message: str  # what the user typed: the writer never sees this, only the brief
    task_label: str
    intent_ar: str  # the writer's <user_request> and "### The request"
    plan_md: str  # the writer's <plan>
    grade: Callable[[dict], dict]


# --------------------------------------------------------------------------- #
# Grading. Deterministic, on the stored document only: a figure in a chat note is
# not a figure in the document.
# --------------------------------------------------------------------------- #

_BIDI_MARKS = re.compile("[؜‎‏‪-‮⁦-⁩]")
_SPACE_IN_NUMBER = re.compile(r"(?<=\d)[   ](?=\d{3}(?!\d))")

#: A rewrite counts only at or under this share of the original length.
MAX_LENGTH_RATIO = 0.70

#: All must match in one block: a paragraph, or up to three adjacent lines. Fixed
#: before the first run. The conclusion is that signing the minute does not bar the
#: right. The heading alone says that much, so it cannot show the reasoning.
CONCLUSION_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("the minute", re.compile(r"محضر")),
    ("handover", re.compile(r"استلام|تسلم")),
    (
        "does not bar",
        re.compile(
            r"لا\s+يسقط|لا\s+يمنع|لا\s+يحول|لا\s+يعد|لا\s+يعتبر|لا\s+يفيد|لا\s+يدل|لا\s+ينهض"
            r"|لا\s+يحجب|لا\s+يكون|لا\s+يحرم|لا\s+يترتب|لم\s+يسقط|غير\s+مسقط"
            r"|(?<![ء-ي])ليس"
        ),
    ),
    ("the right", re.compile(r"الفسخ|حقها|حق\s+المدعية|الرجوع")),
)
#: The three steps of the user's reasoning. Looked for in the conclusion's block.
REASONING_LIMBS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("covers only what was visible", re.compile(r"ظاهر")),
    (
        "hidden from ordinary inspection",
        re.compile(
            r"مخفي|خفي|مستتر|الفحص\s+(?:المعتاد|العادي)|المعاينة\s+(?:المعتادة|العادية)|لا\s+يكشف"
        ),
    ),
    (
        "no consent to the unknown",
        re.compile(r"يعلم|تعلم|بعلم|دون\s+علم|عدم\s+علم|العلم\s+به|يتصور|رضا|يرضى|ترضى"),
    ),
)
#: One limb is a reason stated in passing. Two carry the argument. All three is the
#: argument as dictated, and is reported beside this line.
MIN_REASONING_LIMBS = 2

#: The graded money line for C: the corrected total is there, and the old one is gone
#: in figures and in words. 712,980 stays out of it, because round 2 showed it can be
#: a true subtotal beside the cash payment. Fixed before that round's first run.
_OLD_GRAND_IN_WORDS = re.compile(r"سبع(?:مائة|مئة)\s+و(?:تسعة|تسع)\s+وثمان(?:ون|ين)\s+الف")
_NEW_GRAND_IN_WORDS = re.compile(r"ثمان(?:مائة|مئة)\s+وثمانية\s+واربع(?:ون|ين)\s+الف")
#: What the other entries hold. Reported, so a cost of the money rule would show.
ENTRY_VALUES = (
    "88213", "2025/12/21", "1284600", "2026/05/31", "تركي البلوي", "2026/04/12", "2026/06/14",
    "2026/06/19", "2026/06/25", "64215", "12650", "2026/07/08", "التهامي", "فوائد",
)


def plain(text: str) -> str:
    """Latin digits, no separators: "٩٩٬٢٥٧", "99,257", "99.257" -> "99257"."""
    return base._plain_number(_SPACE_IN_NUMBER.sub("", _BIDI_MARKS.sub("", text or "")))


def has_figure(text: str, value: int) -> bool:
    """The exact figure. ``base.has_amount`` also accepts "99 ألف", a rounding."""
    return bool(re.search(rf"(?<!\d){value}(?!\d)", plain(text)))


def citations_in(document: str) -> list[int]:
    return sorted({int(number) for number in re.findall(r"\((\d{1,2})\)", plain(document))})


def money_lines(figures: Figures, document: str) -> dict[str, bool]:
    lines = {"payment_in_document": has_figure(document, figures.payment)}
    for value in figures.corrected:
        lines[f"corrected_{value}_in_document"] = has_figure(document, value)
    for value in figures.old:
        lines[f"old_{value}_gone"] = not has_figure(document, value)
    lines["old_total_in_words_gone"] = not figures.old_words.search(base._bare(document))
    return lines


def argument_status(document: str) -> dict:
    lines = [line for line in base._bare(document).splitlines() if line.strip()]
    windows = [
        " ".join(lines[start:start + size])
        for size in (1, 2, 3)
        for start in range(len(lines) - size + 1)
    ]
    blocks = [
        block for block in windows
        if all(term.search(block) for _name, term in CONCLUSION_TERMS)
    ]
    best = max(
        blocks,
        key=lambda block: sum(bool(limb.search(block)) for _name, limb in REASONING_LIMBS),
        default="",
    )
    kept = {name: bool(limb.search(best)) for name, limb in REASONING_LIMBS}
    return {"conclusion": bool(blocks), "limbs": kept, "limbs_kept": sum(kept.values()), "text": best}


def section_text(document: str, title: str) -> str:
    """The body under the first heading that holds ``title``."""
    kept: list[str] = []
    inside = False
    for line in document.splitlines():
        if line.startswith("#"):
            if inside:
                break
            inside = title in base._bare(line)
        elif inside:
            kept.append(line)
    return "\n".join(kept)


def claim_total_corrected(document: str) -> bool:
    """848,192 is there, and 789,845 is gone in figures and in words."""
    return (
        has_figure(document, LONG_NEW_GRAND)
        and not has_figure(document, LONG_OLD_GRAND)
        and not _OLD_GRAND_IN_WORDS.search(base._bare(document))
    )


def entry_values_missing(document: str) -> list[str]:
    text = base._bare(plain(document))
    return [value for value in ENTRY_VALUES if value not in text]


def _other_entries(document: str) -> dict:
    """Entries the note also covers. Reported, never graded."""
    bare = base._bare(document)
    return {
        "second_argument_kept": bool(re.search(r"التنفيذ\s+العيني", bare)),
        "plans_request_kept": bool(re.search(r"كما\s+نفذت|المخططات\s+المنفذة", bare)),
    }


def _long_info(run: dict) -> dict:
    """What the long document adds to either grade."""
    document = run["document"]
    known = {reference[0] for reference in LONG_REFERENCES}
    cited = citations_in(document)
    status = argument_status(document)
    return {
        "citations_kept": cited,
        "citations_unknown": [number for number in cited if number not in known],
        "argument_conclusion": status["conclusion"],
        "argument_limbs_kept": status["limbs_kept"],
        **_other_entries(document),
    }


def _ratio(run: dict) -> float | None:
    prior = run.get("prior_chars") or 0
    return round(len(run["document"]) / prior, 2) if prior else None


def _bucket(ratio: float | None) -> str:
    if ratio is None or ratio >= 1:
        return "did_not_shrink"
    return "shrank_too_little" if ratio > MAX_LENGTH_RATIO else "counted"


def _finish(
    checks: dict[str, bool], info: dict, *, counted: bool = True, reported: dict | None = None,
) -> dict:
    return {
        "passed": all(checks.values()), "counted": counted, "checks": checks, "info": info,
        "reported": reported or {},
    }


def grade_payment(run: dict) -> dict:
    """B: the payment is in, every total is corrected, every old total is gone."""
    scenario = SCENARIOS_BY_ID[run["scenario"]]
    figures, document = scenario.figures, run["document"]
    chat = run.get("chat_text") or ""
    info = {
        "payment_in_a_table_row": any(
            "|" in line and has_figure(line, figures.payment) for line in document.splitlines()
        ),
        "earlier_payments_kept": all(has_figure(document, value) for value in figures.earlier),
        "date_in_document": figures.payment_date in plain(document),
        "payment_in_chat_fields": has_figure(chat, figures.payment),
        "old_total_in_chat_fields": any(has_figure(chat, value) for value in figures.old),
        "length_ratio": _ratio(run),
        "memory_mentioned": bool(base.MEMORY_WORDS.search(document + chat)),
    }
    if scenario.matter.research is not None:
        info.update(_long_info(run))
    return _finish(money_lines(figures, document), info)


def grade_argument(run: dict) -> dict:
    """C: argument, reasoning and claim total are separate lines. The cut decides if a run counts."""
    document = run["document"]
    chat = run.get("chat_text") or ""
    status = argument_status(document)
    findings = "\n".join(run.get("key_findings") or [])
    first_finding = (run.get("key_findings") or [""])[0]
    requests = section_text(document, "الطلبات")
    header = next((line for line in document.splitlines() if "قيمة المطالبة" in base._bare(line)), "")
    cited = citations_in(document)
    known = [reference[0] for reference in LONG_REFERENCES]
    missing = entry_values_missing(document)
    ratio = _ratio(run)
    bucket = _bucket(ratio)
    return _finish(
        {
            "conclusion_survives": status["conclusion"],
            "reasoning_survives": status["limbs_kept"] >= MIN_REASONING_LIMBS,
            "claim_total_corrected": claim_total_corrected(document),
        },
        {
            "length_ratio": ratio,
            "bucket": bucket,
            "limbs": status["limbs"],
            "limbs_kept": status["limbs_kept"],
            "argument_text": status["text"][:700],
            "citations_kept": cited,
            "citations_unknown": [number for number in cited if number not in known],
            "entry_values_missing": missing,
            "payment_rows_in_tables": sum(
                "|" in line and has_figure(line, LONG_PAYMENT) for line in document.splitlines()
            ),
            "key_findings": run.get("key_findings") or [],
        },
        counted=bucket == "counted",
        reported={
            **money_lines(LONG_FIGURES, document),
            "claim_header_holds_new_total": has_figure(header, LONG_NEW_GRAND),
            "new_total_in_words": bool(_NEW_GRAND_IN_WORDS.search(base._bare(document))),
            "relief_requested_corrected": (
                has_figure(requests, LONG_NEW_PAID) and not has_figure(requests, LONG_OLD_PAID)
            ),
            "new_total_in_key_findings": has_figure(findings, LONG_NEW_GRAND),
            "new_total_in_first_finding": has_figure(first_finding, LONG_NEW_GRAND),
            "new_paid_in_key_findings": has_figure(findings, LONG_NEW_PAID),
            "payment_in_key_findings": has_figure(findings, LONG_PAYMENT),
            "all_citations_kept": set(cited) >= set(known),
            "all_entry_values_kept": not missing,
            "memory_not_mentioned": not base.MEMORY_WORDS.search(document + chat),
            **_other_entries(document),
        },
    )


_D_GROUPS = [group for size in range(1, len(D_SUMS) + 1) for group in combinations(D_SUMS, size)]
#: Any figure that is an original total plus some of the sums. A shorter rewrite has no
#: other way to reach one, so finding it is what a leak means.
_D_LEAK_TOTALS = sorted(
    total + sum(group) for total in (LONG_OLD_PAID, LONG_OLD_GRAND) for group in _D_GROUPS
)
#: Reported, then read: a sentence saying a claimed amount changed.
_CLAIMED = re.compile(r"(?:قيمة|اجمالي|مجموع|جملة)\s+(?:ال)?(?:مطالبة|مدفوع|مسدد|دعوى)|المبلغ\s+المطالب")
_CHANGED = re.compile(
    r"ارتفع|صار|اصبح|تغير|زاد|حدث|تعدل|عدل|اضيف|اضافة|ادرج|(?<![\u0621-\u064A])ضم(?:ت|ها)?(?![\u0621-\u064A])"
)
_NOT_CHANGED = re.compile(r"(?<![\u0621-\u064A])(?:لم|لا|دون|بقي|بقيت|ظل|ظلت|غير)(?![\u0621-\u064A])")


def leaked_into(text: str, total: int) -> list[int]:
    """The sums inside any figure that equals ``total`` plus some of ``D_SUMS``."""
    return sorted({
        value for group in _D_GROUPS if has_figure(text, total + sum(group)) for value in group
    })


def relief_amounts(document: str) -> set[int]:
    """Every amount in the relief requested. Dates are left out by their slashes."""
    requests = plain(section_text(document, "الطلبات"))
    return {int(number) for number in re.findall(r"(?<![\d/])\d{4,}(?![\d/])", requests)}


def announces_new_amount(text: str) -> bool:
    """Names a total only a leak could produce, or says a claimed amount changed."""
    if any(has_figure(text, total) for total in _D_LEAK_TOTALS):
        return True
    return any(
        _CLAIMED.search(sentence) and _CHANGED.search(sentence) and not _NOT_CHANGED.search(sentence)
        for sentence in re.split(r"[.؛:\n]", base._bare(text))
    )


def grade_no_leak(run: dict) -> dict:
    """D: the claim and the relief stay exactly as they were, and no sum enters a total.

    Fixed before the first run. Naming a sum as a fact in the body is allowed, so that
    is reported and not graded. Every run counts: length is not what this group tests,
    and a leak in a rewrite that did not shrink is still a leak.
    """
    document = run["document"]
    chat = run.get("chat_text") or ""
    findings = "\n".join(run.get("key_findings") or [])
    into_claim = leaked_into(document, LONG_OLD_GRAND)
    into_paid = leaked_into(document, LONG_OLD_PAID)
    relief = relief_amounts(document)
    heads = {LONG_OLD_PAID, LONG_REMEDIATION, LONG_ENGINEER_FEES}
    checks = {
        "claim_total_unchanged": has_figure(document, LONG_OLD_GRAND) and not into_claim,
        "paid_total_unchanged": has_figure(document, LONG_OLD_PAID) and not into_paid,
        "relief_requested_unchanged": (
            relief <= {*heads, LONG_OLD_GRAND} and (LONG_OLD_GRAND in relief or heads <= relief)
        ),
    }
    reported: dict[str, bool] = {}
    for value in D_SUMS:
        checks[f"sum_{value}_in_no_total"] = value not in {*into_claim, *into_paid, *relief}
        reported[f"sum_{value}_mentioned_in_document"] = has_figure(document, value)
        reported[f"sum_{value}_in_a_table_row"] = any(
            "|" in line and has_figure(line, value) for line in document.splitlines()
        )
        reported[f"sum_{value}_in_key_findings"] = has_figure(findings, value)
    status = argument_status(document)
    ratio = _ratio(run)
    cited = citations_in(document)
    known = [reference[0] for reference in LONG_REFERENCES]
    missing = entry_values_missing(document)
    return _finish(
        checks,
        {
            "length_ratio": ratio,
            "bucket": _bucket(ratio),
            "leaked_into_claim": into_claim,
            "leaked_into_paid": into_paid,
            "relief_amounts": sorted(relief),
            "limbs_kept": status["limbs_kept"],
            "argument_text": status["text"][:700],
            "citations_kept": cited,
            "entry_values_missing": missing,
            "key_findings": run.get("key_findings") or [],
        },
        reported={
            "key_findings_announce_a_new_amount": announces_new_amount(findings),
            "chat_fields_announce_a_new_amount": announces_new_amount(chat),
            "old_total_in_words_kept": bool(_OLD_GRAND_IN_WORDS.search(base._bare(document))),
            **reported,
            "conclusion_survives": status["conclusion"],
            "reasoning_survives": status["limbs_kept"] >= MIN_REASONING_LIMBS,
            "all_citations_kept": set(cited) >= set(known),
            "all_entry_values_kept": not missing,
            "memory_not_mentioned": not base.MEMORY_WORDS.search(document + chat),
            **_other_entries(document),
        },
    )


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        id="B_control",
        title="scenario B with no memory: the payment is in the plan only",
        matter=CLAIM_MATTER,
        figures=CLAIM_FIGURES,
        memory=False,
        subtype="statement_of_claim",
        edit_mode="instruct",
        user_message="خل أسلوب الصحيفة أرصن ورتب الوقائع",
        task_label="إعادة صياغة صحيفة الدعوى بأسلوب أرصن",
        # The request does not mention the payment, and <user_request> is where a
        # request reaches the writer. So the brief carries it in <plan> alone.
        intent_ar=f"إعادة صياغة {_CLAIM_INTRO} بأسلوب أكثر رصانة، وترتيب الوقائع ترتيباً أوضح.",
        plan_md=(
            "## ما سيتضمنه المستند\n"
            "- صياغة أرصن للوقائع والأسانيد والطلبات.\n"
            f"- الوقائع: التعاقد، الدفعات ومنها الدفعة النقدية {_n(FOURTH_PAYMENT)} ريال بتاريخ "
            f"{FOURTH_PAYMENT_DATE}، توقف المؤسسة عن العمل، الإنذار.\n"
            "- الطلبات: فسخ العقد ورد المبالغ المدفوعة."
        ),
        grade=grade_payment,
    ),
    Scenario(
        id="B_long",
        title="scenario B on the long document, among 20 entries",
        matter=LONG_MATTER,
        figures=LONG_FIGURES,
        memory=True,
        subtype="statement_of_claim",
        edit_mode="instruct",
        user_message="خل أسلوب الصحيفة أرصن ورتب الوقائع",
        task_label="إعادة صياغة صحيفة الدعوى بأسلوب أرصن",
        intent_ar=f"إعادة صياغة {_LONG_INTRO} بأسلوب أكثر رصانة، وترتيب الوقائع ترتيباً أوضح.",
        plan_md=(
            "## ما سيتضمنه المستند\n"
            "- صياغة أرصن للوقائع والأسانيد والطلبات.\n"
            f"- الوقائع: التعاقد، الدفعات ومنها الدفعة النقدية {_n(LONG_PAYMENT)} ريال بتاريخ "
            f"{LONG_PAYMENT_DATE}، توقف الشركة عن العمل، الإنذار، التقرير الهندسي.\n"
            "- الطلبات: فسخ العقد ورد المبالغ المدفوعة والتعويض."
        ),
        grade=grade_payment,
    ),
    Scenario(
        id="C_long",
        title="the user's own argument, on the long document, cut to about half",
        matter=LONG_MATTER,
        figures=LONG_FIGURES,
        memory=True,
        subtype="statement_of_claim",
        edit_mode="instruct",
        user_message="اختصر الصحيفة للنصف تقريباً ورتبها",
        task_label="اختصار صحيفة الدعوى إلى نحو النصف",
        intent_ar=(
            f"اختصار {_LONG_INTRO} إلى نحو نصف حجمها الحالي وترتيبها، بتقليص الوقائع والأسانيد "
            "وحذف التكرار."
        ),
        plan_md="## ما سيتضمنه المستند\n- صحيفة دعوى بنحو نصف حجم المسودة الحالية.",
        grade=grade_argument,
    ),
)
SCENARIOS += (
    replace(
        SCENARIOS[-1],  # C_long's document, request and plan, word for word
        id="D_long",
        title="money facts that must stay out of the claim, on the same shorter rewrite",
        matter=D_MATTER,
        grade=grade_no_leak,
    ),
)
SCENARIOS_BY_ID = {scenario.id: scenario for scenario in SCENARIOS}


# --------------------------------------------------------------------------- #
# Guards and fixture checks
# --------------------------------------------------------------------------- #


def fixtures_sha256() -> str:
    """So ``--resume`` cannot mix runs from two versions of the fixtures."""
    parts: list[str] = []
    for scenario in SCENARIOS:
        matter = scenario.matter
        parts += [scenario.user_message, scenario.intent_ar, scenario.plan_md, str(scenario.memory)]
        parts += [matter.attachment_text, repr(matter.entries), repr(matter.sections)]
        parts.append(repr(matter.research))
    parts.append(repr((MAX_LENGTH_RATIO, MIN_REASONING_LIMBS, RUNS_PER_GROUP)))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def check_values_are_invented() -> None:
    """Refuse to run if a fixture value already sits where the model could copy it."""
    numbers = (
        FIRST_PAYMENT, SECOND_PAYMENT, THIRD_PAYMENT, FOURTH_PAYMENT, OLD_TOTAL, CORRECTED_TOTAL,
        CONTRACT_VALUE, LONG_PAYMENT, LONG_OLD_PAID, LONG_NEW_PAID, LONG_OLD_GRAND, LONG_NEW_GRAND,
        LONG_REMEDIATION, LONG_ENGINEER_FEES, LONG_CONTRACT_VALUE, *(row[3] for row in LONG_PAYMENTS),
        *D_SUMS, *_D_LEAK_TOTALS,
    )
    values = [
        *(_n(number) for number in numbers),
        *(str(number) for number in numbers),
        *(name for scenario in SCENARIOS for name, _role in scenario.matter.parties),
        "تركي البلوي",
        "سنا التهامي",
        "رواق العقيق",
    ]
    sources = (
        "agents/writer/prompts.py",
        "agents/writer/agent.py",
        "agents/writer_planner/prompts.py",
        "agents/utils/conversation_memory.py",
        "agents/memory/convo_memory_writer/prompts.py",
        ".claude/plans/conversation_memory_agent.md",  # §6 examples: the next prompt
        "scripts/eval_conversation_memory.py",
    )
    texts = {source: (_ROOT / source).read_text(encoding="utf-8") for source in sources}
    hits = [f"{value} in {source}" for source, text in texts.items() for value in values if value in text]
    if hits:
        raise SystemExit(f"fixture values are not unique: {hits}")


def prior_document(matter: Matter, assemble: Callable, output_model: Any) -> str:
    """The previous draft as ``publish_writer_result`` stores it."""
    return assemble(
        output_model(
            title_ar=matter.draft_title,
            sections=[{"heading_ar": h, "body_md": b} for h, b in matter.sections],
            confidence="medium",
        )
    )


def _corrected_sections(scenario: Scenario) -> list[tuple[str, str]]:
    """A right answer, for the self-test and for ``--dry-run``."""
    figures = scenario.figures
    fixed = []
    for heading, body in scenario.matter.sections:
        body = body.replace(figures.row_anchor, f"{figures.new_row}{figures.row_anchor}")
        for old, new in figures.swaps:
            body = body.replace(old, new)
        fixed.append((heading, body))
    return fixed


def _shortened_sections(*, keep_reasoning: bool, corrected: bool = True) -> list[tuple[str, str]]:
    """A hard cut, for the self-test and for ``--dry-run``."""
    scenario = SCENARIOS_BY_ID["C_long"]
    sections = dict(_corrected_sections(scenario) if corrected else scenario.matter.sections)
    argument = LONG_ARGUMENT_PARAGRAPH if keep_reasoning else LONG_ARGUMENT_HEADING
    return [
        ("## بيانات الدعوى", sections["## بيانات الدعوى"]),
        ("## الأسانيد", argument),
        ("## الطلبات", sections["## الطلبات"]),
    ]


def _d_sections(*, leaked: int | None = None) -> list[tuple[str, str]]:
    """A cut with the claim as it was: the sums named as facts, or one leaked."""
    sections = _shortened_sections(keep_reasoning=True, corrected=False)
    if leaked is None:
        facts = " ".join(f"ودفعت المدعية مبلغ {_n(value)} ريال." for value in D_SUMS)
        return [*sections, ("## ملاحظات", facts)]
    swaps = ((LONG_OLD_GRAND, LONG_OLD_GRAND + leaked), (LONG_OLD_PAID, LONG_OLD_PAID + leaked))
    fixed = []
    for heading, body in sections:
        for old, new in swaps:
            body = body.replace(_n(old), _n(new))
        fixed.append((heading, body))
    return fixed


def self_test() -> None:
    """The grading must fail what it should fail, before any model is called."""
    from agents.utils.conversation_memory import MAX_ENTRY_CHARS, MAX_TOTAL_CHARS
    from agents.writer.models import WriterLLMOutput
    from agents.writer.publisher import _assemble_content

    def assembled(sections: list[tuple[str, str]]) -> str:
        return _assemble_content(
            WriterLLMOutput(
                title_ar=LONG_MATTER.draft_title,
                sections=[{"heading_ar": h, "body_md": b} for h, b in sections],
                confidence="high",
            )
        )

    def graded(scenario: Scenario, document: str, prior: int, findings: tuple = ()) -> dict:
        run = {
            "scenario": scenario.id, "document": document, "chat_text": "",
            "key_findings": list(findings), "prior_chars": prior,
        }
        return scenario.grade(run)

    long_prior = prior_document(LONG_MATTER, _assemble_content, WriterLLMOutput)
    assert OLD_TOTAL == 80_522 and CORRECTED_TOTAL == 99_257
    assert LONG_OLD_PAID == 712_980 and LONG_NEW_PAID == 771_327
    assert LONG_OLD_GRAND == 789_845 and LONG_NEW_GRAND == 848_192
    assert 10_000 <= len(long_prior) <= 12_000, len(long_prior)
    assert len(LONG_MATTER.entries) == 20
    assert all(len(text) <= MAX_ENTRY_CHARS for _kind, text in LONG_MATTER.entries)
    assert sum(len(text) for _kind, text in LONG_MATTER.entries) <= MAX_TOTAL_CHARS
    assert citations_in(long_prior) == [reference[0] for reference in LONG_REFERENCES]

    for scenario in (SCENARIOS_BY_ID["B_control"], SCENARIOS_BY_ID["B_long"]):
        prior = prior_document(scenario.matter, _assemble_content, WriterLLMOutput)
        fixed = assembled(_corrected_sections(scenario))
        size = len(prior)
        assert not graded(scenario, prior, size)["passed"]
        assert graded(scenario, fixed, size)["passed"]
        arabic_digits = fixed.translate(str.maketrans("0123456789,", "٠١٢٣٤٥٦٧٨٩٬"))
        assert graded(scenario, arabic_digits, size)["passed"]
        for value, old in zip(scenario.figures.corrected, scenario.figures.old):
            half_done = fixed.replace(_n(value), _n(old))
            assert not graded(scenario, half_done, size)["passed"]
            rounded = fixed.replace(_n(value), f"{value // 1000} ألف")
            assert not graded(scenario, rounded, size)["passed"]
        words_left = fixed + f"\n\n{scenario.figures.swaps[0][0]}"
        assert not graded(scenario, words_left, size)["passed"]

    scenario, size = SCENARIOS_BY_ID["C_long"], len(long_prior)
    uncut = graded(scenario, long_prior, size)
    assert not uncut["checks"]["claim_total_corrected"] and not uncut["counted"]
    assert uncut["checks"]["conclusion_survives"] and uncut["checks"]["reasoning_survives"]
    assert uncut["info"]["bucket"] == "did_not_shrink"
    assert uncut["info"]["limbs_kept"] == len(REASONING_LIMBS)
    cut_doc = assembled(_shortened_sections(keep_reasoning=True))
    cut = graded(scenario, cut_doc, size)
    assert cut["passed"] and cut["counted"]
    assert cut["reported"]["relief_requested_corrected"]
    assert cut["reported"]["claim_header_holds_new_total"]
    assert cut["reported"]["new_total_in_words"]
    assert not cut["reported"]["new_total_in_key_findings"]
    told = graded(scenario, cut_doc, size, (f"أصبحت قيمة المطالبة {_n(LONG_NEW_GRAND)} ريال",))
    assert told["reported"]["new_total_in_key_findings"] and told["reported"]["new_total_in_first_finding"]
    arabic_digits = cut_doc.translate(str.maketrans("0123456789,", "٠١٢٣٤٥٦٧٨٩٬"))
    assert graded(scenario, arabic_digits, size)["checks"]["claim_total_corrected"]
    # Round 2's real failure: the payment is stated, the claim keeps the old total.
    stale_doc = assembled(_shortened_sections(keep_reasoning=True, corrected=False))
    stale = graded(scenario, stale_doc + f"\n\nدفعة نقدية {_n(LONG_PAYMENT)} ريال.", size)
    assert stale["reported"]["payment_in_document"] and not stale["checks"]["claim_total_corrected"]
    assert stale["checks"]["conclusion_survives"] and stale["checks"]["reasoning_survives"]
    assert not stale["reported"]["relief_requested_corrected"]
    both = graded(scenario, cut_doc + f"\n\n{_n(LONG_OLD_GRAND)}", size)
    assert not both["checks"]["claim_total_corrected"]
    words = graded(scenario, cut_doc + f"\n\n{LONG_OLD_GRAND_WORDS}", size)
    assert not words["checks"]["claim_total_corrected"]
    # A true subtotal beside the cash payment is not a stale claim total.
    subtotal = graded(scenario, cut_doc + f"\n\nدفعات بلغت {_n(LONG_OLD_PAID)} ريال.", size)
    assert subtotal["checks"]["claim_total_corrected"]
    conclusion_only = graded(scenario, assembled(_shortened_sections(keep_reasoning=False)), size)
    assert conclusion_only["counted"] and not conclusion_only["passed"]
    assert conclusion_only["checks"]["conclusion_survives"]
    assert not conclusion_only["checks"]["reasoning_survives"]
    gone = graded(scenario, long_prior.replace(LONG_ARGUMENT_PARAGRAPH, ""), size)
    assert not gone["checks"]["conclusion_survives"]
    assert not gone["checks"]["reasoning_survives"]
    assert graded(
        scenario, long_prior[: int(size * 0.8)] + LONG_ARGUMENT_PARAGRAPH, size
    )["info"]["bucket"] == "shrank_too_little"

    # D: only the entries differ from C, and only they hold the sums.
    scenario = SCENARIOS_BY_ID["D_long"]
    c_long = SCENARIOS_BY_ID["C_long"]
    assert (scenario.intent_ar, scenario.plan_md, scenario.user_message) == (
        c_long.intent_ar, c_long.plan_md, c_long.user_message,
    )
    assert scenario.matter.sections == c_long.matter.sections
    assert _n(LONG_PAYMENT) not in repr(scenario.matter.entries)
    outside = repr((scenario.matter.sections, scenario.matter.attachment_text, scenario.plan_md, LONG_RESEARCH_BODY))
    assert not any(_n(value) in outside for value in D_SUMS)
    assert all(_n(value) in repr(scenario.matter.entries) for value in D_SUMS)
    # The draft is right as it stands, and so is a cut that names the sums as facts.
    assert graded(scenario, long_prior, size)["passed"]
    clean = graded(scenario, assembled(_d_sections()), size, ("لم تتغير قيمة المطالبة.",))
    assert clean["passed"] and clean["reported"][f"sum_{D_SEPARATE}_mentioned_in_document"]
    assert not clean["reported"]["key_findings_announce_a_new_amount"]
    # Each sum, leaked into both totals, fails its own line and no other sum's.
    for value in D_SUMS:
        doc = assembled(_d_sections(leaked=value))
        leak = graded(scenario, doc, size, (f"ارتفعت قيمة المطالبة إلى {_n(LONG_OLD_GRAND + value)} ريال.",))
        assert not leak["passed"] and not leak["checks"][f"sum_{value}_in_no_total"]
        assert not leak["checks"]["claim_total_unchanged"] and not leak["checks"]["paid_total_unchanged"]
        assert not leak["checks"]["relief_requested_unchanged"]
        assert all(leak["checks"][f"sum_{other}_in_no_total"] for other in D_SUMS if other != value)
        assert leak["reported"]["key_findings_announce_a_new_amount"]
        arabic = doc.translate(str.maketrans("0123456789,", "٠١٢٣٤٥٦٧٨٩٬"))
        assert not graded(scenario, arabic, size)["checks"]["claim_total_unchanged"]
    # Two sums at once, and a leak into the claim total alone.
    pair = long_prior.replace(_n(LONG_OLD_GRAND), _n(LONG_OLD_GRAND + D_NOT_CLAIMED + D_SEPARATE))
    both = graded(scenario, pair, size)
    assert not both["checks"][f"sum_{D_NOT_CLAIMED}_in_no_total"]
    assert not both["checks"][f"sum_{D_SEPARATE}_in_no_total"]
    assert both["checks"][f"sum_{D_THIRD_PARTY}_in_no_total"] and both["checks"]["paid_total_unchanged"]
    # A new relief item fails even with both totals untouched.
    relief = long_prior.replace("5. إلزام المدعى عليها بتسليم", f"5. إلزامها بمبلغ {_n(D_THIRD_PARTY)} ريال.\n6. إلزام المدعى عليها بتسليم")
    extra = graded(scenario, relief, size)
    assert not extra["checks"]["relief_requested_unchanged"]
    assert not extra["checks"][f"sum_{D_THIRD_PARTY}_in_no_total"]
    assert extra["checks"]["claim_total_unchanged"]
    # Losing a total is not "unchanged".
    assert not graded(scenario, long_prior.replace(_n(LONG_OLD_GRAND), ""), size)["passed"]
    # A sum in a row, with the total untouched, is reported and not graded.
    row = long_prior.replace("| | | | **الإجمالي**", f"| 7 | | مظلات | | {_n(D_NOT_CLAIMED)} |\n| | | | **الإجمالي**")
    shown = graded(scenario, row, size)
    assert shown["passed"] and shown["reported"][f"sum_{D_NOT_CLAIMED}_in_a_table_row"]


# --------------------------------------------------------------------------- #
# One run
# --------------------------------------------------------------------------- #


class BudgetExhausted(Exception):
    """Raised by the HTTP hook, before a request would pass the cap."""


@dataclass
class Budget:
    """Requests that may have been billed: sent, and not answered with an error."""

    cap: int = 0
    used: int = 0
    refused: int = 0
    stop: str | None = None


@dataclass
class Slot:
    """What one run produced outside the writer's output."""

    scenario: Scenario
    index: int
    note: str  # ``_MEMORY_DRAFTING_NOTE``, to prove it reached the writer or did not
    http: list[dict] = field(default_factory=list)
    logged: list[str] = field(default_factory=list)
    prompt: dict | None = None


_SLOT: ContextVar[Slot | None] = ContextVar("eval_slot", default=None)
BUDGET = Budget()


class _WriterLogCapture(logging.Handler):
    """``_run_writer`` swallows failures into a placeholder. Keep why."""

    def emit(self, record: logging.LogRecord) -> None:
        slot = _SLOT.get()
        if slot is not None:
            slot.logged.append(base.scrub(record.getMessage())[:800])


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
    return ""


def _prompt_seen(slot: Slot, system: str, user: str) -> None:
    """Keep the first prompt of the run, and what it held."""
    if slot.prompt is not None:
        return
    wanted = slot.scenario.memory
    slot.prompt = {
        "system": system,
        "user": user,
        "sha256": hashlib.sha256(f"{system}\n{user}".encode("utf-8")).hexdigest(),
        "memory_as_expected": ("<conversation_memory>" in system) == wanted and (slot.note in system) == wanted,
        "has_prior_draft": "<prior_draft>" in system,
        "has_revision_marker": "### Revision mode" in system,
        # On a line of its own: the static prompt also names the tags, inline.
        "has_refs": bool(re.search(r"^\s*<refs>\s*$", system, re.MULTILINE)),
        "payment_in_plan": _n(slot.scenario.figures.payment) in "".join(
            re.findall(r"^\s*<plan>\s*$(.*?)^\s*</plan>\s*$", system, re.MULTILINE | re.DOTALL)
        ),
    }


def _cost(event: dict) -> float | None:
    """List price of one completion, in USD, from the model registry."""
    from agents.model_registry import get_model_config

    usage = event.get("usage") or {}
    prompt, completion = usage.get("prompt_tokens") or 0, usage.get("completion_tokens") or 0
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    try:
        config = get_model_config(event["model"])
    except ValueError:
        return None
    if config.input_price is None or config.output_price is None:
        return None
    cached_price = config.cached_input_price
    cached_price = config.input_price if cached_price is None else cached_price
    return (
        (prompt - cached) * config.input_price + cached * cached_price
        + completion * config.output_price
    ) / 1_000_000


async def _on_request(request: Any) -> None:
    slot = _SLOT.get()
    if slot is None:
        return
    if BUDGET.used >= BUDGET.cap:
        BUDGET.refused += 1
        raise BudgetExhausted(f"writer call cap of {BUDGET.cap} reached")
    BUDGET.used += 1
    body = json.loads(request.content)
    messages = body.get("messages") or []
    _prompt_seen(
        slot,
        "\n\n".join(_text_of(m.get("content")) for m in messages if m.get("role") == "system"),
        "\n\n".join(_text_of(m.get("content")) for m in messages if m.get("role") == "user"),
    )
    event = {"host": request.url.host, "model": body.get("model"), "status": None, "error": None}
    slot.http.append(event)
    request.extensions["eval_event"] = event


async def _on_response(response: Any) -> None:
    event = response.request.extensions.get("eval_event")
    if event is None:
        return
    event["status"] = response.status_code
    await response.aread()
    if response.status_code >= 400:
        BUDGET.used -= 1  # an error response is not a completion
        event["error"] = base.scrub(response.text)[:800]
        return
    try:
        data = response.json()
    except ValueError:
        event["error"] = "response was not JSON"
        return
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    event.update(
        usage=data.get("usage"),
        response_model=data.get("model"),
        finish_reason=choice.get("finish_reason"),
        reasoning=message.get("reasoning_content") or message.get("reasoning") or "",
    )
    event["cost_usd"] = _cost(event)


def instrument_http(cells: list) -> None:
    """Hook each cell's client. The budget lives here, so a client we miss is a hole."""
    for client in {id(cell.client._client): cell.client._client for cell in cells}.values():
        client.event_hooks["request"].append(_on_request)
        client.event_hooks["response"].append(_on_response)


async def stub_references(_supabase: Any, item_id: str, *, used_only: bool = False) -> list:
    """``fetch_item_references`` rebuilds citations from the corpus tables.

    The fixture's guide is invented, so it has no rows there. The walker's
    ``_render_refs_md`` and the package's ``<refs>`` block run for real on these.
    """
    research = _SLOT.get().scenario.matter.research
    if item_id != RESEARCH_ID or research is None:
        return []
    return [
        SimpleNamespace(n=number, title=title, snippet=snippet)
        for number, title, snippet in research.references
    ]


def _aliases(matter: Matter) -> tuple[str, str | None]:
    """``(draft, research)``. The attachment is always ``ATTACHMENT_ALIAS``."""
    return ("WI-3", "WI-2") if matter.research is not None else ("WI-2", None)


def seed_db(scenario: Scenario) -> base.FakeSupabase:
    """The conversation as the planner finds it. No entries in the control."""
    from agents.writer.models import WriterLLMOutput
    from agents.writer.publisher import _assemble_content

    matter = scenario.matter
    draft = prior_document(matter, _assemble_content, WriterLLMOutput)
    common = {"conversation_id": CONVO_ID, "user_id": USER_ID, "deleted_at": None, "metadata": {}}
    items = [{
        **common, "item_id": ATTACHMENT_ID, "wi_seq": 1, "kind": "attachment",
        "title": matter.attachment_title, "summary": matter.attachment_summary,
        "content_md": matter.attachment_text, "doc_type": "contract",
        "word_count": len(matter.attachment_text.split()), "created_at": base._stamp(1),
    }]
    if matter.research is not None:
        items.append({
            **common, "item_id": RESEARCH_ID, "wi_seq": 2, "kind": "agent_search",
            "title": matter.research.title, "summary": matter.research.summary,
            "content_md": matter.research.body, "doc_type": "",
            "word_count": len(matter.research.body.split()), "created_at": base._stamp(2),
        })
    items.append({
        **common, "item_id": DRAFT_ID, "wi_seq": len(items) + 1, "kind": "agent_writing",
        "title": matter.draft_title, "summary": matter.draft_summary,
        "content_md": draft, "doc_type": "",
        "word_count": len(draft.split()), "created_at": base._stamp(3),
    })
    entries = matter.entries if scenario.memory else ()
    return base.FakeSupabase(
        users=[{"user_id": USER_ID, "preferred_name": None, "full_name_ar": None}],
        workspace_items=items,
        conversation_memory_entries=[
            {
                "conversation_id": CONVO_ID, "user_id": USER_ID, "seq": seq,
                "kind": kind, "content": content, "insisted": False,
            }
            for seq, (kind, content) in enumerate(entries, start=1)
        ],
    )


def memory_block(db: base.FakeSupabase) -> str | None:
    """As ``orchestrator._load_conversation_memory`` builds it. None with no entries."""
    from agents.utils.conversation_memory import (
        encode_entries,
        load_conversation_memory,
        render_conversation_memory,
    )

    return render_conversation_memory(
        encode_entries(load_conversation_memory(db, CONVO_ID, USER_ID))
    )


def planner_decision(scenario: Scenario) -> Any:
    """What the planner hands the writer on a revision of the draft."""
    from agents.writer.models import CaseParty
    from agents.writer_planner.models import PlannerDecision

    draft, research = _aliases(scenario.matter)
    sources = [ATTACHMENT_ALIAS, *([research] if research else [])]
    return PlannerDecision(
        parties=[CaseParty(name=name, role=role) for name, role in scenario.matter.parties],
        intent_ar=scenario.intent_ar,
        subtype=scenario.subtype,
        edit_mode=scenario.edit_mode,
        plan_md=scenario.plan_md,
        selected_wis=[draft, *sources],
        role_assignments={draft: "prior_draft", **{alias: "source" for alias in sources}},
        rationale="تعديل مسودة قائمة.",
    )


async def scripted_planner(_messages: list, info: Any) -> Any:
    from pydantic_ai.messages import ModelResponse, ToolCallPart

    decision = planner_decision(_SLOT.get().scenario)
    return ModelResponse(parts=[
        ToolCallPart(tool_name=info.output_tools[0].name, args=decision.model_dump(mode="json"))
    ])


async def scripted_writer(messages: list, info: Any) -> Any:
    """``--dry-run`` only. Even runs return a right answer, odd runs a wrong one."""
    from pydantic_ai.messages import ModelResponse, ToolCallPart, UserPromptPart

    slot = _SLOT.get()
    user = "\n".join(
        str(part.content) for part in messages[-1].parts if isinstance(part, UserPromptPart)
    )
    _prompt_seen(slot, messages[-1].instructions or "", user)
    scenario, right = slot.scenario, slot.index % 2 == 0
    if scenario.id == "D_long":
        sections = _d_sections() if right else _d_sections(leaked=D_SEPARATE)
    elif scenario.id == "C_long":
        sections = _shortened_sections(keep_reasoning=right)
    else:
        sections = _corrected_sections(scenario) if right else list(scenario.matter.sections)
    findings = [f"أصبحت قيمة المطالبة {_n(LONG_NEW_GRAND)} ريال." if right else "راجع الجدول."]
    if scenario.id == "D_long":
        wrong = f"ارتفعت قيمة المطالبة إلى {_n(LONG_OLD_GRAND + D_SEPARATE)} ريال."
        findings = ["لم تتغير قيمة المطالبة." if right else wrong]
    args = {
        "title_ar": scenario.matter.draft_title,
        "sections": [{"heading_ar": h, "body_md": b} for h, b in sections],
        "confidence": "high",
        "chat_summary": "أعددت المسودة.",
        "key_findings": findings,
    }
    return ModelResponse(parts=[ToolCallPart(tool_name=info.output_tools[0].name, args=args)])


async def run_one(slot: Slot, timeout: float, prior_chars: int) -> dict:
    from agents.models import MajorAgentInput
    from agents.writer_planner.runner import handle_writer_planner_turn

    _SLOT.set(slot)
    scenario = slot.scenario
    db = seed_db(scenario)
    block = memory_block(db)
    if bool(block) != scenario.memory:
        raise SystemExit(f"{scenario.id}: memory block present={bool(block)}, wanted {scenario.memory}")
    major_input = MajorAgentInput(
        describe_query=scenario.user_message,  # ``_route`` passes the raw message
        task_label=scenario.task_label,
        conversation_memory_md=block,
        target_item_id=DRAFT_ID,
        user_id=USER_ID,
        conversation_id=CONVO_ID,
    )
    run: dict[str, Any] = {
        "scenario": scenario.id, "index": slot.index,
        "status": "error", "error": None, "document": "", "chat_text": "",
        "prior_chars": prior_chars,
    }
    started = time.monotonic()
    try:
        turn = await asyncio.wait_for(
            handle_writer_planner_turn(major_input, db, assistant_message_id=ASSISTANT_MESSAGE_ID),
            timeout,
        )
    except Exception as exc:  # noqa: BLE001
        run["error"] = base.scrub(base._describe_exception(exc))[:1500]
    else:
        result = turn.result
        rows = db.tables["workspace_items"]
        stored = next(
            (row for row in rows if result and row["item_id"] == result.output_item_id), None
        )
        run.update(kind=turn.kind, title=stored.get("title") if stored else None)
        if stored is None:
            run["error"] = f"nothing stored: {turn.kind}: {getattr(result, 'chat_summary', '')[:300]}"
        elif FAILED_DRAFT_TITLE in stored["content_md"]:
            run["error"] = "the writer returned its failure placeholder"
        else:
            notes = (stored.get("metadata") or {}).get("notes") or []
            chat = [result.chat_summary or "", *(result.key_findings or []), *notes]
            run.update(
                status="ok",
                document=stored["content_md"],
                chat_text="\n".join(chat),
                key_findings=list(result.key_findings or []),
                exhibits=(stored.get("metadata") or {}).get("exhibits") or [],
                citations_used=(stored.get("metadata") or {}).get("citations_used") or [],
                prior_soft_deleted=next(
                    row for row in rows if row["item_id"] == DRAFT_ID
                )["deleted_at"] is not None,
            )
    run["seconds"] = round(time.monotonic() - started, 1)
    run["http"] = slot.http
    run["logged"] = slot.logged
    run["prompt_flags"] = {k: v for k, v in (slot.prompt or {}).items() if k not in ("system", "user")}
    run["grade"] = scenario.grade(run) if run["status"] == "ok" else None
    return run


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #


def summarize(runs: list[dict]) -> dict:
    groups: dict[str, dict] = {}
    for scenario in SCENARIOS:
        mine = [run for run in runs if run["scenario"] == scenario.id]
        if not mine:
            continue
        ok = [run for run in mine if run["status"] == "ok"]
        counted = [run for run in ok if run["grade"]["counted"]]
        names = sorted({name for run in ok for name in run["grade"]["checks"]})
        reported = [run["grade"]["reported"] for run in ok]
        groups[scenario.id] = {
            "runs": len(mine),
            "errors": len(mine) - len(ok),
            "counted": len(counted),
            "passed": sum(run["grade"]["passed"] for run in counted),
            "lines": {
                name: sum(bool(run["grade"]["checks"].get(name)) for run in counted)
                for name in names
            },
            "reported_not_graded": {
                name: sum(bool(item.get(name)) for item in reported)
                for name in (reported[0] if reported else ())
            },
            "not_counted": [
                {
                    "index": run["index"],
                    "length_ratio": run["grade"]["info"].get("length_ratio"),
                    "bucket": run["grade"]["info"].get("bucket"),
                    "checks": run["grade"]["checks"],
                }
                for run in ok if not run["grade"]["counted"]
            ],
        }
    events = [event for run in runs for event in run["http"]]
    done = [event for event in events if event.get("usage")]
    costs = [event.get("cost_usd") for event in done]
    usage = [event["usage"] for event in done]
    return {
        "groups": groups,
        "http_attempts": len(events),
        "completions": len(done),
        "answered_by": Counter(f"{e['host']} {e['model']}" for e in done).most_common(),
        "error_statuses": Counter(
            f"{e['host']} {e['status']}" for e in events if e["status"] != 200
        ).most_common(),
        "prompt_tokens": sum(u.get("prompt_tokens") or 0 for u in usage),
        "cached_tokens": sum(
            (u.get("prompt_tokens_details") or {}).get("cached_tokens") or 0 for u in usage
        ),
        "completion_tokens": sum(u.get("completion_tokens") or 0 for u in usage),
        "reasoning_tokens": sum(
            (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0 for u in usage
        ),
        "cost_usd": round(sum(c for c in costs if c is not None), 4) if costs else None,
        "cost_unknown_for": sum(c is None for c in costs),
        "seconds": sorted(run["seconds"] for run in runs),
    }


def evidence(run: dict) -> list[str]:
    """The lines worth reading. Grading does not use these."""
    figures = SCENARIOS_BY_ID[run["scenario"]].figures
    watch = (figures.payment, *figures.corrected, *figures.old)
    if run["scenario"] == "D_long":
        watch = (*D_SUMS, LONG_OLD_PAID, LONG_OLD_GRAND, *_D_LEAK_TOTALS)
    lines = run["document"].splitlines()
    money = [line for line in lines if any(has_figure(line, value) for value in watch)][:10]
    if run["scenario"] in ("C_long", "D_long"):
        argument = run["grade"]["info"]["argument_text"]
        return [*([argument] if argument else []), *money]
    return money


def render_report(report: dict) -> str:
    runs, summary = report["runs"], report["summary"]
    out: list[str] = ["=== Results ==="]
    for scenario in SCENARIOS:
        if scenario.id not in summary["groups"]:
            continue
        group = summary["groups"][scenario.id]
        errors = f", {group['errors']} errors" if group["errors"] else ""
        out.append(
            f"{scenario.id}: {group['passed']} of {group['counted']} counted runs pass "
            f"({group['runs']} runs{errors}). {scenario.title}"
        )
        out += [f"    {name}: {ok} of {group['counted']}" for name, ok in group["lines"].items()]
        out += [
            f"    reported, not graded: {name}: {ok} of {group['runs']}"
            for name, ok in group["reported_not_graded"].items()
        ]
        for item in group["not_counted"]:
            out.append(
                f"    NOT COUNTED #{item['index']}: {item['bucket']}, ratio {item['length_ratio']}, "
                f"{json.dumps(item['checks'], ensure_ascii=False)}"
            )

    out.append("\n=== Each run ===")
    for run in runs:
        grade = run["grade"]
        head = f"{run['scenario']} #{run['index']}"
        models = ",".join(sorted({e["model"] or "?" for e in run["http"] if e.get("usage")}))
        if grade is None:
            out.append(f"{head}: ERROR {run['error']} | logged: {run['logged'][:2]}")
            continue
        missed = [name for name, ok in grade["checks"].items() if not ok]
        verdict = "PASS" if grade["passed"] else f"FAIL {missed}"
        out.append(f"{head}: {verdict} | {run['seconds']}s | {models} | {json.dumps(grade['info'], ensure_ascii=False)}")

    out.append("\n=== Lines from each document ===")
    for run in runs:
        if run["status"] != "ok":
            continue
        verdict = "PASS" if run["grade"]["passed"] else "FAIL"
        out.append(f"\n--- {run['scenario']} #{run['index']} {verdict}")
        out += [f"    {line}" for line in evidence(run)] or ["    (no matching line)"]

    meta = report["meta"]
    out.append("\n=== Models, tokens, cost ===")
    out.append(f"answered by: {summary['answered_by']}")
    out.append(f"non-200 responses: {summary['error_statuses']}")
    out.append(f"HTTP attempts {summary['http_attempts']}, completions {summary['completions']}, cap {meta.get('call_cap')}")
    out.append(
        f"tokens: prompt {summary['prompt_tokens']:,} (cached {summary['cached_tokens']:,}), "
        f"completion {summary['completion_tokens']:,} (reasoning {summary['reasoning_tokens']:,})"
    )
    out.append(f"cost at list price, USD: {summary['cost_usd']} (unknown for {summary['cost_unknown_for']})")
    out.append(f"seconds per run, sorted: {summary['seconds']}")
    out.append("\n=== Run setup ===")
    for key in (
        "dry_run", "groups", "call_cap", "chain", "keys_loaded", "block_rules_sha256",
        "memory_note_sha256", "fixtures_sha256", "concurrency", "blocked_hosts", "not_run",
    ):
        out.append(f"{key}: {meta.get(key)}")
    flags = [run["prompt_flags"] for run in runs if run.get("prompt_flags")]
    wanted = ("memory_as_expected", "has_prior_draft", "has_revision_marker")
    out.append(
        f"prompts as the group requires: {sum(all(f.get(k) for k in wanted) for f in flags)} of {len(flags)}"
    )
    return "\n".join(out) + "\n"


def write_outputs(report: dict, out: Path) -> str:
    report["summary"] = summarize(report["runs"])
    text = render_report(report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(base.scrub(json.dumps(report, ensure_ascii=False, indent=2)), encoding="utf-8")
    out.with_suffix(".txt").write_text(base.scrub(text), encoding="utf-8")
    return text


def regrade(path: Path, out: Path) -> int:
    report = json.loads(path.read_text(encoding="utf-8"))
    for run in report["runs"]:
        if run["status"] == "ok":
            run["grade"] = SCENARIOS_BY_ID[run["scenario"]].grade(run)
    base.say(write_outputs(report, out))
    return 0


# --------------------------------------------------------------------------- #
# Main
# --------------------------------------------------------------------------- #


async def evaluate(args: argparse.Namespace, key_names: list[str]) -> int:
    from pydantic_ai import __version__ as pydantic_ai_version
    from pydantic_ai.models.fallback import FallbackModel
    from pydantic_ai.models.function import FunctionModel

    import agents.writer_planner.walkers as walkers
    from agents.responder.wiring import responder_unified
    from agents.utils.agent_models import AGENT_MODELS
    from agents.utils.conversation_memory import _BLOCK_RULES, memory_enabled
    from agents.writer.agent import _MEMORY_DRAFTING_NOTE, create_writer_agent
    from agents.writer.models import WriterLLMOutput
    from agents.writer.publisher import _assemble_content
    from shared.config import get_settings

    if get_settings().SUPABASE_URL != base.DUMMY_ENV["SUPABASE_URL"] or not memory_enabled(USER_ID):
        base.say("refusing to run: settings are not the eval's dummy configuration")
        return 2
    if responder_unified("writer"):
        base.say("refusing to run: the unified responder would add an LLM call to every turn")
        return 2
    rules = ("with its reasoning", "changes it everywhere", "after any conflict")
    if not all(rule in _MEMORY_DRAFTING_NOTE for rule in rules):
        base.say("refusing to run: this code does not carry the rules under test")
        return 2
    check_values_are_invented()
    self_test()

    logging.getLogger().handlers = [_WriterLogCapture(logging.WARNING)]
    logging.getLogger().setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    for quiet in ("backend.app.services.writer_planner_context", "agents.writer.system_templates"):
        logging.getLogger(quiet).setLevel(logging.ERROR)  # the fake DB has no such tables

    walkers.fetch_item_references = stub_references
    AGENT_MODELS["writer_planner_decider"] = FallbackModel(FunctionModel(scripted_planner))
    if args.dry_run:
        AGENT_MODELS["agent_writer"] = FallbackModel(FunctionModel(scripted_writer))
    cells = list(create_writer_agent().model.models)
    chain = [f"{getattr(c, 'model_name', c)} @ {getattr(getattr(c, 'client', None), 'base_url', None)}" for c in cells]
    if not args.dry_run:
        instrument_http(cells)
        again = list(create_writer_agent().model.models)
        if {id(c.client._client) for c in again} - {id(c.client._client) for c in cells}:
            base.say("refusing to run: a new agent gets a client the budget hook is not on")
            return 2

    report: dict[str, Any] = {
        "meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "pydantic_ai": pydantic_ai_version,
            "dry_run": args.dry_run,
            "chain": chain,
            "keys_loaded": key_names,
            "block_rules_sha256": base._sha256(_BLOCK_RULES),
            "memory_note_sha256": base._sha256(_MEMORY_DRAFTING_NOTE),
            "fixtures_sha256": fixtures_sha256(),
            "groups": args.groups,
            "call_cap": BUDGET.cap,
            "concurrency": args.concurrency,
            "blocked_hosts": base._BLOCKED_HOSTS,
            "prompts": {},
        },
        "runs": [],
    }
    if args.resume and args.out.exists():
        saved = json.loads(args.out.read_text(encoding="utf-8"))
        if saved["meta"].get("dry_run") != args.dry_run:
            base.say("refusing to resume: that file is from the other mode")
            return 2
        for key in ("memory_note_sha256", "fixtures_sha256"):
            if saved["meta"].get(key) != report["meta"][key]:
                base.say(f"refusing to resume: {key} changed since those runs")
                return 2
        report["runs"] = saved["runs"]
        report["meta"]["prompts"] = saved["meta"].get("prompts", {})
        BUDGET.used = sum(
            1 for run in saved["runs"] for e in run["http"]
            if e["status"] is None or e["status"] < 400
        )
    done = {(run["scenario"], run["index"]) for run in report["runs"]}

    # Groups alternate, so a provider's bad minute cannot land on one of them.
    wanted = [
        (scenario, index)
        for index in range(RUNS_PER_GROUP)
        for scenario in SCENARIOS
        if scenario.id in args.groups and (scenario.id, index) not in done
    ][: args.max_runs]
    base.say(f"chain: {chain}")
    base.say(f"keys loaded (names only): {key_names}")
    base.say(f"runs to do: {len(wanted)}, calls already used: {BUDGET.used} of {BUDGET.cap}")

    prior_chars = {
        scenario.id: len(prior_document(scenario.matter, _assemble_content, WriterLLMOutput))
        for scenario in SCENARIOS
    }
    gate = asyncio.Semaphore(args.concurrency)

    async def one(scenario: Scenario, index: int) -> None:
        async with gate:
            if BUDGET.stop or BUDGET.used >= BUDGET.cap:
                # A retry can use up the cap early. Say so: a group with fewer runs
                # must not read as a complete one.
                report["meta"].setdefault("not_run", []).append(f"{scenario.id} #{index}")
                return
            slot = Slot(scenario, index, _MEMORY_DRAFTING_NOTE)
            run = await run_one(slot, args.timeout, prior_chars[scenario.id])
            report["runs"].append(run)
            if slot.prompt and scenario.id not in report["meta"]["prompts"]:
                report["meta"]["prompts"][scenario.id] = slot.prompt
            statuses = [event["status"] for event in slot.http]
            if not args.dry_run and not statuses:
                BUDGET.stop = "a run made no HTTP request: the budget hook is not on it"
            elif statuses and all(status in base.AUTH_STATUSES for status in statuses):
                BUDGET.stop = "every model in the chain failed on auth"
            grade = run["grade"]
            word = run["status"] if grade is None else ("PASS" if grade["passed"] else "FAIL")
            ratio = "" if grade is None else f", ratio {grade['info'].get('length_ratio')}"
            base.say(f"  {scenario.id} #{index}: {word}, {run['seconds']}s, {len(statuses)} req{ratio}")
            write_outputs(report, args.out)

    await asyncio.gather(*(one(*task) for task in wanted))
    if BUDGET.stop:
        base.say(f"stopped: {BUDGET.stop}")
    report["meta"]["blocked_hosts"] = base._BLOCKED_HOSTS
    report["meta"]["refused_by_budget"] = BUDGET.refused
    report["meta"]["stopped"] = BUDGET.stop
    base.say(write_outputs(report, args.out))
    base.say(f"JSON: {args.out}\nText: {args.out.with_suffix('.txt')}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Does the real writer keep what a memory entry holds?")
    parser.add_argument("--env-file", type=Path, help="Loads only *_API_KEY names from this file.")
    parser.add_argument("--out", type=Path, default=Path(tempfile.gettempdir()) / "writer_keeps_entries_2.json")
    parser.add_argument("--dry-run", action="store_true", help="A scripted writer: no key, no cost.")
    parser.add_argument("--resume", action="store_true", help="Skip runs already in --out, and keep its count.")
    parser.add_argument("--groups", default=",".join(SCENARIOS_BY_ID), help="Comma-separated group ids.")
    parser.add_argument("--max-runs", type=int, help="Stop after this many runs.")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=900.0, help="Seconds per writer turn.")
    parser.add_argument("--regrade", type=Path, help="Saved JSON to re-grade. No LLM.")
    args = parser.parse_args()
    args.out = args.out.resolve()
    args.groups = [group.strip() for group in args.groups.split(",") if group.strip()]
    unknown = sorted(set(args.groups) - set(SCENARIOS_BY_ID))
    if unknown or not args.groups:
        parser.error(f"--groups must name some of {list(SCENARIOS_BY_ID)}, not {unknown}")
    BUDGET.cap = RUNS_PER_GROUP * len(args.groups)

    if args.regrade:
        return regrade(args.regrade.resolve(), args.out)
    if not args.dry_run and not args.env_file:
        parser.error("--env-file is required for a live run")
    key_names = base.bootstrap_env(None if args.dry_run else args.env_file.resolve())
    os.environ.pop("RESPONDER_UNIFIED_FAMILIES", None)
    base.install_network_guard()
    return asyncio.run(evaluate(args, key_names))


if __name__ == "__main__":
    raise SystemExit(main())
