# Policy-as-code with OPA/Conftest. Runs in CI against every rendered manifest.
# `deny` fails the build; `warn` is reported but does not fail.
# Unit tests for these rules are in kubernetes_test.rego (`conftest verify`).
package main

workloads := {"Deployment", "StatefulSet", "DaemonSet", "Job"}

allowed_registries := ["ghcr.io/", "curlimages/"]

containers contains c if {
	input.kind in workloads
	some c in input.spec.template.spec.containers
}

containers contains c if {
	input.kind == "Pod"
	some c in input.spec.containers
}

pod_spec := input.spec.template.spec if input.kind in workloads

pod_spec := input.spec if input.kind == "Pod"

name := sprintf("%s/%s", [input.kind, input.metadata.name])

deny contains msg if {
	some c in containers
	c.securityContext.privileged == true
	msg := sprintf("%s: container '%s' is privileged", [name, c.name])
}

deny contains msg if {
	some c in containers
	not c.securityContext.allowPrivilegeEscalation == false
	msg := sprintf("%s: container '%s' must set allowPrivilegeEscalation: false", [name, c.name])
}

deny contains msg if {
	input.kind in workloads
	not pod_spec.securityContext.runAsNonRoot == true
	msg := sprintf("%s: pod must set securityContext.runAsNonRoot: true", [name])
}

deny contains msg if {
	pod_spec.hostNetwork == true
	msg := sprintf("%s: hostNetwork is not allowed", [name])
}

deny contains msg if {
	some v in pod_spec.volumes
	v.hostPath
	msg := sprintf("%s: hostPath volume '%s' is not allowed", [name, v.name])
}

deny contains msg if {
	some c in containers
	not startswith_any(c.image, allowed_registries)
	msg := sprintf("%s: image '%s' is not from an allowed registry %v", [name, c.image, allowed_registries])
}

deny contains msg if {
	some c in containers
	endswith(c.image, ":latest")
	msg := sprintf("%s: image '%s' uses the mutable :latest tag", [name, c.image])
}

deny contains msg if {
	input.kind == "Service"
	input.spec.type in {"LoadBalancer", "NodePort"}
	msg := sprintf("%s: type %s bypasses the Istio ingress gateway; use ClusterIP", [name, input.spec.type])
}

deny contains msg if {
	input.kind == "Namespace"
	startswith(input.metadata.name, "shop-")
	not input.metadata.labels["istio-injection"] == "enabled"
	msg := sprintf("%s: app namespaces must have istio-injection=enabled", [name])
}

deny contains msg if {
	input.kind == "PeerAuthentication"
	input.spec.mtls.mode in {"PERMISSIVE", "DISABLE"}
	msg := sprintf("%s: mTLS mode %s allows plaintext traffic; use STRICT", [name, input.spec.mtls.mode])
}

# `rules: [{}]` in an ALLOW policy matches every request: it allows everyone.
deny contains msg if {
	input.kind == "AuthorizationPolicy"
	object.get(input.spec, "action", "ALLOW") == "ALLOW"
	some rule in input.spec.rules
	count(rule) == 0
	msg := sprintf("%s: an empty rule allows all traffic", [name])
}

warn contains msg if {
	some c in containers
	not c.securityContext.readOnlyRootFilesystem == true
	msg := sprintf("%s: container '%s' should use readOnlyRootFilesystem: true", [name, c.name])
}

startswith_any(s, prefixes) if {
	some p in prefixes
	startswith(s, p)
}
