import type { FileSection, LibraryFileDetail } from "@/lib/contracts";

/**
 * A GENERATED stress file for the preview: the worst shape on record (318
 * sections, one of them ~32k characters, two documents in one file, metadata
 * rows, OCR image placeholders, a table). No real document text — every word
 * here is filler written for the test.
 */
export const STRESS_FILE_ID = "synthetic-318";

const SENTENCE =
  "هذا نص تجريبي مولَّد لاختبار سرعة العرض، ولا يمثّل أي مستند حقيقي. ";

function filler(chars: number): string {
  const paragraphs: string[] = [];
  let total = 0;
  let n = 0;
  while (total < chars) {
    n += 1;
    const p = `الفقرة ${n}: ${SENTENCE.repeat(6)}`;
    paragraphs.push(p);
    total += p.length + 2;
  }
  return paragraphs.join("\n\n").slice(0, chars);
}

const TABLE = [
  "| البند | القيمة | الملاحظة |",
  "| --- | --- | --- |",
  "| أول | 120 | تجريبي |",
  "| ثانٍ | 45 | تجريبي |",
].join("\n");

/**
 * Invented owners in the seg-v5.2 shape, cycled over the generated sections so
 * every tint, a «قدّمه: …» line, a class with no wording of its own, an unknown
 * owner and a pre-v5.2 row (no fields at all) are all on screen.
 */
const OWNERS: Array<Pick<FileSection, "owner_class" | "owner" | "brought_by">> = [
  { owner_class: "claimant", owner: "وكيل المدعي", brought_by: null },
  { owner_class: "respondent", owner: "المدعى عليه الثاني", brought_by: null },
  { owner_class: "court", owner: "الدائرة", brought_by: null },
  { owner_class: "witness", owner: "الشاهد الأول", brought_by: "المدعي" },
  { owner_class: "expert", owner: "الخبير", brought_by: "المدعى عليه" },
  { owner_class: "authority", owner: null, brought_by: null },
  { owner_class: "mixed", owner: "المدعي، الخبير", brought_by: null },
  { owner_class: null, owner: null, brought_by: null },
  {},
];

export function buildStressFile(): LibraryFileDetail {
  const sections: FileSection[] = Array.from({ length: 318 }, (_, i) => {
    const seq = i + 1;
    const page = Math.floor(i / 3) + 1;
    let content = filler(300 + ((i * 137) % 1400));
    if (i === 7) content = filler(31_886);
    if (i % 10 === 0) content = `${content}\n\n![img-${seq}.jpeg](img-${seq}.jpeg)\n\n${TABLE}`;
    return {
      seq,
      what: `قسم تجريبي ${seq}: وصف قصير لما يحتويه هذا الجزء من المستند المولَّد`,
      is_metadata: i % 40 === 0,
      page_from: page,
      page_to: i % 3 === 0 ? page + 1 : page,
      document_index: i < 160 ? 0 : 1,
      content_md: content,
      ...OWNERS[i % OWNERS.length],
    };
  });
  const content_md = sections.map((s) => s.content_md).join("\n\n");
  return {
    item_id: STRESS_FILE_ID,
    title: "ملف تجريبي مولَّد — 318 قسمًا (اختبار الأداء)",
    filename: "synthetic-318.pdf",
    mime_type: "application/pdf",
    doc_type: "contract",
    summary_preview: "ملف مولَّد لاختبار عرض الأقسام الطويلة.",
    page_count: 107,
    chars: content_md.length,
    has_sections: true,
    original_available: false,
    conversation_id: null,
    wi_seq: null,
    copies: 1,
    created_at: new Date().toISOString(),
    summary:
      "**ملخص المستند:**\n\nملف مولَّد لاختبار عرض **318 قسمًا** في وثيقتين، أحد أقسامه نحو 32 ألف حرف.\n\n- أقسام بيانات المستند كل 40 قسمًا.\n- عناصر صور من OCR في كل عاشر قسم (يجب ألا تظهر).",
    content_md,
    documents: [{ name: "الوثيقة الأولى (تجريبية)" }, { name: "الوثيقة الثانية (تجريبية)" }],
    sections,
  };
}
