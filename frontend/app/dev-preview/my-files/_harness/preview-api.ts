import { getApiBase } from "@/lib/api";
import type {
  LibraryFile,
  LibraryFileDetail,
  LibraryFileListResponse,
  WorkspaceItem,
  WorkspaceItemListResponse,
} from "@/lib/contracts";
import { STRESS_FILE_ID, buildStressFile } from "./synthetic";

/**
 * DEV ONLY — answers the app's own API calls from the fixture route, so the
 * REAL containers (`Sidebar`, `FilesPage`, `LibraryFilePage`, `ChatsPage`,
 * `WorkspacePane`) run unmodified with no backend.
 *
 * The sidebar's recent conversations (and /chats) get NEUTRAL placeholders
 * («محادثة تجريبية 1…») — never anything from the fixture.
 *
 * Mechanism: while the preview is mounted, `window.fetch` is wrapped. A request
 * whose URL starts with the API base (`NEXT_PUBLIC_API_URL` + `/api/v1`) is
 * answered here; everything else (Next's own requests, the fixture route)
 * passes through untouched. An API path this module does not know gets a 404
 * — nothing from the preview ever reaches a real backend.
 *
 * Fixtures are fetched from `/dev-preview/my-files/fixture/<name>` on every
 * call; nothing is cached or bundled.
 */

export type LibraryVariant = "full" | "first3" | "empty";

/** Set by the harness (during render) before the screen's queries run. */
export const previewState: {
  variant: LibraryVariant;
  hidden: Set<string>;
} = {
  variant: "full",
  hidden: new Set(),
};

const FIXTURE_BASE = "/dev-preview/my-files/fixture/";
const SAFE_ID = /^[a-z0-9_-]+$/;

let realFetch: typeof window.fetch | null = null;
let stressFile: LibraryFileDetail | null = null;

export async function loadFixture<T>(name: string): Promise<T | null> {
  const doFetch = realFetch ?? window.fetch.bind(window);
  const res = await doFetch(`${FIXTURE_BASE}${name}`, { cache: "no-store" });
  if (!res.ok) return null;
  return (await res.json()) as T;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function notFound(detail = "غير متاح في المعاينة"): Response {
  return json({ detail }, 404);
}

export function installPreviewApi(): void {
  if (typeof window === "undefined" || realFetch) return;
  const original = window.fetch.bind(window);
  realFetch = original;
  const prefix = `${getApiBase()}/api/v1`;
  window.fetch = ((input: RequestInfo | URL, init?: RequestInit) => {
    const url =
      typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    if (!url.startsWith(prefix)) return original(input, init);
    const method = (
      init?.method ?? (input instanceof Request ? input.method : "GET")
    ).toUpperCase();
    return route(url.slice(prefix.length), method);
  }) as typeof window.fetch;
}

export function uninstallPreviewApi(): void {
  if (typeof window === "undefined" || !realFetch) return;
  window.fetch = realFetch;
  realFetch = null;
}

function typeCounts(files: LibraryFile[]): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const f of files) {
    const key = f.doc_type ?? "untyped";
    counts[key] = (counts[key] ?? 0) + 1;
  }
  return counts;
}

async function listFiles(): Promise<Response> {
  const data = await loadFixture<LibraryFileListResponse>("files");
  if (!data) return notFound("لم يُعثر على ملف المعاينة files.json");
  let files = data.files.filter((f) => !previewState.hidden.has(f.item_id));
  if (previewState.variant === "first3") files = files.slice(0, 3);
  if (previewState.variant === "empty") files = [];
  return json({ files, total: files.length, type_counts: typeCounts(files) });
}

async function fileDetail(id: string): Promise<Response> {
  if (id === STRESS_FILE_ID) {
    stressFile ??= buildStressFile();
    return json(stressFile);
  }
  if (!SAFE_ID.test(id)) return notFound();
  const data = await loadFixture<LibraryFileDetail>(`file_${id}`);
  return data ? json(data) : notFound("الملف غير موجود");
}

async function paneItems(): Promise<WorkspaceItemListResponse | null> {
  return loadFixture<WorkspaceItemListResponse>("pane_workspace");
}

/** Neutral stand-ins for the sidebar list and /chats — no real data. */
function placeholderConversations(q: string | null) {
  const now = Date.now();
  const all = Array.from({ length: 6 }, (_, i) => {
    const at = new Date(now - i * 26 * 3_600_000).toISOString();
    return {
      conversation_id: `preview-conversation-${i + 1}`,
      case_id: null,
      title_ar: `محادثة تجريبية ${i + 1}`,
      message_count: 4 + i,
      is_active: true,
      created_at: at,
      updated_at: at,
      is_starred: false,
    };
  });
  const term = (q ?? "").trim();
  return term ? all.filter((c) => c.title_ar.includes(term)) : all;
}

async function route(path: string, method: string): Promise<Response> {
  const [pathname, search = ""] = path.split("?");
  const params = new URLSearchParams(search);
  const seg = pathname.split("/").filter(Boolean).map(decodeURIComponent);

  if (seg[0] === "files") {
    if (seg.length === 1 && method === "GET") return listFiles();
    if (seg.length === 2 && seg[1] === "match" && method === "POST") return json({ match: null });
    if (seg.length === 2 && method === "GET") return fileDetail(seg[1]);
    if (seg.length === 2 && method === "DELETE") {
      previewState.hidden.add(seg[1]);
      return new Response(null, { status: 204 });
    }
  }

  if (seg[0] === "conversations") {
    if (seg.length === 1 && method === "GET") {
      const conversations = params.get("starred") ? [] : placeholderConversations(params.get("q"));
      return json({ conversations, total: conversations.length, has_more: false });
    }
    if (seg.length === 2 && method === "GET") {
      const now = new Date().toISOString();
      return json({
        conversation: {
          conversation_id: seg[1],
          case_id: null,
          title_ar: "محادثة المعاينة",
          message_count: 0,
          is_active: true,
          created_at: now,
          updated_at: now,
          is_starred: false,
        },
      });
    }
    if (seg.length === 3 && seg[2] === "workspace" && method === "GET") {
      const data = await paneItems();
      return data ? json(data) : notFound("لم يُعثر على ملف المعاينة pane_workspace.json");
    }
  }

  if (seg[0] === "workspace" && seg.length >= 2) {
    if (seg.length === 2 && method === "GET") {
      const data = await paneItems();
      const item: WorkspaceItem | undefined = data?.items.find((i) => i.item_id === seg[1]);
      return item ? json(item) : notFound("العنصر غير موجود");
    }
    if (seg[2] === "references" && method === "GET") return json({ references: [] });
    if (seg[2] === "file") return notFound("الملف الأصلي غير متاح في المعاينة");
  }

  return notFound();
}
