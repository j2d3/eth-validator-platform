{{- define "ethereum-node.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ethereum-node.identityLabels" -}}
platform.galaxy-lab/network: {{ .Values.networkProfile.family }}
platform.galaxy-lab/network-profile: {{ .Values.networkProfile.name }}
platform.galaxy-lab/network-generation: {{ .Values.networkProfile.generation | quote }}
platform.galaxy-lab/execution-client: {{ .Values.executionClient }}
platform.galaxy-lab/consensus-client: {{ .Values.consensusClient }}
platform.galaxy-lab/customer-id: {{ .Values.identity.customerId }}
platform.galaxy-lab/validator-id: {{ .Values.identity.validatorId }}
platform.galaxy-lab/assignment-id: {{ .Values.identity.assignmentId }}
{{- end -}}

{{- define "ethereum-node.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name (include "ethereum-node.name" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.labels" -}}
app.kubernetes.io/name: {{ include "ethereum-node.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: ethereum-validator-platform
platform.galaxy-lab/lifecycle: {{ .Values.lifecycleState }}
{{ include "ethereum-node.identityLabels" . }}
{{- end -}}

{{- define "ethereum-node.selectorLabels" -}}
app.kubernetes.io/name: {{ include "ethereum-node.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{/*
Resetting networks never reuse chain-data PVC identity across generations. The
immutable profile fingerprint is part of each PVC name as well as its
annotation. Persistent networks retain the original names unchanged.
*/}}
{{- define "ethereum-node.executionPvcName" -}}
{{- if eq .Values.networkProfile.resetPolicy "replace-data" -}}
{{- $pairName := include "ethereum-node.fullname" . -}}
{{- printf "%s-%s-%s-execution" ($pairName | trunc 20 | trimSuffix "-") (sha256sum $pairName | trunc 16) (.Values.networkProfile.identityFingerprint | trunc 12) | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-execution" (include "ethereum-node.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.consensusPvcName" -}}
{{- if eq .Values.networkProfile.resetPolicy "replace-data" -}}
{{- $pairName := include "ethereum-node.fullname" . -}}
{{- printf "%s-%s-%s-consensus" ($pairName | trunc 20 | trimSuffix "-") (sha256sum $pairName | trunc 16) (.Values.networkProfile.identityFingerprint | trunc 12) | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-consensus" (include "ethereum-node.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.validatorPvcName" -}}
{{- if eq .Values.networkProfile.resetPolicy "replace-data" -}}
{{- $pairName := include "ethereum-node.fullname" . -}}
{{- printf "%s-%s-%s-validator" ($pairName | trunc 20 | trimSuffix "-") (sha256sum $pairName | trunc 16) (.Values.networkProfile.identityFingerprint | trunc 12) | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-validator" (include "ethereum-node.fullname" .) | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}

{{/*
The scrape-time telemetry contract. Every label here is attached to every
series the pair exposes, and every recording rule retains all of them through
its aggregation, so a normalized series can always be traced back to one
cluster, environment, lifecycle state and identity. cluster/environment are
deliberately set here rather than through Prometheus externalLabels: external
labels are applied on remote-write and federation, not to locally queried
series, so they cannot be selected on in a local dashboard query.
*/}}
{{- define "ethereum-node.telemetryLabels" -}}
cluster, environment, lifecycle_state, network, network_profile, network_generation, network_identity, customer_id, validator_id, assignment_id, execution_client, consensus_client
{{- end -}}

{{/*
Execution-client adapters.

Each supported EL contributes four values through these helpers:
  - dataDirMarker   — the sentinel directory name inside /data that proves
                      this PVC belongs to an initialized instance of THIS
                      client (not another). Used to fail-fast on cross-client
                      PVC misattachment.
  - initCommand     — the shell script body run by the initialize-<el>-genesis
                      init container, with `set -eu`, PVC-marker fencing, and
                      the client's native genesis-import invocation.
  - runCommand      — the shell script body run by the execution container.
                      Reads the digest-verified bootnodes file and execs the
                      client with EL-specific flags for chain-id, data dir,
                      HTTP RPC, Engine API JWT, and metrics.

These helpers exist so a second EL adapter can plug in through the same
StatefulSet template without duplicating the init/runtime plumbing (PVC
namespacing, JWT mount, network artifact layout, security context).
*/}}
{{- define "ethereum-node.executionInitCommand" -}}
{{- $client := .Values.executionClient -}}
{{- if eq $client "geth" -}}
{{- include "ethereum-node.gethInitCommand" . -}}
{{- else if eq $client "reth" -}}
{{- include "ethereum-node.rethInitCommand" . -}}
{{- else if eq $client "erigon" -}}
{{- include "ethereum-node.erigonInitCommand" . -}}
{{- else -}}
{{- fail (printf "no init-command adapter for executionClient=%q" $client) -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.executionRunCommand" -}}
{{- $client := .Values.executionClient -}}
{{- if eq $client "geth" -}}
{{- include "ethereum-node.gethRunCommand" . -}}
{{- else if eq $client "reth" -}}
{{- include "ethereum-node.rethRunCommand" . -}}
{{- else if eq $client "erigon" -}}
{{- include "ethereum-node.erigonRunCommand" . -}}
{{- else -}}
{{- fail (printf "no run-command adapter for executionClient=%q" $client) -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.executionDataDirMarker" -}}
{{- $client := .Values.executionClient -}}
{{- if eq $client "geth" -}}geth
{{- else if eq $client "reth" -}}db
{{- else if eq $client "erigon" -}}chaindata
{{- else -}}
{{- fail (printf "no dataDirMarker for executionClient=%q" $client) -}}
{{- end -}}
{{- end -}}

{{/*
The network-profile adapter for the currently selected execution client.
Every EL adapter dictionary has the same shape (`mode` plus optionally
`network`), so callers can read `.mode` and `.network` without knowing
which client is active.
*/}}
{{- define "ethereum-node.executionAdapter" -}}
{{- $client := .Values.executionClient -}}
{{- $adapter := index .Values.networkProfile.clients $client -}}
{{- if not $adapter -}}
{{- fail (printf "networkProfile.clients.%s is required when executionClient=%q" $client $client) -}}
{{- end -}}
{{- toYaml $adapter -}}
{{- end -}}

{{/*
Consensus-client adapters. Same dispatcher pattern as the execution adapters
above. Today Lighthouse is the only registered CL; adding Teku/Nimbus/Prysm
is chart-side work that plugs in through the same dispatcher without
touching node.yaml.
*/}}
{{- define "ethereum-node.consensusRunCommand" -}}
{{- $client := .Values.consensusClient -}}
{{- if eq $client "lighthouse" -}}
{{- include "ethereum-node.lighthouseRunCommand" . -}}
{{- else if eq $client "teku" -}}
{{- include "ethereum-node.tekuRunCommand" . -}}
{{- else -}}
{{- fail (printf "no run-command adapter for consensusClient=%q" $client) -}}
{{- end -}}
{{- end -}}

{{- define "ethereum-node.lighthouseRunCommand" -}}
set -eu
bootnodes="$(paste -sd, {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.consensusBootnodesText | quote }})"
test -n "$bootnodes"
exec lighthouse bn \
  {{ printf "--testnet-dir=/network/files" | quote }} \
  {{ printf "--checkpoint-sync-url=%s" .Values.networkProfile.checkpointSync.primaryUrl | quote }} \
  --boot-nodes="$bootnodes" \
  --datadir=/data \
  --execution-endpoint=http://127.0.0.1:8551 \
  --execution-jwt=/jwt/jwt.hex \
  --http \
  --http-address=0.0.0.0 \
  --http-port=5052 \
  --metrics \
  --metrics-address=0.0.0.0 \
  --metrics-port=8008
{{- end -}}

{{- define "ethereum-node.tekuRunCommand" -}}
set -eu
bootnodes="$(paste -sd, {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.consensusBootnodesText | quote }})"
test -n "$bootnodes"
exec /opt/teku/bin/teku \
  {{ printf "--network=/network/files/%s" .Values.networkProfile.artifactBundle.files.consensusConfig | quote }} \
  {{ printf "--checkpoint-sync-url=%s" .Values.networkProfile.checkpointSync.primaryUrl | quote }} \
  --p2p-discovery-bootnodes="$bootnodes" \
  --data-path=/data \
  --ee-endpoint=http://127.0.0.1:8551 \
  --ee-jwt-secret-file=/jwt/jwt.hex \
  --rest-api-enabled=true \
  --rest-api-interface=0.0.0.0 \
  --rest-api-port=5052 \
  --metrics-enabled=true \
  --metrics-interface=0.0.0.0 \
  --metrics-port=8008 \
  --metrics-host-allowlist=*
{{- end -}}

{{/*
Geth-specific adapters. These are the exact shell scripts previously inlined
in templates/node.yaml — extracted verbatim so this refactor is a pure
extraction and the rendered chart is byte-identical to the pre-refactor
output. Future EL adapters (reth, nethermind, besu, erigon) add their own
helpers alongside these and register above.
*/}}
{{- define "ethereum-node.gethInitCommand" -}}
set -eu
marker=/data/.platform-network-identity
expected={{ .Values.networkProfile.identityFingerprint | quote }}
test "$(cat /network/.verified-identity)" = "$expected"
if [ -f "$marker" ]; then
  test "$(cat "$marker")" = "$expected" || {
    echo "execution PVC belongs to another network identity" >&2
    exit 1
  }
  test -d /data/{{ include "ethereum-node.executionDataDirMarker" . }} || {
    echo "execution PVC marker exists without initialized Geth data" >&2
    exit 1
  }
  exit 0
fi
for entry in /data/.[!.]* /data/..?* /data/*; do
  [ -e "$entry" ] || continue
  [ "$entry" = /data/lost+found ] && continue
  echo "refusing to initialize an unmarked non-empty execution PVC" >&2
  exit 1
done
geth init --datadir=/data {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionGenesis | quote }}
printf '%s\n' "$expected" > "$marker"
{{- end -}}

{{/*
Reth-specific adapters. Reth's genesis format is Geth-compatible so the same
digest-verified genesis.json bootstraps both clients; init writes state under
/data/db/ (not /data/geth/). Reth accepts the same comma-delimited bootnode
enode list as Geth. Metrics bind to :6060 to keep the chart's PodMonitor and
PrometheusRule contract identical across EL adapters — the exposed metric
names differ (recording-rule work tracked in #86), but the port and scrape
shape do not. Reth has no `--syncmode` flag; its pipelined-staged sync is
always full-derivation, so nothing analogous to Geth's syncMode belongs here.
*/}}
{{- define "ethereum-node.rethInitCommand" -}}
set -eu
marker=/data/.platform-network-identity
expected={{ .Values.networkProfile.identityFingerprint | quote }}
test "$(cat /network/.verified-identity)" = "$expected"
if [ -f "$marker" ]; then
  test "$(cat "$marker")" = "$expected" || {
    echo "execution PVC belongs to another network identity" >&2
    exit 1
  }
  test -d /data/{{ include "ethereum-node.executionDataDirMarker" . }} || {
    echo "execution PVC marker exists without initialized Reth data" >&2
    exit 1
  }
  exit 0
fi
for entry in /data/.[!.]* /data/..?* /data/*; do
  [ -e "$entry" ] || continue
  [ "$entry" = /data/lost+found ] && continue
  echo "refusing to initialize an unmarked non-empty execution PVC" >&2
  exit 1
done
reth init \
  --chain {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionGenesis | quote }} \
  --datadir /data \
  --log.file.max-files 0
printf '%s\n' "$expected" > "$marker"
{{- end -}}

{{- define "ethereum-node.rethRunCommand" -}}
set -eu
bootnodes="$(tr '\n' ',' < {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionBootnodes }})"
bootnodes="${bootnodes%,}"
test -n "$bootnodes"
exec reth node \
  --chain {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionGenesis | quote }} \
  --datadir /data \
  --log.file.max-files 0 \
  --bootnodes "$bootnodes" \
  --http \
  --http.addr 0.0.0.0 \
  --http.port 8545 \
  --http.api eth,net,web3 \
  --authrpc.addr 0.0.0.0 \
  --authrpc.port 8551 \
  --authrpc.jwtsecret /jwt/jwt.hex \
  --metrics 0.0.0.0:6060
{{- end -}}

{{- define "ethereum-node.gethRunCommand" -}}
{{- $execution := index .Values.executionClients .Values.executionClient -}}
set -eu
bootnodes="$(tr '\n' ',' < {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionBootnodes }})"
bootnodes="${bootnodes%,}"
test -n "$bootnodes"
exec geth \
  --networkid={{ printf "%.0f" .Values.networkProfile.identity.executionNetworkId }} \
  --bootnodes="$bootnodes" \
  --syncmode={{ $execution.syncMode }} \
  --datadir=/data \
  --http \
  --http.addr=0.0.0.0 \
  --http.api=eth,net,web3 \
  --authrpc.addr=0.0.0.0 \
  --authrpc.port=8551 \
  --authrpc.vhosts='*' \
  --authrpc.jwtsecret=/jwt/jwt.hex \
  --metrics \
  --metrics.addr=0.0.0.0 \
  --metrics.port=6060
{{- end -}}

{{/*
Erigon-specific adapters. Erigon 3.x accepts the same Geth-compatible
genesis.json (digest-verified from the artifact bundle) via `erigon init`.
Its persistent state lives under /data/chaindata/ (the marker); `erigon init`
creates that directory. Erigon uses a staged-sync pipeline architecturally
different from both Geth's snap and Reth's staged pipeline. Metrics bind to
:6060 for the same PodMonitor/PrometheusRule contract the other EL adapters
follow; series names differ (recording-rule runtime verification is the same
loop PR #96/#124 established).
*/}}
{{- define "ethereum-node.erigonInitCommand" -}}
set -eu
marker=/data/.platform-network-identity
expected={{ .Values.networkProfile.identityFingerprint | quote }}
test "$(cat /network/.verified-identity)" = "$expected"
if [ -f "$marker" ]; then
  test "$(cat "$marker")" = "$expected" || {
    echo "execution PVC belongs to another network identity" >&2
    exit 1
  }
  test -d /data/{{ include "ethereum-node.executionDataDirMarker" . }} || {
    echo "execution PVC marker exists without initialized Erigon data" >&2
    exit 1
  }
  exit 0
fi
for entry in /data/.[!.]* /data/..?* /data/*; do
  [ -e "$entry" ] || continue
  [ "$entry" = /data/lost+found ] && continue
  echo "refusing to initialize an unmarked non-empty execution PVC" >&2
  exit 1
done
erigon init \
  --datadir=/data \
  {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionGenesis | quote }}
printf '%s\n' "$expected" > "$marker"
{{- end -}}

{{- define "ethereum-node.erigonRunCommand" -}}
set -eu
bootnodes="$(tr '\n' ',' < {{ printf "/network/files/%s" .Values.networkProfile.artifactBundle.files.executionBootnodes }})"
bootnodes="${bootnodes%,}"
test -n "$bootnodes"
exec erigon \
  --datadir=/data \
  --networkid={{ printf "%.0f" .Values.networkProfile.identity.executionNetworkId }} \
  --bootnodes="$bootnodes" \
  --http \
  --http.addr=0.0.0.0 \
  --http.api=eth,net,web3 \
  --authrpc.addr=0.0.0.0 \
  --authrpc.port=8551 \
  --authrpc.vhosts='*' \
  --authrpc.jwtsecret=/jwt/jwt.hex \
  --metrics \
  --metrics.addr=0.0.0.0 \
  --metrics.port=6060
{{- end -}}

{{- define "ethereum-node.metricRelabelings" -}}
- action: replace
  replacement: ethereum-validator
  targetLabel: platform
- action: replace
  replacement: {{ .component }}
  targetLabel: component
- action: replace
  replacement: {{ required "telemetry.cluster must be set for every environment" .root.Values.telemetry.cluster }}
  targetLabel: cluster
- action: replace
  replacement: {{ required "telemetry.environment must be set for every environment" .root.Values.telemetry.environment }}
  targetLabel: environment
- action: replace
  replacement: {{ .root.Values.lifecycleState }}
  targetLabel: lifecycle_state
- action: replace
  replacement: {{ .root.Values.networkProfile.family }}
  targetLabel: network
- action: replace
  replacement: {{ .root.Values.networkProfile.name }}
  targetLabel: network_profile
- action: replace
  replacement: {{ .root.Values.networkProfile.generation | quote }}
  targetLabel: network_generation
- action: replace
  replacement: {{ .root.Values.networkProfile.identityFingerprint }}
  targetLabel: network_identity
- action: replace
  replacement: {{ .root.Values.executionClient }}
  targetLabel: execution_client
- action: replace
  replacement: {{ .root.Values.consensusClient }}
  targetLabel: consensus_client
- action: replace
  replacement: {{ .root.Values.identity.customerId }}
  targetLabel: customer_id
- action: replace
  replacement: {{ .root.Values.identity.validatorId }}
  targetLabel: validator_id
- action: replace
  replacement: {{ .root.Values.identity.assignmentId }}
  targetLabel: assignment_id
{{- end -}}
