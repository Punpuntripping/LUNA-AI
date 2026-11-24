"use client";

import { useEffect, useMemo, useState, type MouseEvent, type ReactNode } from "react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { FlaskConical, Moon, Sun } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable";
import { useIsMobile } from "@/hooks/use-media-query";
import { useChatStore } from "@/stores/chat-store";
import { useSidebarStore } from "@/stores/sidebar-store";
import { MY_FILES_ENABLED } from "@/lib/features";
import { SidebarPageShell } from "@/components/shell/SidebarPageShell";
import { Sidebar } from "@/components/sidebar/Sidebar";
import {
  AppNavOverrideProvider,
  type AppNavOverride,
} from "@/components/shell/app-nav-override";
import { ChatsPage } from "@/components/chats/ChatsPage";
import { WorkspacePane } from "@/components/workspace/WorkspacePane";
import { FilesPage } from "@/components/files/FilesPage";
import { LibraryFilePage } from "@/components/files/LibraryFilePage";
import { FilesFeatureGate } from "@/components/files/FilesFeatureGate";
import { FileCard } from "@/components/files/FileCard";
import { FileDocCard } from "@/components/files/FileDocCard";
import {
  FileMatchDialog,
  type FileMatchDialogItem,
} from "@/components/files/FileMatchDialog";
import { FILES_UI_DEFAULTS, FilesUiProvider, type FilesUi } from "@/components/files/files-ui";
import {
  FILE_DOC_TYPES,
  FILE_MATCH_OPTIONS,
  FILE_TYPE_GROUP_LABELS,
  FILE_TYPE_LABELS,
  libraryFileCardModel,
  type FileCardModel,
  type FileDocType,
  type FileMatchDecision,
  type LibraryFile,
  type LibraryFileListResponse,
  type WorkspaceItemListResponse,
} from "@/lib/contracts";
import {
  installPreviewApi,
  loadFixture,
  previewState,
  uninstallPreviewApi,
  type LibraryVariant,
} from "./preview-api";
import { STRESS_FILE_ID } from "./synthetic";
import { ComposerScreen } from "./ComposerScreen";

/**
 * DEV ONLY — the «الملفات» preview (`/dev-preview/my-files`).
 *
 * Renders the REAL app: `SidebarPageShell` with the real `Sidebar`, `FilesPage`,
 * `LibraryFilePage`, `ChatsPage`, `WorkspacePane`, `FileMatchDialog`, the
 * cards — against one user's fixture, with no backend. `preview-api.ts`
 * answers their API calls from `MY_FILES_FIXTURE_DIR` while this page is
 * mounted (the sidebar's conversations are neutral placeholders).
 *
 * The seams, both never provided by the app itself:
 * - `FilesUiProvider` — card / back links stay inside the preview; the Phase-3
 *   controls show as inactive «قريبًا».
 * - `AppNavOverrideProvider` — the sidebar sees `/files` (or `/chats`) as the
 *   current path, its «الملفات» / «المحادثات» rows open preview screens and its
 *   other rows do nothing; on «مقارنة الشريط» it also draws the flag-off nav.
 *
 * Nothing about the files is written into this code.
 */

const BASE = "/dev-preview/my-files";

const SCREENS = [
  { id: "files", label: "الملفات" },
  { id: "files-3", label: "الملفات: 3" },
  { id: "files-empty", label: "الملفات: فارغة" },
  { id: "file-sections", label: "ملف بأقسام" },
  { id: "file-plain", label: "ملف بلا أقسام" },
  { id: "file-stress", label: "ملف مولَّد: 318 قسمًا" },
  { id: "chats", label: "المحادثات" },
  { id: "sidebar-compare", label: "مقارنة الشريط" },
  { id: "pane", label: "لوحة المحادثة" },
  { id: "composer", label: "إرفاق ملف موجود" },
  { id: "match", label: "نافذة التطابق" },
  { id: "states", label: "البطاقات والأنواع" },
] as const;

type ScreenId = (typeof SCREENS)[number]["id"] | "file";

const FILE_SCREENS: ReadonlySet<ScreenId> = new Set<ScreenId>([
  "file",
  "file-sections",
  "file-plain",
  "file-stress",
]);

function parseScreen(value: string | null): ScreenId {
  if (value === "file") return "file";
  return SCREENS.some((s) => s.id === value) ? (value as ScreenId) : "files";
}

function variantFor(screen: ScreenId): LibraryVariant {
  if (screen === "files-3") return "first3";
  if (screen === "files-empty") return "empty";
  return "full";
}

export function MyFilesPreview() {
  // Wrap fetch BEFORE any child query mounts (child effects run before a
  // parent's), and again after StrictMode's mount/unmount/mount cycle.
  useState(() => {
    installPreviewApi();
    return null;
  });
  useEffect(() => {
    installPreviewApi();
    return () => uninstallPreviewApi();
  }, []);

  // Client-only, like the real /files behind AuthGuard: the sidebar picks its
  // phone or desktop branch from the viewport on the first client render, so a
  // server render of this public dev route would not match it.
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);

  const router = useRouter();
  const searchParams = useSearchParams();
  const screen = parseScreen(searchParams.get("screen"));
  const fileId = searchParams.get("id");
  const upcoming = searchParams.get("upcoming") !== "0";
  // `?status=off`: GET /files/status answers { enabled: false } — the build
  // flag on, the feature off for this user.
  const statusOff = searchParams.get("status") === "off";

  const href = useMemo(() => {
    return (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) params.delete(key);
        else params.set(key, value);
      }
      const qs = params.toString();
      return `${BASE}${qs ? `?${qs}` : ""}`;
    };
  }, [searchParams]);

  const { data: index, isError: indexError } = useQuery({
    queryKey: ["dev-preview", "my-files", "index"],
    queryFn: async () => {
      const [files, pane] = await Promise.all([
        loadFixture<LibraryFileListResponse>("files"),
        loadFixture<WorkspaceItemListResponse>("pane_workspace"),
      ]);
      return { files: files?.files ?? [], pane: pane?.items ?? [] };
    },
    staleTime: 0,
  });

  const filesUi = useMemo<FilesUi>(
    () => ({
      fileHref: (itemId) => href({ screen: "file", id: itemId }),
      libraryHref: href({ screen: "files", id: null }),
      angleMapHref: FILES_UI_DEFAULTS.angleMapHref,
      showUpcoming: upcoming,
    }),
    [href, upcoming],
  );

  const resolvedFileId = (() => {
    if (screen === "file") return fileId;
    if (screen === "file-stress") return STRESS_FILE_ID;
    if (screen === "file-sections") {
      return index?.files.find((f) => f.has_sections)?.item_id ?? null;
    }
    if (screen === "file-plain") {
      const plain = index?.files.filter((f) => !f.has_sections) ?? [];
      return (plain.find((f) => f.original_available) ?? plain[0])?.item_id ?? null;
    }
    return null;
  })();

  // The sidebar inside the preview: «الملفات» / «المحادثات» open preview
  // screens, every other row is inert.
  const navigate = (target: string) => {
    if (target === "/files") router.push(href({ screen: "files", id: null }));
    else if (target === "/chats") router.push(href({ screen: "chats", id: null }));
  };
  const goChats = () => router.push(href({ screen: "chats", id: null }));
  const sidebarPath =
    screen === "chats"
      ? "/chats"
      : screen === "files" || screen === "files-3" || screen === "files-empty"
        ? "/files"
        : FILE_SCREENS.has(screen)
          ? `/files/${resolvedFileId ?? ""}`
          : "/chat/preview";

  /**
   * Clicks the override cannot reach — the sidebar's recent-conversation rows
   * and «عرض جميع المحادثات» push with their own router — are caught here so
   * the preview never leaves itself. On «المحادثات» the list rows are inert.
   */
  const handleClickCapture = (e: MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (target.closest("input")) return;
    const inSidebarList = Boolean(
      target.closest(".scrollbar-thin") && target.closest("aside, [role='dialog']"),
    );
    const inChatsList = screen === "chats" && Boolean(target.closest("main"));
    if (!inSidebarList && !inChatsList) return;
    e.preventDefault();
    e.stopPropagation();
    const button = target.closest("button");
    if (inSidebarList && button?.textContent?.includes("عرض جميع المحادثات")) {
      useSidebarStore.getState().setOpen(window.matchMedia("(min-width: 768px)").matches);
      goChats();
    }
  };

  return (
    <div className="flex h-dvh flex-col bg-background" dir="rtl">
      <div className="min-h-0 flex-1 overflow-hidden" onClickCapture={handleClickCapture}>
        {!mounted ? null : indexError ? (
          <Notice>
            تعذّر قراءة ملفات المعاينة. تأكّد أن MY_FILES_FIXTURE_DIR يشير إلى مجلد فيه files.json.
          </Notice>
        ) : (
          <FilesUiProvider value={filesUi}>
            <AppNavOverrideProvider value={{ pathname: sidebarPath ?? "/chat/preview", navigate }}>
              <ScreenHost
                key={`${screen}|${resolvedFileId ?? ""}|${statusOff ? "off" : "on"}`}
                variant={variantFor(screen)}
                statusOff={statusOff}
              >
                <ScreenView
                  screen={screen}
                  fileId={resolvedFileId}
                  files={index?.files}
                  pane={index?.pane}
                />
              </ScreenHost>
            </AppNavOverrideProvider>
          </FilesUiProvider>
        )}
      </div>
      <PreviewBar screen={screen} upcoming={upcoming} statusOff={statusOff} href={href} />
    </div>
  );
}

/**
 * A fresh query cache per screen, so «3» and «فارغة» never see the full list
 * cached by another screen. The fixture variant is set here, during the first
 * render, before any query below it runs.
 */
function ScreenHost({
  variant,
  statusOff,
  children,
}: {
  variant: LibraryVariant;
  statusOff: boolean;
  children: ReactNode;
}) {
  const [client] = useState(() => {
    previewState.variant = variant;
    previewState.statusOff = statusOff;
    return new QueryClient({
      defaultOptions: {
        queries: { staleTime: 30_000, retry: false, refetchOnWindowFocus: false },
      },
    });
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function ScreenView({
  screen,
  fileId,
  files,
  pane,
}: {
  screen: ScreenId;
  fileId: string | null;
  files: LibraryFile[] | undefined;
  pane: WorkspaceItemListResponse["items"] | undefined;
}) {
  switch (screen) {
    case "files":
    case "files-3":
    case "files-empty":
      return (
        <Shell>
          {/* As app/files/page.tsx renders it: behind the per-user gate. */}
          <FilesFeatureGate>
            <FilesPage />
          </FilesFeatureGate>
        </Shell>
      );
    case "file":
    case "file-sections":
    case "file-plain":
    case "file-stress":
      return (
        <Shell>
          {fileId ? (
            <FilesFeatureGate>
              <LibraryFilePage itemId={fileId} />
            </FilesFeatureGate>
          ) : (
            <Notice>لا يوجد في المعاينة ملف من هذا النوع.</Notice>
          )}
        </Shell>
      );
    case "chats":
      return (
        <Shell>
          <ChatsPage />
        </Shell>
      );
    case "sidebar-compare":
      return <SidebarCompare />;
    case "pane":
      if (!pane) return <Notice>جارٍ تحميل المعاينة…</Notice>;
      return <PaneScreen items={pane} />;
    case "composer":
      if (!pane) return <Notice>جارٍ تحميل المعاينة…</Notice>;
      return <ComposerScreen items={pane} />;
    case "match":
      return <MatchScreen files={files ?? []} />;
    case "states":
      return <CardsScreen files={files ?? []} />;
  }
}

/**
 * The real `SidebarPageShell` (Sidebar + main), as `/files` renders it. Its
 * root is `h-dvh`; here it is held to the area above the preview bar.
 */
function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="h-full [&>div]:!h-full">
      <SidebarPageShell>{children}</SidebarPageShell>
    </div>
  );
}

// ---------------------------------------------------------------- sidebar, off vs on

function SidebarCompare() {
  const isMobile = useIsMobile();
  useEffect(() => {
    if (!isMobile) useSidebarStore.getState().setOpen(true);
  }, [isMobile]);
  if (isMobile) return <Notice>المقارنة تحتاج شاشة أعرض من 768 بكسل.</Notice>;

  const off: AppNavOverride = { filesNav: false, pathname: "/chats", navigate: () => undefined };
  const on: AppNavOverride = { filesNav: true, pathname: "/files", navigate: () => undefined };
  return (
    <div className="flex h-full items-stretch justify-center gap-10 overflow-hidden bg-muted/40 p-6">
      {[
        { label: "الآن — الميزة مطفأة (كما هو اليوم)", value: off },
        { label: "بعد التشغيل — «الملفات» نشطة", value: on },
      ].map((col) => (
        <div key={col.label} className="flex min-h-0 flex-col gap-2">
          <p className="text-xs font-semibold text-muted-foreground">{col.label}</p>
          <div className="flex min-h-0 flex-1 overflow-hidden rounded-xl border bg-background shadow-sm">
            <AppNavOverrideProvider value={col.value}>
              <Sidebar />
            </AppNavOverrideProvider>
          </div>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- pane

function PaneScreen({ items }: { items: WorkspaceItemListResponse["items"] }) {
  const conversationId = items[0]?.conversation_id ?? "preview-conversation";
  const isMobile = useIsMobile();
  const isOpen = useChatStore((s) => s.workspaceByConversation[conversationId]?.isOpen ?? false);
  const splitRatio = useChatStore((s) => s.splitRatio);

  useEffect(() => {
    const state = useChatStore.getState();
    if (!state.workspaceByConversation[conversationId]?.isOpen) {
      state.toggleWorkspace(conversationId);
    }
  }, [conversationId]);

  const firstFile = items.find((i) => i.kind === "attachment");
  const firstOther = items.find((i) => i.kind !== "attachment");

  const chatArea = (
    <div className="flex h-full flex-col items-center justify-center gap-4 p-6 text-center">
      <p className="text-sm font-medium text-muted-foreground">منطقة المحادثة (عنصر نائب)</p>
      <p className="max-w-xs text-xs leading-6 text-muted-foreground/80">
        محاكاة ما تفعله رقائق المحادثة: الإبراز يفتح القائمة على التبويب الذي فيه العنصر.
      </p>
      <div className="flex flex-wrap justify-center gap-2">
        {!isOpen && (
          <Button size="sm" onClick={() => useChatStore.getState().toggleWorkspace(conversationId)}>
            فتح المخرجات
          </Button>
        )}
        {firstFile && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => useChatStore.getState().highlightWorkspaceItem(conversationId, firstFile.item_id)}
          >
            إبراز ملف
          </Button>
        )}
        {firstFile && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => useChatStore.getState().openWorkspaceItem(conversationId, firstFile.item_id)}
          >
            فتح ملف
          </Button>
        )}
        {firstOther && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => useChatStore.getState().highlightWorkspaceItem(conversationId, firstOther.item_id)}
          >
            إبراز عنصر آخر
          </Button>
        )}
      </div>
    </div>
  );

  // Phone: the app shows the pane as a full-screen layer over the chat.
  if (isMobile) {
    return isOpen ? <WorkspacePane conversationId={conversationId} /> : chatArea;
  }

  return (
    <div className="flex h-full overflow-hidden bg-background">
      <Sidebar />
      <main className="relative flex min-w-0 flex-1 overflow-hidden">
        {isOpen ? (
          <ResizablePanelGroup direction="horizontal" className="flex-1">
            <ResizablePanel defaultSize={splitRatio} minSize={25} id="chat">
              {chatArea}
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={100 - splitRatio} minSize={25} id="workspace">
              <WorkspacePane conversationId={conversationId} />
            </ResizablePanel>
          </ResizablePanelGroup>
        ) : (
          <div className="flex-1">{chatArea}</div>
        )}
      </main>
    </div>
  );
}

// ---------------------------------------------------------------- approval popup

function MatchScreen({ files }: { files: LibraryFile[] }) {
  const [shown, setShown] = useState<{ key: number; items: FileMatchDialogItem[] } | null>(null);
  const [open, setOpen] = useState(false);
  const [result, setResult] = useState<{
    items: FileMatchDialogItem[];
    decisions: Record<string, FileMatchDecision>;
  } | null>(null);

  const make = (count: number, firstSameConversation: boolean): FileMatchDialogItem[] =>
    files.slice(0, count).map((f, i) => ({
      key: `picked-${i + 1}`,
      match: {
        item_id: f.item_id,
        title: f.title,
        created_at: f.created_at,
        same_conversation: firstSameConversation && i === 0,
        wi_seq: f.wi_seq,
      },
    }));

  const show = (items: FileMatchDialogItem[]) => {
    setResult(null);
    setShown((prev) => ({ key: (prev?.key ?? 0) + 1, items }));
    setOpen(true);
  };

  const label = (d: FileMatchDecision) =>
    FILE_MATCH_OPTIONS.find((o) => o.value === d)?.label ?? d;

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-xl space-y-4 p-6">
        <p className="text-sm leading-7 text-muted-foreground">
          نافذة الموافقة عند اختيار ملف موجود لديك (المرحلة 1ب). لا تُستدعى من شريط الكتابة في هذه
          المرحلة؛ هنا تُفتح لملفات من المعاينة. الإغلاق يعني «رفع نسخة جديدة».
        </p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" disabled={files.length < 1} onClick={() => show(make(1, false))}>
            ملف واحد من محادثة أخرى
          </Button>
          <Button size="sm" variant="outline" disabled={files.length < 1} onClick={() => show(make(1, true))}>
            ملف واحد في هذه المحادثة
          </Button>
          <Button size="sm" variant="outline" disabled={files.length < 3} onClick={() => show(make(3, true))}>
            ثلاثة ملفات معًا
          </Button>
        </div>
        {result && (
          <div className="rounded-lg border bg-card p-4 text-sm">
            <p className="mb-2 font-medium">النتيجة</p>
            <ul className="space-y-1 text-muted-foreground">
              {result.items.map((item, i) => (
                <li key={item.key}>
                  الملف {i + 1}: {label(result.decisions[item.key])}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>
      {shown && (
        <FileMatchDialog
          key={shown.key}
          open={open}
          items={shown.items}
          onResolve={(decisions) => {
            setResult({ items: shown.items, decisions });
            setOpen(false);
          }}
        />
      )}
    </div>
  );
}

// ---------------------------------------------------------------- cards and types

const SAMPLE_SUMMARY =
  "نص تجريبي يمثّل السطور الأولى من ملخص المستند كما تظهر على البطاقة، ويُقطع بعد ثلاثة أسطر حتى تبقى البطاقات متساوية في الارتفاع مهما طال الملخص الأصلي.";

/** One generated tile per type (+ untyped, + the edge cases) — no fixture text. */
function sampleFile(i: number, docType: FileDocType | null, extra: Partial<LibraryFile> = {}): LibraryFile {
  const label = docType ? FILE_TYPE_LABELS[docType] : "بلا نوع";
  return {
    item_id: `sample-${i}`,
    title: `مثال لبطاقة من نوع «${label}»`,
    filename: `sample-${i}.pdf`,
    mime_type: "application/pdf",
    doc_type: docType,
    summary_preview: SAMPLE_SUMMARY,
    page_count: [1, 2, 3, 7, 11, 24, 103][i % 7],
    chars: 1000,
    has_sections: i % 3 === 0,
    original_available: true,
    conversation_id: null,
    wi_seq: null,
    copies: 1,
    created_at: new Date(Date.now() - i * 4 * 86_400_000).toISOString(),
    ...extra,
  };
}

function CardsScreen({ files }: { files: LibraryFile[] }) {
  const first = files[0];
  const base: FileCardModel | null = first ? libraryFileCardModel(first) : null;
  const paneStates: { label: string; model: FileCardModel }[] = base
    ? [
        { label: "جاهز", model: base },
        { label: "بلا ملخص", model: { ...base, summary_preview: "" } },
        { label: "لم يُرسل بعد", model: { ...base, status: "pending" } },
        { label: "قيد الاستخراج", model: { ...base, status: "processing" } },
        { label: "لا يحتوي على نص", model: { ...base, status: "empty" } },
        { label: "تعذّر الاستخراج", model: { ...base, status: "failed" } },
      ]
    : [];

  const catalog = [
    ...FILE_DOC_TYPES.map((t, i) => ({ caption: FILE_TYPE_GROUP_LABELS[t], file: sampleFile(i, t) })),
    { caption: "بلا نوع (صورة)", file: sampleFile(14, null, { mime_type: "image/jpeg" }) },
    {
      caption: "عنوان طويل جدًا وبلا ملخص",
      file: sampleFile(15, "judgment", {
        title:
          "حكم ابتدائي طويل العنوان جدًا لاختبار القطع على سطرين في البطاقة دون أن يدفع القائمة أو يتجاوز حدود البطاقة_اسم_ملف_طويل_بلا_مسافات_1234567890",
        summary_preview: "",
        page_count: null,
        has_sections: true,
      }),
    },
  ];

  return (
    <div className="h-full overflow-y-auto">
      <div className="mx-auto max-w-5xl space-y-10 p-6">
        <section>
          <h2 className="mb-1 text-sm font-semibold">بطاقة «الملفات» لكل نوع (14 نوعًا + بلا نوع)</h2>
          <p className="mb-4 text-xs text-muted-foreground">
            محتوى مولَّد للمقارنة فقط. التسمية أعلى كل بطاقة هي عنوان المجموعة.
          </p>
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {catalog.map(({ caption, file }) => (
              <div key={file.item_id} className="flex flex-col gap-1.5">
                <p className="text-xs text-muted-foreground">{caption}</p>
                <div className="flex-1">
                  <FileDocCard
                    file={file}
                    href={`${BASE}?screen=states`}
                    onRequestDelete={() => undefined}
                    showUpcoming
                  />
                </div>
              </div>
            ))}
          </div>
        </section>

        {paneStates.length > 0 && (
          <section>
            <h2 className="mb-3 text-sm font-semibold">بطاقة اللوحة («الملفات» في المخرجات) — الحالات</h2>
            <div className="grid max-w-3xl gap-3 sm:grid-cols-2">
              {paneStates.map((s) => (
                <div key={s.label}>
                  <p className="mb-1 text-xs text-muted-foreground">{s.label}</p>
                  <FileCard file={s.model} showWiBadge onOpen={() => undefined} />
                </div>
              ))}
            </div>
          </section>
        )}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- chrome

function PreviewBar({
  screen,
  upcoming,
  statusOff,
  href,
}: {
  screen: ScreenId;
  upcoming: boolean;
  statusOff: boolean;
  href: (patch: Record<string, string | null>) => string;
}) {
  const { resolvedTheme, setTheme } = useTheme();
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  const dark = mounted && resolvedTheme === "dark";

  return (
    <div className="shrink-0 border-t border-dashed border-primary/40 bg-background px-3 py-1.5">
      <div className="flex items-center gap-1.5 overflow-x-auto whitespace-nowrap">
        <span className="me-1 inline-flex shrink-0 items-center gap-1.5 text-xs font-semibold text-primary">
          <FlaskConical className="h-3.5 w-3.5" aria-hidden="true" />
          معاينة للتطوير
        </span>
        {SCREENS.map((s) => (
          <Link
            key={s.id}
            href={href({ screen: s.id, id: null })}
            className={cn(
              "shrink-0 rounded-full border px-2.5 py-1 text-xs transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              screen === s.id
                ? "border-primary/60 bg-primary/5 text-foreground"
                : "border-transparent text-muted-foreground hover:bg-accent/50 hover:text-foreground",
            )}
          >
            {s.label}
          </Link>
        ))}
        <span className="mx-1 h-4 w-px shrink-0 bg-border" aria-hidden="true" />
        <Button
          size="sm"
          variant="ghost"
          className="h-7 shrink-0 gap-1 px-2 text-xs"
          onClick={() => setTheme(dark ? "light" : "dark")}
        >
          {dark ? <Sun className="h-3.5 w-3.5" /> : <Moon className="h-3.5 w-3.5" />}
          {dark ? "فاتح" : "داكن"}
        </Button>
        <Link
          href={href({ upcoming: upcoming ? "0" : null })}
          className="shrink-0 rounded-md px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
        >
          {upcoming ? "إخفاء ميزات المرحلة 3" : "إظهار ميزات المرحلة 3"}
        </Link>
        <Link
          href={href({ status: statusOff ? null : "off" })}
          className={cn(
            "shrink-0 rounded-md px-2 py-1 text-xs hover:text-foreground",
            statusOff ? "text-destructive" : "text-muted-foreground",
          )}
          data-testid="preview-status-toggle"
        >
          {statusOff ? "الميزة لهذا المستخدم: مطفأة" : "الميزة لهذا المستخدم: تعمل"}
        </Link>
      </div>
      {!MY_FILES_ENABLED && (
        <p className="mt-1 text-xs text-destructive">
          NEXT_PUBLIC_MY_FILES_ENABLED ليس &quot;true&quot; في هذا الخادم: صفوف الشريط الجديدة وتبويبات اللوحة لن تظهر.
        </p>
      )}
    </div>
  );
}

function Notice({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-full items-center justify-center p-8 text-center text-sm text-muted-foreground">
      {children}
    </div>
  );
}
