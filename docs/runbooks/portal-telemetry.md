# Portal telemetry adapter

## Scope

`portal-status-api` reads a fixed set of Prometheus queries and returns a
public-safe JSON snapshot for the project portal. It does not accept PromQL,
Kubernetes object names, customer identifiers, validator identifiers, public
keys, or credentials from callers.

The first deployment is a ClusterIP service in `portal-system`. There is no
public ingress in this slice. A Grafana URL is `null` until a tested HTTPS
Grafana endpoint is configured.

The adapter uses the Prometheus Operator's headless `prometheus-operated`
Service so AWS VPC CNI NetworkPolicy evaluates the selected Prometheus Pod IP
instead of the Service ClusterIP.

## Response

`GET /v1/status` returns:

- observation time, cache age, and stale state;
- ready nodes, allocatable and used CPU and memory, Pod counts, and restarts;
- aggregate Ethereum Pod CPU, memory, restart, and persistent-volume usage;
- client-pair target state, peers, sync progress, lag, CPU, and memory; and
- a per-pair Grafana URL only when the configured Grafana base URL passes the
  HTTPS and path checks.

Missing Prometheus series are represented as `null` or an empty pair list.
They are not converted into healthy values. The one exception is an observed
zero for workload totals where the PromQL expression explicitly uses
`or vector(0)`.

## EKS qualification

Use the repository kubeconfig and wait for Flux:

```bash
export KUBECONFIG="$PWD/.local/eks-kubeconfig"
flux reconcile source git flux-system -n flux-system
flux reconcile kustomization infrastructure-controllers -n flux-system --with-source
flux reconcile kustomization portal-observability -n flux-system --with-source
kubectl -n portal-system rollout status deployment/portal-status-api
```

Keep the service cluster-private while qualifying it:

```bash
kubectl -n portal-system port-forward service/portal-status-api 18080:8080
```

In another shell:

```bash
curl --fail --silent http://127.0.0.1:18080/healthz | jq
curl --fail --silent http://127.0.0.1:18080/readyz | jq
curl --fail --silent http://127.0.0.1:18080/v1/status | jq
```

Confirm the response contains no customer ID, validator ID, network identity,
public key, Secret value, Pod IP, node IP, or AWS account ID before exposing
the API beyond the cluster.

## HTTPS and Grafana follow-up

The exposure change is separate from this deployment. It will use one exact
HTTPS operations hostname and route the curated API and Grafana under distinct
paths. The portal must hide both links until DNS, certificate validation,
authentication, and direct HTTPS probes succeed.
