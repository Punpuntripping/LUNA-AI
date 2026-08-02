"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { FileText, Loader2, Plus } from "lucide-react";
import { getRelativeTimeAr } from "@/lib/utils";
import { useTemplates } from "@/hooks/use-templates";
import { useSearchQuery } from "@/hooks/use-search";
import { useSidebarStore } from "@/stores/sidebar-store";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import { SEARCH_PRIVATE_COPY } from "@/lib/search/copy";

/**
 * «قوالبي» as a full-pane card grid — the قوالب twin of `MyBlogsGrid`, behind
 * `/templates/mine`. Mirrors مدوناتي's header/grid/empty-state shape so the
 * three per-user collections read as one component family.
 *
 * Deliberately NOT a second editor: a card is a way *into* `/templates/{id}`,
 * where TemplateEditor stays the single place a قالب is written. Destructive
 * actions (حذف) likewise stay in the sidebar row's menu — one home each.
 *
 * ── SEARCH (bm25_navigation_search.md Wave D) ───────────────────────────────
 * `/templates` and `/templates/mine` both render this component and therefore
 * both inherit the box. It reaches `content_md`, not just the title, because a
 * قالب is indexed in full (it is the caller's own text and nothing about it is
 * gated) — so «فسخ العقد» finds the قالب whose clause says it even when its
 * title is «نموذج إنهاء».
 *
 * No `gate` prop: that is D9's anonymous conversion modal and this surface is
 * already authed. No highlighting either (D3) — the markdown preview below is
 * the same static `contentPreview` the unfiltered grid renders.
 */

/**
 * First readable line of a قالب, for the card preview. The stored body is
 * markdown the editor round-trips, so headings/bullets/emphasis have to come
 * off or the preview reads as syntax rather than content.
 */
function contentPreview(md: string): string {
  const text = md
    .replace(/```[\s\S]*?```/g, " ") // fenced code
    .replace(/^\s{0,3}#{1,6}\s+/gm, "") // heading markers
    .replace(/^\s{0,3}[-*+]\s+/gm, "") // bullets
    .replace(/^\s{0,3}>\s?/gm, "") // block quotes
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1") // links / images → their text
    .replace(/[*_`~]/g, "")
    .replace(/\s+/g, " ")
    .trim();
  return text.length > 220 ? `${text.slice(0, 220)}…` : text;
}

export function MyTemplatesGrid() {
  const { value, setValue, query, isSearching } = useSearchQuery();
  const { data, isLoading, isError, isFetching } = useTemplates(query);
  const setCreateTemplateDialogOpen = useSidebarStore(
    (s) => s.setCreateTemplateDialogOpen,
  );
  const templates = data?.templates ?? [];

  /**
   * Same monotonic latch as مدوناتي, for the same reason — see `MyBlogsGrid`.
   * `templates` is the FILTERED set during a search, so deriving visibility
   * from its length would blink the box out on a no-match query and again on
   * the beat after that search is cleared.
   */
  const [everHadTemplates, setEverHadTemplates] = useState(false);
  useEffect(() => {
    if (templates.length > 0) setEverHadTemplates(true);
  }, [templates.length]);
  const showSearch = everHadTemplates || isSearching;

  return (
    <ScrollArea className="flex-1" dir="rtl">
      <div className="mx-auto w-full max-w-5xl px-4 py-8">
        <header className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <FileText className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-xl font-bold text-foreground">قوالبي</h1>
            <p className="text-sm text-muted-foreground">
              الصيغ التي حفظتها لإعادة استخدامها في عملك القانوني.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ms-auto gap-1.5"
            onClick={() => setCreateTemplateDialogOpen(true)}
          >
            <Plus className="h-4 w-4" />
            قالب جديد
          </Button>
        </header>

        {showSearch && (
          <div className="mb-6">
            <SearchBar
              value={value}
              onChange={setValue}
              placeholder={SEARCH_PRIVATE_COPY.templates.placeholder}
              ariaLabel={SEARCH_PRIVATE_COPY.templates.ariaLabel}
              isPending={isSearching && isFetching}
            />
          </div>
        )}

        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : isError ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            تعذّر تحميل قوالبك. حاول مرة أخرى.
          </p>
        ) : templates.length === 0 && isSearching ? (
          // A search that matched nothing is «جرّب كلمات بحث أخرى», never «لا
          // توجد قوالب بعد» — the second would invite a reader who has fifty of
          // them to create a fifty-first.
          <SearchEmptyState />
        ) : templates.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
            <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
              <FileText className="h-7 w-7" />
            </div>
            <h2 className="mb-2 text-lg font-bold text-foreground">
              لا توجد قوالب بعد
            </h2>
            <p className="max-w-md text-sm text-muted-foreground">
              أنشئ قالبًا جديدًا، أو احفظ صيغة من إجاباتك لتظهر هنا وتستخدمها في
              أي محادثة.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {templates.map((template) => {
              const preview = contentPreview(template.content_md ?? "");
              return (
                <Link
                  key={template.template_id}
                  href={`/templates/${template.template_id}`}
                  className="flex flex-col rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/30 hover:shadow-md"
                >
                  <div className="mb-2 flex items-center gap-2">
                    <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                      قالب
                    </span>
                    {template.created_by === "agent" && (
                      <span className="inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-[10px] font-medium text-accent-foreground">
                        من ريحان
                      </span>
                    )}
                  </div>

                  <h3 className="line-clamp-2 text-sm font-bold text-foreground">
                    {template.title?.trim() || "قالب بدون عنوان"}
                  </h3>

                  {preview && (
                    <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
                      {preview}
                    </p>
                  )}

                  <span className="mt-3 text-[11px] text-muted-foreground/80">
                    {getRelativeTimeAr(template.updated_at)}
                  </span>
                </Link>
              );
            })}
          </div>
        )}
      </div>
    </ScrollArea>
  );
}
