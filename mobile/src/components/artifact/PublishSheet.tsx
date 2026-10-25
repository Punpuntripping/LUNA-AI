import * as Clipboard from "expo-clipboard";
import * as Haptics from "expo-haptics";
import { useEffect, useRef, useState } from "react";
import { ActivityIndicator, Platform, Pressable, Share, StyleSheet, TextInput, View } from "react-native";

import type { ShareDisplayMode } from "@contracts";

import { errorMessage } from "@/api/errors";
import { getShareDraft, shareArtifact } from "@/api/workspace";
import { Sheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon, type IconName } from "@/components/Icon";
import { fontFamilyFor } from "@/theme/fonts";
import { useTheme } from "@/theme/ThemeProvider";
import { typeVariants } from "@/theme/typography";

import {
  buildSaveBlogRequest,
  buildShareRequest,
  SAVE_BLOG_ERRORS,
  saveBlogDefaultTitle,
  SHARE_ERRORS,
} from "./action-bar";

export type PublishFlow = "share" | "saveBlog";

export interface PublishSheetProps {
  itemId: string;
  /** `null` = closed. */
  flow: PublishFlow | null;
  onClose: () => void;
}

/**
 * «مشاركة» and «حفظ كمدونة» — the web's ShareArtifactDialog and
 * SaveAsBlogDialog as one bottom sheet (one Modal per flow). Both pre-fill
 * from `GET share-draft` and publish with `POST share`; the content is mounted
 * fresh per opening, so every open starts from a clean draft (web: reset on
 * open).
 */
export function PublishSheet({ itemId, flow, onClose }: PublishSheetProps) {
  // Keep the last flow while the sheet slides away (no blank panel), and
  // remount the content on every opening (state adjusted during render).
  const [prev, setPrev] = useState<PublishFlow | null>(flow);
  const [shown, setShown] = useState<PublishFlow | null>(flow);
  const [seq, setSeq] = useState(0);
  if (flow !== prev) {
    setPrev(flow);
    if (flow !== null) {
      setShown(flow);
      setSeq((n) => n + 1);
    }
  }

  return (
    <Sheet visible={flow !== null} onClose={onClose} testID="wi-publish-sheet">
      {shown === "saveBlog" ? (
        <SaveBlogContent key={`b${seq}`} itemId={itemId} onClose={onClose} />
      ) : shown === "share" ? (
        <ShareContent key={`s${seq}`} itemId={itemId} onClose={onClose} />
      ) : null}
    </Sheet>
  );
}

/** Loads the share draft once per mount; cancelled on unmount. */
function useShareDraft(itemId: string, onLoaded: (q: string, t: string | null) => void, failText: string) {
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const loaded = useRef(onLoaded);
  useEffect(() => {
    loaded.current = onLoaded;
  });
  useEffect(() => {
    let cancelled = false;
    getShareDraft(itemId)
      .then((res) => {
        if (!cancelled) loaded.current(res.default_question ?? "", res.default_title);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errorMessage(err, failText));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [itemId, failText]);
  return { loading, error, setError };
}

/** Native share sheet with the public link (iOS takes a URL, Android text). */
function shareLink(url: string): void {
  void Share.share(Platform.OS === "ios" ? { url } : { message: url }).catch(() => undefined);
}

function ShareContent({ itemId, onClose }: { itemId: string; onClose: () => void }) {
  const { colours } = useTheme();
  const [template, setTemplate] = useState<ShareDisplayMode>("question");
  const [question, setQuestion] = useState("");
  const [title, setTitle] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [publicUrl, setPublicUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const copiedTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(
    () => () => {
      if (copiedTimer.current) clearTimeout(copiedTimer.current);
    },
    [],
  );

  const draft = useShareDraft(
    itemId,
    (q, t) => {
      setQuestion(q);
      setTitle(t ?? "");
    },
    SHARE_ERRORS.draft,
  );

  const copyUrl = async (url: string) => {
    try {
      await Clipboard.setStringAsync(url);
    } catch {
      return; // the link stays visible and selectable
    }
    Haptics.notificationAsync(Haptics.NotificationFeedbackType.Success).catch(() => undefined);
    setCopied(true);
    if (copiedTimer.current) clearTimeout(copiedTimer.current);
    copiedTimer.current = setTimeout(() => setCopied(false), 1500);
  };

  const publish = async () => {
    const built = buildShareRequest(template, question, title);
    if (!built.ok) {
      draft.setError(built.error);
      return;
    }
    draft.setError(null);
    setPublishing(true);
    try {
      const res = await shareArtifact(itemId, built.body);
      setPublicUrl(res.public_url);
      await copyUrl(res.public_url);
      // The phone's equivalent of «فتح»/paste: straight into the share sheet.
      shareLink(res.public_url);
    } catch (err) {
      draft.setError(errorMessage(err, SHARE_ERRORS.publish));
    } finally {
      setPublishing(false);
    }
  };

  return (
    <View style={styles.body}>
      <SheetHeading icon="share" title="مشاركة عبر رابط" />
      <AppText variant="sm" tone="muted">
        تُنشر نسخة ثابتة من المستند عبر رابط خاص — كصفحة سؤال وإجابة أو كمقال في مدوّنة. لا يمكن فتح الصفحة إلا لمن لديه
        الرابط الذي تشاركه — دون الحاجة إلى تسجيل دخول.
      </AppText>

      {publicUrl === null ? (
        <>
          <View
            accessibilityRole="tablist"
            accessibilityLabel="نوع المشاركة"
            style={[styles.segments, { borderColor: colours.border, backgroundColor: colours.surface2 }]}
          >
            <Segment
              label="سؤال"
              icon="shareQuestion"
              selected={template === "question"}
              disabled={publishing}
              onPress={() => setTemplate("question")}
            />
            <Segment
              label="مدونة"
              icon="shareArticle"
              selected={template === "title"}
              disabled={publishing}
              onPress={() => setTemplate("title")}
            />
          </View>

          {template === "question" ? (
            <>
              <AppText variant="sm" weight={600}>
                السؤال
              </AppText>
              {draft.loading ? (
                <Loading tall />
              ) : (
                <Field
                  value={question}
                  onChangeText={setQuestion}
                  placeholder="اكتب السؤال الذي سيظهر على الصفحة العامة..."
                  multiline
                  testID="wi-share-question"
                />
              )}
            </>
          ) : (
            <>
              <AppText variant="sm" weight={600}>
                عنوان المقال
              </AppText>
              {draft.loading ? (
                <Loading />
              ) : (
                <Field value={title} onChangeText={setTitle} placeholder="عنوان المقال" testID="wi-share-title" />
              )}
              <AppText variant="xs" tone="muted">
                سيظهر كعنوان مقال في منتصف الصفحة بتنسيق مدوّنة.
              </AppText>
            </>
          )}

          <AppText variant="xs" tone="muted">
            لإخفاء معلومات حساسة، عدّل النص هنا أو حرّر المستند قبل النشر.
          </AppText>

          {draft.error ? <ErrorBox text={draft.error} /> : null}

          <View style={styles.buttons}>
            <Button
              label="نشر ونسخ الرابط"
              onPress={() => void publish()}
              loading={publishing}
              disabled={draft.loading}
              testID="wi-share-publish"
            />
            <Button label="إلغاء" variant="ghost" onPress={onClose} disabled={publishing} />
          </View>
        </>
      ) : (
        <>
          <Done text="تم النشر — الرابط في الحافظة" />
          <View style={[styles.urlBox, { borderColor: colours.border, backgroundColor: colours.surface2 }]}>
            <AppText variant="xs" selectable numberOfLines={2} style={styles.url} testID="wi-share-url">
              {publicUrl}
            </AppText>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={copied ? "تم النسخ" : "نسخ"}
              onPress={() => void copyUrl(publicUrl)}
              style={({ pressed }) => [styles.urlCopy, { backgroundColor: colours.surface1, opacity: pressed ? 0.7 : 1 }]}
            >
              <Icon name={copied ? "check" : "copy"} size={14} colour={colours.textPrimary} />
              <AppText variant="xs" weight={600}>
                {copied ? "تم النسخ" : "نسخ"}
              </AppText>
            </Pressable>
          </View>
          <View style={styles.buttons}>
            <Button label="مشاركة" variant="secondary" onPress={() => shareLink(publicUrl)} testID="wi-share-native" />
            <Button label="إغلاق" variant="ghost" onPress={onClose} />
          </View>
        </>
      )}
    </View>
  );
}

function SaveBlogContent({ itemId, onClose }: { itemId: string; onClose: () => void }) {
  const [title, setTitle] = useState("");
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  const draft = useShareDraft(
    itemId,
    (q, t) => setTitle(saveBlogDefaultTitle({ default_question: q, default_title: t })),
    SAVE_BLOG_ERRORS.draft,
  );

  const save = async () => {
    const built = buildSaveBlogRequest(title);
    if (!built.ok) {
      draft.setError(built.error);
      return;
    }
    draft.setError(null);
    setSaving(true);
    try {
      await shareArtifact(itemId, built.body);
      setSaved(true);
    } catch (err) {
      draft.setError(errorMessage(err, SAVE_BLOG_ERRORS.save));
    } finally {
      setSaving(false);
    }
  };

  return (
    <View style={styles.body}>
      <SheetHeading icon="bookmarkAdd" title="حفظ كمدونة" />
      <AppText variant="sm" tone="muted">
        احفظ هذه الإجابة كمقال في مدوناتك للرجوع إليه لاحقاً.
      </AppText>
      {saved ? (
        <>
          <Done text="تم الحفظ في مدوناتك" />
          <View style={styles.buttons}>
            <Button label="إغلاق" variant="ghost" onPress={onClose} />
          </View>
        </>
      ) : (
        <>
          <AppText variant="sm" weight={600}>
            عنوان المدونة
          </AppText>
          {draft.loading ? (
            <Loading />
          ) : (
            <Field value={title} onChangeText={setTitle} placeholder="عنوان المقال" testID="wi-save-blog-title" />
          )}
          <AppText variant="xs" tone="muted">
            سيُحفظ في مدوناتك كمقال.
          </AppText>
          {draft.error ? <ErrorBox text={draft.error} /> : null}
          <View style={styles.buttons}>
            <Button label="حفظ" onPress={() => void save()} loading={saving} disabled={draft.loading} testID="wi-save-blog-confirm" />
            <Button label="إلغاء" variant="ghost" onPress={onClose} disabled={saving} />
          </View>
        </>
      )}
    </View>
  );
}

function SheetHeading({ icon, title }: { icon: IconName; title: string }) {
  const { colours } = useTheme();
  return (
    <View style={styles.heading}>
      <Icon name={icon} size={16} colour={colours.textPrimary} />
      <AppText variant="base" weight={600} accessibilityRole="header" style={styles.fill}>
        {title}
      </AppText>
    </View>
  );
}

function Segment({
  label,
  icon,
  selected,
  disabled,
  onPress,
}: {
  label: string;
  icon: IconName;
  selected: boolean;
  disabled: boolean;
  onPress: () => void;
}) {
  const { colours, radius } = useTheme();
  const fg = selected ? colours.primaryFg : colours.textMuted;
  return (
    <Pressable
      accessibilityRole="tab"
      accessibilityLabel={label}
      accessibilityState={{ selected, disabled }}
      disabled={disabled}
      onPress={onPress}
      style={({ pressed }) => [
        styles.segment,
        { backgroundColor: selected ? colours.primary : "transparent", borderRadius: radius.sm, opacity: pressed ? 0.75 : 1 },
      ]}
    >
      <Icon name={icon} size={14} colour={fg} />
      <AppText variant="sm" weight={selected ? 600 : 400} style={{ color: fg }}>
        {label}
      </AppText>
    </Pressable>
  );
}

function Field({
  value,
  onChangeText,
  placeholder,
  multiline = false,
  testID,
}: {
  value: string;
  onChangeText: (v: string) => void;
  placeholder: string;
  multiline?: boolean;
  testID?: string;
}) {
  const { colours, radius } = useTheme();
  return (
    <TextInput
      testID={testID}
      value={value}
      onChangeText={onChangeText}
      placeholder={placeholder}
      placeholderTextColor={colours.textSubtle}
      multiline={multiline}
      textAlignVertical={multiline ? "top" : "center"}
      style={[
        styles.input,
        typeVariants.sm,
        multiline && styles.inputTall,
        {
          fontFamily: fontFamilyFor[400],
          color: colours.textPrimary,
          borderColor: colours.border,
          backgroundColor: colours.canvas,
          borderRadius: radius.default,
        },
      ]}
    />
  );
}

function Loading({ tall = false }: { tall?: boolean }) {
  const { colours, radius } = useTheme();
  return (
    <View
      style={[
        styles.loading,
        tall && styles.inputTall,
        { borderColor: colours.border, backgroundColor: colours.surface2, borderRadius: radius.default },
      ]}
    >
      <ActivityIndicator color={colours.textMuted} />
    </View>
  );
}

function ErrorBox({ text }: { text: string }) {
  const { colours, radius } = useTheme();
  return (
    <View accessibilityRole="alert" style={[styles.error, { backgroundColor: colours.errorBg, borderRadius: radius.default }]}>
      <AppText variant="sm" style={{ color: colours.errorFg }}>
        {text}
      </AppText>
    </View>
  );
}

function Done({ text }: { text: string }) {
  const { colours } = useTheme();
  return (
    <View style={styles.heading}>
      <View style={[styles.doneDot, { backgroundColor: colours.successBg }]}>
        <Icon name="check" size={14} colour={colours.successFg} />
      </View>
      <AppText variant="sm" weight={600} style={styles.fill}>
        {text}
      </AppText>
    </View>
  );
}

const styles = StyleSheet.create({
  body: { paddingHorizontal: 12, paddingBottom: 4, gap: 10 },
  heading: { flexDirection: "row", alignItems: "center", gap: 8 },
  // Heading / «تم» labels take the row width — a shrink-wrapped Naskh label
  // loses its last glyph at large font scales on Android (QA F3, ActionSheet.tsx).
  fill: { flex: 1 },
  segments: { flexDirection: "row", alignSelf: "flex-start", gap: 2, padding: 2, borderWidth: StyleSheet.hairlineWidth, borderRadius: 6 },
  segment: { flexDirection: "row", alignItems: "center", gap: 6, minHeight: 44, paddingHorizontal: 14 },
  input: { borderWidth: 1, paddingHorizontal: 12, paddingVertical: 10, textAlign: "right", writingDirection: "rtl", minHeight: 44 },
  inputTall: { minHeight: 112, maxHeight: 200 },
  loading: { minHeight: 44, borderWidth: 1, alignItems: "center", justifyContent: "center" },
  error: { padding: 10 },
  buttons: { gap: 8, paddingTop: 4 },
  urlBox: { flexDirection: "row", alignItems: "center", gap: 6, borderWidth: 1, borderRadius: 8, padding: 6 },
  // Latin URL: natural alignment puts it at the left, no physical align needed.
  url: { flex: 1, paddingHorizontal: 4 },
  urlCopy: { flexDirection: "row", alignItems: "center", gap: 4, minHeight: 44, paddingHorizontal: 10, borderRadius: 6 },
  doneDot: { width: 24, height: 24, borderRadius: 12, alignItems: "center", justifyContent: "center" },
});
