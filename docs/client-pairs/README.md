# Client-pair profiles

Each page explains one specific execution + consensus client combination as
integrated into this platform. Not generic client documentation — the answer to
"what did this particular pair introduce, and what did we learn?"

## Framing

Every pair introduces exactly one variable:

| Pair | Variable introduced |
|---|---|
| [Geth + Lighthouse](geth-lighthouse.md) | Baseline vertical slice — first complete signing-duty path |
| [Reth + Lighthouse](reth-lighthouse.md) | Changes only the execution client from the baseline |
| [Geth + Teku](geth-teku.md) | Changes only the consensus client from the baseline |
| [Reth + Teku](reth-teku.md) | Completes the original 2×2 matrix by composing two proven single-variable adapters |
| [Erigon + Lighthouse](erigon-lighthouse.md) | Extends execution diversity to a third distinct implementation strategy (staged sync) |

That framing makes the matrix feel intentional rather than a client-logo
collection. Each page explains what the variable exposed — telemetry
differences, storage layout, engine-API wiring, chart adapter quirks.

## Definition of done for a new pair

A pair is not "done" until its profile page exists, so this list also
serves as the acceptance checklist:

- Why the pair was selected (which variable it introduces or isolates).
- Service profile and assignment identifiers.
- Execution, beacon, and validator-client topology.
- Network-generation configuration.
- Storage and restart behavior.
- Engine API and remote-signer wiring.
- Client-specific command-line adaptations.
- Metric normalization and observability differences.
- Non-signing qualification evidence (with a link to the evidence file).
- Signing qualification evidence, where applicable.
- Problems encountered and corrections made.
- Remaining unqualified behavior.
- Links to the chart PR, the catalog PR, related issues, dashboards, and
  evidence records.

CI enforces that every declared ServiceProfile has a matching
`docs/client-pairs/<execution>-<consensus>.md` page. Adding a new pair
without the profile fails the docs-completeness contract.
