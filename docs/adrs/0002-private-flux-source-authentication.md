# ADR 0002: Prefer signed OCI promotion for a private Flux source

- Status: Accepted design; not implemented
- Date: 2026-08-05
- Decision owners: Platform engineering
- Related issue: [#71](https://github.com/j2d3/eth-validator-platform/issues/71)
- Machine-readable contract: [`flux-source-authentication.yaml`](../../hack/qualification/flux-source-authentication.yaml)

## Context

The public repository is a disclosure choice for this lab. The running EKS
cluster does not fetch it anonymously: its `GitRepository` uses SSH with a
repository-scoped read-only deploy key. Anonymous HTTPS is possible only while
the repository remains public.

A production validator-platform repository would normally be private. Its Flux
source therefore needs an authentication and promotion boundary that does not
depend on a developer credential or a long-lived personal access token.

Two private-source designs are viable:

1. Flux reads private GitHub directly through a repository-scoped GitHub App.
2. GitHub remains the private authoring surface, CI promotes signed desired
   state to private ECR, and Flux reads the OCI artifact through EKS Pod
   Identity.

The second design removes GitHub credentials from the cluster, but expands CI
authority and introduces an artifact-packaging boundary. Signature verification
proves producer identity and integrity; it does not prove that the contents are
safe.

## Decision

Retain the current read-only SSH deploy key for the public lab. If the source
repository becomes private before OCI promotion is implemented, a GitHub App is
the accepted intermediate design. The preferred production design is signed
OCI promotion to private ECR.

### Direct private Git through a GitHub App

The Flux source must use:

- `kind: GitRepository`;
- `spec.provider: github`;
- an HTTPS repository URL;
- a Secret containing `githubAppID`, `githubAppPrivateKey`, and exactly one of
  `githubAppInstallationOwner` or `githubAppInstallationID`.

The App installation is limited to this repository with repository contents
read-only and metadata read. The App private key is still a long-lived
credential: GitHub App keys do not expire and must be rotated or revoked. Flux
uses it to obtain installation tokens that expire after one hour.

AWS Secrets Manager is the source of truth for the App key, but External
Secrets Operator cannot bootstrap the first Flux fetch because ESO is itself
installed by Flux. The trusted-local bootstrap operator must materialize the
Secret without writing its value to Git, Terraform state, argv, or logs.

### Private Git promoted to signed OCI in ECR

The promotion job runs only for protected `main`, reads the repository with the
job's ephemeral repository token, assumes one ECR-publisher role through GitHub
OIDC, and publishes an OCI artifact. The cluster's source-controller reads that
repository through EKS Pod Identity. No GitHub or static AWS credential is held
by the cluster.

The artifact is produced from the repository root, with explicit exclusions.
This is required because `clusters/dev` references paths under `platform/` and
the generated HelmReleases reference `charts/ethereum-node`. Packaging only
`clusters/dev` creates an incomplete source artifact even though the top-level
Kustomization exists.

Flux watches a `main` promotion tag, resolves it to an immutable digest, and
verifies a cosign keyless signature against the exact GitHub Actions OIDC issuer
and workflow subject before publishing the source artifact. A literal
`spec.ref.digest` would freeze updates until another writer changed the source
object, recreating an out-of-band deployment path. The mutable channel selects
candidates; digest-bound signature verification authorizes the resolved
content. CI reaches Fulcio to obtain its signing certificate. At verification
time source-controller uses Fulcio trust-root material and Rekor
transparency-log evidence; the design therefore records Rekor as the runtime
network dependency and does not claim that Flux requests a certificate from
Fulcio.

The ECR writer cannot access EKS, RDS, Secrets Manager, or IAM mutation APIs.
The source-controller role has ECR read permissions only. Branch protection,
review, required checks, and workflow-file ownership remain content controls.

## Failure semantics

If fetching or verification of a new revision fails, source-controller must not
publish that failed or unverified revision as a new source artifact. This is the
qualified guarantee.

The last successful artifact can remain in source-controller storage and
existing workloads can continue running. This ADR deliberately does not claim
that all downstream Kustomizations become not-Ready, that the old artifact can
never be reconciled again, or that drift correction stops. Those behaviors are
controller-version and failure-mode dependent and require a live negative-path
drill before they become operational claims.

## Alternatives considered

### Anonymous HTTPS

It removes the credential but works only because the lab repository is public.
It does not model the intended private source-control boundary.

### Continue SSH deploy keys for private Git

The current key is repository-scoped and read-only, so it is materially better
than a developer key. It remains long-lived, must be placed into each cluster,
and has a manual rotation and revocation path.

### Personal access token

Rejected. It binds reconciliation to a human identity and carries broader,
longer-lived authority than the source requires.

### GitHub App as the final production design

Acceptable, and preferred over a PAT or developer SSH key, but it retains a
long-lived GitHub App private key in the cluster. OCI promotion removes that
credential and separates Git review from cluster retrieval.

### Cosign key pair instead of keyless signing

It reduces public transparency-service dependence during verification but
introduces signing key material that must be stored, distributed, rotated, and
revoked. It remains a fallback if network or compliance requirements prohibit
the keyless path.

## Consequences

Benefits:

- no human Git credential is used by Flux;
- the production target keeps GitHub credentials out of EKS entirely;
- GitHub-to-AWS and EKS-to-ECR authentication use short-lived workload
  identities;
- each promoted candidate is resolved to a digest and producer-verified before
  it becomes a source artifact;
- the direct GitHub App fallback has an explicit bootstrap and rotation model.

Costs and open work:

- the OCI path grants one workflow scoped ECR-write authority;
- artifact closure, provenance, signature, and identity policy need automated
  implementation and negative tests;
- ECR, IAM, Pod Identity, and network egress must be provisioned;
- GitHub App rotation/revocation and OCI verification failures are designed but
  not exercised;
- the current SSH deploy key remains until a separately reviewed migration.

Nothing in this ADR changes the live cluster or authorizes those mutations.

## References

- [Flux GitRepository GitHub App authentication](https://fluxcd.io/flux/components/source/gitrepositories/#github)
- [Flux OCIRepository authentication and keyless verification](https://fluxcd.io/flux/components/source/ocirepositories/)
- [GitHub App private-key rotation](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/managing-private-keys-for-github-apps)
- [GitHub App installation tokens](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
