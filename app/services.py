"""Monta e controla o ciclo de vida de todos os serviços.

Em vez de variáveis globais, um único objeto ``Services`` é criado no início e
entregue à API. Os testes criam o seu próprio, com câmera sintética.
"""
from __future__ import annotations

import logging
import shutil
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional

from .capture import CaptureService
from .clipper import ClipService
from .config import Settings
from .events import EventService
from .exporter import ExportService
from .ffmpeg_tools import find_ffmpeg
from .recorder import SegmentRecorder
from .sources import FrameSource, build_source
from .storage import Database

log = logging.getLogger(__name__)

VERSION = "0.2.0"


@dataclass
class Services:
    settings: Settings
    db: Database
    capture: CaptureService
    recorder: SegmentRecorder
    clips: ClipService
    events: EventService
    exporter: ExportService
    started: bool = False

    @classmethod
    def build(cls, settings: Settings, source: Optional[FrameSource] = None) -> "Services":
        for p in (settings.data_path, settings.buffer_path, settings.replays_path, settings.tmp_path,
                  settings.exports_path):
            p.mkdir(parents=True, exist_ok=True)
        ffmpeg = find_ffmpeg(settings.ffmpeg_path)
        log.info("FFmpeg: %s", ffmpeg)
        db = Database(settings.db_path)
        src = source or build_source(settings.camera_source, settings.camera_backend,
                                     settings.width, settings.height, settings.fps)
        capture = CaptureService(src)
        recorder = SegmentRecorder(settings, capture, ffmpeg)
        clips = ClipService(settings, db, recorder, ffmpeg)
        events = EventService(settings, db, clips)
        exporter = ExportService(settings, ffmpeg)
        return cls(settings, db, capture, recorder, clips, events, exporter)

    def start(self) -> None:
        if self.started:
            return
        self.clips.recover_after_restart()
        self.exporter.cleanup()
        self.capture.start()
        self.recorder.start()
        self.clips.start()
        self.started = True
        if not self.settings.device_token:
            log.warning("device_token não definido: qualquer aparelho da rede pode acionar replays.")

    def stop(self) -> None:
        if not self.started:
            return
        self.clips.stop()
        self.recorder.stop()
        self.capture.stop()
        self.db.close()
        self.started = False

    def status(self) -> Dict[str, Any]:
        cam = self.capture.status()
        rec = self.recorder.status()
        disk = shutil.disk_usage(self.settings.data_path)
        free_mb = disk.free / (1024 * 1024)
        problems = []
        if cam.state != "ok":
            problems.append("Câmera sem sinal" if cam.state != "starting" else "Câmera iniciando")
        if rec.state != "recording":
            problems.append("Gravação parada" if rec.state != "starting" else "Gravação iniciando")
        elif rec.buffer_seconds < self.settings.pre_seconds:
            problems.append(f"Buffer enchendo ({rec.buffer_seconds:.0f}s de {self.settings.pre_seconds:.0f}s)")
        if free_mb < self.settings.min_free_disk_mb:
            problems.append("Pouco espaço em disco")
        overall = "ok" if not problems else ("warming" if all("iniciando" in p or "enchendo" in p
                                                                for p in problems) else "degraded")
        return {
            "version": VERSION,
            "server_time": time.time(),
            "overall": overall,
            "problems": problems,
            "camera": cam.__dict__,
            "recorder": rec.__dict__,
            "queue": self.clips.queue_size,
            "disk_free_mb": round(free_mb),
            "replays_by_status": self.db.count_by_status(),
            "last_event_at": self.events.last_event_at,
            "last_device": self.events.last_device,
            "pre_seconds": self.settings.pre_seconds,
            "post_seconds": self.settings.post_seconds,
            "test_trigger_enabled": self.settings.allow_test_trigger,
        }
