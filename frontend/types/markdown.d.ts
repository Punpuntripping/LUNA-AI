// Lets TypeScript accept `import md from "...md"`. The actual bundling is done
// by the webpack `asset/source` rule in next.config.mjs, which resolves the
// import to the file's raw text contents.
declare module "*.md" {
  const content: string;
  export default content;
}
