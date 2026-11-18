# k8s-platform-lab: Minikube + GitHub Actions + Helm + ArgoCD + Istio on Apple Silicon.
# Run `make help` to list the targets.

SHELL := /bin/bash
GH_USER ?=
ENV ?= dev

.PHONY: help
help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ------------------------------------------------------------------ one-time setup
.PHONY: tools
tools: ## Install the CLIs with Homebrew
	brew install minikube kubectl helm istioctl argocd kubeseal yq conftest kubeconform k9s hey
	@echo "Also install Docker Desktop and give it >= 6 CPUs / 10 GB RAM (Settings > Resources)."

.PHONY: init
init: ## Put your GitHub username into the manifests: make init GH_USER=yourname
	@test -n "$(GH_USER)" || (echo "usage: make init GH_USER=<your github username>" && exit 1)
	@lower=$$(echo "$(GH_USER)" | tr '[:upper:]' '[:lower:]'); \
	grep -rlE 'YOUR_GH_USER|your_gh_user' --exclude=Makefile --exclude-dir=.git . | while read -r f; do \
	  perl -pi -e "s/YOUR_GH_USER/$(GH_USER)/g; s/your_gh_user/$$lower/g" "$$f"; echo "updated $$f"; done

# ------------------------------------------------------------------ cluster
.PHONY: up
up: ## Start Minikube and install Istio, Sealed Secrets and ArgoCD
	./scripts/bootstrap.sh

.PHONY: seal-secrets
seal-secrets: ## Encrypt API keys for dev and prod (commit the output files)
	./scripts/seal-secret.sh dev
	./scripts/seal-secret.sh prod

.PHONY: gitops
gitops: ## Hand the cluster to ArgoCD (apply the root App of Apps)
	kubectl apply -f deploy/argocd/root-app.yaml
	@echo "Watch it: kubectl -n argocd get applications -w   (or: make ui-argocd)"

.PHONY: tunnel
tunnel: ## Expose the Istio ingress gateway on 127.0.0.1 (keep it running)
	minikube tunnel

.PHONY: hosts
hosts: ## Add the lab hostnames to /etc/hosts
	@grep -q 'dev.shop.local' /etc/hosts || echo '127.0.0.1 dev.shop.local shop.local lab.shop.local' | sudo tee -a /etc/hosts

.PHONY: smoke
smoke: ## Call the API through the gateway: make smoke ENV=dev API_KEY=...
	API_KEY=$(API_KEY) ./scripts/smoke.sh $(if $(filter prod,$(ENV)),shop.local,dev.shop.local)

.PHONY: helm-lab
helm-lab: ## Plain-Helm playground in namespace helm-lab: make helm-lab TAG=v1
	./scripts/helm-lab.sh

# ------------------------------------------------------------------ dashboards
.PHONY: ui-argocd ui-kiali ui-grafana ui-jaeger
ui-argocd: ## ArgoCD UI on http://localhost:8080 (user: admin)
	@echo "password: $$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
	kubectl -n argocd port-forward svc/argocd-server 8080:80
ui-kiali: ## Kiali: live service graph of the mesh
	istioctl dashboard kiali
ui-grafana: ## Grafana: Istio dashboards
	istioctl dashboard grafana
ui-jaeger: ## Jaeger: distributed traces
	istioctl dashboard jaeger

# ------------------------------------------------------------------ run CI checks locally
.PHONY: ci-local
ci-local: ## Run the same lint/test/deploy-review gates as CI, on your Mac
	cd services/orders && python3 -m venv .venv && . .venv/bin/activate && \
	  pip install -q -r requirements-dev.txt && ruff check . && ruff format --check . && mypy app tests && pytest
	cd services/inventory && go vet ./... && go test -race ./...
	./tools/render.sh build/rendered
	kubeconform -strict -summary -schema-location default \
	  -schema-location 'https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json' \
	  build/rendered deploy/argocd
	conftest verify -p policy && conftest test -p policy --all-namespaces build/rendered/*/*.yaml
	. services/orders/.venv/bin/activate && pip install -q pyyaml && python tools/deploy_guard.py \
	  --values-glob 'deploy/values/**/*.yaml' --values-glob 'charts/*/values.yaml' \
	  --rendered build/rendered --services services

# ------------------------------------------------------------------ teardown
.PHONY: down
down: ## Stop Minikube (keeps state)
	minikube stop

.PHONY: destroy
destroy: ## Delete the cluster entirely (sealed secrets must be re-created afterwards)
	minikube delete
