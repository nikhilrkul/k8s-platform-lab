#!/usr/bin/env python3
"""Deploy guard: a whole-repo reviewer that looks for things that will break at deploy time.

Linters check code style and unit tests check logic. Neither tells you that a Service
selector matches no pods, that `enabled: yes` was quietly turned into a boolean, or
that the app needs API_KEY and no environment provides it. This script does. It runs
on every PR, over the whole codebase (not just the diff), in three passes:

  1. Raw values files: YAML pitfalls that silently change values (lossy scalars),
     nulls, empty keys and duplicate keys.
  2. Rendered manifests (output of `helm template` for every service x env): cross-object
     consistency that `kubectl apply --dry-run` won't catch.
  3. The env-var contract between code and platform.

Exit code 1 if any ERROR is found. Findings are printed as GitHub annotations, so they
show up inline on the PR diff, and as a Markdown table in the job summary.

Usage:
  deploy_guard.py --values-glob 'deploy/values/**/*.yaml' --rendered build/rendered \
                  --services services
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import sys
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# --------------------------------------------------------------------------- findings


@dataclass
class Finding:
    level: str  # "error" | "warning"
    file: str
    message: str
    line: int | None = None


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def error(self, file: str, msg: str, line: int | None = None) -> None:
        self.findings.append(Finding("error", file, msg, line))

    def warn(self, file: str, msg: str, line: int | None = None) -> None:
        self.findings.append(Finding("warning", file, msg, line))

    @property
    def errors(self) -> list[Finding]:
        return [f for f in self.findings if f.level == "error"]

    def emit(self) -> None:
        for f in self.findings:
            loc = f"file={f.file}" + (f",line={f.line}" if f.line else "")
            print(f"::{f.level} {loc}::{f.message}")
        summary = os.environ.get("GITHUB_STEP_SUMMARY")
        if summary:
            with open(summary, "a", encoding="utf-8") as out:
                out.write("## Deploy guard\n\n")
                if not self.findings:
                    out.write("No deploy-time issues found.\n")
                    return
                out.write("| Level | File | Finding |\n|---|---|---|\n")
                for f in self.findings:
                    where = f"{f.file}:{f.line}" if f.line else f.file
                    out.write(f"| {f.level} | `{where}` | {f.message.replace('|', '/')} |\n")


# --------------------------------------------------------------------------- pass 1: raw values

# Unquoted scalars that YAML (1.1, which Go's yaml libs and Helm partly follow) reinterprets.
YAML11_BOOLS = re.compile(r"^(y|Y|yes|Yes|YES|n|N|no|No|NO|on|On|ON|off|Off|OFF)$")
LEADING_ZERO = re.compile(r"^0[0-9]+$")  # 0755 -> octal 493, 012345 zip code -> 5349
SEXAGESIMAL = re.compile(r"^[0-9]+(:[0-5][0-9])+$")  # 1:20 -> 80 in YAML 1.1
TRAILING_ZERO_FLOAT = re.compile(r"^[0-9]+\.[0-9]*0$")  # version 1.10 -> 1.1
BIG_INT = re.compile(r"^[0-9]{7,}$")  # Helm may render 1000000 as 1e+06
NULLISH = {"null", "Null", "NULL", "~"}
KV_LINE = re.compile(r"^(?P<indent>\s*)(?:- )?(?P<key>[A-Za-z0-9_.\-/\"']+):(?:\s+(?P<val>[^#]*?))?\s*(?:#.*)?$")


def scan_raw_values(path: str, report: Report) -> None:
    text = Path(path).read_text(encoding="utf-8")
    lines = text.splitlines()
    for i, line in enumerate(lines, start=1):
        if "\t" in line[: len(line) - len(line.lstrip())]:
            report.error(path, "tab used for indentation (YAML forbids tabs)", i)
        m = KV_LINE.match(line)
        if not m:
            continue
        key, val = m.group("key"), (m.group("val") or "").strip()
        if val == "":
            # Empty value is fine only if the next non-blank line is a nested child.
            nxt = next((ln for ln in lines[i:] if ln.strip() and not ln.strip().startswith("#")), "")
            child_indent = len(nxt) - len(nxt.lstrip())
            if child_indent <= len(m.group("indent")) and not nxt.lstrip().startswith("- "):
                report.error(path, f"'{key}' has no value: it renders as null", i)
            continue
        if val[0] in "\"'[{|>&*!":
            continue  # quoted, flow collection, block scalar, anchor/alias, tag
        if val in NULLISH:
            report.error(path, f"'{key}' is explicitly null; set a real value or remove the key", i)
        elif YAML11_BOOLS.match(val):
            report.error(path, f"'{key}: {val}' is a YAML 1.1 boolean. Write true/false, or quote it", i)
        elif LEADING_ZERO.match(val):
            report.error(path, f"'{key}: {val}' has a leading zero and may be read as octal. Quote it", i)
        elif SEXAGESIMAL.match(val):
            report.error(path, f"'{key}: {val}' may be read as base-60 by YAML 1.1. Quote it", i)
        elif TRAILING_ZERO_FLOAT.match(val):
            report.error(path, f"'{key}: {val}' is a float and loses its trailing zero. Quote it", i)
        elif BIG_INT.match(val):
            report.warn(path, f"'{key}: {val}' may render as scientific notation in Helm. Quote it", i)
    try:
        yaml.load(text, Loader=UniqueKeyLoader)  # noqa: S506 - UniqueKeyLoader extends SafeLoader
    except DuplicateKeyError as exc:
        report.error(path, str(exc), exc.line)
    except yaml.YAMLError as exc:
        report.error(path, f"invalid YAML: {exc}")


class DuplicateKeyError(yaml.YAMLError):
    def __init__(self, msg: str, line: int):
        super().__init__(msg)
        self.line = line


class UniqueKeyLoader(yaml.SafeLoader):
    """PyYAML, Go and Helm all silently keep the LAST duplicate key. We refuse."""

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
        seen: dict[Any, int] = {}
        for key_node, _ in node.value:
            key = self.construct_object(key_node, deep=deep)
            line = key_node.start_mark.line + 1
            if key in seen:
                raise DuplicateKeyError(
                    f"duplicate key '{key}' (first on line {seen[key]}); the first value is discarded",
                    line,
                )
            seen[key] = line
        return super().construct_mapping(node, deep=deep)


# --------------------------------------------------------------------------- pass 2: rendered

DNS1123 = re.compile(r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?(\.[a-z0-9]([-a-z0-9]*[a-z0-9])?)*$")
LABEL_VALUE = re.compile(r"^(([A-Za-z0-9][-A-Za-z0-9_.]*)?[A-Za-z0-9])?$")
WORKLOADS = {"Deployment", "StatefulSet", "DaemonSet"}


def walk_nulls(obj: Any, path: str = "") -> Iterator[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else str(k)
            if v is None:
                yield p
            else:
                yield from walk_nulls(v, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            if v is None:
                yield f"{path}[{i}]"
            else:
                yield from walk_nulls(v, f"{path}[{i}]")


def load_rendered(root: str) -> dict[str, list[tuple[str, dict[str, Any]]]]:
    """Return {env: [(file, manifest), ...]} from build/rendered/<env>/*.yaml."""
    by_env: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for f in sorted(glob.glob(os.path.join(root, "*", "*.yaml"))):
        env = Path(f).parent.name
        with open(f, encoding="utf-8") as fh:
            for doc in yaml.safe_load_all(fh):
                if isinstance(doc, dict) and doc.get("kind"):
                    by_env[env].append((f, doc))
    return by_env


def _selects(selector: dict[str, str], labels: dict[str, str]) -> bool:
    return bool(selector) and all(labels.get(k) == v for k, v in selector.items())


def check_rendered(env: str, docs: list[tuple[str, dict[str, Any]]], report: Report) -> dict[str, set[str]]:
    """Validate one environment. Returns {release: env var names provided} for pass 3."""
    index: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
    seen: set[tuple[str, str, str]] = set()
    for f, d in docs:
        meta = d.get("metadata") or {}
        # Chart objects carry no namespace (Helm/ArgoCD set it), platform objects do.
        full_key = (d["kind"], meta.get("namespace", "<release>"), meta.get("name", ""))
        if full_key in seen:
            report.error(f, f"[{env}] duplicate {full_key[0]} '{full_key[2]}': two sources render the same object")
        seen.add(full_key)
        index[(d["kind"], meta.get("name", ""))] = (f, d)

    def has(kind: str, name: str) -> bool:
        return (kind, name) in index

    secret_keys: dict[str, set[str]] = {}
    cm_keys: dict[str, set[str]] = {}
    for f, d in docs:
        kind, meta = d["kind"], d.get("metadata") or {}
        name = meta.get("name", "")
        for p in walk_nulls(d):
            report.error(f, f"[{env}] {kind}/{name}: '{p}' is null")
        if not DNS1123.match(name) or len(name) > 63:
            report.error(f, f"[{env}] {kind} name '{name}' is not a valid DNS-1123 name")
        for lk, lv in (meta.get("labels") or {}).items():
            if not isinstance(lv, str) or len(lv) > 63 or not LABEL_VALUE.match(lv):
                report.error(f, f"[{env}] {kind}/{name}: label {lk}={lv!r} is not a valid label value")
        if kind == "ConfigMap":
            data = d.get("data") or {}
            for k, v in data.items():
                if not isinstance(v, str):
                    report.error(f, f"[{env}] ConfigMap/{name}: data.{k} is {type(v).__name__}, must be string")
                elif re.fullmatch(r"[0-9.]+e\+[0-9]+", v):
                    report.error(f, f"[{env}] ConfigMap/{name}: data.{k}={v} looks like a lossy float rendering")
            cm_keys[name] = set(data)
        elif kind == "Secret":
            secret_keys[name] = set((d.get("stringData") or {}) | (d.get("data") or {}))
            if env != "local":
                report.warn(f, f"[{env}] plain Secret/{name} would be committed to git; use a SealedSecret")
        elif kind == "SealedSecret":
            secret_keys[name] = set(d.get("spec", {}).get("encryptedData") or {})

    provided: dict[str, set[str]] = {}
    workloads = [(f, d) for f, d in docs if d["kind"] in WORKLOADS]
    for f, d in workloads:
        name = d["metadata"]["name"]
        spec = d.get("spec") or {}
        sel = (spec.get("selector") or {}).get("matchLabels") or {}
        tpl = spec.get("template") or {}
        pod_labels = (tpl.get("metadata") or {}).get("labels") or {}
        if not _selects(sel, pod_labels):
            report.error(f, f"[{env}] {d['kind']}/{name}: selector {sel} does not match its own pod labels")
        pod = tpl.get("spec") or {}
        sa = pod.get("serviceAccountName", "default")
        if sa != "default" and not has("ServiceAccount", sa):
            report.error(f, f"[{env}] {d['kind']}/{name}: serviceAccount '{sa}' is not rendered anywhere")
        release = pod_labels.get("app.kubernetes.io/instance", name)
        env_names: set[str] = provided.setdefault(release, set())
        for c in pod.get("containers") or []:
            cname = c.get("name")
            image = str(c.get("image", ""))
            tag = image.rsplit(":", 1)[-1] if ":" in image.rsplit("/", 1)[-1] else ""
            if not tag or tag == "latest":
                report.error(f, f"[{env}] {name}/{cname}: image '{image}' needs an immutable tag, not latest/empty")
            elif tag == "bootstrap":
                report.warn(f, f"[{env}] {name}/{cname}: image tag is still the 'bootstrap' placeholder")
            res = c.get("resources") or {}
            if not (res.get("requests") or {}).get("memory") or not (res.get("limits") or {}).get("memory"):
                report.error(f, f"[{env}] {name}/{cname}: missing memory request/limit (OOM risk, bad scheduling)")
            if not (res.get("requests") or {}).get("cpu"):
                report.warn(f, f"[{env}] {name}/{cname}: no CPU request; HPA cannot compute utilisation")
            for probe in ("livenessProbe", "readinessProbe"):
                if probe not in c:
                    report.error(f, f"[{env}] {name}/{cname}: no {probe}")
            port_names = {p.get("name") for p in c.get("ports") or []}
            for probe in ("livenessProbe", "readinessProbe"):
                port = ((c.get(probe) or {}).get("httpGet") or {}).get("port")
                if isinstance(port, str) and port not in port_names:
                    report.error(f, f"[{env}] {name}/{cname}: {probe} uses port '{port}' that isn't declared")
            for e in c.get("env") or []:
                env_names.add(e.get("name"))
                if "value" in e and not isinstance(e["value"], str):
                    report.error(
                        f,
                        f"[{env}] {name}/{cname}: env {e.get('name')} is {type(e['value']).__name__}; "
                        "Kubernetes requires a string (quote it)",
                    )
            for src in c.get("envFrom") or []:
                if "configMapRef" in src:
                    ref = src["configMapRef"]["name"]
                    if ref not in cm_keys:
                        report.error(f, f"[{env}] {name}: envFrom ConfigMap '{ref}' is not rendered")
                    env_names |= cm_keys.get(ref, set())
                if "secretRef" in src:
                    ref = src["secretRef"]["name"]
                    if ref not in secret_keys:
                        report.error(
                            f,
                            f"[{env}] {name}: envFrom Secret '{ref}' is not rendered. "
                            "Pods will sit in CreateContainerConfigError (run `make seal-secrets`?)",
                        )
                    env_names |= secret_keys.get(ref, set())

    # Services must select real pods and target declared ports.
    pod_label_sets = [((d["spec"]["template"].get("metadata") or {}).get("labels") or {}, d) for _, d in workloads]
    for f, d in docs:
        if d["kind"] != "Service":
            continue
        name, sel = d["metadata"]["name"], (d.get("spec") or {}).get("selector") or {}
        matches = [w for labels, w in pod_label_sets if _selects(sel, labels)]
        if not matches:
            report.error(f, f"[{env}] Service/{name}: selector {sel} matches no pods; it will have no endpoints")
        for p in d["spec"].get("ports") or []:
            tp = p.get("targetPort")
            if isinstance(tp, str):
                for w in matches:
                    declared = {
                        cp.get("name")
                        for c in w["spec"]["template"]["spec"].get("containers", [])
                        for cp in c.get("ports") or []
                    }
                    if tp not in declared:
                        report.error(
                            f, f"[{env}] Service/{name}: targetPort '{tp}' not declared by {w['metadata']['name']}"
                        )

    # PDBs that can never be satisfied block node drains forever.
    replicas: dict[str, int] = {}
    for _, d in workloads:
        replicas[d["metadata"]["name"]] = int(d["spec"].get("replicas", 1))
    for f, d in docs:
        if d["kind"] == "HorizontalPodAutoscaler":
            target = d["spec"]["scaleTargetRef"]["name"]
            if not has("Deployment", target):
                report.error(f, f"[{env}] HPA/{d['metadata']['name']} targets missing Deployment '{target}'")
            replicas[target] = int(d["spec"].get("minReplicas", 1))
    for f, d in docs:
        if d["kind"] != "PodDisruptionBudget":
            continue
        min_avail = d["spec"].get("minAvailable")
        sel = d["spec"]["selector"]["matchLabels"]
        total = sum(replicas.get(w["metadata"]["name"], 1) for labels, w in pod_label_sets if _selects(sel, labels))
        if isinstance(min_avail, int) and min_avail >= total:
            report.error(
                f, f"[{env}] PDB/{d['metadata']['name']}: minAvailable {min_avail} >= {total} pods; drains will hang"
            )

    # Istio: routes must point at real Services and subsets, and weights must add up.
    services = {d["metadata"]["name"] for _, d in docs if d["kind"] == "Service"}
    subsets: dict[str, set[str]] = {}
    for _, d in docs:
        if d["kind"] == "DestinationRule":
            host = d["spec"]["host"].split(".")[0]
            subsets[host] = {s["name"] for s in d["spec"].get("subsets") or []}
    for f, d in docs:
        if d["kind"] != "VirtualService":
            continue
        vs = d["metadata"]["name"]
        for route in d["spec"].get("http") or []:
            dests = route.get("route") or []
            total_w = sum(int(r.get("weight", 100 if len(dests) == 1 else 0)) for r in dests)
            if total_w != 100:
                report.error(f, f"[{env}] VirtualService/{vs}: route weights add up to {total_w}, not 100")
            for r in dests:
                host = r["destination"]["host"].split(".")[0]
                subset = r["destination"].get("subset")
                if host not in services:
                    report.error(f, f"[{env}] VirtualService/{vs}: destination '{host}' has no Service")
                if subset and subset not in subsets.get(host, set()):
                    report.error(f, f"[{env}] VirtualService/{vs}: subset '{subset}' not defined in a DestinationRule")
                if (
                    subset
                    and r.get("weight", 0) > 0
                    and not any(
                        labels.get("version") == subset and labels.get("app") == host for labels, _ in pod_label_sets
                    )
                ):
                    report.error(f, f"[{env}] VirtualService/{vs}: subset '{subset}' gets traffic but has no pods")
    for f, d in docs:
        if d["kind"] != "AuthorizationPolicy":
            continue
        for rule in d.get("spec", {}).get("rules") or []:
            for frm in rule.get("from") or []:
                for principal in (frm.get("source") or {}).get("principals") or []:
                    sa = principal.rsplit("/", 1)[-1]
                    if "/ns/istio-system/" in principal:
                        continue
                    if not has("ServiceAccount", sa):
                        report.error(
                            f,
                            f"[{env}] AuthorizationPolicy/{d['metadata']['name']}: allows SA '{sa}', "
                            "which doesn't exist in this environment (typo? its calls would get 403)",
                        )
    return provided


# --------------------------------------------------------------------------- pass 3: env contract

PY_ENV = re.compile(r"""(?:os\.environ\.get|os\.getenv|\be\.get|require|parse_\w+)\(\s*["']([A-Z][A-Z0-9_]+)["']""")
GO_ENV = re.compile(r"""(?:os\.Getenv|os\.LookupEnv|getenv)\(\s*"([A-Z][A-Z0-9_]+)"\s*""")


def check_contracts(services_dir: str, provided_by_env: dict[str, dict[str, set[str]]], report: Report) -> None:
    for contract_file in sorted(glob.glob(os.path.join(services_dir, "*", "env-contract.yaml"))):
        svc_dir = Path(contract_file).parent
        svc = svc_dir.name
        contract = yaml.safe_load(Path(contract_file).read_text()) or {}
        required = set(contract.get("required") or [])
        declared = required | set(contract.get("optional") or [])
        for src in list(svc_dir.rglob("*.py")) + list(svc_dir.rglob("*.go")):
            if "test" in src.name or "tests" in src.parts:
                continue
            pattern = PY_ENV if src.suffix == ".py" else GO_ENV
            for i, line in enumerate(src.read_text(encoding="utf-8").splitlines(), start=1):
                for var in pattern.findall(line):
                    if var not in declared:
                        report.error(str(src), f"code reads env var {var}, which is not in {contract_file}", i)
        for env, provided in provided_by_env.items():
            if svc not in provided:
                report.warn(contract_file, f"[{env}] service '{svc}' was not rendered; contract not checked")
                continue
            for var in sorted(required - provided[svc]):
                report.error(
                    contract_file, f"[{env}] {svc} requires env var {var} but no ConfigMap, Secret or env provides it"
                )


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--values-glob", action="append", default=[])
    ap.add_argument("--rendered", help="directory of rendered manifests: <dir>/<env>/<release>.yaml")
    ap.add_argument("--services", help="directory containing services/<name>/env-contract.yaml")
    args = ap.parse_args(argv)

    report = Report()
    for pattern in args.values_glob:
        for path in sorted(glob.glob(pattern, recursive=True)):
            scan_raw_values(path, report)
    provided_by_env: dict[str, dict[str, set[str]]] = {}
    if args.rendered:
        for env, docs in load_rendered(args.rendered).items():
            provided_by_env[env] = check_rendered(env, docs, report)
    if args.services:
        check_contracts(args.services, provided_by_env, report)

    report.emit()
    n_err, n_warn = len(report.errors), len(report.findings) - len(report.errors)
    print(f"deploy-guard: {n_err} error(s), {n_warn} warning(s)")
    return 1 if n_err else 0


if __name__ == "__main__":
    sys.exit(main())
