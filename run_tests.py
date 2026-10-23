#!/usr/bin/env python3
"""Stdlib-only test runner for ShipGate (no pytest required).

Discovers every ``test_*`` function in ``tests/test_shipgate.py``, runs
them in name order and reports a pass/fail summary. The same file also
runs unchanged under pytest: ``python -m pytest tests/``.

Usage:  python run_tests.py
Exit code 0 = all tests passed, 1 = at least one failure.
"""
import importlib.util
import pathlib
import sys
import traceback

ROOT = pathlib.Path(__file__).resolve().parent


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "test_shipgate", ROOT / "tests" / "test_shipgate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    tests = sorted(
        (name, getattr(module, name))
        for name in dir(module)
        if name.startswith("test_") and callable(getattr(module, name))
    )
    failures = 0
    for name, fn in tests:
        try:
            fn()
        except Exception:
            failures += 1
            print(f"FAIL {name}")
            traceback.print_exc()
        else:
            print(f"pass {name}")
    print(f"\n{len(tests) - failures}/{len(tests)} tests passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
