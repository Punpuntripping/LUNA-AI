import { cn } from "@/lib/utils";

/**
 * One table lifted back out of a corpus chunk — the grid a statute printed,
 * where the reading surface has been showing a flattened bullet list.
 *
 * Every table in the regulation corpus was OCR'd and then converted to prose
 * before ingestion (that is what BM25 indexes and what the model reads, and it
 * stays that way). The original `<table>` markup survived beside it, and the
 * body the READER gets swaps each table back in for a whole-line `TBL_…`
 * token. `toLegalBlocks` resolves those tokens; this renders what it resolved.
 *
 * Three things this wrapper has to get right:
 *
 * 1. THE SCROLL CONTAINER IS THE FIGURE, NEVER THE PAGE. These get wide — the
 *    largest fragment in the corpus is 12,653 chars of markup, and a third of
 *    them carry merged cells. `overflow-x-auto` lives on the `<figure>` so a
 *    single annex can never make the document body scroll sideways. Commit
 *    `cf8dafd` ("870px of cards behind a 0px scrollbar") is the same failure
 *    arriving through a different door; it is cheaper to not reopen it.
 * 2. `dir="rtl"`. The content is Arabic and COLUMN ORDER IS MEANINGFUL — an
 *    LTR table reverses the header row against its data.
 * 3. DIGITS INSIDE THE TABLE ARE CORPUS BODY TEXT — an explicit carve-out from
 *    the Latin-numerals policy this app enforces everywhere else. Whatever the
 *    statute printed is what renders. Never normalize digits inside `html`.
 *
 * `html` is sanitized SERVER-SIDE by an allowlist re-serializer: the markup is
 * parsed and REBUILT from a fixed element list, keeping only `rowspan` /
 * `colspan` (int 1..100). Nothing outside the allowlist can survive, whatever
 * the input's shape — so `dangerouslySetInnerHTML` here is trusted by
 * construction, not by inspection. This is the only non-JSON-LD use of that
 * prop in the codebase, and that is the reason it is allowed.
 *
 * The sanitizer also strips `class` and `style`, so the inner markup carries no
 * styling at all: cells are styled from the outside with arbitrary-variant
 * selectors on the wrapper. Corpus tables use `<th>` without `<thead>`
 * (16,370 `<th>` vs. 0 `<thead>`), so the header treatment targets `th`
 * directly. Semantic tokens only — this renders in both themes.
 *
 * Server component.
 */
export function ChunkTable({ html }: ChunkTableProps) {
  return (
    <figure className="my-4 -mx-1 overflow-x-auto" dir="rtl">
      {/*
        A DIV, not a <table>. `html` is the bare `<table>…</table>` fragment the
        corpus stores (all 24,511 rows start `<table` and end `</table>`), so
        setting it as the innerHTML of a <table> element would nest a table in a
        table — which the HTML parser resolves by foster-parenting the inner one
        out, silently, differently per browser. The fragment brings its own root;
        this element only ever styles it from the outside.
      */}
      <div
        className={cn(
          "text-sm text-foreground",
          // `w-max`, NOT `w-full`. A statutory grid can run to a dozen columns
          // (م · المخالفة · حد الغرامة · three size bands × three فئات · إنذار ·
          // الوحدة), and `width:100%` crams them into the container — measured
          // at 712px that squeezes the text columns to roughly one word per
          // line, turning the cell into a vertical ribbon. Sizing to CONTENT
          // and letting the figure scroll is what makes a wide table readable.
          // `min-w-full` keeps a narrow two-column table from shrinking to a
          // stub in the middle of the page.
          "[&_table]:w-max [&_table]:min-w-full [&_table]:border-collapse",
          // Stops a long Arabic cell from collapsing to a one-word column when
          // it shares a row with a dozen siblings.
          "[&_td]:min-w-[6rem] [&_th]:min-w-[5rem]",
          // ...and the other end. `w-max` sizes the table to its content, which
          // is what makes a twelve-column penalty grid readable — but a cell
          // holding a PARAGRAPH then lays that paragraph out on one line. A real
          // corpus table (1 row, 3 cells, one holding 801 chars of نظام
          // المرافعات الشرعية) rendered 4,215px wide as a single strip. Capping
          // the cell makes long prose wrap while leaving short cells to size
          // themselves, so both shapes render correctly.
          "[&_td]:max-w-[32rem] [&_th]:max-w-[32rem]",
          // Caption, when the corpus carried one: a quiet lead-in, start-aligned.
          "[&_caption]:mb-2 [&_caption]:text-start [&_caption]:text-xs [&_caption]:text-text-secondary",
          // Cells. `align-top` because a merged cell spanning four rows must
          // read from its first line, not float in the middle of them.
          "[&_td]:border [&_td]:border-border [&_td]:px-3 [&_td]:py-2 [&_td]:align-top",
          "[&_th]:border [&_th]:border-border [&_th]:px-3 [&_th]:py-2 [&_th]:align-top",
          "[&_th]:bg-muted/40 [&_th]:text-start [&_th]:font-semibold",
        )}
        // Sanitized server-side by allowlist re-serialize — see the docstring.
        dangerouslySetInnerHTML={{ __html: html }}
      />
    </figure>
  );
}

interface ChunkTableProps {
  /** Sanitized table markup, `<table>`-rooted. Rendered, never parsed. */
  html: string;
  /**
   * The prose this table replaced. Accepted so a caller holding a whole
   * segment can spread it, and deliberately NOT rendered: it is the copy
   * string and the gate weight, both settled server-side, and printing it
   * beside the grid would put the same law in the DOM twice.
   */
  md?: string;
}
