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
anonymous Grafana dashboard response, or a non-AWS DNS target is a failed
qualification.

## Pause or removal

Removing or suspending application Pods does not stop the NLB charge. To remove
the edge, first plan/apply the DNS root with a null NLB hostname, then remove
the ingress controller Service through reviewed Flux desired state. The exact
ACM certificate may remain validated at no separate certificate charge.
