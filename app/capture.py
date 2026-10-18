"""Leitura contínua da câmera em uma thread dedicada.

A thread só lê quadros e guarda o mais recente. Ela nunca espera pelo encoder
nem pela geração de clipes, então um clipe lento não derruba a captura.
Se a câmera falhar, a thread tenta reabrir com espera progressiva (1 s → 10 s).
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Optional, Tuple

from .sources import Frame, FrameSource

log = logging.getLogger(__name__)

CAMERA_STARTING = "starting"
CAMERA_OK = "ok"
CAMERA_RECONNECTING = "reconnecting"
CAMERA_UNAVAILABLE = "unavailable"
CAMERA_STOPPED = "stopped"


@dataclass
class CameraStatus:
    state: str
    source: str
    measured_fps: float
    last_frame_age: Optional[float]
    frames_total: int
    reconnects: int
    last_error: str


class CaptureService:
    MAX_CONSECUTIVE_FAILURES = 15

    def __init__(self, source: FrameSource) -> None:
        self.source = source
        self._lock = threading.Lock()
        self._frame: Optional[Frame] = None
        self._frame_time = 0.0  # time.monotonic() do último quadro
        self._frame_times: deque = deque(maxlen=60)
        self._frames_total = 0
        self._state = CAMERA_STOPPED
        self._reconnects = 0
        self._last_error = ""
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ---------------------------------------------------------------- ciclo
    def start(self) -> None:
        self._stop.clear()
        self._state = CAMERA_STARTING
        self._thread = threading.Thread(target=self._run, name="capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        self.source.release()
        self._state = CAMERA_STOPPED

    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                opened = self.source.open()
            except Exception as exc:  # driver da câmera pode lançar exceções inesperadas
                log.exception("Erro ao abrir a câmera")
                opened, self._last_error = False, str(exc)
            if not opened:
                self._state = CAMERA_UNAVAILABLE
                self._last_error = self._last_error or "Não foi possível abrir a câmera"
                log.warning("Câmera indisponível (%s). Nova tentativa em %.0fs", self.source.name, backoff)
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue

            backoff = 1.0
            self._state = CAMERA_OK
            self._last_error = ""
            self._read_loop()
            self.source.release()
            if not self._stop.is_set():
                self._reconnects += 1
                self._state = CAMERA_RECONNECTING
                log.warning("Câmera parou de enviar quadros; reconectando (#%d)", self._reconnects)
                self._stop.wait(0.5)

    def _read_loop(self) -> None:
        failures = 0
        while not self._stop.is_set():
            try:
                ok, frame = self.source.read()
            except Exception as exc:
                log.exception("Erro lendo quadro")
                ok, frame, self._last_error = False, None, str(exc)
            if not ok or frame is None:
                failures += 1
                if failures >= self.MAX_CONSECUTIVE_FAILURES:
                    self._last_error = "Falha na leitura de quadros"
                    return
                time.sleep(0.02)
                continue
            failures = 0
            now = time.monotonic()
            with self._lock:
                self._frame = frame
                self._frame_time = now
                self._frame_times.append(now)
                self._frames_total += 1

    # ------------------------------------------------------------- consulta
    def latest(self) -> Tuple[Optional[Frame], float]:
        """Retorna (quadro, idade em segundos). Quadro None se nunca houve sinal."""
        with self._lock:
            if self._frame is None:
                return None, float("inf")
            return self._frame, time.monotonic() - self._frame_time

    def status(self) -> CameraStatus:
        with self._lock:
            times = list(self._frame_times)
            age = (time.monotonic() - self._frame_time) if self._frame is not None else None
            total = self._frames_total
        fps = 0.0
        if len(times) >= 2 and times[-1] > times[0] and age is not None and age < 2:
            fps = (len(times) - 1) / (times[-1] - times[0])
        return CameraStatus(
            state=self._state, source=self.source.name, measured_fps=round(fps, 1),
            last_frame_age=None if age is None else round(age, 2), frames_total=total,
            reconnects=self._reconnects, last_error=self._last_error,
        )
