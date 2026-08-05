# Flux private-source authentication

This runbook verifies the source-authentication design in
[ADR 0002](../adrs/0002-private-flux-source-authentication.md). The checked-in
slice is non-mutating: no GitHub App, credential, AWS resource, workflow
authority, or Kubernetes object has been created by it.

## Current lab state

The repository is public, so anonymous HTTPS is available. It is not the live
configuration. The EKS source is the `flux-system` `GitRepository` inherited
from `clusters/local/flux-system/gotk-sync.yaml`, patched to the dev sync path,
and configured with:

- `provider: generic` (the default);
- `ssh://git@github.com/j2d3/eth-validator-platform`;
- `secretRef.name: flux-system`;
- a repository-scoped read-only SSH deploy key created by trusted-local
  bootstrap.

Confirm without reading the Secret value:

```bash
kubectl --kubeconfig .local/eks-kubeconfig \
  -n flux-system get gitrepository flux-system \
  -o jsonpath='{.spec.provider}{"\n"}{.spec.url}{"\n"}{.spec.secretRef.name}{"\n"}'
```

Expected provider is `generic` or empty (which defaults to `generic`), the URL
uses SSH, and the Secret reference is `flux-system`.

## Option A: direct private Git with a GitHub App

This is an implementation procedure for a future reviewed change, not an
instruction to mutate the current lab during documentation review.

### Required GitHub and Flux shape

Create a GitHub App installed only on the intended repository. Grant repository
contents read-only and metadata read; grant no Actions, administration,
deployment, secret, or workflow write permission.

The resulting Flux source uses HTTPS and `provider: github`:

```yaml
apiVersion: source.toolkit.fluxcd.io/v1
kind: GitRepository
metadata:
  name: flux-system
  namespace: flux-system
spec:
  provider: github
  url: https://github.com/j2d3/eth-validator-platform
  secretRef:
    name: flux-system-github-app
```

The Secret has this shape. Supply exactly one installation selector, not both:

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: flux-system-github-app
  namespace: flux-system
type: Opaque
stringData:
  githubAppID: "<app-id>"
  githubAppInstallationID: "<installation-id>"
  githubAppPrivateKey: |
    <private-key-pem>
```

`githubAppInstallationOwner` may replace `githubAppInstallationID`. GitHub
Enterprise Server may additionally require `githubAppBaseURL` and `ca.crt`.

### Bootstrap boundary

ESO cannot be the only source of this Secret: Flux installs ESO, while Flux
needs the App credential before its first private fetch. The trusted-local
operator retrieves the key from Secrets Manager into process memory and pipes
the generated Secret directly to the API. Do not put the PEM in argv, Git,
Terraform variables/state, a terminal transcript, or a temporary manifest.

A reviewed implementation can use this shape, with the private-key file on a
protected local filesystem and deleted after the API write:

```bash
flux create secret githubapp flux-system-github-app \
  --namespace=flux-system \
  --app-id="$GITHUB_APP_ID" \
  --app-installation-id="$GITHUB_APP_INSTALLATION_ID" \
  --app-private-key="/protected/path/github-app.pem" \
  --export | kubectl --kubeconfig .local/eks-kubeconfig apply -f -
```

Check field names only:

```bash
kubectl --kubeconfig .local/eks-kubeconfig -n flux-system \
  get secret flux-system-github-app -o json | jq -r '.data | keys[]'
```

### Verify App scope with the correct credentials

Ordinary `gh auth` user credentials are not sufficient for App-only endpoints.
Create an RS256 App JWT from the App ID and private key, then use that JWT to
list App installations and mint an installation token. Do not print either
token.

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $GITHUB_APP_JWT" \
  -H "Accept: application/vnd.github+json" \
  https://api.github.com/app/installations \
  | jq '[.[] | {id, repository_selection, permissions, account: .account.login}]'

INSTALLATION_TOKEN="$({
  curl --fail --silent --show-error -X POST \
    -H "Authorization: Bearer $GITHUB_APP_JWT" \
    -H "Accept: application/vnd.github+json" \
    "https://api.github.com/app/installations/$GITHUB_APP_INSTALLATION_ID/access_tokens"
} | jq -er .token)"

GH_TOKEN="$INSTALLATION_TOKEN" gh api /installation/repositories \
  --jq '{count: .total_count, repositories: [.repositories[].full_name]}'
unset INSTALLATION_TOKEN
```

Require `repository_selection: selected`, exactly the intended repository,
and contents read-only. The installation token expires after one hour; the App
private key does not expire.

### Rotation and revocation qualification

GitHub permits overlapping App keys. A future qualification must:

1. create a second App private key;
2. store it as a new Secrets Manager version;
3. update the cluster Secret and confirm the exact expected Git revision;
4. revoke the old App key;
5. confirm reconciliation again;
6. test uninstall/revocation while recording source and downstream conditions;
7. remove the cluster Secret during teardown.

Do not claim zero-downtime rotation or a particular downstream failure state
until that drill has been observed.

## Option B: signed OCI promotion to private ECR

This is the preferred production design. It is not implemented by this slice.

### Promotion boundary

One protected-main workflow receives `contents: read` and `id-token: write`.
GitHub OIDC may assume one role that can push only to the desired-state ECR
repository. It receives no EKS, RDS, Secrets Manager, or IAM mutation access.

The artifact must be packaged from the repository root:

```bash
flux push artifact \
  "oci://${ECR_REGISTRY}/eth-validator-platform/desired-state:main" \
  --path=. \
  --source="${GITHUB_SERVER_URL}/${GITHUB_REPOSITORY}" \
  --revision="main@sha1:${GITHUB_SHA}"
```

Apply explicit exclusions for `.git`, local state, and portal dependencies.
Do not change `--path` to `clusters/dev`: the dev Kustomizations reference
`platform/`, and node HelmReleases reference `charts/ethereum-node` relative to
the source artifact root.

After publishing, resolve and sign the immutable digest with cosign keyless
signing. The workflow identity policy must match the exact repository, workflow
file, and `refs/heads/main`, not a broad repository prefix. Flux watches the
`main` promotion tag, records the resolved digest and revision in source status,
and verifies the digest-bound signature. Do not configure a literal
`spec.ref.digest` unless a separate reviewed writer exists to advance it; a
fixed digest does not discover the next promotion.

### Cluster retrieval and verification

The EKS Pod Identity association grants `flux-system/source-controller` only
the ECR read actions required for the one repository. The `OCIRepository` uses
`provider: aws`, a digest reference, and cosign verification with anchored
issuer and subject expressions.

Source-controller needs network access to ECR and to the hosted Rekor endpoint
for the default keyless verification path. Fulcio is contacted by CI to issue
the signing certificate; source-controller verifies its chain from trusted
root material and does not request a certificate from Fulcio.

### Failure qualification

Test at least an absent digest, invalid signature, wrong workflow identity,
ECR authorization denial, and Rekor unavailability. The accepted assertion is
that a failed or unverified new revision is not published as a new source
artifact.

Also record whether the last successful artifact remains available, the exact
conditions on every dependent Kustomization, whether drift is still corrected
from that artifact, and whether existing workloads continue. Until observed,
the contract leaves those behaviors explicitly unqualified.

## Static verification

The repository checks the design and reference closure offline:

```bash
python3 -m unittest tests.test_flux_source_authentication_contracts -v
```

This proves document consistency, not runtime authentication, rotation,
revocation, or failure behavior.
