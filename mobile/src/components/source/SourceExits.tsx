import * as Clipboard from "expo-clipboard";
import { useEffect, useRef, useState } from "react";
import { StyleSheet, View } from "react-native";

import { ActionChip } from "@/components/artifact/ActionChip";

import { readerCopy } from "./copy";
import { copyLink, openInBrowser, shareLink } from "./exits";

export interface SourceExitsProps {
  /** «فتح المصدر الرسمي» target; "" = no button. */
  officialUrl: string;
  /** Full https://rayhanai.com… URL; null = no published page → no button. */
  libraryUrl: string | null;
  libraryLabel: string;
  /** «نسخ المحتوى» payload; "" = no button. */
  content: string;
  title: string;
}

function useFlash(): [string | null, (key: string) => void] {
  const [flash, setFlash] = useState<string | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => () => {
    if (timer.current) clearTimeout(timer.current);
  }, []);
  return [
    flash,
    (key) => {
      setFlash(key);
      if (timer.current) clearTimeout(timer.current);
      timer.current = setTimeout(() => setFlash(null), 1500);
    },
  ];
}

/**
 * The reader's action bar (plan 05 §R4, owner decision (a)): «فتح ال… في
 * ريحان» opens our public library page in the in-app browser (no web session
 * there — the visitor view, accepted trade-off), «فتح المصدر الرسمي», then
 * «نسخ الرابط» / «مشاركة» for the shareable link (ours when there is a page,
 * else the official one) and «نسخ المحتوى».
 */
export function SourceExits({ officialUrl, libraryUrl, libraryLabel, content, title }: SourceExitsProps) {
  const [flash, setFlashKey] = useFlash();
  const shareable = libraryUrl ?? (officialUrl || null);

  return (
    <View style={styles.row}>
      {libraryUrl ? <ActionChip label={libraryLabel} variant="filled" onPress={() => openInBrowser(libraryUrl)} testID="source-library-exit" /> : null}
      {officialUrl ? <ActionChip label={readerCopy.officialExit} onPress={() => openInBrowser(officialUrl)} testID="source-official-exit" /> : null}
      {shareable ? (
        <ActionChip
          label={flash === "link" ? readerCopy.linkCopied : readerCopy.copyLink}
          variant="ghost"
          onPress={() => {
            copyLink(shareable).then(() => setFlashKey("link"), () => undefined);
          }}
          testID="source-copy-link"
        />
      ) : null}
      {shareable ? <ActionChip label={readerCopy.share} variant="ghost" onPress={() => shareLink(shareable, title)} testID="source-share" /> : null}
      {content ? (
        <ActionChip
          label={flash === "content" ? readerCopy.copied : readerCopy.copyContent}
          variant="ghost"
          onPress={() => {
            Clipboard.setStringAsync(content).then(() => setFlashKey("content"), () => undefined);
          }}
          testID="source-copy-content"
        />
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
});
