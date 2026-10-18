"""Gravação contínua em segmentos curtos (buffer circular em disco).

Por que disco e não memória?
    10 s de vídeo 720p em quadros brutos ocupam ~830 MB de RAM. Comprimidos em
    H.264 ocupam ~3 MB. Gravamos segmentos de 1 s (MPEG-TS) e apagamos os
    antigos: é um buffer circular que custa pouca RAM, sobrevive a picos de CPU
    e permite montar o clipe só copiando arquivos (sem recodificar).

Como o tempo é controlado
    Esta thread "bate o relógio" no fps configurado (ex.: 30 vezes/s) e envia ao
    FFmpeg o quadro mais recente da câmera. Se a câmera atrasar, repetimos o
    último quadro; se ela cair, enviamos a tela "SEM SINAL". Assim a linha do
    tempo do vídeo acompanha o relógio real, e sabemos o horário exato do
    primeiro quadro de cada segmento — é isso que permite recortar
    "10 s antes / 5 s depois" do botão.
"""
from __future__ import annotations

import itertools
import logging
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

from .capture import CaptureService
from .config import Settings
from .ffmpeg_tools import CREATE_NO_WINDOW

log = logging.getLogger(__name__)

REC_STOPPED = "stopped"
REC_STARTING = "starting"
REC_RECORDING = "recording"
REC_RESTARTING = "restarting"


@dataclass
class Segment:
    index: int
    path: Path
    start: float  # horário (time.time) do primeiro quadro
    end: Optional[float] = None  # horário do primeiro quadro do próximo segmento
    complete: bool = False
    placeholder_frames: int = 0
    frames: int = 0


@dataclass
class RecorderStatus:
    state: str
    buffer_seconds: float
    segments: int
    restarts: int
    dropped_ticks: int
    last_error: str
    session_dir: str


@dataclass
class _Lease:
    start: float
    end: float
    created: float = field(default_factory=time.time)


class SegmentRecorder:
    WATCHDOG_SECONDS = 5.0
    LEASE_MAX_AGE = 300.0

    def __init__(self, settings: Settings, capture: CaptureService, ffmpeg: str,
                 clock: Callable[[], float] = time.time) -> None:
        self.s = settings
        self.capture = capture
        self.ffmpeg = ffmpeg
        self.clock = clock
        self.F = settings.frames_per_segment
        self.seg_duration = self.F / settings.fps

        self._lock = threading.Lock()
        self._segments: Dict[int, Segment] = {}
        self._leases: Dict[int, _Lease] = {}
        self._lease_ids = itertools.count(1)
        self._state = REC_STOPPED
        self._restarts = 0
        self._dropped_ticks = 0
        self._last_error = ""
        self._stop = threading.Event()
        self._proc: Optional[subprocess.Popen] = None
        self._last_write = time.monotonic()
        self._next_index = 0
        self._threads: List[threading.Thread] = []
        self.session_dir = settings.buffer_path / datetime.now().strftime("session_%Y%m%d_%H%M%S")
        self._placeholder = self._make_placeholder()

    # ================================================================ ciclo
    def start(self) -> None:
        self._clean_old_sessions()
        self.session_dir.mkdir(parents=True, exist_ok=True)
        self._stop.clear()
        self._state = REC_STARTING
        for target, name in ((self._run, "recorder"), (self._watchdog, "rec-watchdog")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stop.set()
        proc = self._proc
        if proc and proc.stdin:
            try:
                proc.stdin.close()
            except OSError:
                pass
        for t in self._threads:
            t.join(timeout=10)
        self._state = REC_STOPPED

    def _clean_old_sessions(self) -> None:
        base = self.s.buffer_path
        if not base.exists():
            return
        for child in base.iterdir():
            if child.is_dir() and child.name.startswith("session_") and child != self.session_dir:
                shutil.rmtree(child, ignore_errors=True)

    # ============================================================== FFmpeg
    def _ffmpeg_cmd(self, start_number: int) -> List[str]:
        s = self.s
        return [
            self.ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error",
            "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{s.width}x{s.height}",
            "-r", str(s.fps), "-i", "-",
            "-an", "-c:v", "libx264", "-preset", s.x264_preset, "-tune", "zerolatency",
            "-crf", str(s.x264_crf), "-pix_fmt", "yuv420p", "-profile:v", "main",
            "-g", str(self.F), "-keyint_min", str(self.F), "-sc_threshold", "0",
            "-force_key_frames", f"expr:gte(n,n_forced*{self.F})",
            "-f", "segment", "-segment_time", str(self.seg_duration),
            "-segment_format", "mpegts", "-segment_start_number", str(start_number),
            "-reset_timestamps", "1",
            str(self.session_dir / "seg_%08d.ts"),
        ]

    def _segment_path(self, index: int) -> Path:
        return self.session_dir / f"seg_{index:08d}.ts"

    def _spawn(self) -> subprocess.Popen:
        proc = subprocess.Popen(
            self._ffmpeg_cmd(self._next_index), stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW,
        )
        threading.Thread(target=self._drain_stderr, args=(proc,), name="ffmpeg-log", daemon=True).start()
        return proc

    @staticmethod
    def _drain_stderr(proc: subprocess.Popen) -> None:
        assert proc.stderr is not None
        for line in iter(proc.stderr.readline, b""):
            text = line.decode("utf-8", "replace").strip()
            if text:
                log.warning("ffmpeg: %s", text)

    # ======================================================= loop principal
    def _run(self) -> None:
        backoff = 1.0
        while not self._stop.is_set():
            try:
                self._proc = self._spawn()
            except OSError as exc:
                self._fail(f"Não foi possível iniciar o FFmpeg: {exc}")
                self._stop.wait(backoff)
                backoff = min(backoff * 2, 10.0)
                continue
            self._state = REC_RECORDING
            log.info("Gravação iniciada (segmento inicial %d, %s)", self._next_index, self.session_dir.name)
            clean_exit = self._write_loop(self._proc)
            proc, self._proc = self._proc, None
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
            if clean_exit and self._stop.is_set():
                self._mark_all_complete()
                break
            # FFmpeg morreu: descarta o segmento parcial e reinicia sem parar a captura
            self._discard_partial_segment()
            self._restarts += 1
            self._fail(f"FFmpeg encerrou inesperadamente (código {proc.returncode}); reiniciando")
            self._stop.wait(backoff)
            backoff = min(backoff * 2, 10.0)
        self._state = REC_STOPPED

    def _fail(self, message: str) -> None:
        self._state = REC_RESTARTING
        self._last_error = message
        log.error(message)

    def _write_loop(self, proc: subprocess.Popen) -> bool:
        """Envia quadros ao FFmpeg no ritmo do fps. Retorna True se parou por stop()."""
        assert proc.stdin is not None
        interval = 1.0 / self.s.fps
        next_tick = time.monotonic()
        frame_in_segment = 0
        current: Optional[Segment] = None
        while not self._stop.is_set():
            delay = next_tick - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            elif delay < -0.5:
                # Ficamos para trás (CPU ocupada). Ressincroniza em vez de acumular atraso.
                self._dropped_ticks += int(-delay / interval)
                next_tick = time.monotonic()
            next_tick += interval

            frame, placeholder = self._next_frame()
            now = self.clock()
            if frame_in_segment == 0:
                current = self._open_segment(now)
            try:
                proc.stdin.write(frame.tobytes())
            except (BrokenPipeError, OSError, ValueError):
                # stop() fecha o stdin de outra thread: isso é encerramento normal, não falha
                return self._stop.is_set()
            self._last_write = time.monotonic()
            assert current is not None
            current.frames += 1
            if placeholder:
                current.placeholder_frames += 1
            frame_in_segment += 1
            if frame_in_segment >= self.F:
                frame_in_segment = 0
                self._housekeeping()
            if proc.poll() is not None:
                return False
        try:
            proc.stdin.close()
        except OSError:
            pass
        return True

    def _next_frame(self) -> Tuple[np.ndarray, bool]:
        frame, age = self.capture.latest()
        placeholder = frame is None or age > self.s.frame_stale_seconds
        if placeholder:
            out = self._placeholder.copy()
        else:
            out = frame
            if out.ndim == 2:
                out = cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)
            if out.shape[1] != self.s.width or out.shape[0] != self.s.height:
                out = cv2.resize(out, (self.s.width, self.s.height), interpolation=cv2.INTER_AREA)
            else:
                out = out.copy()  # nunca desenhar sobre o quadro compartilhado da captura
        if self.s.overlay_clock:
            stamp = datetime.now().strftime("%d/%m/%Y %H:%M:%S")
            scale = max(0.5, self.s.height / 900)
            (tw, th), base = cv2.getTextSize(stamp, cv2.FONT_HERSHEY_SIMPLEX, scale, 1)
            cv2.rectangle(out, (6, 6), (18 + tw, 18 + th + base), (0, 0, 0), -1)
            cv2.putText(out, stamp, (12, 12 + th), cv2.FONT_HERSHEY_SIMPLEX,
                        scale, (255, 255, 255), 1, cv2.LINE_AA)
        return out, placeholder

    def _make_placeholder(self) -> np.ndarray:
        img = np.zeros((self.s.height, self.s.width, 3), dtype=np.uint8)
        scale = max(0.6, self.s.height / 500)
        text = "SEM SINAL DA CAMERA"
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, 2)
        cv2.putText(img, text, ((self.s.width - tw) // 2, (self.s.height + th) // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, scale, (60, 60, 230), 2, cv2.LINE_AA)
        return img

    # ======================================================== segmentos
    def _open_segment(self, now: float) -> Segment:
        index = self._next_index
        self._next_index += 1
        seg = Segment(index=index, path=self._segment_path(index), start=now)
        with self._lock:
            prev = self._segments.get(index - 1)
            if prev is not None and prev.end is None:
                prev.end = now
            self._segments[index] = seg
        return seg

    def _discard_partial_segment(self) -> None:
        # Segmentos fechados antes da queda continuam válidos; o resto é descartado.
        self._refresh_completion()
        with self._lock:
            partial = [s for s in self._segments.values() if not s.complete]
            for seg in partial:
                self._segments.pop(seg.index, None)
        for seg in partial:
            try:
                seg.path.unlink()
            except OSError:
                pass

    def _mark_all_complete(self) -> None:
        with self._lock:
            for seg in self._segments.values():
                if seg.path.exists():
                    seg.complete = True
                    if seg.end is None:
                        seg.end = seg.start + seg.frames / self.s.fps

    def _refresh_completion(self) -> None:
        """Um segmento está fechado quando o FFmpeg já criou o arquivo seguinte."""
        with self._lock:
            for seg in self._segments.values():
                if not seg.complete and seg.end is not None and self._segment_path(seg.index + 1).exists():
                    seg.complete = True

    def _housekeeping(self) -> None:
        self._refresh_completion()
        cutoff = self.clock() - self.s.buffer_keep_seconds
        now = time.time()
        with self._lock:
            for lid in [k for k, l in self._leases.items() if now - l.created > self.LEASE_MAX_AGE]:
                self._leases.pop(lid, None)  # proteção contra leases esquecidos
            removable = []
            for seg in self._segments.values():
                if seg.end is None or seg.end >= cutoff:
                    continue
                if any(seg.end > l.start and seg.start < l.end for l in self._leases.values()):
                    continue
                removable.append(seg)
            for seg in removable:
                self._segments.pop(seg.index, None)
        for seg in removable:
            try:
                seg.path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:  # no Windows o arquivo pode estar aberto por outro processo
                log.debug("Não foi possível apagar %s agora: %s", seg.path.name, exc)

    def _watchdog(self) -> None:
        while not self._stop.wait(1.0):
            proc = self._proc
            if proc is None or self._state != REC_RECORDING:
                continue
            if time.monotonic() - self._last_write > self.WATCHDOG_SECONDS:
                log.error("Encoder travado há mais de %.0fs; reiniciando FFmpeg", self.WATCHDOG_SECONDS)
                proc.kill()
                self._last_write = time.monotonic()

    # =========================================== API usada pelo gerador de clipes
    def lease(self, start: float, end: float) -> int:
        """Impede que segmentos dentro de [start, end] sejam apagados."""
        with self._lock:
            lid = next(self._lease_ids)
            self._leases[lid] = _Lease(start, end)
            return lid

    def release(self, lease_id: int) -> None:
        with self._lock:
            self._leases.pop(lease_id, None)

    def covered_until(self) -> Optional[float]:
        """Horário final do último segmento completo."""
        self._refresh_completion()
        with self._lock:
            ends = [s.end for s in self._segments.values() if s.complete and s.end is not None]
        return max(ends) if ends else None

    def segments_between(self, start: float, end: float) -> List[Segment]:
        self._refresh_completion()
        with self._lock:
            segs = [
                s for s in self._segments.values()
                if s.complete and s.end is not None and s.end > start and s.start < end and s.path.exists()
            ]
        return sorted(segs, key=lambda s: s.index)

    @property
    def is_recording(self) -> bool:
        return self._state == REC_RECORDING

    def status(self) -> RecorderStatus:
        self._refresh_completion()
        with self._lock:
            complete = [s for s in self._segments.values() if s.complete]
            buffer_seconds = 0.0
            if complete:
                buffer_seconds = max(s.end or s.start for s in complete) - min(s.start for s in complete)
            count = len(self._segments)
        return RecorderStatus(
            state=self._state, buffer_seconds=round(buffer_seconds, 1), segments=count,
            restarts=self._restarts, dropped_ticks=self._dropped_ticks,
            last_error=self._last_error, session_dir=self.session_dir.name,
        )
