#!/usr/bin/env bash
# End-to-end check through the Istio ingress gateway (needs `make tunnel` running).
set -euo pipefail
HOST="${1:-dev.shop.local}"
KEY="${API_KEY:?export API_KEY=<the key printed by make seal-secrets>}"
BASE="http://127.0.0.1"

echo "--> health";        curl -fsS -H "Host: $HOST" "$BASE/healthz"; echo
echo "--> create order";  ORDER=$(curl -fsS -H "Host: $HOST" -H "X-API-Key: $KEY" \
                            -H 'Content-Type: application/json' -d '{"sku":"BOOK-1","quantity":2}' "$BASE/orders")
echo "$ORDER"
ID=$(echo "$ORDER" | sed -n 's/.*"id":"\([^"]*\)".*/\1/p')
echo "--> fetch order";   curl -fsS -H "Host: $HOST" -H "X-API-Key: $KEY" "$BASE/orders/$ID"; echo
echo "--> no key (expect 401)"; curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $HOST" "$BASE/orders/$ID"
echo "--> inventory is NOT reachable from outside the mesh (expect 404, there is no route)"
curl -s -o /dev/null -w '%{http_code}\n' -H "Host: $HOST" "$BASE/stock/BOOK-1"
