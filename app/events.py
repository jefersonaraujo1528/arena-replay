"""Recepção de acionamentos (botão ESP32 ou botão de teste).

Regras:
- Idempotência: o mesmo ``event_id`` nunca gera dois replays. O ESP32 pode
  reenviar à vontade até receber a confirmação.
- Acionamentos muito próximos (``merge_window_seconds``) viram um único replay.
- O horário do lance é o horário do clique, não o da chegada:
  ``press_time = recebido_em - age_ms`` (o ESP32 informa há quanto tempo o botão
  foi apertado, o que corrige atrasos de Wi-Fi e retentativas).
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Optional

from .clipper import ClipService
from .config import Settings
from .storage import WAITING, Database, Replay

log = logging.getLogger(__name__)

ACCEPTED = "accepted"
DUPLICATE = "duplicate"
MERGED = "merged"


@dataclass
class PressResult:
    outcome: str
    replay_id: Optional[str]
    press_time: float


def new_replay_id(press_time: float) -> str:
    stamp = datetime.fromtimestamp(press_time).strftime("%Y%m%d-%H%M%S")
    return f"{stamp}-{secrets.token_hex(3)}"


class EventService:
    def __init__(self, settings: Settings, db: Database, clips: ClipService,
                 clock: Callable[[], float] = time.time) -> None:
        self.s = settings
        self.db = db
        self.clips = clips
        self.clock = clock
        self._lock = threading.Lock()
        self.last_event_at: Optional[float] = None
        self.last_device: Optional[str] = None

    def register(self, event_id: str, device_id: str, age_ms: int = 0) -> PressResult:
        now = self.clock()
        age = min(max(age_ms, 0), self.s.max_event_age_ms) / 1000.0
        press_time = now - age

        with self._lock:
            self.last_event_at, self.last_device = now, device_id
            existing = self.db.get_event(event_id)
            if existing is not None:
                log.info("Evento duplicado ignorado: %s", event_id)
                return PressResult(DUPLICATE, existing["replay_id"], existing["press_time"])

            last = self.db.last_accepted_replay()
            if last is not None and abs(press_time - last.press_time) < self.s.merge_window_seconds:
                self.db.insert_event(event_id, device_id, now, press_time, MERGED, last.id)
                self.db.update_replay(last.id, presses=last.presses + 1)
                log.info("Evento %s agrupado ao replay %s (acionamentos próximos)", event_id, last.id)
                return PressResult(MERGED, last.id, last.press_time)

            replay = Replay(
                id=new_replay_id(press_time), press_time=press_time, created_at=now, updated_at=now,
                status=WAITING, pre_seconds=self.s.pre_seconds, post_seconds=self.s.post_seconds,
                device_id=device_id,
            )
            self.db.insert_replay(replay)
            self.db.insert_event(event_id, device_id, now, press_time, ACCEPTED, replay.id)

        self.clips.submit(replay)
        log.info("Lance registrado: evento=%s dispositivo=%s replay=%s atraso=%.2fs",
                 event_id, device_id, replay.id, age)
        # Limpeza ocasional do histórico de eventos (dedupe só precisa de algumas horas)
        self.db.prune_events(now - 7 * 24 * 3600)
        return PressResult(ACCEPTED, replay.id, press_time)
