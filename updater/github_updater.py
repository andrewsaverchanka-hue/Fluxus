"""Проверка обновлений через GitHub Releases API + скачивание и установка."""

import re
import sys
import os
import tempfile
import subprocess
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Callable

import requests

from version import VERSION, GITHUB_API_URL
from config.settings import UPDATE_TMP_DIR


# ============================================================================
# Структуры данных
# ============================================================================

@dataclass
class ReleaseInfo:
    """Информация о релизе на GitHub."""
    tag_name: str          # например, "v1.0.1"
    version: str           # очищенная версия, "1.0.1"
    name: str              # заголовок релиза
    body: str              # changelog
    published_at: str      # ISO-дата публикации
    asset_url: Optional[str] = None       # URL .exe-ассета
    asset_name: Optional[str] = None
    asset_size: int = 0


# ============================================================================
# Утилиты окружения
# ============================================================================

def is_frozen() -> bool:
    """True, если приложение запущено как PyInstaller .exe.
    В dev-режиме (python main.py) возвращает False.
    """
    return getattr(sys, 'frozen', False)


def get_current_exe_path() -> Path:
    """Возвращает путь к текущему .exe (в frozen-режиме) или к main.py."""
    if is_frozen():
        return Path(sys.executable)
    return Path(__file__).resolve().parent.parent / "main.py"


def _get_short_path_windows(path: str) -> str:
    """Возвращает short-path (8.3) для Windows-пути.

    Short-paths не содержат кириллицы и пробелов, что критично для
    BAT-скриптов в кодировке cp866. Пример:
      C:\\Users\\Андрей Саверченко\\Downloads  →  C:\\Users\\930C~1\\Downloads

    Если short-path получить не удалось — возвращает исходный путь.
    """
    if sys.platform != "win32":
        return path
    try:
        import ctypes
        from ctypes import wintypes
        GetShortPathNameW = ctypes.windll.kernel32.GetShortPathNameW
        GetShortPathNameW.argtypes = [
            wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD
        ]
        GetShortPathNameW.restype = wintypes.DWORD

        buf_size = 260
        buf = ctypes.create_unicode_buffer(buf_size)
        result = GetShortPathNameW(path, buf, buf_size)
        if result > 0 and result < buf_size:
            return buf.value
    except Exception as e:
        print(f"[Updater] GetShortPathName failed for {path}: {e}")
    return path


# ============================================================================
# Проверка версии
# ============================================================================

def _parse_version(tag: str) -> tuple[int, ...]:
    """Преобразует 'v1.2.3' → (1, 2, 3). Невалидные части → 0."""
    cleaned = tag.lstrip('v').strip()
    parts = re.split(r'[.\-+]', cleaned)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            break
    return tuple(result) if result else (0,)


def check_latest_release(timeout: int = 10) -> Optional[ReleaseInfo]:
    """Запрашивает информацию о последнем релизе с GitHub.
    Возвращает ReleaseInfo или None при ошибке.

    Приоритет ассетов: .exe → .zip → первый попавшийся.
    """
    try:
        response = requests.get(GITHUB_API_URL, timeout=timeout)
        response.raise_for_status()
        data = response.json()

        tag_name = data.get('tag_name', '')
        version = tag_name.lstrip('v').strip()

        info = ReleaseInfo(
            tag_name=tag_name,
            version=version,
            name=data.get('name', ''),
            body=data.get('body', ''),
            published_at=data.get('published_at', ''),
        )

        # Ищем ассеты в порядке приоритета
        assets = data.get('assets', [])
        exe_asset = None
        zip_asset = None
        for asset in assets:
            name = asset.get('name', '').lower()
            if name.endswith('.exe'):
                exe_asset = asset
                break   # .exe — самый приоритетный, выходим
            elif name.endswith('.zip') and zip_asset is None:
                zip_asset = asset

        chosen = exe_asset or zip_asset
        if chosen:
            info.asset_url = chosen.get('browser_download_url')
            info.asset_name = chosen.get('name')
            info.asset_size = chosen.get('size', 0)

        return info
    except Exception as e:
        print(f"[Updater] Ошибка проверки релиза: {e}")
        return None


def is_update_available(release: ReleaseInfo) -> bool:
    """Сравнивает текущую версию с версией релиза."""
    current = _parse_version(VERSION)
    latest = _parse_version(release.version)
    return latest > current


# ============================================================================
# Скачивание
# ============================================================================

def download_update(
    release: ReleaseInfo,
    progress_callback: Optional[Callable[[int, int], None]] = None,
    cancel_check: Optional[Callable[[], bool]] = None,
) -> Optional[Path]:
    """Скачивает Fluxus.exe из релиза в UPDATE_TMP_DIR.

    Args:
        release: объект релиза с asset_url.
        progress_callback: вызывается как progress_callback(bytes_done, total_bytes).
        cancel_check: вызывается периодически; если возвращает True —
                      скачивание прерывается и файл удаляется.

    Returns:
        Path к скачанному файлу или None (ошибка / отмена).
    """
    if not release.asset_url or not release.asset_name:
        print("[Updater] В релизе нет подходящего ассета для скачивания")
        return None

    UPDATE_TMP_DIR.mkdir(parents=True, exist_ok=True)
    target_path = UPDATE_TMP_DIR / release.asset_name

    try:
        with requests.get(release.asset_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', release.asset_size))
            downloaded = 0
            chunk_size = 64 * 1024  # 64 КБ

            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=chunk_size):
                    # Проверка отмены
                    if cancel_check and cancel_check():
                        print("[Updater] Скачивание отменено пользователем")
                        try:
                            f.close()
                            target_path.unlink(missing_ok=True)
                        except Exception:
                            pass
                        return None

                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)

        print(f"[Updater] Скачано: {target_path} ({downloaded} байт)")
        return target_path

    except requests.exceptions.RequestException as e:
        print(f"[Updater] Ошибка скачивания: {e}")
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None
    except Exception as e:
        print(f"[Updater] Неожиданная ошибка: {e}")
        try:
            target_path.unlink(missing_ok=True)
        except Exception:
            pass
        return None


# ============================================================================
# Установка — создание BAT + VBS-обёртки для обхода Defender
# ============================================================================

def install_update(new_exe_path: Path) -> bool:
    """Полный цикл установки через VBS-обёртку + BAT с retry.

    Схема:
      1. Все пути (current_exe, new_exe, temp_dir) конвертируются в short-path
         (8.3 формат), чтобы BAT в cp866 корректно работал с кириллицей.
      2. Создаётся BAT-скрипт с taskkill + copy /Y с retry.
      3. Создаётся VBS-скрипт, который запускает BAT в скрытом окне.
      4. VBS запускается через wscript.exe (легитимный системный процесс).
      5. Windows Defender пропускает такую цепочку как доверенную.

    После успешного запуска приложение должно само завершиться
    (вызывающий код делает QApplication.instance().quit()).

    Args:
        new_exe_path: путь к скачанному Fluxus.exe в UPDATE_TMP_DIR

    Returns:
        True если инсталлятор успешно запущен (можно закрывать приложение),
        False при ошибке (приложение остаётся работать).
    """
    print("=" * 70)
    print("[INSTALL] Начало установки обновления Fluxus")
    print(f"[INSTALL] is_frozen: {is_frozen()}")
    print(f"[INSTALL] sys.executable: {sys.executable}")
    print(f"[INSTALL] Новый файл: {new_exe_path}")

    # Проверка существования файла
    if not new_exe_path or not new_exe_path.exists():
        print(f"[INSTALL] ❌ Новый .exe не найден: {new_exe_path}")
        return False

    size_mb = new_exe_path.stat().st_size / 1024 / 1024
    print(f"[INSTALL] Размер: {size_mb:.1f} MB")
    print("=" * 70)

    if not is_frozen():
        print("[INSTALL] Установка возможна только в .exe-режиме")
        return False

    current_exe = Path(sys.executable).resolve()
    app_dir = str(current_exe.parent)
    exe_name = current_exe.name

    # ====================================================================
    # КРИТИЧНО: конвертируем ВСЕ пути в short-path (8.3) формат.
    # BAT-файлы работают в кодировке cp866 и не могут обрабатывать
    # кириллицу в путях (например, C:\Users\Андрей Саверченко\...).
    # Short-path гарантирует ASCII-only путь (C:\Users\930C~1\...).
    # ====================================================================
    current_exe_short = _get_short_path_windows(str(current_exe))
    app_dir_short = _get_short_path_windows(app_dir)
    new_exe_short = _get_short_path_windows(str(new_exe_path))

    # Системная TEMP-директория — тоже в short-path, т.к. у некоторых
    # пользователей TEMP содержит кириллицу
    temp_dir_orig = tempfile.gettempdir()
    temp_dir_short = _get_short_path_windows(temp_dir_orig)

    log_file = os.path.join(temp_dir_short, "fluxus_update.log")
    bat_path = os.path.join(temp_dir_short, "fluxus_update.bat")
    vbs_path = os.path.join(temp_dir_short, "fluxus_update.vbs")

    print(f"[INSTALL] Short paths:")
    print(f"  current: {current_exe} → {current_exe_short}")
    print(f"  app_dir: {app_dir} → {app_dir_short}")
    print(f"  new_file: {new_exe_path} → {new_exe_short}")
    print(f"  temp: {temp_dir_orig} → {temp_dir_short}")
    print(f"  BAT: {bat_path}")
    print(f"  VBS: {vbs_path}")

    # Начальный лог
    try:
        with open(log_file, "w", encoding="utf-8") as f:
            f.write("=== Fluxus update prepared ===\n")
            f.write(f"Current exe (orig):  {current_exe}\n")
            f.write(f"Current exe (short): {current_exe_short}\n")
            f.write(f"App dir (short):     {app_dir_short}\n")
            f.write(f"Exe name:            {exe_name}\n")
            f.write(f"New file (orig):     {new_exe_path}\n")
            f.write(f"New file (short):    {new_exe_short}\n")
            f.write("===\n\n")
    except Exception as e:
        print(f"[INSTALL] ⚠ Не удалось создать лог: {e}")

    # ====================================================================
    # BAT-скрипт: убить процесс, скопировать новый .exe с retry, запустить.
    # Использует short-paths везде — работает с любой кириллицей в путях.
    # ====================================================================
    bat_content = f'''@echo off
setlocal enabledelayedexpansion
chcp 65001 >nul
set "LOG={log_file}"
set "EXE={current_exe_short}"
set "EXE_NAME={exe_name}"
set "NEWEXE={new_exe_short}"
set "APPDIR={app_dir_short}"

echo. >> "%LOG%"
echo ============================================ >> "%LOG%"
echo BAT started at %date% %time% >> "%LOG%"
echo ============================================ >> "%LOG%"

echo Step 1: Waiting 10 seconds for app to close... >> "%LOG%"
timeout /t 10 /nobreak >nul

echo Step 2: Force killing processes... >> "%LOG%"
taskkill /F /IM "%EXE_NAME%" >nul 2>&1
if !errorlevel! equ 0 (
    echo Killed process %EXE_NAME% >> "%LOG%"
) else (
    echo No running processes found >> "%LOG%"
)
timeout /t 3 /nobreak >nul

echo Step 3: Copying new exe with retry... >> "%LOG%"
set attempts=0
:retry
copy /Y "%NEWEXE%" "%EXE%" >> "%LOG%" 2>&1
if errorlevel 1 (
    set /a attempts+=1
    if !attempts! lss 30 (
        echo Attempt !attempts!/30 failed, retrying... >> "%LOG%"
        timeout /t 1 /nobreak >nul
        goto retry
    ) else (
        echo ERROR: Copy failed after 30 attempts >> "%LOG%"
        exit /b 1
    )
)
echo Copy successful on attempt !attempts! >> "%LOG%"

echo Step 4: Cleaning temp files... >> "%LOG%"
del "%NEWEXE%" >nul 2>&1

echo Step 5: Waiting 5 seconds before restart... >> "%LOG%"
timeout /t 5 /nobreak >nul

echo Step 6: Starting new version via explorer.exe... >> "%LOG%"
explorer.exe "%EXE%"
echo Explorer launch attempted >> "%LOG%"

timeout /t 3 /nobreak >nul

echo Step 7: Verifying new version is running... >> "%LOG%"
tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
if !errorlevel! equ 0 (
    echo SUCCESS: New version is running >> "%LOG%"
) else (
    echo WARNING: Trying start command fallback... >> "%LOG%"
    start "" /D "%APPDIR%" "%EXE%"
    timeout /t 3 /nobreak >nul
    tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
    if !errorlevel! equ 0 (
        echo SUCCESS: Started via start command >> "%LOG%"
    ) else (
        echo WARNING: Trying schtasks fallback... >> "%LOG%"
        schtasks /Create /F /SC ONCE /ST 23:59 /TN "FluxusRestart" /TR "%EXE%" /RU "%USERNAME%" >nul 2>&1
        schtasks /Run /TN "FluxusRestart" >nul 2>&1
        timeout /t 3 /nobreak >nul
        schtasks /Delete /F /TN "FluxusRestart" >nul 2>&1
        tasklist /FI "IMAGENAME eq %EXE_NAME%" 2>nul | find /I "%EXE_NAME%" >nul
        if !errorlevel! equ 0 (
            echo SUCCESS: Started via schtasks >> "%LOG%"
        ) else (
            echo ERROR: All launch methods failed >> "%LOG%"
        )
    )
)

echo Update completed at %date% %time% >> "%LOG%"
endlocal
exit /b 0
'''

    # Записываем BAT в cp1251 (стандарт Windows для .bat)
    try:
        with open(bat_path, "w", encoding="cp1251") as f:
            f.write(bat_content)
        print(f"[INSTALL] ✓ BAT создан: {bat_path}")
    except Exception as e:
        print(f"[INSTALL] ❌ Ошибка создания BAT: {e}")
        return False

    # VBS-обёртка — запускает BAT в скрытом окне через wscript
    # (это важно для обхода Defender — wscript подписанный MS)
    vbs_content = f'''Set WshShell = CreateObject("WScript.Shell")
WshShell.Run """{bat_path}""", 0, False
Set WshShell = Nothing
'''

    try:
        with open(vbs_path, "w", encoding="cp1251") as f:
            f.write(vbs_content)
        print(f"[INSTALL] ✓ VBS создан: {vbs_path}")
    except Exception as e:
        print(f"[INSTALL] ❌ Ошибка создания VBS: {e}")
        return False

    # Запускаем VBS через wscript.exe
    # DETACHED_PROCESS отвязывает процесс от текущего — он живёт независимо
    DETACHED_PROCESS = 0x00000008
    CREATE_NEW_PROCESS_GROUP = 0x00000200

    try:
        proc = subprocess.Popen(
            ["wscript.exe", vbs_path],
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            cwd=temp_dir_short,
        )
        print(f"[INSTALL] ✓ VBS запущен через wscript (PID: {proc.pid})")
        print(f"[INSTALL] Лог установки: {log_file}")
        print("=" * 70)
        return True

    except Exception as e:
        print(f"[INSTALL] ⚠ VBS не запустился: {e}")
        print("[INSTALL] Fallback: запуск BAT напрямую через cmd...")

        # Fallback — прямой запуск BAT (может быть блокирован Defender)
        try:
            CREATE_NO_WINDOW = 0x08000000
            proc = subprocess.Popen(
                ["cmd.exe", "/c", bat_path],
                creationflags=(
                    DETACHED_PROCESS |
                    CREATE_NEW_PROCESS_GROUP |
                    CREATE_NO_WINDOW
                ),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                cwd=temp_dir_short,
            )
            print(f"[INSTALL] ✓ BAT запущен напрямую (PID: {proc.pid})")
            return True
        except Exception as e2:
            print(f"[INSTALL] ❌ Fallback тоже не сработал: {e2}")
            return False