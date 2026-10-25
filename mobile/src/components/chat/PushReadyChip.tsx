import { memo, useEffect, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, View } from "react-native";

import { useSession } from "@/auth/session-store";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { shouldOfferChip } from "@/push/logic";
import { enablePush, PushError, readPushStatus } from "@/push/registration";
import { writeChipHidden } from "@/push/storage";
import { useTheme } from "@/theme/ThemeProvider";

/**
 * «نبّهني عند الجاهزية» — the primary push opt-in (port of the web's
 * PushReadyChip.tsx). Sits on the deep-search note while a run is in flight:
 * the user is literally waiting, the one moment a permission prompt earns its
 * keep. Never prompts on its own — the tap shows the system prompt.
 *
 * Renders nothing when this device already gets the push, after a refusal
 * (remembered per device), or when the permission is blocked in the device
 * settings (the «الإشعارات» settings row explains how to re-enable).
 */

/** How long «سننبّهك ✓» / an error line stays before the chip settles. */
const FLASH_MS = 4000;

const LABEL_ENABLE = "نبّهني عند الجاهزية";
/** The ✓ is the check icon, so screen readers don't read "check mark". */
const LABEL_DONE = "سننبّهك";
const MSG_FALLBACK = "تعذّر تفعيل الإشعارات.";

type ChipState =
  | { kind: "hidden" }
  | { kind: "enable" }
  | { kind: "working" }
  | { kind: "done" }
  | { kind: "error"; message: string };

export const PushReadyChip = memo(function PushReadyChip() {
  const { colours, radius } = useTheme();
  const userId = useSession((s) => s.user?.user_id ?? null);
  // Starts hidden and resolves from the device state, so a registered user
  // never sees the chip flash in and out.
  const [state, setState] = useState<ChipState>({ kind: "hidden" });

  useEffect(() => {
    let cancelled = false;
    void readPushStatus(userId).then((s) => {
      if (cancelled || !s.supported) return;
      if (shouldOfferChip({ permission: s.permission, registered: s.registered, refused: s.chipHidden })) {
        setState({ kind: "enable" });
      }
    });
    return () => {
      cancelled = true;
    };
  }, [userId]);

  // Confirmation / error lines are transient.
  useEffect(() => {
    if (state.kind !== "done" && state.kind !== "error") return;
    const next: ChipState = state.kind === "done" ? { kind: "hidden" } : { kind: "enable" };
    const id = setTimeout(() => setState(next), FLASH_MS);
    return () => clearTimeout(id);
  }, [state.kind]);

  const handleEnable = async () => {
    if (!userId) return;
    setState({ kind: "working" });
    try {
      const result = await enablePush(userId);
      if (result === "subscribed") {
        setState({ kind: "done" });
      } else if (result === "denied") {
        await writeChipHidden();
        setState({ kind: "hidden" });
      } else {
        setState({ kind: "enable" });
      }
    } catch (err) {
      setState({ kind: "error", message: err instanceof PushError ? err.message : MSG_FALLBACK });
    }
  };

  if (state.kind === "hidden") return null;

  if (state.kind === "error") {
    return (
      <AppText variant="xs" tone="destructive" accessibilityRole="alert" style={styles.top}>
        {state.message}
      </AppText>
    );
  }

  const pill = [styles.pill, { borderRadius: radius.full }];

  if (state.kind === "done") {
    return (
      <View
        accessible
        accessibilityLabel={LABEL_DONE}
        accessibilityLiveRegion="polite"
        style={[pill, { borderColor: colours.accentLine, backgroundColor: colours.accentSoft }]}
      >
        <AppText variant="xs" weight={500} tone="link">
          {LABEL_DONE}
        </AppText>
        <Icon name="check" size={14} colour={colours.primary} />
      </View>
    );
  }

  const working = state.kind === "working";
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={LABEL_ENABLE}
      accessibilityState={{ disabled: working, busy: working }}
      disabled={working}
      onPress={() => void handleEnable()}
      style={({ pressed }) => [
        pill,
        {
          borderColor: colours.accentLine,
          backgroundColor: pressed ? colours.accentSoft : colours.canvas,
          opacity: working ? 0.7 : 1,
        },
      ]}
      testID="push-chip-enable"
    >
      {working ? (
        <ActivityIndicator size="small" color={colours.primary} />
      ) : (
        <Icon name="bellRing" size={14} colour={colours.primary} />
      )}
      <AppText variant="xs" weight={500} tone="link">
        {LABEL_ENABLE}
      </AppText>
    </Pressable>
  );
});

const styles = StyleSheet.create({
  top: { marginTop: 8 },
  pill: {
    alignSelf: "flex-start",
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    minHeight: 44,
    marginTop: 8,
    paddingHorizontal: 16,
    borderWidth: 1,
  },
});
