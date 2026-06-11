#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib
import json
import sys
from collections.abc import Iterable


def _iter_rows(module_name: str) -> Iterable[tuple[str, str, str]]:
    module = importlib.import_module(module_name)
    app = getattr(module, "app", None)
    if app is None:
        raise RuntimeError(f"{module_name} does not expose an 'app' object")

    for route in getattr(app, "routes", []):
        methods = sorted(
            method
            for method in (getattr(route, "methods", None) or [])
            if method not in {"HEAD", "OPTIONS"}
        )
        path = getattr(route, "path", None)
        if not path:
            continue
        for method in methods:
            yield module_name, method, path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Dump FastAPI route contracts as METHOD PATH lines."
    )
    parser.add_argument(
        "--module",
        action="append",
        dest="modules",
        required=True,
        help="Python module that contains an 'app' FastAPI instance (repeatable).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON instead of text.",
    )
    args = parser.parse_args()

    rows: list[tuple[str, str, str]] = []
    for module_name in args.modules:
        rows.extend(_iter_rows(module_name))
    rows = sorted(set(rows))

    if args.json:
        payload = [
            {"module": module_name, "method": method, "path": path}
            for module_name, method, path in rows
        ]
        json.dump(payload, sys.stdout, indent=2)
        sys.stdout.write("\n")
        return 0

    current_module = None
    for module_name, method, path in rows:
        if module_name != current_module:
            current_module = module_name
            sys.stdout.write(f"[{module_name}]\n")
        sys.stdout.write(f"{method} {path}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
