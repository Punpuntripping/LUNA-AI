import assert from "node:assert/strict";
import { test } from "node:test";

import {
  WI_PREVIEW_COPY,
  WI_PREVIEW_LEGACY_LINE,
  WI_PREVIEW_LEGACY_SUFFIX,
  isWiPreviewLocked,
  wiPreviewHiddenRefsLine,
  wiPreviewShownPct,
  wiPreviewVisibleContent,
} from "../wi-preview";

/**
 * WI preview lock — the shared client half (.claude/plans/wi_preview_lock.md).
 *
 * Run:  cd frontend && npx tsx --test lib/contracts/__tests__/*.test.ts
 */

const PREVIEW = { locked: true, shown_chars: 40, total_chars: 100, hidden_ref_count: 3 };

test("legacy suffix = blank line + the fixed line", () => {
  assert.equal(WI_PREVIEW_LEGACY_SUFFIX, `\n\n${WI_PREVIEW_LEGACY_LINE}`);
});

test("isWiPreviewLocked: only a locked preview block counts", () => {
  assert.equal(isWiPreviewLocked(null), false);
  assert.equal(isWiPreviewLocked({ content_md: "x" }), false);
  assert.equal(isWiPreviewLocked({ content_md: "x", preview: null }), false);
  assert.equal(isWiPreviewLocked({ content_md: "x", preview: { ...PREVIEW, locked: false } }), false);
  assert.equal(isWiPreviewLocked({ content_md: "x", preview: PREVIEW }), true);
});

test("wiPreviewVisibleContent strips the legacy line on a locked item only", () => {
  const body = "## عنوان\n\nنص مرئي [1].";
  assert.equal(
    wiPreviewVisibleContent({ content_md: body + WI_PREVIEW_LEGACY_SUFFIX, preview: PREVIEW }),
    body,
  );
  // An open item is never touched — even if it happens to end with the line.
  assert.equal(
    wiPreviewVisibleContent({ content_md: body + WI_PREVIEW_LEGACY_SUFFIX }),
    body + WI_PREVIEW_LEGACY_SUFFIX,
  );
  // Locked but no suffix (defensive) → unchanged.
  assert.equal(wiPreviewVisibleContent({ content_md: body, preview: PREVIEW }), body);
  assert.equal(wiPreviewVisibleContent({ content_md: null }), "");
});

test("wiPreviewShownPct", () => {
  assert.equal(wiPreviewShownPct(PREVIEW), 40);
  assert.equal(wiPreviewShownPct({ ...PREVIEW, shown_chars: 377, total_chars: 1000 }), 38);
  assert.equal(wiPreviewShownPct(null), 0);
  assert.equal(wiPreviewShownPct({ ...PREVIEW, total_chars: 0 }), 0);
});

test("wiPreviewHiddenRefsLine: Arabic agreement, Latin digits", () => {
  assert.equal(wiPreviewHiddenRefsLine(0), "");
  assert.equal(wiPreviewHiddenRefsLine(-2), "");
  assert.equal(wiPreviewHiddenRefsLine(1), "ومصدر آخر في الجزء المقفل");
  assert.equal(wiPreviewHiddenRefsLine(2), "ومصدران آخران في الجزء المقفل");
  assert.equal(wiPreviewHiddenRefsLine(3), "و3 مصادر أخرى في الجزء المقفل");
  assert.equal(wiPreviewHiddenRefsLine(10), "و10 مصادر أخرى في الجزء المقفل");
  assert.equal(wiPreviewHiddenRefsLine(11), "و11 مصدراً آخر في الجزء المقفل");
  for (const n of [0, 1, 2, 3, 7, 11, 25]) {
    assert.doesNotMatch(wiPreviewHiddenRefsLine(n), /[٠-٩۰-۹]/);
  }
});

test("wall copy: Arabic, no URL, no Arabic-Indic digits", () => {
  for (const s of Object.values(WI_PREVIEW_COPY)) {
    assert.ok(s.length > 0);
    assert.doesNotMatch(s, /https?:|[٠-٩۰-۹]/);
  }
});
