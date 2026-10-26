import type { Announcement } from "@contracts";
import { StyleSheet, View } from "react-native";

import { announcementLinkUrl } from "@/announcements/logic";
import { openAnnouncementLink } from "@/announcements/open-link";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Markdown } from "@/components/markdown/Markdown";
import { formatDate } from "@/lib/format";
import { useTheme } from "@/theme/ThemeProvider";

// One «رسائل ريحان» message: title, date (Arabic month, Latin digits,
// Gregorian), the markdown body and the optional link button. Shared by the
// messages screen (card) and the popup.

/** Title (with the unread dot) + date. */
export function AnnouncementHeading({ announcement, unread = false }: { announcement: Announcement; unread?: boolean }) {
  const { colours } = useTheme();
  return (
    <View style={styles.heading}>
      <View style={styles.titleRow}>
        {unread ? (
          <View
            accessible
            accessibilityLabel="غير مقروءة"
            style={[styles.dot, { backgroundColor: colours.primary }]}
          />
        ) : null}
        <AppText variant="h3" weight={700} style={styles.title} accessibilityRole="header">
          {announcement.title}
        </AppText>
      </View>
      <AppText variant="xs" tone="muted">
        {formatDate(announcement.published_at)}
      </AppText>
    </View>
  );
}

/** The owner writes line by line: a single newline is a visible line break,
 *  a blank line a paragraph gap (web: `white-space: pre-line` on the body). */
export function AnnouncementBody({ announcement }: { announcement: Announcement }) {
  return <Markdown content={announcement.body_md} variant="chat" breaks />;
}

/** True when the message has a link the app can open. */
export function hasAnnouncementLink(announcement: Announcement): boolean {
  return !!announcement.link_label && announcementLinkUrl(announcement.link_url) !== null;
}

/** A card on the messages screen; unread = the primary dot before the title.
 *  Opening its link needs no read marking — the screen marks what it shows. */
export function AnnouncementCard({ announcement, unread }: { announcement: Announcement; unread: boolean }) {
  const { colours, radius } = useTheme();
  return (
    <View
      style={[styles.card, { backgroundColor: colours.surface1, borderColor: colours.border, borderRadius: radius.xl }]}
      testID={`announcement-${announcement.announcement_id}`}
    >
      <AnnouncementHeading announcement={announcement} unread={unread} />
      <AnnouncementBody announcement={announcement} />
      {hasAnnouncementLink(announcement) ? (
        <Button
          label={announcement.link_label ?? ""}
          variant="secondary"
          onPress={() => void openAnnouncementLink(announcement.link_url)}
          testID={`announcement-link-${announcement.announcement_id}`}
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  heading: { gap: 2 },
  titleRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  title: { flex: 1 },
  dot: { width: 8, height: 8, borderRadius: 4 },
  card: { borderWidth: 1, padding: 16, gap: 12 },
});
