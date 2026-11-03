import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { lstatSync, readdirSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import console from "node:console";

// Scan the exact upload directory, not source files or a different build mode.
export function auditPreview(directory) {
  assert(lstatSync(directory).isDirectory() && !lstatSync(directory).isSymbolicLink(), "Non-regular artifact directory");
  assert.deepEqual(readdirSync(directory).sort(), ["assets", "index.html"]);
  assert(!lstatSync(resolve(directory, "assets")).isSymbolicLink(), "Symlinked assets");
  const files = ["index.html", ...readdirSync(resolve(directory, "assets")).map(name => `assets/${name}`)];
  const forbidden = /sourceMappingURL|\/api\/v1|DATABASE_URL|WORKSPACE_ADMIN_TOKEN|AI_API_KEY|neon\.tech|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----|github_pat_[A-Za-z0-9_]+|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[A-Z0-9]{16}|sk-(?:proj-)?[A-Za-z0-9_-]{24,}|postgres(?:ql)?:\/\//i;
  const manifest = files.sort().map(name => {
    assert(name === "index.html" || /^assets\/(?:index|api)-[\w-]+\.(?:js|css)$/.test(name), `Unexpected artifact: ${name}`);
    const path = resolve(directory, name), stat = lstatSync(path);
    assert(stat.isFile() && !stat.isSymbolicLink(), `Non-regular artifact: ${name}`);
    const bytes = readFileSync(path);
    assert(!forbidden.test(bytes.toString()), `Forbidden content in ${name}`);
    return { file: name, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") };
  });
  const html = readFileSync(resolve(directory, "index.html"), "utf8");
  assert(html.includes("DEMO DATA") && html.includes("v0.1.0-preview"), "Missing demo disclosure");
  const csp = html.match(/<meta http-equiv="Content-Security-Policy" content="([^"]+)"/i)?.[1].replaceAll("&#39;", "'");
  assert(csp?.includes("connect-src 'none'") && csp.includes("form-action 'none'"), "Missing preview CSP");
  for (const match of html.matchAll(/(?:src|href)="([^"]+)"/g)) {
    assert(match[1] === "data:," || match[1].startsWith("/github-radar/assets/"), "Unexpected asset origin/path");
    if (match[1] !== "data:,") assert(files.includes(match[1].slice("/github-radar/".length)), "Missing referenced asset");
  }
  assert(manifest.some(f => f.file.endsWith(".js")) && manifest.some(f => f.file.endsWith(".css")));
  return { status: "PASS", total_bytes: manifest.reduce((sum, f) => sum + f.bytes, 0), files: manifest };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  console.log(JSON.stringify(auditPreview(resolve(process.argv[2] ?? "dist-preview")), null, 2));
}
