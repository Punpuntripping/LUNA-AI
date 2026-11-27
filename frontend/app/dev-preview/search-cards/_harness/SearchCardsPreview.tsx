"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { Moon, Sun } from "lucide-react";
import { getApiBase } from "@/lib/api";
import { isExtendedSynthesis } from "@/lib/contracts/extended-synthesis";
import { useChatStore, type StreamState } from "@/stores/chat-store";
import { ChatLayoutClient } from "@/components/chat/ChatLayoutClient";
import { DeepSearchProgress } from "@/components/chat/DeepSearchProgress";
import { FilesProgress } from "@/components/chat/FilesProgress";
import {
  MERGED_ID,
  PREVIEW_CONVERSATION_ID,
  PREVIEW_ITEMS,
  PREVIEW_REFERENCES,
} from "../../extended-synthesis/_harness/fixture";

/**
 * DEV ONLY. The chat column shows one card per seeded stream (each card reads
 * its own fake conversation's stream); «افتح البحث الشامل» opens the fixture's
 * merged answer in the real pane, which should go full page by itself (chat
 * hidden, sidebar folded) — «تصغير» / Esc bring both back. While mounted,
 * `window.fetch` answers the pane's calls from the fixture and 404s every other
 * API path, so nothing reaches a real server.
 */

let realFetch: typeof window.fetch | null = null;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function answer(path: string, method: string): Response {
  const notFound = json({ detail: "غير متاح في المعاينة" }, 404);
  if (method !== "GET") return notFound;
  const clean = path.split("?")[0];
  if (clean === `/conversations/${PREVIEW_CONVERSATION_ID}/workspace`) {
    return json({ items: PREVIEW_ITEMS, total: PREVIEW_ITEMS.length });
  }
  const refs = /^\/workspace\/([^/]+)\/references$/.exec(clean);
  if (refs) return json({ references: PREVIEW_REFERENCES[refs[1]] ?? [] });
  const one = /^\/workspace\/([^/]+)$/.exec(clean);
  if (one) {
    const found = PREVIEW_ITEMS.find((i) => i.item_id === one[1]);
    return found ? json(found) : notFound;
  }
  return notFound;
}

function installFakeApi(): void {
  if (typeof window === "undefined" || realFetch) return;
  const original = window.fetch.bind(window);
  realFetch = original;
  const prefix = `${getApiBase()}/api/v1`;
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith(prefix)) return original(input, init);
    const method = (init?.method ?? (input instanceof Request ? input.method : "GET")).toUpperCase();
    return Promise.resolve(answer(url.slice(prefix.length), method));
  }) as typeof window.fetch;
}

function uninstallFakeApi(): void {
  if (typeof window === "undefined" || !realFetch) return;
  window.fetch = realFetch;
  realFetch = null;
}

// ── Seeded streams ─────────────────────────────────────────────────────────

function stream(patch: Partial<StreamState>): StreamState {
  return {
    sendId: 0,
    isStreaming: true,
    messageId: null,
    content: "",
    abortController: null,
    reconnectAttempts: 0,
    isReconnecting: false,
    deepSearchProgress: null,
    deepSearchSealable: null,
    isAgentRunning: true,
    runningAgentFamily: "deep_search",
    runningAgentSubtype: null,
    extractingDocuments: false,
    extractingStage: null,
    extractingStartedAt: null,
    extractingStageStarts: {},
    ...patch,
  };
}

const SECOND = 1000;

interface Seed {
  id: string;
  caption: string;
  kind: "files" | "search";
  build: (now: number) => StreamState;
}

const SEEDS: Seed[] = [
  {
    id: "card-files",
    caption: "تجهيز المستندات — الترتيب جارٍ (لا OCR)",
    kind: "files",
    build: (now) =>
      stream({
        runningAgentFamily: null,
        extractingDocuments: true,
        extractingStage: "arranging",
        extractingStartedAt: now - 41 * SECOND,
        extractingStageStarts: { summarizing: now - 41 * SECOND, arranging: now - 30 * SECOND },
      }),
  },
  {
    id: "card-deep",
    caption: "بحث عميق — التقييم",
    kind: "search",
    build: (now) =>
      stream({
        deepSearchProgress: {
          stage: "evaluating",
          text: null,
          sources: 24,
          queries: 9,
          topicsSeen: 0,
          searches: null,
          startedAt: now - 70 * SECOND,
          log: [],
        },
      }),
  },
  {
    id: "card-shamil-search",
    caption: "بحث شامل — البحوث جارية",
    kind: "search",
    build: (now) =>
      stream({
        deepSearchProgress: {
          stage: "searching",
          text: "البحث في الأنظمة والأحكام",
          sources: 42,
          queries: 0,
          topicsSeen: 0,
          searches: { total: 3, done: 1 },
          startedAt: now - 252 * SECOND,
          log: [],
        },
      }),
  },
  {
    id: "card-shamil-merge",
    caption: "بحث شامل — دمج المصادر",
    kind: "search",
    build: (now) =>
      stream({
        deepSearchProgress: {
          stage: "aggregating",
          text: "اكتملت البحوث — دمج النتائج في جواب واحد",
          sources: 118,
          queries: 0,
          topicsSeen: 0,
          searches: { total: 3, done: 3 },
          startedAt: now - 412 * SECOND,
          log: [],
        },
      }),
  },
  {
    id: "card-shamil-write",
    caption: "بحث شامل — عرض الإجابة",
    kind: "search",
    build: (now) =>
      stream({
        deepSearchProgress: {
          stage: "writing",
          text: "جارٍ عرض الإجابة",
          sources: 118,
          queries: 0,
          topicsSeen: 0,
          searches: { total: 3, done: 3 },
          startedAt: now - 590 * SECOND,
          log: [],
        },
      }),
  },
];

function seedStreams(): void {
  const now = Date.now();
  useChatStore.setState((s) => {
    const streams = { ...s.streams };
    for (const seed of SEEDS) streams[seed.id] = seed.build(now);
    return { streams };
  });
}

// ── Page ───────────────────────────────────────────────────────────────────

export function SearchCardsPreview() {
  useState(() => {
    installFakeApi();
    return null;
  });
  useEffect(() => {
    installFakeApi();
    seedStreams();
    return () => uninstallFakeApi();
  }, []);

  const { resolvedTheme, setTheme } = useTheme();
  const open = () =>
    useChatStore.getState().openWorkspaceItem(PREVIEW_CONVERSATION_ID, MERGED_ID);
  const report = () => {
    const other = PREVIEW_ITEMS.find((i) => !isExtendedSynthesis(i.metadata));
    if (other) useChatStore.getState().openWorkspaceItem(PREVIEW_CONVERSATION_ID, other.item_id);
  };

  return (
    <ChatLayoutClient>
      <div dir="rtl" className="flex h-full flex-col">
        <div className="flex flex-wrap items-center gap-2 border-b px-4 py-2 text-xs">
          <span className="font-semibold">معاينة داخلية — بطاقات البحث والصفحة الكاملة</span>
          <button type="button" onClick={open} className="rounded-md border px-2 py-1 hover:bg-muted">
            افتح البحث الشامل (صفحة كاملة)
          </button>
          <button type="button" onClick={report} className="rounded-md border px-2 py-1 hover:bg-muted">
            افتح تقريراً عادياً
          </button>
          <button type="button" onClick={seedStreams} className="rounded-md border px-2 py-1 hover:bg-muted">
            إعادة البطاقات
          </button>
          <button
            type="button"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            className="rounded-md border p-1 hover:bg-muted"
            aria-label="تبديل الوضع"
          >
            {resolvedTheme === "dark" ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          </button>
        </div>
        <div className="flex-1 space-y-6 overflow-y-auto px-4 py-4">
          {SEEDS.map((seed) => (
            <section key={seed.id} className="space-y-1.5">
              <p className="text-[11px] text-muted-foreground">{seed.caption}</p>
              {seed.kind === "files" ? (
                <FilesProgress conversationId={seed.id} />
              ) : (
                <DeepSearchProgress conversationId={seed.id} />
              )}
            </section>
          ))}
        </div>
      </div>
    </ChatLayoutClient>
  );
}
