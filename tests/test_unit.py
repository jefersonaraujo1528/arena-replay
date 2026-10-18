"""Testes rápidos, sem câmera nem FFmpeg."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from app.config import load_settings
from app.events import ACCEPTED, DUPLICATE, MERGED, EventService
from app.storage import DONE, ERROR, WAITING, Database, Replay

WEB = Path(__file__).resolve().parent.parent / "web"


# ------------------------------------------------------------------ config
def test_config_defaults_match_requirements(tmp_path):
    s = load_settings(config_file=tmp_path / "x.json", root=tmp_path)
    assert s.pre_seconds == 10 and s.post_seconds == 5
    assert s.device_token == ""


def test_config_file_and_env_override(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    cfg.write_text(json.dumps({"_comentario": "ok", "pre_seconds": 8, "fps": 25}), encoding="utf-8")
    monkeypatch.setenv("ARENA_FPS", "20")
    monkeypatch.setenv("ARENA_PRECISE_TRIM", "true")
    s = load_settings(config_file=cfg, root=tmp_path)
    assert s.pre_seconds == 8.0
    assert s.fps == 20  # variável de ambiente vence o arquivo
    assert s.precise_trim is True


def test_config_rejects_invalid_values(tmp_path):
    with pytest.raises(ValueError):
        load_settings(config_file=tmp_path / "x.json", root=tmp_path, overrides={"fps": 0})


def test_public_summary_hides_token(tmp_path):
    s = load_settings(config_file=tmp_path / "x.json", root=tmp_path, overrides={"device_token": "abc"})
    assert "abc" not in json.dumps(s.public_summary())


# ----------------------------------------------------------------- storage
def _replay(rid: str, t: float, status: str = WAITING) -> Replay:
    return Replay(id=rid, press_time=t, created_at=t, updated_at=t, status=status,
                  pre_seconds=10, post_seconds=5, device_id="d1")


def test_database_persists_across_reopen(tmp_path):
    db = Database(tmp_path / "a.db")
    db.insert_replay(_replay("20260101-000000-aaaaaa", 100.0))
    db.update_replay("20260101-000000-aaaaaa", status=DONE, warnings=["x"], duration=15.0)
    db.close()
    db2 = Database(tmp_path / "a.db")
    r = db2.get_replay("20260101-000000-aaaaaa")
    assert r is not None and r.status == DONE and r.warnings == ["x"] and r.duration == 15.0


def test_database_update_rejects_unknown_columns(tmp_path):
    db = Database(tmp_path / "a.db")
    with pytest.raises(ValueError):
        db.update_replay("x", id="hack")


def test_retention_query_returns_oldest(tmp_path):
    db = Database(tmp_path / "a.db")
    for i in range(5):
        db.insert_replay(_replay(f"20260101-00000{i}-aaaaaa", float(i)))
    old = db.oldest_replays_beyond(3)
    assert [r.press_time for r in old] == [1.0, 0.0]


# ------------------------------------------------------------------ events
class FakeClips:
    def __init__(self):
        self.submitted = []

    def submit(self, replay):
        self.submitted.append(replay)


class FakeClock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


def _events(tmp_path, **kw):
    s = load_settings(config_file=tmp_path / "x.json", root=tmp_path,
                      overrides={"merge_window_seconds": 3.0, **kw})
    clock, clips = FakeClock(), FakeClips()
    return EventService(s, Database(tmp_path / "e.db"), clips, clock=clock), clips, clock


def test_duplicate_event_creates_single_replay(tmp_path):
    svc, clips, clock = _events(tmp_path)
    first = svc.register("abc-1", "esp")
    clock.t += 10
    again = svc.register("abc-1", "esp", age_ms=10_000)  # retentativa do ESP32
    assert first.outcome == ACCEPTED and again.outcome == DUPLICATE
    assert again.replay_id == first.replay_id
    assert len(clips.submitted) == 1


def test_close_presses_are_merged(tmp_path):
    svc, clips, clock = _events(tmp_path)
    a = svc.register("e1", "esp")
    clock.t += 1.5
    b = svc.register("e2", "esp")
    clock.t += 5
    c = svc.register("e3", "esp")
    assert (a.outcome, b.outcome, c.outcome) == (ACCEPTED, MERGED, ACCEPTED)
    assert b.replay_id == a.replay_id and c.replay_id != a.replay_id
    assert svc.db.get_replay(a.replay_id).presses == 2
    assert len(clips.submitted) == 2


def test_age_ms_moves_press_time_back_and_is_clamped(tmp_path):
    svc, _, clock = _events(tmp_path, max_event_age_ms=5000)
    r = svc.register("e1", "esp", age_ms=1500)
    assert r.press_time == pytest.approx(clock.t - 1.5)
    clock.t += 100
    r2 = svc.register("e2", "esp", age_ms=999_999)
    assert r2.press_time == pytest.approx(clock.t - 5.0)


def test_error_replay_is_not_merge_target(tmp_path):
    svc, _, clock = _events(tmp_path)
    a = svc.register("e1", "esp")
    svc.db.update_replay(a.replay_id, status=ERROR)
    clock.t += 1
    assert svc.register("e2", "esp").outcome == ACCEPTED


# --------------------------------------------------------------- offline
@pytest.mark.parametrize("name", ["index.html", "app.js", "style.css", "manifest.json"])
def test_web_ui_has_no_external_dependencies(name):
    text = (WEB / name).read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text and "//cdn" not in text


def test_database_migrates_old_schema(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    con = sqlite3.connect(str(path))
    con.execute("CREATE TABLE replays (id TEXT PRIMARY KEY, press_time REAL NOT NULL, created_at REAL NOT NULL,"
                " updated_at REAL NOT NULL, status TEXT NOT NULL, pre_seconds REAL NOT NULL, post_seconds REAL NOT NULL,"
                " device_id TEXT NOT NULL, file_name TEXT, thumb_name TEXT, duration REAL, size_bytes INTEGER,"
                " error TEXT, warnings TEXT NOT NULL DEFAULT '[]', presses INTEGER NOT NULL DEFAULT 1)")
    con.commit(); con.close()
    db = Database(path)
    db.insert_replay(_replay("20260101-000000-aaaaaa", 1.0))
    db.update_replay("20260101-000000-aaaaaa", press_offset=3.2)
    assert db.get_replay("20260101-000000-aaaaaa").press_offset == 3.2
