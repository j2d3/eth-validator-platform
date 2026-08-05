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
| [Reth + Teku](reth-teku.md) | Composes two proven single-variable adapters (originally the 2×2 completion; later became the signing home for validator #4) |
| [Erigon + Lighthouse](erigon-lighthouse.md) | Extends execution diversity to a third distinct implementation strategy (staged sync) |
| [Geth + Nimbus](geth-nimbus.md) | First Nim-language CL against a proven EL |
| [Besu + Teku](besu-teku.md) | First JVM-EL + JVM-CL composition (heap-sensitive process co-scheduling) |
| [Nethermind + Lighthouse](nethermind-lighthouse.md) | Fifth EL runtime (.NET/CLR) and the first pair consuming Nethermind-format `chainspec.json` |
| [Nethermind + Prysm](nethermind-prysm.md) | Last CL adapter; the only client that must rewrite a bundle file before exec (Prysm config derivation) |

That framing makes the matrix feel intentional rather than a client-logo
collection. Each page explains what the variable exposed — telemetry
differences, storage layout, engine-API wiring, chart adapter quirks.

## Status of #130

The [#130 umbrella](https://github.com/j2d3/eth-validator-platform/issues/130)
selected four new non-signing pairs. All four are declared:
Erigon+Lighthouse, Besu+Teku, Geth+Nimbus, and Nethermind+Prysm. The
last one arrived in two steps rather than one — the Nethermind EL
adapter shipped first and was activated against the well-exercised
Lighthouse CL, which isolated the new EL as the observed variable and
left Prysm as the only unshipped adapter. Nethermind+Lighthouse is
therefore a pair #130 did not name, and it stays in the fleet on its own
merits (fifth EL runtime, first `chainspec.json` consumer).

The 5×4 EL×CL Cartesian would have 20 cells; the declared set is
**nine pairs**. Nine was never the whole space — it is a curated subset
chosen for the distinct variables each pair isolates.

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
