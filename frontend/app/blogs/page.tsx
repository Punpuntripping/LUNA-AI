"use client";

import Link from "next/link";
import { BookText, Loader2 } from "lucide-react";
import { useMyBlogs } from "@/hooks/use-my-blogs";
import { ScrollArea } from "@/components/ui/scroll-area";

// مدوناتي landing (/blogs). The route-group layout supplies the sidebar; this
// page fills the main pane with the author's own blogs as a card grid. Clicking
// a card opens /blogs/{token} (the management view). Mirrors the empty-state
// convention of app/templates/page.tsx but, since the whole point is "see all
// my blogs", it renders the collection here rather than a bare prompt.

const DATE_FORMAT = new Intl.DateTimeFormat("ar-EG", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : DATE_FORMAT.format(d);
}

// Next.js App Router requires a default export for page files.
// eslint-disable-next-line import/no-default-export
export default function MyBlogsPage() {
  const { data, isLoading, isError } = useMyBlogs();
  const posts = data?.posts ?? [];

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
              المقالات التي حفظتها من إجاباتك.
            </p>
          </div>
        </header>

        {isLoading ? (
          <div className="flex h-40 items-center justify-center">
            <Loader2 className="h-5 w-5 animate-spin text-muted-foreground" />
          </div>
        ) : isError ? (
          <p className="py-16 text-center text-sm text-muted-foreground">
            تعذّر تحميل مدوناتك. حاول مرة أخرى.
          </p>
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
