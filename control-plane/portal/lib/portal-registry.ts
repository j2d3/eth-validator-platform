export type ResourceLink = {
  name: string;
  description: string;
  href: string;
};

export const repository = "https://github.com/j2d3/eth-validator-platform";
export const operationsOrigin = "https://ops.g.j2d3.com";
export const statusEndpoint = `${operationsOrigin}/api/status`;
export const grafanaBase = `${operationsOrigin}/grafana`;
export const signingDashboard =
  `${grafanaBase}/d/eth-eks-ephemery-sync/` +
  "ethereum-platform-eks-ephemery-sync-evidence?orgId=1";

export const grafanaDashboards: ResourceLink[] = [
  {
    name: "Kubernetes and node dashboards",
    description: "Browse the dashboards installed by kube-prometheus-stack",
    href: `${grafanaBase}/dashboards`,
  },
  {
    name: "Client-pair sync",
    description: "Execution and consensus sync metrics for active pairs",
    href: `${grafanaBase}/d/eth-eks-ephemery-sync/ethereum-platform-eks-ephemery-sync-evidence`,
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
    description: "Platform boundaries, lifecycle, and requirements",
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
    name: "EKS sync dashboard",
    description: "Prometheus rules and Grafana dashboard definition",
    href: `${repository}/blob/main/platform/apps/nodes/dev/sync-dashboard.yaml`,
  },
  {
    name: "Portal telemetry adapter",
    description: "Live status API response and qualification procedure",
    href: `${repository}/blob/main/docs/runbooks/portal-telemetry.md`,
  },
  {
    name: "Capacity operations",
    description: "Resume and pause zonal Spot capacity",
    href: `${repository}/blob/main/docs/runbooks/eks-capacity.md`,
  },
  {
    name: "Network policy check",
    description: "Recorded EKS allow-and-deny probe",
    href: `${repository}/blob/main/docs/evidence/2026-08-04-eks-network-policy.md`,
  },
];
