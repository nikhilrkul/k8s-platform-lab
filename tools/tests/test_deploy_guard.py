"""Unit tests for the deploy guard. Each test plants one deploy-time bug and checks it is caught."""

from __future__ import annotations

import copy
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import deploy_guard as g


def write(tmp: Path, rel: str, content: str) -> Path:
    p = tmp / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(textwrap.dedent(content))
    return p


def messages(report: g.Report, level: str = "error") -> str:
    return "\n".join(f.message for f in report.findings if f.level == level)


# ------------------------------------------------------------------ pass 1


@pytest.mark.parametrize(
    "line,expect",
    [
        ("enabled: yes", "YAML 1.1 boolean"),
        ("mode: 0755", "leading zero"),
        ("version: 1.10", "trailing zero"),
        ("window: 1:30", "base-60"),
        ("apiKey: null", "explicitly null"),
        ("apiKey: ~", "explicitly null"),
        ("apiKey:", "has no value"),
    ],
)
def test_raw_values_lossy_scalars(tmp_path: Path, line: str, expect: str) -> None:
    p = write(tmp_path, "v.yaml", f"config:\n  {line}\nother: 1\n")
    r = g.Report()
    g.scan_raw_values(str(p), r)
    assert expect in messages(r)


def test_raw_values_big_int_is_warning(tmp_path: Path) -> None:
    p = write(tmp_path, "v.yaml", "limit: 1000000\n")
    r = g.Report()
    g.scan_raw_values(str(p), r)
    assert "scientific notation" in messages(r, "warning")


def test_raw_values_clean_file(tmp_path: Path) -> None:
    p = write(
        tmp_path,
        "v.yaml",
        """\
        image:
          tag: "abc1234"   # comment
        config:
          ENABLED: "yes"
          MODE: "0755"
        hosts: ["a"]
        list:
          - name: x
        empty: {}
        """,
    )
    r = g.Report()
    g.scan_raw_values(str(p), r)
    assert r.findings == []


def test_duplicate_keys(tmp_path: Path) -> None:
    p = write(tmp_path, "v.yaml", "a: 1\nb: 2\na: 3\n")
    r = g.Report()
    g.scan_raw_values(str(p), r)
    assert "duplicate key 'a'" in messages(r)


# ------------------------------------------------------------------ pass 2

GOOD: list[dict[str, Any]] = yaml.safe_load_all(
    """
apiVersion: v1
kind: ServiceAccount
metadata: {name: orders, labels: {app: orders}}
---
apiVersion: v1
kind: ConfigMap
metadata: {name: orders}
data: {INVENTORY_URL: "http://inventory"}
---
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata: {name: orders}
spec: {encryptedData: {API_KEY: AgBxyz}}
---
apiVersion: apps/v1
kind: Deployment
metadata: {name: orders}
spec:
  replicas: 2
  selector: {matchLabels: {app: orders, version: stable}}
  template:
    metadata: {labels: {app: orders, version: stable, app.kubernetes.io/instance: orders}}
    spec:
      serviceAccountName: orders
      containers:
        - name: app
          image: ghcr.io/x/orders:abc1234
          ports: [{name: http, containerPort: 8000}]
          env: [{name: APP_VERSION, value: "stable-abc1234"}]
          envFrom: [{configMapRef: {name: orders}}, {secretRef: {name: orders}}]
          resources: {requests: {cpu: 50m, memory: 64Mi}, limits: {memory: 256Mi}}
          livenessProbe: {httpGet: {path: /healthz, port: http}}
          readinessProbe: {httpGet: {path: /readyz, port: http}}
---
apiVersion: v1
kind: Service
metadata: {name: orders}
spec:
  selector: {app: orders}
  ports: [{name: http, port: 80, targetPort: http}]
---
apiVersion: policy/v1
kind: PodDisruptionBudget
metadata: {name: orders}
spec: {minAvailable: 1, selector: {matchLabels: {app: orders}}}
---
apiVersion: networking.istio.io/v1
kind: DestinationRule
metadata: {name: orders}
spec:
  host: orders.shop-dev.svc.cluster.local
  subsets: [{name: stable, labels: {version: stable}}, {name: canary, labels: {version: canary}}]
---
apiVersion: networking.istio.io/v1
kind: VirtualService
metadata: {name: orders}
spec:
  hosts: [orders.shop-dev.svc.cluster.local]
  http:
    - route:
        - {destination: {host: orders.shop-dev.svc.cluster.local, subset: stable}, weight: 100}
---
apiVersion: security.istio.io/v1
kind: AuthorizationPolicy
metadata: {name: orders}
spec:
  rules:
    - from:
        - source:
            principals:
              - cluster.local/ns/shop-dev/sa/orders
              - cluster.local/ns/istio-system/sa/istio-ingressgateway-service-account
"""
)
GOOD = list(GOOD)


def run(docs: list[dict[str, Any]]) -> tuple[g.Report, dict[str, set[str]]]:
    r = g.Report()
    provided = g.check_rendered("dev", [("r.yaml", d) for d in docs], r)
    return r, provided


def find(docs: list[dict[str, Any]], kind: str) -> dict[str, Any]:
    return next(d for d in docs if d["kind"] == kind)


def test_good_manifests_pass() -> None:
    r, provided = run(copy.deepcopy(GOOD))
    assert r.findings == []
    assert {"INVENTORY_URL", "API_KEY", "APP_VERSION"} <= provided["orders"]


def mutate(fn: Any) -> g.Report:
    docs = copy.deepcopy(GOOD)
    fn(docs)
    return run(docs)[0]


def test_null_value_caught() -> None:
    r = mutate(lambda d: find(d, "Deployment")["spec"]["template"]["spec"].update(nodeSelector=None))
    assert "is null" in messages(r)


def test_service_selector_typo() -> None:
    r = mutate(lambda d: find(d, "Service")["spec"].update(selector={"app": "order"}))
    assert "matches no pods" in messages(r)


def test_missing_secret_causes_error() -> None:
    r = mutate(lambda d: d.remove(find(d, "SealedSecret")))
    assert "CreateContainerConfigError" in messages(r)


def test_non_string_env_value() -> None:
    def f(d: list[dict[str, Any]]) -> None:
        c = find(d, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        c["env"].append({"name": "DEBUG", "value": True})

    assert "requires a string" in messages(mutate(f))


def test_latest_tag() -> None:
    def f(d: list[dict[str, Any]]) -> None:
        find(d, "Deployment")["spec"]["template"]["spec"]["containers"][0]["image"] = "ghcr.io/x/orders:latest"

    assert "immutable tag" in messages(mutate(f))


def test_missing_probe_and_limits() -> None:
    def f(d: list[dict[str, Any]]) -> None:
        c = find(d, "Deployment")["spec"]["template"]["spec"]["containers"][0]
        del c["readinessProbe"]
        c["resources"] = {}

    msg = messages(mutate(f))
    assert "no readinessProbe" in msg and "memory request/limit" in msg


def test_pdb_blocks_drain() -> None:
    r = mutate(lambda d: find(d, "PodDisruptionBudget")["spec"].update(minAvailable=2))
    assert "drains will hang" in messages(r)


def test_vs_weights_and_subsets() -> None:
    def f(d: list[dict[str, Any]]) -> None:
        route = find(d, "VirtualService")["spec"]["http"][0]["route"]
        route[0]["weight"] = 90
        route.append({"destination": {"host": "orders.shop-dev.svc.cluster.local", "subset": "canary"}, "weight": 20})

    msg = messages(mutate(f))
    assert "add up to 110" in msg and "has no pods" in msg


def test_authz_unknown_service_account() -> None:
    def f(d: list[dict[str, Any]]) -> None:
        principals = find(d, "AuthorizationPolicy")["spec"]["rules"][0]["from"][0]["source"]["principals"]
        principals.append("cluster.local/ns/shop-dev/sa/ordrs")

    assert "'ordrs'" in messages(mutate(f))


def test_configmap_lossy_float() -> None:
    r = mutate(lambda d: find(d, "ConfigMap")["data"].update(LIMIT="1e+06"))
    assert "lossy float" in messages(r)


def test_configmap_non_string() -> None:
    r = mutate(lambda d: find(d, "ConfigMap")["data"].update(PORT=8080))
    assert "must be string" in messages(r)


def test_selector_mismatch_with_pod_labels() -> None:
    r = mutate(lambda d: find(d, "Deployment")["spec"]["selector"]["matchLabels"].update(app="x"))
    assert "does not match its own pod labels" in messages(r)


# ------------------------------------------------------------------ pass 3


def test_contract(tmp_path: Path) -> None:
    write(tmp_path, "orders/env-contract.yaml", "required: [API_KEY, INVENTORY_URL]\noptional: [LOG_LEVEL]\n")
    write(tmp_path, "orders/app/config.py", 'a = require("API_KEY", x)\nb = os.environ.get("SURPRISE")\n')
    r = g.Report()
    g.check_contracts(str(tmp_path), {"dev": {"orders": {"API_KEY"}}}, r)
    msg = messages(r)
    assert "SURPRISE" in msg
    assert "requires env var INVENTORY_URL" in msg


def test_main_exit_code(tmp_path: Path) -> None:
    write(tmp_path, "values/bad.yaml", "flag: yes\n")
    assert g.main(["--values-glob", str(tmp_path / "values/*.yaml")]) == 1
    write(tmp_path, "ok/good.yaml", 'flag: "yes"\n')
    assert g.main(["--values-glob", str(tmp_path / "ok/*.yaml")]) == 0
