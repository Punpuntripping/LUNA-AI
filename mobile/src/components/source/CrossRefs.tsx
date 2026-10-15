import { useState } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import type { CrossRef } from "@contracts";

import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { readerCopy } from "./copy";

/** Corpus token → Arabic unit («madda» is how all 34k rows spell المادة). */
const UNIT_AR: Record<string, string> = { madda: "المادة", article: "المادة", appendix: "الملحق" };

function crossRefUnit(cr: CrossRef): string {
  const unit = UNIT_AR[cr.target_type] ?? cr.target_type ?? "";
  if (cr.target_number == null) return unit;
  return unit ? `${unit} ${cr.target_number}` : String(cr.target_number);
}

/** «الإحالات» — collapsed by default (a مادة can carry ten, dwarfing it). Never
 *  gated: rides the free list, so it renders on a refusal too. */
export function CrossRefs({ crossRefs }: { crossRefs: CrossRef[] }) {
  const { colours, radius } = useTheme();
  const [open, setOpen] = useState(false);
  if (crossRefs.length === 0) return null;

  return (
    <View style={[styles.root, { borderTopColor: colours.border }]}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        onPress={() => setOpen((v) => !v)}
        style={styles.toggle}
        testID="source-crossrefs-toggle"
      >
        <AppText variant="sm" weight={600} tone="secondary">
          {`${readerCopy.crossRefs} (${crossRefs.length}) ${open ? "▴" : "▾"}`}
        </AppText>
      </Pressable>
      {open
        ? crossRefs.map((cr, i) => {
            const heading = [cr.target_reg_title, crossRefUnit(cr)].filter(Boolean).join(" — ");
            return (
              <View key={i} style={[styles.item, { backgroundColor: colours.surface2, borderRadius: radius.default }]}>
                {heading ? (
                  <AppText variant="sm" weight={600}>
                    {heading}
                  </AppText>
                ) : null}
                {cr.content ? (
                  <AppText variant="sm" tone="secondary" selectable>
                    {cr.content}
                  </AppText>
                ) : null}
              </View>
            );
          })
        : null}
    </View>
  );
}

const styles = StyleSheet.create({
  root: { borderTopWidth: StyleSheet.hairlineWidth, marginTop: 16, paddingTop: 4, gap: 8 },
  toggle: { minHeight: 44, justifyContent: "center" },
  item: { paddingHorizontal: 10, paddingVertical: 8, gap: 2 },
});
