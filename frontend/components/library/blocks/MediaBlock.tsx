"use client";

import { useState } from "react";
import { Play } from "lucide-react";
import { cn } from "@/lib/utils";
import type { MediaBlockProps } from "@/types/library";

/**
 * Pull the 11-char YouTube id out of a watch/short/embed URL, or accept a bare
 * id verbatim. Returns null if nothing looks like an id.
 */
function extractYouTubeId(input: string): string | null {
  const trimmed = input.trim();
  if (/^[\w-]{11}$/.test(trimmed)) return trimmed;
  try {
    const url = new URL(trimmed);
    const host = url.hostname.replace(/^www\./, "");
    if (host === "youtu.be") {
      const id = url.pathname.slice(1).split("/")[0];
      return /^[\w-]{11}$/.test(id) ? id : null;
    }
    if (host.endsWith("youtube.com")) {
      const fromQuery = url.searchParams.get("v");
      if (fromQuery && /^[\w-]{11}$/.test(fromQuery)) return fromQuery;
      const match = url.pathname.match(/\/(?:embed|shorts|v)\/([\w-]{11})/);
      if (match) return match[1];
    }
  } catch {
    return null;
  }
  return null;
}

/**
 * Lazy YouTube embed — a click-to-load thumbnail FAÇADE. No `<iframe>` mounts
 * until the user clicks, so the page ships zero YouTube JS/network on load
 * (performance + Core Web Vitals). Client component (the click toggles state).
 * Renders nothing if the URL/id can't be parsed.
 */
export function MediaBlock({
  youtubeUrl,
  title,
  thumbnailUrl,
  className,
}: MediaBlockProps) {
  const [loaded, setLoaded] = useState(false);
  const videoId = extractYouTubeId(youtubeUrl);

  if (!videoId) return null;

  const poster =
    thumbnailUrl ?? `https://img.youtube.com/vi/${videoId}/hqdefault.jpg`;

  return (
    <div
      dir="rtl"
      className={cn(
        "relative aspect-video w-full overflow-hidden rounded-xl border border-border bg-black",
        className,
      )}
    >
      {loaded ? (
        <iframe
          src={`https://www.youtube-nocookie.com/embed/${videoId}?autoplay=1&rel=0`}
          title={title}
          allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
          allowFullScreen
          loading="lazy"
          className="h-full w-full"
        />
      ) : (
        <button
          type="button"
          onClick={() => setLoaded(true)}
          aria-label={`تشغيل الفيديو: ${title}`}
          className="group absolute inset-0 h-full w-full cursor-pointer"
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src={poster}
            alt={title}
            loading="lazy"
            className="h-full w-full object-cover transition-opacity group-hover:opacity-90"
          />
          <span className="absolute inset-0 bg-black/20 transition-colors group-hover:bg-black/30" />
          <span className="absolute inset-0 flex items-center justify-center">
            <span className="flex h-16 w-16 items-center justify-center rounded-full bg-primary/90 text-primary-foreground shadow-lg transition-transform group-hover:scale-105">
              <Play aria-hidden="true" className="h-7 w-7 translate-x-0.5" fill="currentColor" />
            </span>
          </span>
        </button>
      )}
    </div>
  );
}
