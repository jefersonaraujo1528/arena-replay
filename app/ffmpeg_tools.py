"""Localização do FFmpeg e verificação de vídeos gerados."""
from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

import cv2

log = logging.getLogger(__name__)

# No Windows, evita abrir uma janela de console para cada processo do FFmpeg
CREATE_NO_WINDOW = 0x08000000 if sys.platform == "win32" else 0


class FFmpegNotFound(RuntimeError):
    pass


class FFmpegError(RuntimeError):
    def __init__(self, message: str, stderr: str = "") -> None:
        super().__init__(message)
        self.stderr = stderr


def find_ffmpeg(configured: str = "") -> str:
    """Retorna o executável do FFmpeg.

    1. Caminho configurado em ``ffmpeg_path``.
    2. ``ffmpeg`` no PATH do sistema.
    3. Binário estático do pacote ``imageio-ffmpeg`` (instalado via pip).
    """
    if configured:
        if Path(configured).exists():
            return configured
        raise FFmpegNotFound(f"ffmpeg_path configurado não existe: {configured}")
    on_path = shutil.which("ffmpeg")
    if on_path:
        return on_path
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - depende do ambiente
        raise FFmpegNotFound(
            "FFmpeg não encontrado. Rode 'pip install imageio-ffmpeg' ou instale o FFmpeg no PATH."
        ) from exc


def run_ffmpeg(ffmpeg: str, args: List[str], timeout: float) -> None:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    log.debug("ffmpeg %s", " ".join(args))
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"FFmpeg excedeu {timeout:.0f}s") from exc
    stderr = proc.stderr.decode("utf-8", "replace")[-2000:]
    if proc.returncode != 0:
        raise FFmpegError(f"FFmpeg terminou com código {proc.returncode}", stderr)


@dataclass
class VideoInfo:
    frames: int
    fps: float
    width: int
    height: int

    @property
    def duration(self) -> float:
        return self.frames / self.fps if self.fps > 0 else 0.0


def probe_video(path: Path) -> Optional[VideoInfo]:
    """Abre o vídeo com OpenCV e confirma que o primeiro e o último quadro decodificam.

    Retorna None se o arquivo estiver ausente, vazio ou corrompido.
    """
    if not path.exists() or path.stat().st_size == 0:
        return None
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            return None
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        ok, _ = cap.read()
        if not ok or frames <= 0 or fps <= 0:
            return None
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, frames - 2))
        ok_last, _ = cap.read()
        if not ok_last:
            return None
        return VideoInfo(frames=frames, fps=fps, width=width, height=height)
    finally:
        cap.release()


def save_thumbnail(video: Path, dest: Path, at_seconds: float, max_width: int = 640) -> bool:
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return False
        cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, at_seconds) * 1000.0)
        ok, frame = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = cap.read()
        if not ok:
            return False
        h, w = frame.shape[:2]
        if w > max_width:
            frame = cv2.resize(frame, (max_width, int(h * max_width / w)), interpolation=cv2.INTER_AREA)
        return bool(cv2.imwrite(str(dest), frame, [cv2.IMWRITE_JPEG_QUALITY, 80]))
    finally:
        cap.release()


def make_filmstrip(video: Path, dest: Path, count: int = 16, height: int = 90) -> bool:
    """Gera uma tira horizontal com ``count`` miniaturas (usada na barra de recorte)."""
    cap = cv2.VideoCapture(str(video))
    try:
        if not cap.isOpened():
            return False
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        if frames <= 0:
            return False
        tiles = []
        for i in range(count):
            cap.set(cv2.CAP_PROP_POS_FRAMES, min(frames - 1, int((i + 0.5) * frames / count)))
            ok, frame = cap.read()
            if not ok:
                if not tiles:
                    return False
                tiles.append(tiles[-1])
                continue
            h, w = frame.shape[:2]
            tiles.append(cv2.resize(frame, (max(1, int(w * height / h)), height), interpolation=cv2.INTER_AREA))
        strip = cv2.hconcat(tiles)
        return bool(cv2.imwrite(str(dest), strip, [cv2.IMWRITE_JPEG_QUALITY, 70]))
    finally:
        cap.release()
