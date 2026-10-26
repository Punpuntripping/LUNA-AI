import { useMemo } from "react";
import { StyleSheet, View } from "react-native";

import { splitStreamingContent } from "@contracts";

import { useTheme } from "@/theme/ThemeProvider";

import type { MarkdownAdapterProps } from "../types";
import { Block } from "./marked/blocks";
import type { RenderCtx } from "./marked/context";
import { lexMarkdown } from "./marked/lex";

/** (B) `marked` lexer + our own RN renderer — pure JS, runs in any build. */
export function MarkedMarkdown({ content, streaming = false, breaks = false, onCitationPress, variant }: MarkdownAdapterProps) {
  const { colours } = useTheme();
  const ctx = useMemo<RenderCtx>(
    () => ({ colours, body: variant === "reader" ? "read" : "base", selectable: variant === "reader", onCitationPress }),
    [colours, variant, onCitationPress],
  );

  // Streaming: settled prefix parsed once per boundary, tail re-parsed per flush (K4).
  const { prefix, tail } = streaming ? splitStreamingContent(content) : { prefix: content, tail: "" };
  const head = useMemo(() => lexMarkdown(prefix, breaks), [prefix, breaks]);
  const rest = useMemo(() => lexMarkdown(tail, breaks), [tail, breaks]);

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
