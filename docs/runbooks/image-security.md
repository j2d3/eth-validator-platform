# Container image security evidence

This runbook covers the first supply-chain scanning slice tracked by issue #43.
It inventories every in-scope image-bearing repository source and runs a weekly
or manually dispatched Trivy vulnerability scan for each exact digest. Pushes
to `main` also run the full matrix. Pull requests always build the inventory;
they skip the matrix only when the exact image/digest set and coverage
boundaries match the base commit and none of the evidence tooling changed. The
workflow publishes one JSON result artifact per scanned image for 14 days.

It is intentionally an **evidence workflow, not yet a promotion gate**. A green
workflow means inventory and scanner execution succeeded. It does not mean the
images contain no vulnerabilities, and the scanner uses `exit-code: 0` because
promotion policy is not implemented. Findings of every severity, including
unfixed findings, remain in the retained JSON.

Each verified report is also evaluated into `image-scan-decision.json`. The
machine-readable document counts Critical and High finding occurrences
separately, split between findings with and without a non-empty Trivy
`FixedVersion`. It records the evaluation time, an evidence-only outcome, and
always states `promotionGate: false`. A successful evaluation means the report
and any exception metadata are internally valid; it does not mean the image is
approved for promotion.

Fresh scans also aggregate those per-image documents into
`image-scan-summary.json`. Aggregation downloads the `container-image-inventory`
artifact retained by the same run and binds itself to it: the scanned
`(image, digest)` subjects must equal the discovered exact subjects. A discovered
subject with no decision, a decision for an undiscovered subject, and a duplicate
on either side all fail the job rather than producing a quietly smaller count.

The workflow then publishes exact-subject coverage, the unresolved
coverage-gap count, and raw Critical/High occurrence totals, including the
subset with a non-empty `FixedVersion`, in a successful check name bound to the
same workflow run and source SHA. GitHub exposes that check metadata through its
public read-only API, which lets the portal show coverage and counts without a
repository token or write permission.

**Findings cover the exact discovered subjects only.** `21/21 exact subjects
scanned` means every digest the discovery tool could resolve from committed
desired state was scanned; it says nothing about the images behind the reported
coverage gaps. Those unresolved sources — mutable Flux controller tags and
transitive images from pinned third-party chart defaults — have **unknown**
findings, not zero. The counts likewise remain evidence only, and are not
unique-vulnerability counts or a promotion decision.

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
the evidence-only decision, and workflow source/checkout SHA provenance. Before
upload, a separate verifier
fails unless Trivy identifies the result as a container image, names the exact
requested subject, and reports the requested digest in `Metadata.RepoDigests`.
Treat report creation time and vulnerability-database update time as different
fields; a new report backed by a stale database is stale evidence. Do not report
zero findings when the workflow did not run, verification failed, a scan or
version artifact is absent, or registry access failed; those states are
**unknown/unavailable**.

The portal accepts aggregate coverage and counts only when the check is
successful, its head SHA matches the latest completed `main` image-security run,
and its job URL is part of that exact run. It also rejects impossible values,
such as more scanned subjects than were discovered or an inventory with no
subject at all. Missing or malformed evidence renders as unavailable; it never
renders as zero. The portal shows the scanned/discovered subject ratio next to
the unresolved coverage-gap count so that partial coverage is visible rather
than implied to be complete.

For an unchanged-inventory pull request, the `Container image evidence
decision` check records that no new Trivy execution occurred. That result reuses
only the identity-level fact that the reviewed source still names the same
exact digests; it does not manufacture a fresh scan timestamp or new finding
set. Scheduled, manual, evidence-tooling, and `main` runs never take this reuse
path, so the public portal's latest-`main` workflow result continues to refer to
a full scan.

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

Those are real gaps, and their count is published publicly alongside the
exact-subject result precisely so that the incompleteness is not hidden behind a
green check. Resolving a tag during a workflow and scanning whatever it means
that day would create mutable evidence, so this slice refuses to call that
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
5. if the risk must be accepted temporarily, add a per-image exception document
   under `security/image-vulnerability-exceptions/`. Every entry must name the
   exact digest, vulnerability ID, rationale, GitHub owner, and UTC expiry. The
   evaluator rejects malformed, expired, duplicate, wrong-digest, or unused
   entries rather than silently ignoring them.

An exception for one digest and vulnerability ID covers every occurrence of
that ID across the report's packages and targets. Raw counts remain
occurrence-based, and the output retains both total and unexcepted counts.
Expiry is checked against the current workflow evaluation time, not the older
report creation time, so retained evidence cannot keep an expired exception
valid.

These exception records annotate the evidence; they do not suppress the raw
finding counts or authorize deployment. A later promotion policy still needs a
reviewed severity/fix-availability threshold, an explicit treatment for
unfixed findings, approval ownership, and enforcement at the artifact promotion
boundary. Project-owned images will add an SBOM and keyless provenance before
admission policy is considered. ECR
scan-on-push and repository lifecycle controls remain a separate Terraform
slice; this workflow has no AWS credential, OIDC token, push permission, or
cluster authority.
