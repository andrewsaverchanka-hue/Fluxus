"""Загрузка и сохранение пользовательского конфига (config.json)."""

import json
from pathlib import Path
from typing import Any

from config.settings import CONFIG_FILE


DEFAULT_CONFIG = {
    "ssh_key_path": "",
    "default_device_password": "",
    "theme": "dark",
    "devices": [
        # Пример: {"id": "001", "host": "10.8.0.1", "comment": "W2 терминал #1"}
    ],
    "last_window_geometry": None,  # [x, y, w, h]
    "auto_check_updates": True,
}


class ConfigManager:
    """Thread-safe чтение/запись конфига. Использует in-memory кэш."""

    def __init__(self):
        self._config: dict = {}
        self._dirty = False
        self.load()

    def load(self) -> None:
        """Загружает конфиг с диска. Если файла нет — создаёт дефолтный."""
        if CONFIG_FILE.exists():
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    self._config = json.load(f)
                # Добавляем недостающие ключи из DEFAULT_CONFIG
                for key, value in DEFAULT_CONFIG.items():
                    self._config.setdefault(key, value)
            except Exception as e:
                print(f"[ConfigManager] Ошибка загрузки: {e}, использую дефолт")
                self._config = DEFAULT_CONFIG.copy()
        else:
            self._config = DEFAULT_CONFIG.copy()
            self.save()

    def save(self) -> None:
        """Сохраняет конфиг на диск."""
        try:
            CONFIG_FILE.parent.mkdir(exist_ok=True, parents=True)
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self._config, f, indent=2, ensure_ascii=False)
            self._dirty = False
        except Exception as e:
            print(f"[ConfigManager] Ошибка сохранения: {e}")

    def get(self, key: str, default: Any = None) -> Any:
        return self._config.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._config[key] = value
        self._dirty = True

    def set_and_save(self, key: str, value: Any) -> None:
        self.set(key, value)
        self.save()


# Единый экземпляр для всего приложения
_instance: ConfigManager | None = None


def get_config() -> ConfigManager:
    """Получить глобальный экземпляр ConfigManager."""
    global _instance
    if _instance is None:
        _instance = ConfigManager()
    return _instance