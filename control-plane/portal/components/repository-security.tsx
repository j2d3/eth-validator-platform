"use client";

import { useEffect, useState } from "react";
import {
  countOpenDependabotPulls,
  DEPENDABOT_PULLS_API,
  IMAGE_SECURITY_RUNS_API,
  imageSecurityChecksApi,
  parseImageSecurityEvidence,
  parseImageSecurityRun,
} from "../lib/github-security-status.mjs";
import {
  dependabotPullRequests,
  imageSecurityRunbook,
  imageSecurityWorkflowRuns,
} from "../lib/portal-registry";

type ImageSecurityRun = {
  status: string;
  conclusion: string | null;
  sourceSha: string;
  updatedAt: string;
  htmlUrl: string;
};

type SecurityStatus = {
  imageRun: ImageSecurityRun | null;
  imageEvidence: ImageSecurityEvidence | null;
  dependabotPulls: number | null;
};

type ImageSecurityEvidence = {
  images: number;
  critical: { total: number; available: number };
  high: { total: number; available: number };
  completedAt: string;
  htmlUrl: string;
};

function scanLabel(run: ImageSecurityRun | null): string {
  if (!run) return "Unavailable";
  if (run.status !== "completed") return "Running";
  if (run.conclusion === "success") return "Success";
  return run.conclusion ? run.conclusion.replaceAll("_", " ") : "Unavailable";
}

function formatTimestamp(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "Unavailable";
  return date.toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "short",
    timeZone: "UTC",
  });
}

async function fetchJson(url: string): Promise<unknown> {
  const response = await fetch(url, {
    cache: "no-store",
    headers: {
      Accept: "application/vnd.github+json",
    },
  });
  if (!response.ok) throw new Error(`GitHub HTTP ${response.status}`);
  return response.json();
}

export default function RepositorySecurity() {
  const [status, setStatus] = useState<SecurityStatus>({
    imageRun: null,
    imageEvidence: null,
    dependabotPulls: null,
  });

  useEffect(() => {
    let active = true;

    async function load() {
      const [imageResult, pullResult] = await Promise.allSettled([
        fetchJson(IMAGE_SECURITY_RUNS_API),
        fetchJson(DEPENDABOT_PULLS_API),
      ]);
      if (!active) return;

      let imageRun: ImageSecurityRun | null = null;
      let imageEvidence: ImageSecurityEvidence | null = null;
      let dependabotPulls: number | null = null;
      try {
        if (imageResult.status === "fulfilled") {
          imageRun = parseImageSecurityRun(imageResult.value);
        }
      } catch {
        imageRun = null;
      }
      try {
        if (pullResult.status === "fulfilled") {
          dependabotPulls = countOpenDependabotPulls(pullResult.value);
        }
      } catch {
        dependabotPulls = null;
      }
      try {
        if (imageRun) {
          const checkResponse = await fetchJson(
            imageSecurityChecksApi(imageRun.sourceSha),
          );
          imageEvidence = parseImageSecurityEvidence(checkResponse, imageRun);
        }
      } catch {
        imageEvidence = null;
      }
      if (!active) return;
      setStatus({ imageRun, imageEvidence, dependabotPulls });
    }

    void load();
    return () => {
      active = false;
    };
  }, []);

  const imageRun = status.imageRun;

  return (
    <section className="panel" aria-labelledby="repository-security-title">
      <div className="panel-heading">
        <h2 id="repository-security-title">Repository security</h2>
        <span>GitHub</span>
      </div>
      <div className="summary-grid security-summary-grid">
        <article className="summary-item">
          <span className="summary-item__label">Container image scan</span>
          <strong>{scanLabel(imageRun)}</strong>
          <span className="detail">
            {imageRun
              ? `Main · ${imageRun.sourceSha.slice(0, 8)} · ${formatTimestamp(
                  imageRun.updatedAt,
                )} UTC`
              : "Latest main run unavailable"}
          </span>
          <a href={imageRun?.htmlUrl ?? imageSecurityWorkflowRuns}>Workflow runs</a>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Image finding occurrences</span>
          <strong>
            {status.imageEvidence
              ? `${status.imageEvidence.critical.total} Critical · ${status.imageEvidence.high.total} High`
              : "Unavailable"}
          </strong>
          <span className="detail">
            {status.imageEvidence
              ? `${status.imageEvidence.images} images · ${status.imageEvidence.critical.available} Critical and ${status.imageEvidence.high.available} High with fixes available`
              : "Latest aggregate unavailable"}
          </span>
          <a href={status.imageEvidence?.htmlUrl ?? imageSecurityWorkflowRuns}>
            Evidence check
          </a>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Dependency updates</span>
          <strong>
            {status.dependabotPulls === null
              ? "Unavailable"
              : `${status.dependabotPulls} open`}
          </strong>
          <span className="detail">Dependabot pull requests</span>
          <a href={dependabotPullRequests}>Open pull requests</a>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Image enforcement</span>
          <strong>Evidence only</strong>
          <span className="detail">Findings do not block promotion yet</span>
          <a href={imageSecurityRunbook}>Runbook</a>
        </article>
      </div>
    </section>
  );
}
