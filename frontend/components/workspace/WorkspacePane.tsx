"use client";

import { useMemo, useState } from "react";
import { X, Loader2, ArrowRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useChatStore } from "@/stores/chat-store";
import {
  useWorkspaceItem,
  useConversationWorkspace,
} from "@/hooks/use-workspace";
import { useConversationDetail } from "@/hooks/use-conversations";
import { useFilesEnabled, useLibraryFile } from "@/hooks/use-files";
import { isDemoConversation } from "@/hooks/use-demo-conversation";
import { FileViewer } from "@/components/files/FileViewer";
import { PaneFileList } from "@/components/files/PaneFileList";
import { useFilesUi } from "@/components/files/files-ui";
import { FILES_COPY } from "@/components/files/copy";
import {
  AttachmentRenderer,
  PartialReadNote,
  hasPartialReadNote,
  type AttachmentMetadata,
} from "./AttachmentRenderer";
import { NoteEditor } from "./NoteEditor";
import { AgentSearchViewer } from "./AgentSearchViewer";
import { ConvoContextViewer } from "./ConvoContextViewer";
import { ReferencesRenderer } from "./ReferencesRenderer";
import { WorkspaceList } from "./WorkspaceList";
import { WorkspaceAddMenu } from "./WorkspaceAddMenu";
import { WiBadge } from "./WiBadge";
import type { WorkspaceItem } from "@/types";

interface WorkspacePaneProps {
  conversationId: string;
}

/**
 * Two-mode workspace pane.
 *
 * - **List mode** (default, ``openItemId === null``): renders ``WorkspaceList``
 *   with one button per workspace item, grouped by kind. Clicking a button
 *   switches to detail mode.
 * - **Detail mode** (``openItemId`` set): renders the kind-specific viewer
 *   for the focused item. Header shows a back arrow that returns to list
 *   mode (``closeWorkspaceItem``); the X button still closes the entire
 *   pane (``closeWorkspace``).
 *
 * Renderer dispatch by ``item.kind``:
 * | kind            | renderer              |
 * | --------------- | --------------------- |
 * | attachment      | AttachmentRenderer    |
 * | note            | NoteEditor            |
 * | agent_writing   | NoteEditor            |
 * | agent_search    | AgentSearchViewer     |
 * | convo_context   | ConvoContextViewer    |
 * | references      | ReferencesRenderer    |
 *
 * «الملفات» (``useFilesEnabled()``, my_files.md §3.1): the pane is titled
 * «المخرجات» and list mode gets two tabs with counts — «العناصر» (every
 * non-attachment kind, grouped by ``WorkspaceList`` as before) and «الملفات»
 * (attachments as file cards, newest first). The tab follows the item the app
 * focuses: opening or highlighting an attachment shows «الملفات», any other
 * kind «العناصر». An attachment with extracted text opens in ``FileViewer``
 * (``GET /files/{id}``: summary + sections); without text — or if that request
 * fails — it falls back to ``AttachmentRenderer`` exactly as before. Flag off:
 * none of this renders.
 */
export function WorkspacePane({ conversationId }: WorkspacePaneProps) {
  const openItemId = useChatStore(
    (s) => s.workspaceByConversation[conversationId]?.openItemId ?? null,
  );
  const focusedReferenceN = useChatStore(
    (s) => s.workspaceByConversation[conversationId]?.focusedReferenceN ?? null,
  );
  const closeWorkspace = useChatStore((s) => s.closeWorkspace);
  const closeWorkspaceItem = useChatStore((s) => s.closeWorkspaceItem);
  const openWorkspaceItem = useChatStore((s) => s.openWorkspaceItem);
  const clearFocusedReference = useChatStore((s) => s.clearFocusedReference);

  // Always loaded so the list mode renders without an extra fetch on close.
  const { data: listData, isLoading: listLoading } =
    useConversationWorkspace(conversationId);
  const { data: item, isLoading: itemLoading, error: itemError } =
    useWorkspaceItem(openItemId ?? undefined);
  // Needed by WorkspaceAddMenu to enable the "link from case docs" option.
  const { data: convData } = useConversationDetail(conversationId);
  const caseId = convData?.conversation?.case_id ?? null;
  // The ONE shared demo conversation is read-only for everyone. Derived here
  // (off the row this pane already holds) and drilled down, so no viewer or
  // action bar ever learns the fixture id — they only learn "this is a demo".
  const isDemo = isDemoConversation(convData?.conversation);

  const inDetailMode = openItemId !== null;
  // On for THIS user only (build flag + GET /files/status); loading or a
  // failed status call keeps the pane exactly as it was.
  const filesOn = useFilesEnabled();
  const { showUpcoming } = useFilesUi();

  // --- «العناصر» / «الملفات» (flag on) -------------------------------------
  const highlightedItemId = useChatStore(
    (s) => s.workspaceByConversation[conversationId]?.highlightedItemId ?? null,
  );
  const { otherItems, fileItems } = useMemo(() => {
    const all = listData?.items;
    if (!all) return { otherItems: undefined, fileItems: undefined };
    return {
      otherItems: all.filter((i) => i.kind !== "attachment"),
      fileItems: all.filter((i) => i.kind === "attachment"),
    };
  }, [listData]);

  // The tab is per conversation (this pane is reused across conversations).
  const [tabState, setTabState] = useState<{ conversationId: string; tab: PaneTab }>(
    { conversationId, tab: "items" },
  );
  const tab: PaneTab = tabState.conversationId === conversationId ? tabState.tab : "items";
  const setTab = (next: PaneTab) => setTabState({ conversationId, tab: next });

  // Follow the item the app focuses (a chat chip opening or highlighting it):
  // an attachment → «الملفات», anything else → «العناصر». Applied when the
  // opened item or the highlight CHANGES to a new item (an open wins over a
  // highlight in the same update) — never when one clears, so coming back from
  // an opened file keeps «الملفات», and the user's own tab clicks are left
  // alone until the next focus. Done during render so the list never paints a
  // frame on the wrong tab.
  const kindOf = (id: string | null) =>
    id
      ? (listData?.items.find((i) => i.item_id === id)?.kind ??
        (item?.item_id === id ? item.kind : undefined))
      : undefined;
  const openKind = kindOf(openItemId);
  const highlightKind = kindOf(highlightedItemId);
  const openKey = openItemId && openKind ? `${conversationId}:${openItemId}` : null;
  const highlightKey =
    highlightedItemId && highlightKind ? `${conversationId}:${highlightedItemId}` : null;
  const [applied, setApplied] = useState<{ open: string | null; highlight: string | null }>({
    open: null,
    highlight: null,
  });
  if (filesOn && (openKey !== applied.open || highlightKey !== applied.highlight)) {
    const kind =
      openKey && openKey !== applied.open
        ? openKind
        : highlightKey && highlightKey !== applied.highlight
          ? highlightKind
          : undefined;
    setApplied({ open: openKey, highlight: highlightKey });
    if (kind) {
      const wanted: PaneTab = kind === "attachment" ? "files" : "items";
      if (wanted !== tab) setTabState({ conversationId, tab: wanted });
    }
  }

  return (
    <div className="flex h-full flex-col bg-background">
      <PaneHeader
        item={inDetailMode ? item ?? null : null}
        inDetailMode={inDetailMode}
        onBack={() => closeWorkspaceItem(conversationId)}
        onClose={() => closeWorkspace(conversationId)}
        title={filesOn ? FILES_COPY.paneTitle : "العناصر"}
        closeLabel={filesOn ? "إغلاق لوحة المخرجات" : "إغلاق لوحة العناصر"}
      />

      {filesOn && !inDetailMode ? (
        <Tabs
          value={tab}
          onValueChange={(next) => setTab(next === "files" ? "files" : "items")}
          className="flex min-h-0 flex-1 flex-col"
        >
          <div className="shrink-0 border-b px-2 py-1.5">
            <TabsList className="grid h-9 w-full grid-cols-2">
              <TabsTrigger value="items" className="h-7 gap-1.5">
                {FILES_COPY.paneItemsTab}
                <TabCount n={otherItems?.length} />
              </TabsTrigger>
              <TabsTrigger value="files" className="h-7 gap-1.5">
                {FILES_COPY.paneFilesTab}
                <TabCount n={fileItems?.length} />
              </TabsTrigger>
            </TabsList>
          </div>
          <div className="flex flex-1 flex-col min-h-0 overflow-y-auto">
            <TabsContent value="items" className="mt-0 data-[state=inactive]:hidden">
              <WorkspaceList
                items={otherItems}
                isLoading={listLoading}
                onItemClick={(itemId) => openWorkspaceItem(conversationId, itemId)}
              />
            </TabsContent>
            <TabsContent value="files" className="mt-0 data-[state=inactive]:hidden">
              <PaneFileList
                conversationId={conversationId}
                items={fileItems}
                isLoading={listLoading}
              />
            </TabsContent>
          </div>
          <WorkspaceAddMenu
            conversationId={conversationId}
            caseId={caseId}
            isDemo={isDemo}
            section={tab}
            showLibraryPick={showUpcoming}
          />
        </Tabs>
      ) : (
        <>
          <div className="flex flex-1 flex-col min-h-0 overflow-y-auto">
            {!inDetailMode ? (
              <WorkspaceList
                items={listData?.items}
                isLoading={listLoading}
                onItemClick={(itemId) => openWorkspaceItem(conversationId, itemId)}
              />
            ) : itemLoading ? (
              <LoadingState />
            ) : itemError || !item ? (
              <ErrorState />
            ) : (
              <KindRouter
                item={item}
                focusedReferenceN={focusedReferenceN}
                onFlashDone={() => clearFocusedReference(conversationId)}
                isDemo={isDemo}
                filesOn={filesOn}
              />
            )}
          </div>

          {/* Add-item menu lives at the bottom of the pane in list mode only.
              Detail mode has its own kind-specific viewer footer (e.g. autosave
              status) and shouldn't show the create-new dropdown. */}
          {!inDetailMode && (
            <WorkspaceAddMenu
              conversationId={conversationId}
              caseId={caseId}
              isDemo={isDemo}
            />
          )}
        </>
      )}
    </div>
  );
}

type PaneTab = "items" | "files";

function TabCount({ n }: { n: number | undefined }) {
  if (n === undefined) return null;
  return <span className="text-xs font-normal tabular-nums text-muted-foreground">{n}</span>;
}

function PaneHeader({
  item,
  inDetailMode,
  onBack,
  onClose,
  title,
  closeLabel,
}: {
  item: WorkspaceItem | null;
  inDetailMode: boolean;
  onBack: () => void;
  onClose: () => void;
  /** List-mode title: «العناصر», or «المخرجات» when the feature is on. */
  title: string;
  closeLabel: string;
}) {
  return (
    <div className="flex items-center gap-1 border-b px-2 py-2.5">
      {inDetailMode && (
        <Button
          variant="ghost"
          size="icon"
          // Tour anchor `pane-back` — both steps that used it are RETIRED; the
          // five-step script returns to the list itself, via the last step's
          // `onEnter`. Inert attribute.
          data-tour="pane-back"
          className="h-8 w-8 max-md:h-10 max-md:w-10 shrink-0"
          onClick={onBack}
          aria-label="رجوع إلى قائمة العناصر"
        >
          {/* RTL: ArrowRight points "back" toward the list (start side) */}
          <ArrowRight className="h-4 w-4" />
        </Button>
      )}
      <h2 className="flex-1 truncate px-1 text-sm font-semibold text-foreground">
        {inDetailMode ? item?.title ?? "..." : title}
      </h2>
      {/* Detail mode only: the alias the agents cite in chat, beside the title
          of the item the user just opened. Its dedicated tour step is RETIRED —
          the five-step script explains WI-1 in a line of the مخرج step — but the
          `wi-badge` anchor is still emitted. */}
      {inDetailMode && (
        <WiBadge seq={item?.wi_seq} className="me-1" dataTour="wi-badge" />
      )}
      <Button
        variant="ghost"
        size="icon"
        data-tour="pane-close"
        className="h-8 w-8 shrink-0"
        onClick={onClose}
        aria-label={closeLabel}
      >
        <X className="h-4 w-4" />
      </Button>
    </div>
  );
}

function LoadingState() {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
    </div>
  );
}

function ErrorState() {
  return (
    <div className="flex flex-1 items-center justify-center p-8">
      <p className="text-sm text-destructive">حدث خطأ في تحميل العنصر</p>
    </div>
  );
}

function KindRouter({
  item,
  focusedReferenceN,
  onFlashDone,
  isDemo,
  filesOn,
}: {
  item: WorkspaceItem;
  focusedReferenceN: number | null;
  onFlashDone: () => void;
  /** Read-only shared demo conversation — see AgentSearchViewer.isDemo. */
  isDemo: boolean;
  /** «الملفات» on: attachments with text open in ``FileViewer``. */
  filesOn: boolean;
}) {
  switch (item.kind) {
    case "attachment":
      return filesOn ? (
        <AttachmentFileDetail key={item.item_id} item={item} />
      ) : (
        <AttachmentRenderer item={item} />
      );
    case "note":
    case "agent_writing":
      // Keyed by item: switching straight from one cached note/draft to another
      // must REMOUNT the editor, never reuse A's local title/body/autosave
      // state under B's id (that PATCHed B with A's content).
      return <NoteEditor key={item.item_id} item={item} />;
    case "agent_search":
      return (
        <AgentSearchViewer
          item={item}
          focusedReferenceN={focusedReferenceN}
          onFlashDone={onFlashDone}
          isDemo={isDemo}
        />
      );
    case "convo_context":
      return <ConvoContextViewer item={item} />;
    case "references":
      return <ReferencesRenderer item={item} />;
    default:
      return (
        <div className="flex flex-1 items-center justify-center p-8 text-center">
          <p className="text-sm text-muted-foreground">
            نوع غير معروف: {(item as { kind: string }).kind}
          </p>
        </div>
      );
  }
}

/**
 * An attachment opened in the pane with «الملفات» on. A file whose text has
 * been extracted is shown by ``FileViewer`` from ``GET /files/{id}`` (summary,
 * sections). A file with no text yet (not sent, OCR empty or failed) has
 * nothing for that view to show and keeps ``AttachmentRenderer`` — its
 * original-file fallback and OCR status line — as does any file the endpoint
 * fails on, so the pane never loses an item it could show before.
 */
function AttachmentFileDetail({ item }: { item: WorkspaceItem }) {
  const hasText = (item.content_md ?? "").trim().length > 0;
  const { data, isLoading, isError } = useLibraryFile(hasText ? item.item_id : undefined);
  const meta = (item.metadata as AttachmentMetadata) || {};

  if (!hasText || isError) return <AttachmentRenderer item={item} />;
  if (isLoading || !data) return <LoadingState />;
  return (
    <FileViewer
      file={data}
      variant="pane"
      notice={hasPartialReadNote(meta) ? <PartialReadNote meta={meta} /> : undefined}
    />
  );
}
