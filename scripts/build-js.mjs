/** Vendor the pinned standard Web Vitals build and its Apache-2.0 notice locally. */
import {readFile, writeFile} from "node:fs/promises";
const files = [
  ["node_modules/web-vitals/dist/web-vitals.iife.js", "app/assets/js/web-vitals.js"],
  ["node_modules/web-vitals/LICENSE", "app/assets/licenses/web-vitals-LICENSE.txt"],
];
for (const [source, target] of files) {
  let content = await readFile(new URL(`../${source}`, import.meta.url), "utf8");
  if (source.endsWith(".js")) content = content.replace(/\n?\/\/# sourceMappingURL=.*\n?/g, "\n");
  const output = new URL(`../${target}`, import.meta.url);
  if (process.argv.includes("--check")) {
    if (await readFile(output, "utf8") !== content) throw new Error(`Run npm run build:js: ${target}`);
  } else {
    await writeFile(output, content);
  }
}
