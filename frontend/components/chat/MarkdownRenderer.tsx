"use client";

import { memo, useMemo } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeHighlight from "rehype-highlight";
import { cn } from "@/lib/utils";
import { CodeBlock } from "@/components/chat/CodeBlock";
import { CitationMarker } from "@/components/chat/CitationMarker";
import { remarkCitations } from "@/lib/markdown/remark-citations";

// Import highlight.js dark theme
import "highlight.js/styles/github-dark.css";

interface MarkdownRendererProps {
  content: string;
  className?: string;
  /**
   * When set, ``[n]`` markers inside text nodes become clickable
   * ``<CitationMarker>`` buttons. Omitting the prop (or passing undefined)
   * leaves citations as plain text — used for messages without artifacts
   * or other non-deep_search content.
   */
  onCitationClick?: (n: number) => void;
}

function buildMarkdownComponents(
  onCitationClick: ((n: number) => void) | undefined,
): Components {
  return {
    // --- Citation markers ([n] / [n,m,...]) ---
    // The ``remarkCitations`` plugin (registered below) transforms the
    // mdast text nodes that contain citation tokens into ``<cite data-n="n">``
    // hast elements. react-markdown then dispatches each one here. This
    // works inside paragraphs, list items, table cells, blockquotes, bold
    // / italic / nested formatting — every container — because the
    // interception happens at the AST level, not at the per-container
    // component layer. Code-block contents are NOT touched (they live on
    // ``code`` mdast nodes' ``value``, never as text children).
    cite({ node, children }) {
      // Hast properties are camelized for HTML attrs but data-* attrs land
      // on ``properties`` keyed by the dash-cased form. Read defensively.
      const props = (node as { properties?: Record<string, unknown> } | undefined)
        ?.properties ?? {};
      const raw = (props["data-n"] ?? (props as Record<string, unknown>).dataN) as
        | string
        | number
        | undefined;
      const n =
        typeof raw === "number" ? raw : Number.parseInt(String(raw ?? ""), 10);
      if (!Number.isFinite(n) || n <= 0) {
        // Defensive: malformed cite. Render the inner text (if any) so we
        // don't blank-out user-visible content.
        return <>{children}</>;
      }
      return <CitationMarker n={n} onClick={onCitationClick} />;
    },

    // --- Code blocks & inline code ---
    pre({ children }) {
      // react-markdown wraps code blocks in <pre><code>. We intercept <pre>
      // and let the child <code> render via CodeBlock.
      return <>{children}</>;
    },

    code({ className, children, ...props }) {
      // Detect fenced code block by the presence of a language class
      const match = /language-(\w+)/.exec(className || "");
      const isBlock = match || (typeof children === "string" && children.includes("\n"));

      if (isBlock) {
        const language = match ? match[1] : undefined;
        const codeString = String(children).replace(/\n$/, "");
        return <CodeBlock language={language}>{codeString}</CodeBlock>;
      }

      // Inline code — render children directly (no citation rewrite here).
      return (
        <code
          className={cn(
            "rounded px-1.5 py-0.5 text-[13px] font-mono",
            "bg-muted/60 text-foreground",
            className
          )}
          dir="ltr"
          {...props}
        >
          {children}
        </code>
      );
    },

    // --- Tables ---
    table({ children }) {
      return (
        <div className="my-3 overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm border-collapse">{children}</table>
        </div>
      );
    },
    thead({ children }) {
      return <thead className="bg-muted/50">{children}</thead>;
    },
    th({ children }) {
      return (
        <th className="border-b border-border px-3 py-2 text-start font-semibold text-sm">
          {children}
        </th>
      );
    },
    td({ children }) {
      return (
        <td className="border-b border-border/50 px-3 py-2 text-sm">
          {children}
        </td>
      );
    },
    tr({ children }) {
      return <tr className="hover:bg-muted/30 transition-colors">{children}</tr>;
    },

    // --- Links ---
    a({ href, children }) {
      return (
        <a
          href={href}
          target="_blank"
          rel="noopener noreferrer"
          className="text-primary underline underline-offset-2 hover:text-primary/80 transition-colors"
        >
          {children}
        </a>
      );
    },

    // --- Blockquotes ---
    blockquote({ children }) {
      return (
        <blockquote
          className={cn(
            "my-3 border-e-4 border-primary/30 pe-4 ps-0",
            "text-muted-foreground italic"
          )}
        >
          {children}
        </blockquote>
      );
    },

    // --- Lists ---
    ul({ children }) {
      return (
        <ul className="my-2 list-disc pe-0 ps-0 me-5 ms-0 space-y-1">
          {children}
        </ul>
      );
    },
    ol({ children }) {
      return (
        <ol className="my-2 list-decimal pe-0 ps-0 me-5 ms-0 space-y-1">
          {children}
        </ol>
      );
    },
    li({ children }) {
      return <li className="text-base leading-relaxed">{children}</li>;
    },

    // --- Headers (scaled down for chat context) ---
    h1({ children }) {
      return (
        <h1 className="text-lg font-bold mt-4 mb-2 text-foreground">
          {children}
        </h1>
      );
    },
    h2({ children }) {
      return (
        <h2 className="text-base font-bold mt-3 mb-1.5 text-foreground">
          {children}
        </h2>
      );
    },
    h3({ children }) {
      return (
        <h3 className="text-base font-semibold mt-3 mb-1 text-foreground">
          {children}
        </h3>
      );
    },
    h4({ children }) {
      return (
        <h4 className="text-sm font-semibold mt-2 mb-1 text-foreground">
          {children}
        </h4>
      );
    },
    h5({ children }) {
      return (
        <h5 className="text-sm font-medium mt-2 mb-1 text-foreground">
          {children}
        </h5>
      );
    },
    h6({ children }) {
      return (
        <h6 className="text-sm font-medium mt-2 mb-1 text-muted-foreground">
          {children}
        </h6>
      );
    },

    // --- Paragraphs ---
    p({ children }) {
      return (
        <p className="text-base leading-relaxed mb-2 last:mb-0">{children}</p>
      );
    },

    // --- Horizontal rules ---
    hr() {
      return <hr className="my-4 border-border" />;
    },

    // --- Strong & emphasis ---
    strong({ children }) {
      return <strong className="font-semibold">{children}</strong>;
    },
    em({ children }) {
      return <em className="italic">{children}</em>;
    },
  };
}

// ``remarkCitations`` runs AFTER ``remarkGfm`` so any GFM-specific text node
// shapes (e.g. inside table cells) are already in their final form before we
// split them on ``[n]`` tokens.
const remarkPlugins = [remarkGfm, remarkCitations];
const rehypePlugins = [rehypeHighlight];

export const MarkdownRenderer = memo(function MarkdownRenderer({
  content,
  className,
  onCitationClick,
}: MarkdownRendererProps) {
  // Memoize the content to avoid unnecessary re-parses on parent re-renders
  // that don't change the content string
  const stableContent = useMemo(() => content, [content]);

  // Re-bind the component map only when the citation click handler changes.
  // The handler identity is generally stable per message, so this is cheap.
  const components = useMemo(
    () => buildMarkdownComponents(onCitationClick),
    [onCitationClick],
  );

  return (
    <div
      className={cn(
        "markdown-content text-base leading-relaxed",
        // Ensure the markdown inherits the RTL direction from parent
        // but code blocks override to LTR via their own dir attribute
        className
      )}
    >
      <ReactMarkdown
        remarkPlugins={remarkPlugins}
        rehypePlugins={rehypePlugins}
        components={components}
      >
        {stableContent}
      </ReactMarkdown>
    </div>
  );
});
