// https://docs.expo.dev/guides/using-eslint/
// Expo's flat config + the product rules the web enforces
// (frontend/.eslintrc.json): Latin digits only, no bare "ar" locales. Plus two
// phone-specific rules: no argument-less toLocale*() (an Arabic-language
// device renders ٠-٩) and no physical left/right layout props (RTL app — use
// start/end, marginStart, paddingEnd, …).
const { defineConfig } = require("eslint/config");
const expoConfig = require("eslint-config-expo/flat");

const ARABIC_INDIC = "[\\u0660-\\u0669\\u06F0-\\u06F9]";
const DIGITS_MSG =
  "Arabic-Indic digits (٠-٩) are never rendered by the app — write Latin digits and format through src/lib/format.ts (AR_NUM_LOCALE / AR_DATE_LOCALE from @contracts).";
const BARE_AR_MSG =
  'A bare "ar" / "ar-EG" / "ar-SA" locale resolves to the `arab` numbering system and emits Arabic-Indic digits. Use AR_NUM_LOCALE / AR_DATE_LOCALE from @contracts.';
const NO_ARG_LOCALE_MSG =
  "toLocale*() with no locale follows the DEVICE language — on an Arabic phone it renders ٠-٩. Use src/lib/format.ts or pass AR_NUM_LOCALE / AR_DATE_LOCALE.";
const PHYSICAL_MSG =
  "RTL app: use logical props (start/end, marginStart/marginEnd, paddingStart/paddingEnd, borderStartWidth, …), never left/right.";

const PHYSICAL_PROPS =
  "^(left|right|marginLeft|marginRight|paddingLeft|paddingRight|borderLeftWidth|borderRightWidth|borderLeftColor|borderRightColor|borderTopLeftRadius|borderTopRightRadius|borderBottomLeftRadius|borderBottomRightRadius)$";

module.exports = defineConfig([
  expoConfig,
  {
    ignores: ["dist/*", "scripts/*", ".expo/*"],
  },
  {
    files: ["app/**/*.{ts,tsx}", "src/**/*.{ts,tsx}"],
    rules: {
      "no-restricted-syntax": [
        "error",
        { selector: `Literal[value=/${ARABIC_INDIC}/]`, message: DIGITS_MSG },
        { selector: `TemplateElement[value.raw=/${ARABIC_INDIC}/]`, message: DIGITS_MSG },
        { selector: `JSXText[value=/${ARABIC_INDIC}/]`, message: DIGITS_MSG },
        {
          selector: "CallExpression[callee.property.name=/^toLocale(String|DateString|TimeString)$/] > Literal[value=/^ar([-_]|$)/]",
          message: BARE_AR_MSG,
        },
        { selector: "NewExpression[callee.object.name='Intl'] > Literal[value=/^ar([-_]|$)/]", message: BARE_AR_MSG },
        { selector: "CallExpression[callee.object.name='Intl'] > Literal[value=/^ar([-_]|$)/]", message: BARE_AR_MSG },
        {
          selector: "CallExpression[callee.property.name=/^toLocale(String|DateString|TimeString)$/][arguments.length=0]",
          message: NO_ARG_LOCALE_MSG,
        },
        { selector: "NewExpression[callee.object.name='Intl'][arguments.length=0]", message: NO_ARG_LOCALE_MSG },
        { selector: `Property[key.name=/${PHYSICAL_PROPS}/]`, message: PHYSICAL_MSG },
      ],
    },
  },
]);
