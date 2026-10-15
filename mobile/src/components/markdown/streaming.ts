// Spike version of plan K4 (`splitStreamingContent`, web StreamingText.tsx):
// while a message streams, everything up to the last blank line OUTSIDE a
// fenced code block is settled (parsed once, memoized); only the tail is
// re-parsed on each flush.

const FENCE_RE = /^ {0,3}(`{3,}|~{3,})/;

export interface StreamingSplit {
  prefix: string;
  tail: string;
}

export function splitStreamingContent(content: string): StreamingSplit {
  let fence: string | null = null;
  let cut = 0;
  let pos = 0;
  const lines = content.split("\n");
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const opener = FENCE_RE.exec(line);
    if (fence) {
      if (opener && opener[1][0] === fence[0] && opener[1].length >= fence.length) fence = null;
    } else if (opener) {
      fence = opener[1];
    } else if (line.trim() === "" && i > 0 && i < lines.length - 1) {
      cut = pos; // start of this blank line: a block boundary
    }
    pos += line.length + 1;
  }
  return { prefix: content.slice(0, cut), tail: content.slice(cut) };
}
