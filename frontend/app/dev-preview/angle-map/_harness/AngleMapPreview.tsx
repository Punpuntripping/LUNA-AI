"use client";

import { useEffect, useMemo, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useTheme } from "next-themes";
import { ArrowRight, FlaskConical, FolderOpen, Moon, Sun, X } from "lucide-react";
import { cn } from "@/lib/utils";
import { FileCard } from "@/components/files/FileCard";
import {
  AngleMapPageBody,
  AnglesStack,
  FilesViewSwitch,
  LibraryAnglesList,
  type FilesView,
} from "@/components/files/angles/AnglesViews";
import {
  libraryFileCardModel,
  type AngleKind,
  type AngleMap,
  type AngleMapSummary,
  type LibraryFileDetail,
} from "@/lib/contracts";

/**
 * DEV ONLY — «الأوجه» at the front of «الملفات» (angle_map_ui.md rev 3) on the
 * real map groups of the angle-map preview runs, for the owner to review.
 *
 * - «لوحة المحادثة»: the pane's «الملفات» tab — «الأوجه» (the conversation's
 *   maps, stacked newest first) | «الملفات» (its files).
 * - «صفحة الملفات» / «جوال»: /files — «الأوجه» (every map, grouped by
 *   conversation; a row opens the map's page) | «الملفات».
 * - «محادثة بخريطتين»: adds a made-up second map to the chosen conversation, to
 *   see the stack (most real conversations have one).
 *
 * The real presentational components render; only their data is a fixture.
 */

interface IndexRow {
  key: string;
  prompt_version: string;
  documents: number;
  angles: number;
  kinds: Record<AngleKind, number>;
  first_title: string;
}

interface GroupFixture {
  key: string;
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

function kindsOf(map: AngleMap): Record<AngleKind, number> {
  const k: Record<AngleKind, number> = { strengthen: 0, attack: 0, answer: 0 };
  for (const a of map.angles) k[a.kind] = (k[a.kind] ?? 0) + 1;
  return k;
}

function summaryOf(map: AngleMap): AngleMapSummary {
  return {
    map_id: map.map_id,
    conversation_id: map.conversation_id,
    conversation_title: map.conversation_title,
    message_id: map.message_id,
    created_at: map.created_at,
    documents: map.documents,
    angle_count: map.angles.length,
    kinds: kindsOf(map),
  };
}

/** A made-up older map in the same conversation (angles reversed). */
function syntheticSecondMap(map: AngleMap): AngleMap {
  return {
    ...map,
    map_id: `${map.map_id}-second`,
    created_at: "2026-11-10T09:00:00+00:00",
    angles: [...map.angles].reverse().map((a, i) => ({ ...a, seq: i + 1 })),
  };
}

export function AngleMapPreview() {
  const router = useRouter();
  const params = useSearchParams();
  const [index, setIndex] = useState<IndexRow[] | null>(null);
  const [groups, setGroups] = useState<Record<string, GroupFixture>>({});
  const [error, setError] = useState<string | null>(null);

  const key = params.get("g") ?? "";
  const view = (["pane", "page", "phone"].includes(params.get("v") ?? "") ? params.get("v") : "pane") as View;
  const second = params.get("two") === "1";
  const openMap = params.get("map");

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

  // The chosen group first (the pane needs only it), then every other one in
  // the background (the /files list needs them all).
  useEffect(() => {
    if (!index || !activeKey) return;
    let alive = true;
    const add = (g: GroupFixture) => {
      if (alive) setGroups((prev) => (prev[g.key] ? prev : { ...prev, [g.key]: g }));
    };
    fetchFixture<GroupFixture>(activeKey)
      .then(add)
      .then(() => Promise.all(index.filter((r) => r.key !== activeKey).map((r) => fetchFixture<GroupFixture>(r.key).then(add))))
      .catch((e: unknown) => setError(String(e)));
    return () => {
      alive = false;
    };
  }, [index, activeKey]);
  const group = groups[activeKey];

  // Each fixture map gets its group's key as id (unique, and what the list links to).
  const allMaps = useMemo(() => {
    const maps = Object.values(groups).map((g) => ({ ...g.map, map_id: g.key }));
    if (second && group) maps.push(syntheticSecondMap({ ...group.map, map_id: group.key }));
    return maps.sort((a, b) => (a.created_at < b.created_at ? 1 : a.created_at > b.created_at ? -1 : 0));
  }, [groups, second, group]);

  const conversationMaps = useMemo(
    () => (group ? allMaps.filter((m) => m.conversation_id === group.map.conversation_id) : []),
    [allMaps, group],
  );
  const summaries = useMemo(() => allMaps.map(summaryOf), [allMaps]);
  const opened = openMap ? allMaps.find((m) => m.map_id === openMap) ?? null : null;

  return (
    <div dir="rtl" className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b bg-background/95 px-4 py-2.5 backdrop-blur">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-4 gap-y-2 text-sm">
          <span className="inline-flex items-center gap-1.5 font-semibold">
            <FlaskConical className="h-4 w-4 text-primary" aria-hidden="true" />
            «الخيوط» في واجهة «الملفات» — معاينة داخلية
          </span>
          <label className="flex items-center gap-1.5">
            <span className="text-muted-foreground">المحادثة (المجموعة)</span>
            <select
              value={activeKey}
              onChange={(e) => setParam("g", e.target.value)}
              className="h-8 max-w-[22rem] rounded-md border bg-card px-2 text-xs"
            >
              {(index ?? []).map((row) => (
                <option key={row.key} value={row.key}>
                  {`${row.key} · ${row.documents} مستند · ${row.angles} خيوط · ${row.prompt_version} — ${row.first_title.slice(0, 40)}`}
                </option>
              ))}
            </select>
          </label>
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
            محادثة بخريطتين
          </label>
          <ThemeButton />
        </div>
        <p className="mx-auto mt-1 max-w-6xl text-xs text-muted-foreground">
          بيانات حقيقية من تشغيلات المعاينة (مستندات عملاء) — محلية فقط. عناوين المحادثات هنا معرّفات، لا عناوينها الحقيقية.
        </p>
      </header>

      {error && <p className="p-6 text-sm text-destructive">{error} — هل ضُبط ANGLE_MAP_FIXTURE_DIR؟</p>}

      <main className="px-4 py-6">
        {!group ? (
          !error && <p className="p-6 text-center text-sm text-muted-foreground">جارٍ التحميل…</p>
        ) : view === "pane" ? (
          <PaneMock files={group.files} maps={conversationMaps} />
        ) : (
          <div className={cn(view === "phone" && "mx-auto w-[375px] overflow-hidden rounded-[28px] border-8 border-foreground/80")}>
            <div className={cn(view === "phone" ? "h-[760px] overflow-y-auto px-4 py-5" : "mx-auto w-full max-w-5xl")}>
              {opened ? (
                <div className="mx-auto max-w-3xl">
                  <button
                    type="button"
                    onClick={() => setParam("map", null)}
                    className="mb-4 inline-flex items-center gap-1.5 rounded-md px-1.5 py-1 text-sm text-muted-foreground hover:text-foreground"
                  >
                    <ArrowRight className="h-4 w-4" aria-hidden="true" />
                    الملفات
                  </button>
                  <AngleMapPageBody key={opened.map_id} map={opened} conversationHref="#" />
                </div>
              ) : (
                <FilesPageMock
                  summaries={summaries}
                  files={Object.values(groups).flatMap((g) => g.files)}
                  onOpen={(id) => setParam("map", id)}
                />
              )}
            </div>
          </div>
        )}
      </main>
    </div>
  );
}

/** The chat pane: «العناصر» | «الملفات», and inside «الملفات» «الأوجه» | «الملفات». */
function PaneMock({ files, maps }: { files: LibraryFileDetail[]; maps: AngleMap[] }) {
  const [inner, setInner] = useState<FilesView>("files");
  return (
    <div className="mx-auto flex max-w-6xl gap-4">
      <div className="hidden flex-1 rounded-xl border border-dashed p-6 text-sm text-muted-foreground lg:block">
        (المحادثة)
      </div>
      <aside className="flex h-[calc(100dvh-9rem)] w-full max-w-[480px] flex-col overflow-hidden rounded-xl border bg-background shadow-sm">
        <div className="flex shrink-0 items-center gap-2 border-b px-4 py-2.5">
          <span className="min-w-0 flex-1 truncate text-sm font-semibold">المخرجات</span>
          <X className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
        </div>
        <div className="shrink-0 border-b px-2 py-1.5">
          <div className="grid h-9 w-full grid-cols-2 rounded-md bg-muted p-1 text-sm">
            <span className="flex items-center justify-center text-muted-foreground">العناصر</span>
            <span className="flex items-center justify-center rounded-sm bg-background shadow-sm">
              الملفات <span className="ms-1.5 text-xs tabular-nums text-muted-foreground">{files.length}</span>
            </span>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="flex justify-center border-b border-border/60 px-2 py-2">
            <FilesViewSwitch value={inner} onChange={setInner} />
          </div>
          {inner === "angles" ? (
            <AnglesStack maps={maps} />
          ) : (
            <div className="space-y-1.5 p-2">
              {files.map((f) => (
                <FileCard key={f.item_id} file={libraryFileCardModel(f)} showWiBadge onOpen={() => {}} />
              ))}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}

/** /files: the header, «الأوجه» | «الملفات», then the list or the files. */
function FilesPageMock({
  summaries,
  files,
  onOpen,
}: {
  summaries: AngleMapSummary[];
  files: LibraryFileDetail[];
  onOpen: (mapId: string) => void;
}) {
  const [view, setView] = useState<FilesView>("files");
  return (
    <div className="py-2">
      <header className="mb-6 flex items-center gap-3">
        <span className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
          <FolderOpen className="h-5 w-5" />
        </span>
        <div>
          <h1 className="text-xl font-bold">الملفات</h1>
          <p className="text-sm text-muted-foreground tabular-nums">{files.length} ملفًا من محادثاتك</p>
        </div>
      </header>
      <FilesViewSwitch value={view} onChange={setView} className="mb-5" />
      {view === "angles" ? (
        <div
          onClickCapture={(e) => {
            const a = (e.target as HTMLElement).closest("a");
            if (!a) return;
            e.preventDefault();
            onOpen(decodeURIComponent((a.getAttribute("href") ?? "").split("/").pop() ?? ""));
          }}
        >
          <LibraryAnglesList maps={summaries} mapHref={(id) => `/files/angles/${encodeURIComponent(id)}`} />
        </div>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {files.slice(0, 12).map((f) => (
            <FileCard key={f.item_id} file={libraryFileCardModel(f)} onOpen={() => {}} />
          ))}
        </div>
      )}
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
