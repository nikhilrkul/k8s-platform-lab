#!/usr/bin/env python3
"""Convert a Go coverprofile into Cobertura XML so diff-cover can gate new Go lines.

Go reports coverage per *block* (start line to end line). Cobertura, which diff-cover
reads, wants per-line hits. Each line in a block takes the block's hit count.

Usage (run inside the Go module directory):
  go test -coverprofile=coverage.out ./...
  python3 ../../tools/gocover_to_cobertura.py coverage.out coverage.xml
"""

from __future__ import annotations

import re
import sys
import time
from collections import defaultdict
from pathlib import Path
from xml.sax.saxutils import quoteattr

BLOCK = re.compile(r"^(?P<file>.+):(?P<sl>\d+)\.\d+,(?P<el>\d+)\.\d+ (?P<stmts>\d+) (?P<count>\d+)$")


def module_path(go_mod: Path) -> str:
    for line in go_mod.read_text().splitlines():
        if line.startswith("module "):
            return line.split()[1]
    raise SystemExit("go.mod has no module line")


def convert(profile: str, out: str, go_mod: str = "go.mod") -> float:
    mod = module_path(Path(go_mod)) + "/"
    hits: dict[str, dict[int, int]] = defaultdict(dict)
    for raw in Path(profile).read_text().splitlines()[1:]:  # first line is "mode: ..."
        m = BLOCK.match(raw.strip())
        if not m:
            continue
        rel = m["file"].removeprefix(mod)
        lines = hits[rel]
        for ln in range(int(m["sl"]), int(m["el"]) + 1):
            lines[ln] = max(lines.get(ln, 0), int(m["count"]))
    total = sum(len(v) for v in hits.values())
    covered = sum(1 for v in hits.values() for c in v.values() if c > 0)
    rate = covered / total if total else 1.0
    parts = [
        '<?xml version="1.0" ?>',
        f'<coverage line-rate="{rate:.4f}" branch-rate="0" version="1" timestamp="{int(time.time())}">',
        "<sources><source>.</source></sources><packages>",
    ]
    for pkg in sorted({str(Path(f).parent) for f in hits}):
        parts.append(f'<package name={quoteattr(pkg)} line-rate="0"><classes>')
        for f in sorted(x for x in hits if str(Path(x).parent) == pkg):
            parts.append(f'<class name={quoteattr(Path(f).name)} filename={quoteattr(f)} line-rate="0"><lines>')
            parts.extend(f'<line number="{n}" hits="{c}"/>' for n, c in sorted(hits[f].items()))
            parts.append("</lines></class>")
        parts.append("</classes></package>")
    parts.append("</packages></coverage>")
    Path(out).write_text("\n".join(parts))
    return rate


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    print(f"line coverage: {convert(sys.argv[1], sys.argv[2]) * 100:.1f}%")
