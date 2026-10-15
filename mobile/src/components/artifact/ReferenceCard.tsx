import { StyleSheet, View } from "react-native";

import {
  referenceLabel,
  referenceLibraryExitLabel,
  referencePrimaryUrl,
  usableDocType,
  type Reference,
  type ReferenceDomain,
} from "@contracts";

import { AppText } from "@/components/AppText";
import { libraryWebUrl, openInBrowser } from "@/components/source/exits";
import { LRI, PDI } from "@/components/markdown/links";
import { useTheme } from "@/theme/ThemeProvider";

import { ActionChip } from "./ActionChip";

/** The card's type chip per wing — pinned to the web's ReferencePanel labels. */
const DOMAIN_LABEL: Record<ReferenceDomain, string> = {
  regulations: "نظام",
  cases: "قضية",
  compliance: "خدمة حكومية",
  circulars: "تعميم",
  articles: "مادة",
  regulation_docs: "نظام",
};

export function referenceTypeLabel(ref: Reference): string {
  const docType = usableDocType(ref);
  if (docType) return docType;
  const base = DOMAIN_LABEL[ref.domain] ?? DOMAIN_LABEL.regulations;
  const articleNum = ref.domain === "articles" ? (ref.article_num ?? "").trim() : "";
  return articleNum ? `${base} ${articleNum}` : base;
}

export interface ReferenceCardProps {
  reference: Reference;
  /** «عرض المصدر» — omitted inside the reader (already there) and when the
   *  reference has no buildable source (`has_source` false). */
  onViewSource?: () => void;
  /** The reader shows its own exits row; the card then hides its pair. */
  showExits?: boolean;
  /** Snippet lines; `undefined` = all. */
  snippetLines?: number;
}

/**
 * One numbered citation. Exits are exactly two, as on the web: «فتح ال… في
 * ريحان» (filled — the one that keeps the reader in ريحان; dropped when there
 * is no published page) and «فتح المصدر الرسمي». Both free navigation.
 */
export function ReferenceCard({ reference, onViewSource, showExits = true, snippetLines = 2 }: ReferenceCardProps) {
  const { colours, radius } = useTheme();
  const libraryUrl = libraryWebUrl(reference.library_url);
  const officialUrl = referencePrimaryUrl(reference);
  const high = reference.relevance === "high";

  return (
    <View
      style={[styles.card, { backgroundColor: colours.surface1, borderColor: colours.border, borderRadius: radius.default }]}
      testID={`reference-card-${reference.n}`}
    >
      <View style={styles.row}>
        <View style={[styles.badge, { backgroundColor: colours.surface2 }]}>
          <AppText variant="xs" weight={600}>
            {`${LRI}${reference.n}${PDI}`}
          </AppText>
        </View>
        <View style={styles.main}>
          <View style={styles.meta}>
            <AppText variant="xs" tone="muted" weight={500}>
              {referenceTypeLabel(reference)}
            </AppText>
            <View
              style={[styles.dot, { backgroundColor: high ? colours.primary : colours.warningFg }]}
              accessibilityLabel={high ? "صلة عالية" : "صلة متوسطة"}
            />
            {reference.source_wi ? (
              <View style={[styles.wi, { backgroundColor: colours.surface2 }]}>
                <AppText variant="xs" tone="muted">{`من ${LRI}${reference.source_wi}${PDI}`}</AppText>
              </View>
            ) : null}
          </View>
          <AppText variant="sm" weight={600}>
            {referenceLabel(reference)}
          </AppText>
          {reference.snippet ? (
            <AppText variant="xs" tone="secondary" numberOfLines={snippetLines}>
              {reference.snippet}
            </AppText>
          ) : null}
          {onViewSource || showExits ? (
            <View style={styles.actions}>
              {onViewSource && reference.has_source ? (
                <ActionChip label="عرض المصدر" variant="ghost" onPress={onViewSource} testID={`reference-view-${reference.n}`} />
              ) : null}
              {showExits && libraryUrl ? (
                <ActionChip label={referenceLibraryExitLabel(reference)} variant="filled" onPress={() => openInBrowser(libraryUrl)} />
              ) : null}
              {showExits && officialUrl ? (
                <ActionChip label="فتح المصدر الرسمي" variant="ghost" onPress={() => openInBrowser(officialUrl)} />
              ) : null}
            </View>
          ) : null}
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: StyleSheet.hairlineWidth, paddingHorizontal: 12, paddingVertical: 10 },
  row: { flexDirection: "row", alignItems: "flex-start", gap: 10 },
  badge: { minWidth: 28, height: 28, borderRadius: 8, alignItems: "center", justifyContent: "center", paddingHorizontal: 6 },
  main: { flex: 1, gap: 4 },
  meta: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  dot: { width: 6, height: 6, borderRadius: 3 },
  wi: { borderRadius: 4, paddingHorizontal: 4 },
  actions: { flexDirection: "row", flexWrap: "wrap", gap: 6, marginTop: 4 },
});
