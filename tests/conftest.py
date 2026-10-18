from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Settings, load_settings  # noqa: E402
from app.services import Services  # noqa: E402
from app.sources import SyntheticSource  # noqa: E402

# Configuração reduzida para os testes rodarem rápido em qualquer notebook
FAST = dict(
    camera_source="synthetic", width=320, height=240, fps=15, pre_seconds=3.0, post_seconds=2.0,
    buffer_extra_seconds=4, min_free_disk_mb=1, merge_window_seconds=1.0, x264_preset="ultrafast",
)


@pytest.fixture
def make_settings(tmp_path: Path) -> Callable[..., Settings]:
    def factory(**overrides) -> Settings:
        values = {**FAST, "data_dir": str(tmp_path / "data"), **overrides}
        return load_settings(config_file=tmp_path / "nao-existe.json", root=tmp_path, overrides=values)
    return factory


@pytest.fixture
def services(make_settings):
    started = []

    def factory(**overrides) -> Services:
        s = make_settings(**overrides)
        svc = Services.build(s, source=SyntheticSource(s.width, s.height, s.fps))
        svc.start()
        started.append(svc)
        return svc

    yield factory
    for svc in started:
        svc.stop()


def wait_until(predicate: Callable[[], bool], timeout: float, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False
