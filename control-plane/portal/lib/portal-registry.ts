export type StatusTone = "ready" | "paused" | "off";

export type SummaryItem = {
  label: string;
  value: string;
  detail: string;
  tone: StatusTone;
};

export type ComponentStatus = {
  component: string;
  status: string;
  detail: string;
  tone: StatusTone;
  href: string;
  linkLabel: string;
};

export type ResourceLink = {
  name: string;
  description: string;
  href: string;
};

export const repository = "https://github.com/j2d3/eth-validator-platform";
export const observedRevision =
  "86d561f38f1f174cb86e759e62903e51f174ed7d";
export const observedAt = "2026-08-04 02:57 UTC";

export const summary: SummaryItem[] = [
  {
    label: "System nodes",
    value: "2",
    detail: "Ready · on-demand",
    tone: "ready",
  },
  {
    label: "Ethereum nodes",
    value: "0",
    detail: "Spot capacity at desired 0",
    tone: "paused",
  },
  {
    label: "Running node pairs",
    value: "0",
    detail: "Node applications suspended",
    tone: "paused",
  },
  {
    label: "Signing",
    value: "Disabled",
    detail: "Signer applications suspended",
    tone: "off",
  },
];

export const componentStatuses: ComponentStatus[] = [
  {
    component: "EKS",
    status: "Running",
    detail: "Development cluster · us-west-2",
    tone: "ready",
    href: `${repository}/tree/main/terraform/environments/dev`,
    linkLabel: "Terraform",
  },
  {
    component: "Flux",
    status: "Ready",
    detail: "Controllers and infrastructure configuration reconciled",
    tone: "ready",
    href: `${repository}/tree/main/clusters/dev`,
    linkLabel: "Cluster state",
  },
  {
    component: "Monitoring",
    status: "Running",
    detail: "Prometheus, Grafana, Alertmanager, and node exporters",
    tone: "ready",
    href: `${repository}/blob/main/platform/infrastructure/controllers/monitoring.yaml`,
    linkLabel: "Manifests",
  },
  {
    component: "Ethereum capacity",
    status: "Paused",
    detail: "Three zonal Spot groups · min 0 · desired 0 · max 1",
    tone: "paused",
    href: `${repository}/blob/main/docs/runbooks/eks-capacity.md`,
    linkLabel: "Runbook",
  },
  {
    component: "Geth + Lighthouse",
    status: "Suspended",
    detail: "Ephemery profile declared · no pods running",
    tone: "paused",
    href: `${repository}/blob/main/docs/runbooks/eks-ephemery-sync.md`,
    linkLabel: "Runbook",
  },
  {
    component: "Web3Signer",
    status: "Not deployed",
    detail: "Signer prerequisites and applications suspended",
    tone: "off",
    href: `${repository}/blob/main/clusters/dev/signer-prerequisites.yaml`,
    linkLabel: "Manifest",
  },
];

export const resources: ResourceLink[] = [
  {
    name: "Source repository",
    description: "Terraform, Flux resources, Helm chart, tests, and history",
    href: repository,
  },
  {
    name: "Architecture specification",
    description: "Platform boundaries, lifecycle, and safety requirements",
    href: `${repository}/blob/main/docs/prd/001-dynamic-validator-platform.md`,
  },
  {
    name: "EKS bootstrap",
    description: "Terraform apply and Flux bootstrap procedure",
    href: `${repository}/blob/main/docs/runbooks/eks-flux-bootstrap.md`,
  },
  {
    name: "Node-pair lifecycle",
    description: "Start, inspect, and stop the Ephemery sync pair",
    href: `${repository}/blob/main/docs/runbooks/eks-ephemery-sync.md`,
  },
  {
    name: "Dashboard definitions",
    description: "Prometheus and Grafana resources stored in Git",
    href: `${repository}/tree/main/platform/apps/local`,
  },
  {
    name: "Network policy check",
    description: "Recorded EKS allow-and-deny probe",
    href: `${repository}/blob/main/docs/evidence/2026-08-04-eks-network-policy.md`,
  },
];
