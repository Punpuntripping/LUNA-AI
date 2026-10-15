// Long markdown → independent render blocks (plan 05 §R3). A circular body can
// run to ~168k chars; parsed and laid out as ONE markdown tree it stalls the JS
// thread and the whole tree stays mounted. Cut into blocks of ~TARGET chars it
// renders through a virtualised list, one block per row.
//
// Cuts land ONLY on a blank line outside a code fence — never inside a
// paragraph, a table (GFM tables have no blank lines) or a fence — so every
// block is valid markdown on its own. A single block can exceed TARGET when the
// text offers no blank line to cut at; that is still correct, just bigger.

const FENCE = /^\s{0,3}(`{3,}|~{3,})/;

export const CHUNK_TARGET = 4000;

export function chunkMarkdown(markdown: string, target: number = CHUNK_TARGET): string[] {
  const text = markdown.replace(/\r\n?/g, "\n");
  if (text.length <= target) return text.trim() ? [text] : [];

  const lines = text.split("\n");
  const blocks: string[] = [];
  let current: string[] = [];
  let size = 0;
  let fence: string | null = null;

  const flush = () => {
    const block = current.join("\n").trim();
    if (block) blocks.push(block);
    current = [];
    size = 0;
  };

  for (const line of lines) {
    const m = FENCE.exec(line);
    if (m) {
      const marker = m[1][0];
      if (fence === null) fence = marker;
      else if (marker === fence) fence = null;
    }
    if (fence === null && line.trim() === "" && size >= target) {
      flush();
      continue;
    }
    current.push(line);
    size += line.length + 1;
  }
  flush();
  return blocks;
}
