# Container image security evidence

This runbook covers the first supply-chain scanning slice tracked by issue #43.
It inventories every in-scope image-bearing repository source and runs a weekly
or manually dispatched Trivy vulnerability scan for each exact digest. The
workflow publishes one JSON result artifact per image for 14 days.

It is intentionally an **evidence workflow, not yet a promotion gate**. A green
workflow means inventory and scanner execution succeeded. It does not mean the
images contain no vulnerabilities, and the scanner uses `exit-code: 0` while
the exception and promotion policy is still being designed. Findings of every
severity, including unfixed findings, remain in the retained JSON.

## Run or inspect it

The source-derived inventory is available without registry access:

```bash
python3 tools/discover_container_images.py --format markdown
python3 tools/discover_container_images.py --format json
```

Run **Container image security evidence** from GitHub Actions or wait for its
Monday schedule. The inventory job summary lists exact subjects and explicit
coverage gaps. Download `container-image-inventory` plus the `image-scan-*`
artifacts from the same run before triage. Each image artifact contains the
exact subject, Trivy JSON result, structured scanner/database version evidence,
and workflow source/checkout SHA provenance. Before upload, a separate verifier
fails unless Trivy identifies the result as a container image, names the exact
requested subject, and reports the requested digest in `Metadata.RepoDigests`.
Treat report creation time and vulnerability-database update time as different
fields; a new report backed by a stale database is stale evidence. Do not report
zero findings when the workflow did not run, verification failed, a scan or
version artifact is absent, or registry access failed; those states are
**unknown/unavailable**.

## Current coverage boundary

The discovery tool searches repository-wide runtime/configuration sources
rather than a list of current workload files. Its machine-checked inputs are:

- every YAML file outside explicitly non-runtime paths, including GitHub
  workflow containers, every current or future `clusters/*` Flux tree, every
  `platform/*` manifest, chart values, and Kustomize image replacements;
- every shell image constant and every Dockerfile base image;
- literal image lines in Helm templates, while templated image expressions are
  resolved from the in-scope values files;
- every third-party HelmRelease, retained as an explicit transitive-image gap
  until the pinned chart is rendered into committed digest identities.

Current exact subjects therefore include:

- digest-pinned Geth, Lighthouse, validator init, Web3Signer, Flyway, and
  CloudNativePG PostgreSQL images declared directly in desired state;
- digest-pinned Loki, Loki canary, and Alloy identities shared with the
  hardened runtime-contract verifier;
- the digest-pinned `kindest/node` image used to create the local Kubernetes
  cluster.

It also reports, but does not scan as durable identities:

- Flux bootstrap controller images still named by tags;
- transitive images supplied by pinned third-party Helm chart defaults.

Those are real gaps. Resolving a tag during a workflow and scanning whatever it
means that day would create mutable evidence, so this slice refuses to call that
coverage. Narrative docs, test fixtures, generated tool/dependency directories,
secret material, and templated Helm expressions backed by scanned values files
are emitted separately as non-runtime scope exclusions; an image-bearing YAML,
shell constant, Dockerfile base, or literal Helm template line outside those
boundaries cannot silently disappear from the inventory. A follow-up must render
every pinned third-party chart, commit or verify each multi-platform digest, and
then make exact identity a promotion requirement rather than an evidence gap.

## Triage and evolution

For each Critical or High result:

1. confirm the verifier-bound digest and workflow provenance in
   `scan-subject.json` match the Trivy report and inventory artifact;
2. distinguish fixed from unfixed findings without hiding either;
3. check the upstream image release and rebuild history;
4. prefer a reviewed digest upgrade, rerun the runtime contract, and rescan;
5. if the risk must be accepted temporarily, do not add an unbounded CVE
   ignore. The next policy slice will require digest, CVE, rationale, owner, and
   expiry for every exception.

The later promotion gate will block fixed Critical/High findings after those
exception semantics are implemented and tested. Project-owned images will add
an SBOM and keyless provenance before admission policy is considered. ECR
scan-on-push and repository lifecycle controls remain a separate Terraform
slice; this workflow has no AWS credential, OIDC token, push permission, or
cluster authority.
