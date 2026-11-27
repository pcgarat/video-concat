"""Pruebas del layout: panel lateral, zoom de tarjetas y timeline acoplado al reproductor."""

from __future__ import annotations

import os
import sys
import time

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
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


def _pump(app, turns: int = 20) -> None:
    for _ in range(turns):
        app.processEvents()
        time.sleep(0.01)


@pytest.fixture
def populated(window, app, clips_factory):
    paths = [
        clips_factory("a.mp4", first_color="red", last_color="lime"),
        clips_factory("b.mp4", first_color="lime", last_color="blue"),
        clips_factory("c.mp4", first_color="blue", last_color="yellow"),
    ]
    window._ingest_paths(paths)
    assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 3)
    return window


@pytest.fixture
def variants_window(window, app, clips_factory):
    """Cuatro filas: tres planos sueltos y un plano con dos variantes."""
    paths = [
        clips_factory("a.mp4", first_color="red", last_color="lime"),
        clips_factory("b.mp4", first_color="lime", last_color="blue"),
        clips_factory("c.mp4", first_color="blue", last_color="yellow"),
        clips_factory("v1.mp4", first_color="magenta", last_color="cyan"),
        clips_factory("v2.mp4", first_color="magenta", last_color="cyan"),
    ]
    window._ingest_paths(paths)
    assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 4)
    window.show()
    _pump(app)
    return window


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


def test_player_mirrors_the_visible_rows(populated):
    expected = [row.active.media.path for row in populated.rows.rows()]
    assert [media.path for media in populated.player.clips] == expected
    assert populated.player.total == pytest.approx(
        sum(m.duration for m in populated._selected_media())
    )


def test_side_panel_toggles_with_the_header_button(populated):
    window = populated
    assert not window.side_panel.isHidden(), "arranca visible"

    window.btn_panel.setChecked(False)
    assert window.side_panel.isHidden()

    window.btn_panel.setChecked(True)
    assert not window.side_panel.isHidden()


def test_zoom_slider_resizes_the_cards(populated, app):
    window = populated
    window.zoom_slider.setValue(80)
    _pump(app)
    small = window.rows.rows()[0].tiles[0].width()

    window.zoom_slider.setValue(130)
    _pump(app)
    big = window.rows.rows()[0].tiles[0].width()

    assert big > small, "el deslizador debe escalar la tarjeta de cada fila"


def test_selecting_a_row_parks_the_cursor_at_its_clip(populated):
    window = populated
    window._on_row_selected(2)
    durations = [m.duration for m in window._selected_media()]
    assert window.player.current_index == 2
    assert window.montage.position == pytest.approx(sum(durations[:2]), abs=0.05)


def test_clicking_the_timeline_moves_the_cursor(populated, app):
    window = populated
    app.processEvents()
    paths = window.montage.clip_paths()
    target = paths[1]
    rect = window.montage.rect_for(target)
    assert rect is not None

    _click(window.montage, rect.center())

    durations = [row.active.media.duration for row in window.rows.rows()]
    expected = durations[0] + durations[1] / 2
    assert window.montage.position == pytest.approx(expected, abs=0.05)
    assert window.player.current_index == 1
    assert window.rows.rows()[1].is_selected()


def test_playhead_stays_inside_the_current_segment(populated, app):
    window = populated
    app.processEvents()
    durations = [row.active.media.duration for row in window.rows.rows()]
    rect = window.montage.rect_for(window.montage.clip_paths()[1])
    assert rect is not None

    window.montage.set_position(durations[0] + durations[1] / 2)

    x = window.montage.playhead_x
    assert x is not None
    assert rect.left() <= x <= rect.right() + 0.01


def test_removing_a_card_drops_its_clip(populated):
    window = populated
    row = window.rows.rows()[0]
    victim = row.active.media

    row.remove_requested.emit(victim)

    assert victim.path not in window._media
    assert len(window.rows.rows()) == 2
    assert victim.path not in [m.path for m in window.player.clips]


def test_run_button_becomes_the_stop_control_while_concatenating(populated):
    window = populated
    window._concatenating = True
    window._update_actions()
    assert window.btn_run.text() == "Detener"
    assert window.btn_run.isEnabled(), "hay que poder detener una concatenación en curso"

    window._concatenating = False
    window._update_actions()
    assert window.btn_run.text() == "Concatenar"


# --- timeline: fotograma único, arrastre y variantes ---
def _color_near(image, rect, target, tolerance: int = 26) -> bool:
    """
    ¿Aparece `target` en el borde de `rect`?

    Se recorre una banda de 2 px pegada al borde interior, recortada 4 px en las
    esquinas: los tramos son contiguos y el borde de uno se antialiasa sobre el
    vecino, así que mirar justo en la frontera daría falsos positivos.
    """
    from PySide6.QtGui import QColor

    wanted = QColor(target)
    left, top = int(rect.left()) + 4, int(rect.top()) + 4
    right, bottom = int(rect.right()) - 4, int(rect.bottom()) - 4
    points = []
    for x in range(left, right, 2):
        points.append((x, int(rect.top()) + 1))
        points.append((x, int(rect.bottom()) - 1))
    for y in range(top, bottom, 2):
        points.append((int(rect.left()) + 1, y))
        points.append((int(rect.right()) - 1, y))
    for x, y in points:
        color = image.pixelColor(x, y)
        if max(
            abs(color.red() - wanted.red()),
            abs(color.green() - wanted.green()),
            abs(color.blue() - wanted.blue()),
        ) <= tolerance:
            return True
    return False


def test_timeline_uses_only_the_first_frame_of_each_clip(populated, app):
    """Cada tramo pinta el primer fotograma, sin el último al lado."""
    window = populated
    app.processEvents()
    image = window.montage.grab().toImage()
    # a.mp4 abre en rojo y cierra en verde lima: el tramo entero debe ser rojo.
    rect = window.montage.rect_for(window.montage.clip_paths()[0])
    assert rect is not None
    for fraction in (0.15, 0.5, 0.85):
        x = int(rect.left() + rect.width() * fraction)
        color = image.pixelColor(x, int(rect.center().y()))
        assert color.red() > 180 and color.green() < 90, (
            f"el tramo debe ser del primer fotograma (rojo), no {color.name()}"
        )


def test_slot_with_variants_gets_an_orange_border(variants_window, app):
    from video_concat.ui.styles import COLORS

    window = variants_window
    app.processEvents()
    image = window.montage.grab().toImage()
    slots = window.montage.slots()
    variant_slot = next(i for i, slot in enumerate(slots) if slot.has_variants)
    plain_slot = next(i for i, slot in enumerate(slots) if not slot.has_variants)

    variant_rect = window.montage.rect_for(slots[variant_slot].media.path)
    plain_rect = window.montage.rect_for(slots[plain_slot].media.path)
    assert variant_rect is not None and plain_rect is not None
    assert _color_near(image, variant_rect, COLORS["accent"]), "borde naranja esperado"
    assert not _color_near(image, plain_rect, COLORS["accent"]), (
        "un clip sin variantes no lleva borde naranja"
    )


def test_dragging_a_timeline_segment_reorders_its_row(variants_window, app):
    window = variants_window
    before = [row.group.reference.media.path.name for row in window.rows.rows()]
    slots_before = [slot.media.path for slot in window.montage.slots()]

    window.montage.segment_moved.emit(0, 3)
    assert _wait(app, lambda: not window._busy, timeout=10)

    after = [row.group.reference.media.path.name for row in window.rows.rows()]
    assert after == [before[1], before[2], before[0], before[3]]
    slots_after = [slot.media.path for slot in window.montage.slots()]
    assert slots_after[2] == slots_before[0], "el tramo arrastrado se queda en el hueco"


def test_dropping_a_segment_where_it_was_changes_nothing(variants_window):
    window = variants_window
    before = [row.group.reference.media.path.name for row in window.rows.rows()]

    window.montage.segment_moved.emit(1, 1)
    window.montage.segment_moved.emit(1, 2)

    after = [row.group.reference.media.path.name for row in window.rows.rows()]
    assert after == before


def test_insertion_gap_follows_the_segments(variants_window, app):
    window = variants_window
    app.processEvents()
    slots = window.montage.slots()
    assert len(slots) == 4
    first = window.montage.rect_for(slots[0].media.path)
    last = window.montage.rect_for(slots[-1].media.path)
    assert first is not None and last is not None
    assert window.montage.insertion_gap(first.left() + 1) == 0
    assert window.montage.insertion_gap(last.right() - 1) == len(slots)
    assert window.montage.insertion_gap(first.right() + 2) == 1


def test_hovering_a_variant_slot_shows_the_tray(variants_window, app):
    window = variants_window
    app.processEvents()
    index = next(i for i, slot in enumerate(window.montage.slots()) if slot.has_variants)

    window.montage.show_variants(index)

    assert window.montage.popup.isVisible()
    chips = window.montage.popup.chips()
    assert len(chips) == 2
    assert [chip.is_current for chip in chips] == [True, False]


def test_hovering_a_plain_slot_shows_nothing(variants_window, app):
    window = variants_window
    app.processEvents()
    index = next(i for i, slot in enumerate(window.montage.slots()) if not slot.has_variants)

    window.montage.show_variants(index)

    assert not window.montage.popup.isVisible()


def test_choosing_a_variant_swaps_the_timeline_clip(variants_window, app):
    window = variants_window
    app.processEvents()
    index = next(i for i, slot in enumerate(window.montage.slots()) if slot.has_variants)
    row = [r for r in window.rows.rows() if not r.isHidden()][index]

    window.montage.show_variants(index)
    window.montage.popup.chips()[1].click()
    _pump(app)

    assert row.active_index == 1
    assert window.montage.slots()[index].active == 1
    assert window.montage.slots()[index].media.path == row.tiles[1].clip.media.path
    assert not window.montage.popup.isVisible()
    assert row.tiles[1].clip.media.path in [m.path for m in window.player.clips]


def test_choosing_the_current_variant_is_a_no_op(variants_window, app):
    window = variants_window
    app.processEvents()
    index = next(i for i, slot in enumerate(window.montage.slots()) if slot.has_variants)
    row = [r for r in window.rows.rows() if not r.isHidden()][index]
    before = row.active_index

    seen: list[tuple[int, int]] = []
    window.montage.variant_chosen.connect(lambda i, p: seen.append((i, p)))
    window.montage.show_variants(index)
    window.montage.popup.chips()[before].click()

    assert seen == []
    assert row.active_index == before


def test_moving_the_mouse_over_a_segment_starts_the_drag(variants_window, app, monkeypatch):
    """Pulsar y mover más allá del umbral arrastra el tramo (sin abrir un QDrag real)."""
    from PySide6.QtCore import QEvent, QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    window = variants_window
    app.processEvents()
    montage = window.montage
    started: list[int] = []
    monkeypatch.setattr(montage, "_start_drag", started.append)

    slot = montage.slots()[1]
    rect = montage.rect_for(slot.media.path)
    assert rect is not None
    start = rect.center()
    _click(montage, start)

    def move(point: QPointF) -> None:
        montage.mouseMoveEvent(
            QMouseEvent(
                QEvent.Type.MouseMove,
                point,
                montage.mapToGlobal(point.toPoint()),
                Qt.MouseButton.NoButton,
                Qt.MouseButton.LeftButton,
                Qt.KeyboardModifier.NoModifier,
            )
        )

    move(start + QPointF(2, 0))
    assert started == [], "un temblor de 2 px no debe arrastrar"

    move(start + QPointF(20, 0))
    assert started == [1], "al pasar el umbral se arrastra el tramo pulsado"

def test_reordering_from_the_timeline_keeps_the_moved_row_selected(variants_window, app):
    """Arrastrar un tramo no debe robar la selección a la fila que se mueve."""
    window = variants_window
    window._on_row_selected(3)
    moved = window.rows.rows()[3].group.reference.media.path

    window.montage.segment_moved.emit(3, 0)
    assert _wait(app, lambda: not window._busy, timeout=10)

    selected = [row for row in window.rows.rows() if row.is_selected()]
    assert len(selected) == 1
    assert selected[0].group.reference.media.path == moved


# --- sincronía de selección panel ↔ timeline ---
def _press(widget, point):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QMouseEvent

    widget.mousePressEvent(
        QMouseEvent(
            QMouseEvent.Type.MouseButtonPress,
            point,
            widget.mapToGlobal(point.toPoint()),
            Qt.MouseButton.LeftButton,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
        )
    )


def test_only_one_row_can_be_selected(variants_window, app):
    window = variants_window
    window._on_row_selected(1)
    window._on_row_selected(3)
    _pump(app)

    assert [row.index for row in window.rows.rows() if row.is_selected()] == [3]


def test_timeline_click_selects_the_same_clip_in_the_panel(variants_window, app):
    window = variants_window
    app.processEvents()
    slot = window.montage.slots()[2]
    rect = window.montage.rect_for(slot.media.path)
    assert rect is not None

    _press(window.montage, rect.center())
    _pump(app)

    selected = [row for row in window.rows.rows() if row.is_selected()]
    assert [row.index for row in selected] == [2]
    assert selected[0].active.media.path == slot.media.path
    assert window.montage.selected == slot.media.path


def test_panel_selection_marks_the_timeline(variants_window, app):
    window = variants_window
    window._on_row_selected(2)
    _pump(app)

    assert window.montage.selected == window.rows.rows()[2].active.media.path
    durations = [row.active.media.duration for row in window.rows.rows()]
    assert window.montage.position == pytest.approx(sum(durations[:2]), abs=0.05)


def test_timeline_click_reveals_the_row_in_a_small_panel(app, clips_factory, monkeypatch):
    """Con muchas filas, elegir un tramo del timeline desplaza el panel hasta él."""
    monkeypatch.setattr(
        "video_concat.ui.main_window.detect_gpu",
        lambda: __import__(
            "video_concat.core.gpu", fromlist=["GpuCapabilities"]
        ).GpuCapabilities(False, False, False, None, ("test",)),
    )
    from video_concat.ui.main_window import MainWindow

    window = MainWindow()
    window.resize(1024, 700)
    colors = ["red", "lime", "blue", "yellow", "magenta", "cyan", "pink", "orange"]
    paths = [clips_factory(f"c{i}.mp4", first_color=c, last_color=c) for i, c in enumerate(colors)]
    try:
        window._ingest_paths(paths)
        assert _wait(app, lambda: not window._busy and len(window.rows.rows()) == 8)
        window.show()
        _pump(app)
        scrollbar = window.scroll.verticalScrollBar()
        assert scrollbar.maximum() > 0, "la ventana de prueba debe necesitar scroll"

        last = window.montage.slots()[-1]
        rect = window.montage.rect_for(last.media.path)
        assert rect is not None
        _press(window.montage, rect.center())
        _pump(app)

        assert [row.index for row in window.rows.rows() if row.is_selected()] == [7]
        assert scrollbar.value() > 0, "el panel debe desplazarse hasta la fila elegida"
    finally:
        window.close()


def test_playback_advances_the_panel_selection(variants_window, app):
    window = variants_window
    window._on_row_selected(0)
    _pump(app)
    window._on_player_clip_changed(2)
    _pump(app)

    assert [row.index for row in window.rows.rows() if row.is_selected()] == [2]
    assert window.montage.selected == window.rows.rows()[2].active.media.path


def test_choosing_a_variant_in_the_panel_swaps_the_timeline_segment(variants_window, app):
    window = variants_window
    row = window.rows.rows()[3]
    assert row.active_index == 0
    wanted = row.tiles[1].clip.media.path

    row.tiles[1].activated.emit(row.tiles[1])
    _pump(app)

    slot = window.montage.slots()[3]
    assert slot.active == 1
    assert slot.media.path == wanted
    assert window.montage.selected == wanted
    assert wanted in [media.path for media in window.player.clips]


def test_reselecting_the_current_clip_does_not_restart_it(variants_window, app):
    window = variants_window
    window._on_row_selected(1)
    _pump(app)
    before = window.player.position

    window._on_row_selected(1)
    _pump(app)

    assert window.player.position == pytest.approx(before)
