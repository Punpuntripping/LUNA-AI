"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { FlaskConical, Moon, Sun, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { FileViewer } from "@/components/files/FileViewer";
import type { AngleMap, LibraryFileDetail } from "@/lib/contracts";

/**
 * DEV ONLY — the «الأوجه» tab on the 27 real map groups of the angle-map
 * preview runs (preview_v4 + preview_v5), for the owner to review before any
 * backend route or switch exists.
 *
 * - «المجموعة»: one message (its files were mapped together).
 * - «الملف المفتوح»: which of its files is open — the map is the same, the
 *   «هذا الملف» column moves.
 * - «العرض»: the chat pane (narrow, beside a conversation), the /files page, a phone.
 * - «محادثة ثانية»: adds a made-up second map of the same file, to see the
 *   «من محادثة» picker (the real data has no file in two maps).
 *
 * The real `FileViewer` renders; only its data is a fixture.
 */

interface IndexRow {
  key: string;
  prompt_version: string;
  documents: number;
  angles: number;
  kinds: { strengthen: number; attack: number; answer: number };
  gaps: number;
  client_confidence: string;
  first_title: string;
  source: string;
}

interface GroupFixture {
  key: string;
  message_id: string;
  conversation_id: string;
  prompt_version: string;
  files: LibraryFileDetail[];
  map: AngleMap;
}

type View = "pane" | "page" | "phone";

const VIEW_LABELS: Record<View, string> = {
  pane: "لوحة المحادثة",
  page: "صفحة الملفات",
  phone: "جوال",
};

async function fetchFixture<T>(name: string): Promise<T> {
  const res = await fetch(`/dev-preview/angle-map/fixture/${name}`, { cache: "no-store" });
  if (!res.ok) throw new Error(`fixture ${name}: ${res.status}`);
  return (await res.json()) as T;
}

/** A made-up second map: same file, another conversation, angles in reverse. */
function syntheticSecondMap(map: AngleMap): AngleMap {
  const angles = [...map.angles].reverse().map((a, i) => ({ ...a, seq: i + 1 }));
  return {
    ...map,
    map_id: `${map.map_id}-second`,
    conversation_id: "synthetic-second",
    conversation_title: "محادثة أخرى (مثال مصطنع)",
    created_at: "2026-11-01T09:00:00+00:00",
    angles,
  };
}

export function AngleMapPreview() {
  const router = useRouter();
  const params = useSearchParams();
  const [index, setIndex] = useState<IndexRow[] | null>(null);
  const [group, setGroup] = useState<GroupFixture | null>(null);
  const [error, setError] = useState<string | null>(null);

  const key = params.get("g") ?? "";
  const view = (["pane", "page", "phone"].includes(params.get("v") ?? "") ? params.get("v") : "pane") as View;
  const docParam = params.get("d") ?? "";
  const second = params.get("two") === "1";

  const setParam = (name: string, value: string | null) => {
    const next = new URLSearchParams(params.toString());
    if (value == null || value === "") next.delete(name);
    else next.set(name, value);
    router.replace(`?${next.toString()}`, { scroll: false });
  };

  useEffect(() => {
    fetchFixture<{ groups: IndexRow[] }>("index")
      .then((d) => setIndex(d.groups))
      .catch((e: unknown) => setError(String(e)));
  }, []);

  const activeKey = key || index?.[0]?.key || "";
  useEffect(() => {
    if (!activeKey) return;
    setGroup(null);
    fetchFixture<GroupFixture>(activeKey)
      .then(setGroup)
      .catch((e: unknown) => setError(String(e)));
  }, [activeKey]);

  const mappedFiles = useMemo(() => {
    if (!group) return [];
    const ids = new Set(group.map.documents.map((d) => d.item_id));
    return group.files.filter((f) => ids.has(f.item_id));
  }, [group]);
  const file = mappedFiles.find((f) => f.item_id.startsWith(docParam) && docParam) ?? mappedFiles[0] ?? null;
  const maps = useMemo(
    () => (group ? (second ? [group.map, syntheticSecondMap(group.map)] : [group.map]) : []),
    [group, second],
  );
  const docNumber = file ? group?.map.documents.find((d) => d.item_id === file.item_id)?.index : null;

  return (
    <div dir="rtl" className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b bg-background/95 px-4 py-2.5 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span className="inline-flex items-center gap-1.5 font-semibold">
            <FlaskConical className="h-4 w-4 text-primary" aria-hidden="true" />
            خريطة الأوجه — معاينة داخلية
          </span>
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">المجموعة</span>
            <select
              value={activeKey}
              onChange={(e) => setParam("g", e.target.value) /* the file resets below */}
              className="h-8 max-w-[22rem] rounded-md border bg-card px-2 text-xs"
            >
              {(index ?? []).map((row) => (
                <option key={row.key} value={row.key}>
                  {`${row.key} · ${row.documents} مستند · ${row.angles} أوجه (${row.kinds.strengthen}/${row.kinds.attack}/${row.kinds.answer}) · ${row.prompt_version} — ${row.first_title.slice(0, 40)}`}
                </option>
              ))}
            </select>
          </label>
          {mappedFiles.length > 1 && (
            <label className="flex items-center gap-1.5">
              <span className="text-muted-foreground">الملف المفتوح</span>
              <select
                value={file?.item_id ?? ""}
                onChange={(e) => setParam("d", e.target.value.slice(0, 8))}
                className="h-8 max-w-[16rem] rounded-md border bg-card px-2 text-xs"
              >
                {mappedFiles.map((f) => {
                  const n = group?.map.documents.find((d) => d.item_id === f.item_id)?.index;
                  return (
                    <option key={f.item_id} value={f.item_id}>
                      {`المستند ${n} — ${f.title.slice(0, 40)}`}
                    </option>
                  );
                })}
              </select>
            </label>
          )}
          <div className="flex items-center gap-1 rounded-lg border p-0.5" role="group" aria-label="العرض">
            {(Object.keys(VIEW_LABELS) as View[]).map((v) => (
              <button
                key={v}
                type="button"
                aria-pressed={view === v}
                onClick={() => setParam("v", v)}
                className={cn(
                  "rounded-md px-2.5 py-1 text-xs",
                  view === v ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent",
                )}
              >
                {VIEW_LABELS[v]}
              </button>
            ))}
          </div>
          <label className="flex items-center gap-1.5 text-xs">
            <input type="checkbox" checked={second} onChange={(e) => setParam("two", e.target.checked ? "1" : null)} />
            محادثة ثانية
          </label>
          <ThemeButton />
        </div>
        <p className="mx-auto mt-1 max-w-6xl text-xs text-muted-foreground">
          بيانات حقيقية من تشغيلات المعاينة (مستندات عملاء) — محلية فقط، لا شيء مخزّن ولا مفعّل.
          {group && ` المجموعة ${group.key} · ${group.prompt_version}${docNumber ? ` · المستند ${docNumber} مفتوح` : ""}`}
        </p>
      </header>

      {error && <p className="p-6 text-sm text-destructive">{error} — هل ضُبط ANGLE_MAP_FIXTURE_DIR؟</p>}

      <main className="px-4 py-6">
        {!file || !group ? (
          !error && <p className="p-6 text-center text-sm text-muted-foreground">جارٍ التحميل…</p>
        ) : view === "pane" ? (
          <PaneFrame file={file}>
            <FileViewer
              key={`${group.key}-${file.item_id}-${second}`}
              file={file}
              variant="pane"
              angleMaps={maps}
              initialTab="angles"
            />
          </PaneFrame>
        ) : view === "page" ? (
          <div className="mx-auto w-full max-w-3xl">
            <FileViewer
              key={`${group.key}-${file.item_id}-${second}`}
              file={file}
              variant="page"
              angleMaps={maps}
              initialTab="angles"
            />
          </div>
        ) : (
          <div className="mx-auto w-[375px] overflow-hidden rounded-[28px] border-8 border-foreground/80 bg-background">
            <div className="h-[760px] overflow-y-auto px-4 py-5">
              <FileViewer
                key={`${group.key}-${file.item_id}-${second}`}
                file={file}
                variant="page"
                angleMaps={maps}
                initialTab="angles"
              />
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

/** The conversation pane as it sits beside a chat: a ~480px column with its header. */
function PaneFrame({ file, children }: { file: LibraryFileDetail; children: React.ReactNode }) {
  return (
    <div className="mx-auto flex max-w-6xl gap-4">
      <div className="hidden flex-1 rounded-xl border border-dashed p-6 text-sm text-muted-foreground lg:block">
        (المحادثة)
      </div>
      <aside className="flex h-[calc(100dvh-9rem)] w-full max-w-[480px] flex-col overflow-hidden rounded-xl border bg-background shadow-sm">
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5">
          {file.wi_seq != null && (
            <span className="rounded bg-muted px-1.5 text-xs font-semibold tabular-nums">WI-{file.wi_seq}</span>
          )}
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">{file.title}</span>
          <X className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">{children}</div>
      </aside>
    </div>
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
