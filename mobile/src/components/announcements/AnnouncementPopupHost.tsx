import { unreadOldestFirst } from "@contracts";
import { usePathname } from "expo-router";
import { useEffect, useState, useSyncExternalStore } from "react";
import { AppState, ScrollView, StyleSheet, useWindowDimensions, View } from "react-native";

import { MESSAGES_PATH } from "@/announcements/logic";
import { openAnnouncementLink } from "@/announcements/open-link";
import { useAnnouncements, useMarkAnnouncementsRead } from "@/announcements/use-announcements";
import { Sheet } from "@/components/ActionSheet";
import { Button } from "@/components/Button";
import { modalCoordinator } from "@/lib/modal-coordinator";

import { AnnouncementBody, AnnouncementHeading, hasAnnouncementLink } from "./AnnouncementContent";

/** After a link opened from the popup returns (iOS: browser closed; Android:
 *  browser launched — the app then goes to the background), wait this long
 *  before the next message may pop up. */
const LINK_GRACE_MS = 1000;

function subscribeAppState(onChange: () => void): () => void {
  const sub = AppState.addEventListener("change", onChange);
  return () => sub.remove();
}

/** False while the app is in the background (no popup is presented then). */
function useAppForeground(): boolean {
  return useSyncExternalStore(subscribeAppState, () => AppState.currentState !== "background");
}

/**
 * «رسائل ريحان» popup (plan D2 + popup rules), mounted once in the signed-in
 * layout. Every unread message pops up, one at a time, oldest first:
 *   - only after the list has loaded, never on the messages screen;
 *   - never on top of another modal / sheet (or while one is settling);
 *   - closing it any way (حسناً, back, backdrop) marks it read optimistically
 *     → the cached list flips, the bell drops, the next one pops;
 *   - its link marks it read, closes it, then opens the page.
 * A failed fetch shows nothing.
 */
export function AnnouncementPopupHost() {
  const { height } = useWindowDimensions();
  const pathname = usePathname();
  const onMessages = pathname === MESSAGES_PATH;
  const foreground = useAppForeground();
  const { data } = useAnnouncements();
  const markRead = useMarkAnnouncementsRead();
  const [linkOpen, setLinkOpen] = useState(false);

  const nextId = data ? (unreadOldestFirst(data.items)[0]?.announcement_id ?? null) : null;
  const wanted = nextId !== null && !onMessages && foreground && !linkOpen;

  // The message picked for presentation. It stays picked while the sheet
  // slides away (its content must not vanish mid-animation); it hides as
  // soon as it is read — by this popup or by the messages screen.
  const [shownId, setShownId] = useState<string | null>(null);
  useEffect(() => {
    if (!wanted || nextId === null) return undefined;
    return modalCoordinator.whenClear(() => setShownId(nextId));
  }, [wanted, nextId]);

  const current = shownId ? (data?.items.find((a) => a.announcement_id === shownId) ?? null) : null;
  const visible = current !== null && !current.read && !onMessages;

  const dismiss = () => {
    if (current && !current.read) markRead([current.announcement_id]);
  };

  const openLink = () => {
    if (!current) return;
    const url = current.link_url;
    setLinkOpen(true);
    dismiss();
    // The browser presents once this sheet is fully gone (iOS refuses a
    // presentation over a dismissing modal).
    modalCoordinator.whenSettled(() => {
      void openAnnouncementLink(url).finally(() => {
        setTimeout(() => setLinkOpen(false), LINK_GRACE_MS);
      });
    });
  };

  const link = current !== null && hasAnnouncementLink(current);

  return (
    <Sheet visible={visible} onClose={dismiss} testID="announcement-popup">
      {current ? (
        <View style={styles.content}>
          <AnnouncementHeading announcement={current} />
          <ScrollView style={{ maxHeight: Math.round(height * 0.5) }} contentContainerStyle={styles.body}>
            <AnnouncementBody announcement={current} />
          </ScrollView>
          <View style={styles.buttons}>
            {link ? (
              <Button label={current.link_label ?? ""} onPress={openLink} testID="announcement-popup-link" />
            ) : null}
            <Button
              label="حسناً"
              variant={link ? "secondary" : "primary"}
              onPress={dismiss}
              testID="announcement-popup-ok"
            />
          </View>
        </View>
      ) : null}
    </Sheet>
  );
}

const styles = StyleSheet.create({
  content: { paddingHorizontal: 12, paddingTop: 4, gap: 12 },
  body: { paddingBottom: 4 },
  buttons: { gap: 8, paddingTop: 4 },
});
