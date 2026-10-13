// Shared contracts — pure TypeScript consumed by BOTH the Next.js web app
// (`@/lib/contracts`) and the Expo mobile app (`@contracts`).
// See README.md in this folder for the rules. Bump when a breaking contract
// change lands (rule 7).
export const CONTRACTS_VERSION = 0;

export * from "./app-config";
export * from "./auth";
export * from "./errors";
export * from "./legal";
export * from "./numerals";
