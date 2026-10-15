import { Platform } from "react-native";

import type { Colours } from "@/theme/tokens.generated";
import type { TypeVariant } from "@/theme/typography";

/** Everything a block/inline renderer needs — built once per render of the
 *  root, so memoized blocks compare by identity. */
export interface RenderCtx {
  colours: Colours;
  /** Body text tier: chat = base (17/26), reader = read (17/29, roomier leading). */
  body: TypeVariant;
  selectable: boolean;
  onCitationPress?: (n: number) => void;
}

export const MONO = Platform.select({ ios: "Menlo", default: "monospace" });
