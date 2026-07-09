"use client";

import { useCallback, useState } from "react";
import {
  BookText,
  LayoutTemplate,
  Link2,
  Loader2,
  Paperclip,
  Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useTemplates } from "@/hooks/use-templates";
import { useMyBlogs } from "@/hooks/use-my-blogs";
import type { PendingTemplate } from "@/types";

interface ComposerPlusMenuProps {
  /** Disables the whole «+» trigger (streaming / conversation being created). */
  disabled?: boolean;
  /**
   * Whether attach-style items (مرفق, مدونة) can work — they need an existing
   * conversation or the create-on-attach handler. The قالب item is pure text
   * at send time so it stays enabled regardless.
   */
  canAttach: boolean;
  /** مرفق — opens the hidden file input (behavior unchanged). */
  onPickFiles: () => void;
  /** قالب — attach ONE template chip (replaces any existing one). */
  onPickTemplate: (template: PendingTemplate) => void;
  /**
   * مدونة — queue blog token(s) as composer chips. Same path for a «من
   * مدوناتي» list pick and a validated «إضافة رابط» dialog submit; the caller
   * (ChatInput) dedups, caps, and rides the create-on-attach flow.
   */
  onAddBlogTokens: (tokens: string[]) => void;
}

/**
 * Extract a blog share token from user input: either a full share URL
 * containing ``/blog/<32-hex>`` or a bare 32-hex token. Mirrors the paste
 * handler's regex in ChatInput. Returns null when nothing matches.
 */
function extractBlogToken(input: string): string | null {
  const fromUrl = input.match(/\/blog\/([0-9a-f]{32})(?![0-9a-f])/i);
  if (fromUrl) return fromUrl[1].toLowerCase();
  const bare = input.trim().match(/^([0-9a-f]{32})$/i);
  return bare ? bare[1].toLowerCase() : null;
}

/** Non-interactive status row for the submenu lists (loading/empty/error). */
function MenuStatus({ text, loading }: { text: string; loading?: boolean }) {
  return (
    <div className="flex items-center gap-2 px-2 py-2 text-xs text-muted-foreground">
      {loading && <Loader2 className="h-3.5 w-3.5 animate-spin" />}
      <span>{text}</span>
    </div>
  );
}

/**
 * قوالبي list — mounts (and therefore fetches) only when the قالب submenu
 * opens. Scrollable via the parent SubContent's max-height.
 */
function TemplateMenuList({
  onPick,
}: {
  onPick: (template: PendingTemplate) => void;
}) {
  const { data, isPending, isError } = useTemplates();

  if (isPending) return <MenuStatus text="جارٍ التحميل..." loading />;
  if (isError) return <MenuStatus text="تعذّر تحميل القوالب" />;

  const templates = data.templates;
  if (templates.length === 0) return <MenuStatus text="لا توجد قوالب بعد" />;

  return (
    <>
      {templates.map((t) => (
        <DropdownMenuItem
          key={t.template_id}
          className="gap-2"
          onSelect={() =>
            onPick({ templateId: t.template_id, title: t.title })
          }
        >
          <LayoutTemplate className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate">{t.title.trim() || "قالب بدون عنوان"}</span>
        </DropdownMenuItem>
      ))}
    </>
  );
}

/**
 * مدوناتي list — mounts (and therefore fetches) only when the مدونة submenu
 * opens. Question-mode posts have a null title, so fall back to the snippet.
 */
function MyBlogMenuList({ onPick }: { onPick: (token: string) => void }) {
  const { data, isPending, isError } = useMyBlogs();

  if (isPending) return <MenuStatus text="جارٍ التحميل..." loading />;
  if (isError) return <MenuStatus text="تعذّر تحميل المدونات" />;

  const posts = data.posts;
  if (posts.length === 0) return <MenuStatus text="لا توجد مدونات بعد" />;

  return (
    <>
      {posts.map((post) => (
        <DropdownMenuItem
          key={post.post_id}
          className="gap-2"
          onSelect={() => onPick(post.token)}
        >
          <BookText className="h-4 w-4 shrink-0 text-muted-foreground" />
          <span className="truncate">
            {(post.title ?? "").trim() || post.snippet.trim() || "مدونة"}
          </span>
        </DropdownMenuItem>
      ))}
    </>
  );
}

/**
 * The composer's «+» menu — replaces the old paperclip-only button. Three
 * appendable message add-ons, each ending as a removable pre-send indicator
 * above the input:
 *
 *   - مرفق  → the unchanged file picker (FilePreview cards).
 *   - قالب  → scrollable قوالبي list → single template chip.
 *   - مدونة → «إضافة رابط» dialog + scrollable «من مدوناتي» list → blog chips.
 */
export function ComposerPlusMenu({
  disabled,
  canAttach,
  onPickFiles,
  onPickTemplate,
  onAddBlogTokens,
}: ComposerPlusMenuProps) {
  const [linkDialogOpen, setLinkDialogOpen] = useState(false);
  const [linkValue, setLinkValue] = useState("");
  const [linkError, setLinkError] = useState<string | null>(null);

  const handleLinkSubmit = useCallback(() => {
    const token = extractBlogToken(linkValue);
    if (!token) {
      setLinkError("رابط مدونة غير صالح");
      return;
    }
    onAddBlogTokens([token]);
    setLinkDialogOpen(false);
    setLinkValue("");
    setLinkError(null);
  }, [linkValue, onAddBlogTokens]);

  const handleLinkDialogChange = useCallback((open: boolean) => {
    setLinkDialogOpen(open);
    if (!open) {
      setLinkValue("");
      setLinkError(null);
    }
  }, []);

  return (
    <>
      <DropdownMenu dir="rtl">
        <DropdownMenuTrigger asChild>
          <Button
            variant="ghost"
            size="icon"
            className="h-10 w-10 shrink-0"
            disabled={disabled}
            aria-label="إضافة إلى الرسالة"
          >
            <Plus className="h-5 w-5" />
          </Button>
        </DropdownMenuTrigger>

        <DropdownMenuContent side="top" align="start" className="w-52">
          <DropdownMenuItem
            className="gap-2"
            disabled={!canAttach}
            onSelect={onPickFiles}
          >
            <Paperclip className="h-4 w-4 shrink-0 text-muted-foreground" />
            <span>مرفق</span>
          </DropdownMenuItem>

          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-2">
              <LayoutTemplate className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span>قالب</span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="max-h-72 w-64 overflow-y-auto">
              <TemplateMenuList onPick={onPickTemplate} />
            </DropdownMenuSubContent>
          </DropdownMenuSub>

          <DropdownMenuSub>
            <DropdownMenuSubTrigger className="gap-2" disabled={!canAttach}>
              <BookText className="h-4 w-4 shrink-0 text-muted-foreground" />
              <span>مدونة</span>
            </DropdownMenuSubTrigger>
            <DropdownMenuSubContent className="w-64">
              <DropdownMenuItem
                className="gap-2"
                // Defer past the menu's close/unmount so its focus-restore
                // doesn't fight the dialog's focus trap (Radix dropdown→dialog
                // pointer-events trap).
                onSelect={() => setTimeout(() => setLinkDialogOpen(true), 0)}
              >
                <Link2 className="h-4 w-4 shrink-0 text-muted-foreground" />
                <span>إضافة رابط...</span>
              </DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
                من مدوناتي
              </DropdownMenuLabel>
              <div className="max-h-60 overflow-y-auto">
                <MyBlogMenuList onPick={(token) => onAddBlogTokens([token])} />
              </div>
            </DropdownMenuSubContent>
          </DropdownMenuSub>
        </DropdownMenuContent>
      </DropdownMenu>

      <Dialog open={linkDialogOpen} onOpenChange={handleLinkDialogChange}>
        <DialogContent dir="rtl" className="max-w-md">
          <DialogHeader>
            <DialogTitle>إضافة مدونة برابط</DialogTitle>
          </DialogHeader>

          <div className="space-y-2">
            <input
              dir="ltr"
              type="url"
              value={linkValue}
              onChange={(e) => {
                setLinkValue(e.target.value);
                setLinkError(null);
              }}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleLinkSubmit();
                }
              }}
              placeholder="https://rayhanai.com/blog/..."
              autoFocus
              className="w-full rounded-lg border bg-muted/50 px-3 py-2 text-sm placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-ring"
            />
            {linkError && (
              <p className="text-xs text-destructive">{linkError}</p>
            )}
          </div>

          <DialogFooter className="gap-2">
            <Button
              variant="outline"
              onClick={() => handleLinkDialogChange(false)}
            >
              إلغاء
            </Button>
            <Button onClick={handleLinkSubmit} disabled={!linkValue.trim()}>
              إضافة
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
