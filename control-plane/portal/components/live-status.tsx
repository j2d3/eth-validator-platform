"use client";

import { useEffect, useState } from "react";
import { signingDashboard, statusEndpoint } from "../lib/portal-registry";

type NullableNumber = number | null;

type PairSnapshot = {
  assignmentId: string;
  network: string | null;
  networkGeneration: string | null;
  executionClient: string | null;
  consensusClient: string | null;
  lifecycleState: string | null;
  targets: Record<string, NullableNumber | undefined>;
  signing?: {
    validatorsEnabled: NullableNumber;
  };
  sync: Record<string, NullableNumber | undefined>;
  resources: {
    cpuCores: Record<string, NullableNumber | undefined>;
    memoryBytes: Record<string, NullableNumber | undefined>;
  };
  grafanaUrl: string | null;
};

type SigningSnapshot = {
  validatorsEnabled: NullableNumber;
  signerUp: NullableNumber;
  keysLoaded: NullableNumber;
  slashingPermittedTotal: NullableNumber;
  slashingPreventedTotal: NullableNumber;
  missingIdentifierTotal: NullableNumber;
};

type StatusSnapshot = {
  schemaVersion: 1;
  observedAt: string;
  source: {
    prometheusReady: boolean;
    stale: boolean;
    cacheAgeSeconds: number;
  };
  cluster: {
    name: string;
    environment: string;
    nodes: {
      ready: NullableNumber;
      systemReady: NullableNumber;
      ethereumReady: NullableNumber;
    };
    capacity: {
      cpuCores: NullableNumber;
      memoryBytes: NullableNumber;
    };
    usage: {
      cpuCores: NullableNumber;
      memoryBytes: NullableNumber;
    };
    pods: {
      total: NullableNumber;
      running: NullableNumber;
      pending: NullableNumber;
    };
    containerRestarts: NullableNumber;
    ethereumWorkloads: {
      pods: NullableNumber;
      podsRunning: NullableNumber;
      cpuCores: NullableNumber;
      memoryBytes: NullableNumber;
      containerRestarts: NullableNumber;
      persistentVolumeBytes: {
        used: NullableNumber;
        capacity: NullableNumber;
      };
    };
  };
  signing?: SigningSnapshot;
  pairs: PairSnapshot[];
};

const POLL_INTERVAL_MS = 15_000;
const GRAFANA_ORIGIN = "https://ops.g.j2d3.com";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isNullableNumber(value: unknown): value is NullableNumber {
  return value === null || (typeof value === "number" && Number.isFinite(value));
}

function hasNullableNumbers(
  value: unknown,
  names: readonly string[],
): value is Record<string, NullableNumber> {
  return (
    isRecord(value) && names.every((name) => isNullableNumber(value[name]))
  );
}

function isPair(value: unknown): value is PairSnapshot {
  if (!isRecord(value) || typeof value.assignmentId !== "string") return false;
  if (!isRecord(value.targets) || !isRecord(value.sync)) return false;
  if (!isRecord(value.resources)) return false;
  if (!isRecord(value.resources.cpuCores) || !isRecord(value.resources.memoryBytes)) {
    return false;
  }
  if (
    value.signing !== undefined &&
    !hasNullableNumbers(value.signing, ["validatorsEnabled"])
  ) {
    return false;
  }
  for (const name of [
    "network",
    "networkGeneration",
    "executionClient",
    "consensusClient",
    "lifecycleState",
    "grafanaUrl",
  ]) {
    if (value[name] !== null && typeof value[name] !== "string") return false;
  }
  return true;
}

function isSnapshot(value: unknown): value is StatusSnapshot {
  if (!isRecord(value) || value.schemaVersion !== 1) return false;
  if (typeof value.observedAt !== "string" || !Array.isArray(value.pairs)) {
    return false;
  }
  if (!isRecord(value.source)) return false;
  if (
    typeof value.source.prometheusReady !== "boolean" ||
    typeof value.source.stale !== "boolean" ||
    typeof value.source.cacheAgeSeconds !== "number"
  ) {
    return false;
  }
  if (!isRecord(value.cluster)) return false;
  if (
    typeof value.cluster.name !== "string" ||
    typeof value.cluster.environment !== "string"
  ) {
    return false;
  }
  if (!hasNullableNumbers(value.cluster.nodes, ["ready", "systemReady", "ethereumReady"])) {
    return false;
  }
  if (!hasNullableNumbers(value.cluster.capacity, ["cpuCores", "memoryBytes"])) {
    return false;
  }
  if (!hasNullableNumbers(value.cluster.usage, ["cpuCores", "memoryBytes"])) {
    return false;
  }
  if (!hasNullableNumbers(value.cluster.pods, ["total", "running", "pending"])) {
    return false;
  }
  if (!isNullableNumber(value.cluster.containerRestarts)) return false;
  if (!hasNullableNumbers(value.cluster.ethereumWorkloads, [
    "pods",
    "podsRunning",
    "cpuCores",
    "memoryBytes",
    "containerRestarts",
  ])) {
    return false;
  }
  if (
    !hasNullableNumbers(value.cluster.ethereumWorkloads.persistentVolumeBytes, [
      "used",
      "capacity",
    ])
  ) {
    return false;
  }
  if (
    value.signing !== undefined &&
    !hasNullableNumbers(value.signing, [
      "validatorsEnabled",
      "signerUp",
      "keysLoaded",
      "slashingPermittedTotal",
      "slashingPreventedTotal",
      "missingIdentifierTotal",
    ])
  ) {
    return false;
  }
  return value.pairs.every(isPair);
}

function formatNumber(value: NullableNumber | undefined, digits = 2): string {
  if (value === null || value === undefined) return "Unavailable";
  return new Intl.NumberFormat("en-US", {
    maximumFractionDigits: digits,
  }).format(value);
}

function formatBytes(value: NullableNumber | undefined): string {
  if (value === null || value === undefined) return "Unavailable";
  if (value === 0) return "0 B";
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  const unit = Math.min(Math.floor(Math.log(value) / Math.log(1024)), units.length - 1);
  return `${formatNumber(value / 1024 ** unit, 1)} ${units[unit]}`;
}

function formatObservedAt(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("en-US", {
    dateStyle: "medium",
    timeStyle: "medium",
    timeZone: "UTC",
  });
}

function validGrafanaUrl(value: string | null): string | null {
  if (!value) return null;
  try {
    const url = new URL(value);
    if (
      url.origin !== GRAFANA_ORIGIN ||
      !url.pathname.startsWith("/grafana/d/") ||
      url.username ||
      url.password
    ) {
      return null;
    }
    return url.toString();
  } catch {
    return null;
  }
}

function pairTarget(value: NullableNumber | undefined): string {
  if (value === 1) return "Up";
  if (value === 0) return "Down";
  return "Unavailable";
}

function validatorsEnabled(value: NullableNumber | undefined): string {
  if (value === null || value === undefined) return "Unavailable";
  return `${formatNumber(value, 0)} enabled`;
}

function EnvironmentHeading({ signing }: { signing?: SigningSnapshot }) {
  const validators = signing?.validatorsEnabled;
  const signerUp = signing?.signerUp;
  const available = validators !== null && validators !== undefined;
  const healthy = available && validators > 0 && signerUp === 1;
  const signerLabel =
    signerUp === 1 ? "Signer up" : signerUp === 0 ? "Signer down" : "Signer unavailable";

  return (
    <section className="environment-heading" aria-labelledby="page-title">
      <div>
        <p className="context">Development · EKS · AWS us-west-2</p>
        <h1 id="page-title">Environment status</h1>
      </div>
      <a
        className={`signing-state ${healthy ? "signing-state--ready" : "signing-state--off"}`}
        href={signingDashboard}
        aria-label={`Open signing metrics in Grafana: ${
          validatorsEnabled(validators).toLowerCase()
        }, ${signerLabel.toLowerCase()}`}
      >
        <span>Signing validators</span>
        <strong>{validatorsEnabled(validators)}</strong>
        <small>{signerLabel} · Grafana ↗</small>
      </a>
    </section>
  );
}

export default function LiveStatus() {
  const [snapshot, setSnapshot] = useState<StatusSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;

    async function load() {
      try {
        const response = await fetch(statusEndpoint, {
          cache: "no-store",
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body: unknown = await response.json();
        if (!isSnapshot(body)) throw new Error("Invalid status response");
        if (active) {
          setSnapshot(body);
          setError(null);
        }
      } catch {
        if (active) setError("Live status unavailable");
      }
    }

    void load();
    const poller = window.setInterval(load, POLL_INTERVAL_MS);
    return () => {
      active = false;
      window.clearInterval(poller);
    };
  }, []);

  if (!snapshot) {
    return (
      <>
        <EnvironmentHeading />
        <section className="panel telemetry-message" aria-live="polite">
          {error ?? "Loading live status…"}
        </section>
      </>
    );
  }

  const cluster = snapshot.cluster;
  const workload = cluster.ethereumWorkloads;
  const freshness = snapshot.source.stale ? "Stale" : "Current";

  return (
    <>
      <EnvironmentHeading signing={snapshot.signing} />

      <section className="summary-grid" aria-label="Live cluster summary">
        <article className="summary-item">
          <span className="summary-item__label">Ready nodes</span>
          <strong>{formatNumber(cluster.nodes.ready, 0)}</strong>
          <span className="detail detail--ready">
            System {formatNumber(cluster.nodes.systemReady, 0)} · Ethereum{" "}
            {formatNumber(cluster.nodes.ethereumReady, 0)}
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Cluster CPU</span>
          <strong>{formatNumber(cluster.usage.cpuCores)} cores</strong>
          <span className="detail">
            {formatNumber(cluster.capacity.cpuCores)} cores allocatable
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Cluster memory</span>
          <strong>{formatBytes(cluster.usage.memoryBytes)}</strong>
          <span className="detail">
            {formatBytes(cluster.capacity.memoryBytes)} allocatable
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Running Pods</span>
          <strong>{formatNumber(cluster.pods.running, 0)}</strong>
          <span className="detail">
            {formatNumber(cluster.pods.pending, 0)} pending ·{" "}
            {formatNumber(cluster.containerRestarts, 0)} restarts
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Client pairs</span>
          <strong>{snapshot.pairs.length}</strong>
          <span className="detail">
            {formatNumber(workload.podsRunning, 0)} /{" "}
            {formatNumber(workload.pods, 0)} Ethereum Pods running
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Ethereum CPU</span>
          <strong>{formatNumber(workload.cpuCores)} cores</strong>
          <span className="detail">{formatBytes(workload.memoryBytes)} memory</span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Ethereum storage</span>
          <strong>{formatBytes(workload.persistentVolumeBytes.used)}</strong>
          <span className="detail">
            {formatBytes(workload.persistentVolumeBytes.capacity)} capacity
          </span>
        </article>
        <article className="summary-item">
          <span className="summary-item__label">Telemetry</span>
          <strong>{freshness}</strong>
          <span className="detail">
            {formatNumber(snapshot.source.cacheAgeSeconds, 1)}s cache age
          </span>
        </article>
      </section>

      <section className="panel" aria-labelledby="signing-title">
        <div className="panel-heading">
          <h2 id="signing-title">Signing</h2>
          <a href={signingDashboard}>Open in Grafana</a>
        </div>
        <div className="summary-grid signing-summary-grid">
          <article className="summary-item">
            <span className="summary-item__label">Validators enabled</span>
            <strong>{formatNumber(snapshot.signing?.validatorsEnabled, 0)}</strong>
          </article>
          <article className="summary-item">
            <span className="summary-item__label">Signer target</span>
            <strong>{pairTarget(snapshot.signing?.signerUp)}</strong>
          </article>
          <article className="summary-item">
            <span className="summary-item__label">Keys loaded</span>
            <strong>{formatNumber(snapshot.signing?.keysLoaded, 0)}</strong>
          </article>
          <article className="summary-item">
            <span className="summary-item__label">Permitted checks</span>
            <strong>{formatNumber(snapshot.signing?.slashingPermittedTotal, 0)}</strong>
          </article>
          <article className="summary-item">
            <span className="summary-item__label">Prevented checks</span>
            <strong>{formatNumber(snapshot.signing?.slashingPreventedTotal, 0)}</strong>
          </article>
          <article className="summary-item">
            <span className="summary-item__label">Unknown-key requests</span>
            <strong>{formatNumber(snapshot.signing?.missingIdentifierTotal, 0)}</strong>
          </article>
        </div>
      </section>

      <section className="panel" aria-labelledby="pairs-title">
        <div className="panel-heading">
          <h2 id="pairs-title">Client pairs</h2>
          <span>
            Observed {formatObservedAt(snapshot.observedAt)} UTC
            {error ? " · Update failed" : ""}
          </span>
        </div>
        <div className="status-table-wrap">
          <table className="status-table pair-table">
            <thead>
              <tr>
                <th scope="col">Pair</th>
                <th scope="col">Clients</th>
                <th scope="col">Network</th>
                <th scope="col">Targets</th>
                <th scope="col">Signing</th>
                <th scope="col">Peers</th>
                <th scope="col">Sync</th>
                <th scope="col">Resources</th>
                <th scope="col">Dashboard</th>
              </tr>
            </thead>
            <tbody>
              {snapshot.pairs.length === 0 ? (
                <tr>
                  <td colSpan={9}>No active client pairs observed.</td>
                </tr>
              ) : (
                snapshot.pairs.map((pair) => {
                  const dashboard = validGrafanaUrl(pair.grafanaUrl);
                  const totalCpu =
                    (pair.resources.cpuCores.execution ?? 0) +
                    (pair.resources.cpuCores.consensus ?? 0);
                  const totalMemory =
                    (pair.resources.memoryBytes.execution ?? 0) +
                    (pair.resources.memoryBytes.consensus ?? 0);
                  return (
                    <tr key={pair.assignmentId}>
                      <th scope="row">
                        {pair.assignmentId}
                        <small>{pair.lifecycleState ?? "Unavailable"}</small>
                      </th>
                      <td>
                        {pair.executionClient ?? "Unavailable"} +{" "}
                        {pair.consensusClient ?? "Unavailable"}
                      </td>
                      <td>
                        {pair.network ?? "Unavailable"}
                        <small>{pair.networkGeneration ?? ""}</small>
                      </td>
                      <td>
                        EL {pairTarget(pair.targets.execution)} · CL{" "}
                        {pairTarget(pair.targets.consensus)}
                      </td>
                      <td>
                        {validatorsEnabled(pair.signing?.validatorsEnabled)}
                      </td>
                      <td>
                        EL {formatNumber(pair.sync.executionPeers, 0)} · CL{" "}
                        {formatNumber(pair.sync.consensusPeers, 0)}
                      </td>
                      <td>
                        EL distance {formatNumber(pair.sync.executionSyncDistance, 0)}
                        <small>
                          CL slot lag {formatNumber(pair.sync.consensusSlotLag, 0)} ·
                          finality lag{" "}
                          {formatNumber(pair.sync.consensusFinalityLagEpochs, 0)} epochs
                        </small>
                      </td>
                      <td>
                        {formatNumber(totalCpu)} cores
                        <small>{formatBytes(totalMemory)}</small>
                      </td>
                      <td>{dashboard ? <a href={dashboard}>Open</a> : "Unavailable"}</td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
