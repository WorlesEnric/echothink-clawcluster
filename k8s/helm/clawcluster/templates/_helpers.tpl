{{/*
Common labels
*/}}
{{- define "clawcluster.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: clawcluster
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{ include "clawcluster.selectorLabels" . }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "clawcluster.selectorLabels" -}}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create a fully qualified name.
We truncate at 63 chars because some Kubernetes name fields are limited.
*/}}
{{- define "clawcluster.fullname" -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end }}

{{/*
Component-level labels helper.
Usage: {{ include "clawcluster.componentLabels" (dict "ctx" . "component" "postgres") }}
*/}}
{{- define "clawcluster.componentLabels" -}}
{{ include "clawcluster.labels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component-level selector labels helper.
*/}}
{{- define "clawcluster.componentSelectorLabels" -}}
{{ include "clawcluster.selectorLabels" .ctx }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Namespace helper
*/}}
{{- define "clawcluster.namespace" -}}
{{ .Values.global.namespace | default "clawcluster" }}
{{- end }}

{{/*
Image helper – combines image and imagePullPolicy
Usage: {{ include "clawcluster.image" (dict "image" .Values.postgres.image "policy" .Values.global.imagePullPolicy) }}
*/}}
{{- define "clawcluster.image" -}}
image: {{ .image }}
imagePullPolicy: {{ .policy | default "IfNotPresent" }}
{{- end }}

{{/*
Storage class helper
*/}}
{{- define "clawcluster.storageClass" -}}
{{- if .Values.global.storageClass -}}
storageClassName: {{ .Values.global.storageClass }}
{{- end -}}
{{- end }}
