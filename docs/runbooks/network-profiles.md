# Ethereum network profiles

Network selection is a safety boundary, not a convenience string. A validator
identity and every assignment of that identity reference one reviewed
`NetworkProfile`. The profile binds a friendly family name to immutable chain
identity and to typed client adapters.

The operating unit is therefore:

`customer + validator identity + assignment + service profile + network profile + storage profile`

Customer-facing workflows select an existing profile by name. They do not
accept client flags, genesis URLs, bootnodes, checkpoint endpoints, or signer
configuration.

## Implemented contract

The current catalog retains Hoodi as a built-in-network example and runs one
generation-pinned Ephemery profile on EKS:

- Geth receives the typed built-in adapter `--hoodi`.
- Lighthouse beacon and validator clients receive `--network=hoodi`.
- the catalog carries Hoodi chain/network ID, EL genesis hash, CL genesis
  validators root, fork version, and deposit metadata;
- assignment and identity references must agree;
- a deterministic fingerprint covers family, generation, immutable chain
  identity, and any future artifact digest;
- Pods, PVCs, lifecycle records, Prometheus series, and Loki streams carry the
  profile, generation, or fingerprint needed to distinguish network state;
- a checkpoint endpoint may change without changing chain identity;
- Ephemery generation `162` pins `testnet-all.tar.gz` at SHA-256
  `478ca7181212f2d87137c337e854befbed8aacde8bee8f64d6ca7e28967ee2fb`;
- a non-root init path downloads that exact HTTPS release with time/size bounds,
  verifies the whole archive, and extracts only the reviewed file map;
- Geth initializes the verified `genesis.json`, receives the profile's numeric
  network ID and bootnodes, and rejects non-empty or mismatched chain data;
- Lighthouse beacon nodes receive `--testnet-dir=/network/files`, where the
  verified `config.yaml`, `genesis.ssz`, and `boot_enr.yaml` live, plus the
  reviewed checkpoint-sync endpoint;
- the signing Lighthouse validator client obtains genesis bytes from its
  already-synced internal beacon endpoint, verifies the profile's genesis time
  and validators root, and initializes its own data directory before duties;
- an Ephemery-specific Web3Signer instance reads the committed custom-network
  configuration and deposit-contract block metadata without downloading
  mutable network data at runtime;
- resetting profiles put both a deterministic full-pair hash and the
  identity-fingerprint prefix in their PVC names, so long pair references do
  not collide and a new generation renders different execution and consensus
  claims.

The original artifact adapter was deliberately node-only. The reviewed signing
slice now enables one deposited identity only for the active Geth + Lighthouse
assignment. Reth + Lighthouse remains non-signing. The chart still rejects
validator duties unless the catalog provides an active registered identity,
signer reference, fee recipient, and both signing-safety confirmations.

The first observed duty is recorded in
[the EKS signing evidence](../evidence/2026-08-04-first-signing-validator.md).

Hoodi's canonical values are maintained by the
[eth-clients Hoodi repository](https://github.com/eth-clients/hoodi).

## Ephemery shape

Ephemery is the proof that the abstraction handles more than named client
flags. It publishes a new custom genesis and configuration bundle for each
reset. A profile is therefore generation-addressed (`ephemery-162`, not
`ephemery`) and pins the release bundle by SHA-256. Runtime workloads must never
download from a mutable `/latest/` URL.

The committed generation profile provides:

- the exact chain/network ID, EL genesis hash, CL genesis validators root,
  fork version, genesis time, and deposit metadata;
- a release URL and SHA-256 for `testnet-all.tar.gz`;
- paths to `genesis.json`, `config.yaml`, `genesis.ssz`, execution bootnodes,
  and consensus bootnodes inside that verified bundle;
- a Geth init-container contract for a new generation-specific data volume;
- Lighthouse beacon-node and validator-client custom-network configuration;
- explicit reset/EOL metadata and a successor-by-reviewed-PR policy.

The signer network configuration is a separate artifact from the EL/CL release
bundle, but it is bound to the same immutable profile fields. Flux mounts it in
the signing namespace and the validator client independently verifies genesis
metadata from the internal beacon API before starting.

Render the catalog projection without editing chart values by hand:

```bash
python3 tools/render_local_assignments.py --values-for assignment-ephemery-162-synthetic

python3 tools/render_local_assignments.py \
  --values-for assignment-ephemery-162-synthetic \
  | helm template ephemery-162 charts/ethereum-node \
      --namespace ethereum --values - --set lifecycleState=active
```

The non-signing lifecycle workflow now refuses
`assignment-ephemery-162-synthetic` because that assignment carries a deposited
identity and signing authorization. It remains valid for the non-signing Reth +
Lighthouse assignment. Changes to the signing assignment require an explicit
reviewed catalog PR until a dedicated signing workflow implements equivalent
gates.

The [official Ephemery resources](https://github.com/ephemery-testnet/ephemery-resources)
state that existing nodes must use a different data directory or delete the
old one after a reset. This platform chooses different PVC identity by default:
an old volume marker must never be accepted by a new generation.

## Signer boundary

A Web3Signer `eth2` process has one network configuration. The live signer is
therefore bound to Ephemery generation 162 and admits only the deposited key
for that generation. Concurrent Hoodi or successor-Ephemery duties require a
separate signer deployment and admitted-key set.

The first Ephemery qualification used a new validator signing key and the
generation-bound signer with RDS-backed slashing protection. Public-key reuse
is cryptographically domain-separated by genesis data, but epochs and slots
restart and retained slashing high-watermarks may reject duties. The platform
therefore does not reuse that signing identity across reset generations.
Sharing slashing state across generations remains unqualified until
conflicting signatures and restore behavior are tested against the exact
Web3Signer schema.

Web3Signer supports a named network or a custom local YAML configuration through
its [`eth2 --network` option](https://docs.web3signer.consensys.io/reference/cli/subcommands#network).

## Rollover sequence

1. Disable validator duties and remove signer admission.
2. Stop the old assignment; retain its profile, audit record, keys, and slashing history.
3. Add a new generation-addressed, digest-pinned profile by reviewed PR.
4. Create new EL/CL PVC identities and reject any mismatched volume marker.
5. Generate and deposit a new test-only validator identity for that generation.
6. Deploy or reconfigure a signer bound only to the new profile.
7. Verify EL chain ID and block-zero hash, CL genesis time/root, signer profile,
   sync, uniqueness, and doppelganger gates.
8. Enable duties only after every gate passes.

The old profile is never edited in place. A monitor may notice a new Ephemery
release and open a proposed update PR, but it may not update workloads directly.

## Adding another testnet

For a client-built-in network:

1. add one schema-valid `applications/networks/<profile>.yaml`;
2. add canonical identity and deposit metadata from the network's maintained
   configuration source;
3. select typed built-in adapters already supported by the chart;
4. add golden render and identity tests;
5. bind an identity and assignment to the same profile.

For a custom network, also implement or reuse the digest-pinned artifact adapter
and qualify its contents, initialization, signer configuration, and reset
semantics. A new arbitrary flag path is not an acceptable adapter.
