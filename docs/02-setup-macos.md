# 2. Setup on an Apple Silicon Mac

Budget about 45 minutes the first time. You need about 10 GB of free RAM for Docker.

## 2.1 Prerequisites

1. **Docker Desktop**: in Settings > Resources, give it **6 CPUs and 10 GB of memory** (at least
   4 CPUs / 8 GB). Minikube runs as a container inside it.
2. **Homebrew**, then from the repo root: `make tools`.
3. A **GitHub account**. Make the repository **public**. This keeps everything free:
   arm64 runners, dependency-review, and anonymous image pulls from GHCR.

Check the tools:

```bash
minikube version && kubectl version --client && helm version && istioctl version --remote=false \
  && argocd version --client && kubeseal --version
```

## 2.2 Create the GitHub repo

```bash
cd k8s-platform-lab
make init GH_USER=<your-github-username>    # replaces nikhilrkul placeholders
git init -b main && git add -A && git commit -m "initial import"
gh repo create k8s-platform-lab --public --source . --push    # or create it in the UI and push
```

Then in the repo's **Settings**:

* **Actions > General > Workflow permissions**: "Read and write permissions", and tick
  "Allow GitHub Actions to create and approve pull requests" (the bump commit and the
  promotion PR need these).
* **Advanced Security / Code security**: enable the **Dependency graph** (dependency-review needs it).

Don't add branch protection yet: the first push goes straight to main.

## 2.3 Build the cluster

```bash
make up
```

What happens (read `scripts/bootstrap.sh` along the way):

1. `minikube start --driver=docker --cpus=4 --memory=8192 --addons=metrics-server`.
   Override the sizes with `MINIKUBE_CPUS=6 MINIKUBE_MEMORY=10240 make up`.
2. `istioctl install -f bootstrap/istio-config.yaml`: istiod (the control plane) plus the
   ingress gateway, with access logs, tracing to Jaeger, and `REGISTRY_ONLY` egress.
3. The Prometheus, Grafana, Kiali and Jaeger add-ons.
4. The Sealed Secrets controller (in `kube-system`).
5. ArgoCD from its Helm chart, tuned for a laptop. The script prints the admin password.

Check it worked:

```bash
kubectl get pods -A          # everything should be Running
istioctl verify-install
```

## 2.4 Secrets, then the first pipeline run

```bash
make seal-secrets
```

This generates a random API key per environment, encrypts it with **this cluster's** public key,
and writes `deploy/values/orders/secrets-{dev,prod}.yaml`. **Write down the keys it prints.**

```bash
git add -A && git commit -m "add sealed secrets" && git push
```

Watch **Actions > ci**. On a push to main, all gates run, then `build-scan` pushes
`ghcr.io/<you>/k8s-platform-lab/{orders,inventory}:<sha>`, and `gitops-bump-dev` commits the new
tags into `values-dev.yaml`.

Then make the images pullable: on your GitHub profile, open **Packages > orders > Package settings >
Change visibility > Public**, and do the same for `inventory`. (The alternative is to keep them
private and give the cluster an `imagePullSecret`. See troubleshooting.)

Prod still points at the `bootstrap` tag. Run **Actions > promote-to-prod > Run workflow** and
merge the PR it opens.

## 2.5 Hand over to ArgoCD

```bash
git pull                 # get the bot's tag-bump commit
make gitops              # kubectl apply -f deploy/argocd/root-app.yaml
kubectl -n argocd get applications -w
```

You should see `root`, `platform`, `orders-dev`, `inventory-dev` go **Synced / Healthy**.
`orders-prod` and `inventory-prod` stay **OutOfSync** on purpose, because prod syncs by hand:

```bash
make ui-argocd                      # http://localhost:8080, user admin
# or from the CLI:
argocd login localhost:8080 --username admin --insecure
argocd app sync inventory-prod orders-prod
```

## 2.6 Send traffic

```bash
make tunnel                         # terminal 2. Keep it open; it asks for your Mac password.
make hosts                          # adds dev.shop.local / shop.local to /etc/hosts
make smoke API_KEY=<dev key>        # create an order, fetch it, check the 401
make smoke ENV=prod API_KEY=<prod key>
```

Load, so the dashboards have something to show:

```bash
hey -z 60s -c 5 -m POST -H "Host: dev.shop.local" -H "X-API-Key: <dev key>" \
  -H "Content-Type: application/json" -d '{"sku":"PEN-2","quantity":1}' http://127.0.0.1/orders
make ui-kiali                       # Graph > namespace shop-dev > Display: Traffic animation, Security
```

## 2.7 Turn on branch protection

**Settings > Branches > Add rule** for `main`:

* Require a pull request before merging (and CODEOWNERS review, if you like)
* Require status checks to pass. Pick: `lint`, `test-python`, `test-go`, `sast`,
  `dependencies`, `deploy-review`, `build-scan (orders)`, `build-scan (inventory)`
* Require branches to be up to date before merging

Now nothing reaches main, and so nothing reaches the cluster, without passing every gate.

## 2.8 Daily use

```bash
make down          # stop Minikube, keep the state
minikube start     # bring it back; ArgoCD resyncs
make destroy       # wipe it. You must re-run `make seal-secrets` after a new cluster,
                   # because the old ciphertexts were made for the old controller key.
```
