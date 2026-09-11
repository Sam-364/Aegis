{{/*
Expand the name of the chart.
*/}}
{{- define "aegis.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name (release-name + chart-name, deduplicated).
*/}}
{{- define "aegis.fullname" -}}
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
Chart label value.
*/}}
{{- define "aegis.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Name of a component resource: <fullname>-<component>.
Usage: include "aegis.componentName" (dict "root" $ "component" "api")
*/}}
{{- define "aegis.componentName" -}}
{{- printf "%s-%s" (include "aegis.fullname" .root) .component | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Selector labels for a component.
Usage: include "aegis.selectorLabels" (dict "root" $ "component" "api")
*/}}
{{- define "aegis.selectorLabels" -}}
app.kubernetes.io/name: {{ include "aegis.name" .root }}
app.kubernetes.io/instance: {{ .root.Release.Name }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Full label set for a component.
Usage: include "aegis.labels" (dict "root" $ "component" "api")
*/}}
{{- define "aegis.labels" -}}
helm.sh/chart: {{ include "aegis.chart" .root }}
{{ include "aegis.selectorLabels" . }}
app.kubernetes.io/version: {{ .root.Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .root.Release.Service }}
app.kubernetes.io/part-of: aegis
{{- end }}

{{/*
Images.
*/}}
{{- define "aegis.image" -}}
{{- printf "%s:%s" .Values.image.repository (default .Chart.AppVersion .Values.image.tag) }}
{{- end }}

{{- define "aegis.webImage" -}}
{{- printf "%s:%s" .Values.web.image.repository (default .Chart.AppVersion .Values.web.image.tag) }}
{{- end }}

{{/*
ServiceAccount name.
*/}}
{{- define "aegis.serviceAccountName" -}}
{{- if .Values.serviceAccount.create }}
{{- default (include "aegis.fullname" .) .Values.serviceAccount.name }}
{{- else }}
{{- default "default" .Values.serviceAccount.name }}
{{- end }}
{{- end }}

{{/*
Names of the ConfigMaps / Secret consumed by the workloads.
*/}}
{{- define "aegis.configMapName" -}}
{{- printf "%s-config" (include "aegis.fullname" .) }}
{{- end }}

{{- define "aegis.webConfigMapName" -}}
{{- printf "%s-web-config" (include "aegis.fullname" .) }}
{{- end }}

{{- define "aegis.secretName" -}}
{{- default (printf "%s-secrets" (include "aegis.fullname" .)) .Values.secrets.existingSecret }}
{{- end }}

{{/*
Whether the chart itself renders the application Secret.
*/}}
{{- define "aegis.createSecret" -}}
{{- if and (not .Values.secrets.existingSecret) (or .Values.secrets.databaseUrl .Values.secrets.redisUrl .Values.secrets.llmApiKey .Values.secrets.apiKeys) }}true{{ end }}
{{- end }}

{{/*
Effective service addresses (in-release dev infra wins over configured values).
*/}}
{{- define "aegis.simulatorUrl" -}}
{{- if .Values.simulator.enabled }}
{{- printf "http://%s:%d" (include "aegis.componentName" (dict "root" . "component" "simulator")) (int .Values.simulator.service.port) }}
{{- else }}
{{- .Values.config.simulatorUrl }}
{{- end }}
{{- end }}

{{- define "aegis.temporalAddress" -}}
{{- if .Values.devInfra.enabled }}
{{- printf "%s:7233" (include "aegis.componentName" (dict "root" . "component" "temporal")) }}
{{- else }}
{{- .Values.config.temporal.address }}
{{- end }}
{{- end }}

{{- define "aegis.apiUrl" -}}
{{- printf "http://%s:%d" (include "aegis.componentName" (dict "root" . "component" "api")) (int .Values.api.service.port) }}
{{- end }}

{{/*
Env entries that pull values from the application Secret. The LLM key and the
API keys are optional so pods can start when the provider is disabled / auth
is off.
Usage: include "aegis.secretEnv" (dict "root" $ "keys" (list "AEGIS_DATABASE_URL" "AEGIS_REDIS_URL"))
*/}}
{{- define "aegis.secretEnv" -}}
{{- $secret := include "aegis.secretName" .root -}}
{{- $out := list -}}
{{- range .keys -}}
{{- $ref := dict "name" $secret "key" . -}}
{{- if has . (list "AEGIS_LLM_API_KEY" "AEGIS_API_KEYS") -}}
{{- $_ := set $ref "optional" true -}}
{{- end -}}
{{- $out = append $out (dict "name" . "valueFrom" (dict "secretKeyRef" $ref)) -}}
{{- end -}}
{{- toYaml $out }}
{{- end }}

{{/*
Default pod anti-affinity for a component, used when .affinity is empty.
Usage: include "aegis.defaultAffinity" (dict "root" $ "component" "api")
*/}}
{{- define "aegis.defaultAffinity" -}}
podAntiAffinity:
  preferredDuringSchedulingIgnoredDuringExecution:
    - weight: 100
      podAffinityTerm:
        topologyKey: kubernetes.io/hostname
        labelSelector:
          matchLabels:
            {{- include "aegis.selectorLabels" . | nindent 12 }}
{{- end }}

{{/*
Checksum of the rendered ConfigMap so Deployments roll on config changes.
*/}}
{{- define "aegis.configChecksum" -}}
{{- include (print .Template.BasePath "/configmap.yaml") . | sha256sum }}
{{- end }}
