"""Определение типа устройства и его идентификатора (Lima/W2)."""

from config.settings import LIMA_KEY_PATH, W2_CONFIG_PATH
from connector.ssh_client import SSHClientWrapper


def identify_device(ssh: SSHClientWrapper) -> tuple[str, str]:
    """
    Определяет тип и идентификатор устройства.

    Returns:
        (device_type, identifier)
        device_type: 'Lima', 'W2', 'Unknown'
        identifier:  ключ Lima, номер счёта W2 или 'unknown'

    Логика (вариант Б — приоритет Lima при обоих):
    - Если /opt/sftw/build/BACKSERVER/mc3_key.txt существует и не пуст → Lima
    - Если /root/configuration.yml содержит terminalAccount → W2
    - Иначе → Unknown
    """
    # 1. Пробуем Lima
    out, _ = ssh.run_command(f"cat {LIMA_KEY_PATH} 2>/dev/null", timeout=10)
    lima_key = out.strip()

    # 2. Пробуем W2 (grep из configuration.yml строки terminalAccount)
    out, _ = ssh.run_command(
        f"grep 'terminalAccount:' {W2_CONFIG_PATH} 2>/dev/null | "
        f"head -1 | awk -F: '{{print $2}}' | tr -d '\" '",
        timeout=10
    )
    w2_account = out.strip()

    # Логика выбора: приоритет Lima
    if lima_key:
        return 'Lima', lima_key
    if w2_account:
        return 'W2', w2_account
    return 'Unknown', 'unknown'