"""Recortes de replay para download ("baixar só do segundo X ao Y").

- Recodifica o trecho (precisão de quadro, não só de keyframe).
- Um recorte por vez (semáforo) para não roubar CPU da gravação ao vivo.
- Cache: o mesmo trecho pedido duas vezes é entregue sem recodificar.
- Arquivos em data/exports são apagados depois de ``export_keep_hours``.
"""
from __future__ import annotations

import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .config import Settings
from .ffmpeg_tools import FFmpegError, probe_video, run_ffmpeg
from .storage import DONE, Replay

log = logging.getLogger(__name__)

EXPORT_NAME_RE = re.compile(r"^(\d{8}-\d{6}-[0-9a-f]{6})_(\d{5})-(\d{5})\.mp4$")
MIN_SECONDS = 0.5
WAIT_SECONDS = 60.0


class ExportError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class ExportResult:
    name: str
    path: Path
    start: float
    end: float
    duration: float
    size_bytes: int
    cached: bool


class ExportService:
    def __init__(self, settings: Settings, ffmpeg: str) -> None:
        self.s = settings
        self.ffmpeg = ffmpeg
        self._sem = threading.Semaphore(1)
        settings.exports_path.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def name_for(replay_id: str, start: float, end: float) -> str:
        # décimos de segundo no nome: o mesmo trecho reaproveita o arquivo
        return f"{replay_id}_{int(round(start * 10)):05d}-{int(round(end * 10)):05d}.mp4"

    def path_for(self, name: str) -> Optional[Path]:
        """Caminho seguro de um recorte existente (None se o nome for inválido ou não existir)."""
        if not EXPORT_NAME_RE.match(name):
            return None
        base = self.s.exports_path.resolve()
        path = (base / name).resolve()
        if path.parent != base or not path.is_file():
            return None
        return path

    def export(self, replay: Replay, start: float, end: float) -> ExportResult:
        if replay.status != DONE or not replay.file_name or not replay.duration:
            raise ExportError("O replay ainda não está pronto.", 409)
        start = round(max(0.0, start), 1)
        end = round(min(end, replay.duration), 1)
        if end - start < MIN_SECONDS:
            raise ExportError(f"Selecione pelo menos {MIN_SECONDS:g} segundo.")
        if end - start > self.s.export_max_seconds:
            raise ExportError("Trecho longo demais.")
        source = self.s.replays_path / replay.file_name
        if not source.exists():
            raise ExportError("Arquivo do replay não encontrado.", 404)

        name = self.name_for(replay.id, start, end)
        final = self.s.exports_path / name
        if final.exists():
            return ExportResult(name, final, start, end, end - start, final.stat().st_size, True)

        if not self._sem.acquire(timeout=WAIT_SECONDS):
            raise ExportError("O sistema está ocupado gerando outro recorte. Tente de novo.", 503)
        try:
            if final.exists():  # outro pedido igual terminou enquanto esperávamos
                return ExportResult(name, final, start, end, end - start, final.stat().st_size, True)
            part = self.s.tmp_path / f"{name}.part.mp4"
            try:
                run_ffmpeg(self.ffmpeg, [
                    "-ss", f"{start:.3f}", "-i", str(source), "-t", f"{end - start:.3f}",
                    "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "21",
                    "-pix_fmt", "yuv420p", "-profile:v", "main", "-movflags", "+faststart",
                    "-f", "mp4", str(part),
                ], timeout=120)
                info = probe_video(part)
                if info is None or info.duration < (end - start) * 0.7:
                    raise ExportError("Falha ao gerar o recorte (vídeo inválido).", 500)
                os.replace(part, final)
            except FFmpegError as exc:
                log.error("Recorte %s falhou: %s | %s", name, exc, exc.stderr)
                raise ExportError("Falha ao gerar o recorte. Tente novamente.", 500) from exc
            finally:
                try:
                    part.unlink()
                except OSError:
                    pass
            log.info("Recorte gerado: %s (%.1fs)", name, end - start)
            return ExportResult(name, final, start, end, info.duration, final.stat().st_size, False)
        finally:
            self._sem.release()
            self.cleanup()

    def cleanup(self) -> None:
        cutoff = time.time() - self.s.export_keep_hours * 3600
        for f in self.s.exports_path.glob("*.mp4"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
            except OSError:
                pass

    def delete_for(self, replay_id: str) -> None:
        for f in self.s.exports_path.glob(f"{replay_id}_*.mp4"):
            try:
                f.unlink()
            except OSError:
                pass
