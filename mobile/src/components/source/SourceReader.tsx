import { FlashList } from "@shopify/flash-list";
import { router } from "expo-router";
import { useMemo, useState } from "react";
import { ActivityIndicator, StyleSheet, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import {
  officialUrl as officialUrlFor,
  referenceDefiniteType,
  referenceLabel,
  referenceLibraryExitLabel,
  sourceViewExternalUrl,
  type Reference,
  type ReferenceSourceResponse,
} from "@contracts";

import { refreshAccessToken } from "@/auth/refresh";
import { ReferenceCard } from "@/components/artifact/ReferenceCard";
import { useReferences } from "@/components/artifact/use-artifact";
import { AppText } from "@/components/AppText";
import { useTheme } from "@/theme/ThemeProvider";

import { balanceCopy, failureCopy, readerCopy, sourceUnavailableCopy, unlockedNotice } from "./copy";
import { CrossRefs } from "./CrossRefs";
import { libraryWebUrl } from "./exits";
import { RefusalCard } from "./RefusalCard";
import { SourceExits } from "./SourceExits";
import { SourceRowView } from "./SourceRowView";
import { extractSourceContent, sourceRows, type SourceRow } from "./source-rows";
import { useReferenceSource } from "./use-reference-source";

export interface SourceReaderProps {
  itemId: string;
  n: number;
}

/**
 * The native source reader (plan 05 §R2–R4). The reference card renders at
 * once from the FREE list (normally already cached by the artifact sheet);
 * the METERED reveal fires once, on open, only when the list says a source
 * exists. Long bodies are block-chunked into a virtualised list.
 */
export function SourceReader({ itemId, n }: SourceReaderProps) {
  const { colours } = useTheme();
  const insets = useSafeAreaInsets();
  const refs = useReferences(itemId);
  const reference = refs.data?.find((r) => r.n === n) ?? null;

  // Wait for the free list (no charge for a stub card). A list that failed, or
  // a number it does not know, still asks — the backend has the last word.
  const listSettled = !refs.isPending;
  const knownNoSource = reference !== null && !reference.has_source;
  const source = useReferenceSource(itemId, n, listSettled && !knownNoSource);
  const [relogging, setRelogging] = useState(false);

  const result = source.data;
  const ok = result?.ok ? result.data : null;

  const rows = useMemo<SourceRow[]>(() => {
    if (!ok) return [];
    const libraryUrl = libraryWebUrl(ok.library_url ?? reference?.library_url);
    const external = reference ? officialUrlFor(reference, ok.source_view) : sourceViewExternalUrl(ok.source_view);
    return sourceRows(ok.source_view, {
      hasFullTextExit: !!(libraryUrl || external),
      definiteType: reference ? referenceDefiniteType(reference) : "النظام",
    });
  }, [ok, reference]);

  const login = async () => {
    setRelogging(true);
    try {
      // A dead refresh token signs the user out (the session store routes to
      // login) — the user asked for it with this button; nothing ejects them
      // on its own.
      await refreshAccessToken();
      await source.refetch();
    } catch {
      // Network failure: stay here; the card is still on screen.
    } finally {
      setRelogging(false);
    }
  };

  const header = (
    <View style={styles.header}>
      {reference ? (
        <ReferenceCard reference={reference} snippetLines={4} />
      ) : refs.isPending ? null : (
        <AppText variant="sm" tone="muted">
          {readerCopy.missingReference}
        </AppText>
      )}
      <ReaderState
        reference={reference}
        loading={refs.isPending || source.isFetching}
        knownNoSource={knownNoSource}
        result={result}
        ok={ok}
        onRetry={() => void source.refetch()}
        onLogin={() => void login()}
        relogging={relogging}
        loaderColour={colours.primary}
      />
    </View>
  );

  return (
    <FlashList
      data={rows}
      keyExtractor={(row) => row.key}
      getItemType={(row) => row.kind}
      renderItem={({ item }) => <SourceRowView row={item} />}
      ListHeaderComponent={header}
      ListFooterComponent={<ReaderFooter reference={reference} ok={ok} showCrossRefs={!!result || knownNoSource} />}
      contentContainerStyle={{ paddingHorizontal: 16, paddingTop: 12, paddingBottom: 32 + insets.bottom }}
    />
  );
}

function ReaderState({
  reference,
  loading,
  knownNoSource,
  result,
  ok,
  onRetry,
  onLogin,
  relogging,
  loaderColour,
}: {
  reference: Reference | null;
  loading: boolean;
  knownNoSource: boolean;
  result: ReturnType<typeof useReferenceSource>["data"];
  ok: ReferenceSourceResponse | null;
  onRetry: () => void;
  onLogin: () => void;
  relogging: boolean;
  loaderColour: string;
}) {
  if (knownNoSource) return <RefusalCard copy={sourceUnavailableCopy} />;
  if (loading && !relogging) {
    return (
      <View style={styles.loading} accessibilityLiveRegion="polite">
        <ActivityIndicator color={loaderColour} />
        <AppText variant="sm" tone="muted">
          {readerCopy.loading}
        </AppText>
      </View>
    );
  }
  if (ok) {
    return (
      <AppText variant="h3" weight={700} accessibilityRole="header" selectable>
        {ok.source_view.title || (reference ? referenceLabel(reference) : "المصدر")}
      </AppText>
    );
  }
  if (!result || result.ok) return null;

  const { copy, action } = failureCopy(result);
  const label = action === "retry" ? readerCopy.retry : action === "login" ? readerCopy.login : readerCopy.dismiss;
  const onAction = action === "retry" ? onRetry : action === "login" ? onLogin : () => router.back();
  return <RefusalCard copy={copy} actionLabel={label} onAction={onAction} actionLoading={relogging} />;
}

function ReaderFooter({
  reference,
  ok,
  showCrossRefs,
}: {
  reference: Reference | null;
  ok: ReferenceSourceResponse | null;
  showCrossRefs: boolean;
}) {
  const { colours, radius } = useTheme();
  const crossRefs = showCrossRefs ? (reference?.cross_refs ?? []) : [];
  if (!ok) return <CrossRefs crossRefs={crossRefs} />;

  const view = ok.source_view;
  const notice = unlockedNotice(ok.unlocked);
  const charged = ok.unlocked.reason === "granted";
  const balance = ok.balance;
  const remaining = balance && balance.limit !== null ? Math.max(balance.limit - balance.used, 0) : null;
  // The reveal's answer wins; the card's is the fallback (compliance guides).
  const libraryUrl = libraryWebUrl(ok.library_url ?? reference?.library_url);
  const official = reference ? officialUrlFor(reference, view) : sourceViewExternalUrl(view);
  const title = view.title || (reference ? referenceLabel(reference) : "");

  return (
    <View style={styles.footer}>
      <CrossRefs crossRefs={crossRefs} />
      {notice ? (
        <View
          accessibilityRole="text"
          style={[
            styles.notice,
            { borderRadius: radius.default, backgroundColor: charged ? colours.accentSoft : colours.surface2 },
          ]}
        >
          <AppText variant="xs" tone={charged ? "primary" : "muted"}>
            {notice}
          </AppText>
          {balance && balance.limit !== null && remaining !== null ? (
            <View style={[styles.chip, { backgroundColor: colours.pillBg }]}>
              <AppText variant="xs" weight={600} style={{ color: colours.pillFg }}>
                {remaining > 0 ? balanceCopy.remaining(remaining, balance.limit) : balanceCopy.exhausted}
              </AppText>
            </View>
          ) : null}
        </View>
      ) : null}
      <SourceExits
        officialUrl={official}
        libraryUrl={libraryUrl}
        libraryLabel={reference ? referenceLibraryExitLabel(reference) : "فتح المصدر في ريحان"}
        content={extractSourceContent(view)}
        title={title}
      />
    </View>
  );
}

const styles = StyleSheet.create({
  header: { gap: 12, marginBottom: 12 },
  loading: { flexDirection: "row", alignItems: "center", gap: 8, paddingVertical: 12 },
  footer: { gap: 12 },
  notice: { padding: 10, gap: 6, flexDirection: "row", flexWrap: "wrap", alignItems: "center", marginTop: 12 },
  chip: { borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 },
});
