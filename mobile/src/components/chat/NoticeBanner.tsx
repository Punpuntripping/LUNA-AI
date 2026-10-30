import { memo } from "react";
import { Pressable, StyleSheet, View } from "react-native";

import { streamStore, useChatError, useChatNotice, type ChatNotice } from "@/chat/stream-store";
import { AppText } from "@/components/AppText";
import { Icon } from "@/components/Icon";
import { useTheme } from "@/theme/ThemeProvider";

// M8 — refusals and run errors for ONE conversation, above the composer.
// The server's Arabic text only. 📏 No upgrade button, no plan names, no web
// link (store rules — the paywall is Phase 4).

const ARABIC = /[؀-ۿ]/;
const FALLBACK = "تعذّر إرسال رسالتك الآن — حاول مجدداً بعد قليل";
// Only if the server's `message_ar` is missing. Copy owner-approved 2026-10-30 (migration 197).
const DEEP_SEARCH_GATED_FALLBACK = "البحث المعمّق غير متاح في باقتك الآن.";

function arabic(value: string | null | undefined): string | null {
  const v = value?.trim();
  return v && ARABIC.test(v) ? v : null;
}

/** The Arabic line a refusal shows (exported for tests / other hosts). */
export function noticeText(notice: ChatNotice): string {
  switch (notice.kind) {
    case "quota":
      return (
        (notice.info.reason === "parallel_reserve" ? arabic(notice.info.detail) : null) ??
        arabic(notice.info.message_ar) ??
        FALLBACK
      );
    case "parallel_limit":
      return arabic(notice.info.detail) ?? FALLBACK;
    case "duplicate":
      return arabic(notice.detail) ?? FALLBACK;
    case "deep_search_gated":
      // The send went through — FALLBACK's «تعذّر إرسال رسالتك» would be false.
      return arabic(notice.info.message_ar) ?? DEEP_SEARCH_GATED_FALLBACK;
  }
}

export const NoticeBanner = memo(function NoticeBanner({ conversationId }: { conversationId: string }) {
  const notice = useChatNotice(conversationId);
  const error = useChatError(conversationId);
  const { colours, radius } = useTheme();
  if (!notice && !error) return null;

  const isError = !notice;
  const text = notice ? noticeText(notice) : (error as string);
  const bg = isError ? colours.errorBg : colours.warningBg;
  const fg = isError ? colours.errorFg : colours.warningFg;

  return (
    <View
      testID={isError ? "chat-error-banner" : "chat-notice-banner"}
      accessibilityRole="alert"
      accessibilityLiveRegion="polite"
      style={[styles.box, { backgroundColor: bg, borderRadius: radius.xl }]}
    >
      <AppText variant="sm" style={[styles.text, { color: fg }]}>
        {text}
      </AppText>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel="إغلاق"
        style={styles.close}
        onPress={() => {
          if (notice) streamStore.setNotice(conversationId, null);
          else streamStore.setError(conversationId, null);
        }}
      >
        <Icon name="close" size={16} colour={fg} />
      </Pressable>
    </View>
  );
});

const styles = StyleSheet.create({
  box: { flexDirection: "row", alignItems: "flex-start", gap: 8, paddingHorizontal: 12, paddingVertical: 10, marginBottom: 8 },
  text: { flex: 1 },
  // 44×44 touch target around the 16dp glyph; the negative margin gives the
  // extra 14dp back so the banner's layout is unchanged. (A plain hitSlop
  // would poke outside the banner, where Android drops the touch.)
  close: { width: 44, height: 44, alignItems: "center", justifyContent: "center", margin: -14 },
});
