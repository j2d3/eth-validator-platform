# Documentation index

Documentation is split by **what question it answers**. When the same fact
lives in two places, one of them is stale — this index is the authoritative
map.

## Front door

The [top-level README](../README.md) is intentionally short. It states what the
project is, what has been demonstrated, and where to look. Longer material
lives here.

## By question

| I want to know… | Look at |
|---|---|
| What is this project trying to be? | [PRD](prd/001-dynamic-validator-platform.md) |
| How does the whole system fit together? | [architecture/system-overview.md](architecture/system-overview.md) |
| Where are the trust boundaries? | [architecture/safety-and-custody-boundaries.md](architecture/safety-and-custody-boundaries.md) |
| What does subsystem X own and how does it fail? | [components/](components/) — one page per subsystem |
| Why was this specific client pair added? | [client-pairs/](client-pairs/) — one page per pair |
| How do I perform operator procedure X safely? | [runbooks/](runbooks/) |
| What was actually observed at a specific commit? | [evidence/](evidence/) |
| Why was durable decision X made? | [adrs/](adrs/) |
| How does the two-agent build model work? | [development/agentic-workflow.md](development/agentic-workflow.md) |
| How do I reproduce the two-agent GitHub workflow? | [development/two-agent-setup.md](development/two-agent-setup.md) |
| How is the local development environment set up? | [runbooks/local-development.md](runbooks/local-development.md) |
| How does an operator bootstrap EKS + Flux? | [runbooks/eks-flux-bootstrap.md](runbooks/eks-flux-bootstrap.md) |
| How is one validator key generated, deposited, and onboarded? | [runbooks/validator-key-ceremony.md](runbooks/validator-key-ceremony.md) |
| How would a private repository supply Flux desired state? | [ADR 0002](adrs/0002-private-flux-source-authentication.md) and [runbook](runbooks/flux-source-authentication.md) |
| How would slashing history be recovered, and what proves the copy is usable? | [runbooks/rds-slashing-recovery-drill.md](runbooks/rds-slashing-recovery-drill.md) |
| Where does the code live? | [`README.md`'s repository map](../README.md#repository-map) |

## Documentation types

Each type has a distinct job. Content that fits two types belongs in exactly
one.

| Type | Half-life | Answers | Owns |
|---|---|---|---|
| README | Days | "Should I keep reading?" | Elevator pitch, current-matrix table, entry-point links |
| Architecture | Years | "How does it fit together?" | Trust boundaries, control planes, invariants |
| Components | Months | "What does this subsystem own?" | Per-subsystem contract, implementation, failure modes |
| Client pairs | Months | "Why does this combination matter?" | Per-pair "what variable did it introduce" |
| Runbooks | Months | "How do I do this safely?" | Operator procedures with fail-closed steps |
| Evidence | Immutable | "What did we observe at time T?" | Sanitized runtime observations, one per event |
| PRD | Years | "What are we building?" | Product requirements, safety invariants, phases |
| ADRs | Years | "Why did we choose this?" | Durable decisions and their tradeoffs |
| Agentic workflow | Months | "How was this built?" | Two-agent collaboration model narrative |

## Sections

### [Architecture](architecture/)

- [system-overview.md](architecture/system-overview.md) — how Terraform, Flux,
  and the applications fit together
- [safety-and-custody-boundaries.md](architecture/safety-and-custody-boundaries.md)
  — where secrets live and what enforces the boundaries

### [Components](components/)

Per-subsystem contract, implementation, and failure modes. Placeholder pages
link to the current runbook or PRD section that owns each subsystem.

- [desired-state-catalog.md](components/desired-state-catalog.md)
- [terraform-aws-foundation.md](components/terraform-aws-foundation.md)
- [eks-capacity-and-storage.md](components/eks-capacity-and-storage.md)
- [flux-reconciliation.md](components/flux-reconciliation.md)
- [ethereum-node-chart.md](components/ethereum-node-chart.md)
- [network-profiles.md](components/network-profiles.md) (links to
  [runbooks/network-profiles.md](runbooks/network-profiles.md))
- [lifecycle-automation.md](components/lifecycle-automation.md)
- [secrets-and-key-projection.md](components/secrets-and-key-projection.md)
- [web3signer-and-slashing-protection.md](components/web3signer-and-slashing-protection.md)
- [networking-and-ingress.md](components/networking-and-ingress.md) (links to
  [runbooks/operations-ingress.md](runbooks/operations-ingress.md))
- [observability-and-portal.md](components/observability-and-portal.md) (links
  to [runbooks/portal-telemetry.md](runbooks/portal-telemetry.md))
- [ci-and-runtime-qualification.md](components/ci-and-runtime-qualification.md)

### [Client pairs](client-pairs/)

Each pair page answers: why this combination, what variable it introduced,
what did we learn.

- [README.md](client-pairs/README.md) — matrix summary and the framing
- [geth-lighthouse.md](client-pairs/geth-lighthouse.md)
- [reth-lighthouse.md](client-pairs/reth-lighthouse.md)
- [geth-teku.md](client-pairs/geth-teku.md)
- [reth-teku.md](client-pairs/reth-teku.md)
- [erigon-lighthouse.md](client-pairs/erigon-lighthouse.md)

### [Runbooks](runbooks/)

Operator procedures.

- [local-development.md](runbooks/local-development.md)
- [eks-flux-bootstrap.md](runbooks/eks-flux-bootstrap.md)
- [eks-capacity.md](runbooks/eks-capacity.md)
- [eks-ephemery-sync.md](runbooks/eks-ephemery-sync.md)
- [network-profiles.md](runbooks/network-profiles.md)
- [operations-ingress.md](runbooks/operations-ingress.md)
- [portal-telemetry.md](runbooks/portal-telemetry.md)
- [ethereum-alerts.md](runbooks/ethereum-alerts.md)
- [validator-key-ceremony.md](runbooks/validator-key-ceremony.md)
- [rds-slashing-recovery-drill.md](runbooks/rds-slashing-recovery-drill.md)
- [flux-source-authentication.md](runbooks/flux-source-authentication.md)
- [dependabot.md](runbooks/dependabot.md)

### [Evidence](evidence/)

Sanitized runtime observations at a specific commit and time. Immutable.

- [README.md](evidence/README.md) — evidence-record rules
- [2026-08-04-eks-network-policy.md](evidence/2026-08-04-eks-network-policy.md)
- [2026-08-04-first-signing-validator.md](evidence/2026-08-04-first-signing-validator.md)
- [2026-08-05-eks-spot-rebalance.md](evidence/2026-08-05-eks-spot-rebalance.md)

### [PRD](prd/)

The product-requirements-and-architecture contract.

- [001-dynamic-validator-platform.md](prd/001-dynamic-validator-platform.md)

### [ADRs](adrs/)

Accepted architecture decisions.

- [0001-local-first-kind.md](adrs/0001-local-first-kind.md)
- [0002-private-flux-source-authentication.md](adrs/0002-private-flux-source-authentication.md)

### [Development](development/)

- [agentic-workflow.md](development/agentic-workflow.md) — narrative
  companion to [COLLABORATION.md](../COLLABORATION.md)

## Legacy content

[production-evolution.md](production-evolution.md) predates this split; it
still describes the local-vs-EKS-vs-production gap. Its content will be
folded into `architecture/` and `components/` pages over time. The PRD remains
authoritative if they disagree.

## Contributing to the docs

- Pick the type from the table above before creating a new page.
- Each client pair PR must include its `docs/client-pairs/<execution>-<consensus>.md`
  page — a CI contract enforces the pairing.
- Evidence pages are append-only; correct mistakes with a new-record + a
  correction note, not by editing history.
- Runtime state that changes hourly (current pair count, current signing
  validators) belongs on the portal, not in `README.md` or docs pages.
