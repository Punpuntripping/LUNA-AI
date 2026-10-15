import type { MarkedToken, Token, Tokens } from "marked";
import { memo, type ReactNode } from "react";
import { ScrollView, StyleSheet, Text, View } from "react-native";

import { AppText } from "@/components/AppText";
import type { FontWeight } from "@/theme/fonts";
import type { TypeVariant } from "@/theme/typography";

import { MONO, type RenderCtx } from "./context";
import { renderInline } from "./inline";
import { MarkdownTable } from "./Table";

const HEADING: Record<number, { variant: TypeVariant; weight: FontWeight }> = {
  1: { variant: "xl", weight: 700 },
  2: { variant: "lg", weight: 700 },
  3: { variant: "base", weight: 700 },
};
const BULLETS = ["•", "◦", "▪"];

/** One top-level block. Memoized on token identity: settled tokens (the
 *  streaming prefix) keep their identity, so only the tail re-renders. */
export const Block = memo(function Block({ token, ctx }: { token: Token; ctx: RenderCtx }) {
  return renderBlock(token, ctx, 0);
});

export function renderBlocks(tokens: Token[], ctx: RenderCtx, depth: number): ReactNode[] {
  return tokens.map((t, i) => <View key={i}>{renderBlock(t, ctx, depth)}</View>);
}

function renderBlock(raw: Token, ctx: RenderCtx, depth: number): ReactNode {
  const token = raw as MarkedToken;
  switch (token.type) {
    case "paragraph":
    case "text":
      return (
        <AppText variant={ctx.body} selectable={ctx.selectable}>
          {"tokens" in token && token.tokens ? renderInline(token.tokens, ctx) : token.text}
        </AppText>
      );
    case "heading": {
      const h = HEADING[token.depth] ?? { variant: ctx.body, weight: 600 };
      return (
        <AppText variant={h.variant} weight={h.weight} accessibilityRole="header" selectable={ctx.selectable} style={styles.heading}>
          {renderInline(token.tokens, ctx)}
        </AppText>
      );
    }
    case "list":
      return <List list={token} ctx={ctx} depth={depth} />;
    case "blockquote":
      return (
        <View style={[styles.quote, { borderStartColor: ctx.colours.accentLine }]}>
          {renderBlocks(token.tokens, ctx, depth)}
        </View>
      );
    case "code":
      return <CodeBlock code={token.text} lang={token.lang} ctx={ctx} />;
    case "table":
      return <MarkdownTable table={token} ctx={ctx} />;
    case "hr":
      return <View style={[styles.hr, { backgroundColor: ctx.colours.border }]} />;
    default:
      return null; // space, html blocks, defs — not rendered (same as react-markdown defaults)
  }
}

function List({ list, ctx, depth }: { list: Tokens.List; ctx: RenderCtx; depth: number }) {
  const start = typeof list.start === "number" ? list.start : 1;
  return (
    <View style={styles.list}>
      {list.items.map((item, i) => (
        <View key={i} style={styles.item}>
          <AppText variant={ctx.body} weight={list.ordered ? 600 : 400} tone="muted" style={styles.marker}>
            {list.ordered ? `${start + i}.` : BULLETS[depth % BULLETS.length]}
          </AppText>
          <View style={styles.itemBody}>{renderBlocks(item.tokens, ctx, depth + 1)}</View>
        </View>
      ))}
    </View>
  );
}

/** Code is LTR whatever the app direction; long lines scroll, never wrap. */
function CodeBlock({ code, lang, ctx }: { code: string; lang?: string; ctx: RenderCtx }) {
  const { colours } = ctx;
  return (
    <View style={[styles.code, { backgroundColor: colours.codeBg, borderColor: colours.codeBorder }]}>
      {lang ? (
        <View style={[styles.codeHead, { backgroundColor: colours.codeHeadBg }]}>
          <Text style={[styles.codeLang, { color: colours.codeMuted }]}>{lang}</Text>
        </View>
      ) : null}
      <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.ltr}>
        <Text selectable style={[styles.codeText, { color: colours.codeFg }]}>
          {code.replace(/\n$/, "")}
        </Text>
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  heading: { marginTop: 4 },
  list: { gap: 4 },
  item: { flexDirection: "row", gap: 6 },
  marker: { minWidth: 22, textAlign: "center" },
  itemBody: { flex: 1, gap: 6 },
  quote: { borderStartWidth: 3, paddingStart: 12, gap: 8 },
  hr: { height: StyleSheet.hairlineWidth, marginVertical: 4 },
  code: { borderWidth: 1, borderRadius: 8, overflow: "hidden" },
  codeHead: { paddingHorizontal: 12, paddingVertical: 6, direction: "ltr" },
  codeLang: { fontFamily: MONO, fontSize: 12, writingDirection: "ltr", textAlign: "left" },
  ltr: { direction: "ltr" },
  codeText: { fontFamily: MONO, fontSize: 13, lineHeight: 20, padding: 12, writingDirection: "ltr", textAlign: "left" },
});
