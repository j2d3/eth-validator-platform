{{- define "ethereum-node.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "ethereum-node.identityLabels" -}}
platform.galaxy-lab/network: {{ .Values.network }}
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

{{- define "ethereum-node.metricRelabelings" -}}
- action: replace
  replacement: ethereum-validator
  targetLabel: platform
- action: replace
  replacement: {{ .component }}
  targetLabel: component
- action: replace
  replacement: {{ .root.Values.network }}
  targetLabel: network
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
