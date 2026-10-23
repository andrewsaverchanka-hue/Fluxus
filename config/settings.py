"""Константы и пути приложения."""

from pathlib import Path

# Корень проекта
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Директория для скачанных логов
DOWNLOADED_LOGS_DIR = PROJECT_ROOT / "downloaded_logs"
DOWNLOADED_LOGS_DIR.mkdir(exist_ok=True)

# Директория для временных файлов обновления
UPDATE_TMP_DIR = PROJECT_ROOT / "update_tmp"

# Путь к пользовательскому конфигу
CONFIG_FILE = PROJECT_ROOT / "config.json"

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
#
# Используются в режиме «🌊 Открыть логи на устройстве». grep/zgrep фильтрует
# на устройстве только нужные парсерам строки, уменьшая трафик по сети в 4-10 раз.
#
# Паттерны объединены через |, передаются в grep -E (extended regex).
# Все паттерны парсятся парсерами из одной строки, многострочный контекст
# не требуется (поэтому grep -A N не нужен).
# =============================================================================

LOG_GREP_PATTERNS = {
    # devices_server-лог: Lima (BNV + stacked), LCDM2000, LCDM4000, Health,
    # а также W2-инкассации (COLLECTION/Withdraw/Cash in — ловятся по rawTemplate).
    "devices": (
        'DEVICE_EVENT_TYPE_BILL_ACCEPTOR_BANKNOTE_'
        '|BanknoteValidator'
        # ===== Lima-инкассации =====
        '|COLLECTION REPORT'
        '|Print.*notes='
        # ===== W2 Low-Level (LCDM2000/LCDM4000) =====
        '|Handle Dispense Request:'
        '|Dispense worker:'
        '|Dispense: '
        '|CommandDispense:'
        '|LCDM4000 CCTalk:'
        # ===== W2-инкассации в devices_server-логе =====
        # Ловим Print-команду принтера с rawTemplate.
        # Три типа rawTemplate: COLLECTION REPORT / Withdraw report / Cash in.
        '|Request: Print'
        '|Withdraw report'
        '|Cash in'
        '|takenFromDispenserBoxes'
        '|takenFromRejectedBox'
        '|addedToDispenserBoxes'
        '|outByNotes'
        '|inByDatesAndNotes'
    ),

    # skills-battle-frontend-лог: W2 Frontend + инкассации
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
        # ===== W2-инкассации в frontend-логе =====
        # Эти строки нужны для парсинга boxes_delta и reject_delta.
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