#!/usr/bin/env python3
"""Parse a log with the Python implementation and report timing (parse only)."""
import sys, time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "fluxus-py"))
from parsers.lima_parser import LogAnalyzer

path = sys.argv[1]
best = None
for _ in range(3):
    a = LogAnalyzer()
    t = time.perf_counter()
    a.parse_log_file(path)
    a.build_sessions()
    dt = time.perf_counter() - t
    best = dt if best is None else min(best, dt)
    last = (a.lines_found, len(a.sessions))
print(f"{path} lines_found={last[0]} sessions={last[1]} elapsed_ms={int(best*1000)}")
