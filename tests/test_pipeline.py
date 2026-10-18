"""Testes de ponta a ponta: câmera sintética -> buffer -> clipe MP4 -> API.

Usam o FFmpeg de verdade. Configuração reduzida (3 s antes + 2 s depois, 320x240)
para rodarem em ~1 minuto; ``test_full_length_clip`` usa os valores reais 10 s + 5 s.
"""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.ffmpeg_tools import probe_video
from app.storage import DONE, ERROR, WAITING, Replay
from tests.conftest import wait_until


def _wait_buffer(svc, seconds):
    assert wait_until(lambda: svc.recorder.status().buffer_seconds >= seconds, timeout=seconds + 15), \
        f"buffer não chegou a {seconds}s: {svc.recorder.status()}"


def _wait_replay(svc, replay_id, timeout=40) -> Replay:
    assert wait_until(lambda: svc.db.get_replay(replay_id).status in (DONE, ERROR), timeout=timeout)
    return svc.db.get_replay(replay_id)


# ---------------------------------------------------------------- captura
def test_capture_is_continuous(services):
    svc = services()
    assert wait_until(lambda: svc.capture.status().frames_total > 30, timeout=10)
    first = svc.capture.status().frames_total
    time.sleep(1.0)
    st = svc.capture.status()
    assert st.state == "ok"
    assert st.frames_total - first >= 10  # ~15 fps
    assert 10 <= st.measured_fps <= 20


def test_buffer_is_bounded(services):
    svc = services(buffer_extra_seconds=1, pre_seconds=1, post_seconds=1)
    time.sleep(7)
    st = svc.recorder.status()
    # mantém ~3 s + segmento em escrita; nunca a partida inteira
    assert st.segments <= 6, st
    files = list(svc.recorder.session_dir.glob("seg_*.ts"))
    assert len(files) <= 7


# ----------------------------------------------------------------- clipes
def test_clip_generation_duration_and_playback(services):
    svc = services()
    _wait_buffer(svc, 4)
    res = svc.events.register("evt-1", "test")
    r = _wait_replay(svc, res.replay_id)
    assert r.status == DONE, r.error
    video = svc.settings.replays_path / r.file_name
    info = probe_video(video)
    assert info is not None
    expected = svc.settings.pre_seconds + svc.settings.post_seconds
    assert expected <= info.duration <= expected + 2.1  # recorte por segmentos: até +1 s de cada lado
    assert (info.width, info.height) == (320, 240)
    head = video.read_bytes()[:4096]
    assert b"ftyp" in head and b"moov" in head  # faststart: índice no início (streaming no navegador)
    assert b"avc1" in video.read_bytes()  # H.264
    assert (svc.settings.replays_path / r.thumb_name).exists()
    assert r.warnings == []


def test_precise_trim_duration(services):
    svc = services(precise_trim=True)
    _wait_buffer(svc, 4)
    r = _wait_replay(svc, svc.events.register("evt-p", "test").replay_id)
    assert r.status == DONE, r.error
    assert abs(r.duration - 5.0) <= 0.5


def test_consecutive_presses_generate_independent_clips(services):
    svc = services()
    _wait_buffer(svc, 4)
    a = svc.events.register("evt-a", "test")
    time.sleep(1.5)  # acima da janela de agrupamento (1 s nos testes)
    b = svc.events.register("evt-b", "test")
    assert a.replay_id != b.replay_id
    ra, rb = _wait_replay(svc, a.replay_id), _wait_replay(svc, b.replay_id)
    assert ra.status == DONE and rb.status == DONE


def test_press_right_after_start_warns_about_short_buffer(services):
    svc = services()
    assert wait_until(lambda: svc.recorder.status().segments >= 1, timeout=10)
    r = _wait_replay(svc, svc.events.register("evt-early", "test").replay_id)
    assert r.status == DONE, r.error
    assert any("antes do lance" in w for w in r.warnings)


def test_camera_loss_is_reported_and_recording_continues(services):
    svc = services()
    _wait_buffer(svc, 4)
    svc.capture.source.fail_reads = True
    time.sleep(3.5)  # frame_stale_seconds = 2 -> tela "SEM SINAL"
    res = svc.events.register("evt-cam", "test")
    time.sleep(0.5)
    svc.capture.source.fail_reads = False
    r = _wait_replay(svc, res.replay_id)
    assert r.status == DONE
    assert any("sem sinal" in w for w in r.warnings)
    assert wait_until(lambda: svc.capture.status().state == "ok", timeout=10)
    assert svc.capture.status().reconnects >= 1


def test_encoder_crash_is_recovered(services):
    svc = services()
    _wait_buffer(svc, 2)
    svc.recorder._proc.kill()  # simula o FFmpeg morrendo
    assert wait_until(lambda: svc.recorder.status().restarts >= 1, timeout=10)
    assert wait_until(lambda: svc.recorder.is_recording, timeout=10)
    _wait_buffer(svc, 4)
    r = _wait_replay(svc, svc.events.register("evt-crash", "test").replay_id)
    assert r.status == DONE, r.error


def test_low_disk_space_fails_with_clear_message(services):
    svc = services(min_free_disk_mb=10**9)
    _wait_buffer(svc, 4)
    r = _wait_replay(svc, svc.events.register("evt-disk", "test").replay_id)
    assert r.status == ERROR and "espaço" in r.error


def test_restart_recovery_marks_interrupted_jobs(make_settings):
    from app.services import Services
    from app.sources import SyntheticSource

    s = make_settings()
    svc = Services.build(s, source=SyntheticSource(s.width, s.height, s.fps))
    svc.db.insert_replay(Replay(id="20260101-000000-abcdef", press_time=1.0, created_at=1.0,
                                updated_at=1.0, status=WAITING, pre_seconds=3, post_seconds=2,
                                device_id="x"))
    (s.tmp_path / "lixo.part.mp4").write_bytes(b"incompleto")
    svc.start()
    try:
        r = svc.db.get_replay("20260101-000000-abcdef")
        assert r.status == ERROR and "reiniciado" in r.error
        assert not (s.tmp_path / "lixo.part.mp4").exists()
    finally:
        svc.stop()


@pytest.mark.slow
def test_full_length_clip(services):
    """Valores reais do MVP: 10 s antes + 5 s depois."""
    svc = services(pre_seconds=10.0, post_seconds=5.0)
    _wait_buffer(svc, 11)
    r = _wait_replay(svc, svc.events.register("evt-full", "test").replay_id, timeout=60)
    assert r.status == DONE, r.error
    assert 15.0 <= r.duration <= 17.1


# -------------------------------------------------------------------- API
@pytest.fixture
def client(services):
    svc = services(device_token="token-de-teste")
    with TestClient(create_app(svc, manage_lifecycle=False)) as c:
        c.svc = svc
        yield c


def _post(client, body, token="token-de-teste"):
    headers = {"X-Arena-Token": token} if token else {}
    return client.post("/api/events", json=body, headers=headers)


def test_api_event_flow_and_download(client):
    _wait_buffer(client.svc, 4)
    r1 = _post(client, {"event_id": "esp-1", "device_id": "quadra1", "age_ms": 100})
    assert r1.status_code == 201 and r1.json()["status"] == "accepted"
    r2 = _post(client, {"event_id": "esp-1", "device_id": "quadra1", "age_ms": 900})
    assert r2.status_code == 200 and r2.json()["status"] == "duplicate"
    rid = r1.json()["replay_id"]
    assert client.get(f"/api/replays/{rid}/video").status_code == 409  # ainda não pronto
    assert wait_until(lambda: client.get(f"/api/replays/{rid}").json()["status"] == "done", timeout=40)

    full = client.get(f"/api/replays/{rid}/video")
    assert full.status_code == 200 and full.headers["content-type"] == "video/mp4"
    part = client.get(f"/api/replays/{rid}/video", headers={"Range": "bytes=0-99"})
    assert part.status_code == 206 and len(part.content) == 100  # exigido pelo Safari/iPhone
    dl = client.get(f"/api/replays/{rid}/video?download=1")
    assert "attachment" in dl.headers["content-disposition"]
    assert client.get(f"/api/replays/{rid}/thumb.jpg").status_code == 200
    assert any(x["id"] == rid for x in client.get("/api/replays").json()["replays"])


def test_api_rejects_bad_token(client):
    assert _post(client, {"event_id": "a", "device_id": "b"}, token="errado").status_code == 401
    assert _post(client, {"event_id": "a", "device_id": "b"}, token=None).status_code == 401


@pytest.mark.parametrize("body", [
    {"event_id": "../../etc/passwd", "device_id": "x"},
    {"event_id": "a" * 65, "device_id": "x"},
    {"event_id": "", "device_id": "x"},
    {"event_id": "ok", "device_id": "x", "age_ms": -1},
    {"event_id": "ok", "device_id": "x", "extra": 1},
    {"device_id": "x"},
])
def test_api_validates_event_body(client, body):
    assert _post(client, body).status_code == 422


def test_api_rejects_large_body(client):
    big = {"event_id": "a", "device_id": "b", "pad": "x" * 5000}
    assert _post(client, big).status_code == 413


@pytest.mark.parametrize("path", [
    "/api/replays/..%2F..%2Fconfig.json/video",
    "/api/replays/../app/config.py",
    "/api/replays/20260101-000000-zzzzzz/video",
    "/api/replays/%2e%2e/thumb.jpg",
])
def test_api_blocks_path_traversal(client, path):
    resp = client.get(path)
    assert resp.status_code == 404
    assert b"Settings" not in resp.content


def test_api_status_snapshot_and_ui(client):
    assert wait_until(lambda: client.get("/api/camera/snapshot.jpg").status_code == 200, timeout=10)
    st = client.get("/api/status").json()
    assert st["camera"]["state"] == "ok" and "token" not in str(st).lower().replace("device_token", "")
    page = client.get("/")
    assert page.status_code == 200 and "Arena Replay" in page.text
    assert "default-src 'self'" in page.headers["content-security-policy"]


def test_test_trigger_can_be_disabled(services):
    svc = services(allow_test_trigger=False)
    with TestClient(create_app(svc, manage_lifecycle=False)) as c:
        assert c.post("/api/events/test").status_code == 403


# ------------------------------------------------- recorte, miniaturas, QR
def _ready_replay(client):
    _wait_buffer(client.svc, 4)
    rid = _post(client, {"event_id": "esp-x", "device_id": "q1"}).json()["replay_id"]
    assert wait_until(lambda: client.get(f"/api/replays/{rid}").json()["status"] == "done", timeout=40)
    return client.get(f"/api/replays/{rid}").json()


def test_replay_json_has_editor_fields(client):
    r = _ready_replay(client)
    # o toque fica ~3 s depois do início (pre_seconds de teste), dentro da duração
    assert 2.0 <= r["press_offset"] <= r["duration"]
    assert r["fps"] == 15
    strip = client.get(r["filmstrip_url"])
    assert strip.status_code == 200 and strip.headers["content-type"] == "image/jpeg"


def test_export_trim_is_precise_and_cached(client):
    r = _ready_replay(client)
    res = client.post(r["export_url"], json={"start": 1.0, "end": 3.5})
    assert res.status_code == 200, res.text
    body = res.json()
    assert abs(body["duration"] - 2.5) <= 0.25 and body["cached"] is False
    dl = client.get(body["url"])
    assert dl.status_code == 200 and "attachment" in dl.headers["content-disposition"]
    assert ".mp4" in dl.headers["content-disposition"]
    path = client.svc.settings.exports_path / body["url"].rsplit("/", 1)[1]
    info = probe_video(path)
    assert info is not None and abs(info.duration - 2.5) <= 0.25
    again = client.post(r["export_url"], json={"start": 1.0, "end": 3.5}).json()
    assert again["cached"] is True and again["url"] == body["url"]


@pytest.mark.parametrize("body,code", [
    ({"start": 2.0, "end": 2.2}, 400),        # curto demais
    ({"start": -1, "end": 2}, 422),
    ({"start": 1, "end": 2, "x": 1}, 422),
    ({"start": "a", "end": 2}, 422),
])
def test_export_validation(client, body, code):
    r = _ready_replay(client)
    assert client.post(r["export_url"], json=body).status_code == code


def test_export_end_is_clamped_to_duration(client):
    r = _ready_replay(client)
    body = client.post(r["export_url"], json={"start": 0, "end": 999}).json()
    assert body["end"] <= r["duration"] + 0.05


def test_export_not_ready_and_unknown(client):
    _wait_buffer(client.svc, 2)
    rid = _post(client, {"event_id": "esp-wait", "device_id": "q1"}).json()["replay_id"]
    assert client.post(f"/api/replays/{rid}/exports", json={"start": 0, "end": 2}).status_code == 409
    assert client.post("/api/replays/20260101-000000-abcdef/exports", json={"start": 0, "end": 2}).status_code == 404


@pytest.mark.parametrize("name", [
    "..%2F..%2Fconfig.json", "x.mp4", "20260101-000000-abcdef_00010-00020.mp4",
    "20260101-000000-abcdef_00010-00020.mp4%00.txt",
])
def test_export_download_blocks_bad_names(client, name):
    assert client.get(f"/api/exports/{name}").status_code == 404


def test_access_qr(client):
    a = client.get("/api/access").json()
    assert a["urls"] and a["urls"][0].startswith("http")
    qr = client.get("/api/access/qr.svg")
    assert qr.status_code == 200 and qr.headers["content-type"].startswith("image/svg+xml")
    assert b"<svg" in qr.content


def test_old_exports_are_cleaned(services):
    import os
    svc = services()
    old = svc.settings.exports_path / "20260101-000000-abcdef_00000-00010.mp4"
    old.write_bytes(b"x")
    os.utime(old, (1, 1))
    svc.exporter.cleanup()
    assert not old.exists()
