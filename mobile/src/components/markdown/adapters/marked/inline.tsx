import type { MarkedToken, Token } from "marked";
import type { ReactNode } from "react";
import { Text } from "react-native";

import { fontFamilyFor } from "@/theme/fonts";

import { decodeEntities } from "../../entities";
import { citationNumber, isExternalHref, LRI, openExternal, PDI } from "../../links";
import { MONO, type RenderCtx } from "./context";

/** Inline tokens → nested <Text>. Nested Text inherits family/size/colour
 *  from the enclosing AppText, so only the delta is styled here; weight is
 *  always a family switch (Android ignores fontWeight on custom fonts). */
export function renderInline(tokens: Token[] | undefined, ctx: RenderCtx, keyPrefix = "i"): ReactNode[] {
  if (!tokens) return [];
  return tokens.map((raw, i) => {
    const token = raw as MarkedToken;
    const key = `${keyPrefix}${i}`;
    switch (token.type) {
      case "text":
        return token.tokens?.length ? (
          <Text key={key}>{renderInline(token.tokens, ctx, key)}</Text>
        ) : (
          decodeEntities(token.text)
        );
      case "escape":
        return token.text;
      case "strong":
        return (
          <Text key={key} style={{ fontFamily: fontFamilyFor[700] }}>
            {renderInline(token.tokens, ctx, key)}
          </Text>
        );
      case "em":
        // Naskh has no italic face; an oblique Arabic glyph reads as broken.
        return (
          <Text key={key} style={{ color: ctx.colours.textSecondary }}>
            {renderInline(token.tokens, ctx, key)}
          </Text>
        );
      case "del":
        return (
          <Text key={key} style={{ textDecorationLine: "line-through" }}>
            {renderInline(token.tokens, ctx, key)}
          </Text>
        );
      case "codespan":
        return (
          <Text
            key={key}
            style={{ fontFamily: MONO, fontSize: 14, color: ctx.colours.textPrimary, backgroundColor: ctx.colours.surface2 }}
          >
            {`${LRI} ${token.text} ${PDI}`}
          </Text>
        );
      case "br":
        return "\n";
      case "link":
        return renderLink(token.href, token.tokens, ctx, key);
      case "html":
        return /^<br\s*\/?>$/i.test(token.text.trim()) ? "\n" : null;
      case "image":
        return null; // stripped upstream (K1 stripMarkdownImages); never rendered inline
      default:
        return null;
    }
  });
}

function renderLink(href: string, children: Token[], ctx: RenderCtx, key: string): ReactNode {
  const n = citationNumber(href);
  if (n !== null) return <CitationPill key={key} n={n} ctx={ctx} />;
  if (!isExternalHref(href)) {
    // Any other scheme is dropped — the text stays, the link does not (K3).
    return <Text key={key}>{renderInline(children, ctx, key)}</Text>;
  }
  return (
    <Text
      key={key}
      accessibilityRole="link"
      onPress={() => openExternal(href)}
      style={{ color: ctx.colours.primary, textDecorationLine: "underline" }}
    >
      {renderInline(children, ctx, key)}
    </Text>
  );
}

/** «[N]» marker (web CitationMarker): LTR-isolated, primary on a soft brand
 *  ground, muted and inert when the message has no search artifact. */
function CitationPill({ n, ctx }: { n: number; ctx: RenderCtx }) {
  const onPress = ctx.onCitationPress;
  return (
    <Text
      accessibilityRole={onPress ? "link" : undefined}
      accessibilityLabel={`فتح المرجع رقم ${n}`}
      onPress={onPress ? () => onPress(n) : undefined}
      suppressHighlighting={false}
      style={{
        fontFamily: fontFamilyFor[600],
        fontSize: 13,
        color: ctx.colours.primary,
        backgroundColor: ctx.colours.brandSoftBg,
        opacity: onPress ? 1 : 0.7,
      }}
    >
      {`${LRI} [${n}] ${PDI}`}
    </Text>
  );
}
