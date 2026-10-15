import { lazy, Suspense, useMemo } from "react";
import { UIManager } from "react-native";

import { AppText } from "@/components/AppText";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import type { Colours } from "@/theme/tokens.generated";
import { MAX_FONT_SCALE, typeVariants } from "@/theme/typography";

import { citationNumber, LRI, openExternal, PDI } from "../links";
import type { MarkdownAdapterProps } from "../types";

// (A) react-native-enriched-markdown — native (Fabric) md4c renderer. It is a
// native module: a build without it has no `EnrichedMarkdownText` component,
// so the package is only evaluated (lazy import) once the component is known
// to be registered. Needs a NEW dev build to actually render.
const NATIVE_NAME = "EnrichedMarkdownText";

const EnrichedMarkdownText = lazy(() =>
  import("react-native-enriched-markdown").then((m) => ({ default: m.EnrichedMarkdownText })),
);

type EnrichedProps = React.ComponentProps<typeof EnrichedMarkdownText>;
type MarkdownStyle = NonNullable<EnrichedProps["markdownStyle"]>;

export function isEnrichedAvailable(): boolean {
  try {
    return UIManager.hasViewManagerConfig(NATIVE_NAME);
  } catch {
    return false;
  }
}

function buildStyle(colours: Colours, reader: boolean): MarkdownStyle {
  const body = reader ? typeVariants.read : typeVariants.base;
  const regular = fontFamilyFor[400];
  const bold = fontFamilyFor[700];
  const block = { fontFamily: regular, fontWeight: "normal", color: colours.textPrimary, ...body };
  const heading = (size: { fontSize: number; lineHeight: number }) => ({
    ...block,
    ...size,
    fontFamily: bold,
    marginTop: 4,
    marginBottom: 8,
  });
  return {
    paragraph: { ...block, marginBottom: 12 },
    h1: heading(typeVariants.xl),
    h2: heading(typeVariants.lg),
    h3: heading(typeVariants.base),
    h4: { ...heading(typeVariants.base), fontFamily: fontFamilyFor[600] },
    h5: { ...heading(typeVariants.base), fontFamily: fontFamilyFor[600] },
    h6: { ...heading(typeVariants.base), fontFamily: fontFamilyFor[600] },
    // Android ignores fontWeight on custom fonts: a per-weight family, no synthetic bold on top.
    strong: { fontFamily: bold, fontWeight: "normal" },
    em: { fontFamily: regular, fontStyle: "normal", color: colours.textSecondary },
    list: { ...block, bulletColor: colours.textMuted, markerColor: colours.textMuted, marginBottom: 12 },
    blockquote: { ...block, borderColor: colours.accentLine, borderWidth: 3, gapWidth: 12, marginBottom: 12 },
    link: { color: colours.primary, underline: true },
    linkVariants: {
      "^cite:": { color: colours.primary, backgroundColor: colours.brandSoftBg, underline: false, fontFamily: fontFamilyFor[600] },
    },
    code: { fontSize: 14, color: colours.textPrimary, backgroundColor: colours.surface2, borderColor: colours.border },
    codeBlock: {
      fontSize: 13,
      lineHeight: 20,
      color: colours.codeFg,
      backgroundColor: colours.codeBg,
      borderColor: colours.codeBorder,
      borderWidth: 1,
      borderRadius: 8,
      padding: 12,
      marginBottom: 12,
    },
    table: {
      ...block,
      fontSize: typeVariants.sm.fontSize,
      lineHeight: typeVariants.sm.lineHeight,
      headerFontFamily: fontFamilyFor[600],
      headerBackgroundColor: colours.surface2,
      headerTextColor: colours.textPrimary,
      rowEvenBackgroundColor: colours.surface1,
      rowOddBackgroundColor: colours.surface1,
      borderColor: colours.border,
      borderWidth: 1,
      borderRadius: 8,
      cellPaddingHorizontal: 12,
      cellPaddingVertical: 8,
      marginBottom: 12,
    },
    thematicBreak: { color: colours.border, height: 1, marginTop: 4, marginBottom: 12 },
  };
}

/** `[[4]](cite:4)` → link text wrapped in LRI…PDI so the brackets stay on their number. */
function isolateCitations(markdown: string): string {
  return markdown.replace(/\[\[(\d+)\]\]\(cite:(\d+)\)/g, (_m, n: string, href: string) => `[${LRI}[${n}]${PDI}](cite:${href})`);
}

export function EnrichedMarkdown({ content, streaming = false, onCitationPress, variant }: MarkdownAdapterProps) {
  const { colours } = useTheme();
  const reader = variant === "reader";
  const markdownStyle = useMemo(() => buildStyle(colours, reader), [colours, reader]);
  const markdown = useMemo(() => isolateCitations(content), [content]);

  if (!isEnrichedAvailable()) {
    return (
      <AppText variant="sm" tone="destructive">
        المحرّك (A) غير مضمَّن في هذا البناء — يلزم بناء تطوير جديد.
      </AppText>
    );
  }

  return (
    <Suspense fallback={null}>
      <EnrichedMarkdownText
        markdown={markdown}
        flavor="github"
        writingDirection="rtl"
        markdownStyle={markdownStyle}
        containerStyle={{ direction: "rtl" }}
        selectable={reader}
        allowFontScaling
        maxFontSizeMultiplier={MAX_FONT_SCALE}
        streamingAnimation={streaming}
        streamingConfig={{ tableMode: "progressive", codeBlockMode: "progressive" }}
        onLinkPress={({ url }) => {
          const n = citationNumber(url);
          if (n !== null) onCitationPress?.(n);
          else openExternal(url);
        }}
      />
    </Suspense>
  );
}
