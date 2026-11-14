"""Prueba de humo de la UI de filas (requiere Qt y ffmpeg)."""

import os
import sys
from pathlib import Path

import pytest

pytest.importorskip("PySide6")

SAMPLES = sorted((Path(__file__).resolve().parents[1] / "src").glob("*.mp4"))


@pytest.fixture(scope="module")
def app():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    instance = QApplication.instance() or QApplication(sys.argv[:1])
    yield instance


@pytest.fixture
def window(app, monkeypatch):
    monkeypatch.setattr(
        "video_concat.ui.main_window.detect_gpu",
        lambda: __import__(
            "video_concat.core.gpu", fromlist=["GpuCapabilities"]
        ).GpuCapabilities(False, False, False, None, ("test",)),
    )
    from video_concat.ui.main_window import MainWindow

    win = MainWindow()
    yield win
    win.close()


def _wait(app, predicate, timeout: float = 120.0) -> bool:
    """Procesa eventos hasta que se cumpla `predicate` (no bloquea el hilo de UI)."""
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _load(window, app):
    """Simula la ingesta completa: probe + fotogramas + render de filas."""
    from video_concat.core.probe import probe_many

    medias = probe_many(SAMPLES)
    for media in medias:
        window._media[media.path] = media
    window._start_frames()
    assert _wait(app, lambda: len(window.rows.rows()) == len(medias))
    return medias


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_rows_one_per_clip_and_single_selection(window, app):
    medias = _load(window, app)
    rows = window.rows.rows()
    assert len(rows) == len(medias)
    assert all(r.active is not None for r in rows)

    window._on_row_selected(2)
    assert sum(1 for r in window.rows.rows() if r.is_selected()) == 1
    assert window.rows.rows()[2].is_selected()
    assert len(window._selected_media()) == len(rows)


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_order_chains_by_continuity(window, app):
    _load(window, app)
    names = [r.group.reference.media.path.name[5:10] for r in window.rows.rows()]
    assert names == ["-6585", "-3798", "-3868", "-1274", "-3053", "-3345"]


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_drag_reorder_updates_concat_order(window, app):
    _load(window, app)
    before = [r.group.reference.media.path for r in window.rows.rows()]
    window.rows.reorder(3, 0)
    after = [r.group.reference.media.path for r in window.rows.rows()]
    assert after[0] == before[3]
    assert len(after) == len(before)
    assert [m.path for m in window._selected_media()] == after


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_manual_order_survives_after_reorder(window, app):
    """El orden manual no debe ser sobrescrito por el encadenado automático."""
    _load(window, app)
    window.rows.reorder(5, 0)
    first_after_drag = window.rows.rows()[0].group.reference.media.path
    window._rebuild_groups(keep_order=True)
    assert window.rows.rows()[0].group.reference.media.path == first_after_drag


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_row_play_uses_shared_inline_player(window, app):
    _load(window, app)
    row = window.rows.rows()[0]
    row.player.play_requested.emit(row.active.media)
    assert window._playback is not None
    assert window._playback.current is not None
    assert window._playback.current.path == row.active.media.path
    window._playback.stop()
    assert window._playback.current is None


@pytest.mark.skipif(len(SAMPLES) < 2, reason="faltan clips de muestra")
def test_removing_row_removes_clip(window, app):
    medias = _load(window, app)
    window._on_row_selected(0)
    victim = window.rows.rows()[0].active.media.path
    window._remove_selection()
    assert victim not in window._media
    assert len(window.rows.rows()) == len(medias) - 1
