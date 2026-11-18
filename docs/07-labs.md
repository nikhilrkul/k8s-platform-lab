# 7. Hands-on labs: break it on purpose

Do them in order. Each lab says what to change, what to watch, and what you should be able to
explain afterwards. Labs A (Helm) and B (GitOps) are in docs 04 and 05.

Set these once:

```bash
export KEY=<dev api key>
alias shop='curl -s -H "Host: dev.shop.local" -H "X-API-Key: $KEY" -H "Content-Type: application/json"'
```

---

## Lab C: a full change through the pipeline

1. On a branch, add a `GET /orders` endpoint to `services/orders/app/main.py` that lists all orders.
2. Open a PR **without a test**. `test-python` fails on diff coverage. Read the job summary.
3. Add the test, push, and watch everything go green.
4. Merge. Follow the run on main: image push, signature, bot commit. Then ArgoCD syncs `orders-dev`.
5. `shop http://127.0.0.1/orders`
6. Promote to prod with the workflow, merge, sync.

**Explain:** why the image tag is a git SHA and not `latest`; what would happen if CI did
`kubectl apply` instead; how you would find out which commit is running in prod.

## Lab D: canary release with Istio

1. Change inventory visibly, e.g. add `"version": s.Version` to the stock JSON. Merge it, and note the
   new SHA from the bot commit (`git log -1 deploy/values`).
2. Revert dev's `image.tag` to the previous SHA, and set the canary in `values-dev.yaml`:

   ```yaml
   istio:
     canary: {enabled: true, imageTag: "<new sha>", weight: 10, replicaCount: 1}
   ```
3. After ArgoCD syncs, measure the split from inside orders:

   ```bash
   kubectl -n shop-dev exec deploy/orders -c app -- python -c "
   import httpx, collections
   c = collections.Counter(httpx.get('http://inventory/stock/BOOK-1').headers['x-inventory-version'] for _ in range(200))
   print(c)"
   ```
   Expect about 180 `stable-...` and 20 `canary-...`.
4. Open Kiali's graph with "Traffic distribution" on. Raise the weight to 50, then 100.
5. Promote: set `image.tag` to the new SHA and `canary.enabled: false`.

**Explain:** why the Service selector has no `version` label; the difference between a weight split
and a replica split; what Argo Rollouts would add (automatic metric analysis and rollback).

## Lab E: resilience: retries, timeouts, circuit breaking

1. Enable an abort on inventory: `faultInjection: {enabled: true, abort: {percentage: 30, httpStatus: 503}}`.
2. Create 50 orders. About 30% fail, and orders turns inventory's 503 into a clean 502.
   Notice that the retries did **not** hide the aborts. Envoy injects faults in a filter that runs
   *before* its router, and the router is where retries happen. Injected faults test the
   *caller's* error handling, not the mesh's retries.
3. To see retries for real, remove the fault, run `hey` against orders, and delete an inventory
   pod mid-run (`kubectl -n shop-dev delete pod -l app=inventory --wait=false`). Connection resets
   are retried (`retryOn: connect-failure,reset`), so orders sees few or no errors. Count them:
   `kubectl -n shop-dev exec deploy/orders -c istio-proxy -- pilot-agent request GET stats | grep upstream_rq_retry`
4. Inject `delay: {percentage: 100, fixedDelay: 4s}`. orders' own client timeout
   (`REQUEST_TIMEOUT_MS: 2000`) fires first, so callers get a 502 after about 2s. Which timeout
   should win, the app's or the mesh's, and why? (Hint: each caller up the chain needs a larger
   budget than the one below it, or its retries can never happen.)
5. Remove the faults.

**Explain:** retry storms; why a timeout must be shorter than the caller's timeout; what outlier
detection does that retries don't.

## Lab F: zero-trust security

1. Delete inventory's ALLOW rule for orders: `allowedServiceAccounts: []`. The deploy guard passes
   (it's valid), ArgoCD syncs, and orders' readiness probe starts failing, because `/readyz` calls
   inventory and gets 403. orders pods leave the Service's endpoints.
2. `kubectl -n shop-dev logs deploy/inventory -c istio-proxy | grep 403` shows `rbac_access_denied`.
3. Restore it. Now set `allowedServiceAccounts: ["order"]` (typo) in a PR. The guard blocks it.
4. Change the mesh PeerAuthentication to `PERMISSIVE` in a PR. Conftest blocks it.

**Explain:** identity-based vs IP-based security (NetworkPolicy); why readiness should or shouldn't
depend on downstream services (there is a real trade-off: cascading unavailability).

## Lab G: the deploy guard catches what kubectl wouldn't

Make each change in a PR and read the `deploy-review` output:

| Change | Why it would hurt in production |
|---|---|
| `INVENTORY_URL` removed from `values-prod.yaml` | prod pods CrashLoop on start; dev looks fine |
| `pdb.minAvailable: 2` in prod (HPA min 2) | the next node drain or cluster upgrade hangs forever |
| `service.portName: web` | the schema rejects it. Istio reads the protocol from the port name, and an unknown prefix means plain TCP: no HTTP routing, retries or metrics |
| `SEED_STOCK: ` (empty) | inventory crashes on start (and the guard flags the null) |
| `config: {MAX_ITEMS: 1000000}` | schema: must be a string. If quoted badly, it renders as `1e+06` |
| delete `secrets-prod.yaml` | `CreateContainerConfigError`: the Secret it references doesn't exist |

## Lab H: scaling and disruption

```bash
kubectl -n shop-prod get hpa -w
hey -z 120s -c 20 -m POST -H "Host: shop.local" -H "X-API-Key: <prod key>" \
  -H "Content-Type: application/json" -d '{"sku":"PEN-2","quantity":1}' http://127.0.0.1/orders
```

Watch the HPA scale orders from 2 toward 4. Then `kubectl drain minikube --ignore-daemonsets --delete-emptydir-data --dry-run=server`:
the PDB is what keeps a real drain from taking everything down at once. (On a one-node cluster,
an actual drain would evict everything, so stay with the dry run, or run `minikube node add` for a second node.)

## Lab I: graceful shutdown

1. `kubectl -n shop-dev rollout restart deploy/inventory` while `hey` runs against orders.
2. There should be zero errors. Why? Old pods get SIGTERM, the Go server stops accepting new
   connections and drains the in-flight ones (`srv.Shutdown`), endpoints are removed, and
   `maxUnavailable: 0` means new pods are Ready before old ones go.
3. Stretch: add a `preStop: sleep 5` hook and explain why big clusters need it (endpoint removal is
   eventually consistent across nodes).

## Lab J: disaster recovery

1. `make destroy && make up`. The cluster is empty.
2. `make seal-secrets`, commit, push (the new cluster has a new key, so old ciphertexts are useless).
3. `make gitops`. Everything comes back from git in minutes.

**Explain:** what is the source of truth; what was lost (in-memory orders: this is why services
need real databases, and how StatefulSets and PersistentVolumes fit in); how backing up the
Sealed Secrets key would have avoided step 2.
