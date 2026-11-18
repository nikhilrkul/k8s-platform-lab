#!/usr/bin/env bash
# Render every service for every environment exactly as ArgoCD will, into
# build/rendered/<env>/<service>.yaml, so CI can validate the real output.
set -euo pipefail
cd "$(dirname "$0")/.."
OUT="${1:-build/rendered}"
CHART=charts/microservice
rm -rf "$OUT"
for env in dev prod; do
  mkdir -p "$OUT/$env"
  for dir in deploy/values/*/; do
    svc="$(basename "$dir")"
    args=(--namespace "shop-$env" -f "$dir/values-$env.yaml")
    if [[ -f "$dir/secrets-$env.yaml" ]]; then
      args+=(-f "$dir/secrets-$env.yaml")
    fi
    echo "==> $svc ($env)"
    helm lint --strict "$CHART" "${args[@]}" >/dev/null
    helm template "$svc" "$CHART" --skip-tests "${args[@]}" > "$OUT/$env/$svc.yaml"
  done
  # Platform manifests (namespaces, mesh policies, gateway) are validated alongside.
  cat deploy/platform/*.yaml > "$OUT/$env/_platform.yaml"
done
echo "Rendered to $OUT"
