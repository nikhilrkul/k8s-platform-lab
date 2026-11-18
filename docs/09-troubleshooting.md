# 9. Troubleshooting

## The debugging ladder

Go top to bottom. Most problems show up by step 3.

```bash
argocd app get orders-dev                        # 1. Synced? Healthy? Which resource is unhappy?
kubectl -n shop-dev get pods                     # 2. Pending / CrashLoopBackOff / ImagePullBackOff / not Ready?
kubectl -n shop-dev describe pod <pod>           # 3. Events at the bottom tell you why
kubectl -n shop-dev logs <pod> -c app --previous # 4. App logs from the crashed container
kubectl -n shop-dev logs <pod> -c istio-proxy    # 5. Envoy access log: response codes and flags
kubectl -n shop-dev get endpoints orders         # 6. Does the Service have Ready pods behind it?
istioctl analyze -n shop-dev                     # 7. Mesh config mistakes
istioctl proxy-config cluster deploy/orders -n shop-dev   # 8. What Envoy thinks exists
```

## Symptoms and fixes

| Symptom | Likely cause | Fix |
|---|---|---|
| `ImagePullBackOff`, `denied` / `unauthorized` | GHCR package is private | Make the package public, or create a pull secret: `kubectl -n shop-dev create secret docker-registry ghcr --docker-server=ghcr.io --docker-username=<you> --docker-password=<PAT with read:packages>` and set `imagePullSecrets: [{name: ghcr}]` |
| `ImagePullBackOff`, `manifest unknown` | tag is still `bootstrap`, or CI hasn't pushed yet | wait for the `ci` run on main, then `git pull` |
| `exec format error` in logs | amd64 image on the arm64 Minikube | build on the arm64 runner (default here), or multi-arch |
| `CreateContainerConfigError` | missing ConfigMap or Secret key | `kubectl describe pod`; usually `make seal-secrets` wasn't run for this cluster |
| SealedSecret `no key could decrypt secret` | ciphertext made for another cluster (after `make destroy`) | re-run `make seal-secrets`, commit, push |
| `CrashLoopBackOff` with `ConfigError: ... is required` | an env var is missing | add it to the values for that env; the deploy guard should have caught it |
| pod `0/2 Running`, readiness failing | orders `/readyz` can't reach inventory | check inventory pods and the AuthorizationPolicy (403 in its istio-proxy log) |
| `upstream connect error or disconnect/reset before headers` | the destination has no healthy endpoints, or mTLS mismatch | `kubectl get endpoints`; `istioctl x describe pod` |
| `RBAC: access denied` (403) | AuthorizationPolicy | which SA called? `kubectl get pod -o jsonpath='{.spec.serviceAccountName}'`, then compare with the policy |
| `404` via gateway | wrong Host header, or the VS isn't bound to the gateway | `curl -H "Host: dev.shop.local"`; `istioctl proxy-config routes deploy/istio-ingressgateway -n istio-system` |
| `curl 127.0.0.1` hangs | `minikube tunnel` isn't running | run `make tunnel` and keep that terminal open |
| ArgoCD app `OutOfSync` forever on one field | something else changes it (HPA, a mutating webhook) | add `ignoreDifferences` for that field |
| ArgoCD `ComparisonError ... values.schema.json` | invalid values in git | the same error appears in CI's `deploy-review`; fix it in a PR |
| `helm test` pod never completes | an Istio sidecar keeps it alive | the chart calls `/quitquitquit` already; check that the test pod's SA is allowed by the policy |
| Minikube very slow or pods `Pending` with `Insufficient memory` | too little RAM | `minikube delete && MINIKUBE_MEMORY=10240 make up`; or scale prod to 1 replica |
| `dependency-review` fails with "Dependency graph not enabled" | repo setting | Settings > Code security > enable Dependency graph |
| bot push fails with 403 | token permissions | Settings > Actions > General > Read and write permissions |

## Reading Envoy response flags

In the `istio-proxy` access log, the field after the status code is the **response flag**:

| Flag | Meaning |
|---|---|
| `-` | normal |
| `UF` | upstream connection failure |
| `UH` | no healthy upstream (all ejected or none Ready) |
| `URX` | retry limit exceeded |
| `UT` | upstream request timeout (the VirtualService timeout) |
| `DI` | delay injected (fault injection) |
| `FI` | fault injected (abort) |
| `NR` | no route configured (wrong host or port name) |
| `RBAC` / `rbac_access_denied_matched_policy` | denied by an AuthorizationPolicy |

## Reset everything

```bash
kubectl delete -f deploy/argocd/root-app.yaml   # ArgoCD cascades the deletes via finalizers
make destroy && make up
```
