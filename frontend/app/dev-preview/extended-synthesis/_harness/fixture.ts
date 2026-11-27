import type { Reference, WorkspaceItem } from "@/lib/contracts";

/**
 * DEV ONLY — an INVENTED «بحث شامل» conversation for the preview harness: one
 * merged answer (`extended_synthesis`), two child reports (`legal_synthesis`)
 * and a sparse/malformed merged item to check the lenient parsing. Every name,
 * sentence and number here is made up; nothing comes from a real file.
 */

export const PREVIEW_CONVERSATION_ID = "preview-extended-conv";
export const MERGED_ID = "preview-merged";
export const SPARSE_ID = "preview-merged-sparse";
const CHILD_1 = "preview-report-1";
const CHILD_2 = "preview-report-2";

const NOW = "2026-11-24T09:00:00+00:00";

function item(over: Partial<WorkspaceItem> & Pick<WorkspaceItem, "item_id" | "title">): WorkspaceItem {
  return {
    user_id: "preview-user",
    conversation_id: PREVIEW_CONVERSATION_ID,
    case_id: null,
    message_id: null,
    wi_seq: null,
    agent_family: "deep_search",
    kind: "agent_search",
    created_by: "agent",
    content_md: "",
    storage_path: null,
    document_id: null,
    is_visible: true,
    feedback: null,
    metadata: {},
    created_at: NOW,
    updated_at: NOW,
    ...over,
  };
}

function ref(n: number, over: Partial<Reference>): Reference {
  return {
    n,
    source_type: "chunk",
    domain: "regulations",
    relevance: "high",
    regulation_title: "نظام العقود (مثال)",
    title: "",
    snippet: "نص تجريبي للمعاينة فقط.",
    ref_id: `preview-ref-${n}`,
    landing_url: "",
    service_url: "",
    url: "",
    details_url: "",
    entity_name: "",
    cross_refs: [],
    source_view: null,
    has_source: false,
    library_url: null,
    ...over,
  };
}

const MERGED_BODY = `## الخلاصة

يثبت تأخر المورد في التسليم بالمراسلات المتبادلة، ويُحتسب التأخير من التاريخ المتفق عليه في العقد [1]. أما دفع المورد بالقوة القاهرة فله رد، لأن الظرف كان متوقعاً عند التعاقد [2].

### الشرط الجزائي

يجوز طلب تخفيض الشرط الجزائي إذا ثبت أن الضرر الفعلي أقل بكثير من المبلغ المتفق عليه [3]، ولم تتفق البحوث على معيار التخفيض، فعُرض الرأيان [1][4].

- القبول الجزئي دون تحفظ قد يُعدّ تنازلاً عن جزء من المطالبة [4].
- يلزم إثبات تاريخ استلام الإشعار قبل الاحتجاج به.`;

const MERGED_REFS: Reference[] = [
  ref(1, { title: "المادة 12 — الالتزام بالتسليم في الموعد", article_num: "12" }),
  ref(2, { title: "المادة 30 — الظروف الطارئة", article_num: "30" }),
  ref(3, { title: "المادة 41 — تخفيض الشرط الجزائي", article_num: "41" }),
  ref(4, {
    source_type: "case",
    domain: "cases",
    regulation_title: "",
    title: "حكم تجريبي في قبول التسليم الجزئي",
    entity_name: "محكمة تجريبية",
  }),
];

const MERGED_METADATA = {
  subtype: "extended_synthesis",
  confidence: "medium",
  detail_level: "medium",
  prompt_version: "merger-preview",
  model_used: "preview",
  client_side: "claimant",
  run_id: "preview-run",
  ref_count: 4,
  cited_count: 4,
  children: [CHILD_1, CHILD_2],
  reports: [
    { n: 1, title: "التأخر في التسليم وبداية احتسابه", mode: "full", angles: ["A1", "A3"], item_id: CHILD_1, confidence: "high", ok: true },
    { n: 2, title: "الشرط الجزائي وتخفيضه", mode: "reg_compliance_led", angles: ["A5"], item_id: CHILD_2, confidence: "medium", ok: true },
    { n: 3, title: "أحكام القبول الجزئي", mode: "case_led", angles: ["A4"], item_id: null, confidence: null, ok: false },
  ],
  angles: [
    { id: "A1", kind: "strengthen", strength: "strong", title: "تأخر المورد في التسليم ثابت بالمراسلات", for_party: "المدعي", for_side: "claimant", argument: "المراسلات تُظهر أن المورد أقر بالتأخر وطلب مهلة.", legal_question: "هل يكفي إقرار المورد في المراسلات لإثبات التأخر؟", weakness: "لم تُرفق جميع الرسائل.", sections: ["المستند 1 · القسم 3", "المستند 2 · القسم 1"], ocr_suspect: false },
    { id: "A2", kind: "attack", strength: "medium", title: "الإشعار بالفسخ لم يُرسل في المدة المتفق عليها", for_party: "المدعي", for_side: "unknown", argument: "تاريخ الإشعار المقروء يتجاوز المدة.", legal_question: "ما أثر تأخر الإشعار على حق الفسخ؟", weakness: "", sections: ["المستند 1 · القسم 5"], ocr_suspect: true },
    { id: "A3", kind: "answer", strength: "medium", title: "دفع المورد بالقوة القاهرة", for_party: "المدعى عليه", for_side: "respondent", argument: "يحتج المورد بانقطاع سلاسل الإمداد.", legal_question: "هل يُعد الظرف المذكور قوة قاهرة؟", weakness: "الظرف كان معلوماً قبل التعاقد.", sections: ["المستند 2 · القسم 4"], ocr_suspect: false },
    { id: "A4", kind: "strengthen", strength: "weak", title: "قبول التسليم الجزئي دون تحفظ", for_party: "المدعى عليه", for_side: "respondent", argument: "استلم المدعي جزءاً من البضاعة دون اعتراض مكتوب.", legal_question: "هل القبول دون تحفظ تنازل عن المطالبة؟", weakness: "", sections: [], ocr_suspect: false },
    { id: "A5", kind: "attack", strength: "strong", title: "الشرط الجزائي في حدود المعقول", for_party: "المدعي", for_side: "claimant", argument: "المبلغ يقارب الضرر المتوقع عند التعاقد.", legal_question: "متى يجوز للمحكمة تخفيض الشرط الجزائي؟", weakness: "لا يوجد حساب مفصل للضرر.", sections: ["المستند 1 · القسم 7"], ocr_suspect: false },
  ],
  angle_status: [
    { angle: "A1", status: "supported", note: "تدعمه أحكام الالتزام بالتسليم ومراسلات الطرفين." },
    { angle: "A5", status: "open", note: "يحسمه ما إذا كان الضرر الفعلي أقل كثيراً من مبلغ الشرط." },
    { angle: "A2", status: "not_researched", note: "لم يغطه أي بحث." },
    { angle: "A3", status: "answered", note: "للدفع رد: الظرف كان متوقعاً عند التعاقد." },
    { angle: "A4", status: "against", note: "يميل القضاء إلى اعتبار القبول دون تحفظ تنازلاً جزئياً." },
  ],
  resolved: [
    { topic: "بداية احتساب مدة التأخير", reports: [1, 2], decision: "من التاريخ المتفق عليه في العقد، لا من تاريخ الإشعار.", settled: true },
    { topic: "معيار تخفيض الشرط الجزائي", reports: [2, 3], decision: "رأى أحد البحوث أن العبرة بالضرر الفعلي، ورأى الآخر أن العبرة بالضرر المتوقع؛ عُرض الرأيان.", settled: false },
  ],
  left_out: [{ angle: "A6", title: "اختصاص المحكمة التجارية", why: "خارج سؤال المستخدم." }],
  gaps: ["لم يُعثر على حكم منشور في التسليم الجزئي المقبول بتحفظ شفهي.", "يلزم التحقق من تاريخ استلام الإشعار."],
  ref_reports: { "1": [1, 2], "2": [1], "3": [2], "4": [2] },
  stats: {},
};

/** Bad data on purpose: wrong types, unknown states, a missing angle, no reports. */
const SPARSE_METADATA = {
  subtype: "extended_synthesis",
  confidence: "certain",
  client_side: "",
  reports: "غير صالح",
  angles: [{ id: "A1", kind: "praise", title: "وجه بلا تفاصيل" }],
  angle_status: [
    { angle: "A1", status: "supported", note: "ملاحظة قصيرة." },
    { angle: "A9", status: "open" },
    { angle: "A2", status: "maybe", note: "حالة غير معروفة تُسقط" },
  ],
  resolved: [{ topic: "", decision: "" }],
  gaps: [7, "فجوة واحدة صالحة."],
};

export const PREVIEW_ITEMS: WorkspaceItem[] = [
  item({
    item_id: MERGED_ID,
    title: "التأخر في التسليم والشرط الجزائي — جواب مدمج",
    wi_seq: 4,
    content_md: MERGED_BODY,
    metadata: MERGED_METADATA,
  }),
  item({
    item_id: SPARSE_ID,
    title: "بحث شامل ببيانات ناقصة",
    wi_seq: 5,
    content_md: "جواب قصير للمعاينة [1].",
    metadata: SPARSE_METADATA,
  }),
  item({
    item_id: CHILD_1,
    title: "التأخر في التسليم وبداية احتسابه",
    wi_seq: 2,
    content_md: "## البحث 1\n\nيُحتسب التأخير من التاريخ المتفق عليه [1].",
    metadata: { subtype: "legal_synthesis", confidence: "high" },
  }),
  item({
    item_id: CHILD_2,
    title: "الشرط الجزائي وتخفيضه",
    wi_seq: 3,
    content_md: "## البحث 2\n\nيجوز تخفيض الشرط إذا كان مبالغاً فيه [1].",
    metadata: { subtype: "legal_synthesis", confidence: "medium" },
  }),
];

export const PREVIEW_REFERENCES: Record<string, Reference[]> = {
  [MERGED_ID]: MERGED_REFS,
  [SPARSE_ID]: [MERGED_REFS[0]],
  [CHILD_1]: [{ ...MERGED_REFS[0], n: 1 }],
  [CHILD_2]: [{ ...MERGED_REFS[2], n: 1 }],
};
