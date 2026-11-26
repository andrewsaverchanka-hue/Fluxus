#!/usr/bin/env bash
# Validate the Rust port against real customer logs.
#
# Usage: tools/real_log_check.sh <devices_server.log[.gz]> <skills-battle-frontend.log[.gz]>
#
# Runs, for the given pair:
#   * low-level dispenser parity
#   * frontend parity
#   * correlation parity + link/flag summary
#   * .anl.gz bundle parity in both directions
# and reports Python vs Rust timings.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -lt 2 ]; then
  echo "usage: $0 <devices_server.log[.gz]> <skills-battle-frontend.log[.gz]>" >&2
  exit 2
fi
DEV=$1
FE=$2
OUT=fixtures/real
mkdir -p "$OUT"
BIN=./target/release/fluxus-parse
cargo build --release >/dev/null

echo "== lowlevel =="
t0=$(date +%s%N); python3 tools/reference.py "$DEV" --kind lowlevel > "$OUT/py_low.json"; t1=$(date +%s%N)
py_ms=$(( (t1 - t0) / 1000000 ))
$BIN "$DEV" --kind lowlevel --bench
$BIN "$DEV" --kind lowlevel > "$OUT/rs_low.json"
python3 tools/compare.py "$OUT/py_low.json" "$OUT/rs_low.json"

echo "== frontend =="
t0=$(date +%s%N); python3 tools/reference.py "$FE" --kind frontend > "$OUT/py_front.json"; t1=$(date +%s%N)
fe_py_ms=$(( (t1 - t0) / 1000000 ))
$BIN "$FE" --kind frontend --bench
$BIN "$FE" --kind frontend > "$OUT/rs_front.json"
python3 tools/compare.py "$OUT/py_front.json" "$OUT/rs_front.json" --ignore=loaded_by_nominal,total_loaded_cents

echo "== correlated =="
t0=$(date +%s%N); python3 tools/reference.py "$FE" --kind correlate --low "$DEV" > "$OUT/py_corr.json"; t1=$(date +%s%N)
corr_py_ms=$(( (t1 - t0) / 1000000 ))
PYTHONPATH=py python3 -c "
import fluxus_native, sys
open(sys.argv[3], 'w').write(
    fluxus_native.parse_w2_correlated(sys.argv[1], sys.argv[2], True))
" "$FE" "$DEV" "$OUT/rs_corr.json"
python3 tools/compare.py "$OUT/py_corr.json" "$OUT/rs_corr.json" --ignore=loaded_by_nominal,total_loaded_cents
python3 -c "
import json
d = json.load(open('$OUT/py_corr.json'))
fe, ll = d['frontend'], d['lowlevel']
linked = sum(1 for s in fe['sessions'] if s['lowlevel_sessions'])
flagged = sum(1 for s in fe['sessions'] if any(s['suspicion_flags']))
print(f'  frontend={len(fe[\"sessions\"])} lowlevel={len(ll[\"sessions\"])} '
      f'linked={linked} flagged={flagged}')
"

echo "== serializer =="
python3 tools/serializer_parity.py "$DEV" "$FE" "$DEV" | tail -4

echo "== timings (python vs rust) =="
echo "  lowlevel   python ${py_ms} ms"
echo "  frontend   python ${fe_py_ms} ms"
echo "  correlated python ${corr_py_ms} ms"
