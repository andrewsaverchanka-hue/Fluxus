"""Снимает краткую информацию о системе устройства после SSH-подключения.

Собирает:
- RAM: используется / всего, в процентах
- CPU-температура (из /sys/class/thermal/thermal_zone0/temp)
- Uptime (красивый формат через `uptime -p`)
- CPU loadavg (1 мин, 5 мин, 15 мин)
- Количество CPU-ядер

Все команды работают и на GNU coreutils (Ubuntu — Lima/W2),
и на BusyBox (на всякий случай для embedded).
"""

from dataclasses import dataclass, field
from typing import Optional

from connector.ssh_client import SSHClientWrapper


@dataclass
class SystemInfo:
    """Информация о системе устройства, собираемая один раз при подключении."""

    # Память (МБ)
    mem_total_mb: Optional[int] = None
    mem_used_mb: Optional[int] = None
    mem_percent: Optional[float] = None

    # CPU
    cpu_temp_celsius: Optional[float] = None
    cpu_loadavg_1min: Optional[float] = None
    cpu_cores: Optional[int] = None
    cpu_load_percent: Optional[float] = None  # loadavg_1min / cores * 100

    # Uptime
    uptime_human: Optional[str] = None  # "up 2 days, 3 hours, 45 minutes"
    uptime_seconds: Optional[int] = None

    # Build / версия
    build_number: Optional[str] = None

    # Ошибки сбора (для debug)
    errors: list = field(default_factory=list)

    def mem_display(self) -> str:
        """Красивое отображение памяти для GUI."""
        if self.mem_total_mb is None:
            return "н/д"
        total_gb = self.mem_total_mb / 1024
        used_gb = (self.mem_used_mb or 0) / 1024
        pct = self.mem_percent or 0
        return f"{pct:.0f}% ({used_gb:.1f}/{total_gb:.1f} ГБ)"

    def cpu_temp_display(self) -> str:
        """Красивое отображение CPU-температуры."""
        if self.cpu_temp_celsius is None:
            return "н/д"
        return f"{self.cpu_temp_celsius:.0f}°C"

    def cpu_load_display(self) -> str:
        """Красивое отображение загрузки CPU."""
        if self.cpu_load_percent is None:
            return "н/д"
        return f"{self.cpu_load_percent:.0f}%"

    def uptime_display(self) -> str:
        """Короткое отображение uptime для карточки."""
        if self.uptime_human:
            # Убираем "up " в начале и укорачиваем: "2 days, 3 hours" → "2д 3ч"
            text = self.uptime_human.replace("up ", "").strip()
            text = (text
                    .replace(" days,", "д")
                    .replace(" day,", "д")
                    .replace(" hours,", "ч")
                    .replace(" hour,", "ч")
                    .replace(" minutes", "м")
                    .replace(" minute", "м"))
            return text
        if self.uptime_seconds:
            days = self.uptime_seconds // 86400
            hours = (self.uptime_seconds % 86400) // 3600
            mins = (self.uptime_seconds % 3600) // 60
            if days > 0:
                return f"{days}д {hours}ч"
            if hours > 0:
                return f"{hours}ч {mins}м"
            return f"{mins}м"
        return "н/д"


def collect_system_info(ssh: SSHClientWrapper) -> SystemInfo:
    """Снимает информацию о системе устройства одним пачковым запросом.

    Все команды защищены от ошибок — если что-то не сработает,
    поле остаётся None и ошибка добавляется в SystemInfo.errors.
    """
    info = SystemInfo()

    # ===== 1. Память (free -m) =====
    try:
        out, _ = ssh.run_command(
            "free -m 2>/dev/null | awk '/^Mem:/ {print $2, $3}'",
            timeout=10
        )
        parts = out.strip().split()
        if len(parts) >= 2:
            info.mem_total_mb = int(parts[0])
            info.mem_used_mb = int(parts[1])
            if info.mem_total_mb > 0:
                info.mem_percent = (info.mem_used_mb / info.mem_total_mb) * 100
    except Exception as e:
        info.errors.append(f"mem: {e}")

    # ===== 2. CPU: количество ядер =====
    try:
        out, _ = ssh.run_command("nproc 2>/dev/null", timeout=5)
        if out.strip().isdigit():
            info.cpu_cores = int(out.strip())
    except Exception as e:
        info.errors.append(f"cores: {e}")

    # ===== 3. CPU: loadavg =====
    try:
        out, _ = ssh.run_command(
            "cat /proc/loadavg 2>/dev/null | awk '{print $1}'",
            timeout=5
        )
        if out.strip():
            info.cpu_loadavg_1min = float(out.strip())
            if info.cpu_cores and info.cpu_cores > 0:
                info.cpu_load_percent = (
                    info.cpu_loadavg_1min / info.cpu_cores * 100
                )
    except Exception as e:
        info.errors.append(f"loadavg: {e}")

    # ===== 4. CPU: температура =====
    try:
        # Пробуем стандартный путь thermal_zone0
        out, _ = ssh.run_command(
            "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null",
            timeout=5
        )
        if out.strip().isdigit():
            # Значение в милликельвинах → переводим в Цельсии
            info.cpu_temp_celsius = int(out.strip()) / 1000.0
    except Exception as e:
        info.errors.append(f"temp: {e}")

    # ===== 5. Uptime =====
    try:
        # Пробуем `uptime -p` — даёт красивый формат
        out, _ = ssh.run_command("uptime -p 2>/dev/null", timeout=5)
        if out.strip():
            info.uptime_human = out.strip()
    except Exception:
        pass
    if not info.uptime_human:
        # Fallback: /proc/uptime даёт секунды
        try:
            out, _ = ssh.run_command(
                "cat /proc/uptime 2>/dev/null | awk '{print $1}'",
                timeout=5
            )
            if out.strip():
                info.uptime_seconds = int(float(out.strip()))
        except Exception as e:
            info.errors.append(f"uptime: {e}")

    # ===== 6. Build number (опционально) =====
    try:
        out, _ = ssh.run_command(
            "cat /opt/buildnumber 2>/dev/null", timeout=5
        )
        if out.strip():
            info.build_number = out.strip()
    except Exception:
        pass

    return info