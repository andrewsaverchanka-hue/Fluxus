@echo off
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================================
echo  Fluxus Rust core - Windows build
echo ============================================================
echo.

REM ===================== 1. Python ============================
python --version >nul 2>&1
if errorlevel 1 goto :no_python

for /f "delims=" %%v in ('python -c "import struct;print(struct.calcsize('P')*8)"') do set "ARCH=%%v"
if not "!ARCH!"=="64" goto :bad_arch

REM ===================== 2. Rust ==============================
cargo --version >nul 2>&1
if errorlevel 1 goto :no_rust

for /f "tokens=2" %%i in ('rustc -vV ^| findstr /b "host:"') do set "HOST=%%i"
echo Rust target: !HOST!
echo !HOST! | findstr /i "msvc" >nul
if errorlevel 1 goto :gnu_toolchain

REM ============ 3. Visual Studio C++ build tools ==============
set "PF=%ProgramFiles%"
set "PF86=%ProgramFiles(x86)%"

set "VSCPP="
if exist "!PF86!\Microsoft Visual Studio\Installer\vswhere.exe" (
    for /f "usebackq delims=" %%i in (`"!PF86!\Microsoft Visual Studio\Installer\vswhere.exe" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSCPP=%%i"
)
if not defined VSCPP if exist "!PF86!\Microsoft Visual Studio" for /f "delims=" %%y in ('dir /b /ad "!PF86!\Microsoft Visual Studio" 2^>nul') do call :scan_vs "!PF86!\Microsoft Visual Studio\%%y"
if not defined VSCPP if exist "!PF\Microsoft Visual Studio" for /f "delims=" %%y in ('dir /b /ad "!PF\Microsoft Visual Studio" 2^>nul') do call :scan_vs "!PF\Microsoft Visual Studio\%%y"

if not defined VSCPP goto :no_vs_tools
echo C++ tools:   !VSCPP!
echo.

REM ===================== 4. Build =============================
echo [1/3] Compiling the Rust parser (first run takes a few minutes)...
cargo build --release -p fluxus-py
if errorlevel 1 goto :build_failed

REM =============== 5. Install next to main.py =================
echo.
echo [2/3] Copying files into the Fluxus folder...
if not exist "..\main.py" goto :no_main
copy /Y "target\release\fluxus_native.dll" "..\fluxus_native.pyd" >nul
if errorlevel 1 goto :build_failed
copy /Y "py\fluxus_adapter.py" "..\fluxus_adapter.py" >nul
copy /Y "py\check_rust_core.py" "..\check_rust_core.py" >nul

echo.
echo [3/3] Verifying...
pushd ..
python check_rust_core.py
set "RESULT=!errorlevel!"
popd

echo.
if "!RESULT!"=="0" (
    echo ============================================================
    echo  SUCCESS. The Rust core is installed in your Fluxus folder.
    echo.
    echo  Last step: open main.py and add the block shown in
    echo  INTEGRATION_GUIDE.md, Step 4, ABOVE the line
    echo  "from gui.main_window import MainWindow".
    echo ============================================================
) else (
    echo ============================================================
    echo  Build finished, but verification failed - read the message
    echo  above, it names the problem.
    echo ============================================================
)
pause
exit /b 0

REM ===================== ERROR GUIDE ==========================
:no_python
echo [ERROR] Python was not found.
echo.
echo   Install Python 3.11 (64-bit) from python.org and tick
echo   "Add python.exe to PATH" during setup.
echo   Then close this window and run build.bat again.
pause
exit /b 1

:bad_arch
echo [ERROR] Your Python is !ARCH!-bit. 64-bit is required.
echo.
echo   Install the 64-bit Python 3.11 build.
pause
exit /b 1

:no_rust
echo [ERROR] Rust is not installed.
echo.
echo   1. Go to https://rustup.rs and run the installer.
echo   2. Accept the defaults.
echo   3. Close this window, open a NEW one, run build.bat again.
pause
exit /b 1

:gnu_toolchain
echo [ERROR] Rust is set up for the GNU target, not MSVC.
echo.
echo   Fix it by running:
echo       rustup default stable-msvc
echo   then run build.bat again.
pause
exit /b 1

:no_vs_tools
echo [ERROR] Visual Studio C++ build tools were not found.
echo         This is what "link.exe not found" means: Rust has no linker.
echo.
echo   Install "Build Tools for Visual Studio 2022" from:
echo     https://visualstudio.microsoft.com/visual-cpp-build-tools/
echo.
echo   In the installer, tick this workload:
echo     [x] Desktop development with C++
echo.
echo   Then check that the right-hand panel contains:
echo     [x] MSVC v143 - VS 2022 C++ x64/x86 build tools
echo     [x] Windows 11 SDK   (or Windows 10 SDK)
echo.
echo   IMPORTANT: "Visual Studio Code" is NOT Build Tools. If you only
echo   installed VS Code, install Build Tools as well.
echo.
echo   After installing, run build.bat again (no reboot needed).
echo.
echo   Alternative without installing anything: build the .pyd for free
echo   on GitHub - see INTEGRATION_GUIDE.md, "Build the .pyd in CI".
pause
exit /b 1

:build_failed
echo [ERROR] The build failed. The messages above are from the compiler.
echo.
echo   Run diagnose.bat and keep its output - it contains everything
echo   needed to identify the cause.
pause
exit /b 1

:no_main
echo [ERROR] Could not find ..\main.py
echo.
echo   The fluxus-rs folder must be INSIDE your Fluxus project folder,
echo   next to main.py:
echo.
echo     Fluxus\main.py
echo     Fluxus\fluxus-rs\build.bat     ^<-- run this
pause
exit /b 1

:scan_vs
for %%e in (BuildTools Community Professional Enterprise) do (
    if exist "%~1\%%e\VC\Tools\MSVC" set "VSCPP=%~1\%%e"
)
exit /b 0
