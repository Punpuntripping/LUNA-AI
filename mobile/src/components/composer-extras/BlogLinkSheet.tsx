import { extractBlogToken, myBlogMenuLabel, type MyBlogItem } from "@contracts";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FlatList, StyleSheet, View } from "react-native";

import { listMyBlogs } from "@/api/blog-items";
import { Sheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { useTheme } from "@/theme/ThemeProvider";

import { myBlogsKeys } from "./keys";
import { ErrorText, ListRow, ListStatus, SheetHeading, SheetInput, sheetStyles } from "./parts";

export interface BlogLinkSheetProps {
  visible: boolean;
  onClose: () => void;
  /**
   * Queue the token(s) as blog chips. Resolves to an Arabic error to show in
   * the sheet (cap reached, no conversation, …) or null when done — then the
   * sheet closes.
   */
  onAddTokens: (tokens: string[]) => Promise<string | null>;
}

/**
 * «مدونة» — the web composer «+» → مدونة submenu and its «إضافة مدونة برابط»
 * dialog in one sheet: a share link (or bare token) validated client-side
 * («رابط مدونة غير صالح»), and «من مدوناتي» (the caller's posts, title or
 * snippet). The import itself (`POST /conversations/{id}/blog-items`) runs in
 * `onAddTokens` and shows on the composer chip.
 */
export function BlogLinkSheet({ visible, onClose, onAddTokens }: BlogLinkSheetProps) {
  return (
    <Sheet visible={visible} onClose={onClose} testID="blog-link-sheet">
      {visible ? <BlogContent onClose={onClose} onAddTokens={onAddTokens} /> : null}
    </Sheet>
  );
}

function BlogContent({ onClose, onAddTokens }: Omit<BlogLinkSheetProps, "visible">) {
  const { colours } = useTheme();
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const blogs = useQuery({ queryKey: myBlogsKeys.list(), queryFn: listMyBlogs });

  const add = async (tokens: string[]) => {
    if (busy) return;
    setBusy(true);
    const err = await onAddTokens(tokens);
    setBusy(false);
    if (err) {
      setError(err);
      return;
    }
    onClose();
  };

  const submitLink = () => {
    const token = extractBlogToken(link);
    if (!token) {
      setError("رابط مدونة غير صالح");
      return;
    }
    void add([token]);
  };

  let status: { text: string; loading?: boolean } | null = null;
  if (blogs.isPending) status = { text: "جارٍ التحميل...", loading: true };
  else if (blogs.isError) status = { text: "تعذّر تحميل المدونات" };
  else if (blogs.data.posts.length === 0) status = { text: "لا توجد مدونات بعد" };

  return (
    <View style={sheetStyles.body}>
      <SheetHeading icon="link" title="إضافة مدونة برابط" />
      <SheetInput
        ltr
        value={link}
        onChangeText={(v) => {
          setLink(v);
          setError(null);
        }}
        placeholder="https://rayhanai.com/blog/..."
        accessibilityLabel="رابط المدونة"
        keyboardType="url"
        autoCapitalize="none"
        autoCorrect={false}
        returnKeyType="done"
        onSubmitEditing={submitLink}
        testID="blog-link-input"
      />
      {error ? <ErrorText text={error} /> : null}
      <Button label="إضافة" onPress={submitLink} disabled={!link.trim()} loading={busy} testID="blog-link-add" />

      <View style={[styles.separator, { backgroundColor: colours.border }]} />
      <AppText variant="xs" tone="muted">
        من مدوناتي
      </AppText>
      {status ? (
        <ListStatus text={status.text} loading={status.loading} />
      ) : (
        <FlatList
          style={styles.list}
          data={blogs.data?.posts ?? []}
          keyExtractor={(p: MyBlogItem) => p.post_id}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item }) => (
            <ListRow
              icon="blog"
              label={myBlogMenuLabel(item)}
              disabled={busy}
              onPress={() => void add([item.token])}
              testID={`blog-mine-${item.post_id}`}
            />
          )}
        />
      )}

      <View style={sheetStyles.buttons}>
        <Button label="إلغاء" variant="ghost" onPress={onClose} disabled={busy} />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  separator: { height: StyleSheet.hairlineWidth, marginVertical: 2 },
  list: { maxHeight: 240 },
});
