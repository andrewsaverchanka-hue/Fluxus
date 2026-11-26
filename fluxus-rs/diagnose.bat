@echo off
chcp 65001 >nul
setlocal EnableExtensions
cd /d "%~dp0"

set "OUT=%~dp0diagnostics.txt"
echo Collecting diagnostics, please wait...
call :report > "%OUT%" 2>&1

echo.
echo ============================================================
echo  Report saved to: %OUT%
echo  Showing it below. Copy all of this when asking for help.
echo ============================================================
echo.
type "%OUT%"
echo.
pause
exit /b 0

:report
set "PF=%ProgramFiles%"
set "PF86=%ProgramFiles(x86)%"

echo ============================================================
echo  Fluxus Rust core - diagnostics
echo ============================================================
echo.
echo --- Folder --------------------------------------------------
echo %CD%
echo.

echo --- Windows -------------------------------------------------
ver
echo.

echo --- Python --------------------------------------------------
where python 2>&1
python --version 2>&1
python -c "import struct;print('architecture:',struct.calcsize('P')*8,'-bit')" 2>&1
python -c "import sys;print('executable:',sys.executable)" 2>&1
python -c "import sys;print('version:',sys.version)" 2>&1
echo.

echo --- Rust ----------------------------------------------------
where rustc 2>&1
rustc -vV 2>&1
echo.
rustup show 2>&1
echo.

echo --- Visual Studio C++ toolchain -----------------------------
set "VSDIR="
if exist "%PF86%\Microsoft Visual Studio" for /f "delims=" %%y in ('dir /b /ad "%PF86%\Microsoft Visual Studio" 2^>nul') do call :check_vs "%PF86%\Microsoft Visual Studio\%%y"
if exist "%PF%\Microsoft Visual Studio" for /f "delims=" %%y in ('dir /b /ad "%PF%\Microsoft Visual Studio" 2^>nul') do call :check_vs "%PF%\Microsoft Visual Studio\%%y"

if exist "%PF86%\Microsoft Visual Studio\Installer\vswhere.exe" (
    echo vswhere is available
    for /f "usebackq delims=" %%i in (`"%PF86%\Microsoft Visual Studio\Installer\vswhere.exe" -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do echo vswhere reports C++ tools at: %%i
) else (
    echo vswhere.exe not present
)
echo.

if defined VSDIR (
    echo C++ build tools FOUND:
    echo   %VSDIR%
    echo.
    echo MSVC compiler versions:
    dir /b "%VSDIR%\VC\Tools\MSVC" 2>&1
) else (
    echo C++ build tools NOT FOUND in the standard folders.
    echo.
    echo NOTE: this check looks for a "VC\Tools\MSVC" folder inside
    echo       Visual Studio installations. If vswhere above reported a
    echo       path, the tools ARE installed and this line is a false alarm.
    echo.
    echo Otherwise install "Build Tools for Visual Studio" and tick
    echo the workload "Desktop development with C++".
    echo https://visualstudio.microsoft.com/visual-cpp-build-tools/
)
echo.

echo --- Windows SDK ---------------------------------------------
if exist "%PF86%\Windows Kits\10\Lib" (
    dir /b "%PF86%\Windows Kits\10\Lib" 2>&1
) else (
    echo Windows SDK NOT FOUND - it comes with the C++ workload.
)
echo.

echo --- Python-fluxus extension -------------------------------
if exist "..\fluxus_native.pyd" echo FOUND ..\fluxus_native.pyd
if exist "..\fluxus_adapter.py" echo FOUND ..\fluxus_adapter.py
echo.
echo --- End -----------------------------------------------------
exit /b 0

:check_vs
for %%e in (BuildTools Community Professional Enterprise) do (
    if exist "%~1\%%e\VC\Tools\MSVC" set "VSDIR=%~1\%%e"
)
exit /b 0
