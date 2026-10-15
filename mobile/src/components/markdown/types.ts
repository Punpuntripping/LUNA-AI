export type MarkdownVariant = "chat" | "reader";

export interface MarkdownAdapterProps {
  /** Already preprocessed (images stripped, citations linkified). */
  content: string;
  streaming?: boolean;
  onCitationPress?: (n: number) => void;
  variant: MarkdownVariant;
}
