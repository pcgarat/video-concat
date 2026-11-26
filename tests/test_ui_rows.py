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


@pytest.fixture
def variants(window, app, clips_factory):
    """
    Ventana con dos variantes reales del mismo plano (mismo primer y último
    fotograma) más un clip independiente: así el agrupado las ve como una fila
    de dos tarjetas y el botón ↓ tiene de dónde sacar el clip.
    """
    paths = [
        clips_factory("v1.mp4", first_color="red", last_color="blue"),
        clips_factory("v2.mp4", first_color="red", last_color="blue"),
        clips_factory("solo.mp4", first_color="lime", last_color="yellow"),
    ]
    window._ingest_paths(paths)
    assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 2)
    return window


def test_variants_fixture_gives_a_two_tile_row(variants):
    sizes = sorted(len(row.tiles) for row in variants.rows.rows())
    assert sizes == [1, 2]


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


def test_order_chains_by_continuity(window, app, clips_factory):
    """Encendido el automático, el orden de filas sale por continuidad, no por ingesta."""
    window._on_sort_toggled(True)
    assert window._auto_sort is True

    # Ingesta desordenada: c, a, b. Cada uno empieza donde acaba el anterior.
    paths = [
        clips_factory("c.mp4", first_color="blue", last_color="yellow"),
        clips_factory("a.mp4", first_color="red", last_color="lime"),
        clips_factory("b.mp4", first_color="lime", last_color="blue"),
    ]
    window._ingest_paths(paths)
    assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 3)

    names = [r.group.reference.media.path.name for r in window.rows.rows()]
    assert names == ["a.mp4", "b.mp4", "c.mp4"]


def test_order_keeps_ingestion_order_when_sort_is_off(populated):
    """Apagado por defecto: se respeta el orden en que se añadieron los clips."""
    window = populated
    assert window._auto_sort is False
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


class TestAutoSortSwitch:
    """El interruptor «orden automático» decide quién manda: fotogramas u orden manual."""

    def test_auto_sort_is_off_by_default(self, populated):
        assert populated._auto_sort is False
        assert not populated.sort_switch.isChecked()

    def test_auto_sort_chains_new_clips_by_continuity(self, window, app, clips_factory):
        """Encendido: reordena por fotogramas y coloca el clip nuevo donde toca."""
        window._on_sort_toggled(True)
        # Ingesta desordenada: c, a, b.
        paths = [
            clips_factory("c.mp4", first_color="blue", last_color="yellow"),
            clips_factory("a.mp4", first_color="red", last_color="lime"),
            clips_factory("b.mp4", first_color="lime", last_color="blue"),
        ]
        window._ingest_paths(paths)
        assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 3)
        assert [r.active.media.path.name for r in window.rows.rows()] == [
            "a.mp4",
            "b.mp4",
            "c.mp4",
        ]
        # Magenta → rojo: encaja justo antes de a.mp4 (que arranca en rojo).
        extra = clips_factory("z.mp4", first_color="magenta", last_color="red")
        window._ingest_paths([extra])
        assert _wait(app, lambda: not window._busy)
        assert [r.active.media.path.name for r in window.rows.rows()] == [
            "z.mp4",
            "a.mp4",
            "b.mp4",
            "c.mp4",
        ]

    def test_auto_sort_off_appends_new_clips_and_keeps_manual_order(
        self, populated, app, clips_factory
    ):
        """Apagado: el orden manual manda y los clips nuevos entran al final."""
        window = populated
        window.rows.reorder(2, 0)
        intended = [r.group.reference.media.path.name for r in window.rows.rows()]
        assert intended == ["c.mp4", "a.mp4", "b.mp4"]
        assert window._auto_sort is False, "arrastrar apaga el automático"

        # Continúa a c.mp4, pero con el automático apagado no puede colarse delante.
        extra = clips_factory("z.mp4", first_color="yellow", last_color="magenta")
        window._ingest_paths([extra])
        assert _wait(app, lambda: not window._busy)

        after = [r.group.reference.media.path.name for r in window.rows.rows()]
        assert after[:3] == intended
        assert after[3] == "z.mp4"

    def test_turning_sort_on_again_discards_manual_order(self, populated):
        window = populated
        window.rows.reorder(2, 0)
        assert window._auto_sort is False

        window._on_sort_toggled(True)

        assert window._auto_sort is True
        assert window.sort_switch.isChecked()
        assert window._order == [], "encender el automático descarta el orden manual"
        assert [r.active.media.path.name for r in window.rows.rows()] == [
            "a.mp4",
            "b.mp4",
            "c.mp4",
        ]

    def test_turning_sort_off_freezes_the_visible_order(self, populated):
        window = populated
        window._on_sort_toggled(True)
        window._on_sort_toggled(False)

        assert window._auto_sort is False
        assert [m.path.name for m in window._selected_media()] == ["a.mp4", "b.mp4", "c.mp4"]
        assert window._order, "el orden visible se congela para no perderse"

    def test_manual_order_survives_a_rebuild_with_sort_off(self, populated):
        """Apagado, un recálculo (umbral) no puede reordenar las filas."""
        window = populated
        window.rows.reorder(2, 0)
        intended = [r.group.reference.media.path.name for r in window.rows.rows()]

        window._on_threshold_changed(2)

        assert [r.group.reference.media.path.name for r in window.rows.rows()] == intended

    def test_threshold_reorders_when_sort_is_on(self, populated):
        window = populated
        window._on_sort_toggled(True)
        assert window._auto_sort is True
        window._on_threshold_changed(2)
        assert [r.active.media.path.name for r in window.rows.rows()] == [
            "a.mp4",
            "b.mp4",
            "c.mp4",
        ]

    def test_detaching_a_clip_surrenders_auto_sort(self, variants):
        window = variants
        window._on_sort_toggled(True)
        assert window._auto_sort is True

        pair = next(row for row in window.rows.rows() if len(row.tiles) == 2)
        window._detach_clip(pair.tiles[1].clip.media.path)

        assert window._auto_sort is False
        assert not window.sort_switch.isChecked()
        assert "Orden manual" in window.status_label.text()

    def test_clear_all_keeps_the_sort_setting(self, populated):
        window = populated
        window._on_sort_toggled(False)
        window._clear_all()
        assert window._auto_sort is False, "vaciar la lista no cambia la preferencia"
        assert not window.sort_switch.isChecked()

    def test_sort_switch_is_disabled_while_busy(self, populated):
        window = populated
        window._set_busy(True, "Analizando clips…")
        assert not window.sort_switch.isEnabled()
        window._set_busy(False, "Listo")
        assert window.sort_switch.isEnabled()


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


def test_montage_shows_a_segment_per_visible_row(populated, app):
    """El montaje es el orden de concatenación: un tramo por clip activo."""
    window = populated
    app.processEvents()
    montage = window.montage
    expected = [row.active.media.path for row in window.rows.rows() if not row.isHidden()]
    assert montage.clip_paths() == expected
    assert len(montage.clip_paths()) == 3


def test_montage_segments_cover_the_track_without_overlaps(populated, app):
    window = populated
    app.processEvents()
    rects = [window.montage.rect_for(p) for p in window.montage.clip_paths()]
    assert all(rect is not None and rect.width() > 0 for rect in rects)
    for left, right in zip(rects, rects[1:], strict=False):
        assert left.right() <= right.left() + 0.01, "los tramos no se solapan"
    usable = window.montage.width() - 2 * 8
    assert sum(rect.width() for rect in rects) == pytest.approx(usable, abs=0.5)


def test_montage_follows_row_order(populated, app):
    window = populated
    before = window.montage.clip_paths()
    assert len(before) == 3
    window.rows.reorder(0, 2)
    assert _wait(app, lambda: not window._busy, timeout=10)
    app.processEvents()
    after = window.montage.clip_paths()
    row_order = [row.active.media.path for row in window.rows.rows()]
    assert after == row_order, "el montaje sigue el orden de las filas"
    assert after != before, "el reordenado debe verse en el montaje"


def test_hover_highlights_the_matching_montage_segment(populated, app):
    """Pasar por una fila ilumina su clip en el montaje; salir lo apaga."""
    window = populated
    app.processEvents()
    row = window.rows.rows()[1]
    row._set_hovered(True)
    assert window.montage.highlighted == row.active.media.path

    row._set_hovered(False)
    assert window.montage.highlighted is None


def test_hover_on_another_row_moves_the_highlight(populated, app):
    window = populated
    app.processEvents()
    first, second = window.rows.rows()[0], window.rows.rows()[2]
    first._set_hovered(True)
    second._set_hovered(True)
    assert window.montage.highlighted == second.active.media.path
    second._set_hovered(False)
    assert window.montage.highlighted is None


def test_montage_empty_state(populated, app):
    window = populated
    window._clear_all()
    app.processEvents()
    assert window.montage.clip_paths() == []
    assert window.montage.highlighted is None


def test_montage_updates_when_the_active_variant_changes(app):
    """El montaje enseña lo que se concatenará, no la primera variante."""
    from video_concat.ui.main_window import MainWindow
    from video_concat.ui.widgets import ClipGroupRow

    window = MainWindow()
    group = _fake_group(3)
    row = ClipGroupRow(group, 0)
    window.rows.add_row(row)
    window._refresh_montage()
    assert window.montage.clip_paths() == [group.members[0].media.path]

    row.set_active_member(2)
    window._on_row_selected(0)
    assert window.montage.clip_paths() == [group.members[2].media.path]
    window.close()


def test_hovering_a_tile_highlights_that_tile_not_the_active_one(app):
    """Si la fila tiene variantes, el tramo resaltado es el de la tarjeta tocada."""
    from video_concat.ui.widgets import ClipGroupRow

    group = _fake_group(3)
    row = ClipGroupRow(group, 0)
    seen: list[object] = []
    row.hover_changed.connect(seen.append)

    row._set_hovered(True)
    assert seen[-1] == group.members[0].media.path, "sin tarjeta concreta manda la activa"

    row._on_tile_hovered(row.tiles[2])
    assert seen[-1] == group.members[2].media.path

    row._on_tile_unhovered()
    assert seen[-1] == row.active.media.path

    row._set_hovered(False)
    assert seen[-1] is None


def test_hover_does_not_track_tiles_outside_the_row(app):
    from video_concat.ui.widgets import ClipGroupRow

    row = ClipGroupRow(_fake_group(3), 0)
    seen: list[object] = []
    row.hover_changed.connect(seen.append)
    row._on_tile_hovered(row.tiles[1])
    assert seen == [], "sin estar sobre la fila no se emite nada"


def _click(widget, point):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent

    event = QMouseEvent(
        QMouseEvent.Type.MouseButtonPress,
        point,
        widget.mapToGlobal(point.toPoint()),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    widget.mousePressEvent(event)


def test_clicking_a_montage_segment_selects_its_row(populated, app):
    """Pulsar un tramo del montaje selecciona la fila que aporta ese clip."""
    window = populated
    app.processEvents()
    target = window.montage.clip_paths()[2]
    rect = window.montage.rect_for(target)
    assert rect is not None
    _click(window.montage, rect.center())
    app.processEvents()

    selected = [i for i, row in enumerate(window.rows.rows()) if row.is_selected()]
    assert selected == [2]
    assert window.rows.rows()[2].active.media.path == target
    assert window.montage.selected == target


def test_clicking_the_margin_changes_nothing(populated, app):
    from PySide6.QtCore import QPointF

    window = populated
    app.processEvents()
    before = window.montage.selected
    _click(window.montage, QPointF(1, 1))
    app.processEvents()
    assert window.montage.selected == before
    assert sum(1 for row in window.rows.rows() if row.is_selected()) == 1


def test_path_at_maps_points_to_segments(populated, app):
    from PySide6.QtCore import QPointF

    window = populated
    app.processEvents()
    first = window.montage.clip_paths()[0]
    rect = window.montage.rect_for(first)
    assert window.montage.path_at(rect.center()) == first
    assert window.montage.path_at(QPointF(0, 0)) is None


def test_montage_selection_follows_row_selection(populated, app):
    """Seleccionar en la lista también marca el tramo correspondiente."""
    window = populated
    window._on_row_selected(1)
    app.processEvents()
    assert window.montage.selected == window.rows.rows()[1].active.media.path


def test_selected_segment_is_cleared_when_rows_disappear(populated, app):
    window = populated
    window._on_row_selected(2)
    app.processEvents()
    dropped = window.rows.rows()[2].active.media.path
    window._on_row_selected(2)
    window._remove_selection()
    app.processEvents()
    assert dropped not in window.montage.clip_paths()
    assert window.montage.selected != dropped


def test_detach_button_emits_the_clip_it_sits_on(app):
    """El botón ↓ de una tarjeta pide separar ese clip concreto, no la fila."""
    from video_concat.ui.widgets import ClipGroupRow

    group = _fake_group(3)
    row = ClipGroupRow(group, 0)
    emitted: list[tuple[object, bool]] = []
    row.detach_requested.connect(lambda path, sep: emitted.append((path, sep)))

    target = group.members[1].media.path
    assert row.tiles[1].btn_detach.isEnabled()
    row.tiles[1].btn_detach.click()
    assert emitted == [(target, True)]
    assert row.tiles[1].btn_detach.isChecked()

    row.tiles[1].btn_detach.click()
    assert emitted[-1] == (target, False)
    assert not row.tiles[1].btn_detach.isChecked()


def test_detach_button_is_disabled_when_the_tile_is_alone(app):
    from video_concat.ui.widgets import ClipGroupRow

    row = ClipGroupRow(_fake_group(1), 0)
    assert not row.tiles[0].btn_detach.isEnabled(), (
        "un clip ya solo no tiene de dónde salir"
    )


def test_detach_button_stays_clickable_on_a_detached_clip(app):
    """Un clip ya separado sigue solo en su fila: el botón debe permitir deshacer."""
    from video_concat.ui.widgets import ClipGroupRow

    row = ClipGroupRow(_fake_group(1), 0)
    tile = row.tiles[0]
    assert not tile.btn_detach.isEnabled()

    row.tiles[0].set_detach_checked(True)
    assert tile.btn_detach.isEnabled(), "debe poder deshacerse la separación"


def test_reattach_returns_the_clip_to_its_variants(variants):
    """Volver a pulsar ↓ devuelve el clip a la fila de sus variantes."""
    window = variants
    pair = next(row for row in window.rows.rows() if len(row.tiles) == 2)
    target = pair.tiles[1].clip.media.path

    window._detach_clip(target)
    assert len(window.rows.rows()) == 3

    window._reattach_clip(target)
    assert target not in window._detached
    assert len(window.rows.rows()) == 2, "vuelve a la fila de sus variantes"
    rejoined = next(r for r in window.rows.rows() if len(r.tiles) == 2)
    assert any(t.clip.media.path == target for t in rejoined.tiles)


def test_detach_places_the_clip_under_its_variants(variants):
    """El clip separado queda en la fila inmediatamente inferior a sus variantes."""
    window = variants
    pair = next(row for row in window.rows.rows() if len(row.tiles) == 2)
    target = pair.tiles[1].clip.media.path
    companion = pair.tiles[0].clip.media.path

    window._detach_clip(target)

    assert target in window._detached
    assert window._order.index(target) == window._order.index(companion) + 1, (
        "el clip va justo detrás de su variante"
    )
    rows = window.rows.rows()
    moved = next(r for r in rows if any(t.clip.media.path == target for t in r.tiles))
    assert len(moved.tiles) == 1, "el clip separado ocupa su propia fila"


def test_detached_clip_does_not_rejoin_its_group_on_rebuild(variants):
    """Un recálculo (umbral, ingesta) no puede reabsorber el clip separado."""
    window = variants
    pair = next(row for row in window.rows.rows() if len(row.tiles) == 2)
    target = pair.tiles[1].clip.media.path
    window._detach_clip(target)

    window._rebuild_groups()

    rows_with_target = [
        r for r in window.rows.rows() if any(t.clip.media.path == target for t in r.tiles)
    ]
    assert len(rows_with_target) == 1
    assert len(rows_with_target[0].tiles) == 1, "el clip separado sigue en su fila propia"
    assert len(window.rows.rows()) == 3, "las tres filas siguen existiendo"


def test_detach_ignores_unknown_clip(populated):
    """Un path que no está en ninguna fila no rompe nada."""
    window = populated
    before = len(window.rows.rows())
    window._detach_clip(Path("fantasma.mp4"))
    assert len(window.rows.rows()) == before
    assert window._detached == set()


def test_detach_on_a_single_tile_row_is_a_no_op(populated):
    """Un clip que ya está solo no se puede separar más."""
    window = populated
    row = window.rows.rows()[0]
    assert len(row.tiles) == 1
    window._detach_clip(row.tiles[0].clip.media.path)
    assert window._detached == set()
    assert "ya está solo" in window.status_label.text()


def test_removing_a_detached_clip_clears_its_mark(variants):
    """Quitar el clip también borra su marca: no debe resucitar en un recálculo."""
    window = variants
    pair = next(row for row in window.rows.rows() if len(row.tiles) == 2)
    target = pair.tiles[1].clip.media.path
    window._detach_clip(target)
    assert target in window._detached

    window._on_row_selected(
        next(
            i
            for i, r in enumerate(window.rows.rows())
            if any(t.clip.media.path == target for t in r.tiles)
        )
    )
    window._remove_selection()

    assert target not in window._detached
    assert target not in window._order


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
