const REPOSITORY = "j2d3/eth-validator-platform";
const RUN_URL_PREFIX = `https://github.com/${REPOSITORY}/actions/runs/`;

export const IMAGE_SECURITY_RUNS_API =
  `https://api.github.com/repos/${REPOSITORY}/actions/workflows/` +
  "image-security.yaml/runs?branch=main&per_page=1";

export const DEPENDABOT_PULLS_API =
  `https://api.github.com/repos/${REPOSITORY}/pulls?state=open&per_page=100`;

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
