#!/usr/bin/env bash
# Builds the whole lab on an Apple Silicon Mac. Safe to re-run: every step is idempotent.
#
#   Layer 0  Minikube (one-node Kubernetes inside Docker Desktop)
#   Layer 1  Platform installed imperatively: Istio, Sealed Secrets, ArgoCD
#            (the "bootstrap problem": something has to install the GitOps tool itself)
#   Layer 2  From here on, everything comes from git through ArgoCD (make gitops)
set -euo pipefail
cd "$(dirname "$0")/.."

CPUS="${MINIKUBE_CPUS:-4}"
MEMORY="${MINIKUBE_MEMORY:-8192}"
ARGOCD_CHART_VERSION="${ARGOCD_CHART_VERSION:-10.18.0}"
SEALED_SECRETS_CHART_VERSION="${SEALED_SECRETS_CHART_VERSION:-2.22.0}"

step() { printf '\n\033[1;34m==> %s\033[0m\n' "$*"; }

step "Checking tools"
for t in docker minikube kubectl helm istioctl kubeseal argocd; do
  command -v "$t" >/dev/null || { echo "missing: $t (run: make tools)"; exit 1; }
done
docker info >/dev/null 2>&1 || { echo "Docker Desktop is not running"; exit 1; }

step "Starting Minikube (${CPUS} CPUs, ${MEMORY} MB)"
if ! minikube status >/dev/null 2>&1; then
  minikube start --driver=docker --cpus="$CPUS" --memory="$MEMORY" --disk-size=30g \
    --addons=metrics-server
else
  echo "already running"
fi
kubectl config use-context minikube

step "Installing Istio (istiod + ingress gateway) and the observability add-ons"
istioctl install -f bootstrap/istio-config.yaml -y
ISTIO_MINOR="$(istioctl version --remote=false 2>/dev/null | grep -oE '[0-9]+\.[0-9]+' | head -1)"
ISTIO_MINOR="${ISTIO_MINOR:-1.32}"
for addon in prometheus grafana kiali jaeger; do
  kubectl apply -f "https://raw.githubusercontent.com/istio/istio/release-${ISTIO_MINOR}/samples/addons/${addon}.yaml"
done

step "Installing the Sealed Secrets controller"
helm repo add sealed-secrets https://bitnami-labs.github.io/sealed-secrets >/dev/null 2>&1 || true
helm repo add argo https://argoproj.github.io/argo-helm >/dev/null 2>&1 || true
helm repo update >/dev/null
helm upgrade --install sealed-secrets sealed-secrets/sealed-secrets \
  --namespace kube-system --version "$SEALED_SECRETS_CHART_VERSION" \
  --set fullnameOverride=sealed-secrets-controller --wait

step "Installing ArgoCD"
helm upgrade --install argocd argo/argo-cd \
  --namespace argocd --create-namespace --version "$ARGOCD_CHART_VERSION" \
  -f bootstrap/argocd-values.yaml --wait

step "Done"
PASS="$(kubectl -n argocd get secret argocd-initial-admin-secret -o jsonpath='{.data.password}' | base64 -d)"
cat <<MSG
ArgoCD admin password: ${PASS}

Next:
  1. make seal-secrets     # create encrypted API keys, then commit them
  2. git push              # CI builds images and bumps the dev tags
  3. make gitops           # hand the cluster over to ArgoCD
  4. make tunnel           # in a second terminal (asks for your password)
  5. make smoke
MSG
