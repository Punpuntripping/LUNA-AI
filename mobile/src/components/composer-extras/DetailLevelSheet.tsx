import { DETAIL_LEVEL_OPTIONS } from "@contracts";
import { View } from "react-native";

import { Sheet } from "@/components/ActionSheet";
import { AppText } from "@/components/AppText";
import { Button } from "@/components/Button";
import { Icon } from "@/components/Icon";
import { useDetailLevel } from "@/components/settings/use-detail-level";
import { useTheme } from "@/theme/ThemeProvider";

import { ListRow, SheetHeading, sheetStyles } from "./parts";

export interface DetailLevelSheetProps {
  visible: boolean;
  onClose: () => void;
}

/**
 * «مستوى التفصيل» from the composer «+» — the web ComposerPlusMenu submenu:
 * «اختر مستوى التفصيل للإجابة», مختصر / متوسط / مفصّل with a check on the
 * current one, disabled until the preference loaded and while a change saves.
 * Same value as the settings screen (`user_preferences.detail_level`).
 */
export function DetailLevelSheet({ visible, onClose }: DetailLevelSheetProps) {
  return (
    <Sheet visible={visible} onClose={onClose} testID="detail-level-sheet">
      {visible ? <Content onClose={onClose} /> : null}
    </Sheet>
  );
}

function Content({ onClose }: { onClose: () => void }) {
  const { colours } = useTheme();
  const { level, loading, saving, setLevel } = useDetailLevel();
  return (
    <View style={sheetStyles.body}>
      <SheetHeading icon="sliders" title="مستوى التفصيل" />
      <AppText variant="xs" tone="muted">
        اختر مستوى التفصيل للإجابة
      </AppText>
      <View accessibilityRole="radiogroup" accessibilityLabel="مستوى التفصيل">
        {DETAIL_LEVEL_OPTIONS.map((opt) => (
          <ListRow
            key={opt.value}
            label={opt.label}
            disabled={loading || saving}
            trailing={opt.value === level ? <Icon name="check" size={16} colour={colours.primary} /> : null}
            onPress={() => {
              setLevel(opt.value);
              onClose();
            }}
            testID={`composer-detail-level-${opt.value}`}
          />
        ))}
      </View>
      <View style={sheetStyles.buttons}>
        <Button label="إلغاء" variant="ghost" onPress={onClose} />
      </View>
    </View>
  );
}
