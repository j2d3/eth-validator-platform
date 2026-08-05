# Portal telemetry adapter

## Scope

`portal-status-api` reads a fixed set of Prometheus queries and returns a
public-safe JSON snapshot for the project portal. It does not accept PromQL,
Kubernetes object names, customer identifiers, validator identifiers, public
keys, or credentials from callers.

The adapter remains a ClusterIP service in `portal-system`. In EKS,
ingress-nginx exposes only `https://ops.g.j2d3.com/api/status`; the exact path
is rewritten to `/v1/status`. Grafana is served separately under
`https://ops.g.j2d3.com/grafana`. The current public testnet demo permits
anonymous Viewer access; writes still require the Grafana admin login. This
exposes the Prometheus query surface and is not the production access model.

The adapter uses the Prometheus Operator's headless `prometheus-operated`
Service so AWS VPC CNI NetworkPolicy evaluates the selected Prometheus Pod IP
instead of the Service ClusterIP.

## Response

`GET /v1/status` returns:

- observation time, cache age, and stale state;
- ready nodes, allocatable and used CPU and memory, Pod counts, and restarts;
- aggregate Ethereum Pod CPU, memory, restart, and persistent-volume usage;
- enabled validator count, signer target state, loaded-key count, permitted and
  prevented slashing checks, and unknown-key signing requests;
- aggregate firing-alert total plus critical/warning counts, excluding the
  always-firing Prometheus `Watchdog`; no alert labels or annotations cross the
  public API;
- client-pair target state, peers, sync progress, lag, CPU, and memory;
- the enabled-validator count for each active client pair; and
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

## HTTPS qualification

After the operations ingress and Terraform-owned DNS record are ready:

```bash
curl --fail --silent https://ops.g.j2d3.com/api/status | jq '.source'
curl --silent --show-error --head https://ops.g.j2d3.com/grafana/
```

The API must return the same public-safe schema observed through the private
port-forward. Grafana must serve the Viewer surface over HTTPS while rejecting
anonymous writes; the demo's accepted anonymous-read boundary is documented in
`docs/runbooks/operations-ingress.md`.
