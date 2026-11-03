import assert from "node:assert/strict";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { build } from "vite";

const lock = JSON.parse(readFileSync("package-lock.json", "utf8"));
const modules = new Set();
await build({ mode: "static-preview", plugins: [{
  name: "preview-runtime-module-audit",
  generateBundle(_options, bundle) {
    for (const chunk of Object.values(bundle)) {
      if (chunk.type !== "chunk") continue;
      for (const id of Object.keys(chunk.modules)) {
        const path = id.replaceAll("\\", "/").split("/web/").at(-1).replace(/^\0/, "");
        modules.add(path);
        if (!path.includes("node_modules/")) continue;
        const pkg = path.match(/^(.*node_modules\/(?:@[^/]+\/)?[^/]+)/)?.[1];
        assert(pkg && lock.packages[pkg] && !lock.packages[pkg].dev,
          `Development or unknown package in preview: ${path}`);
      }
    }
  },
}] });
mkdirSync("../build/preview-evidence", { recursive: true });
writeFileSync("../build/preview-evidence/bundle-modules.json", JSON.stringify([...modules].sort(), null, 2) + "\n");
