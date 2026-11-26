#!/usr/bin/env bash
# Linux / macOS equivalent of build.bat: build the extension and copy it next
# to the Fluxus main.py (the parent folder of fluxus-rs).
set -euo pipefail
cd "$(dirname "$0")"

echo "== Fluxus Rust core build =="
python3 - <<'PY'
import platform, struct, sys
print("python", platform.python_version(), f"{struct.calcsize('P')*8}-bit")
if struct.calcsize("P") * 8 != 64:
    sys.exit("64-bit Python is required")
PY

cargo build --release -p fluxus-py

if [ ! -f ../main.py ]; then
  echo "ERROR: ../main.py not found - put fluxus-rs inside the Fluxus project" >&2
  exit 1
fi

case "$(uname -s)" in
  Darwin) LIB=target/release/libfluxus_native.dylib; EXT=pyd ;;
  *)      LIB=target/release/libfluxus_native.so;    EXT=so  ;;
esac

cp -f "$LIB" "../fluxus_native.$EXT"
cp -f py/fluxus_adapter.py ../fluxus_adapter.py
cp -f py/check_rust_core.py ../check_rust_core.py
echo "installed ../fluxus_native.$EXT"
(cd .. && python3 check_rust_core.py)
