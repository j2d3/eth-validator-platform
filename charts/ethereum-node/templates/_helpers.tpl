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
