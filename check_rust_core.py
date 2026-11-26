#!/usr/bin/env python3
"""Self-check for the Fluxus Rust core.

Run it from your Fluxus project folder:

    python check_rust_core.py                 # just check it loads
    python check_rust_core.py devices.log     # check + parse a log + time it

It prints plain-English diagnostics, so if something is wrong the message
usually says exactly what to fix.
"""

import platform
import sys
import time


def line(label, value):
    print(f"  {label:<26} {value}")


def main():
    print("Fluxus Rust core — installation check")
    print("-" * 52)
    line("Python", f"{platform.python_version()} ({platform.architecture()[0]})")
    line("Executable", sys.executable)
    line("Platform", platform.platform())

    ok = True

    # 1. the compiled extension
    try:
        import fluxus_native
    except ImportError as e:
        ok = False
        print("\n[FAIL] fluxus_native is not importable.")
        print(f"       reason: {e}")
        print("       -> the .pyd file is missing, or built for a different Python.")
    else:
        line("fluxus_native", f"OK, version {fluxus_native.__version__}")
        line("  loaded from", getattr(fluxus_native, "__file__", "?"))

    # 2. the adapter layer
    try:
        import fluxus_adapter  # noqa: F401
    except ImportError as e:
        ok = False
        print("\n[FAIL] fluxus_adapter.py is not importable.")
        print(f"       reason: {e}")
        print("       -> copy fluxus_adapter.py next to main.py.")
    else:
        line("fluxus_adapter", "OK")

    # 3. optional: parse a log
    if len(sys.argv) > 1:
        path = sys.argv[1]
        try:
            from fluxus_adapter import LimaAnalyzer
            a = LimaAnalyzer()
            t = time.perf_counter()
            a.parse_log_file(path)
            a.build_sessions()
            dt = (time.perf_counter() - t) * 1000
            stats = a.get_stats()
            print()
            line("Parse time", f"{dt:.0f} ms")
            line("Banknotes counted", stats[1])
            line("Sessions", len(a.sessions))
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"\n[FAIL] could not parse {path}: {e}")

    print("-" * 52)
    print("RESULT:", "READY — the Rust core is installed correctly"
          if ok else "NOT READY — fix the items marked FAIL above")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
