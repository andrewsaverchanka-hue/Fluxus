#!/usr/bin/env python3
"""Compare the JSON produced by the Python reference and the Rust port.

Reports the first N mismatches with a path into the structure, so a behaviour
difference is a one-line diagnosis instead of a mystery.
"""

import json
import sys

MAX_REPORTS = 25


def norm_num(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return float(v)
    return v


IGNORE = set()


def ignored(path):
    return any(path == f".{k}" or path.endswith(f".{k}") for k in IGNORE)


def diff(a, b, path="", out=None, limit=MAX_REPORTS):
    if out is None:
        out = []
    if len(out) >= limit or ignored(path):
        return out

    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float)) and not isinstance(a, bool) and not isinstance(b, bool)):
        out.append(f"{path or '.'}: type {type(a).__name__} != {type(b).__name__}")
        return out

    if isinstance(a, dict):
        a_keys, b_keys = set(a), set(b)
        for k in sorted(a_keys - b_keys):
            if ignored(f"{path}.{k}"):
                continue
            out.append(f"{path}.{k}: missing in rust")
        for k in sorted(b_keys - a_keys):
            if ignored(f"{path}.{k}"):
                continue
            out.append(f"{path}.{k}: extra in rust")
        for k in sorted(a_keys & b_keys):
            diff(a[k], b[k], f"{path}.{k}", out, limit)
            if len(out) >= limit:
                break
    elif isinstance(a, list):
        if len(a) != len(b):
            out.append(f"{path}: list length {len(a)} != {len(b)}")
        for i, (x, y) in enumerate(zip(a, b)):
            diff(x, y, f"{path}[{i}]", out, limit)
            if len(out) >= limit:
                break
    else:
        if isinstance(a, float) or isinstance(b, float):
            if abs(norm_num(a) - norm_num(b)) > 1e-9:
                out.append(f"{path}: {a!r} != {b!r}")
        elif a != b:
            out.append(f"{path}: {a!r} != {b!r}")
    return out


def main():
    if len([a for a in sys.argv[1:] if not a.startswith("--")]) < 2:
        print("usage: compare.py <python.json> <rust.json> [--ignore=key,key]", file=sys.stderr)
        return 2
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    for a in sys.argv[1:]:
        if a.startswith("--ignore="):
            IGNORE.update(a.split("=", 1)[1].split(","))

    with open(args[0], encoding="utf-8") as f:
        py = json.load(f)
    with open(args[1], encoding="utf-8") as f:
        rs = json.load(f)

    problems = diff(py, rs)
    if not problems:
        print("PARITY OK: python and rust outputs are identical")
        return 0
    print(f"PARITY FAILED: {len(problems)} difference(s) shown")
    for p in problems:
        print("  " + p)
    return 1


if __name__ == "__main__":
    sys.exit(main())
