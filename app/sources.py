"""Fontes de vídeo.

Toda câmera é acessada pela interface ``FrameSource``. Hoje existem:

- ``OpenCVSource``: webcam (índice "0", "1"...) ou câmera IP ("rtsp://...", "http://...").
- ``SyntheticSource``: padrão de teste animado, usado nos testes e em demonstrações sem câmera.

Para suportar um novo tipo de câmera basta implementar ``open/read/release``.
"""
from __future__ import annotations

import logging
import sys
import time
from typing import Optional, Tuple

import cv2
import numpy as np

log = logging.getLogger(__name__)

Frame = np.ndarray

_BACKENDS = {
    "dshow": cv2.CAP_DSHOW,
    "msmf": cv2.CAP_MSMF,
    "avfoundation": cv2.CAP_AVFOUNDATION,
    "v4l2": cv2.CAP_V4L2,
    "ffmpeg": cv2.CAP_FFMPEG,
}


class FrameSource:
    name = "base"

    def open(self) -> bool:
        raise NotImplementedError

    def read(self) -> Tuple[bool, Optional[Frame]]:
        raise NotImplementedError

    def release(self) -> None:
        raise NotImplementedError


class OpenCVSource(FrameSource):
    def __init__(self, source: str, backend: str, width: int, height: int, fps: int) -> None:
        self.source = source
        self.backend = backend
        self.width, self.height, self.fps = width, height, fps
        self._cap: Optional[cv2.VideoCapture] = None
        self.name = f"opencv:{source}"

    def _backend_id(self) -> int:
        if self.backend != "auto":
            return _BACKENDS.get(self.backend, cv2.CAP_ANY)
        if not self.source.isdigit():
            return cv2.CAP_FFMPEG  # RTSP/HTTP
        # DirectShow abre webcams bem mais rápido que MSMF no Windows
        return cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY

    def open(self) -> bool:
        self.release()
        target = int(self.source) if self.source.isdigit() else self.source
        cap = cv2.VideoCapture(target, self._backend_id())
        if not cap.isOpened():
            cap.release()
            return False
        if isinstance(target, int):
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.height)
            cap.set(cv2.CAP_PROP_FPS, self.fps)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # menor latência
        ok, frame = cap.read()
        if not ok or frame is None:
            cap.release()
            return False
        log.info(
            "Câmera aberta: %s (%dx%d, fps informado %.1f)",
            self.source, frame.shape[1], frame.shape[0], cap.get(cv2.CAP_PROP_FPS),
        )
        self._cap = cap
        return True

    def read(self) -> Tuple[bool, Optional[Frame]]:
        if self._cap is None:
            return False, None
        ok, frame = self._cap.read()
        return (ok and frame is not None), frame

    def release(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None


class SyntheticSource(FrameSource):
    """Gera quadros animados no ritmo do fps configurado (sem hardware)."""

    name = "synthetic"

    def __init__(self, width: int, height: int, fps: int) -> None:
        self.width, self.height, self.fps = width, height, fps
        self._n = 0
        self._next = 0.0
        self.fail_reads = False  # usado em testes para simular perda de sinal

    def open(self) -> bool:
        self._next = time.monotonic()
        return True

    def read(self) -> Tuple[bool, Optional[Frame]]:
        self._next += 1.0 / self.fps
        delay = self._next - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        if self.fail_reads:
            return False, None
        frame = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        frame[:] = (40, 90, 30)  # verde quadra
        x = int((self._n * 8) % self.width)
        y = int(self.height / 2 + (self.height / 3) * np.sin(self._n / 15.0))
        cv2.circle(frame, (x, y), max(6, self.height // 30), (40, 230, 230), -1)
        cv2.putText(frame, f"TESTE #{self._n}", (20, self.height - 20),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.8, (255, 255, 255), 2)
        self._n += 1
        return True, frame

    def release(self) -> None:
        pass


def build_source(camera_source: str, backend: str, width: int, height: int, fps: int) -> FrameSource:
    if camera_source.strip().lower() == "synthetic":
        return SyntheticSource(width, height, fps)
    return OpenCVSource(camera_source.strip(), backend, width, height, fps)
