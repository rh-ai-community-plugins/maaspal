{{/*
Target namespace for all namespaced resources.
*/}}
{{- define "maaspal.namespace" -}}
{{- .Values.namespace | default .Release.Namespace }}
{{- end }}

{{/*
Expand the name of the chart.
*/}}
{{- define "maaspal.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Create a default fully qualified app name.
*/}}
{{- define "maaspal.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Create chart name and version as used by the chart label.
*/}}
{{- define "maaspal.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Common labels
*/}}
{{- define "maaspal.labels" -}}
helm.sh/chart: {{ include "maaspal.chart" . }}
{{ include "maaspal.selectorLabels" . }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels
*/}}
{{- define "maaspal.selectorLabels" -}}
app.kubernetes.io/name: {{ include "maaspal.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Create the name of the service account to use
*/}}
{{- define "maaspal.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "maaspal.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Prefix for cluster-scoped objects (ClusterRoles/Bindings), so two installs in
different namespaces never overwrite each other's grants.
*/}}
{{- define "maaspal.clusterPrefix" -}}
{{- printf "%s-%s" (include "maaspal.namespace" .) (include "maaspal.fullname" .) | trunc 50 | trimSuffix "-" }}
{{- end }}

{{/*
BFF image reference — also the image every harness Job runs.
*/}}
{{- define "maaspal.bffImage" -}}
{{- printf "%s:%s" .Values.bff.image.repository (.Values.bff.image.tag | default .Chart.AppVersion) }}
{{- end }}

{{/*
Cluster apps domain, read from the OpenShift ingress config at install time
(empty under `helm template`, where lookup returns nothing).
*/}}
{{- define "maaspal.appsDomain" -}}
{{- $ingress := lookup "config.openshift.io/v1" "Ingress" "" "cluster" }}
{{- if $ingress }}{{ $ingress.spec.domain }}{{ end }}
{{- end }}

{{- define "maaspal.maasApiUrl" -}}
{{- if .Values.maas.apiUrl }}{{ .Values.maas.apiUrl }}
{{- else }}{{- $d := include "maaspal.appsDomain" . }}{{ if $d }}https://maas.{{ $d }}{{ end }}
{{- end }}
{{- end }}

{{- define "maaspal.metricsUrl" -}}
{{- if .Values.maas.metricsUrl }}{{ .Values.maas.metricsUrl }}
{{- else }}{{- $d := include "maaspal.appsDomain" . }}{{ if $d }}https://thanos-querier-openshift-monitoring.{{ $d }}/api/v1/query{{ end }}
{{- end }}
{{- end }}
