"use client";

import { useParams, useRouter } from "next/navigation";
import { Newspaper } from "lucide-react";
import { cn } from "@/lib/utils";
import { useMyBlogs } from "@/hooks/use-my-blogs";
import { useSidebarStore } from "@/stores/sidebar-store";
import { ScrollArea } from "@/components/ui/scroll-area";
import { ImportBlogDialog } from "@/components/blogs/ImportBlogDialog";
import type { MyBlogItem } from "@/types";

function SectionHeader({ children }: { children: React.ReactNode }) {
  return (
    <div className="px-4 pt-3 pb-2 shrink-0">
      <p className="text-[11px] font-medium uppercase tracking-[0.2em] text-muted-foreground/60">
        {children}
      </p>
    </div>
  );
}

function BlogSkeleton() {
  return (
    <div className="space-y-1.5 px-3 py-2">
      {Array.from({ length: 6 }).map((_, i) => (
        <div key={i} className="h-7 rounded-md bg-muted/40 animate-pulse" />
      ))}
    </div>
  );
}

// Gregorian Arabic date (e.g. «٣٠ يونيو ٢٠٢٦») — matches the public blog byline.
const DATE_FORMAT = new Intl.DateTimeFormat("ar-EG", {
  day: "numeric",
  month: "long",
  year: "numeric",
});

function formatDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return DATE_FORMAT.format(d);
}

function BlogItem({ blog }: { blog: MyBlogItem }) {
  const router = useRouter();
  const params = useParams<{ token?: string }>();

  const isActive = params?.token === blog.token;
  const title = (blog.title ?? "").trim() || "بدون عنوان";
  const modeLabel = blog.display_mode === "title" ? "مدونة" : "سؤال";

  return (
    <div
      role="button"
      tabIndex={0}
      className={cn(
        "group flex items-center gap-2 rounded-md px-3 py-2 cursor-pointer transition-colors",
        isActive
          ? "bg-accent text-accent-foreground"
          : "text-sidebar-foreground/85 hover:bg-accent/40 hover:text-foreground",
      )}
      onClick={() => router.push(`/blogs/${blog.token}`)}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          router.push(`/blogs/${blog.token}`);
        }
      }}
    >
      <div className="flex-1 min-w-0">
        <p className="text-sm truncate">{title}</p>
        <div className="mt-1 flex items-center gap-2">
          <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] font-medium leading-none text-muted-foreground">
            {modeLabel}
          </span>
          {blog.is_imported && (
            <span className="rounded-full bg-accent px-1.5 py-0.5 text-[10px] font-medium leading-none text-accent-foreground">
              مستوردة
            </span>
          )}
          <span className="inline-flex items-center gap-1 text-[10px] font-medium leading-none text-muted-foreground">
            <span
              className={cn(
                "h-1.5 w-1.5 rounded-full",
                blog.is_public ? "bg-primary" : "bg-muted-foreground/40",
              )}
              aria-hidden
            />
            {blog.is_public ? "عام" : "خاص"}
          </span>
          <span className="text-[10px] text-muted-foreground/70">
            {formatDate(blog.created_at)}
          </span>
        </div>
      </div>
    </div>
  );
}

export function BlogList() {
  const { data, isLoading, isError } = useMyBlogs();
  const isImportOpen = useSidebarStore((s) => s.isImportBlogDialogOpen);
  const setImportOpen = useSidebarStore((s) => s.setImportBlogDialogOpen);
  const blogs = data?.posts ?? [];

  let body: React.ReactNode;
  if (isLoading) {
    body = <BlogSkeleton />;
  } else if (isError) {
    body = (
      <div className="flex flex-col items-center justify-center py-8 px-4 text-center">
        <p className="text-sm text-destructive">حدث خطأ في تحميل المدونات</p>
      </div>
    );
  } else if (blogs.length === 0) {
    body = (
      <div className="flex flex-col items-center justify-center py-12 px-4 text-center gap-3">
        <Newspaper className="h-9 w-9 text-muted-foreground/40" />
        <div>
          <p className="text-sm font-medium text-muted-foreground">
            لا توجد مدونات محفوظة بعد.
          </p>
          <p className="text-xs text-muted-foreground/70 mt-1">
            احفظ إجابة كمدونة لتظهر هنا.
          </p>
        </div>
      </div>
    );
  } else {
    body = (
      <ScrollArea className="flex-1 min-h-0">
        <div className="px-2 pb-2 space-y-0.5">
          {blogs.map((blog) => (
            <BlogItem key={blog.post_id} blog={blog} />
          ))}
        </div>
      </ScrollArea>
    );
  }

  return (
    <div className="flex flex-col flex-1 min-h-0">
      <SectionHeader>مدوناتي</SectionHeader>
      {body}
      <ImportBlogDialog open={isImportOpen} onOpenChange={setImportOpen} />
    </div>
  );
}
