import { Lexer, type Token } from "marked";
import { useMemo } from "react";
import { StyleSheet, View } from "react-native";

import { splitStreamingContent } from "@contracts";

import { useTheme } from "@/theme/ThemeProvider";

import type { MarkdownAdapterProps } from "../types";
import { Block } from "./marked/blocks";
import type { RenderCtx } from "./marked/context";

const LEX_OPTIONS = { gfm: true, breaks: false } as const;

function lex(markdown: string): Token[] {
  return markdown ? Lexer.lex(markdown, LEX_OPTIONS).filter((t) => t.type !== "space") : [];
}

/** (B) `marked` lexer + our own RN renderer — pure JS, runs in any build. */
export function MarkedMarkdown({ content, streaming = false, onCitationPress, variant }: MarkdownAdapterProps) {
  const { colours } = useTheme();
  const ctx = useMemo<RenderCtx>(
    () => ({ colours, body: variant === "reader" ? "read" : "base", selectable: variant === "reader", onCitationPress }),
    [colours, variant, onCitationPress],
  );

  // Streaming: settled prefix parsed once per boundary, tail re-parsed per flush (K4).
  const { prefix, tail } = streaming ? splitStreamingContent(content) : { prefix: content, tail: "" };
  const head = useMemo(() => lex(prefix), [prefix]);
  const rest = useMemo(() => lex(tail), [tail]);

  return (
    <View style={styles.root}>
      {head.map((t, i) => (
        <Block key={`p${i}`} token={t} ctx={ctx} />
      ))}
      {rest.map((t, i) => (
        <Block key={`t${i}`} token={t} ctx={ctx} />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { gap: 12, direction: "rtl" },
});
