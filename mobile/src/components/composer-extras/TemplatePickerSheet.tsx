import { TEMPLATE_SEARCH_MIN_CHARS, templateMenuLabel, type PendingTemplate, type UserTemplate } from "@contracts";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FlatList, View } from "react-native";

import { listTemplates } from "@/api/templates";
import { Sheet } from "@/components/ActionSheet";
import { Button } from "@/components/Button";
import { useDebouncedValue } from "@/hooks/use-debounced-value";

import { templateKeys } from "./keys";
import { ListRow, ListStatus, SheetHeading, SheetInput, sheetStyles } from "./parts";

export interface TemplatePickerSheetProps {
  visible: boolean;
  onClose: () => void;
  /** The chosen قالب — ONE per message; the caller replaces any earlier pick. */
  onPick: (template: PendingTemplate) => void;
}

/**
 * «قالب» — the web composer «+» → قالب submenu (TemplateMenuList): قوالبي
 * newest-updated first, one row per template («قالب بدون عنوان» when the title
 * is blank), loading / error / empty lines. Fetches only while open. Adds the
 * قوالبي page's search (same `GET /templates?q=`, 3+ characters).
 */
export function TemplatePickerSheet({ visible, onClose, onPick }: TemplatePickerSheetProps) {
  return (
    <Sheet visible={visible} onClose={onClose} testID="template-picker-sheet">
      {visible ? <PickerContent onClose={onClose} onPick={onPick} /> : null}
    </Sheet>
  );
}

function PickerContent({ onClose, onPick }: Omit<TemplatePickerSheetProps, "visible">) {
  const [q, setQ] = useState("");
  const debounced = useDebouncedValue(q.trim(), 300);
  const term = debounced.length >= TEMPLATE_SEARCH_MIN_CHARS ? debounced : "";
  const query = useQuery({
    queryKey: templateKeys.list(term),
    queryFn: () => listTemplates(term),
    placeholderData: keepPreviousData,
  });

  const pick = (t: UserTemplate) => {
    onClose();
    onPick({ templateId: t.template_id, title: t.title });
  };

  let status: { text: string; loading?: boolean } | null = null;
  if (query.isPending) status = { text: "جارٍ التحميل...", loading: true };
  else if (query.isError) status = { text: "تعذّر تحميل القوالب" };
  else if (query.data.templates.length === 0) status = { text: term ? "لا توجد نتائج" : "لا توجد قوالب بعد" };

  return (
    <View style={sheetStyles.body}>
      <SheetHeading icon="template" title="قالب" />
      <SheetInput
        value={q}
        onChangeText={setQ}
        placeholder="ابحث في قوالبي…"
        accessibilityLabel="ابحث في قوالبي"
        returnKeyType="search"
        autoCorrect={false}
        testID="template-picker-search"
      />
      {status ? (
        <ListStatus text={status.text} loading={status.loading} />
      ) : (
        <FlatList
          style={sheetStyles.list}
          data={query.data?.templates ?? []}
          keyExtractor={(t) => t.template_id}
          keyboardShouldPersistTaps="handled"
          renderItem={({ item }) => (
            <ListRow
              icon="template"
              label={templateMenuLabel(item.title)}
              onPress={() => pick(item)}
              testID={`template-picker-${item.template_id}`}
            />
          )}
        />
      )}
      <View style={sheetStyles.buttons}>
        <Button label="إلغاء" variant="ghost" onPress={onClose} />
      </View>
    </View>
  );
}
