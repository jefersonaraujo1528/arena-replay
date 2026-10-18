"""Configuração do Arena Replay.

Ordem de prioridade (a última vence):
1. Valores padrão definidos em ``Settings``.
2. Arquivo ``config.json`` na raiz do projeto (não versionado).
3. Variáveis de ambiente ``ARENA_*`` (ex.: ``ARENA_DEVICE_TOKEN``).

O arquivo ``config.example.json`` documenta todas as chaves.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent


@dataclass
class Settings:
    # --- Câmera -----------------------------------------------------------
    # "0", "1"... = índice da webcam | "rtsp://..." = câmera IP | "synthetic" = padrão de teste
    camera_source: str = "0"
    # "auto" | "dshow" | "msmf" | "avfoundation" | "v4l2" | "ffmpeg"
    camera_backend: str = "auto"
    width: int = 1280
    height: int = 720
    fps: int = 30
    # Sem quadro novo por este tempo => a gravação usa a tela "SEM SINAL"
    frame_stale_seconds: float = 2.0
    overlay_clock: bool = True

    # --- Buffer em disco (segmentos) --------------------------------------
    segment_seconds: int = 1
    # Quanto tempo de segmentos manter além do necessário para um replay
    buffer_extra_seconds: int = 30

    # --- Encoder ------------------------------------------------------------
    ffmpeg_path: str = ""  # vazio = PATH do sistema ou imageio-ffmpeg
    x264_preset: str = "veryfast"
    x264_crf: int = 23

    # --- Replays ------------------------------------------------------------
    pre_seconds: float = 10.0
    post_seconds: float = 5.0
    # false = recorte por segmentos (rápido, sem recodificar, até +1 s de cada lado)
    # true  = recorte exato (recodifica o clipe, usa mais CPU)
    precise_trim: bool = False
    # Acionamentos mais próximos que isto viram o mesmo replay
    merge_window_seconds: float = 3.0
    # Idade máxima aceita para um evento atrasado (retentativas do ESP32)
    max_event_age_ms: int = 30000
    clip_workers: int = 1
    max_replays: int = 300
    min_free_disk_mb: int = 1024

    # --- Rede / API ---------------------------------------------------------
    host: str = "0.0.0.0"
    port: int = 8000
    # Token compartilhado com o ESP32 (cabeçalho X-Arena-Token). Vazio = sem autenticação.
    device_token: str = ""
    allow_test_trigger: bool = True
    # Endereço exibido no QR Code (vazio = detecta o IP do notebook na rede local)
    public_url: str = ""

    # --- Recortes para download ---------------------------------------------
    export_keep_hours: float = 24.0
    export_max_seconds: float = 120.0

    # --- Diretórios ---------------------------------------------------------
    data_dir: str = "data"

    # Campos derivados (não vêm do JSON)
    _root: Path = field(default=PROJECT_ROOT, repr=False, compare=False)

    # -----------------------------------------------------------------------
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        return p if p.is_absolute() else (self._root / p)

    @property
    def buffer_path(self) -> Path:
        return self.data_path / "buffer"

    @property
    def replays_path(self) -> Path:
        return self.data_path / "replays"

    @property
    def tmp_path(self) -> Path:
        return self.data_path / "tmp"

    @property
    def exports_path(self) -> Path:
        return self.data_path / "exports"

    @property
    def logs_path(self) -> Path:
        return self.data_path / "logs"

    @property
    def db_path(self) -> Path:
        return self.data_path / "arena_replay.db"

    @property
    def frames_per_segment(self) -> int:
        return max(1, int(round(self.fps * self.segment_seconds)))

    @property
    def buffer_keep_seconds(self) -> float:
        return self.pre_seconds + self.post_seconds + self.buffer_extra_seconds

    def validate(self) -> None:
        errors = []
        if not (1 <= self.fps <= 60):
            errors.append("fps deve estar entre 1 e 60")
        if self.width % 2 or self.height % 2 or self.width < 160 or self.height < 120:
            errors.append("width/height devem ser pares e >= 160x120")
        if not (1 <= self.segment_seconds <= 4):
            errors.append("segment_seconds deve estar entre 1 e 4")
        if not (0 <= self.pre_seconds <= 60) or not (0 <= self.post_seconds <= 60):
            errors.append("pre_seconds/post_seconds devem estar entre 0 e 60")
        if self.pre_seconds + self.post_seconds < 1:
            errors.append("o replay precisa ter pelo menos 1 segundo")
        if not (1 <= self.port <= 65535):
            errors.append("port inválida")
        if self.clip_workers < 1:
            errors.append("clip_workers deve ser >= 1")
        if errors:
            raise ValueError("Configuração inválida: " + "; ".join(errors))

    def public_summary(self) -> Dict[str, Any]:
        """Configuração segura para exibir na API (sem token)."""
        data = asdict(self)
        data.pop("_root", None)
        data["device_token"] = "definido" if self.device_token else "não definido"
        return data


def _coerce(name: str, raw: Any, current: Any) -> Any:
    if isinstance(current, bool):
        if isinstance(raw, bool):
            return raw
        return str(raw).strip().lower() in ("1", "true", "yes", "sim", "on")
    if isinstance(current, int):
        return int(raw)
    if isinstance(current, float):
        return float(raw)
    return str(raw)


def load_settings(config_file: Optional[Path] = None, root: Optional[Path] = None,
                  overrides: Optional[Dict[str, Any]] = None) -> Settings:
    settings = Settings()
    if root is not None:
        settings._root = Path(root)
    known = {f.name: f for f in fields(Settings) if not f.name.startswith("_")}

    cfg_path = config_file or (settings._root / "config.json")
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"config.json inválido (linha {exc.lineno}): {exc.msg}") from exc
        for key, value in data.items():
            if key.startswith("_"):
                continue  # comentários no JSON
            if key not in known:
                log.warning("Chave desconhecida em config.json ignorada: %s", key)
                continue
            setattr(settings, key, _coerce(key, value, getattr(settings, key)))

    for name in known:
        env_value = os.environ.get("ARENA_" + name.upper())
        if env_value is not None:
            setattr(settings, name, _coerce(name, env_value, getattr(settings, name)))

    for key, value in (overrides or {}).items():
        setattr(settings, key, value)

    settings.validate()
    return settings
