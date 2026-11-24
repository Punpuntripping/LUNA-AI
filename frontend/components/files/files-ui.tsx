"use client";

import { createContext, useContext, type ReactNode } from "react";

/**
 * Where the «الملفات» screens link to, and whether the Phase-3 actions are on
 * screen. One small context so a host can re-point them without threading
 * props through `FilesPage` and `WorkspacePane`.
 *
 * The app never provides it — the defaults ARE production. The dev preview
 * (`app/dev-preview/my-files`) is the one place that overrides it, to keep its
 * navigation inside the preview and to show the Phase-3 controls as inactive.
 */
export interface FilesUi {
  /** The page that shows one file (`/files/{id}`). */
  fileHref: (itemId: string) => string;
  /** «الملفات» itself (`/files`) — where a file page's back link returns. */
  libraryHref: string;
  /** One angle map's page (`/files/angles/{id}`, angle_map_ui.md rev 3). */
  angleMapHref: (mapId: string) => string;
  /**
   * Phase 3 («رفع ملف» on /files, «أضف إلى محادثة» on a card, «من الملفات» in
   * the pane's add menu). Not live: `false` hides them; `true` shows them as
   * visibly inactive controls («قريبًا») so their placement can be judged.
   */
  showUpcoming: boolean;
}

export const FILES_UI_DEFAULTS: FilesUi = {
  fileHref: (itemId) => `/files/${encodeURIComponent(itemId)}`,
  libraryHref: "/files",
  angleMapHref: (mapId) => `/files/angles/${encodeURIComponent(mapId)}`,
  showUpcoming: false,
};

const FilesUiContext = createContext<FilesUi>(FILES_UI_DEFAULTS);

export function FilesUiProvider({
  value,
  children,
}: {
  value: FilesUi;
  children: ReactNode;
}) {
  return <FilesUiContext.Provider value={value}>{children}</FilesUiContext.Provider>;
}

export function useFilesUi(): FilesUi {
  return useContext(FilesUiContext);
}
