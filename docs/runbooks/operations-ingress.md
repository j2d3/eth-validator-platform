# Operations HTTPS ingress

## Resources

This ingress creates:

- one exact ACM certificate for `ops.g.j2d3.com`;
- one two-replica ingress-nginx Deployment on the always-on system nodes; and
- one internet-facing NLB with only a TLS listener on port 443.

The NLB has an hourly and capacity-unit charge for as long as its Kubernetes
Service exists. ACM public certificates have no separate certificate charge.
The reviewed Ingress objects expose only `/api/status` and `/grafana` on the
exact operations hostname.

## 1. Create and validate the certificate

Initialize the existing DNS root, then review a plan with no NLB hostname:

```bash
terraform -chdir=terraform/environments/dns init -backend-config=backend.hcl
terraform -chdir=terraform/environments/dns plan \
  -out=operations-certificate.tfplan
terraform -chdir=terraform/environments/dns apply operations-certificate.tfplan
```

Load the non-secret ARN without printing it and create the Flux input:

```bash
OPERATIONS_CERTIFICATE_ARN="$(
  terraform -chdir=terraform/environments/dns output -raw \
    operations_acm_certificate_arn
)"
kubectl -n flux-system create configmap aws-ingress-inputs \
  --from-literal=OPERATIONS_ACM_CERTIFICATE_ARN="$OPERATIONS_CERTIFICATE_ARN" \
  --dry-run=client -o yaml | kubectl apply -f -
kubectl -n flux-system annotate configmap aws-ingress-inputs \
  kustomize.toolkit.fluxcd.io/prune=disabled --overwrite
unset OPERATIONS_CERTIFICATE_ARN
```

The ARN is an identifier, not key material. The ConfigMap remains outside Git
because it includes the AWS account ID and region-specific resource identity.

## 2. Reconcile the ingress controller

After the reviewed desired state is on `main`:

```bash
flux reconcile source git flux-system -n flux-system
flux reconcile kustomization infrastructure-controllers \
  -n flux-system --with-source
kubectl -n ingress-nginx rollout status deployment/ingress-nginx-controller
kubectl -n ingress-nginx get service ingress-nginx-controller
```

Wait until the Service reports a load-balancer hostname. Confirm it has the AWS
shape `name.elb.region.amazonaws.com`; do not accept a URL, IP address, or
arbitrary hostname.

## 3. Publish Terraform-owned DNS

Capture the observed hostname and pass it to a new saved plan:

```bash
OPERATIONS_NLB_HOSTNAME="$(
  kubectl -n ingress-nginx get service ingress-nginx-controller \
    -o jsonpath='{.status.loadBalancer.ingress[0].hostname}'
)"
case "$OPERATIONS_NLB_HOSTNAME" in
  *.elb.*.amazonaws.com) ;;
  *) echo "unexpected load-balancer hostname" >&2; exit 1 ;;
esac
terraform -chdir=terraform/environments/dns plan \
  -var="operations_load_balancer_hostname=$OPERATIONS_NLB_HOSTNAME" \
  -out=operations-dns.tfplan
terraform -chdir=terraform/environments/dns apply operations-dns.tfplan
unset OPERATIONS_NLB_HOSTNAME
```

## 4. Verify the edge

```bash
dig +short ops.g.j2d3.com CNAME
curl --silent --show-error --head https://ops.g.j2d3.com/
```

Verify the two configured routes and confirm an unrelated path remains absent:

```bash
curl --fail --silent https://ops.g.j2d3.com/api/status | jq '.source'
curl --silent --show-error --head https://ops.g.j2d3.com/grafana/
test "$(curl --silent --output /dev/null --write-out '%{http_code}' \
  https://ops.g.j2d3.com/not-configured)" = "404"
```

Certificate failure, plaintext application traffic, a different SAN, an
authenticated-only response on `/grafana/` (see the public read surface below),
or a non-AWS DNS target is a failed qualification.

## Public read surface (Grafana)

The Grafana instance behind `/grafana/` intentionally accepts anonymous
requests in the `Viewer` role. This is a deliberate tradeoff for a public,
non-financial Ethereum testnet demo where no data under the cluster
Prometheus datasource is secret.

The exposure is broader than the pre-built dashboards. OSS Grafana's Viewer
role is not per-dashboard: a Viewer can issue arbitrary PromQL through
`/grafana/api/datasources/proxy/...` against every configured organization
datasource, so any unauthenticated caller can query the full cluster
Prometheus surface, not only the visible panels. Confirm the shape with:

```bash
curl --silent --show-error https://ops.g.j2d3.com/grafana/api/datasources | jq '.[].name'
```

Write actions remain gated. The admin login is still required for dashboard
edits, datasource changes, plugin installation, user administration, and any
other mutation. `security.cookie_secure`, `security.cookie_samesite: lax`,
`security.disable_gravatar`, and `auth.anonymous.hide_version` remain set to
constrain session and fingerprinting surface.

For a production deployment this configuration is not appropriate. Replace
anonymous access with an OIDC/SSO front and expose only a curated
query-scoped datasource (or embed pre-rendered snapshots) rather than the
raw cluster Prometheus.

## Pause or removal

Removing or suspending application Pods does not stop the NLB charge. To remove
the edge, first plan/apply the DNS root with a null NLB hostname, then remove
the ingress controller Service through reviewed Flux desired state. The exact
ACM certificate may remain validated at no separate certificate charge.
