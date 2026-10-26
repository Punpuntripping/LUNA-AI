/// <reference types="node" />
import assert from "node:assert/strict";
import { describe, test } from "node:test";

import { unreadOldestFirst, type Announcement, type AnnouncementsResponse } from "@contracts";

import {
  announcementLinkUrl,
  normalizeAnnouncements,
  readBatches,
  unreadAccessibilityValue,
  unreadBadgeLabel,
  unreadIds,
  withRead,
} from "../logic";

function item(id: string, published_at: string, read = false, link: [string, string] | null = null): Announcement {
  return {
    announcement_id: id,
    title: `عنوان ${id}`,
    body_md: "نص **الرسالة**",
    link_label: link ? link[0] : null,
    link_url: link ? link[1] : null,
    published_at,
    read,
  };
}

const NEW = item("c", "2026-10-26T10:00:00Z");
const MID = item("b", "2026-10-20T10:00:00Z", true);
const OLD = item("a", "2026-10-01T10:00:00Z");
const LIST: AnnouncementsResponse = { items: [NEW, MID, OLD], unread_count: 2 };

describe("normalizeAnnouncements", () => {
  test("a valid body passes through", () => {
    assert.deepEqual(normalizeAnnouncements(LIST), LIST);
  });

  test("a body that is not {items: [...]} throws (the query fails quietly)", () => {
    assert.throws(() => normalizeAnnouncements(null));
    assert.throws(() => normalizeAnnouncements({ detail: "x" }));
    assert.throws(() => normalizeAnnouncements([]));
  });

  test("bad rows are dropped, a half link is removed, read is strict", () => {
    const out = normalizeAnnouncements({
      items: [
        { announcement_id: "x", title: "", body_md: "b", published_at: "2026-10-01T00:00:00Z" },
        { ...item("y", "2026-10-02T00:00:00Z"), link_label: "افتح", link_url: null, read: "true" },
      ],
      unread_count: 1,
    });
    assert.equal(out.items.length, 1);
    assert.equal(out.items[0].announcement_id, "y");
    assert.equal(out.items[0].link_label, null);
    assert.equal(out.items[0].read, false);
  });

  test("a missing or invalid unread_count is counted from the items", () => {
    assert.equal(normalizeAnnouncements({ items: [NEW, MID, OLD] }).unread_count, 2);
    assert.equal(normalizeAnnouncements({ items: [NEW], unread_count: -1 }).unread_count, 1);
  });
});

describe("read state", () => {
  test("popup queue: unread only, oldest first", () => {
    assert.deepEqual(
      unreadOldestFirst(LIST.items).map((a) => a.announcement_id),
      ["a", "c"],
    );
  });

  test("withRead flips the ids and lowers the count by what flipped", () => {
    const out = withRead(LIST, ["a", "b", "zzz"]);
    assert.equal(out.unread_count, 1);
    assert.deepEqual(unreadIds(out.items), ["c"]);
    assert.equal(LIST.items[2].read, false, "the input is not mutated");
  });

  test("withRead returns the same object when nothing changes", () => {
    assert.equal(withRead(LIST, []), LIST);
    assert.equal(withRead(LIST, new Set(["b"])), LIST);
  });

  test("the count never goes below zero", () => {
    assert.equal(withRead({ items: [OLD], unread_count: 0 }, ["a"]).unread_count, 0);
  });

  test("read batches are distinct and at most 50 ids", () => {
    const ids = Array.from({ length: 120 }, (_, i) => `id${i}`);
    const batches = readBatches([...ids, "id0"]);
    assert.deepEqual(
      batches.map((b) => b.length),
      [50, 50, 20],
    );
    assert.equal(new Set(batches.flat()).size, 120);
  });
});

describe("bell badge", () => {
  test("no badge at zero, Latin digits, 9+ above nine", () => {
    assert.equal(unreadBadgeLabel(0), null);
    assert.equal(unreadBadgeLabel(1), "1");
    assert.equal(unreadBadgeLabel(9), "9");
    assert.equal(unreadBadgeLabel(10), "9+");
    assert.equal(unreadBadgeLabel(Number.NaN), null);
  });

  test("screen readers hear the count only when there is one", () => {
    assert.equal(unreadAccessibilityValue(0), undefined);
    assert.equal(unreadAccessibilityValue(12), "غير المقروءة: 12");
  });
});

describe("link button target", () => {
  test("an in-site path opens on rayhanai.com", () => {
    assert.equal(announcementLinkUrl("/learn/usage-limits"), "https://rayhanai.com/learn/usage-limits");
  });

  test("an https URL is kept", () => {
    assert.equal(announcementLinkUrl("https://example.com/a?b=1"), "https://example.com/a?b=1");
  });

  test("anything else has no button", () => {
    assert.equal(announcementLinkUrl(null), null);
    assert.equal(announcementLinkUrl("  "), null);
    assert.equal(announcementLinkUrl("//evil.example"), null);
    assert.equal(announcementLinkUrl("http://example.com"), null);
    assert.equal(announcementLinkUrl("javascript:alert(1)"), null);
    assert.equal(announcementLinkUrl("https:///x"), null);
  });
});
