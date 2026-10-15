import type { Tokens } from "marked";
import { ScrollView, StyleSheet, View } from "react-native";

import { AppText } from "@/components/AppText";

import type { RenderCtx } from "./context";
import { renderInline } from "./inline";

const MIN_COL = 120;
const MAX_COL = 260;
// ~px per character at the `sm` tier (15px Naskh) + cell padding.
const PX_PER_CHAR = 7;
const CELL_PAD = 24;

/** Column width from the longest cell text — no measuring pass, so every row
 *  lines up on the first frame. Long cells wrap inside MAX_COL. */
function columnWidths(table: Tokens.Table): number[] {
  return table.header.map((head, col) => {
    const longest = Math.max(head.text.length, ...table.rows.map((row) => row[col]?.text.length ?? 0));
    return Math.min(MAX_COL, Math.max(MIN_COL, Math.round(longest * PX_PER_CHAR + CELL_PAD)));
  });
}

/** GFM table: a horizontal ScrollView of rows. In an RTL layout the first
 *  column sits at the start (right) and the table scrolls towards the end. */
export function MarkdownTable({ table, ctx }: { table: Tokens.Table; ctx: RenderCtx }) {
  const { colours } = ctx;
  const widths = columnWidths(table);

  const renderRow = (cells: Tokens.TableCell[], header: boolean, rowKey: string, last: boolean) => (
    <View
      key={rowKey}
      style={[
        styles.row,
        { borderBottomColor: colours.border, borderBottomWidth: last ? 0 : StyleSheet.hairlineWidth },
        header && { backgroundColor: colours.surface2 },
      ]}
    >
      {widths.map((width, col) => {
        const cell = cells[col];
        return (
          <View
            key={col}
            style={[
              styles.cell,
              { width, borderStartColor: colours.border, borderStartWidth: col === 0 ? 0 : StyleSheet.hairlineWidth },
            ]}
          >
            <AppText variant="sm" weight={header ? 600 : 400} align={cell?.align === "center" ? "center" : "auto"} selectable={ctx.selectable}>
              {cell ? renderInline(cell.tokens, ctx, `${rowKey}c${col}`) : null}
            </AppText>
          </View>
        );
      })}
    </View>
  );

  return (
    <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.scroll}>
      <View style={[styles.frame, { borderColor: colours.border, backgroundColor: colours.surface1 }]}>
        {renderRow(table.header, true, "h", table.rows.length === 0)}
        {table.rows.map((row, r) => renderRow(row, false, `r${r}`, r === table.rows.length - 1))}
      </View>
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  scroll: { flexGrow: 1 },
  frame: { borderWidth: 1, borderRadius: 8, overflow: "hidden" },
  row: { flexDirection: "row" },
  cell: { paddingHorizontal: 12, paddingVertical: 8 },
});
