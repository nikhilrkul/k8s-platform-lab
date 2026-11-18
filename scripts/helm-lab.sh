#!/usr/bin/env bash
# Helm hands-on WITHOUT ArgoCD: builds images inside Minikube's Docker and installs
# the chart into the `helm-lab` namespace. ArgoCD doesn't manage this namespace, so its
# self-heal won't undo your experiments.
set -euo pipefail
cd "$(dirname "$0")/.."
TAG="${TAG:-v1}"
NS=helm-lab

kubectl get ns "$NS" >/dev/null 2>&1 || kubectl create ns "$NS"
kubectl label ns "$NS" istio-injection=enabled --overwrite

# Build straight into the cluster's container runtime: no registry needed.
eval "$(minikube docker-env)"
docker build -t "local/orders:${TAG}" services/orders
docker build -t "local/inventory:${TAG}" services/inventory

for svc in inventory orders; do
  helm upgrade --install "$svc" charts/microservice -n "$NS" \
    -f "deploy/local/${svc}.yaml" --set image.tag="$TAG" --wait --timeout 3m
done
helm -n "$NS" list
