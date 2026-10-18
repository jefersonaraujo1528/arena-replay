"""Geração dos clipes de replay.

Fluxo de cada replay:
    1. WAITING     – espera a gravação passar de (lance + segundos posteriores).
    2. PROCESSING  – junta os segmentos do intervalo em um MP4 (H.264, faststart).
    3. Verifica o arquivo gerado (abre, conta quadros, confere duração).
    4. DONE        – só depois da verificação o arquivo é movido para data/replays.
       ERROR       – qualquer falha vira uma mensagem compreensível + detalhe no log.

Roda em threads próprias: a captura e a gravação nunca esperam um clipe.
"""
from __future__ import annotations

import logging
import os
import queue
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .config import Settings
from .ffmpeg_tools import FFmpegError, make_filmstrip, probe_video, run_ffmpeg, save_thumbnail
from .recorder import Segment, SegmentRecorder
from .storage import DONE, ERROR, PROCESSING, WAITING, Database, Replay

log = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 120.0


class ClipError(Exception):
    """Erro com mensagem pronta para o usuário."""


class ClipService:
    def __init__(self, settings: Settings, db: Database, recorder: SegmentRecorder, ffmpeg: str,
                 clock: Callable[[], float] = time.time) -> None:
        self.s = settings
        self.db = db
        self.recorder = recorder
        self.ffmpeg = ffmpeg
        self.clock = clock
        self._queue: "queue.Queue[Optional[str]]" = queue.Queue()
        self._leases: Dict[str, int] = {}
        self._leases_lock = threading.Lock()
        self._stop = threading.Event()
        self._workers: List[threading.Thread] = []
        for p in (settings.replays_path, settings.tmp_path):
            p.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ ciclo
    def start(self) -> None:
        self._stop.clear()
        for i in range(self.s.clip_workers):
            t = threading.Thread(target=self._worker, name=f"clip-{i}", daemon=True)
            t.start()
            self._workers.append(t)

    def stop(self) -> None:
        self._stop.set()
        for _ in self._workers:
            self._queue.put(None)
        for t in self._workers:
            t.join(timeout=10)

    def recover_after_restart(self) -> None:
        """Chamado na inicialização: resolve o que ficou pela metade."""
        for part in self.s.tmp_path.glob("*"):
            try:
                part.unlink()
            except OSError:
                pass
        for replay in self.db.replays_with_status(WAITING, PROCESSING):
            self.db.update_replay(
                replay.id, status=ERROR,
                error="Interrompido: o sistema foi reiniciado antes de concluir este replay.",
            )
            log.warning("Replay %s marcado como erro (interrompido por reinicialização)", replay.id)
        for replay in self.db.replays_with_status(DONE):
            if not replay.file_name or not (self.s.replays_path / replay.file_name).exists():
                self.db.update_replay(replay.id, status=ERROR, error="Arquivo do replay não encontrado.")

    def submit(self, replay: Replay) -> None:
        # Protege o intervalo do replay contra a limpeza do buffer desde já
        lease = self.recorder.lease(replay.press_time - replay.pre_seconds,
                                    replay.press_time + replay.post_seconds)
        with self._leases_lock:
            self._leases[replay.id] = lease
        self._queue.put(replay.id)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    # ----------------------------------------------------------------- worker
    def _worker(self) -> None:
        while not self._stop.is_set():
            replay_id = self._queue.get()
            if replay_id is None:
                break
            try:
                self._process(replay_id)
            except ClipError as exc:
                log.warning("Replay %s falhou: %s", replay_id, exc)
                self.db.update_replay(replay_id, status=ERROR, error=str(exc))
            except Exception:
                log.exception("Erro inesperado gerando replay %s", replay_id)
                self.db.update_replay(replay_id, status=ERROR,
                                      error="Erro inesperado ao gerar o replay. Veja o log do sistema.")
            finally:
                with self._leases_lock:
                    lease = self._leases.pop(replay_id, None)
                if lease is not None:
                    self.recorder.release(lease)

    def _process(self, replay_id: str) -> None:
        replay = self.db.get_replay(replay_id)
        if replay is None:
            return
        t0 = replay.press_time - replay.pre_seconds
        t1 = replay.press_time + replay.post_seconds

        self._wait_for_recording(t1)
        if self._stop.is_set():
            return
        self.db.update_replay(replay_id, status=PROCESSING)
        self._check_disk()

        segments = self.recorder.segments_between(t0, t1)
        if not segments:
            raise ClipError("Buffer insuficiente: não há vídeo gravado para o momento do lance.")
        warnings = self._coverage_warnings(segments, t0, t1)

        tmp_video = self.s.tmp_path / f"{replay_id}.part.mp4"
        list_file = self.s.tmp_path / f"{replay_id}.txt"
        try:
            self._write_concat_list(list_file, segments)
            if self.s.precise_trim:
                offset = max(0.0, t0 - segments[0].start)
                expected = min(t1, segments[-1].end or t1) - max(t0, segments[0].start)
                self._ffmpeg_precise(list_file, tmp_video, offset, expected)
                press_offset = replay.press_time - max(t0, segments[0].start)
            else:
                expected = sum(len_of(s, self.s.fps) for s in segments)
                self._ffmpeg_copy(list_file, tmp_video)
                press_offset = replay.press_time - segments[0].start

            info = probe_video(tmp_video)
            if info is None:
                raise ClipError("O vídeo gerado está corrompido ou vazio.")
            if not (expected * 0.8 - 0.5 <= info.duration <= expected * 1.25 + 1.0):
                raise ClipError(
                    f"Duração inesperada do vídeo ({info.duration:.1f}s, esperado ~{expected:.1f}s)."
                )

            final_video = self.s.replays_path / f"{replay_id}.mp4"
            os.replace(tmp_video, final_video)  # atômico: nunca expomos arquivo incompleto
            thumb = self.s.replays_path / f"{replay_id}.jpg"
            thumb_name = thumb.name if save_thumbnail(final_video, thumb, press_offset) else None
            make_filmstrip(final_video, filmstrip_path(self.s.replays_path, replay_id))

            self.db.update_replay(
                replay_id, status=DONE, file_name=final_video.name, thumb_name=thumb_name,
                duration=round(info.duration, 2), size_bytes=final_video.stat().st_size,
                warnings=warnings, error=None,
                press_offset=round(min(max(press_offset, 0.0), info.duration), 2),
            )
            log.info("Replay %s pronto (%.1fs, %d segmentos)", replay_id, info.duration, len(segments))
        except FFmpegError as exc:
            log.error("FFmpeg falhou no replay %s: %s | %s", replay_id, exc, exc.stderr)
            raise ClipError("Falha ao montar o vídeo (FFmpeg). Tente novamente.") from exc
        finally:
            for p in (tmp_video, list_file):
                try:
                    p.unlink()
                except OSError:
                    pass
        self._apply_retention()

    # --------------------------------------------------------------- etapas
    def _wait_for_recording(self, target_end: float) -> None:
        deadline = target_end + 3 * self.recorder.seg_duration + 15.0
        while not self._stop.is_set():
            covered = self.recorder.covered_until()
            if covered is not None and covered >= target_end:
                return
            if self.clock() > deadline:
                raise ClipError(
                    "A gravação não avançou a tempo (câmera ou encoder parados). Verifique o estado do sistema."
                )
            self._stop.wait(0.2)

    def _check_disk(self) -> None:
        free_mb = shutil.disk_usage(self.s.data_path).free / (1024 * 1024)
        if free_mb < self.s.min_free_disk_mb:
            raise ClipError(f"Pouco espaço em disco ({free_mb:.0f} MB livres). Apague replays antigos.")

    def _coverage_warnings(self, segments: List[Segment], t0: float, t1: float) -> List[str]:
        warnings = []
        before = segments[0].start
        if before > t0 + 0.5:
            pre_available = max(0.0, (t0 + self.s.pre_seconds) - before)
            warnings.append(f"Só havia {pre_available:.0f}s gravados antes do lance.")
        indices = [s.index for s in segments]
        if indices != list(range(indices[0], indices[-1] + 1)):
            warnings.append("Houve uma falha de gravação durante este replay (trecho faltando).")
        if sum(s.placeholder_frames for s in segments) > 0:
            warnings.append("A câmera ficou sem sinal durante parte do replay.")
        return warnings

    @staticmethod
    def _write_concat_list(list_file: Path, segments: List[Segment]) -> None:
        lines = []
        for seg in segments:
            path = seg.path.resolve().as_posix().replace("'", "'\\''")
            lines.append(f"file '{path}'")
        list_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def _ffmpeg_copy(self, list_file: Path, out: Path) -> None:
        run_ffmpeg(self.ffmpeg, [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", "-f", "mp4", str(out),
        ], timeout=FFMPEG_TIMEOUT)

    def _ffmpeg_precise(self, list_file: Path, out: Path, offset: float, duration: float) -> None:
        run_ffmpeg(self.ffmpeg, [
            "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-ss", f"{offset:.3f}", "-t", f"{duration:.3f}",
            "-an", "-c:v", "libx264", "-preset", self.s.x264_preset, "-crf", str(self.s.x264_crf),
            "-pix_fmt", "yuv420p", "-profile:v", "main", "-movflags", "+faststart",
            "-f", "mp4", str(out),
        ], timeout=FFMPEG_TIMEOUT)

    def _apply_retention(self) -> None:
        for old in self.db.oldest_replays_beyond(self.s.max_replays):
            if old.status in (WAITING, PROCESSING):
                continue
            for f in self.s.exports_path.glob(f"{old.id}_*.mp4"):
                try:
                    f.unlink()
                except OSError:
                    pass
            for name in (old.file_name, old.thumb_name, filmstrip_path(self.s.replays_path, old.id).name):
                if name:
                    try:
                        (self.s.replays_path / name).unlink()
                    except OSError:
                        pass
            self.db.delete_replay(old.id)
            log.info("Retenção: replay antigo %s removido", old.id)


def filmstrip_path(replays_dir: Path, replay_id: str) -> Path:
    return replays_dir / f"{replay_id}_strip.jpg"


def len_of(seg: Segment, fps: int) -> float:
    if seg.end is not None:
        return seg.end - seg.start
    return seg.frames / fps
