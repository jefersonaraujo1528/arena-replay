"""API HTTP (FastAPI) e servidor da interface web local."""
from __future__ import annotations

import hmac
import io
import re
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import cv2
import segno
from fastapi import FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from .clipper import filmstrip_path
from .events import ACCEPTED
from .exporter import EXPORT_NAME_RE, ExportError
from .ffmpeg_tools import make_filmstrip
from .network import access_urls
from .services import VERSION, Services
from .storage import Replay

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
REPLAY_ID_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")
MAX_BODY_BYTES = 2048

STATUS_LABELS = {
    "waiting_recording": "Aguardando gravação",
    "processing": "Processando",
    "done": "Concluído",
    "error": "Erro",
}


class EventIn(BaseModel):
    """Corpo enviado pelo ESP32. Campos extras são rejeitados."""

    model_config = ConfigDict(extra="forbid")
    event_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    device_id: str = Field(..., min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_-]+$")
    age_ms: int = Field(0, ge=0, le=600_000)


class ExportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start: float = Field(..., ge=0, le=3600)
    end: float = Field(..., gt=0, le=3600)


def replay_to_json(r: Replay, fps: int) -> Dict[str, Any]:
    base = f"/api/replays/{r.id}"
    done = r.status == "done"
    press_offset = r.press_offset
    if press_offset is None and r.duration:  # replays de versões antigas
        press_offset = min(r.pre_seconds, r.duration)
    return {
        "id": r.id,
        "status": r.status,
        "status_label": STATUS_LABELS.get(r.status, r.status),
        "press_time": r.press_time,
        "press_time_iso": datetime.fromtimestamp(r.press_time).isoformat(timespec="seconds"),
        "pre_seconds": r.pre_seconds,
        "post_seconds": r.post_seconds,
        "device_id": r.device_id,
        "duration": r.duration,
        "size_bytes": r.size_bytes,
        "error": r.error,
        "warnings": r.warnings,
        "presses": r.presses,
        "press_offset": press_offset,
        "fps": fps,
        "video_url": f"{base}/video" if done else None,
        "download_url": f"{base}/video?download=1" if done else None,
        "thumb_url": f"{base}/thumb.jpg" if done and r.thumb_name else None,
        "filmstrip_url": f"{base}/filmstrip.jpg" if done else None,
        "export_url": f"{base}/exports" if done else None,
    }


def create_app(services: Services, manage_lifecycle: bool = True) -> FastAPI:
    settings = services.settings

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if manage_lifecycle:
            services.start()
        try:
            yield
        finally:
            if manage_lifecycle:
                services.stop()

    app = FastAPI(title="Arena Replay", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security(request: Request, call_next):
        length = request.headers.get("content-length")
        if request.method == "POST" and length and length.isdigit() and int(length) > MAX_BODY_BYTES:
            return JSONResponse({"detail": "Corpo da requisição muito grande"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; img-src 'self' data:; media-src 'self'; style-src 'self'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'"
        )
        return response

    # ------------------------------------------------------------ helpers
    def get_replay_or_404(replay_id: str) -> Replay:
        if not REPLAY_ID_RE.match(replay_id):
            raise HTTPException(404, "Replay não encontrado")
        replay = services.db.get_replay(replay_id)
        if replay is None:
            raise HTTPException(404, "Replay não encontrado")
        return replay

    def safe_file(name: Optional[str]) -> Path:
        """Garante que o arquivo está dentro de data/replays (sem path traversal)."""
        if not name:
            raise HTTPException(404, "Arquivo não disponível")
        base = settings.replays_path.resolve()
        path = (base / name).resolve()
        if path.parent != base or not path.is_file():
            raise HTTPException(404, "Arquivo não disponível")
        return path

    # ------------------------------------------------------------- rotas
    @app.get("/api/health")
    def health() -> Dict[str, Any]:
        return {"ok": True, "version": VERSION}

    @app.get("/api/status")
    def status() -> Dict[str, Any]:
        return services.status()

    @app.get("/api/replays")
    def list_replays(limit: int = Query(30, ge=1, le=200)) -> Dict[str, Any]:
        return {"server_time": time.time(),
                "replays": [replay_to_json(r, settings.fps) for r in services.db.list_replays(limit)]}

    @app.get("/api/replays/{replay_id}")
    def get_replay(replay_id: str) -> Dict[str, Any]:
        return replay_to_json(get_replay_or_404(replay_id), settings.fps)

    @app.get("/api/replays/{replay_id}/video")
    def video(replay_id: str, download: bool = False) -> FileResponse:
        replay = get_replay_or_404(replay_id)
        if replay.status != "done":
            raise HTTPException(409, "O replay ainda não está pronto")
        path = safe_file(replay.file_name)
        filename = None
        if download:
            stamp = datetime.fromtimestamp(replay.press_time).strftime("%Y-%m-%d_%H-%M-%S")
            filename = f"arena-replay_{stamp}.mp4"
        # FileResponse responde a requisições Range (necessário para o Safari/iPhone)
        return FileResponse(path, media_type="video/mp4", filename=filename,
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/replays/{replay_id}/thumb.jpg")
    def thumb(replay_id: str) -> FileResponse:
        replay = get_replay_or_404(replay_id)
        return FileResponse(safe_file(replay.thumb_name), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/replays/{replay_id}/filmstrip.jpg")
    def filmstrip(replay_id: str) -> FileResponse:
        replay = get_replay_or_404(replay_id)
        if replay.status != "done":
            raise HTTPException(409, "O replay ainda não está pronto")
        strip = filmstrip_path(settings.replays_path, replay.id)
        if not strip.exists():  # replays antigos: gera na primeira vez
            make_filmstrip(safe_file(replay.file_name), strip)
        return FileResponse(safe_file(strip.name), media_type="image/jpeg",
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.post("/api/replays/{replay_id}/exports")
    def create_export(replay_id: str, body: ExportIn) -> Dict[str, Any]:
        replay = get_replay_or_404(replay_id)
        try:
            res = services.exporter.export(replay, body.start, body.end)
        except ExportError as exc:
            raise HTTPException(exc.status, str(exc)) from exc
        return {"url": f"/api/exports/{res.name}", "start": res.start, "end": res.end,
                "duration": round(res.duration, 2), "size_bytes": res.size_bytes, "cached": res.cached}

    @app.get("/api/exports/{name}")
    def get_export(name: str) -> FileResponse:
        path = services.exporter.path_for(name)
        if path is None:
            raise HTTPException(404, "Recorte não encontrado ou expirado")
        replay_id, a, b = EXPORT_NAME_RE.match(name).groups()
        filename = f"arena-replay_{replay_id[:15]}_{int(a) / 10:.1f}s-{int(b) / 10:.1f}s.mp4"
        return FileResponse(path, media_type="video/mp4", filename=filename,
                            headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/access")
    def access() -> Dict[str, Any]:
        return {"urls": access_urls(settings.port, settings.public_url)}

    @app.get("/api/access/qr.svg")
    def access_qr() -> Response:
        url = access_urls(settings.port, settings.public_url)[0]
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=8, border=2, dark="#0B1B2B", light="#F4F1E8")
        return Response(buf.getvalue(), media_type="image/svg+xml", headers={"Cache-Control": "no-store"})

    @app.post("/api/events")
    def receive_event(event: EventIn, response: Response,
                      x_arena_token: Optional[str] = Header(None, max_length=128)) -> Dict[str, Any]:
        if settings.device_token:
            if not x_arena_token or not hmac.compare_digest(x_arena_token, settings.device_token):
                raise HTTPException(401, "Token do dispositivo inválido")
        result = services.events.register(event.event_id, event.device_id, event.age_ms)
        response.status_code = 201 if result.outcome == ACCEPTED else 200
        return {"status": result.outcome, "event_id": event.event_id, "replay_id": result.replay_id}

    @app.post("/api/events/test")
    def test_event(response: Response) -> Dict[str, Any]:
        if not settings.allow_test_trigger:
            raise HTTPException(403, "Acionamento de teste desativado na configuração")
        event_id = "web-" + secrets.token_hex(8)
        result = services.events.register(event_id, "painel-web", 0)
        response.status_code = 201 if result.outcome == ACCEPTED else 200
        return {"status": result.outcome, "event_id": event_id, "replay_id": result.replay_id}

    @app.get("/api/camera/snapshot.jpg")
    def snapshot() -> Response:
        frame, age = services.capture.latest()
        if frame is None or age > 5:
            raise HTTPException(503, "Câmera sem imagem")
        h, w = frame.shape[:2]
        if w > 640:
            frame = cv2.resize(frame, (640, int(h * 640 / w)), interpolation=cv2.INTER_AREA)
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
        if not ok:
            raise HTTPException(500, "Falha ao gerar a imagem")
        return Response(buf.tobytes(), media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    app.mount("/", StaticFiles(directory=str(WEB_DIR), html=True), name="web")
    return app
