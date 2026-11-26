# Putting the Rust core into your Fluxus project

This guide assumes you have never built Rust code before. Follow it in order.
Total time: about 20 minutes the first time.

---

## What you are adding

Your Fluxus app is written in **Python**. Python is slow at reading huge log
files, which is why parsing takes minutes.

We are adding a **second engine**, written in **Rust**, that does only the
parsing. Python keeps doing everything else — the window, buttons, tables,
SSH, updates.

The two talk through one file: `fluxus_native.pyd` (on Windows). Python calls
into it like a normal module.

```
main.py  →  fluxus_adapter.py  →  fluxus_native.pyd  →  Rust parser
   (your app)   (translator)        (compiled engine)
```

You do **not** need to change any GUI file. The adapter has the same class
names and methods your app already calls (`LogAnalyzer`,
`W2FrontendAnalyzer`, `W2LowLevelAnalyzer`, `AnalysisSerializer`).

---

## Before you start

1. **Make a backup** of the Fluxus folder, or use git:
   ```
   git checkout -b rust-core
   ```
2. Check your Python version — it must be **64-bit** and **3.11 or newer**:
   ```
   python -c "import sys, struct; print(sys.version, struct.calcsize('P')*8)"
   ```
   It should print something like `3.11.x … 64` or `3.14.x … 64`.
   The extension is built with the stable ABI, so one file works on 3.11
   through 3.14 — you don't have to match the version used to package the app.
3. Keep the `fluxus-rs` folder inside your project:
   ```
   Fluxus\
     main.py
     gui\
     parsers\
     fluxus-rs\        <- the Rust project
   ```

> The `fluxus_native.so` file in the archive is a **Linux** build. It will not
> work on Windows. You must build your own `.pyd` (steps 1–2).

---

## Step 1 — Install the build tools (once)

You need two things:

**a) Rust.** Go to <https://rustup.rs> and run the installer.
When it asks, accept the default. It uses the "MSVC" toolchain, which is what
we want on Windows.

**b) Visual Studio Build Tools.** Rust needs a C++ linker.
Download "Build Tools for Visual Studio 2022" from Microsoft and install the
workload **"Desktop development with C++"**.

To check both are installed, open a **new** Command Prompt and run:

```
rustc --version
cargo --version
```

Both should print a version. If `rustc` is "not recognized", close and reopen
the terminal (the PATH was updated by the installer).

---

## Step 2 — Build the engine

### Option A: double-click `build.bat` (easiest)

Open the `fluxus-rs` folder and double-click **`build.bat`**.

It checks your tools, compiles the engine, copies `fluxus_native.pyd`,
`fluxus_adapter.py` and `check_rust_core.py` into the Fluxus folder, and runs
the verification for you. Then skip to Step 4.

If it reports a problem, the message says which one (missing Rust, missing
Build Tools, wrong Python architecture) and how to fix it. If you need more
detail, run **`diagnose.bat`** — it prints the Rust target, the detected Visual
Studio installation and the Python used.

### Option B: build manually with maturin

In Command Prompt:

```
cd C:\path\to\Fluxus\fluxus-rs
pip install maturin
maturin build --release -p fluxus-py
```

The first build takes a few minutes. When it finishes you will see a line like:

```
Built wheel for fluxus-py to target\wheels\fluxus_py-0.1.0-cp311-cp311-win_amd64.whl
```

Now unpack that wheel to get the `.pyd` file:

```
cd target\wheels
python -m zipfile -e fluxus_py-0.1.0-cp311-cp311-win_amd64.whl extracted
dir extracted
```

You will see `fluxus_native.cp311-win_amd64.pyd`. That is your engine.

> **Wheel name.** It looks like `fluxus_py-0.1.0-cp311-abi3-win_amd64.whl`.
> `abi3` means it works on every Python 3.11+ — including 3.14 — which is why
> you do not need to match the packaged app's version exactly.

### Fixing "link.exe not found"

This is the most common first-build failure. It means Rust is installed but the
C++ linker is not. Run **`diagnose.bat`** first — it prints exactly which piece
is missing.

> **Run it from a Command Prompt, not by double-clicking.** Open Start, type
> `cmd`, press Enter, then:
> ```
> cd /d C:\path\to\Fluxus\fluxus-rs
> diagnose.bat
> ```
> Double-clicking works too, but if the window still closes, the report is
> saved as `fluxus-rs\diagnostics.txt` — open that file.

**Option 1 — install the C++ build tools (normal fix)**

1. Download **Build Tools for Visual Studio 2022**:
   <https://visualstudio.microsoft.com/visual-cpp-build-tools/>
2. Run the installer and tick the workload **"Desktop development with C++"**.
3. On the right-hand side make sure these two are ticked:
   - `MSVC v143 - VS 2022 C++ x64/x86 build tools`
   - `Windows 11 SDK` (or `Windows 10 SDK`)
4. Install (about 3–6 GB), then run `build.bat` again. No reboot needed.
5. Run `build.bat` again.

> **"Visual Studio Code" is not "Build Tools".** If you installed VS Code,
> that does not help — they are different products with confusingly similar
> names.

**Option 2 — Rust is using the wrong toolchain**

If `diagnose.bat` shows a host line ending in `-gnu` instead of `-msvc`, run:

```
rustup default stable-msvc
```

then `build.bat` again.

**Option 3 — no admin rights, or you don't want a 4 GB install**

Build the `.pyd` for free on GitHub instead (about 3 minutes, one time):

1. Put the `fluxus-rs` folder in your repository and commit it.
2. Copy `fluxus-rs\ci\build-pyd-artifact.yml` to
   `.github/workflows/build-pyd.yml` and commit that too.
3. On GitHub open **Actions → "Build Rust core (.pyd)" → Run workflow**.
4. When it turns green, open the run and download the artifact
   **`fluxus-native-win64-cp311`** (a zip).
5. Unzip it and put `fluxus_native.pyd` and `fluxus_adapter.py` next to your
   `main.py`. That is exactly what `build.bat` would have produced.

From then on, every push that touches `fluxus-rs` rebuilds the `.pyd`
automatically.

---

## Step 3 — Copy two files into the Fluxus folder

Copy both files **next to `main.py`**:

| From | To |
|---|---|
| `fluxus-rs\target\wheels\extracted\fluxus_native.cp311-win_amd64.pyd` | `Fluxus\fluxus_native.pyd` (rename it) |
| `fluxus-rs\py\fluxus_adapter.py` | `Fluxus\fluxus_adapter.py` |

Your folder should now look like:

```
Fluxus\
  main.py
  fluxus_adapter.py
  fluxus_native.pyd
  gui\
  parsers\
  fluxus-rs\
```

---

## Step 4 — Turn it on in `main.py`

This is the only code change, and it is four lines.

**Important:** the block must be *above* `from gui.main_window import MainWindow`,
because the GUI grabs the parser classes when it is imported.

Open `main.py` and make it look like this (only the marked part is new):

```python
"""Точка входа приложения Fluxus."""

import os                      # <-- add this line
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from PySide6.QtCore import Qt, QTranslator, QLibraryInfo, QLocale

sys.path.insert(0, str(Path(__file__).parent))

# ==================== Rust core (new) ====================
if os.environ.get("FLUXUS_PYTHON_PARSERS") != "1":
    try:
        from fluxus_adapter import install as _install_rust_core
        _install_rust_core()
        print("[Fluxus] Rust core enabled")
    except Exception as _error:
        print(f"[Fluxus] Rust core disabled, using Python parsers: {_error}")
# =========================================================

from version import APP_NAME, VERSION
from gui.main_window import MainWindow      # <-- must stay BELOW the block
from gui.theme import apply_dark_theme
```

Nothing else in `main.py` changes.

---

## Step 5 — Test it

**First, the quick check.** From the Fluxus folder:

```
python check_rust_core.py
```

Expected output ends with:

```
RESULT: READY — the Rust core is installed correctly
```

You can also make it parse a real log and time it:

```
python check_rust_core.py "C:\logs\devices_server.log"
```

**Then run the app:**

```
python main.py
```

Look at the console output. You should see:

```
[Fluxus] Rust core enabled
```

If you see `Rust core disabled, using Python parsers: ...`, the message after
the colon tells you what is missing — see Troubleshooting.

**Then check the result is correct.** Open a device with logs in the app and
compare the numbers (banknotes, sums, sessions) with what you saw before. They
must be identical. If they are not, run with `set FLUXUS_PYTHON_PARSERS=1`
before `python main.py` to switch back and compare.

---

## Step 6 — Build the `.exe`

Your existing PyInstaller command needs two extra flags so the `.pyd` is packed
into the executable:

```
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

The two new lines are `--hidden-import fluxus_native` and
`--collect-binaries fluxus_native`. Without them the `.exe` runs but silently
falls back to the slow Python parser.

Test the `.exe` on a machine that does **not** have Rust installed. If it shows
`[Fluxus] Rust core enabled`, the packaging is correct.

---

## Step 7 (recommended) — Let GitHub build it

If you already release through GitHub Actions, you do not need Rust on your PC
at all. Your workflow already runs on `windows-latest`.

Add these steps **before** the PyInstaller step in `.github/workflows/build.yml`:

```yaml
      - name: Install Rust
        uses: dtolnay/rust-toolchain@stable

      - name: Build Rust core
        working-directory: fluxus-rs
        run: |
          pip install maturin
          maturin build --release -p fluxus-py
          $whl = Get-ChildItem target/wheels/*.whl | Select-Object -First 1
          pip install $whl.FullName
```

And add the same two PyInstaller flags shown in Step 6. Then commit the
`fluxus-rs` folder to the repository so the workflow can see it.

---

## Troubleshooting

| What you see | What it means | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'fluxus_native'` | The `.pyd` is not next to `main.py`, or was built for another Python | Copy it into the Fluxus folder; check `python check_rust_core.py` |
| `ImportError: DLL load failed while importing fluxus_native` | Windows is missing the C++ runtime | Install "Microsoft Visual C++ Redistributable 2015–2022 (x64)" |
| `error: linker link.exe not found` | Rust has no C++ linker | See **"Fixing link.exe not found"** below |
| `'maturin' is not recognized` | Not installed for this Python | `python -m pip install maturin` |
| `ModuleNotFoundError` for `fluxus_native` after upgrading Python | Old build was version-specific | Rebuild — current builds use `abi3` and work on 3.11+ |
| App works but is still slow | The `.pyd` is not being used | Look for `[Fluxus] Rust core enabled` in the console; for the `.exe`, add `--collect-binaries` |
| `Rust core disabled: ... cannot import ...` | Something above | The message names the actual error — read it |
| Numbers differ from before | Should not happen | Run with `FLUXUS_PYTHON_PARSERS=1`, compare, and send the log |
| Analysis window does not open; `AttributeError: 'str' object has no attribute 'strftime'` | Old `fluxus_adapter.py` that didn't convert timestamps to `datetime` | Replace `fluxus_adapter.py` with the current one and rebuild (`build.bat`) |
| Window opens but BNV section is empty | Old build without `bnv_events` in the result | Rebuild the `.pyd` (`build.bat`) |

---

## How to switch it off

You can always go back, three ways:

1. **Temporary:** set an environment variable before starting the app:
   ```
   set FLUXUS_PYTHON_PARSERS=1
   python main.py
   ```
2. **Permanent:** delete the `# ==== Rust core ====` block from `main.py`.
3. **Full removal:** delete `fluxus_native.pyd` and `fluxus_adapter.py`.

The Python parsers are still in the project and still work. Nothing is lost.

---

## Questions people ask

**Do I have to change the GUI?**
No. `fluxus_adapter.py` provides the same classes and methods, so
`gui/pages/parser_page.py` and the results window keep working unchanged.

**Will the results be the same?**
Yes — I verified it on your synthetic fixtures *and* on your real logs:
W2 lowlevel (4 rotations), frontend (2 logs), correlated runs, Lima (7
rotations), and `.anl.gz` bundles in both directions. All identical.

**What actually gets faster?**
Parsing. On your real logs: the 13 rotated W2 device logs went from about
2 minutes to about 20 seconds; a Lima terminal's 7 logs from 30 s to 8 s.

**Does it change the `.exe` size much?**
Barely. The Rust part is about 2.5 MB; PyInstaller and PySide6 dominate.

**Do I need Rust on customer machines?**
No. Rust is only needed to *build* the `.pyd`. Customers get the `.exe`.

**Is it safe if something is wrong?**
Yes — the `try/except` means a missing or broken `.pyd` falls back to the old
Python parser instead of crashing the app.
