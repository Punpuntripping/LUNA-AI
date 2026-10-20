// Config plugin — Android MainActivity `configChanges` + storage permission scope.
//
// 1) `fontScale`: changing the system font size otherwise RECREATES the Activity,
//    which remounts the JS tree and drops an open chat back to the list (QA F8).
//    With `fontScale` declared, Android delivers `onConfigurationChanged` instead;
//    RN's ReactHost re-reads the display metrics and re-lays out the surfaces
//    (`enableFontScaleChangesUpdatingLayout`, on by default since RN 0.80), and
//    `useWindowDimensions().fontScale` updates. The Expo template already sets
//    keyboard|keyboardHidden|orientation|screenSize|screenLayout|uiMode|
//    smallestScreenSize|locale|layoutDirection — only the missing flags are added.
//    `density` (the «Display size» setting) is deliberately NOT added: RN only
//    re-lays out, it does not remount views at the new density, so a recreate is
//    the correct (if lossy) response there.
//
// 2) WRITE_EXTERNAL_STORAGE: the template and expo-image-picker declare it up to
//    API 32, but it grants nothing once scoped storage applies (API 29+). Only
//    launchCameraAsync on Android 7–9 (API 24–28) needs it, so it is capped at 28.
//    READ_EXTERNAL_STORAGE is removed entirely via `android.blockedPermissions`
//    (the photo picker needs no permission).

const { AndroidConfig, withAndroidManifest } = require("expo/config-plugins");

const REQUIRED_CONFIG_CHANGES = ["fontScale"];
const WRITE_STORAGE = "android.permission.WRITE_EXTERNAL_STORAGE";
const WRITE_STORAGE_MAX_SDK = "28";

function addConfigChanges(manifest) {
  const activity = AndroidConfig.Manifest.getMainActivityOrThrow(manifest);
  const current = (activity.$["android:configChanges"] ?? "").split("|").filter(Boolean);
  const merged = [...current];
  for (const flag of REQUIRED_CONFIG_CHANGES) {
    if (!merged.includes(flag)) merged.push(flag);
  }
  activity.$["android:configChanges"] = merged.join("|");
}

function capWriteStorage(manifest) {
  AndroidConfig.Manifest.ensureToolsAvailable(manifest);
  const permissions = manifest.manifest["uses-permission"] ?? [];
  const existing = permissions.find((p) => p.$["android:name"] === WRITE_STORAGE);
  // Blocked elsewhere (tools:node="remove")? Leave it alone.
  if (existing && existing.$["tools:node"] === "remove") return;
  const entry = existing ?? { $: { "android:name": WRITE_STORAGE } };
  entry.$["android:maxSdkVersion"] = WRITE_STORAGE_MAX_SDK;
  // The library manifests declare a higher maxSdkVersion — ours wins the merge.
  entry.$["tools:replace"] = "android:maxSdkVersion";
  if (!existing) {
    permissions.push(entry);
    manifest.manifest["uses-permission"] = permissions;
  }
}

module.exports = function withAndroidConfigChanges(config) {
  return withAndroidManifest(config, (cfg) => {
    addConfigChanges(cfg.modResults);
    capWriteStorage(cfg.modResults);
    return cfg;
  });
};
