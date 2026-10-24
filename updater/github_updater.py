"""Проверка обновлений через GitHub Releases API + скачивание и установка."""

import re
import sys
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
# Установка — создание bat-скрипта и запуск
# ============================================================================

def prepare_installation(new_exe_path: Path) -> Optional[Path]:
    """Создаёт bat-скрипт для замены Fluxus.exe и перезапуска.

    Логика скрипта:
      1. Ждёт 2 секунды (чтобы текущий .exe успел закрыться)
      2. С повторными попытками (до 5 раз) перемещает новый .exe поверх старого
      3. Запускает обновлённый .exe
      4. Удаляет сам себя

    Returns:
        Path к bat-скрипту или None при ошибке.
    """
    if not is_frozen():
        print("[Updater] Установка возможна только в .exe-режиме")
        return None

    current_exe = Path(sys.executable).resolve()
    bat_path = UPDATE_TMP_DIR / "install_update.bat"

    bat_content = f'''@echo off
chcp 65001 >nul
echo Обновление Fluxus...
timeout /T 2 /NOBREAK >nul

:: Пробуем заменить файл с повторами (на случай блокировки .exe)
set RETRY=0
:RETRY
move /Y "{new_exe_path}" "{current_exe}" >nul 2>&1
if %ERRORLEVEL% EQU 0 goto SUCCESS
set /A RETRY=%RETRY%+1
if %RETRY% LSS 5 (
    timeout /T 1 /NOBREAK >nul
    goto RETRY
)
echo Не удалось обновить Fluxus.exe. Файл может быть заблокирован.
echo Закройте приложение и попробуйте снова.
pause
exit /B 1

:SUCCESS
echo Запуск обновлённого Fluxus...
start "" "{current_exe}"
del "%~f0"
'''

    try:
        with open(bat_path, 'w', encoding='utf-8') as f:
            f.write(bat_content)
        print(f"[Updater] Bat-скрипт создан: {bat_path}")
        return bat_path
    except Exception as e:
        print(f"[Updater] Ошибка создания bat-скрипта: {e}")
        return None


def install_update(new_exe_path: Path) -> bool:
    """Полный цикл установки:
      1. Создаёт bat-скрипт
      2. Запускает его в фоне (отдельный процесс)
      3. Приложение должно само завершиться после этого

    Returns:
        True если bat-скрипт успешно запущен (приложение должно завершиться),
        False при ошибке.
    """
    bat_path = prepare_installation(new_exe_path)
    if bat_path is None:
        return False

    try:
        # Запускаем bat в отдельной консоли, отвязанной от нашего процесса
        subprocess.Popen(
            ['cmd.exe', '/c', str(bat_path)],
            creationflags=(
                subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
            ),
            close_fds=True,
        )
        print("[Updater] Инсталлятор запущен")
        return True
    except Exception as e:
        print(f"[Updater] Не удалось запустить инсталлятор: {e}")
        return False