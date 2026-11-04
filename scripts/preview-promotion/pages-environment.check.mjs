import assert from "node:assert/strict";
import { test } from "node:test";
import { verifyEnvironment } from "./pages-environment.mjs";

test("promotion requires independent review and the main branch only", () => {
  const environment = { deployment_branch_policy: { custom_branch_policies: true },
    protection_rules: [{ type: "required_reviewers", prevent_self_review: true, reviewers: [{ type: "User" }] }] };
  const branches = { total_count: 1, branch_policies: [{ name: "main", type: "branch" }] };
  verifyEnvironment(environment, branches);
  for (const bad of [{}, { ...environment, protection_rules: [] },
    { ...environment, protection_rules: [{ ...environment.protection_rules[0], prevent_self_review: false }] },
    { ...environment, protection_rules: [{ ...environment.protection_rules[0], reviewers: [] }] }]) {
    assert.throws(() => verifyEnvironment(bad, branches));
  }
  for (const bad of [{}, { ...branches, total_count: 2 },
    { ...branches, branch_policies: [{ name: "main", type: "tag" }] },
    { ...branches, branch_policies: [{ name: "*", type: "branch" }] }]) {
    assert.throws(() => verifyEnvironment(environment, bad));
  }
});
