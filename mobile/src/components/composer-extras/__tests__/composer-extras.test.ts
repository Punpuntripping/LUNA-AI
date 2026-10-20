/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import {
  coerceDetailLevel,
  detailLevelLabel,
  extractBlogToken,
  myBlogMenuLabel,
  planBlogQueue,
  templateChipLabel,
  templateDirectiveLine,
  templateMenuLabel,
  withTemplateDirective,
} from "@contracts";

import {
  blogChipLabel,
  buildExtrasSend,
  extrasBlockSend,
  extrasMakeSendable,
  type PendingBlogChip,
} from "../send-parts";

const TOKEN = "0123456789abcdef0123456789abcdef";
const T = { templateId: "t1", title: "  لائحة دعوى عمالية " };

function chip(over: Partial<PendingBlogChip> = {}): PendingBlogChip {
  return { id: "c1", token: TOKEN, title: "مدونة أ", status: "ready", itemId: "i1", createdByChip: true, errorMessage: null, ...over };
}

describe("template directive (web ChatInput.handleSend)", () => {
  test("line wraps the trimmed title in «»", () => {
    assert.equal(templateDirectiveLine(T.title), "استخدم القالب: «لائحة دعوى عمالية»");
  });
  test("text + blank line + directive; trimmed text", () => {
    assert.equal(withTemplateDirective("  اكتب لائحة \n", T), "اكتب لائحة\n\nاستخدم القالب: «لائحة دعوى عمالية»");
  });
  test("empty text → just the directive", () => {
    assert.equal(withTemplateDirective("   ", T), "استخدم القالب: «لائحة دعوى عمالية»");
  });
  test("no template → text untouched", () => {
    assert.equal(withTemplateDirective("  نص  ", null), "  نص  ");
  });
  test("labels fall back like the web", () => {
    assert.equal(templateMenuLabel("  "), "قالب بدون عنوان");
    assert.equal(templateChipLabel(""), "قالب");
  });
});

describe("blog links (web ComposerPlusMenu.extractBlogToken)", () => {
  test("full share URL, any case → lower-cased token", () => {
    assert.equal(extractBlogToken(`https://rayhanai.com/blog/${TOKEN.toUpperCase()}?x=1`), TOKEN);
  });
  test("bare token, surrounding spaces", () => {
    assert.equal(extractBlogToken(`  ${TOKEN} `), TOKEN);
  });
  test("33 hex chars or junk → null", () => {
    assert.equal(extractBlogToken(`https://rayhanai.com/blog/${TOKEN}a`), null);
    assert.equal(extractBlogToken("https://rayhanai.com/blog/abc"), null);
    assert.equal(extractBlogToken("مرحبا"), null);
  });
  test("my-blog label: title, else snippet, else «مدونة»", () => {
    assert.equal(myBlogMenuLabel({ title: " عنوان ", snippet: "x" }), "عنوان");
    assert.equal(myBlogMenuLabel({ title: null, snippet: " مقتطف " }), "مقتطف");
    assert.equal(myBlogMenuLabel({ title: "", snippet: "" }), "مدونة");
  });
});

describe("planBlogQueue (web queueBlogTokens)", () => {
  test("dedups against held and within the batch", () => {
    assert.deepEqual(planBlogQueue(["a"], ["a", "b", "b"]), { ok: true, tokens: ["b"] });
  });
  test("nothing new → silent no-op", () => {
    assert.deepEqual(planBlogQueue(["a"], ["a"]), { ok: false, error: null });
  });
  test("cap of 3 — Latin digits in the error", () => {
    assert.deepEqual(planBlogQueue(["a", "b", "c"], ["d"]), { ok: false, error: "الحد الأقصى 3 مدونات في الرسالة" });
    assert.deepEqual(planBlogQueue(["a", "b"], ["c", "d"]), { ok: true, tokens: ["c"] });
  });
});

describe("detail level", () => {
  test("unknown → medium; labels", () => {
    assert.equal(coerceDetailLevel("high"), "high");
    assert.equal(coerceDetailLevel("extreme"), "medium");
    assert.equal(coerceDetailLevel(undefined), "medium");
    assert.equal(detailLevelLabel("low"), "مختصر");
    assert.equal(detailLevelLabel("high"), "مفصّل");
  });
});

describe("buildExtrasSend", () => {
  test("ready blogs ride attachment_ids; failed/loading do not; all chips are released", () => {
    const blogs = [chip(), chip({ id: "c2", status: "failed", itemId: null }), chip({ id: "c3", status: "loading", itemId: null })];
    const res = buildExtrasSend("سؤال", null, blogs, 10_000, "10,000");
    assert.ok(res.ok);
    assert.equal(res.parts.content, "سؤال");
    assert.deepEqual(res.parts.attachmentIds, ["i1"]);
    assert.deepEqual(res.parts.optimisticAttachments, [
      { id: "c1", document_id: "i1", attachment_type: "file", filename: "مدونة أ", file_size: 0, kind: "agent_search" },
    ]);
    assert.deepEqual(res.parts.sentBlogChipIds, ["c1", "c2", "c3"]);
  });
  test("directive pushing past the limit is refused in Arabic", () => {
    const res = buildExtrasSend("x".repeat(20), T, [], 25, "25");
    assert.deepEqual(res, { ok: false, error: "الحد الأقصى 25 حرف" });
  });
  test("send gating", () => {
    assert.equal(extrasMakeSendable(null, []), false);
    assert.equal(extrasMakeSendable(T, []), true);
    assert.equal(extrasMakeSendable(null, [chip({ status: "failed", itemId: null })]), false);
    assert.equal(extrasMakeSendable(null, [chip()]), true);
    assert.equal(extrasBlockSend([chip({ status: "loading", itemId: null })]), true);
    assert.equal(extrasBlockSend([chip()]), false);
  });
  test("chip labels", () => {
    assert.equal(blogChipLabel(chip({ status: "failed", errorMessage: null })), "رابط مدونة غير صالح");
    assert.equal(blogChipLabel(chip({ title: "  " })), "مدونة");
  });
});
