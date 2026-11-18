package main

good_deploy := {
	"kind": "Deployment",
	"metadata": {"name": "orders"},
	"spec": {"template": {"spec": {
		"securityContext": {"runAsNonRoot": true},
		"containers": [{
			"name": "app",
			"image": "ghcr.io/me/orders:abc123",
			"securityContext": {"allowPrivilegeEscalation": false, "readOnlyRootFilesystem": true},
		}],
	}}},
}

test_good_deployment_passes if {
	count(deny) == 0 with input as good_deploy
	count(warn) == 0 with input as good_deploy
}

test_privileged_denied if {
	d := json.patch(good_deploy, [{"op": "add", "path": "/spec/template/spec/containers/0/securityContext/privileged", "value": true}])
	count(deny) > 0 with input as d
}

test_latest_and_registry_denied if {
	d := json.patch(good_deploy, [{"op": "replace", "path": "/spec/template/spec/containers/0/image", "value": "docker.io/nginx:latest"}])
	count(deny) == 2 with input as d
}

test_root_denied if {
	d := json.patch(good_deploy, [{"op": "remove", "path": "/spec/template/spec/securityContext/runAsNonRoot"}])
	count(deny) == 1 with input as d
}

test_loadbalancer_denied if {
	count(deny) == 1 with input as {"kind": "Service", "metadata": {"name": "x"}, "spec": {"type": "LoadBalancer"}}
}

test_namespace_needs_injection if {
	count(deny) == 1 with input as {"kind": "Namespace", "metadata": {"name": "shop-dev", "labels": {}}}
	count(deny) == 0 with input as {"kind": "Namespace", "metadata": {"name": "shop-dev", "labels": {"istio-injection": "enabled"}}}
}

test_permissive_mtls_denied if {
	count(deny) == 1 with input as {"kind": "PeerAuthentication", "metadata": {"name": "default"}, "spec": {"mtls": {"mode": "PERMISSIVE"}}}
}

test_allow_all_authz_denied if {
	count(deny) == 1 with input as {"kind": "AuthorizationPolicy", "metadata": {"name": "x"}, "spec": {"rules": [{}]}}
	count(deny) == 0 with input as {"kind": "AuthorizationPolicy", "metadata": {"name": "deny-all"}, "spec": {}}
}
