"use client";

interface HighlightedTextProps {
  text: string;
  highlight: string;
}

/**
 * Renders text with matching portions highlighted.
 * Case-insensitive matching, works with Arabic text.
 */
export function HighlightedText({ text, highlight }: HighlightedTextProps) {
  if (!highlight.trim()) return <>{text}</>;

  const regex = new RegExp(`(${highlight.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")})`, "gi");
  const parts = text.split(regex);

  return (
    <>
      {parts.map((part, i) =>
        regex.test(part) ? (
          <span
            key={i}
            className="bg-primary/20 text-primary rounded-sm px-0.5"
          >
            {part}
          </span>
        ) : (
          part
        )
      )}
    </>
  );
}
