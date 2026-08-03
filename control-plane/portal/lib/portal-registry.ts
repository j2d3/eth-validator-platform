export type SurfaceAccess = "public" | "operator" | "local";
export type SurfaceState = "available" | "connect" | "planned";

export type PortalSurface = {
  name: string;
  category: "Observe" | "Operate" | "Trace" | "Learn";
  description: string;
  access: SurfaceAccess;
  state: SurfaceState;
  href?: string;
  detail: string;
};

export type SecuritySignal = {
  name: string;
  state: "enabled" | "planned";
  description: string;
  evidence: string;
  href?: string;
  linkLabel?: string;
};

const repository = "https://github.com/j2d3/eth-validator-platform";

export const securitySignals: SecuritySignal[] = [
  {
    name: "Vulnerability alerts",
    state: "enabled",
    description:
      "GitHub evaluates the dependency graph against its advisory database. Live findings remain inside the authenticated repository security view.",
    evidence: "GitHub admin API · verified 2026-08-02",
    href: `${repository}/security/dependabot`,
    linkLabel: "live findings ↗",
  },
  {
    name: "Security update PRs",
    state: "enabled",
    description:
      "Dependabot may propose a targeted remediation when a vulnerable dependency has a compatible patched release.",
    evidence: "GitHub admin API · verified 2026-08-02",
    href: `${repository}/security/dependabot`,
    linkLabel: "live findings ↗",
  },
  {
    name: "Version-update policy",
    state: "enabled",
    description:
      "A reviewed weekly schedule covers repository Actions, Terraform roots, Python validation, and this portal's npm lockfile with bounded update queues.",
    evidence: ".github/dependabot.yml · reviewed policy on main",
    href: `${repository}/blob/main/.github/dependabot.yml`,
    linkLabel: "source policy ↗",
  },
  {
    name: "Container image scanning",
    state: "planned",
    description:
      "The next supply-chain slice combines free ECR basic scan-on-push for owned images with CI scanning of every third-party image digest.",
    evidence: "PRD requirement · implementation queued",
    href: `${repository}/issues/43`,
    linkLabel: "delivery issue ↗",
  },
];

export const surfaces: PortalSurface[] = [
  {
    name: "Fleet overview",
    category: "Observe",
    description:
      "Fleet posture, client diversity, target health, and assignment drill-down.",
    access: "operator",
    state: "connect",
    detail: "Grafana · eth-fleet-overview",
  },
  {
    name: "Validator detail",
    category: "Observe",
    description:
      "One identity across pair health, resources, volumes, duties, and logs.",
    access: "operator",
    state: "connect",
    detail: "Grafana · eth-validator-detail",
  },
  {
    name: "Geth + Lighthouse",
    category: "Observe",
    description:
      "Execution and consensus health for the first qualified client adapter.",
    access: "operator",
    state: "connect",
    detail: "Grafana · eth-validator-geth-lighthouse",
  },
  {
    name: "Signer & slashing",
    category: "Observe",
    description:
      "Web3Signer readiness, signing outcomes, JVM health, and PostgreSQL signals.",
    access: "operator",
    state: "connect",
    detail: "Grafana · eth-signer-slashing",
  },
  {
    name: "Correlated logs",
    category: "Trace",
    description:
      "Loki streams for platform and pair components with shared identity context.",
    access: "operator",
    state: "connect",
    detail: "Grafana · eth-platform-logs",
  },
  {
    name: "Platform smoke",
    category: "Observe",
    description:
      "The local platform-service baseline: signer, database, secrets, and telemetry.",
    access: "local",
    state: "connect",
    detail: "Grafana · eth-platform-local-smoke",
  },
  {
    name: "Desired state",
    category: "Operate",
    description:
      "Catalog, platform manifests, infrastructure definitions, and review history.",
    access: "operator",
    state: "available",
    href: repository,
    detail: "GitHub · private repository",
  },
  {
    name: "Lifecycle requests",
    category: "Operate",
    description:
      "Open reviewed pull requests for non-signing pair activation and stop requests.",
    access: "operator",
    state: "available",
    href: `${repository}/actions/workflows/node-pair-lifecycle.yaml`,
    detail: "GitHub Actions · PR author only",
  },
  {
    name: "Reconciliation",
    category: "Operate",
    description:
      "Flux source, Kustomization, and HelmRelease health with revision evidence.",
    access: "operator",
    state: "planned",
    detail: "Flux · read model not connected",
  },
  {
    name: "EKS capacity",
    category: "Operate",
    description:
      "System tier, zonal Spot groups, EBS placement, and warm-pause posture.",
    access: "operator",
    state: "planned",
    detail: "AWS · environment link not configured",
  },
  {
    name: "Product contract",
    category: "Learn",
    description:
      "The architecture, safety invariants, lifecycle, and production-evolution plan.",
    access: "operator",
    state: "available",
    href: `${repository}/blob/main/docs/prd/001-dynamic-validator-platform.md`,
    detail: "PRD · canonical specification",
  },
  {
    name: "Operating runbooks",
    category: "Learn",
    description:
      "Local development, AWS capacity, storage, signing, and recovery procedures.",
    access: "operator",
    state: "available",
    href: `${repository}/tree/main/docs/runbooks`,
    detail: "GitHub · reviewed procedures",
  },
];

export const architectureSteps = [
  { name: "Git", detail: "reviewed intent", state: "ready" },
  { name: "Flux", detail: "reconciliation", state: "ready" },
  { name: "EKS", detail: "warm-paused", state: "ready" },
  { name: "EL + CL", detail: "stopped", state: "off" },
  { name: "Signer", detail: "0 keys", state: "guard" },
  { name: "Postgres", detail: "slashing history", state: "ready" },
] as const;

export const clientMatrix = [
  { execution: "Geth", consensus: "Lighthouse", state: "implemented" },
  { execution: "Geth", consensus: "Prysm", state: "planned" },
  { execution: "Geth", consensus: "Teku", state: "planned" },
  { execution: "Geth", consensus: "Nimbus", state: "planned" },
  { execution: "Nethermind", consensus: "Lighthouse", state: "planned" },
  { execution: "Besu", consensus: "Lighthouse", state: "planned" },
  { execution: "Reth", consensus: "Lighthouse", state: "planned" },
] as const;
