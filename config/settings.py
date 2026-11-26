"""Константы и пути приложения."""

import sys
import tempfile
from pathlib import Path

# ============================================================================
# ОПРЕДЕЛЕНИЕ БАЗОВЫХ ПУТЕЙ
# ============================================================================
# В dev-режиме (python main.py) PROJECT_ROOT — корень проекта.
# В .exe-режиме (PyInstaller --onefile) __file__ указывает на распакованную
# _MEI... папку в %TEMP%, которая удаляется при закрытии. Поэтому для
# пользовательских данных (config, downloaded_logs, update_tmp) используем
# директорию рядом с .exe — sys.executable.parent.

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _is_frozen() -> bool:
    """Собранный .exe: PyInstaller (sys.frozen) или Nuitka (__compiled__)."""
    return bool(
        getattr(sys, "frozen", False)
        or "__compiled__" in globals()
        or getattr(sys, "nuitka", False)
    )


def _app_dir() -> Path:
    """Каталог, где реально лежит Fluxus.exe.

    В Nuitka --onefile sys.executable указывает на временную распакованную
    копию в %TEMP%, которая удаляется при выходе. Если брать её, то
    config.json, downloaded_logs и update_tmp теряются при перезапуске,
    а BAT-установщик не находит скачанное обновление.
    Поэтому сначала пробуем sys.argv[0] — путь, которым запустили программу.
    """
    if not _is_frozen():
        return PROJECT_ROOT

    tmp_root = Path(tempfile.gettempdir()).resolve()

    def is_temp_path(path: Path) -> bool:
        try:
            path.relative_to(tmp_root)
            return True
        except ValueError:
            return any("onefile_" in part for part in path.parts)

    compiled = globals().get("__compiled__")
    candidates = []
    for value in (getattr(compiled, "original_argv0", None),
                  (sys.argv[0] if sys.argv else None),
                  sys.executable):
        if value:
            candidates.append(Path(value))

    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except Exception:
            continue
        if (resolved.suffix.lower() == ".exe"
                and resolved.parent.exists()
                and not is_temp_path(resolved)):
            return resolved.parent

    # ничего не нашли — последний вариант
    return Path(sys.executable).resolve().parent


USER_DATA_DIR = _app_dir()

# ============================================================================
# ПОЛЬЗОВАТЕЛЬСКИЕ ДИРЕКТОРИИ (рядом с .exe или в корне проекта)
# ============================================================================

# Директория для скачанных логов
DOWNLOADED_LOGS_DIR = USER_DATA_DIR / "downloaded_logs"
DOWNLOADED_LOGS_DIR.mkdir(exist_ok=True)

# Директория для временных файлов обновления (куда качается новый .exe)
UPDATE_TMP_DIR = USER_DATA_DIR / "update_tmp"

# Путь к пользовательскому конфигу
CONFIG_FILE = USER_DATA_DIR / "config.json"

# ============================================================================
# Остальное — без изменений
# ============================================================================

# Шлюз SSH — транзитный сервер, через который идём на устройства по VPN
SSH_GATEWAY = {
    "name": "Новый Сервер",
    "host": "45.82.66.80",
    "port": 22,
    "username": "support",
}

# Пути к файлам идентификации устройства
LIMA_KEY_PATH = "/opt/sftw/build/BACKSERVER/mc3_key.txt"
W2_CONFIG_PATH = "/root/configuration.yml"

# Паттерны логов на устройстве (для листинга и скачивания)
LOG_PATTERNS = {
    "devices_server": "/var/log/devices_server.log*",
    "frontend": "/var/log/skills-battle-frontend.log*",
}

# Таймауты SSH (секунды)
SSH_CONNECT_TIMEOUT = 15
SSH_COMMAND_TIMEOUT = 60
SSH_SFTP_TIMEOUT = 300

# Параметры GUI
WINDOW_DEFAULT_WIDTH = 1400
WINDOW_DEFAULT_HEIGHT = 900
WINDOW_MIN_WIDTH = 1100
WINDOW_MIN_HEIGHT = 700

SIDEBAR_WIDTH = 240

# =============================================================================
# GREP-паттерны для стриминга логов с устройства.
# =============================================================================

LOG_GREP_PATTERNS = {
    "devices": (
        'DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_'
        '|BanknoteValidator'
        '|COLLECTION REPORT'
        '|Print.*notes='
        '|Handle Dispense Request:'
        '|Dispense worker:'
        '|Dispense: '
        '|CommandDispense:'
        '|LCDM4000 CCTalk:'
        '|Request: Print'
        '|Withdraw report'
        '|Cash in'
        '|takenFromDispenserBoxes'
        '|takenFromRejectedBox'
        '|addedToDispenserBoxes'
        '|outByNotes'
        '|inByDatesAndNotes'
    ),

    "frontend": (
        'payoutCoupon'
        '|INSERT INTO terminal_'
        '|UPDATE terminal_'
        '|DispenserService.dispense'
        '|processNewEvent'
        '|cannotContinue'
        '|Error occurs while withdrawing'
        '|createCoupon'
        '|printSlip'
        '|Complete transaction'
        '|Redirect to HOME'
        '|COLLECTION REPORT'
        '|Withdraw report'
        '|Cash in'
        '|takenFromDispenserBoxes'
        '|takenFromRejectedBox'
        '|addedToDispenserBoxes'
        '|outByNotes'
        '|inByDatesAndNotes'
        '|INSERT INTO collection_'
    ),
}