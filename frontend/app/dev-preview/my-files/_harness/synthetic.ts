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
