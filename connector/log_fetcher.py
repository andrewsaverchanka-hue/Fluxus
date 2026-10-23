"""Листинг и скачивание логов с устройства по SFTP + стриминг через grep."""

import io
import os
import stat
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

from config.settings import LOG_PATTERNS, LOG_GREP_PATTERNS, SSH_SFTP_TIMEOUT
from connector.ssh_client import SSHClientWrapper


class LogFile:
    """Информация о логе на устройстве."""
    def __init__(self, remote_path: str, size: int, mtime: datetime):
        self.remote_path = remote_path
        self.size = size
        self.mtime = mtime
        self.filename = Path(remote_path).name

    def __repr__(self):
        return f"LogFile({self.filename}, {self.size}b, mtime={self.mtime})"

    @property
    def is_gzipped(self) -> bool:
        return self.remote_path.lower().endswith('.gz')

    @property
    def log_type(self) -> str:
        """Определяет тип лога по имени файла: 'devices' | 'frontend' | 'unknown'."""
        name = self.filename.lower()
        if 'skills-battle' in name:
            return 'frontend'
        if 'devices_server' in name:
            return 'devices'
        return 'unknown'


# =============================================================================
# ЛИСТИНГ
# =============================================================================

def list_logs(ssh: SSHClientWrapper, patterns: Optional[dict] = None) -> list[LogFile]:
    """Возвращает список лог-файлов на устройстве.

    Использует `sh -c` для гарантии glob-разворачивания и `stat` вместо
    `ls --time-style` — универсально работает и на GNU coreutils (Ubuntu),
    и на BusyBox (на случай embedded-устройств).

    Формат stat -c "%s %Y %n": размер mtime_unix путь.
    Если glob не находит совпадений, цикл `for` просто пустой — без ошибок.
    """
    if patterns is None:
        patterns = LOG_PATTERNS

    all_logs: list[LogFile] = []
    for category, pattern in patterns.items():
        cmd = (
            f"sh -c 'for f in {pattern}; do "
            f"[ -e \"$f\" ] && stat -c \"%s %Y %n\" \"$f\"; "
            f"done' 2>/dev/null"
        )
        out, _ = ssh.run_command(cmd, timeout=15)

        for line in out.splitlines():
            parts = line.strip().split(None, 2)
            if len(parts) < 3:
                continue
            try:
                size = int(parts[0])
                mtime_unix = int(parts[1])
                remote_path = parts[2]
                mtime = datetime.fromtimestamp(mtime_unix)
                all_logs.append(LogFile(remote_path, size, mtime))
            except (ValueError, IndexError):
                continue

    return all_logs


def filter_logs_by_hours(logs: list[LogFile], hours: int) -> list[LogFile]:
    """Фильтрует логи, отобранные за последние N часов."""
    cutoff = datetime.now() - timedelta(hours=hours)
    return [lf for lf in logs if lf.mtime >= cutoff]


def filter_logs_by_date_range(logs: list[LogFile],
                               start: datetime,
                               end: datetime) -> list[LogFile]:
    """Фильтрует логи по диапазону дат."""
    return [lf for lf in logs if start <= lf.mtime <= end]


# =============================================================================
# СКАЧИВАНИЕ (режим «📥 Скачать логи»)
# =============================================================================

def download_logs(ssh: SSHClientWrapper,
                   logs: list[LogFile],
                   target_folder: Path,
                   progress_callback=None) -> list[Path]:
    """Скачивает список логов в target_folder через SFTP.

    progress_callback(current_file: int, total_files: int, filename: str).
    Возвращает список локальных путей.
    """
    target_folder.mkdir(parents=True, exist_ok=True)
    sftp = ssh.open_sftp()
    sftp.get_channel().settimeout(SSH_SFTP_TIMEOUT)

    downloaded: list[Path] = []
    total = len(logs)
    try:
        for i, lf in enumerate(logs, 1):
            local_path = target_folder / lf.filename
            if progress_callback:
                progress_callback(i, total, lf.filename)
            try:
                sftp.get(lf.remote_path, str(local_path))
                downloaded.append(local_path)
            except Exception as e:
                print(f"[LogFetcher] Ошибка скачивания {lf.remote_path}: {e}")
    finally:
        sftp.close()
    return downloaded


# =============================================================================
# СТРИМИНГ (режим «🌊 Открыть логи на устройстве»)
# =============================================================================

class StreamError(Exception):
    """Ошибка стриминга лога с устройства."""
    def __init__(self, message: str, category: str = 'generic'):
        """
        Args:
            message: текст ошибки
            category: 'channel' | 'tool_missing' | 'empty' | 'generic'
                      Используется GUI для выбора правильного fallback-диалога.
        """
        super().__init__(message)
        self.category = category


def _check_tool_available(ssh: SSHClientWrapper, tool: str) -> bool:
    """Проверяет, доступна ли утилита на устройстве (zgrep, zcat и т.д.)."""
    try:
        out, _ = ssh.run_command(f"command -v {tool} 2>/dev/null", timeout=5)
        return bool(out.strip())
    except Exception:
        return False


def _build_grep_command(log_file: LogFile, pattern: str,
                        use_zcat_fallback: bool = False) -> str:
    """
    Собирает команду grep/zgrep для стриминга.

    Args:
        log_file: лог для стриминга
        pattern: regex-паттерн (объединённый через |)
        use_zcat_fallback: если True — использовать zcat | grep вместо zgrep

    Возвращает команду shell для exec_command.
    """
    # Экранируем одинарные кавычки в паттерне
    pattern_safe = pattern.replace("'", "'\\''")
    path_safe = log_file.remote_path.replace("'", "'\\''")

    if log_file.is_gzipped:
        if use_zcat_fallback:
            # Fallback: zcat | grep
            return f"zcat '{path_safe}' | grep -E -a '{pattern_safe}'"
        else:
            # Основной путь: zgrep
            return f"zgrep -E -a '{pattern_safe}' '{path_safe}'"
    else:
        # Обычный .log — просто grep
        return f"grep -E -a '{pattern_safe}' '{path_safe}'"


def stream_log_filtered(ssh: SSHClientWrapper, log_file: LogFile,
                         pattern_key: Optional[str] = None):
    """Открывает поток чтения лога с устройства с grep-фильтрацией.

    Returns:
        file-like объект (io.TextIOWrapper поверх paramiko channel),
        из которого парсер читает отфильтрованные строки.

    Raises:
        StreamError: если не удалось открыть поток. Атрибут .category:
            - 'tool_missing' — zgrep/zcat недоступны
            - 'channel'      — не удалось открыть SSH-канал
            - 'empty'        — grep вернул пустой результат
            - 'generic'      — другая ошибка

    Args:
        pattern_key: 'devices' | 'frontend' | None.
                     Если None — определяется автоматически по имени файла.
    """
    # 1. Определяем, какой паттерн использовать
    if pattern_key is None:
        pattern_key = log_file.log_type

    if pattern_key == 'unknown':
        # Для unknown-логов объединяем оба паттерна
        pattern = (
            LOG_GREP_PATTERNS['devices'] + '|' +
            LOG_GREP_PATTERNS['frontend']
        )
    elif pattern_key in LOG_GREP_PATTERNS:
        pattern = LOG_GREP_PATTERNS[pattern_key]
    else:
        raise StreamError(
            f"Неизвестный pattern_key: {pattern_key}",
            category='generic'
        )

    # 2. Проверяем доступность zgrep (только для .gz-файлов)
    use_fallback = False
    if log_file.is_gzipped:
        if not _check_tool_available(ssh, 'zgrep'):
            # zgrep недоступен — проверяем zcat как fallback
            if not _check_tool_available(ssh, 'zcat'):
                raise StreamError(
                    f"На устройстве недоступны ни zgrep, ни zcat. "
                    f"Невозможно стримить {log_file.filename} без распаковки.",
                    category='tool_missing'
                )
            use_fallback = True

    # 3. Собираем команду
    cmd = _build_grep_command(log_file, pattern, use_zcat_fallback=use_fallback)

    # 4. Открываем SSH-канал
    try:
        if ssh.target_client is None:
            raise StreamError("SSH-клиент не подключён", category='channel')
        _, stdout, stderr = ssh.target_client.exec_command(cmd, timeout=300)
    except Exception as e:
        raise StreamError(
            f"Не удалось открыть SSH-канал для {log_file.filename}: {e}",
            category='channel'
        )

    # 5. Оборачиваем stdout в текстовый поток
    try:
        text_stream = io.TextIOWrapper(stdout, encoding='utf-8', errors='ignore')
        return text_stream
    except Exception as e:
        raise StreamError(
            f"Не удалось создать text-stream для {log_file.filename}: {e}",
            category='generic'
        )


def stream_log_to_memory(ssh: SSHClientWrapper, log_file: LogFile,
                          pattern_key: Optional[str] = None) -> str:
    """Стримит лог и возвращает весь отфильтрованный текст как строку.

    Удобно для случаев, когда один devices-лог нужно пропустить через
    два парсера (LogAnalyzer + W2LowLevelAnalyzer) — текст буферизуется
    один раз в памяти, затем парсеры читают его через io.StringIO.

    Raises:
        StreamError: как в stream_log_filtered + 'empty' если grep ничего
                     не вернул (возможно, в файле нет нужных строк).
    """
    stream = stream_log_filtered(ssh, log_file, pattern_key)
    try:
        text = stream.read()
    except Exception as e:
        raise StreamError(
            f"Ошибка чтения потока {log_file.filename}: {e}",
            category='generic'
        )
    finally:
        try:
            stream.close()
        except Exception:
            pass

    if not text:
        raise StreamError(
            f"grep вернул пустой результат для {log_file.filename} "
            f"(возможно, в файле нет нужных строк)",
            category='empty'
        )

    return text