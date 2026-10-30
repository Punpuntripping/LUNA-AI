import { SymbolView } from "expo-symbols";
import type { ComponentProps } from "react";
import type { ColorValue, StyleProp, ViewStyle } from "react-native";

// One place for the app's glyphs: SF Symbols on iOS, Material Symbols on
// Android (expo-symbols; both names are type-checked). Only names listed here
// are used, so a missing glyph is a compile error, not a blank square.
//
// Direction: every glyph below is direction-neutral (up/down arrows, stop,
// copy…) or deliberately physical (`nextStep` points up-left = towards the
// composer's start in RTL, same as the web's ArrowUpLeft). Back arrows are
// drawn by StackHeader, never here.

type SymbolName = ComponentProps<typeof SymbolView>["name"];

const ICONS = {
  send: { ios: "arrow.up", android: "arrow_upward" },
  stop: { ios: "stop.fill", android: "stop" },
  copy: { ios: "doc.on.doc", android: "content_copy" },
  edit: { ios: "pencil", android: "edit" },
  regenerate: { ios: "arrow.clockwise", android: "refresh" },
  star: { ios: "star.fill", android: "star" },
  search: { ios: "magnifyingglass", android: "search" },
  settings: { ios: "gearshape", android: "settings" },
  newChat: { ios: "square.and.pencil", android: "edit_square" },
  more: { ios: "ellipsis", android: "more_horiz" },
  close: { ios: "xmark", android: "close" },
  check: { ios: "checkmark", android: "check" },
  checkCircle: { ios: "checkmark.circle.fill", android: "check_circle" },
  circle: { ios: "circle", android: "radio_button_unchecked" },
  chevronDown: { ios: "chevron.down", android: "keyboard_arrow_down" },
  arrowDown: { ios: "arrow.down", android: "arrow_downward" },
  question: { ios: "questionmark.circle", android: "help" },
  info: { ios: "info.circle", android: "info" },
  error: { ios: "exclamationmark.circle", android: "error" },
  sparkles: { ios: "sparkles", android: "auto_awesome" },
  deepSearch: { ios: "text.magnifyingglass", android: "manage_search" },
  draft: { ios: "pencil.line", android: "edit_note" },
  apply: { ios: "scale.3d", android: "gavel" },
  open: { ios: "book", android: "menu_book" },
  answer: { ios: "text.bubble", android: "quickreply" },
  nextStep: { ios: "arrow.up.left", android: "north_west" },
  template: { ios: "doc.text", android: "description" },
  chat: { ios: "bubble.left", android: "chat_bubble" },
  trash: { ios: "trash", android: "delete" },
  hide: { ios: "eye.slash", android: "visibility_off" },
  // WI preview wall (web gate panels: lucide Lock).
  lock: { ios: "lock", android: "lock" },
  // Push opt-in (web PushReadyChip / الإشعارات: lucide BellRing).
  bellRing: { ios: "bell.badge", android: "notifications_active" },
  // «رسائل ريحان» beside the settings gear (web AnnouncementsBell: lucide Bell).
  bell: { ios: "bell", android: "notifications" },
  // Agent-output action bar (web WorkspaceItemActionBar: lucide Eye, Pencil,
  // Share2, BookmarkPlus, Download, ThumbsUp/Down; ExportMenu FileText/FileType2).
  // Android's Material Symbols font has no filled variant, so an active thumb
  // is told apart by colour there (iOS also switches to the `.fill` glyph).
  preview: { ios: "eye", android: "visibility" },
  share: { ios: "square.and.arrow.up", android: "share" },
  bookmarkAdd: { ios: "bookmark", android: "bookmark_add" },
  download: { ios: "square.and.arrow.down", android: "download" },
  filePdf: { ios: "doc.richtext", android: "picture_as_pdf" },
  fileWord: { ios: "doc.text", android: "description" },
  link: { ios: "link", android: "link" },
  shareQuestion: { ios: "questionmark.bubble", android: "quiz" },
  shareArticle: { ios: "newspaper", android: "newspaper" },
  thumbUp: { ios: "hand.thumbsup", android: "thumb_up" },
  thumbUpFill: { ios: "hand.thumbsup.fill", android: "thumb_up" },
  thumbDown: { ios: "hand.thumbsdown", android: "thumb_down" },
  thumbDownFill: { ios: "hand.thumbsdown.fill", android: "thumb_down" },
  // Composer attachments (web ComposerPlusMenu: lucide Plus, Paperclip).
  plus: { ios: "plus", android: "add" },
  attachFile: { ios: "paperclip", android: "attach_file" },
  photos: { ios: "photo.on.rectangle", android: "photo_library" },
  camera: { ios: "camera", android: "photo_camera" },
  image: { ios: "photo", android: "image" },
  // Composer extras (web ComposerPlusMenu: lucide BookText, SlidersHorizontal).
  blog: { ios: "text.book.closed", android: "auto_stories" },
  sliders: { ios: "slider.horizontal.3", android: "tune" },
  // Profession cards (web onboarding-content: lucide Gavel, Rocket, BadgeCheck,
  // Users). SF Symbols has no gavel or rocket: courthouse columns / briefcase.
  professionLegal: { ios: "building.columns", android: "gavel" },
  professionEntrepreneur: { ios: "briefcase", android: "rocket_launch" },
  professionSpecialist: { ios: "checkmark.seal", android: "verified" },
  professionIndividual: { ios: "person.2", android: "group" },
} as const satisfies Record<string, Exclude<SymbolName, string>>;

export type IconName = keyof typeof ICONS;

export interface IconProps {
  name: IconName;
  size?: number;
  colour: ColorValue;
  style?: StyleProp<ViewStyle>;
}

export function Icon({ name, size = 20, colour, style }: IconProps) {
  return (
    <SymbolView
      name={ICONS[name]}
      size={size}
      tintColor={colour}
      style={style}
      accessible={false}
      importantForAccessibility="no"
    />
  );
}
