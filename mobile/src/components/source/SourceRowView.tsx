import { Image } from "expo-image";
import { StyleSheet, View } from "react-native";

import { ActionChip } from "@/components/artifact/ActionChip";
import { AppText } from "@/components/AppText";
import { Markdown } from "@/components/markdown/Markdown";
import { useTheme } from "@/theme/ThemeProvider";

import { openInBrowser } from "./exits";
import type { SourceRow } from "./source-rows";

/** One row of a revealed source (see `sourceRows`). */
export function SourceRowView({ row }: { row: SourceRow }) {
  const { colours, radius } = useTheme();

  switch (row.kind) {
    case "label":
      return (
        <AppText variant="xs" weight={600} tone="muted" style={styles.gap}>
          {row.text}
        </AppText>
      );
    case "note":
      return (
        <AppText variant="sm" tone="muted" style={styles.gap}>
          {row.text}
        </AppText>
      );
    case "md":
      return (
        <View style={styles.gap}>
          <Markdown content={row.text} variant="reader" />
        </View>
      );
    case "image": {
      const ratio = row.width > 0 && row.height > 0 ? row.width / row.height : 4 / 3;
      return (
        <View style={styles.gap}>
          <Image
            source={{ uri: row.url }}
            style={[styles.image, { aspectRatio: ratio, borderRadius: radius.default, backgroundColor: colours.surface2 }]}
            contentFit="contain"
            transition={150}
            accessibilityLabel={row.description || row.title}
          />
          <AppText variant="sm" tone="secondary" style={styles.caption}>
            {row.title ? `الصورة ${row.n}: ${row.title}` : `الصورة ${row.n}`}
          </AppText>
        </View>
      );
    }
    case "provenance":
      return (
        <View style={[styles.provenance, styles.gap, { backgroundColor: colours.surface2, borderRadius: radius.default }]}>
          <AppText variant="xs" weight={600} tone="muted">
            المصدر
          </AppText>
          {row.citation.map((c) => (
            <AppText key={c.label} variant="sm" selectable>
              <AppText variant="sm" tone="muted">{`${c.label}: `}</AppText>
              {c.value}
            </AppText>
          ))}
          {row.links.length > 0 ? (
            <View style={styles.links}>
              {row.links.map((l) => (
                <ActionChip key={l.href} label={l.title} variant="ghost" onPress={() => openInBrowser(l.href)} />
              ))}
            </View>
          ) : null}
        </View>
      );
  }
}

const styles = StyleSheet.create({
  gap: { marginBottom: 12 },
  image: { width: "100%" },
  caption: { marginTop: 6 },
  provenance: { padding: 12, gap: 4 },
  links: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
});
