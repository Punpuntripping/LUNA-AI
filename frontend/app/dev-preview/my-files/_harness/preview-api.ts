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
 *
 * The composer screen's uploads are stubbed here too (`/init` → the dev-only
 * tus route, `/finalize`, `/cancel`, `DELETE /workspace/{id}`, `/files/match`),
 * and every answered call is written to a small log the screen shows — and the
 * headless checks read (`window.__previewApiLog`).
 */

export type LibraryVariant = "full" | "first3" | "empty";

/** Set by the harness (during render) before the screen's queries run. */
export const previewState: {
  variant: LibraryVariant;
  hidden: Set<string>;
  /** `GET /files/status` answers `{ enabled: false }` (the per-user switch off). */
  statusOff: boolean;
  /** `POST /files/match` finds the picked file among the conversation's files. */
  matchNext: boolean;
  /** Milliseconds per tus chunk, so «uploading» can be watched. */
  uploadDelayMs: number;
} = {
  variant: "full",
  hidden: new Set(),
  statusOff: false,
  matchNext: true,
  uploadDelayMs: 2500,
};

// ---------------------------------------------------------------- call log

export interface PreviewApiCall {
  at: number;
  method: string;
  path: string;
  note: string;
}

let callLog: PreviewApiCall[] = [];
const logListeners = new Set<() => void>();

function logCall(method: string, path: string, note = ""): void {
  callLog = [...callLog, { at: Date.now(), method, path: path.split("?")[0], note }];
  if (typeof window !== "undefined") {
    (window as unknown as { __previewApiLog: PreviewApiCall[] }).__previewApiLog = callLog;
  }
  logListeners.forEach((listener) => listener());
}

export function subscribePreviewLog(listener: () => void): () => void {
  logListeners.add(listener);
  return () => logListeners.delete(listener);
}

export function getPreviewLog(): PreviewApiCall[] {
  return callLog;
}

export function clearPreviewLog(): void {
  callLog = [];
  if (typeof window !== "undefined") {
    (window as unknown as { __previewApiLog: PreviewApiCall[] }).__previewApiLog = callLog;
  }
  logListeners.forEach((listener) => listener());
}

// ---------------------------------------------------------------- composer uploads

interface StubUpload {
  itemId: string;
  conversationId: string;
  filename: string;
  mimeType: string;
  size: number;
  state: "uploading" | "ready" | "cancelled" | "deleted";
  createdAt: string;
}

const stubUploads = new Map<string, StubUpload>();
let uploadSeq = 0;
let matchCursor = 0;

function stubItem(u: StubUpload): WorkspaceItem {
  return {
    item_id: u.itemId,
    user_id: "preview-user",
    conversation_id: u.conversationId,
    case_id: null,
    wi_seq: null,
    agent_family: null,
    kind: "attachment",
    created_by: "user",
    title: u.filename,
    content_md: "",
    storage_path: `preview/${u.itemId}`,
    document_id: null,
    is_visible: true,
    feedback: null,
    metadata: {
      filename: u.filename,
      mime_type: u.mimeType,
      file_size_bytes: u.size,
      upload_status: "ready",
    },
    created_at: u.createdAt,
    updated_at: u.createdAt,
    summary_preview: null,
  };
}

async function readJson(init: RequestInit | undefined): Promise<Record<string, unknown>> {
  try {
    return typeof init?.body === "string" ? (JSON.parse(init.body) as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

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
    const path = url.slice(prefix.length);
    return route(path, method, init).then((res) => {
      logCall(method, path, String(res.status));
      return res;
    });
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

/**
 * `POST /files/match`: with «الملف التالي موجود مسبقًا» on, the picked file is
 * "found" among this conversation's own read files (Phase 1b answers only
 * same-conversation copies), cycling so several picks name different files.
 * Answers after a short wait, like the real call beside a starting upload.
 */
async function matchFile(init?: RequestInit): Promise<Response> {
  const body = await readJson(init);
  await new Promise((resolve) => setTimeout(resolve, 400));
  if (!previewState.matchNext || !body.conversation_id) return json({ match: null });
  const data = await paneItems();
  const candidates = (data?.items ?? []).filter(
    (i) => i.kind === "attachment" && (i.content_md ?? "").trim().length > 0,
  );
  if (candidates.length === 0) return json({ match: null });
  const hit = candidates[matchCursor % candidates.length];
  matchCursor += 1;
  return json({
    match: {
      item_id: hit.item_id,
      title: hit.title,
      created_at: hit.created_at,
      same_conversation: true,
      wi_seq: hit.wi_seq ?? null,
    },
  });
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

async function route(path: string, method: string, init?: RequestInit): Promise<Response> {
  const [pathname, search = ""] = path.split("?");
  const params = new URLSearchParams(search);
  const seg = pathname.split("/").filter(Boolean).map(decodeURIComponent);

  if (seg[0] === "files") {
    if (seg.length === 2 && seg[1] === "status" && method === "GET") {
      return json({ enabled: !previewState.statusOff });
    }
    if (seg.length === 1 && method === "GET") return listFiles();
    if (seg.length === 2 && seg[1] === "match" && method === "POST") return matchFile(init);
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
      if (!data) return notFound("لم يُعثر على ملف المعاينة pane_workspace.json");
      // The composer screen's finished uploads join the pane, newest first;
      // a cancelled or deleted one never shows.
      const live = [...stubUploads.values()]
        .filter((u) => u.conversationId === seg[1] && u.state === "ready")
        .map(stubItem)
        .reverse();
      const items = [...live, ...data.items];
      return json({ items, total: items.length });
    }
    if (
      seg.length === 5 &&
      seg[2] === "workspace" &&
      seg[3] === "attachments" &&
      seg[4] === "upload" &&
      method === "POST"
    ) {
      // The pane's «رفع ملف»: one multipart request, answered after a while.
      const form = init?.body instanceof FormData ? init.body : null;
      const file = form?.get("file");
      uploadSeq += 1;
      const upload: StubUpload = {
        itemId: `preview-upload-${uploadSeq}`,
        conversationId: seg[1],
        filename: file instanceof File ? file.name : "ملف",
        mimeType: file instanceof File ? file.type : "application/octet-stream",
        size: file instanceof File ? file.size : 0,
        state: "ready",
        createdAt: new Date().toISOString(),
      };
      await new Promise((resolve) => setTimeout(resolve, previewState.uploadDelayMs));
      stubUploads.set(upload.itemId, upload);
      return json(stubItem(upload));
    }
    if (
      seg.length === 5 &&
      seg[2] === "workspace" &&
      seg[3] === "attachments" &&
      seg[4] === "init" &&
      method === "POST"
    ) {
      const body = await readJson(init);
      uploadSeq += 1;
      const itemId = `preview-upload-${uploadSeq}`;
      stubUploads.set(itemId, {
        itemId,
        conversationId: seg[1],
        filename: String(body.filename ?? "ملف"),
        mimeType: String(body.mime_type ?? "application/octet-stream"),
        size: Number(body.size_bytes ?? 0),
        state: "uploading",
        createdAt: new Date().toISOString(),
      });
      return json({
        item_id: itemId,
        storage_path: `preview/${itemId}`,
        bucket: "documents",
        upload_url: `/dev-preview/my-files/tus?delay=${previewState.uploadDelayMs}`,
        expires_at: new Date(Date.now() + 3_600_000).toISOString(),
      });
    }
  }

  if (seg[0] === "workspace" && seg[1] === "attachments" && seg.length === 4 && method === "POST") {
    const upload = stubUploads.get(seg[2]);
    if (!upload) return notFound("الرفع غير موجود");
    if (seg[3] === "cancel") {
      if (upload.state === "uploading" || upload.state === "ready") upload.state = "cancelled";
      return json({ success: true });
    }
    if (seg[3] === "finalize") {
      if (upload.state !== "uploading") return json({ detail: "أُلغي هذا الرفع" }, 409);
      upload.state = "ready";
      return json(stubItem(upload));
    }
  }

  if (seg[0] === "workspace" && seg.length === 2 && method === "DELETE") {
    const upload = stubUploads.get(seg[1]);
    if (upload) upload.state = "deleted";
    return json({ success: true });
  }

  if (seg[0] === "workspace" && seg.length >= 2) {
    if (seg.length === 2 && method === "GET") {
      const upload = stubUploads.get(seg[1]);
      if (upload && upload.state === "ready") return json(stubItem(upload));
      const data = await paneItems();
      const item: WorkspaceItem | undefined = data?.items.find((i) => i.item_id === seg[1]);
      return item ? json(item) : notFound("العنصر غير موجود");
    }
    if (seg[2] === "references" && method === "GET") return json({ references: [] });
    if (seg[2] === "file") return notFound("الملف الأصلي غير متاح في المعاينة");
  }

  return notFound();
}
