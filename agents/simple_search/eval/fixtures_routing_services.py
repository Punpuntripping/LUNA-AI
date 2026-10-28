"""Router fixtures for the government-services boundary (plan
``.claude/plans/simple_search_compliance_path.md`` §0 D8 + §9, added 2026-10-28).

WHY THIS SET EXISTS
-------------------
Before §9 the router prompt sent a turn to simple_search only when the user
NAMED a whole object (Test 1). A service question *describes a need* («من وين
أطلع العنوان الوطني لمؤسستي؟») and names nothing, so by the prompt's own rules
it was deep_search. Prod convo ``93fafee7`` went simple_search anyway, only by
reading the question as "one خدمة حكومية" — and the owner says that was the
right call. §9 makes it a rule. These fixtures pin both sides of it:

* ``svc-*``  → simple_search: a direct how / where / what-documents question
  whose answer is one or two government services.
* ``reg-*``  → deep_search: the same topics asked as LAW — is it mandatory,
  what is the penalty, what are my obligations / the legal effect.
* ``multi-*`` → deep_search: broad compliance that spans several services
  («وش أحتاج عشان أفتح…»).

The ``reg-*`` rows deliberately reuse the ``svc-*`` topics (national address,
CR, iqama, Ejar) so the only moving part is the QUESTION, not the subject —
the same pairing discipline as ``fixtures_routing``'s dangerous pair.

None of these strings is copied from the router prompt's own examples except
``svc-93fafee7``, which is the prod message verbatim (it is NOT in the prompt).

Run: ``agents/simple_search/eval/rerun_routing_services.py`` (standalone, no
DB setup) or the ``S-SVC`` / ``S-REG`` cells of ``ab_rerun.py``.
"""
from __future__ import annotations

from agents.simple_search.eval.fixtures_routing import RouteFixture

SERVICE_ROUTE_FIXTURES: list[RouteFixture] = [
    # ── simple_search: one or two government services answer it ─────────────
    RouteFixture(
        "svc-93fafee7", "service_direct", "simple_search",
        ("بطلع العنوان الوطني لمؤسستي من فين؟ وكيف اقدر اثبت ان العنوان الوطني للمؤسسة رقم (رقم السجل التجاري)",),
        why="Prod convo 93fafee7, verbatim. Two needs (issue + prove), each one "
            "service. The owner's reference case for D8.",
    ),
    RouteFixture(
        "svc-national-address", "service_direct", "simple_search",
        ("وين أسجّل العنوان الوطني لمنشأتي الجديدة؟",),
        why="Where to register — one service. Pairs with reg-mandatory (same "
            "topic, asked as law).",
    ),
    RouteFixture(
        "svc-cr-renew", "service_direct", "simple_search",
        ("وش أحتاج عشان أجدد السجل التجاري لمؤسستي؟",),
        why="CR renewal — one service. Pairs with reg-cr-penalty (same topic) AND "
            "with multi-cafe (same «وش أحتاج عشان…» opening): one service vs a "
            "licence set is the D8 line.",
    ),
    RouteFixture(
        "svc-muqeem-exit", "service_direct", "simple_search",
        ("كيف أطلع تأشيرة خروج وعودة لعامل عندي عن طريق مقيم؟",),
        why="Absher/Muqeem-style e-service — one service. Pairs with "
            "reg-iqama-obligation.",
    ),
    RouteFixture(
        "svc-name-reserve", "service_direct", "simple_search",
        ("أبغى أحجز اسم تجاري لمؤسستي، وش الخطوات؟",),
        why="Trade-name reservation — one service. Worded differently from the "
            "prompt's «وش المستندات المطلوبة لحجز اسم تجاري؟».",
    ),
    RouteFixture(
        "svc-ejar-docs", "service_direct", "simple_search",
        ("وش المستندات المطلوبة لتوثيق عقد إيجار تجاري في منصة إيجار؟",),
        why="Documents needed for one service. Pairs with reg-ejar-effect.",
    ),

    # ── deep_search: the law around the service ────────────────────────────
    RouteFixture(
        "reg-mandatory", "service_regulatory", "deep_search",
        ("هل تسجيل العنوان الوطني إلزامي على المنشآت؟",),
        why="Whether something is mandatory is in the أنظمة, not the service.",
    ),
    RouteFixture(
        "reg-cr-penalty", "service_regulatory", "deep_search",
        ("وش غرامة عدم تجديد السجل التجاري في وقته؟",),
        why="Penalty — regulatory, same topic as svc-cr-renew.",
    ),
    RouteFixture(
        "reg-iqama-obligation", "service_regulatory", "deep_search",
        ("وش التزامات صاحب العمل لو انتهت إقامة العامل وما جددها؟",),
        why="Employer obligations — regulatory, same topic as svc-muqeem-exit.",
    ),
    RouteFixture(
        "reg-ejar-effect", "service_regulatory", "deep_search",
        ("وش يترتب نظامًا لو ما وثقت عقد الإيجار في منصة إيجار؟",),
        why="Legal effect of not using the service — regulatory, same topic as "
            "svc-ejar-docs.",
    ),

    # ── deep_search: broad multi-service compliance ─────────────────────────
    RouteFixture(
        "multi-cafe", "service_multi", "deep_search",
        ("ابغى أفتح مقهى، وش كل التراخيص والإجراءات اللي أحتاجها من البداية؟",),
        why="A licence set from several entities, not one or two services.",
    ),
    RouteFixture(
        "multi-food-import", "service_multi", "deep_search",
        ("أبغى أبدأ نشاط استيراد مواد غذائية، وش التصاريح والتسجيلات اللي لازم أسويها؟",),
        why="Multi-entity compliance (CR, SFDA, customs) — not one or two services.",
    ),
]


def total_calls(reps: int = 1) -> int:
    return reps * sum(len(f.paraphrases) for f in SERVICE_ROUTE_FIXTURES)
