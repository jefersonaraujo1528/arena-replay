"""Endereços da máquina na rede local (para o celular acessar e para o QR Code)."""
from __future__ import annotations

import socket
from typing import List


def local_ips() -> List[str]:
    ips = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # UDP não envia nada; só escolhe a interface de rede
            ips.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(info[4][0])
    except OSError:
        pass
    return sorted(ip for ip in ips if not ip.startswith("127.") and not ip.startswith("169.254."))


def access_urls(port: int, public_url: str = "") -> List[str]:
    if public_url:
        return [public_url.rstrip("/")]
    urls = [f"http://{ip}:{port}" for ip in local_ips()]
    return urls or [f"http://localhost:{port}"]
