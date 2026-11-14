"""Pruebas de la UI de filas con clips sintéticos (no dependen de samples locales)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

pytest.importorskip("PySide6")


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


def _wait(app, predicate, timeout: float = 180.0) -> bool:
    """Procesa eventos hasta que se cumpla `predicate` (no bloquea el hilo de UI)."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.fixture
def populated(window, app, clips_factory):
    """Ventana con clips sintéticos encadenados: cada uno empieza donde acaba el previo."""
    paths = [
        clips_factory("a.mp4", first_color="red", last_color="lime"),
        clips_factory("b.mp4", first_color="lime", last_color="blue"),
        clips_factory("c.mp4", first_color="blue", last_color="yellow"),
    ]
    # Camino real de ingesta: probe worker -> frames worker -> render.
    window._ingest_paths(paths)
    assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 3)
    return window


def test_rows_are_built_from_synthetic_clips(populated):
    """La suite no depende de samples: los clips se generan en el propio test."""
    rows = populated.rows.rows()
    assert len(rows) == 3
    assert all(row.active is not None for row in rows)
    assert all(tile.thumb_first.has_frame for row in rows for tile in row.tiles)


def test_exactly_one_selected_row(populated):
    window = populated
    window._on_row_selected(2)
    assert sum(1 for r in window.rows.rows() if r.is_selected()) == 1
    assert window.rows.rows()[2].is_selected()
    assert len(window._selected_media()) == len(window.rows.rows())


def test_order_chains_by_continuity(populated):
    """Cada clip empieza donde acaba el anterior, así que el orden es a -> b -> c."""
    window = populated
    names = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert names == ["a.mp4", "b.mp4", "c.mp4"]


def test_downward_drag_lands_on_the_drop_target(populated):
    """Arrastrar hacia abajo no puede saltarse un hueco (off-by-one clásico)."""
    window = populated
    before = [r.group.reference.media.path.name for r in window.rows.rows()]
    window.rows.reorder(0, 2)
    after = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert after == [before[1], before[0], before[2]]
    assert [m.path.name for m in window._selected_media()] == after


def test_upward_drag_lands_on_the_drop_target(populated):
    window = populated
    before = [r.group.reference.media.path.name for r in window.rows.rows()]
    window.rows.reorder(2, 0)
    after = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert after == [before[2], before[0], before[1]]


def test_manual_order_survives_ingesting_another_clip(populated, app, clips_factory):
    """
    El orden manual debe sobrevivir a la ingesta de un clip nuevo.

    Se captura el orden pretendido de forma independiente del estado del
    widget: si la reconstrucción vuelve a encadenar por contenido, la lista
    deja de coincidir con esta secuencia y el test falla.
    """
    window = populated
    window.rows.reorder(2, 0)
    intended = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert intended == ["c.mp4", "a.mp4", "b.mp4"]

    extra = clips_factory("d.mp4", first_color="pink", last_color="cyan")
    window._ingest_paths([extra])
    assert _wait(app, lambda: not window._busy)

    after = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert after[:3] == intended, "la ingesta no puede revertir el orden manual"
    assert after[3] == "d.mp4"


def test_removing_active_clip_keeps_the_others(populated):
    window = populated
    window._on_row_selected(0)
    victim = window.rows.rows()[0].active.media.path
    window._remove_selection()
    assert victim not in window._media
    assert len(window.rows.rows()) == 2


def test_busy_state_clears_when_every_clip_is_cached(populated, app):
    """Reentrar con todas las firmas cacheadas no puede dejar la UI bloqueada."""
    window = populated
    window._set_busy(True, "Analizando clips…")
    window._start_frames()
    assert _wait(app, lambda: not window._busy, timeout=10)
    assert window.btn_add.isEnabled()
    assert window.threshold_box.isEnabled()

def test_play_opens_system_player(populated, monkeypatch):
    window = populated
    opened: list[str] = []
    monkeypatch.setattr(
        "video_concat.ui.main_window.QDesktopServices.openUrl",
        lambda url: opened.append(url.toLocalFile()) or True,
    )
    row = window.rows.rows()[0]
    row.play_requested.emit(row.active.media)
    assert opened == [str(row.active.media.path)]


def test_rows_show_every_member_inline(populated):
    for row in populated.rows.rows():
        assert len(row.tiles) == len(row.group.members)
        for tile in row.tiles:
            assert tile.thumb_first.has_frame
            assert tile.thumb_last.has_frame


def test_tile_click_chooses_active_member(app):
    """Elegir variante es determinista: se construye el grupo en el test."""
    from video_concat.ui.widgets import ClipGroupRow

    row = ClipGroupRow(_fake_group(3), 0)
    row.tiles[1].activated.emit(row.tiles[1])
    assert row.active_index == 1
    assert row.active.media.path == row.group.members[1].media.path


def _fake_group(count: int):
    """Grupo sintético de variantes: la variante i empieza en i/10 y acaba en (i+5)/10."""
    from video_concat.core.frames import GRID, EdgeFrames, FrameSignature
    from video_concat.core.grouping import ClipGroup, GroupedClip
    from video_concat.core.probe import MediaInfo, StreamInfo

    def signature(value: float) -> FrameSignature:
        return FrameSignature(rgb=tuple([value, value, value] * (GRID * GRID)))

    def rgb(value: float) -> bytes:
        channel = int(value * 255)
        return bytes([channel, channel, channel]) * (128 * 128)

    members = []
    for i in range(count):
        path = Path(f"variante{i}.mp4")
        start, end = i / 10, (i + 5) / 10
        frames = EdgeFrames(
            first=signature(start),
            last=signature(end),
            first_rgb=rgb(start),
            last_rgb=rgb(end),
        )
        media = MediaInfo(
            path=path,
            duration=4.0,
            format_name="mov,mp4",
            video=StreamInfo(codec_name="h264", codec_type="video", width=480, height=640),
            audio=None,
            size_bytes=1024,
        )
        members.append(GroupedClip(media=media, frames=frames))
    return ClipGroup(tuple(members), 0.99, "casi coincidencia (0.95)")


def test_filter_never_concats_a_hidden_active_tile(app):
    """Si el filtro oculta la variante activa, se activa la primera visible."""
    from video_concat.ui.widgets import ClipGroupRow

    group = _fake_group(4)
    row = ClipGroupRow(group, 0)
    row.set_active_member(3)
    assert row.active_index == 3

    row.apply_follow_filter({group.members[0].media.path})
    assert len(row.visible_tiles) == 1
    assert row.active_index == 0
    assert row.active.media.path == group.members[0].media.path


def test_filter_keeps_active_tile_when_it_survives(app):
    from video_concat.ui.widgets import ClipGroupRow

    group = _fake_group(4)
    row = ClipGroupRow(group, 0)
    row.set_active_member(2)
    row.apply_follow_filter({group.members[2].media.path, group.members[0].media.path})
    assert row.active_index == 2
    assert len(row.visible_tiles) == 2
    assert "2 de 4" in row.title.text()


def test_dragging_a_row_with_variants_keeps_every_member(populated, app):
    """Arrastrar no puede borrar del modelo las variantes no activas."""
    window = populated
    rows = window.rows.rows()
    total_members = sum(len(r.group.members) for r in rows)
    assert total_members == len(window._media)

    window.rows.reorder(0, 2)
    assert _wait(app, lambda: not window._busy, timeout=10)
    after_members = sum(len(r.group.members) for r in window.rows.rows())
    assert after_members == total_members, (
        "el arrastre no puede perder miembros del modelo"
    )
    assert set(window._media) == {
        m.media.path for row in window.rows.rows() for m in row.group.members
    }


def test_close_does_not_terminate_running_threads(populated, monkeypatch):
    """Cerrar cancela de forma cooperativa: nunca mata el hilo."""
    window = populated
    terminated: list[bool] = []

    class StubThread:
        def isRunning(self):
            return False

        def quit(self):
            pass

        def wait(self, _ms):
            return True

        def terminate(self):
            terminated.append(True)

    window._frames_thread = StubThread()
    window._probe_thread = None
    window._concat_thread = None

    from PySide6.QtGui import QCloseEvent

    window.closeEvent(QCloseEvent())
    assert terminated == [], "no se debe forzar terminate() sobre hilos vivos"
