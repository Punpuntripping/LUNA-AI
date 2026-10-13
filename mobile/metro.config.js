// Metro config: Expo defaults + access to the shared contracts folder that
// lives in the web app (`../frontend/lib/contracts`, imported as `@contracts`).
// The `@contracts` alias itself comes from tsconfig.json `paths` (Expo resolves
// tsconfig paths natively); this file only makes the folder visible to Metro
// and guarantees modules are resolved from mobile's OWN node_modules.
const path = require("path");
const { getDefaultConfig } = require("expo/metro-config");

const projectRoot = __dirname;
const contractsRoot = path.resolve(projectRoot, "../frontend/lib/contracts");
const frontendNodeModules = path.resolve(projectRoot, "../frontend/node_modules");

const config = getDefaultConfig(projectRoot);

// Let Metro watch/serve files from the contracts folder (outside projectRoot).
config.watchFolders = [...(config.watchFolders ?? []), contractsRoot];

// Bare imports (react, react-native, ...) resolve from mobile/node_modules only.
config.resolver.nodeModulesPaths = [path.resolve(projectRoot, "node_modules")];

// Never pick anything up from the web app's node_modules (a second React
// copy would break the app). Hierarchical lookup from a contracts file would
// otherwise walk up into ../frontend/node_modules.
const escapeRe = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const existingBlockList = config.resolver.blockList ?? [];
config.resolver.blockList = [
  ...(Array.isArray(existingBlockList) ? existingBlockList : [existingBlockList]),
  new RegExp(`^${escapeRe(frontendNodeModules)}[\\\\/].*`),
];

module.exports = config;
