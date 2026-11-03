import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import process from "node:process";

export function verifyEnvironment(environment, branches) {
  assert.equal(environment.deployment_branch_policy?.custom_branch_policies, true, "Missing branch restrictions");
  assert(environment.protection_rules?.some(rule => rule.type === "required_reviewers" &&
    rule.prevent_self_review === true && rule.reviewers?.length > 0), "Missing independent required reviewer");
  assert.equal(branches.total_count, 1, "Only main may publish");
  assert.equal(branches.branch_policies?.length, 1, "Missing/incomplete branch policy");
  assert.equal(branches.branch_policies[0].name, "main", "Only main may publish");
  assert.equal(branches.branch_policies[0].type, "branch", "Tags may not publish");
}

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  verifyEnvironment(...process.argv.slice(2, 4).map(path => JSON.parse(readFileSync(path, "utf8"))));
}
