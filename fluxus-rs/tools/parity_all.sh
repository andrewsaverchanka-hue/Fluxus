#!/usr/bin/env bash
# Verify every parser against the Python reference and run the adapter check.
set -euo pipefail
cd "$(dirname "$0")/.."

BIN=./target/release/fluxus-parse
cargo build --release >/dev/null

mkdir -p fixtures
[ -f fixtures/small.log ] || python3 tools/gen_logs.py fixtures/small.log --lines 20000 --seed 7
[ -f fixtures/front.log ] || python3 tools/gen_w2.py frontend fixtures/front.log --lines 4000
[ -f fixtures/low.log ] || python3 tools/gen_w2.py lowlevel fixtures/low.log --lines 4000

echo "== lima =="
python3 tools/reference.py fixtures/small.log > fixtures/py.json
$BIN fixtures/small.log > fixtures/rs.json
python3 tools/compare.py fixtures/py.json fixtures/rs.json

echo "== lima (gzip) =="
gzip -kf fixtures/small.log
$BIN fixtures/small.log.gz > fixtures/rs_gz.json
python3 tools/compare.py fixtures/py.json fixtures/rs_gz.json

echo "== frontend =="
python3 tools/reference.py fixtures/front.log --kind frontend > fixtures/py_front.json
$BIN fixtures/front.log --kind frontend > fixtures/rs_front.json
python3 tools/compare.py fixtures/py_front.json fixtures/rs_front.json --ignore=loaded_by_nominal,total_loaded_cents

echo "== lowlevel =="
python3 tools/reference.py fixtures/low.log --kind lowlevel > fixtures/py_low.json
$BIN fixtures/low.log --kind lowlevel > fixtures/rs_low.json
python3 tools/compare.py fixtures/py_low.json fixtures/rs_low.json

echo "== correlated =="
python3 tools/reference.py fixtures/front.log --kind correlate --low fixtures/low.log > fixtures/py_corr.json
PYTHONPATH=py python3 -c "
import fluxus_native
open('fixtures/rs_corr.json','w').write(
    fluxus_native.parse_w2_correlated('fixtures/front.log','fixtures/low.log',True))
"
python3 tools/compare.py fixtures/py_corr.json fixtures/rs_corr.json --ignore=loaded_by_nominal,total_loaded_cents

echo "== adapter =="
PYTHONPATH=py python3 tools/adapter_check.py fixtures/small.log fixtures/front.log fixtures/low.log

echo "== GUI access patterns (datetimes, numeric keys) =="
PYTHONPATH=py python3 tools/gui_smoke_check.py fixtures/small.log fixtures/front.log fixtures/low.log

echo "== serializer (both directions) =="
python3 tools/serializer_parity.py fixtures/small.log fixtures/front.log fixtures/low.log

echo "== serializer via adapter =="
PYTHONPATH=py python3 tools/serializer_adapter_check.py fixtures/small.log fixtures/front.log fixtures/low.log
