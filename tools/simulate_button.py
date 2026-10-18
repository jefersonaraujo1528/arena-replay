"""Simula o botão ESP32 pela rede (mesmo protocolo do firmware). Só usa a biblioteca padrão.

Exemplos:
    python tools/simulate_button.py                       # um toque
    python tools/simulate_button.py --count 3 --interval 6
    python tools/simulate_button.py --duplicate           # reenvia o mesmo evento (testa idempotência)
    python tools/simulate_button.py --host 192.168.0.10 --token MEU_TOKEN
"""
from __future__ import annotations

import argparse
import json
import secrets
import sys
import time
import urllib.error
import urllib.request


def send(url: str, token: str, event_id: str, device_id: str, pressed_at: float) -> tuple:
    body = json.dumps({
        "event_id": event_id, "device_id": device_id,
        "age_ms": int((time.monotonic() - pressed_at) * 1000),
    }).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json", "X-Arena-Token": token,
    })
    try:
        with urllib.request.urlopen(req, timeout=2.5) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def press(url: str, token: str, device_id: str, boot_id: str, n: int, duplicate: bool) -> bool:
    event_id = f"{device_id}-{boot_id}-{n}"
    pressed_at = time.monotonic()
    delay = 0.25
    while time.monotonic() - pressed_at < 25:
        try:
            code, data = send(url, token, event_id, device_id, pressed_at)
        except (urllib.error.URLError, OSError) as exc:
            print(f"  {event_id}: sem resposta ({exc}); tentando de novo em {delay:.2f}s")
            time.sleep(delay)
            delay = min(delay * 2, 5)
            continue
        print(f"  {event_id}: HTTP {code} {data}")
        if code in (401, 403, 422):
            return False
        if code in (200, 201):
            if duplicate:
                code2, data2 = send(url, token, event_id, device_id, pressed_at)
                print(f"  {event_id} (reenvio): HTTP {code2} {data2}")
            return True
        time.sleep(delay)
        delay = min(delay * 2, 5)
    print(f"  {event_id}: expirou sem confirmação")
    return False


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p.add_argument("--token", default="")
    p.add_argument("--device", default="simulador")
    p.add_argument("--count", type=int, default=1)
    p.add_argument("--interval", type=float, default=5.0)
    p.add_argument("--duplicate", action="store_true")
    a = p.parse_args()

    url = f"http://{a.host}:{a.port}/api/events"
    boot_id = secrets.token_hex(4)
    print(f"Enviando {a.count} toque(s) para {url}")
    ok = True
    for n in range(1, a.count + 1):
        ok = press(url, a.token, a.device, boot_id, n, a.duplicate) and ok
        if n < a.count:
            time.sleep(a.interval)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
