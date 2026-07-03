"use client";

import { useState } from "react";
import { useParams, useRouter } from "next/navigation";
import { MessagesSquare } from "lucide-react";
import { Button } from "@/components/ui/button";
import { BlogDestinationDialog } from "@/components/blog/BlogDestinationDialog";
import { useAuthStore } from "@/stores/auth-store";
import { setPendingIntent } from "@/lib/post-login-intent";

interface ChatWithBlogButtonProps {
  variant?: "default" | "secondary" | "outline" | "ghost";
  size?: "default" | "sm" | "lg" | "icon";
  className?: string;
}

/**
 * «اتحدث مع المدونة» (.claude/plans/blog_import.md §D4) — start a conversation
 * seeded with this blog post.
 *
 * The token comes from the route (both reading surfaces carry it:
 * ``/blog/[token]`` public, ``/blogs/[token]`` مدوناتي) — no prop drilling
 * through the views. Auth-aware:
 *   - authed  → destination picker dialog (new / existing conversation)
 *   - anon    → stash a post-login intent + go to /login; AuthGuard resumes
 *               the flow after any successful sign-in (email, signup, OAuth).
 */
export function ChatWithBlogButton({
  variant = "secondary",
  size = "sm",
  className,
}: ChatWithBlogButtonProps) {
  const params = useParams<{ token?: string }>();
  const token = typeof params?.token === "string" ? params.token : undefined;
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated);
  const router = useRouter();
  const [dialogOpen, setDialogOpen] = useState(false);

  if (!token) return null;

  function handleClick() {
    if (!token) return;
    if (!isAuthenticated) {
      setPendingIntent({ type: "chat_with_blog", token });
      router.push("/login");
      return;
    }
    setDialogOpen(true);
  }

  return (
    <>
      <Button
        type="button"
        variant={variant}
        size={size}
        onClick={handleClick}
        className={className}
      >
        <MessagesSquare className="h-3.5 w-3.5" />
        اتحدث مع المدونة
      </Button>
      {dialogOpen && (
        <BlogDestinationDialog
          token={token}
          open={dialogOpen}
          onOpenChange={setDialogOpen}
        />
      )}
    </>
  );
}
