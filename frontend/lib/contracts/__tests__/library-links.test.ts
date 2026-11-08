import assert from "node:assert/strict";
import { test } from "node:test";

import { MAX_LIBRARY_LINKS, isLibraryPath, parseLibraryLinks } from "../library-links";

const LINK = { label: "اقرأ الحكم كاملًا", url: "/judgments/hukm-123", title: "حكم المحكمة العامة" };

test("a valid link survives with its title", () => {
  assert.deepEqual(parseLibraryLinks([LINK]), [LINK]);
});

test("absent or malformed metadata yields no links", () => {
  assert.deepEqual(parseLibraryLinks(undefined), []);
  assert.deepEqual(parseLibraryLinks("x"), []);
  assert.deepEqual(parseLibraryLinks([null, 3, [], { url: LINK.url }]), []);
});

test("only an in-app judgments path becomes a link", () => {
  for (const url of [
    "https://evil.example/judgments/x",
    "//evil.example/judgments/x",
    "javascript:alert(1)",
    "/regulations/x",
    "/judgments/",
    "/judgments/a/b",
    "/judgments/a b",
  ]) {
    assert.equal(isLibraryPath(url), false, url);
    assert.deepEqual(parseLibraryLinks([{ ...LINK, url }]), []);
  }
  assert.equal(isLibraryPath("/judgments/حكم-رقم-5"), true);
});

test("a missing title is null and a repeated url is kept once", () => {
  const out = parseLibraryLinks([{ label: LINK.label, url: LINK.url }, LINK]);
  assert.deepEqual(out, [{ label: LINK.label, url: LINK.url, title: null }]);
});

test("at most three links", () => {
  const many = Array.from({ length: 5 }, (_, i) => ({ ...LINK, url: `/judgments/h-${i}` }));
  assert.equal(parseLibraryLinks(many).length, MAX_LIBRARY_LINKS);
});
