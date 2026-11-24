import assert from "node:assert/strict";
import { test } from "node:test";

import {
  angleClientKnown,
  angleDocuments,
  angleKindCounts,
  angleMapRows,
  angleMapSection,
  anglesOfSection,
  coerceAngleKind,
  coerceAngleStrength,
  isCrossDocumentAngle,
  parseAngleRef,
  groupAngleMapsByConversation,
  type AngleMap,
  type AngleMapSection,
} from "../angles";

function section(ref: string, side: AngleMapSection["side"] = "neutral"): AngleMapSection {
  const parsed = parseAngleRef(ref)!;
  return {
    ref,
    document: parsed.document,
    seq: parsed.seq,
    what: `what ${ref}`,
    value: "core",
    owner_class: null,
    owner: null,
    brought_by: null,
    page_from: null,
    page_to: null,
    side,
    content_md: `text ${ref}`,
  };
}

function makeMap(overrides: Partial<AngleMap> = {}): AngleMap {
  return {
    map_id: "m1",
    conversation_id: "c1",
    conversation_title: "c1",
    message_id: "msg1",
    created_at: "2026-11-20T10:00:00Z",
    prompt_version: "angles-v5",
    client: { side: "claimant", party: "المدعي", basis: "", confidence: "clear", by_document: [] },
    documents: [
      { index: 1, item_id: "i1", title: "D1", doc_type: null, wi_seq: 1 },
      { index: 2, item_id: "i2", title: "D2", doc_type: null, wi_seq: 2 },
      { index: 3, item_id: "i3", title: "D3", doc_type: null, wi_seq: 3 },
    ],
    left_out: [],
    sections: ["D1.S4", "D1.S9", "D2.S12", "D2.S3", "D3.S25"].map((r) => section(r)),
    angles: [
      {
        seq: 1,
        kind: "strengthen",
        strength: "strong",
        for_side: "claimant",
        for_party: "المدعي",
        title: "a1",
        argument: "",
        legal_question: "",
        weakness: "",
        ocr_suspect: false,
        // most important first: D3 first, then D2 in reverse document order
        sections: [
          { ref: "D3.S25", side: "neutral" },
          { ref: "D2.S12", side: "opp" },
          { ref: "D2.S3", side: "opp" },
          { ref: "D9.S1", side: null }, // unknown ref — skipped
        ],
      },
      {
        seq: 2,
        kind: "answer",
        strength: "medium",
        for_side: "claimant",
        for_party: "المدعي",
        title: "a2",
        argument: "",
        legal_question: "",
        weakness: "",
        ocr_suspect: true,
        sections: [
          { ref: "D1.S9", side: "own" },
          { ref: "D2.S12", side: "opp" },
        ],
      },
    ],
    gaps: [],
    ...overrides,
  };
}

test("parseAngleRef reads D·S refs in both spellings", () => {
  assert.deepEqual(parseAngleRef("D2.S7"), { document: 2, seq: 7 });
  assert.deepEqual(parseAngleRef("D10·S113"), { document: 10, seq: 113 });
  assert.equal(parseAngleRef("S7"), null);
  assert.equal(parseAngleRef(""), null);
});

test("angleDocuments / isCrossDocumentAngle", () => {
  const map = makeMap();
  assert.deepEqual(angleDocuments(map.angles[0]), [2, 3, 9]);
  assert.equal(isCrossDocumentAngle(map.angles[0]), true);
  assert.equal(isCrossDocumentAngle({ sections: [{ ref: "D1.S1", side: null }, { ref: "D1.S2", side: null }] }), false);
});

test("angleKindCounts and the coercers", () => {
  assert.deepEqual(angleKindCounts(makeMap().angles), { strengthen: 1, attack: 0, answer: 1 });
  assert.equal(coerceAngleKind("bogus"), "strengthen");
  assert.equal(coerceAngleStrength(undefined), "medium");
});

test("angleMapRows: a row per angle, a cell per document, sections in document order", () => {
  const rows = angleMapRows(makeMap());
  assert.deepEqual(rows.map((r) => r.angle.seq), [1, 2]);
  // Angle 1: D2 (S3 then S12, document order) and D3; the unknown D9 ref is skipped.
  assert.deepEqual(
    rows[0].cells.map((c) => [c.document, c.sections.map((s) => s.seq)]),
    [
      [1, []],
      [2, [3, 12]],
      [3, [25]],
    ],
  );
  assert.equal(rows[0].first, 1);
  assert.equal(rows[0].last, 2);
  // Angle 2: D1 and D2.
  assert.equal(rows[1].first, 0);
  assert.equal(rows[1].last, 1);
});

test("an angle with no known sections has empty cells and no line", () => {
  const map = makeMap();
  map.angles[0].sections = [{ ref: "D7.S1", side: null }];
  const row = angleMapRows(map)[0];
  assert.ok(row.cells.every((c) => c.sections.length === 0));
  assert.equal(row.first, null);
  assert.equal(row.last, null);
});

test("angleMapSection / anglesOfSection", () => {
  const map = makeMap();
  assert.equal(angleMapSection(map, "D2.S12")?.seq, 12);
  assert.equal(angleMapSection(map, "D2.S99"), null);
  assert.deepEqual(anglesOfSection(map, "D2.S12").map((a) => a.seq), [1, 2]);
});

test("groupAngleMapsByConversation keeps newest-first order", () => {
  const row = (id: string, conv: string | null) => ({ map_id: id, conversation_id: conv, conversation_title: conv });
  const groups = groupAngleMapsByConversation([row("m3", "b"), row("m2", "a"), row("m1", "b"), row("m0", null)]);
  assert.deepEqual(
    groups.map((g) => [g.conversation_id, g.maps.map((m) => m.map_id)]),
    [
      ["b", ["m3", "m1"]],
      ["a", ["m2"]],
      [null, ["m0"]],
    ],
  );
  assert.deepEqual(groupAngleMapsByConversation([]), []);
});

test("angleClientKnown", () => {
  assert.equal(angleClientKnown({ side: "claimant" }), true);
  assert.equal(angleClientKnown({ side: "unknown" }), false);
  assert.equal(angleClientKnown({ side: "" }), false);
});
