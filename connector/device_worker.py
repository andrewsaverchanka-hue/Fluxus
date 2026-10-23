"""QThread-воркер для управления подключением к устройству."""

import queue
from pathlib import Path
from datetime import datetime

from PySide6.QtCore import QThread, Signal

from config.settings import DOWNLOADED_LOGS_DIR
from core.utils import safe_filename
from connector.ssh_client import SSHClientWrapper, SSHConnectionError
from connector.device_identifier import identify_device
from connector.device_info import collect_system_info, SystemInfo
from connector.log_fetcher import (
    list_logs, filter_logs_by_hours, filter_logs_by_date_range,
    download_logs, stream_log_to_memory, StreamError,
)


class DeviceWorker(QThread):
    """Фоновый воркер устройства. Держит SSH-соединение и обрабатывает команды.

    Сигналы:
      status_changed(device_id, status, label)
        status: 'connecting' | 'connected' | 'disconnected' | 'error'
      log_message(device_id, level, msg)
      logs_downloaded(device_id, folder_path, file_paths)
      streams_ready(device_id, streams_data, device_label)
      download_progress(device_id, current, total, filename)
      error(device_id, msg)
      stream_error(device_id, category, msg)
      system_info_ready(device_id, system_info_dict)
        — новое: данные о системе устройства после подключения
    """

    status_changed = Signal(str, str, str)
    log_message = Signal(str, str, str)
    logs_downloaded = Signal(str, str, list)
    streams_ready = Signal(str, list, str)
    download_progress = Signal(str, int, int, str)
    error = Signal(str, str)
    stream_error = Signal(str, str, str)
    system_info_ready = Signal(str, object)

    def __init__(self, device: dict, key_path: str,
                 default_password: str = "", parent=None):
        super().__init__(parent)
        self.device = device
        self.key_path = key_path
        self.default_password = default_password
        self.ssh: SSHClientWrapper | None = None
        self.device_type = 'Unknown'
        self.device_identifier = 'unknown'
        self._queue: queue.Queue = queue.Queue()
        self._stop = False

    def request_disconnect(self):
        self._stop = True
        self._queue.put(('disconnect', {}))

    def request_fetch_logs(self, mode: str, params: dict):
        self._queue.put(('fetch_logs', {'mode': mode, 'params': params}))

    def run(self):
        device_id = self.device['id']
        try:
            # 1. Подключение
            self.status_changed.emit(device_id, 'connecting', '')
            self.ssh = SSHClientWrapper(
                self.device, self.key_path, self.default_password
            )
            self.ssh.set_log_callback(
                lambda level, msg:
                    self.log_message.emit(device_id, level, msg)
            )
            if not self.ssh.connect():
                self.status_changed.emit(device_id, 'error', '')
                self.error.emit(device_id, "Не удалось подключиться")
                return

            # 2. Определяем тип устройства
            self.log_message.emit(device_id, 'info',
                                   "Определение типа устройства...")
            try:
                self.device_type, self.device_identifier = \
                    identify_device(self.ssh)
                label = (f"{self.device_type}_"
                         f"{safe_filename(self.device_identifier)}")
                self.log_message.emit(
                    device_id, 'info',
                    f"✓ Тип: {self.device_type}, ID: {self.device_identifier}"
                )
            except Exception as e:
                self.log_message.emit(device_id, 'warning',
                                       f"Не удалось определить тип: {e}")
                label = f"Unknown_{device_id}"

            self.status_changed.emit(device_id, 'connected', label)

            # 3. НОВОЕ: собираем системную информацию (RAM, CPU, uptime)
            self.log_message.emit(device_id, 'info',
                                   "Сбор системной информации...")
            try:
                sys_info = collect_system_info(self.ssh)
                # Эмитим сигнал — GUI обновит карточку
                self.system_info_ready.emit(device_id, sys_info)
                self.log_message.emit(
                    device_id, 'info',
                    f"✓ RAM: {sys_info.mem_display()}, "
                    f"CPU: {sys_info.cpu_temp_display()}, "
                    f"uptime: {sys_info.uptime_display()}"
                )
            except Exception as e:
                self.log_message.emit(device_id, 'warning',
                                       f"Не удалось снять sysinfo: {e}")

            # 4. Главный цикл — ждём команды из GUI
            while not self._stop:
                try:
                    cmd, data = self._queue.get(timeout=0.5)
                except queue.Empty:
                    if not self.ssh.is_connected():
                        self.log_message.emit(
                            device_id, 'warning', "Соединение потеряно"
                        )
                        break
                    continue

                if cmd == 'disconnect':
                    break
                elif cmd == 'fetch_logs':
                    self._handle_fetch_logs(data)

        except Exception as e:
            self.error.emit(device_id, f"Фатальная ошибка воркера: {e}")
        finally:
            if self.ssh:
                self.ssh.disconnect()
            self.status_changed.emit(device_id, 'disconnected', '')

    def _handle_fetch_logs(self, data: dict):
        """Обработка запроса на скачивание или стриминг логов."""
        device_id = self.device['id']
        mode = data['mode']
        params = data['params']
        stream_mode = bool(params.get('stream_mode', False))

        try:
            self.log_message.emit(device_id, 'info',
                                   "Листинг логов на устройстве...")
            all_logs = list_logs(self.ssh)
            self.log_message.emit(device_id, 'info',
                                   f"Всего найдено файлов: {len(all_logs)}")

            if mode == 'hours':
                filtered = filter_logs_by_hours(all_logs, params['hours'])
                self.log_message.emit(
                    device_id, 'info',
                    f"Отобрано за {params['hours']} часов: {len(filtered)}"
                )
            elif mode == 'date_range':
                filtered = filter_logs_by_date_range(
                    all_logs, params['start'], params['end']
                )
                self.log_message.emit(
                    device_id, 'info',
                    f"Отобрано в диапазоне: {len(filtered)}"
                )
            else:
                self.error.emit(device_id, f"Неизвестный режим: {mode}")
                return

            if not filtered:
                self.log_message.emit(device_id, 'warning',
                                       "Нет файлов для обработки")
                return

            if stream_mode:
                self._do_stream(device_id, filtered)
            else:
                self._do_download(device_id, filtered)

        except Exception as e:
            self.error.emit(device_id, f"Ошибка обработки логов: {e}")

    def _do_stream(self, device_id: str, filtered: list):
        """Стриминг логов через grep/zgrep без скачивания на диск."""
        self.log_message.emit(
            device_id, 'info',
            f"🌊 Стриминг {len(filtered)} файлов (без записи на диск)"
        )

        device_label = (f"{self.device_type}_"
                        f"{safe_filename(self.device_identifier)}")
        streams_data = []
        total = len(filtered)

        for i, lf in enumerate(filtered, 1):
            self.download_progress.emit(device_id, i, total, lf.filename)
            log_type = ('frontend' if 'skills-battle' in lf.filename
                        else 'devices')
            try:
                text = stream_log_to_memory(self.ssh, lf, log_type)
                streams_data.append((lf.filename, text, log_type))
                self.log_message.emit(
                    device_id, 'info',
                    f"✓ Стрим {lf.filename}: {len(text)} символов"
                )
            except StreamError as e:
                self.log_message.emit(
                    device_id, 'error',
                    f"Ошибка стрима {lf.filename}: {e}"
                )
                self.stream_error.emit(device_id, e.category, str(e))
                return
            except Exception as e:
                self.log_message.emit(
                    device_id, 'error',
                    f"Непредвиденная ошибка стрима {lf.filename}: {e}"
                )
                self.stream_error.emit(device_id, 'generic', str(e))
                return

        if not streams_data:
            self.log_message.emit(device_id, 'warning',
                                   "Стрим вернул пустой результат")
            self.stream_error.emit(device_id, 'empty',
                                    "grep не нашёл ни одной нужной строки")
            return

        self.log_message.emit(
            device_id, 'info',
            f"✓ Стриминг завершён, собрано файлов: {len(streams_data)}"
        )
        self.streams_ready.emit(device_id, streams_data, device_label)

    def _do_download(self, device_id: str, filtered: list):
        """Скачивание файлов через SFTP в локальную папку."""
        date_str = datetime.now().strftime('%Y-%m-%d')
        folder_name = (f"{self.device_type}_"
                       f"{safe_filename(self.device_identifier)}_"
                       f"{date_str}")
        target_folder = DOWNLOADED_LOGS_DIR / folder_name
        target_folder.mkdir(parents=True, exist_ok=True)
        self.log_message.emit(device_id, 'info',
                               f"📥 Скачивание в {target_folder}...")

        def _progress(current, total, filename):
            self.download_progress.emit(device_id, current, total, filename)

        downloaded = download_logs(
            self.ssh, filtered, target_folder, _progress
        )
        self.log_message.emit(
            device_id, 'info',
            f"✓ Скачано файлов: {len(downloaded)}/{len(filtered)}"
        )
        self.logs_downloaded.emit(
            device_id, str(target_folder),
            [str(p) for p in downloaded]
        )