{{/*
Name of the service. Defaults to the release name, so `helm install orders ...`
gives a Service called "orders" (DNS: orders.<namespace>.svc.cluster.local).
*/}}
{{- define "microservice.fullname" -}}
{{- default .Release.Name .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "microservice.name" -}}
{{- default .Release.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{/* Labels on every object. `app` is what Kiali and Istio dashboards group by. */}}
{{- define "microservice.labels" -}}
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: shop
{{ include "microservice.selectorLabels" . }}
{{- end -}}

{{/*
Selector labels. These are IMMUTABLE on a Deployment once it exists, so they must
never include something that changes (like the image tag or chart version).
*/}}
{{- define "microservice.selectorLabels" -}}
app.kubernetes.io/name: {{ include "microservice.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app: {{ include "microservice.name" . }}
{{- end -}}

{{- define "microservice.serviceAccountName" -}}
{{- if .Values.serviceAccount.create -}}
{{ include "microservice.fullname" . }}
{{- else -}}
default
{{- end -}}
{{- end -}}

{{/* Name of the Secret the pods read, or "" when there is none. */}}
{{- define "microservice.secretName" -}}
{{- if .Values.existingSecretName -}}
{{ .Values.existingSecretName }}
{{- else if or .Values.secret.create .Values.sealedSecret.enabled -}}
{{ include "microservice.fullname" . }}
{{- end -}}
{{- end -}}

{{/*
Pod template shared by the stable and canary Deployments.
Call with: (dict "root" $ "track" "stable" "tag" "abc123")
*/}}
{{- define "microservice.podTemplate" -}}
{{- $root := .root -}}
{{- $v := $root.Values -}}
metadata:
  labels:
    {{- include "microservice.selectorLabels" $root | nindent 4 }}
    version: {{ .track }}
  annotations:
    # Changing the ConfigMap or Secret changes this hash, which changes the pod
    # template, which triggers a rolling restart. Without it, pods keep stale config.
    checksum/config: {{ include (print $root.Template.BasePath "/configmap.yaml") $root | sha256sum }}
    checksum/secret: {{ include (print $root.Template.BasePath "/secret.yaml") $root | sha256sum }}
    {{- if $v.istio.enabled }}
    # Hold the app until the Envoy sidecar is ready, so early outbound calls don't fail.
    proxy.istio.io/config: '{"holdApplicationUntilProxyStarts": true}'
    {{- end }}
spec:
  serviceAccountName: {{ include "microservice.serviceAccountName" $root }}
  terminationGracePeriodSeconds: {{ $v.terminationGracePeriodSeconds }}
  {{- with $v.imagePullSecrets }}
  imagePullSecrets:
    {{- toYaml . | nindent 4 }}
  {{- end }}
  securityContext:
    {{- toYaml $v.podSecurityContext | nindent 4 }}
  containers:
    - name: app
      image: "{{ $v.image.repository }}:{{ .tag }}"
      imagePullPolicy: {{ $v.image.pullPolicy }}
      ports:
        - name: http
          containerPort: {{ $v.containerPort }}
          protocol: TCP
      env:
        - name: APP_VERSION
          value: {{ printf "%s-%s" .track .tag | quote }}
        {{- range $v.env }}
        - name: {{ .name }}
          value: {{ .value | quote }}
        {{- end }}
      envFrom:
        - configMapRef:
            name: {{ include "microservice.fullname" $root }}
        {{- $secretName := include "microservice.secretName" $root }}
        {{- if $secretName }}
        - secretRef:
            name: {{ $secretName }}
        {{- end }}
      livenessProbe:
        httpGet:
          path: {{ $v.probes.liveness.path }}
          port: http
        initialDelaySeconds: {{ $v.probes.liveness.initialDelaySeconds }}
        periodSeconds: {{ $v.probes.liveness.periodSeconds }}
        failureThreshold: {{ $v.probes.liveness.failureThreshold }}
      readinessProbe:
        httpGet:
          path: {{ $v.probes.readiness.path }}
          port: http
        initialDelaySeconds: {{ $v.probes.readiness.initialDelaySeconds }}
        periodSeconds: {{ $v.probes.readiness.periodSeconds }}
        failureThreshold: {{ $v.probes.readiness.failureThreshold }}
      resources:
        {{- toYaml $v.resources | nindent 8 }}
      securityContext:
        {{- toYaml $v.securityContext | nindent 8 }}
      volumeMounts:
        - name: tmp
          mountPath: /tmp
  volumes:
    # readOnlyRootFilesystem is on, so give the app a writable scratch dir.
    - name: tmp
      emptyDir: {}
{{- end -}}
