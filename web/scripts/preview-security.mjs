import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
import { fileURLToPath } from "node:url";
import process from "node:process";
import console from "node:console";
import { auditPreview } from "./audit-preview.mjs";

const sha256 = value => createHash("sha256").update(value).digest("hex");
const canonical = value => JSON.stringify(value, (_key, item) =>
  item && typeof item === "object" && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(key => [key, item[key]])) : item);

export function verifySecurity({ full, production, fullExit, productionExit, policy, lockBytes, artifact, now = Date.now() }) {
  assert.equal(policy.scope, "github-radar/v0.1.0-preview/static-only");
  assert.equal(sha256(lockBytes), policy.lock_sha256, "Unreviewed lockfile");
  assert.deepEqual(artifact, policy.artifact, "Unreviewed artifact bytes");
  for (const report of [full, production]) {
    assert.equal(report.auditReportVersion, 2, "Missing/unsupported audit report");
    assert(!report.error && report.vulnerabilities && report.metadata?.vulnerabilities, "Audit unavailable");
    assert(typeof report.vulnerabilities === "object" && !Array.isArray(report.vulnerabilities), "Malformed audit findings");
    const counts = { info: 0, low: 0, moderate: 0, high: 0, critical: 0, total: 0 };
    for (const entry of Object.values(report.vulnerabilities)) {
      assert(Object.hasOwn(counts, entry.severity) && entry.severity !== "total", "Unknown severity");
      counts[entry.severity]++; counts.total++;
    }
    assert.deepEqual(report.metadata.vulnerabilities, counts, "Incomplete/inconsistent audit");
  }
  assert.equal(productionExit, 0, "Production audit failed");
  assert.equal(production.metadata.vulnerabilities.total, 0, "Production vulnerability");
  assert.equal(fullExit, full.metadata.vulnerabilities.total ? 1 : 0, "Full audit failed unexpectedly");
  assert.equal(full.metadata.vulnerabilities.high + full.metadata.vulnerabilities.critical, 0, "High/critical finding");
  assert.equal(sha256(canonical(full.vulnerabilities)), policy.vulnerabilities_sha256, "New or changed advisory/path");
  const packages = JSON.parse(lockBytes).packages;
  for (const entry of Object.values(full.vulnerabilities)) {
    assert.equal(entry.severity, "moderate", "Only reviewed moderate findings qualify");
    for (const path of entry.nodes) assert.equal(packages[path]?.dev, true, `Non-development path: ${path}`);
  }
  const reviewed = Date.parse(policy.reviewed_at), expires = Date.parse(policy.expires_at);
  assert(Number.isFinite(now) && reviewed <= now && now < expires && expires - reviewed <= 14 * 86400000,
    "Expired or invalid exception window");
  assert(policy.approval, "Exception is UNAPPROVED; operator risk decision required");
  assert(/^[a-z\d](?:[a-z\d-]{0,38})$/i.test(policy.approval.approver ?? ""), "Missing operator identity");
  const approved = Date.parse(policy.approval.approved_at);
  assert(reviewed <= approved && approved <= now, "Invalid approval date");
  assert(/^https:\/\/github\.com\/zinverno\/github-radar\/pull\/6#(?:issuecomment|pullrequestreview)-\d+$/.test(policy.approval.record ?? ""), "Missing operator approval record");
  // A moderate application exception cannot clear the separately reviewed toolchain highs.
  assert.deepEqual(policy.toolchain_blockers, [], "Unresolved npm toolchain high findings; separate verified remediation required");
  return { status: "PASS", scope: policy.scope, expires_at: policy.expires_at, approval: policy.approval };
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  const evidence = resolve(process.argv[2] ?? "../build/preview-evidence");
  const json = path => JSON.parse(readFileSync(path, "utf8"));
  const exitCode = path => {
    const value = readFileSync(path, "utf8").trim();
    assert(/^[01]$/.test(value), "Missing or invalid npm audit exit status");
    return Number(value);
  };
  try {
    const result = verifySecurity({
      full: json(`${evidence}/npm-full.json`), production: json(`${evidence}/npm-production.json`),
      fullExit: exitCode(`${evidence}/npm-full.exit`),
      productionExit: exitCode(`${evidence}/npm-production.exit`),
      policy: json("../docs/security/static-preview-policy.json"),
      lockBytes: readFileSync("package-lock.json"), artifact: auditPreview("dist-preview"),
    });
    writeFileSync(`${evidence}/security-gate.json`, JSON.stringify(result, null, 2) + "\n");
    console.log(JSON.stringify(result));
  } catch (error) {
    const result = { status: "BLOCKED", reason: error.message };
    writeFileSync(`${evidence}/security-gate.json`, JSON.stringify(result, null, 2) + "\n");
    console.error(JSON.stringify(result)); process.exitCode = 1;
  }
}
