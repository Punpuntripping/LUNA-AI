"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { cn } from "@/lib/utils";
import { clearTokens, getAccessToken, setTokens } from "@/lib/api";
import { useIsMobile } from "@/hooks/use-media-query";
import { useAuthStore } from "@/stores/auth-store";
import { useChatStore } from "@/stores/chat-store";
import { Sidebar } from "@/components/sidebar/Sidebar";
import { ChatInput } from "@/components/chat/ChatInput";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import type { WorkspaceItemListResponse } from "@/lib/contracts";
import {
  clearPreviewLog,
  getPreviewLog,
  previewState,
  subscribePreviewLog,
  type PreviewApiCall,
} from "./preview-api";

/** A 1×1 PNG — the "file" a simulated pick hands the real composer. */
const PNG_1PX =
  "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==";

function samplePng(name: string): File {
  const bytes = Uint8Array.from(atob(PNG_1PX), (c) => c.charCodeAt(0));
  return new File([bytes], name, { type: "image/png" });
}

/** Arabic label for one logged API call of the attach flow. */
function describe(call: PreviewApiCall): string | null {
  const p = call.path;
  if (p === "/files/status") return `حالة الميزة للمستخدم (${call.note})`;
  if (p === "/files/match") return "سؤال: هل الملف موجود؟ (files/match)";
  if (p.endsWith("/attachments/init")) return "بدأ رفع النسخة الجديدة (init)";
  if (p.endsWith("/attachments/upload")) return "رُفعت نسخة جديدة من اللوحة (upload)";
  if (p.endsWith("/finalize")) return call.note === "200" ? "اكتمل رفع النسخة الجديدة (finalize)" : `finalize رُفض (${call.note})`;
  if (p.endsWith("/cancel")) return "أُلغي رفع النسخة الجديدة (cancel)";
  if (call.method === "DELETE" && p.startsWith("/workspace/")) return "حُذفت النسخة الجديدة (DELETE)";
  return null;
}

/**
 * DEV ONLY — the REAL composer (`ChatInput`) and the REAL pane, wired to the
 * preview's stubbed API: a pick → `/files/match` (answers with one of this
 * conversation's own files) → the approval popup, while the upload runs
 * through the real resumable path against a dev-only tus route. Watch the
 * chip swap to the existing file, the new upload get cancelled or deleted,
 * and the pane never show the dropped copy.
 */
export function ComposerScreen({ items }: { items: WorkspaceItemListResponse["items"] }) {
  const conversationId = items[0]?.conversation_id ?? "preview-conversation";
  const isMobile = useIsMobile();
  const composerRef = useRef<HTMLDivElement>(null);
  const [lastSend, setLastSend] = useState<{ title: string; existing: boolean }[] | null>(null);
  const [, force] = useState(0);
  const log = useSyncExternalStore(subscribePreviewLog, getPreviewLog, getPreviewLog);
  const existingIds = new Set(items.map((i) => i.item_id));

  // The real upload path refuses to start without a session token. The
  // preview has no session, so — once AuthGuard's own probe has finished — it
  // holds a dummy in-memory token. Every call it could authorise is answered
  // by the preview (API stubs + the dev tus route).
  const authLoading = useAuthStore((s) => s.isLoading);
  useEffect(() => {
    if (authLoading) return;
    if (!getAccessToken()) setTokens({ access_token: "dev-preview-token", refresh_token: "" });
    return () => clearTokens();
  }, [authLoading]);

  // Open the pane, as the chat page does once there is something in it.
  useEffect(() => {
    const state = useChatStore.getState();
    if (!state.workspaceByConversation[conversationId]?.isOpen) {
      state.toggleWorkspace(conversationId);
    }
    useChatStore.getState().clearPendingFiles();
    clearPreviewLog();
  }, [conversationId]);

  const pick = (names: string[]) => {
    const input = composerRef.current?.querySelector<HTMLInputElement>("input[type='file']");
    if (!input) return;
    const transfer = new DataTransfer();
    for (const name of names) transfer.items.add(samplePng(name));
    input.files = transfer.files;
    input.dispatchEvent(new Event("change", { bubbles: true }));
  };

  const handleSend = () => {
    const store = useChatStore.getState();
    // The same rule use-chat.ts applies to build `attachment_ids`.
    const carried = store.pendingFiles
      .filter((f) => f.uploadStatus === "completed" && f.itemId)
      .map((f) => ({
        title: f.displayName ?? f.name,
        existing: existingIds.has(f.itemId as string),
      }));
    setLastSend(carried);
    store.clearPendingFiles();
  };

  const toggle = (key: "matchNext" | "slow") => {
    if (key === "matchNext") previewState.matchNext = !previewState.matchNext;
    else previewState.uploadDelayMs = previewState.uploadDelayMs > 0 ? 0 : 2500;
    force((n) => n + 1);
  };

  const flowLog = log.map((c) => ({ c, label: describe(c) })).filter((x) => x.label !== null);

  const chat = (
    <div className="flex h-full min-h-0 flex-col" dir="rtl">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        <div className="space-y-1">
          <p className="text-sm font-semibold">شريط الكتابة الحقيقي — إرفاق ملف موجود مسبقًا</p>
          <p className="text-xs leading-6 text-muted-foreground">
            اختر ملفًا (أو استخدم «+» ← مرفق). يُسأل الخادم المحاكى إن كان الملف موجودًا بينما يبدأ
            الرفع؛ تظهر النافذة، و«استخدام الموجود» يحوّل البطاقة إلى الملف الموجود ويلغي الرفع الجديد.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" onClick={() => pick(["صورة-تجريبية.png"])} data-testid="preview-pick-one">
            اختيار ملف تجريبي
          </Button>
          <Button
            size="sm"
            variant="outline"
            onClick={() => pick(["صورة-تجريبية-أ.png", "صورة-تجريبية-ب.png"])}
            data-testid="preview-pick-two"
          >
            اختيار ملفين معًا
          </Button>
          <Button size="sm" variant="ghost" onClick={() => toggle("matchNext")}>
            الملف موجود مسبقًا: {previewState.matchNext ? "نعم" : "لا"}
          </Button>
          <Button size="sm" variant="ghost" onClick={() => toggle("slow")} data-testid="preview-speed">
            سرعة الرفع: {previewState.uploadDelayMs > 0 ? "بطيئة" : "فورية"}
          </Button>
        </div>

        <section className="rounded-lg border bg-card p-3">
          <p className="mb-2 text-xs font-semibold text-muted-foreground">سجل الطلبات</p>
          {flowLog.length === 0 ? (
            <p className="text-xs text-muted-foreground">لا شيء بعد.</p>
          ) : (
            <ol className="space-y-1 text-xs" data-testid="preview-flow-log">
              {flowLog.map(({ c, label }, i) => (
                <li key={`${i}-${c.at}`} className="flex gap-2">
                  <span className="text-muted-foreground tabular-nums">
                    {new Date(c.at).toLocaleTimeString("en-GB")}
                  </span>
                  <span>{label}</span>
                </li>
              ))}
            </ol>
          )}
        </section>

        {lastSend && (
          <section className="rounded-lg border bg-card p-3" data-testid="preview-last-send">
            <p className="mb-2 text-xs font-semibold text-muted-foreground">
              آخر إرسال (معرّفات المرفقات التي كانت ستُرسل)
            </p>
            {lastSend.length === 0 ? (
              <p className="text-xs text-muted-foreground">لا مرفقات.</p>
            ) : (
              <ul className="space-y-1 text-xs">
                {lastSend.map((s, i) => (
                  <li key={i} className="flex items-center gap-2">
                    <span
                      className={cn(
                        "rounded-full px-2 py-0.5",
                        s.existing ? "bg-primary/10 text-primary" : "bg-muted text-muted-foreground",
                      )}
                    >
                      {s.existing ? "ملف موجود" : "نسخة جديدة"}
                    </span>
                    <span className="truncate">{s.title}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>
        )}
      </div>
      <div ref={composerRef}>
        <ChatInput onSend={handleSend} conversationId={conversationId} />
      </div>
    </div>
  );

  if (isMobile) return chat;

  return (
    <div className="flex h-full overflow-hidden bg-background">
      <Sidebar />
      <main className="relative flex min-w-0 flex-1 overflow-hidden">
        <ResizablePanelGroup direction="horizontal" className="flex-1">
          <ResizablePanel defaultSize={55} minSize={30} id="chat">
            {chat}
          </ResizablePanel>
          <ResizableHandle withHandle />
          <ResizablePanel defaultSize={45} minSize={25} id="workspace">
            <WorkspacePane conversationId={conversationId} />
          </ResizablePanel>
        </ResizablePanelGroup>
      </main>
    </div>
  );
}
