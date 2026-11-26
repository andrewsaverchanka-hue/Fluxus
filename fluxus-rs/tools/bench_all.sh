#!/usr/bin/env bash
# Reproduce the migration benchmark: Python reference vs Rust core.
set -euo pipefail
cd "$(dirname "$0")/.."

LOG=${1:-fixtures/big.log}
if [ ! -f "$LOG" ]; then
  echo "generating $LOG (400k lines)..."
  mkdir -p fixtures
  python3 tools/gen_logs.py "$LOG" --lines 400000 --seed 42
fi

cargo build --release >/dev/null

echo "== Rust (parse only, best of 3) =="
for _ in 1 2 3; do ./target/release/fluxus-parse "$LOG" --bench | tail -1; done

echo "== Python (parse only, best of 3) =="
python3 tools/bench.py "$LOG"

echo "== Rust via PyO3 (end-to-end to Python dict, summary mode) =="
PYTHONPATH=py python3 - "$LOG" <<'PY'
import sys, time, json, fluxus_native
t = time.perf_counter()
s = fluxus_native.parse_lima_json(sys.argv[1], False)
d = json.loads(s)
print(f"total={((time.perf_counter()-t)*1000):.0f}ms sessions={len(d['sessions'])}")
PY
