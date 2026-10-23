"""SSH-клиент: подключение к устройствам через шлюз-транзит."""

import os
from typing import Optional

import paramiko

from config.settings import (
    SSH_GATEWAY, SSH_CONNECT_TIMEOUT, SSH_COMMAND_TIMEOUT,
)


class SSHConnectionError(Exception):
    """Ошибка подключения SSH."""
    pass


class SSHClientWrapper:
    """
    Обёртка над paramiko для подключения к устройству через шлюз.
    Поддерживает:
    - аутентификацию SSH-ключом на шлюзе и устройстве,
    - fallback на пароль для устройства,
    - выполнение команд и SFTP.
    """

    def __init__(self, device: dict, key_path: str = "",
                 default_password: str = ""):
        """
        Args:
            device: {"id": str, "host": str, "port": int,
                     "username": str, "password": str (опц.)}
            key_path: путь к SSH-ключу
            default_password: fallback-пароль, если не задан у устройства
        """
        self.device = device
        self.key_path = key_path
        self.default_password = default_password
        self.gateway_client: Optional[paramiko.SSHClient] = None
        self.target_client: Optional[paramiko.SSHClient] = None
        self._log_callback = None  # (level, msg)

    def set_log_callback(self, callback):
        """callback(level: str, msg: str), level in {'info', 'warning', 'error'}"""
        self._log_callback = callback

    def _log(self, level: str, msg: str):
        if self._log_callback:
            self._log_callback(level, msg)
        else:
            print(f"[SSH-{level}] {msg}")

    # ========== Загрузка SSH-ключа ==========

    def _load_ssh_key(self, passphrase=None):
        if not self.key_path or not os.path.exists(self.key_path):
            raise SSHConnectionError(
                f"Файл SSH-ключа не найден: {self.key_path}"
            )
        errors = []
        for key_class in (paramiko.Ed25519Key, paramiko.RSAKey,
                          paramiko.ECDSAKey):
            try:
                return key_class.from_private_key_file(
                    self.key_path, password=passphrase
                )
            except Exception as e:
                errors.append(f"{key_class.__name__}: {e}")
        raise SSHConnectionError(f"Не удалось загрузить ключ: {errors}")

    # ========== Подключение ==========

    def connect(self) -> bool:
        """
        Устанавливает SSH-туннель: клиент → шлюз → устройство.
        Возвращает True при успехе.
        """
        self._log('info', f"Подключение к шлюзу {SSH_GATEWAY['host']}...")
        self.gateway_client = paramiko.SSHClient()
        self.gateway_client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy()
        )
        key = None
        try:
            key = self._load_ssh_key()
            self.gateway_client.connect(
                SSH_GATEWAY['host'], port=SSH_GATEWAY['port'],
                username=SSH_GATEWAY['username'],
                pkey=key, timeout=SSH_CONNECT_TIMEOUT,
            )
            self._log('info', "✓ Шлюз: вход по ключу")
        except Exception as e:
            self._log('warning', f"Ошибка входа ключом на шлюз: {e}")
            try:
                self.gateway_client.connect(
                    SSH_GATEWAY['host'], port=SSH_GATEWAY['port'],
                    username=SSH_GATEWAY['username'],
                    allow_agent=True, look_for_keys=True,
                    timeout=SSH_CONNECT_TIMEOUT,
                )
                self._log('info', "✓ Шлюз: вход через SSH-агент")
            except Exception as agent_error:
                self._log('error', f"Шлюз недоступен: {agent_error}")
                return False

        transport = self.gateway_client.get_transport()

        # Подключение к устройству через шлюз-туннель
        self.target_client = paramiko.SSHClient()
        self.target_client.set_missing_host_key_policy(
            paramiko.AutoAddPolicy()
        )
        try:
            if key is None:
                key = self._load_ssh_key()
            channel = transport.open_channel(
                "direct-tcpip",
                (self.device['host'], self.device['port']),
                (SSH_GATEWAY['host'], SSH_GATEWAY['port']),
            )
            self._log('info',
                      f"Устройство {self.device['host']}: вход ключом...")
            self.target_client.connect(
                self.device['host'], port=self.device['port'],
                username=self.device['username'],
                pkey=key, sock=channel, timeout=SSH_CONNECT_TIMEOUT,
            )
            self._log('info', "✓ Устройство: вход по ключу")
            return True
        except Exception as key_error:
            self._log('warning', f"Не удалось войти ключом: {key_error}")

        # Fallback: пароль
        password = self.device.get('password') or self.default_password
        if not password:
            self._log('error', "Пароль не задан — вход невозможен")
            return False
        try:
            channel = transport.open_channel(
                "direct-tcpip",
                (self.device['host'], self.device['port']),
                (SSH_GATEWAY['host'], SSH_GATEWAY['port']),
            )
            self.target_client = paramiko.SSHClient()
            self.target_client.set_missing_host_key_policy(
                paramiko.AutoAddPolicy()
            )
            self._log('info', "Устройство: вход паролем...")
            self.target_client.connect(
                self.device['host'], port=self.device['port'],
                username=self.device['username'],
                password=password, sock=channel,
                timeout=SSH_CONNECT_TIMEOUT,
            )
            self._log('info', "✓ Устройство: вход по паролю")
            return True
        except Exception as pass_error:
            self._log('error', f"Ошибка входа паролем: {pass_error}")
            return False

    def disconnect(self):
        """Закрывает SSH-соединения."""
        if self.target_client:
            try:
                self.target_client.close()
            except Exception:
                pass
            self.target_client = None
        if self.gateway_client:
            try:
                self.gateway_client.close()
            except Exception:
                pass
            self.gateway_client = None
        self._log('info', "SSH-соединения закрыты")

    def is_connected(self) -> bool:
        if not self.target_client:
            return False
        transport = self.target_client.get_transport()
        return transport is not None and transport.is_active()

    # ========== Команды ==========

    def run_command(self, command: str,
                    timeout: int = SSH_COMMAND_TIMEOUT) -> tuple[str, str]:
        """Выполняет команду на устройстве, возвращает (stdout, stderr)."""
        if not self.target_client:
            raise SSHConnectionError("Нет подключения к устройству")
        _, stdout, stderr = self.target_client.exec_command(
            command, timeout=timeout
        )
        out = stdout.read().decode(errors='replace').strip()
        err = stderr.read().decode(errors='replace').strip()
        return out, err

    def open_sftp(self):
        """Возвращает SFTP-сессию для скачивания файлов."""
        if not self.target_client:
            raise SSHConnectionError("Нет подключения к устройству")
        return self.target_client.open_sftp()