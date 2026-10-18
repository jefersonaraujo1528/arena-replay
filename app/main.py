"""Ponto de entrada: ``python -m app.main``"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import uvicorn

from .api import create_app
from .config import load_settings
from .logging_setup import setup_logging
from .network import access_urls
from .services import VERSION, Services

log = logging.getLogger("arena_replay")


def main() -> int:
    parser = argparse.ArgumentParser(description="Arena Replay - servidor local")
    parser.add_argument("--config", type=Path, help="caminho do config.json")
    parser.add_argument("--synthetic", action="store_true", help="usa câmera de teste (sem webcam)")
    parser.add_argument("--camera", help="sobrescreve camera_source (ex.: 0, 1, rtsp://...)")
    args = parser.parse_args()

    overrides = {}
    if args.synthetic:
        overrides["camera_source"] = "synthetic"
    if args.camera:
        overrides["camera_source"] = args.camera
    try:
        settings = load_settings(args.config, overrides=overrides)
    except ValueError as exc:
        print(f"ERRO: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.logs_path)
    log.info("Arena Replay %s iniciando (câmera=%s, %dx%d@%d, replay=%gs+%gs)",
             VERSION, settings.camera_source, settings.width, settings.height, settings.fps,
             settings.pre_seconds, settings.post_seconds)
    try:
        services = Services.build(settings)
    except Exception as exc:
        log.error("Falha ao iniciar: %s", exc)
        return 1

    app = create_app(services)
    print("\n  Arena Replay rodando. Abra no navegador:")
    print(f"    Neste computador:  http://localhost:{settings.port}")
    for url in access_urls(settings.port, settings.public_url):
        print(f"    Celular (mesma rede): {url}")
    print("  Ctrl+C para encerrar.\n")
    uvicorn.run(app, host=settings.host, port=settings.port, log_config=None, access_log=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
