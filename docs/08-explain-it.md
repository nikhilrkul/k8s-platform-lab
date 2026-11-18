# 8. Explain it to anyone

## The 30-second version (for a non-engineer)

> "Developers propose changes. A robot checks every change for bugs, security holes and risky
> settings before it can be accepted. Once accepted, the change is packaged and written into a
> shared logbook of how the system *should* look. A second robot inside our servers keeps reading
> that logbook and makes reality match it. If anything drifts, it fixes it. If a change goes
> wrong, we undo the logbook entry and the system follows. Between our services there is a
> security guard and traffic controller that encrypts every conversation, checks who is allowed
> to talk to whom, and lets us test a new version on 10% of the traffic first."

## The 2-minute version (for an engineer or an interviewer)

> "It's a GitOps platform on Kubernetes. On every PR, GitHub Actions runs linting and type
> checks, unit tests with an 80% coverage gate on **changed lines** (diff-cover), diff-aware
> SAST with Semgrep, secret scanning, dependency scans, and a deploy review. The deploy review
> renders every Helm release for every environment and validates it with a JSON schema,
> kubeconform, OPA policies and a custom checker for nulls, lossy YAML and broken cross-object
> references. Images are built, Trivy-scanned (any fixable CVE fails the build), SBOM'd and
> cosign-signed.
>
> CI never touches the cluster. Its last step commits the new image tag to the env's values file.
> ArgoCD, with an App-of-Apps and an ApplicationSet, pulls from git and renders the shared Helm
> chart. Dev auto-syncs with self-heal; prod is promoted by PR and synced by hand. Secrets are
> Sealed Secrets, so git stays safe.
>
> At runtime, Istio sidecars give us STRICT mTLS with SPIFFE identities, default-deny
> AuthorizationPolicies, timeouts, retries, outlier detection, weighted canaries and fault
> injection, plus golden-signal metrics and traces in Kiali, Grafana and Jaeger, with no code changes."

## Whiteboard order

Draw it in this order. Each step builds on the one before, and you can stop at any point:

1. Two boxes, **orders → inventory**, inside a **Kubernetes** box. Pods, Deployment, Service.
2. **Helm**: one chart + values-dev / values-prod → manifests.
3. **Git** on the left. **ArgoCD** inside the cluster, with an arrow *from* the cluster *to* git (pull).
4. **CI** between the developer and git: the gates, then the image to the registry and the tag commit.
5. **Istio**: a small Envoy box beside each pod, istiod above, the ingress gateway at the edge, a padlock on the arrow.
6. Observability at the bottom: Prometheus, Kiali, Jaeger.

## Interview questions you should now be able to answer

**Kubernetes**
1. What happens, step by step, from `kubectl apply` of a Deployment to a running container? (API server → etcd → controller creates ReplicaSet → scheduler binds pod → kubelet pulls and starts → probes → endpoints)
2. Liveness vs readiness vs startup probes. What goes wrong if liveness checks a dependency?
3. Why are a Deployment's selector labels immutable, and how does that shape chart design?
4. Requests vs limits. Why no CPU limit here? What happens when a pod exceeds its memory limit?
5. How does a rolling update with `maxSurge: 1, maxUnavailable: 0` keep full capacity?
6. What does a PodDisruptionBudget protect against, and how can a bad one block a cluster upgrade?
7. Why does a ConfigMap change not restart pods by itself, and how does the checksum annotation fix that?

**Helm**
8. Chart vs release vs revision. Where does Helm store release state?
9. How does `helm rollback` work, and why is it a new revision?
10. When would you use `required`, `fail` or `values.schema.json`?
11. Why doesn't `helm list` show apps deployed by ArgoCD?

**CI/CD and security**
12. Total coverage vs diff coverage: which would you gate on in a legacy codebase, and why?
13. SAST vs SCA vs image scanning vs DAST. Which one finds a CVE in `urllib3`?
14. Why scan before pushing the image? Why tag by git SHA?
15. What do cosign keyless signing and an SBOM give you? How would the cluster *enforce* signatures? (an admission policy: Kyverno, Sigstore policy-controller)
16. How do you handle a CVE that has no fix yet?

**GitOps / ArgoCD**
17. Push vs pull deployment. Why is pull safer?
18. What do `prune` and `selfHeal` do? Why might prod have neither?
19. App of Apps vs ApplicationSet.
20. How do you roll back in GitOps? What does the ArgoCD UI rollback do to auto-sync?
21. How do you keep secrets in a GitOps repo?

**Istio**
22. How does a sidecar intercept traffic without app changes?
23. VirtualService vs DestinationRule.
24. How does mTLS work in the mesh, and what identity does a workload have?
25. Walk through a request that gets `403 RBAC: access denied`, and how you would debug it.
26. How is a 10% canary different from scaling to 10 pods with 1 on the new version?
27. Retries: when do they help, when do they hurt, and why didn't they mask injected faults?
28. Sidecar vs ambient mode trade-offs.

## Common misconceptions to correct when explaining

* "Secrets are encrypted in Kubernetes." They are base64 by default. Encryption at rest is a cluster setting.
* "ArgoCD deploys with Helm." It *renders* with Helm and applies the manifests itself.
* "Istio replaces Kubernetes Services." It doesn't. It still uses Services for discovery and names.
* "100% coverage means no bugs." Coverage shows code that *ran*, not code that was *checked*.
* "Rollback means `helm rollback`." In GitOps, rollback means git revert. Anything else drifts.
