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
export * from "./agents";
export * from "./conversations";
export * from "./messages";
export * from "./references";
export * from "./share";
export * from "./sse";
export * from "./templates";
export * from "./blogs";
export * from "./preferences";

// Pure helpers (mobile Phase 2 — see README "What lives here").
export * from "./citations";
export * from "./library-refusal";
export * from "./markdown/images";
export * from "./markdown/plain-text";
export * from "./markdown/streaming";
export * from "./next-steps";
export * from "./placeholder";
export * from "./reference-labels";
export * from "./references-copy";
export * from "./sse-parser";
export * from "./templates-used";
export * from "./utf8";
