import assert from "node:assert/strict";
import { mkdtempSync, mkdirSync, rmSync, writeFileSync, symlinkSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { auditPreview } from "./audit-preview.mjs";

test("artifact gate accepts only labeled static files and rejects leaks, maps and symlinks", () => {
  const dir = mkdtempSync(join(tmpdir(), "radar-artifact-check-"));
  try {
    mkdirSync(join(dir, "assets"));
    writeFileSync(join(dir, "index.html"), `<title>v0.1.0-preview DEMO DATA</title><meta http-equiv="Content-Security-Policy" content="connect-src 'none'; form-action 'none'"><script src="/github-radar/assets/index-test.js"></script>`);
    writeFileSync(join(dir, "assets/index-test.css"), "body{}");
    writeFileSync(join(dir, "assets/index-test.js"), "export const demo = true;");
    assert.equal(auditPreview(dir).status, "PASS");
    for (const content of ["DATABASE_URL", "//# sourceMappingURL=secret.map", "ghp_" + "x".repeat(36), "/api/v1/workspace"]) {
      writeFileSync(join(dir, "assets/index-test.js"), content);
      assert.throws(() => auditPreview(dir), /Forbidden content/);
    }
    writeFileSync(join(dir, "assets/index-test.js"), "");
    for (const url of ["https://unexpected.example/collect", "wss://unexpected.example", "http://localhost:8000/api"]) {
      writeFileSync(join(dir, "assets/index-test.js"), url);
      assert.throws(() => auditPreview(dir), /Unexpected URL/);
    }
    writeFileSync(join(dir, "assets/index-test.js"), "");
    writeFileSync(join(dir, "assets/index-test.js.map"), "{}");
    assert.throws(() => auditPreview(dir), /Unexpected artifact/);
    rmSync(join(dir, "assets/index-test.js.map"));
    rmSync(join(dir, "assets/index-test.js"));
    symlinkSync(join(dir, "index.html"), join(dir, "assets/index-test.js"));
    assert.throws(() => auditPreview(dir), /Non-regular artifact/);
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
