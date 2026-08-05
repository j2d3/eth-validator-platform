const REPOSITORY = "j2d3/eth-validator-platform";
const RUN_URL_PREFIX = `https://github.com/${REPOSITORY}/actions/runs/`;

export const IMAGE_SECURITY_RUNS_API =
  `https://api.github.com/repos/${REPOSITORY}/actions/workflows/` +
  "image-security.yaml/runs?branch=main&per_page=1";

export const DEPENDABOT_PULLS_API =
  `https://api.github.com/repos/${REPOSITORY}/pulls?state=open&per_page=100`;

const FINDINGS_NAME =
  /^Image findings - (\d+)\/(\d+) exact subjects scanned - (\d+) coverage gaps - Critical (\d+) \((\d+) fix available\) - High (\d+) \((\d+) fix available\)$/;

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isIsoDate(value) {
  return typeof value === "string" && !Number.isNaN(Date.parse(value));
}

export function parseImageSecurityRun(value) {
  if (!isRecord(value) || !Array.isArray(value.workflow_runs)) {
    throw new Error("Invalid image-security workflow response");
  }

  const run = value.workflow_runs[0];
  if (!isRecord(run)) return null;
  if (
    typeof run.status !== "string" ||
    (run.conclusion !== null && typeof run.conclusion !== "string") ||
    typeof run.head_sha !== "string" ||
    !/^[0-9a-f]{40}$/.test(run.head_sha) ||
    run.head_branch !== "main" ||
    typeof run.html_url !== "string" ||
    !run.html_url.startsWith(RUN_URL_PREFIX) ||
    !isIsoDate(run.updated_at)
  ) {
    throw new Error("Invalid image-security workflow run");
  }

  return {
    status: run.status,
    conclusion: run.conclusion,
    sourceSha: run.head_sha,
    updatedAt: run.updated_at,
    htmlUrl: run.html_url,
  };
}

export function imageSecurityChecksApi(sourceSha) {
  if (typeof sourceSha !== "string" || !/^[0-9a-f]{40}$/.test(sourceSha)) {
    throw new Error("Invalid image-security source SHA");
  }
  return `https://api.github.com/repos/${REPOSITORY}/commits/${sourceSha}/check-runs?per_page=100`;
}

export function parseImageSecurityEvidence(value, run) {
  if (!isRecord(value) || !Array.isArray(value.check_runs)) {
    throw new Error("Invalid image-security check response");
  }
  if (!isRecord(run) || typeof run.sourceSha !== "string" || typeof run.htmlUrl !== "string") {
    throw new Error("Invalid image-security run binding");
  }

  const candidates = value.check_runs.filter(
    (check) =>
      isRecord(check) &&
      typeof check.name === "string" &&
      check.name.startsWith("Image findings - ") &&
      check.head_sha === run.sourceSha &&
      typeof check.html_url === "string" &&
      check.html_url.startsWith(`${run.htmlUrl}/job/`),
  );
  if (candidates.length === 0) return null;

  const check = candidates.sort((left, right) =>
    String(right.completed_at).localeCompare(String(left.completed_at)),
  )[0];
  const match = FINDINGS_NAME.exec(check.name);
  if (
    !match ||
    check.status !== "completed" ||
    check.conclusion !== "success" ||
    !isIsoDate(check.completed_at)
  ) {
    throw new Error("Invalid image-security finding check");
  }

  const [
    exactScanned,
    exactExpected,
    coverageGaps,
    criticalTotal,
    criticalAvailable,
    highTotal,
    highAvailable,
  ] = match.slice(1).map(Number);
  if (
    exactExpected < 1 ||
    exactScanned !== exactExpected ||
    coverageGaps < 0 ||
    criticalAvailable > criticalTotal ||
    highAvailable > highTotal
  ) {
    throw new Error("Invalid image-security finding counts");
  }

  return {
    images: exactExpected,
    exactSubjects: { scanned: exactScanned, expected: exactExpected },
    coverageGaps,
    critical: { total: criticalTotal, available: criticalAvailable },
    high: { total: highTotal, available: highAvailable },
    completedAt: check.completed_at,
    htmlUrl: check.html_url,
  };
}

export function countOpenDependabotPulls(value) {
  if (!Array.isArray(value)) {
    throw new Error("Invalid pull-request response");
  }

  return value.filter(
    (pull) =>
      isRecord(pull) &&
      pull.state === "open" &&
      isRecord(pull.user) &&
      pull.user.login === "dependabot[bot]",
  ).length;
}
