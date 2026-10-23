"""Проверка обновлений через GitHub Releases API."""

import re
from pathlib import Path
from dataclasses import dataclass
from typing import Optional

import requests

from version import VERSION, GITHUB_API_URL
from config.settings import UPDATE_TMP_DIR


@dataclass
class ReleaseInfo:
    """Информация о релизе на GitHub."""
    tag_name: str          # например, "v1.0.1"
    version: str           # очищенная версия, "1.0.1"
    name: str              # заголовок релиза
    body: str              # changelog
    published_at: str      # ISO-дата публикации
    asset_url: Optional[str] = None   # URL первого .zip-ассета
    asset_name: Optional[str] = None
    asset_size: int = 0


def _parse_version(tag: str) -> tuple[int, ...]:
    """Преобразует 'v1.2.3' → (1, 2, 3). Невалидные части → 0."""
    cleaned = tag.lstrip('v').strip()
    parts = re.split(r'[.\-+]', cleaned)
    result = []
    for p in parts:
        try:
            result.append(int(p))
        except ValueError:
            break  # прекращаем на нечисловой части (напр. "rc1")
    return tuple(result) if result else (0,)


def check_latest_release(timeout: int = 10) -> Optional[ReleaseInfo]:
    """
    Запрашивает информацию о последнем релизе с GitHub.
    Возвращает ReleaseInfo или None при ошибке.
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

        # Ищем первый .zip-ассет
        for asset in data.get('assets', []):
            asset_name = asset.get('name', '')
            if asset_name.endswith('.zip'):
                info.asset_url = asset.get('browser_download_url')
                info.asset_name = asset_name
                info.asset_size = asset.get('size', 0)
                break

        return info
    except Exception as e:
        print(f"[Updater] Ошибка проверки релиза: {e}")
        return None


def is_update_available(release: ReleaseInfo) -> bool:
    """Сравнивает текущую версию с версией релиза."""
    current = _parse_version(VERSION)
    latest = _parse_version(release.version)
    return latest > current


def download_update(release: ReleaseInfo,
                     progress_callback=None) -> Optional[Path]:
    """
    Скачивает ZIP-архив обновления в UPDATE_TMP_DIR.
    progress_callback(bytes_downloaded, total_bytes).
    Возвращает путь к скачанному файлу или None.
    """
    if not release.asset_url:
        return None

    UPDATE_TMP_DIR.mkdir(parents=True, exist_ok=True)
    target_path = UPDATE_TMP_DIR / release.asset_name

    try:
        with requests.get(release.asset_url, stream=True, timeout=60) as r:
            r.raise_for_status()
            total = int(r.headers.get('content-length', 0))
            downloaded = 0
            with open(target_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if progress_callback:
                            progress_callback(downloaded, total)
        return target_path
    except Exception as e:
        print(f"[Updater] Ошибка скачивания: {e}")
        return None