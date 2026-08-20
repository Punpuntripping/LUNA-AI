"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { BookText, Loader2, Plus } from "lucide-react";
import { useMyBlogs } from "@/hooks/use-my-blogs";
import { useSearchQuery } from "@/hooks/use-search";
import { useSidebarStore } from "@/stores/sidebar-store";
import { Button } from "@/components/ui/button";
import { ScrollArea } from "@/components/ui/scroll-area";
import { SearchBar } from "@/components/search/SearchBar";
import { SearchEmptyState } from "@/components/search/SearchEmptyState";
import { SEARCH_PRIVATE_COPY } from "@/lib/search/copy";
import { AR_DATE_LOCALE } from "@/lib/format/numerals";

/**
 * «مدوناتي» as a full-pane card grid — the surface behind both `/blogs` (the
 * route-group landing) and `/blogs/mine` (the explicit per-user address that
 * mirrors `/library/mine`). ONE implementation, two addresses: the sidebar's
 * «عرض كل المدونات» button points at `/blogs/mine` so the three "things I've
 * collected" tabs (قوالبي · مدوناتي · مكتبتي) all resolve to a `/mine` page,
 * while the older `/blogs` links (SaveAsBlogDialog, the `[token]` back button)
 * keep working untouched.
 *
 * Clicking a card opens `/blogs/{token}` — the management view.
 *
 * ── SEARCH (bm25_navigation_search.md Wave D) ───────────────────────────────
 * One box, and BOTH addresses inherit it because both render this component —
 * which is the reason §6.2 wires the grid rather than the two pages.
 *
 * `SearchBar` is used WITHOUT a `gate`, and that is deliberate rather than an
 * omission: the gate is D9's anonymous conversion modal, and this page is
 * already behind auth, so there is no anonymous visitor here to convert.
 * Omitting it also means the box never subscribes to the auth store at all.
 *
 * There is no snippet handling and no highlighting (D3): the search response is
 * the same `MyBlogsResponse` the unfiltered listing returns, so the card below
 * keeps rendering the static `post.snippet` it always did. Nothing on this
 * surface interpolates a query into markup.
 */

const DATE_FORMAT = new Intl.DateTimeFormat(AR_DATE_LOCALE, {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : DATE_FORMAT.format(d);
}

export function MyBlogsGrid() {
  const { value, setValue, query, isSearching } = useSearchQuery();
  const { data, isLoading, isError, isFetching } = useMyBlogs(query);
  const setImportBlogDialogOpen = useSidebarStore(
    (s) => s.setImportBlogDialogOpen,
  );
  const posts = data?.posts ?? [];

  /**
   * The box appears once there is something to search, and then STAYS.
   *
   * A bare `posts.length > 0` cannot express that, because during a search
   * `posts` is the FILTERED set: it goes to zero on a no-match query — hiding
   * the one control that gets the reader back out of it — and, worse, stays at
   * zero for the beat after the box is cleared, while `keepPreviousData` holds
   * the empty result on screen and the unfiltered refetch is still in flight.
   * The box would blink out and back in on every abandoned search.
   *
   * So it latches, monotonically, on the first non-empty listing of the
   * session. A reader with no blogs at all still sees no box: searching an
   * empty collection is a control that can only ever answer «لا توجد نتائج».
   */
  const [everHadPosts, setEverHadPosts] = useState(false);
  useEffect(() => {
    if (posts.length > 0) setEverHadPosts(true);
  }, [posts.length]);
  const showSearch = everHadPosts || isSearching;

  return (
    <ScrollArea className="flex-1" dir="rtl">
      <div className="mx-auto w-full max-w-5xl px-4 py-8">
        <header className="mb-6 flex items-center gap-3">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <BookText className="h-5 w-5" />
          </span>
          <div>
            <h1 className="text-xl font-bold text-foreground">مدوناتي</h1>
            <p className="text-sm text-muted-foreground">
              المقالات التي حفظتها من إجاباتك أو أضفتها برابط.
            </p>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="ms-auto gap-1.5"
            onClick={() => setImportBlogDialogOpen(true)}
          >
            <Plus className="h-4 w-4" />
            إضافة برابط
          </Button>
        </header>

        {showSearch && (
          <div className="mb-6">
            <SearchBar
              value={value}
              onChange={setValue}
              placeholder={SEARCH_PRIVATE_COPY.blogs.placeholder}
              ariaLabel={SEARCH_PRIVATE_COPY.blogs.ariaLabel}
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
            تعذّر تحميل مدوناتك. حاول مرة أخرى.
          </p>
        ) : posts.length === 0 && isSearching ? (
          // «جرّب كلمات بحث أخرى» — never «لا توجد مدونات محفوظة بعد», which
          // would tell someone with 40 posts that they have none.
          <SearchEmptyState />
        ) : posts.length === 0 ? (
          <div className="flex flex-col items-center justify-center px-4 py-16 text-center">
            <div className="mb-5 flex h-16 w-16 items-center justify-center rounded-2xl bg-muted text-muted-foreground">
              <BookText className="h-7 w-7" />
            </div>
            <h2 className="mb-2 text-lg font-bold text-foreground">
              لا توجد مدونات محفوظة بعد
            </h2>
            <p className="max-w-md text-sm text-muted-foreground">
              من أي إجابة، اضغط «حفظ كمدونة» لحفظها هنا — ثم يمكنك نشرها في المدونة
              العامة.
            </p>
          </div>
        ) : (
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {posts.map((post) => (
              <Link
                key={post.token}
                href={`/blogs/${post.token}`}
                className="flex flex-col rounded-xl border bg-card p-4 shadow-sm transition hover:border-primary/30 hover:shadow-md"
              >
                <div className="mb-2 flex items-center gap-2">
                  <span className="inline-flex items-center rounded-full bg-primary/10 px-2 py-0.5 text-[10px] font-medium text-primary">
                    {post.display_mode === "title" ? "مدونة" : "سؤال"}
                  </span>
                  {post.is_imported && (
                    <span className="inline-flex items-center rounded-full bg-accent px-2 py-0.5 text-[10px] font-medium text-accent-foreground">
                      مستوردة
                    </span>
                  )}
                  <span
                    className={
                      "inline-flex items-center gap-1 text-[10px] font-medium " +
                      (post.is_public
                        ? "text-success-fg"
                        : "text-muted-foreground")
                    }
                  >
                    <span
                      className={
                        "h-1.5 w-1.5 rounded-full " +
                        (post.is_public ? "bg-success-fg" : "bg-muted-foreground/50")
                      }
                    />
                    {post.is_public ? "عام" : "خاص"}
                  </span>
                </div>

                <h3 className="line-clamp-2 text-sm font-bold text-foreground">
                  {post.title?.trim() || "بدون عنوان"}
                </h3>

                {post.snippet && (
                  <p className="mt-1.5 line-clamp-3 text-xs leading-relaxed text-muted-foreground">
                    {post.snippet}
                  </p>
                )}

                <span className="mt-3 text-[11px] text-muted-foreground/80">
                  {formatDate(post.created_at)}
                </span>
              </Link>
            ))}
          </div>
        )}
      </div>
    </ScrollArea>
  );
}
