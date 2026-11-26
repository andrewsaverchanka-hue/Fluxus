# Building Fluxus with the Rust core on Windows

The hybrid layout means two artifacts ship together:

* `fluxus_native.cp311-win_amd64.pyd` — the compiled Rust parser (PyO3 extension)
* the PyInstaller `Fluxus.exe` — the existing PySide6 GUI, which imports it

PyInstaller still packages CPython. The win is that all parsing happens in
native code; the interpreter only runs the GUI.

## 1. Prerequisites (build machine)

| Tool | Notes |
|---|---|
| Rust stable, **MSVC** toolchain | `rustup default stable-msvc` (not GNU) |
| Visual Studio Build Tools 2022 | workload "Desktop development with C++" |
| Python **3.11, 64-bit** | must match the interpreter PyInstaller bundles |
| `pip install maturin` | builds the wheel |

Check the interpreter architecture before anything else:

```bat
python -c "import struct; print(struct.calcsize('P') * 8)"   :: must print 64
```

## 2. Build the extension

From the `fluxus-rs` directory:

```bat
maturin build --release -p fluxus-py
pip install --force-reinstall target\wheels\fluxus_py-0.1.0-cp311-cp311-win_amd64.whl
```

Or, inside an activated venv, `maturin develop --release -p fluxus-py`.

Verify:

```bat
python -c "import fluxus_native, sys; print(fluxus_native.__version__, sys.executable)"
```

The wheel tag used to be `cp311`; this project now builds with the **stable ABI**
(`abi3-py311`), so the tag is `cp311-abi3` and the same `.pyd` works on
**Python 3.11, 3.12, 3.13 and 3.14**. That matters here: the app is packaged
with 3.11 in CI, but a developer machine may have 3.14 — one build covers both.

If you ever need to change the minimum version, edit `crates/fluxus-py/Cargo.toml`:

```toml
pyo3 = { version = "0.30", features = ["extension-module", "abi3-py311"] }
```

## 3. Enable it in the app

`install()` patches the three analyzer classes. It must run **before** the GUI
modules are imported, because `gui/pages/parser_page.py` binds the classes at
import time:

```python
# main.py
import os
import sys

# --- Rust core (optional, falls back to pure Python) ---
if os.environ.get("FLUXUS_PYTHON_PARSERS") != "1":
    try:
        from fluxus_adapter import install as _install_rust_core
        _install_rust_core()
    except ImportError:
        pass  # extension not built: keep the Python parsers

from PySide6.QtWidgets import QApplication
from gui.main_window import MainWindow   # imported AFTER the patch
```

Keep `FLUXUS_PYTHON_PARSERS=1` as an escape hatch: it forces the old code path
if a customer log trips the Rust parser.

## 4. Package with PyInstaller

```bat
pyinstaller --onefile --windowed --name Fluxus ^
  --icon logo.png ^
  --add-data "assets;assets" ^
  --add-data "logo.png;." ^
  --add-data "logo_full.png;." ^
  --collect-all PySide6 ^
  --hidden-import PySide6.QtSvg ^
  --hidden-import PySide6.QtSvgWidgets ^
  --hidden-import PySide6.QtNetwork ^
  --hidden-import fluxus_native ^
  --collect-binaries fluxus_native ^
  main.py
```

`--collect-binaries fluxus_native` is the line that matters: without it the
`.pyd` is not copied next to the executable and the app silently falls back to
Python parsing (because of the `try/except ImportError` above).

Smoke-test on a clean machine — a missing MSVC runtime shows up as
`ImportError: DLL load failed`, which is why the fallback exists.

## 5. CI job (GitHub Actions)

```yaml
jobs:
  build-windows:
    runs-on: windows-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.11'
          cache: 'pip'
      - uses: dtolnay/rust-toolchain@stable
        with:
          targets: x86_64-pc-windows-msvc
      - name: Install deps
        run: |
          pip install -r requirements.txt pyinstaller maturin
      - name: Build Rust extension
        run: |
          maturin build --release -p fluxus-py
          pip install (Get-ChildItem target/wheels/*.whl | Select-Object -First 1).FullName
      - name: Verify extension imports
        run: python -c "import fluxus_native; print(fluxus_native.__version__)"
      - name: Build exe
        run: |
          pyinstaller --onefile --windowed --name Fluxus `
            --icon logo.png `
            --add-data "assets;assets" --add-data "logo.png;." `
            --add-data "logo_full.png;." `
            --collect-all PySide6 `
            --hidden-import PySide6.QtSvg --hidden-import PySide6.QtSvgWidgets `
            --hidden-import PySide6.QtNetwork `
            --hidden-import fluxus_native --collect-binaries fluxus_native `
            main.py
      - uses: softprops/action-gh-release@v2
        if: startsWith(github.ref, 'refs/tags/')
        with:
          files: dist/Fluxus.exe
```

Note the working directory: run the `maturin` steps from `fluxus-rs/` (add
`working-directory: fluxus-rs` to those steps) if the crate lives in a
subdirectory of the repo.

## 6. Signing (do this — it replaces the VBS/Defender hack)

```bat
signtool sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 ^
  /a dist\Fluxus.exe
```

Signing is what actually stops Defender from quarantining the app. With one
signed binary the whole `install_update` VBS → BAT → `schtasks` chain in
`updater/github_updater.py` can be deleted; a signed exe can safely replace
itself via a small helper process.

## 7. What to expect

| Item | Value |
|---|---|
| `fluxus_native.pyd` size | ~2.5 MB (release) |
| Lima parse, 400k-line log | ~2.6 s vs ~60 s in Python |
| PyInstaller exe size | roughly unchanged (parsing was not the bulk) |

If the exe is missing the extension at runtime, you will see correct results —
just at the old speed. Check with `fluxus_native.__version__` from the frozen
app's log, or add a startup print.
