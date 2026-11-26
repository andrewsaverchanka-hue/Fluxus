"""Извлечение логов из архивов поддержки (tar.gz / tgz / zip).

Архивы KMLogs содержат десятки файлов: журналы systemd, базы, Xorg и т.п.
Нам нужны только два типа:

    devices_server.log, devices_server.log.N, devices_server.log.N.gz
    skills-battle-frontend.log, skills-battle-frontend.log.N, skills-battle-frontend.log.N.gz

Файлы читаются потоково (tarfile mode "r|gz"), поэтому архив не нужно
распаковывать целиком и не нужно держать его в памяти. Вложенные .gz
распаковывать не нужно — парсеры умеют читать их напрямую.
"""

from __future__ import annotations

import os
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

# devices_server.log      devices_server.log.7      devices_server.log.7.gz
# skills-battle-frontend.log / .7 / .7.gz
LOG_NAME_RE = re.compile(
    r"^(devices_server\.log|skills-battle-frontend\.log)(\.\d+)?(\.gz)?$"
)

ARCHIVE_SUFFIXES = (".tar.gz", ".tgz", ".tar", ".zip")


def is_log_name(name: str) -> bool:
    """Правда, если имя файла — один из нужных типов логов."""
    return bool(LOG_NAME_RE.match(os.path.basename(name)))


def _safe_target(dest: Path, member_name: str) -> Path:
    """Только basename: защита от путей вида ../../etc/passwd в архиве."""
    return dest / Path(member_name).name


def _write_member(src, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "wb") as out:
        shutil.copyfileobj(src, out, length=1024 * 1024)


class _CountingReader:
    """Обёртка над файлом: считает прочитанные (сжатые) байты."""

    def __init__(self, fileobj):
        self._file = fileobj
        self.bytes_read = 0

    def read(self, size=-1):
        data = self._file.read(size)
        self.bytes_read += len(data)
        return data

    def close(self):
        self._file.close()


def extract_logs_from_archive(archive_path: str | Path,
                              dest_dir: str | Path,
                              progress_callback=None) -> list[Path]:
    """Извлекает из архива только логи двух типов.

    Возвращает список извлечённых путей (в порядке архива).
    Архив читается потоково; посторонние файлы (базы, journal, Xorg)
    пропускаются и на диск не попадают.
    """
    archive_path = Path(archive_path)
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    extracted: list[Path] = []
    name = archive_path.name.lower()

    def report(percent: int, member_name: str = ""):
        if progress_callback:
            progress_callback(max(0, min(99, percent)), member_name)

    if name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            members = [i for i in zf.infolist()
                       if not i.is_dir() and is_log_name(i.filename)]
            total = sum(i.compress_size for i in members) or 1
            done = 0
            for info in members:
                target = _safe_target(dest, info.filename)
                with zf.open(info) as src:
                    _write_member(src, target)
                extracted.append(target)
                done += info.compress_size
                report(int(done * 100 / total), Path(info.filename).name)
        return extracted

    if name.endswith((".tar.gz", ".tgz", ".tar")):
        # Потоковое чтение (r|gz) + счётчик прочитанных сжатых байт,
        # чтобы показывать реальный прогресс без повторной распаковки.
        mode = "r|gz" if name.endswith((".tar.gz", ".tgz")) else "r|"
        total_bytes = max(1, os.path.getsize(archive_path))
        with open(archive_path, "rb") as raw:
            counting = _CountingReader(raw)
            with tarfile.open(fileobj=counting, mode=mode) as tf:
                for member in tf:
                    if not member.isfile() or not is_log_name(member.name):
                        continue
                    src = tf.extractfile(member)
                    if src is None:
                        continue
                    target = _safe_target(dest, member.name)
                    _write_member(src, target)
                    extracted.append(target)
                    report(int(counting.bytes_read * 100 / total_bytes),
                           Path(member.name).name)
        return extracted

    raise ValueError(f"Неподдерживаемый формат архива: {archive_path.name}")


def scan_folder_for_logs(folder: str | Path) -> list[Path]:
    """Файлы логов в папке (без рекурсии), отсортированные по ротации."""
    folder = Path(folder)
    found = [p for p in folder.iterdir() if p.is_file() and is_log_name(p.name)]

    def sort_key(p: Path):
        m = re.search(r"\.(\d+)(\.gz)?$", p.name)
        rotation = int(m.group(1)) if m else 10 ** 9  # текущий файл — последним
        kind = 0 if p.name.startswith("devices_server") else 1
        return (kind, rotation, p.name)

    return sorted(found, key=sort_key)
