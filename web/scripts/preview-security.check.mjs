import assert from "node:assert/strict";
import { Buffer } from "node:buffer";
import { readFileSync } from "node:fs";
import { test } from "node:test";
import { verifySecurity } from "./preview-security.mjs";
import { verifyEnvironment } from "./pages-environment.mjs";

const json = name => JSON.parse(readFileSync(`../docs/security/static-preview-${name}.json`, "utf8"));
function reviewed() {
  const policy = json("policy");
  // A hypothetical approval exists only in memory; the committed proposal stays null.
  policy.approval = { approver: "test-operator", approved_at: "2026-11-03T01:00:00Z",
    record: "https://github.com/zinverno/github-radar/pull/6#issuecomment-123" };
  policy.toolchain_blockers = [];
  return { full: json("npm-full"), production: json("npm-production"), fullExit: 1, productionExit: 0,
    policy, lockBytes: readFileSync("package-lock.json"), artifact: globalThis.structuredClone(policy.artifact),
    now: Date.parse("2026-11-03T02:00:00Z") };
}

test("Pages requires an existing independent approval and main-only environment", () => {
  const env = { deployment_branch_policy: { custom_branch_policies: true },
    protection_rules: [{ type: "required_reviewers", prevent_self_review: true, reviewers: [{ type: "User" }] }] };
  const branches = { total_count: 1, branch_policies: [{ name: "main", type: "branch" }] };
  verifyEnvironment(env, branches);
  for (const bad of [{}, { ...env, deployment_branch_policy: null },
    { ...env, protection_rules: [] }, { ...env, protection_rules: [{ ...env.protection_rules[0], prevent_self_review: false }] },
    { ...env, protection_rules: [{ ...env.protection_rules[0], reviewers: [] }] }]) {
    assert.throws(() => verifyEnvironment(bad, branches));
  }
  for (const bad of [{}, { ...branches, total_count: 2 }, { ...branches, branch_policies: [] },
    { ...branches, branch_policies: [{ name: "main", type: "tag" }] },
    { ...branches, branch_policies: [{ name: "*", type: "branch" }] }]) {
    assert.throws(() => verifyEnvironment(env, bad));
  }
});

test("the exact hypothetical approved preview is the only permitted exception", () => {
  assert.equal(verifySecurity(reviewed()).status, "PASS");
});

for (const [name, mutate] of Object.entries({
  unapproved: x => { x.policy.approval = null; },
  expired: x => { x.now = Date.parse(x.policy.expires_at); },
  extension: x => { x.policy.expires_at = "2027-01-01T00:00:00Z"; },
  futureApproval: x => { x.policy.approval.approved_at = "2026-11-10T00:00:00Z"; },
  missingApprover: x => { x.policy.approval.approver = ""; },
  missingRecord: x => { x.policy.approval.record = ""; },
  newAdvisory: x => { x.full.vulnerabilities.minimatch.via[0].url += "-new"; },
  changedPath: x => { x.full.vulnerabilities.minimatch.nodes.push("node_modules/new/minimatch"); },
  changedRange: x => { x.full.vulnerabilities.minimatch.range = "*"; },
  critical: x => { x.full.vulnerabilities.minimatch.severity = "critical";
    x.full.metadata.vulnerabilities.moderate--; x.full.metadata.vulnerabilities.critical++; },
  high: x => { x.full.vulnerabilities.minimatch.severity = "high";
    x.full.metadata.vulnerabilities.moderate--; x.full.metadata.vulnerabilities.high++; },
  production: x => { x.production = globalThis.structuredClone(x.full); x.productionExit = 1; },
  registryError: x => { x.full = { error: { code: "EAI_AGAIN" } }; },
  missingReport: x => { x.full = {}; },
  incompleteCounts: x => { x.full.metadata.vulnerabilities.total--; },
  unexpectedExit: x => { x.fullExit = 2; },
  productionExit: x => { x.productionExit = 1; },
  lockChanged: x => { x.lockBytes = Buffer.from("{}"); },
  artifactChanged: x => { x.artifact.files[0].sha256 = "0".repeat(64); },
  artifactExtraFile: x => { x.artifact.files.push({ file: "secret.txt" }); },
  backendScope: x => { x.policy.scope = "backend"; },
  toolchainHighs: x => { x.policy.toolchain_blockers = json("policy").toolchain_blockers; },
})) {
  test(`preview gate blocks ${name}`, () => {
    const input = reviewed(); mutate(input); assert.throws(() => verifySecurity(input));
  });
}
