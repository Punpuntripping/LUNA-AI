"use client";

import { useEffect, useState } from "react";
import { useTheme } from "next-themes";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { FlaskConical, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { getApiBase } from "@/lib/api";
import { useChatStore } from "@/stores/chat-store";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import {
  MERGED_ID,
  PREVIEW_CONVERSATION_ID,
  PREVIEW_ITEMS,
  PREVIEW_REFERENCES,
  SPARSE_ID,
} from "./fixture";

/**
 * DEV ONLY — the REAL `WorkspacePane` showing the «بحث موسّع» merged item, on an
 * invented fixture (./fixture.ts), with no backend: while this page is mounted
 * `window.fetch` answers the pane's three calls (the conversation's item list,
 * one item, one item's references) and 404s every other API path, so nothing
 * reaches a real server. A report card opens its child in the same pane.
 */

type Width = "narrow" | "wide" | "phone";

const WIDTHS: Record<Width, { label: string; cls: string }> = {
  narrow: { label: "لوحة ضيقة", cls: "w-[440px]" },
  wide: { label: "لوحة عريضة", cls: "w-[960px]" },
  phone: { label: "جوال", cls: "w-[375px]" },
};

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

function openItem(id: string): void {
  useChatStore.getState().openWorkspaceItem(PREVIEW_CONVERSATION_ID, id);
}

function uninstallFakeApi(): void {
  if (typeof window === "undefined" || !realFetch) return;
  window.fetch = realFetch;
  realFetch = null;
}

export function ExtendedSynthesisPreview() {
  // Wrap fetch BEFORE any child query mounts, and again after StrictMode's
  // mount/unmount/mount cycle.
  useState(() => {
    installFakeApi();
    return null;
  });
  useEffect(() => {
    installFakeApi();
    return () => uninstallFakeApi();
  }, []);

  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: { staleTime: Infinity, retry: false, refetchOnWindowFocus: false },
        },
      }),
  );
  const [width, setWidth] = useState<Width>("narrow");
  useEffect(() => {
    openItem(MERGED_ID);
  }, []);

  return (
    <QueryClientProvider client={client}>
      <div dir="rtl" className="min-h-dvh bg-background text-foreground">
        <header className="sticky top-0 z-30 border-b bg-background/95 px-4 py-2.5 backdrop-blur">
          <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 text-sm">
            <span className="inline-flex items-center gap-1.5 font-semibold">
              <FlaskConical className="h-4 w-4 text-primary" aria-hidden="true" />
              «بحث موسّع» في لوحة المخرجات — معاينة داخلية
            </span>
            <div className="flex items-center gap-1 rounded-lg border p-0.5" role="group" aria-label="العرض">
              {(Object.keys(WIDTHS) as Width[]).map((w) => (
                <button
                  key={w}
                  type="button"
                  aria-pressed={width === w}
                  onClick={() => setWidth(w)}
                  className={cn(
                    "rounded-md px-2.5 py-1 text-xs",
                    width === w ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent",
                  )}
                >
                  {WIDTHS[w].label}
                </button>
              ))}
            </div>
            <button type="button" onClick={() => openItem(MERGED_ID)} className="rounded-md border px-2 py-1 text-xs">
              الجواب المدمج
            </button>
            <button type="button" onClick={() => openItem(SPARSE_ID)} className="rounded-md border px-2 py-1 text-xs">
              بيانات ناقصة
            </button>
            <ThemeButton />
          </div>
          <p className="mx-auto mt-1 max-w-6xl text-xs text-muted-foreground">
            بيانات مختلقة للمعاينة فقط — لا خادم ولا بيانات عملاء.
          </p>
        </header>
        <main className="overflow-x-auto px-4 py-6">
          <aside
            className={cn(
              "mx-auto flex h-[calc(100dvh-9rem)] max-w-full flex-col overflow-hidden rounded-xl border bg-background shadow-sm",
              WIDTHS[width].cls,
            )}
          >
            <WorkspacePane conversationId={PREVIEW_CONVERSATION_ID} />
          </aside>
        </main>
      </div>
    </QueryClientProvider>
  );
}

function ThemeButton() {
  const { resolvedTheme, setTheme } = useTheme();
  // The theme is known only in the browser: render the light state until mounted.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const dark = mounted && resolvedTheme === "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(dark ? "light" : "dark")}
      className="ms-auto inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs"
    >
      {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
      {dark ? "فاتح" : "داكن"}
    </button>
  );
}
