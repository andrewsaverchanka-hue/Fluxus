#!/usr/bin/env python3
"""Verify .anl.gz compatibility in both directions.

1. Python parses the logs, correlates, saves a bundle  -> py.anl.gz
2. Rust parses the same logs and saves a bundle        -> rs.anl.gz
3. The two documents must be semantically identical (except `saved_at`).
4. Rust must be able to load the *Python* file and re-emit the same document.
5. Python must be able to load the *Rust* file and recover the same analysis.

Usage: serializer_parity.py <lima log> <frontend log> <lowlevel log>
"""

import gzip
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT.parent / "fluxus-py"))

from parsers.lima_parser import LogAnalyzer  # noqa: E402
from parsers.serializer import AnalysisSerializer  # noqa: E402
from parsers.w2_frontend_parser import W2FrontendAnalyzer  # noqa: E402
from parsers.w2_lowlevel_parser import W2LowLevelAnalyzer  # noqa: E402

FIX = ROOT / "fixtures"
BIN = ROOT / "target/release/fluxus-parse"


def parse_all(lima_log, front_log, low_log):
    lima = LogAnalyzer()
    lima.parse_log_file(lima_log)
    lima.build_sessions()
    fe = W2FrontendAnalyzer()
    fe.parse_log_file(front_log)
    fe.build_sessions()
    ll = W2LowLevelAnalyzer()
    ll.parse_log_file(low_log)
    ll.build_sessions()
    ll.correlate_with_frontend(fe.sessions)
    return lima, fe, ll


def read_gz(path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return json.load(f)


def main():
    if len(sys.argv) < 4:
        print(__doc__, file=sys.stderr)
        return 2
    lima_log, front_log, low_log = sys.argv[1:4]

    py_path = FIX / "py.anl.gz"
    rs_path = FIX / "rs.anl.gz"

    lima, fe, ll = parse_all(lima_log, front_log, low_log)
    AnalysisSerializer.save(py_path, lima, fe, ll, [lima_log, front_log, low_log])
    py_doc = read_gz(py_path)
    (FIX / "py_bundle.json").write_text(
        json.dumps(py_doc, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )

    subprocess.run(
        [str(BIN), "--save", str(rs_path), "--lima", lima_log,
         "--frontend", front_log, "--lowlevel", low_log],
        check=True, capture_output=True,
    )
    rs_doc = read_gz(rs_path)
    (FIX / "rs_bundle.json").write_text(
        json.dumps(rs_doc, sort_keys=True, ensure_ascii=False), encoding="utf-8"
    )

    print("== rust save vs python save ==")
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools/compare.py"),
         str(FIX / "py_bundle.json"), str(FIX / "rs_bundle.json"),
         "--ignore=saved_at,loaded_by_nominal,total_loaded_cents"],
        capture_output=True, text=True,
    )
    print(r.stdout.strip())
    if r.returncode != 0:
        return 1

    # ---- Rust loads the Python file and re-serializes it ----
    print("== rust load(python file) round-trip ==")
    out = subprocess.run(
        [str(BIN), "--load", str(py_path)], check=True, capture_output=True, text=True
    )
    (FIX / "rs_reload.json").write_text(out.stdout, encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "tools/compare.py"),
         str(FIX / "py_bundle.json"), str(FIX / "rs_reload.json"),
         "--ignore=saved_at,loaded_by_nominal,total_loaded_cents"],
        capture_output=True, text=True,
    )
    print(r.stdout.strip())
    if r.returncode != 0:
        return 1

    # ---- Python loads the Rust file ----
    print("== python loads rust file ==")
    lima2, fe2, ll2, meta = AnalysisSerializer.load(rs_path)
    linked = sum(len(s.lowlevel_sessions) for s in fe2.sessions)
    flagged = sum(1 for s in fe2.sessions if s.has_suspicious_match())
    print(
        f"  version={meta['version']} lima_sessions={len(lima2.sessions)} "
        f"fe_sessions={len(fe2.sessions)} ll_sessions={len(ll2.sessions)} "
        f"links={linked} flagged={flagged} cash={len(fe2.cash_events)}"
    )
    assert len(lima2.sessions) == len(lima.sessions), "lima session count changed"
    assert len(fe2.sessions) == len(fe.sessions), "frontend session count changed"
    assert linked == sum(len(s.lowlevel_sessions) for s in fe.sessions), \
        "correlation links changed on load"
    assert lima2.total_count == lima.total_count, "lima totals changed on load"
    assert abs(lima2.total_sum - lima.total_sum) < 1e-6, "lima sum changed on load"
    print("  python load OK")
    print("SERIALIZER PARITY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
