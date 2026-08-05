import assert from "node:assert/strict";
import test from "node:test";

import {
  countOpenDependabotPulls,
  DEPENDABOT_PULLS_API,
  IMAGE_SECURITY_RUNS_API,
  imageSecurityChecksApi,
  parseImageSecurityEvidence,
  parseImageSecurityRun,
} from "../lib/github-security-status.mjs";

const validRun = {
  workflow_runs: [
    {
      status: "completed",
      conclusion: "success",
      head_sha: "a".repeat(40),
      head_branch: "main",
      html_url:
        "https://github.com/j2d3/eth-validator-platform/actions/runs/123456",
      updated_at: "2026-08-05T09:31:47Z",
    },
  ],
};

const validEvidence = {
  check_runs: [
    {
      name: "Image findings - 21/21 exact subjects scanned - 11 coverage gaps - Critical 3 (2 fix available) - High 47 (31 fix available)",
      head_sha: "a".repeat(40),
      status: "completed",
      conclusion: "success",
      completed_at: "2026-08-05T13:42:11Z",
      html_url:
        "https://github.com/j2d3/eth-validator-platform/actions/runs/123456/job/789",
    },
  ],
};

test("accepts one exact main image-security run", () => {
  assert.deepEqual(parseImageSecurityRun(validRun), {
    status: "completed",
    conclusion: "success",
    sourceSha: "a".repeat(40),
    updatedAt: "2026-08-05T09:31:47Z",
    htmlUrl:
      "https://github.com/j2d3/eth-validator-platform/actions/runs/123456",
  });
});

test("rejects a run link outside the repository", () => {
  const response = structuredClone(validRun);
  response.workflow_runs[0].html_url = "https://attacker.invalid/run/123456";
  assert.throws(() => parseImageSecurityRun(response));
});

test("rejects a non-main workflow result", () => {
  const response = structuredClone(validRun);
  response.workflow_runs[0].head_branch = "feature";
  assert.throws(() => parseImageSecurityRun(response));
});

test("parses public finding counts bound to the exact workflow run", () => {
  const run = parseImageSecurityRun(validRun);
  assert.deepEqual(parseImageSecurityEvidence(validEvidence, run), {
    images: 21,
    exactSubjects: { scanned: 21, expected: 21 },
    coverageGaps: 11,
    critical: { total: 3, available: 2 },
    high: { total: 47, available: 31 },
    completedAt: "2026-08-05T13:42:11Z",
    htmlUrl:
      "https://github.com/j2d3/eth-validator-platform/actions/runs/123456/job/789",
  });
});

test("does not accept finding counts from another workflow run", () => {
  const response = structuredClone(validEvidence);
  response.check_runs[0].html_url =
    "https://github.com/j2d3/eth-validator-platform/actions/runs/999/job/789";
  assert.equal(parseImageSecurityEvidence(response, parseImageSecurityRun(validRun)), null);
});

test("rejects impossible public finding counts", () => {
  const response = structuredClone(validEvidence);
  response.check_runs[0].name =
    "Image findings - 21/21 exact subjects scanned - 11 coverage gaps - Critical 3 (4 fix available) - High 47 (31 fix available)";
  assert.throws(() =>
    parseImageSecurityEvidence(response, parseImageSecurityRun(validRun)),
  );
});

test("rejects a partial-coverage check where scanned != expected", () => {
  const response = structuredClone(validEvidence);
  response.check_runs[0].name =
    "Image findings - 20/21 exact subjects scanned - 11 coverage gaps - Critical 3 (2 fix available) - High 47 (31 fix available)";
  assert.throws(() =>
    parseImageSecurityEvidence(response, parseImageSecurityRun(validRun)),
  );
});

test("rejects the previous single-count check-name format", () => {
  const response = structuredClone(validEvidence);
  response.check_runs[0].name =
    "Image findings - 21 images - Critical 3 (2 fix available) - High 47 (31 fix available)";
  assert.throws(() =>
    parseImageSecurityEvidence(response, parseImageSecurityRun(validRun)),
  );
});

test("counts only open Dependabot pull requests", () => {
  assert.equal(
    countOpenDependabotPulls([
      { state: "open", user: { login: "dependabot[bot]" } },
      { state: "closed", user: { login: "dependabot[bot]" } },
      { state: "open", user: { login: "human" } },
    ]),
    1,
  );
});

test("uses public read-only GitHub endpoints", () => {
  assert.equal(
    IMAGE_SECURITY_RUNS_API,
    "https://api.github.com/repos/j2d3/eth-validator-platform/actions/workflows/image-security.yaml/runs?branch=main&per_page=1",
  );
  assert.equal(
    DEPENDABOT_PULLS_API,
    "https://api.github.com/repos/j2d3/eth-validator-platform/pulls?state=open&per_page=100",
  );
  assert.equal(
    imageSecurityChecksApi("a".repeat(40)),
    `https://api.github.com/repos/j2d3/eth-validator-platform/commits/${"a".repeat(40)}/check-runs?per_page=100`,
  );
});
