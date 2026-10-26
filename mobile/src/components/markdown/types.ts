export type MarkdownVariant = "chat" | "reader";

export interface MarkdownAdapterProps {
  /** Already preprocessed (images stripped, citations linkified). */
  content: string;
  streaming?: boolean;
  /** A single newline inside a paragraph renders as a line break (web
   *  `white-space: pre-line`). «رسائل ريحان» bodies only — never chat.
   *  Engine B only; the A spike (dev-markdown screen) ignores it. */
  breaks?: boolean;
  onCitationPress?: (n: number) => void;
  variant: MarkdownVariant;
}
