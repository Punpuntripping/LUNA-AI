// Moved to the shared contracts folder (used by web + mobile) as a zod-free
// port — proven equivalent to the zod original by
// `lib/__tests__/contracts-parsers-equivalence.test.ts`. Re-exported here so
// existing `@/lib/next-steps` imports keep working unchanged.
export { parseNextSteps } from "@/lib/contracts/next-steps";
