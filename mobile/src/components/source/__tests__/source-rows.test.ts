/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import type { SourceView } from "@contracts";

import { chunkMarkdown } from "../chunk-markdown";
import { failureCopy, unlockedNotice } from "../copy";
import { libraryWebUrl } from "../exits-url";
import { extractSourceContent, sourceRows } from "../source-rows";

const opts = { hasFullTextExit: true, definiteType: "النظام" };

describe("chunkMarkdown", () => {
  test("short text is one block", () => {
    assert.deepEqual(chunkMarkdown("فقرة"), ["فقرة"]);
    assert.deepEqual(chunkMarkdown("   "), []);
  });

  test("cuts only on blank lines, never inside a fence or a table", () => {
    const para = Array(30).fill("نص").join(" ");
    const table = "| أ | ب |\n|---|---|\n| 1 | 2 |";
    const fence = "```\nسطر\n\nسطر بعد فراغ\n```";
    const md = [para, table, fence, para].join("\n\n");
    const blocks = chunkMarkdown(md, 20);
    assert.ok(blocks.length > 1);
    assert.ok(blocks.some((b) => b === table), "table kept whole");
    assert.ok(blocks.some((b) => b === fence), "fence kept whole");
    assert.equal(blocks.join("\n\n"), md);
  });

  test("a 168k body is split into bounded blocks", () => {
    const md = Array.from({ length: 2000 }, (_, i) => `فقرة ${i} ${"ن".repeat(80)}`).join("\n\n");
    const blocks = chunkMarkdown(md);
    assert.ok(blocks.length > 30);
    assert.ok(blocks.every((b) => b.length < 4200));
  });
});

describe("sourceRows", () => {
  test("chunk segments: tables via md (never html), https images only", () => {
    const view: SourceView = {
      source_type: "chunk",
      title: "t",
      content: "ignored",
      regulation_title: "",
      regulation_source_url: "",
      display_segments: [
        { kind: "text", text: "نص" },
        { kind: "table", ref: "t1", html: "<table><script>x</script></table>", md: "| a |\n|---|\n| 1 |" },
        { kind: "image", image_ref: "i", n: 1, title: "شكل", description: "d", url: "https://x/1.png", width: 4, height: 3, weight: 1, span_len: 1 },
        { kind: "image", image_ref: "j", n: 2, title: "", description: "", url: "images/rel.png", width: 1, height: 1, weight: 1, span_len: 1 },
      ],
    };
    const rows = sourceRows(view, opts);
    assert.deepEqual(rows.map((r) => r.kind), ["md", "md", "image"]);
    assert.ok(rows.every((r) => !("text" in r) || !r.text.includes("<table")));
  });

  test("case: ملخص label follows the exit, provenance kept", () => {
    const view: SourceView = {
      source_type: "case",
      title: "حكم",
      summary: "ملخص",
      content: "",
      details_url: "",
      citation: [{ label: "المجلد", value: "3" }],
    };
    assert.equal(sourceRows(view, opts)[0].kind, "label");
    const noExit = sourceRows(view, { ...opts, hasFullTextExit: false });
    assert.deepEqual(noExit[0], { kind: "label", key: "label", text: "ملخص الحكم" });
    assert.equal(noExit.at(-1)?.kind, "provenance");
    assert.equal(extractSourceContent(view), "ملخص");
  });

  test("regulation_summary uses the definite type; article_full names the مادة", () => {
    const sum: SourceView = { source_type: "regulation_summary", title: "x", content: "c", regulation_source_url: "" };
    assert.equal(sourceRows(sum, { hasFullTextExit: false, definiteType: "اللائحة" })[0].kind, "label");
    assert.deepEqual(sourceRows(sum, { hasFullTextExit: false, definiteType: "اللائحة" })[0], {
      kind: "label",
      key: "label",
      text: "ملخص اللائحة",
    });
    const art: SourceView = {
      source_type: "article_full",
      title: "x",
      article_num: "81 مكرر",
      content: "c",
      regulation_title: "نظام العمل",
      regulation_source_url: "",
    };
    assert.deepEqual(sourceRows(art, opts)[0], { kind: "label", key: "label", text: "المادة 81 مكرر — نظام العمل" });
  });

  test("gov_service: a note only when an exit exists", () => {
    const view: SourceView = { source_type: "gov_service", title: "خدمة", service_url: "" };
    assert.equal(sourceRows(view, opts).length, 1);
    assert.equal(sourceRows(view, { ...opts, hasFullTextExit: false }).length, 0);
  });
});

describe("reader copy", () => {
  test("refusals never carry a CTA link (no web pricing)", () => {
    for (const reason of ["quota_exhausted", "frozen_library", "anonymous", "locked", "unresolvable"] as const) {
      const { copy, action } = failureCopy({
        ok: false,
        kind: "refusal",
        refusal: { reason, message: "", used: 10, limit: 10, resets_at: null, stored_count: 3 },
      });
      assert.equal(copy.ctaHref, undefined);
      assert.equal(action, "dismiss");
    }
  });

  test("401 → login, 429 → retry, 404 → dismiss", () => {
    const err = (error: "unauthorized" | "rate_limited" | "not_found") => failureCopy({ ok: false, kind: "error", error, status: null });
    assert.equal(err("unauthorized").action, "login");
    assert.equal(err("unauthorized").copy.title, "انتهت جلستك");
    assert.equal(err("rate_limited").action, "retry");
    assert.equal(err("not_found").action, "dismiss");
  });

  test("unlocked notice names a whole نظام and keeps «81 مكرر» verbatim", () => {
    const base = { content_id: "x", charged: true, cost: 1, reason: "granted" as const };
    assert.match(unlockedNotice({ ...base, content_type: "regulation", title: "نظام العمل", article_no: null }), /بجميع مواده/);
    assert.match(unlockedNotice({ ...base, content_type: "article", title: "نظام العمل", article_no: "81 مكرر" }), /المادة 81 مكرر/);
    assert.match(unlockedNotice({ ...base, cost: 3, content_type: "regulation", title: "ن", article_no: null }), /احتُسب 3/);
  });
});

describe("libraryWebUrl", () => {
  test("site paths only — never guessed", () => {
    assert.equal(libraryWebUrl("/regulations/labor"), "https://rayhanai.com/regulations/labor");
    assert.equal(libraryWebUrl(null), null);
    assert.equal(libraryWebUrl(""), null);
    assert.equal(libraryWebUrl("//evil.com/x"), null);
    assert.equal(libraryWebUrl("javascript:alert(1)"), null);
    assert.equal(libraryWebUrl("https://evil.com/x"), null);
  });
});
