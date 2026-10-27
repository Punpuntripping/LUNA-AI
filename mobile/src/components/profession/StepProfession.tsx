import { Pressable, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";
import { SheetInput } from "@/components/composer-extras/parts";
import { Icon } from "@/components/Icon";
import { PROFESSION_GROUPS, STEP_PROFESSION, type ProfessionGroup } from "@/profession/content";
import {
  chipsFor,
  isOtherActive,
  pickChip,
  pickDecline,
  pickGroup,
  pickOther,
  typeOther,
  type ProfessionStepState,
} from "@/profession/logic";
import { withAlpha } from "@/theme/alpha";
import { useTheme } from "@/theme/ThemeProvider";

export interface StepProfessionProps {
  state: ProfessionStepState;
  onChange: (next: ProfessionStepState) => void;
  /** The «أخرى» field got / lost focus — the sheet keeps it above the keyboard. */
  onOtherFocus?: () => void;
  onOtherBlur?: () => void;
}

interface ChipCell {
  key: string;
  testID: string;
  label: string;
  selected: boolean;
  onPress: () => void;
}

/** Pairs for the 2-column grids (an odd tail gets an empty cell). */
function pairs<T>(items: readonly T[]): (T | null)[][] {
  const rows: (T | null)[][] = [];
  for (let i = 0; i < items.length; i += 2) rows.push([items[i], items[i + 1] ?? null]);
  return rows;
}

/**
 * «وش أقرب وصف لك؟» — the web StepProfession: 2×2 cards (قانوني top-right in
 * RTL, then رائد أعمال، مختص، فرد) + a quieter full-width «أفضل عدم الإجابة»
 * row. Picking مختص or فرد reveals the optional chips (with a free-text
 * «أخرى») BELOW the grid. The chips sit in a 2-column grid instead of the
 * web's wrapping row: every label takes its cell's width (flex: 1) so a large
 * font scale wraps it instead of clipping its last glyph (Android, QA F3).
 * State and saving live in ProfessionPromptHost.
 */
export function StepProfession({ state, onChange, onOtherFocus, onOtherBlur }: StepProfessionProps) {
  const { selection } = state;
  const chips = chipsFor(selection.group);
  const isOther = isOtherActive(state);
  const chipCells: ChipCell[] = chips
    ? [
        ...chips.map((chip, i) => ({
          key: chip,
          testID: `profession-chip-${i}`,
          label: chip,
          selected: !isOther && selection.label === chip,
          onPress: () => onChange(pickChip(state, chip)),
        })),
        {
          key: "other",
          testID: "profession-chip-other",
          label: STEP_PROFESSION.otherChip,
          selected: isOther,
          onPress: () => onChange(pickOther(state)),
        },
      ]
    : [];

  return (
    <View style={styles.root}>
      <View style={styles.headingBlock}>
        <AppText variant="base" weight={600} accessibilityRole="header">
          {STEP_PROFESSION.heading}
        </AppText>
        <AppText variant="sm" tone="muted">
          {STEP_PROFESSION.intro}
        </AppText>
      </View>

      <View accessibilityRole="radiogroup" accessibilityLabel={STEP_PROFESSION.heading} style={styles.choices}>
        {pairs(PROFESSION_GROUPS).map((row) => (
          <View key={row.map((g) => g?.key ?? "").join("-")} style={styles.gridRow}>
            {row.map((group) =>
              group ? (
                <GroupCard
                  key={group.key}
                  group={group}
                  selected={selection.group === group.key}
                  onPress={() => onChange(pickGroup(state, group.key))}
                />
              ) : (
                <View key="empty" style={styles.cell} />
              ),
            )}
          </View>
        ))}
        <DeclineRow selected={selection.group === "declined"} onPress={() => onChange(pickDecline())} />
      </View>

      {/* finer segment — only مختص and فرد have options; always optional */}
      {chips ? (
        <View style={styles.options}>
          <AppText variant="xs" tone="muted">
            {STEP_PROFESSION.optionsHint}
          </AppText>
          {pairs(chipCells).map((row) => (
            <View key={row.map((c) => c?.key ?? "").join("-")} style={styles.chipRow}>
              {row.map((cell) =>
                cell ? (
                  <Chip
                    key={cell.key}
                    label={cell.label}
                    selected={cell.selected}
                    onPress={cell.onPress}
                    testID={cell.testID}
                  />
                ) : (
                  <View key="empty" style={styles.cell} />
                ),
              )}
            </View>
          ))}
          {isOther ? (
            <SheetInput
              testID="profession-other-input"
              accessibilityLabel={STEP_PROFESSION.otherPlaceholder.replace("…", "")}
              value={selection.label ?? ""}
              onChangeText={(text) => onChange(typeOther(state, text))}
              placeholder={STEP_PROFESSION.otherPlaceholder}
              maxLength={120}
              // Only after a tap on «أخرى» — not when a stored free-text answer
              // is shown on open (the keyboard would cover the sheet).
              autoFocus={state.otherOpen}
              returnKeyType="done"
              onFocus={onOtherFocus}
              onBlur={onOtherBlur}
            />
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function GroupCard({ group, selected, onPress }: { group: ProfessionGroup; selected: boolean; onPress: () => void }) {
  const { colours, radius } = useTheme();
  return (
    <Pressable
      testID={`profession-group-${group.key}`}
      accessibilityRole="radio"
      accessibilityLabel={group.label}
      accessibilityHint={group.hint}
      accessibilityState={{ selected }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.cell,
        styles.card,
        {
          borderRadius: radius.xl,
          // selected: border-primary/60 + ring-1 (2px total, padding keeps the size)
          borderWidth: selected ? 2 : 1,
          padding: selected ? 15 : 16,
          borderColor: selected ? withAlpha(colours.primary, 0.6) : colours.border,
          backgroundColor: selected
            ? withAlpha(colours.primary, 0.05)
            : withAlpha(colours.surface2, pressed ? 0.5 : 0.3),
        },
      ]}
    >
      <View
        style={[
          styles.iconBox,
          { borderRadius: radius.default, backgroundColor: selected ? withAlpha(colours.primary, 0.15) : colours.surface2 },
        ]}
      >
        <Icon name={group.icon} size={20} colour={selected ? colours.primary : colours.textMuted} />
      </View>
      <AppText variant="sm" weight={600}>
        {group.label}
      </AppText>
      <AppText variant="xs" tone="muted">
        {group.hint}
      </AppText>
    </Pressable>
  );
}

function DeclineRow({ selected, onPress }: { selected: boolean; onPress: () => void }) {
  const { colours, radius } = useTheme();
  return (
    <Pressable
      testID="profession-decline"
      accessibilityRole="radio"
      accessibilityLabel={STEP_PROFESSION.declineLabel}
      accessibilityState={{ selected }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.decline,
        {
          borderRadius: radius.xl,
          borderColor: selected ? withAlpha(colours.primary, 0.6) : colours.border,
          backgroundColor: selected ? withAlpha(colours.primary, 0.05) : pressed ? colours.surface2 : "transparent",
        },
      ]}
    >
      <AppText variant="sm" align="center" style={[styles.fill, { color: selected ? colours.primary : colours.textMuted }]}>
        {STEP_PROFESSION.declineLabel}
      </AppText>
    </Pressable>
  );
}

function Chip({
  label,
  selected,
  onPress,
  testID,
}: {
  label: string;
  selected: boolean;
  onPress: () => void;
  testID?: string;
}) {
  const { colours, radius } = useTheme();
  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ selected }}
      onPress={onPress}
      style={({ pressed }) => [
        styles.cell,
        styles.chip,
        {
          borderRadius: radius.full,
          borderColor: selected ? withAlpha(colours.primary, 0.5) : colours.border,
          backgroundColor: selected
            ? withAlpha(colours.primary, 0.1)
            : withAlpha(colours.surface2, pressed ? 0.6 : 0.3),
        },
      ]}
    >
      <AppText variant="sm" align="center" style={[styles.fill, { color: selected ? colours.primary : colours.textMuted }]}>
        {label}
      </AppText>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: { gap: 16 },
  headingBlock: { gap: 4 },
  choices: { gap: 12 },
  gridRow: { flexDirection: "row", gap: 12 },
  // Equal columns; a card's / chip's width never comes from its own text.
  cell: { flex: 1 },
  card: { gap: 8 },
  iconBox: { alignSelf: "flex-start", width: 36, height: 36, alignItems: "center", justifyContent: "center" },
  decline: { borderWidth: 1, borderStyle: "dashed", minHeight: 44, paddingHorizontal: 16, paddingVertical: 10, justifyContent: "center" },
  options: { gap: 8 },
  chipRow: { flexDirection: "row", gap: 8 },
  chip: { borderWidth: 1, minHeight: 44, paddingHorizontal: 12, paddingVertical: 6, justifyContent: "center" },
  fill: { alignSelf: "stretch" },
});
