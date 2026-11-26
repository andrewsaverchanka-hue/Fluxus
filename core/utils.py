"""Вспомогательные функции."""

import re
import gzip
from pathlib import Path

from core.constants import LCDM_STATUS_CODES, LCDM4000_DEVICE_CODES


def strip_ansi(text: str) -> str:
    """Удаляет ANSI-escape последовательности (цветовые коды) из текста."""
    return re.sub(r'\x1b\[[0-9;]*m', '', text)


def classify_dispense_error(status_detail: str | None) -> str:
    """Классифицирует текст ошибки диспенсера в категорию FAILED_*."""
    if not status_detail:
        return 'FAILED_HARDWARE'
    sd = status_detail.lower()
    if 'jam' in sd:
        return 'FAILED_JAM'
    if 'bill-end' in sd or 'bill end' in sd or 'empty' in sd:
        return 'FAILED_EMPTY'
    if 'timeout' in sd:
        return 'FAILED_TIMEOUT'
    return 'FAILED_HARDWARE'


def classify_lcdm_status(status_code: int) -> tuple[str, str]:
    """По коду статуса LCDM2000 возвращает (описание, категория статуса)."""
    if status_code in LCDM_STATUS_CODES:
        return LCDM_STATUS_CODES[status_code]
    return (f'Unknown 0x{status_code:02X}', 'FAILED_HARDWARE')


def classify_lcdm4000_code(device_code: int,
                           description: str | None = None) -> tuple[str, str]:
    """По deviceCode LCDM4000 возвращает (описание, категория статуса)."""
    if device_code in LCDM4000_DEVICE_CODES:
        return LCDM4000_DEVICE_CODES[device_code]
    if description:
        return (description, classify_dispense_error(description))
    return (f'Unknown code {device_code}', 'FAILED_HARDWARE')


def open_log_file(file_path: Path | str, mode: str = 'rt',
                  encoding: str = 'utf-8', errors: str = 'ignore'):
    """
    Универсальный open — умеет работать и с обычными .log, и с .gz.
    Возвращает context manager, пригодный для `with ... as f`.
    """
    path_str = str(file_path).lower()
    if path_str.endswith('.gz'):
        return gzip.open(file_path, mode, encoding=encoding, errors=errors)
    return open(file_path, mode, encoding=encoding, errors=errors)


def detect_log_type(file_path: Path | str) -> str:
    """
    Определяет тип лога по первым 8 КБ:
    - 'frontend' — лог skills-battle-frontend (метки вида [10/11/2026, 4:28:25 PM])
    - 'devices' — лог devices_server (метки вида "15/10 14:11:07 DBG ...")
    - 'unknown' — не удалось определить
    Умеет читать .gz напрямую, убирает ANSI-коды перед проверкой.
    """
    try:
        with open_log_file(file_path) as f:
            head = f.read(8192)
        head = strip_ansi(head)
        if re.search(r'\[\d{1,2}/\d{1,2}/\d{4},\s*\d{1,2}:\d{2}:\d{2}\s*(AM|PM)\]', head):
            return 'frontend'
        if re.search(r'\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}\s+DBG', head):
            return 'devices'
        # Fallback по характерным ключевым словам devices-лога
        if 'DevicesServer' in head or 'LCDM' in head or 'serialPath=' in head:
            return 'devices'
        return 'unknown'
    except Exception:
        return 'unknown'


def safe_filename(value: str, max_length: int = 64) -> str:
    """Приводит произвольную строку к безопасному имени файла/папки."""
    if not value:
        return "unknown"
    # Убираем всё, кроме букв/цифр/дефиса/подчёркивания
    cleaned = re.sub(r'[^\w\-]', '_', value).strip('_')
    # Убираем дубликаты подчёркиваний
    cleaned = re.sub(r'_+', '_', cleaned)
    return cleaned[:max_length] or "unknown"