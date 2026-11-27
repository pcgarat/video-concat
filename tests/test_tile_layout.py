"""Geometría de las tarjetas del panel y del timeline (lógica pura, sin Qt)."""

import pytest

from video_concat.ui.layout_metrics import (
    CARD_ZOOM_MAX,
    CARD_ZOOM_MIN,
    MONTAGE_MIN_SEGMENT,
    card_metrics,
    montage_widths,
)


class TestCardMetrics:
    """La tarjeta horizontal del panel crece con el zoom y cabe en el panel mínimo."""

    def test_zoom_scales_the_card_and_its_thumbnails(self):
        small_width, small_side = card_metrics(CARD_ZOOM_MIN)
        big_width, big_side = card_metrics(CARD_ZOOM_MAX)
        assert big_width > small_width
        assert big_side > small_side

    def test_zoom_is_clamped_to_its_range(self):
        assert card_metrics(0.1) == card_metrics(CARD_ZOOM_MIN)
        assert card_metrics(20.0) == card_metrics(CARD_ZOOM_MAX)

    def test_widest_card_still_fits_the_minimum_panel(self):
        # 380 px de panel - márgenes - asa - bordes de fila.
        widest, _ = card_metrics(CARD_ZOOM_MAX)
        assert widest <= 310, "con el panel al mínimo la tarjeta no puede recortarse"

    def test_text_column_keeps_a_readable_width_at_every_zoom(self):
        from video_concat.ui.layout_metrics import (
            CARD_COLUMN_GAP,
            CARD_PADDING,
            CARD_TEXT_WIDTH,
            CARD_THUMB_GAP,
        )

        for zoom in (CARD_ZOOM_MIN, 1.0, CARD_ZOOM_MAX):
            width, side = card_metrics(zoom)
            text = (
                width
                - 2 * side
                - CARD_THUMB_GAP
                - CARD_COLUMN_GAP
                - 2 * CARD_PADDING
            )
            assert text == CARD_TEXT_WIDTH


class TestMontageWidths:
    """El montaje reparte el ancho por duración, sin clips que desaparezcan."""

    def test_width_is_proportional_to_duration(self):
        widths = montage_widths([1.0, 2.0, 1.0], 1000, min_width=1)
        assert widths == pytest.approx([250, 500, 250])

    def test_total_never_exceeds_available(self):
        widths = montage_widths([0.4, 9.0, 3.0, 0.2], 900)
        assert sum(widths) == pytest.approx(900)

    def test_short_clip_keeps_a_visible_floor(self):
        widths = montage_widths([0.2, 20.0, 20.0], 900)
        assert widths[0] >= MONTAGE_MIN_SEGMENT - 1e-6
        assert sum(widths) == pytest.approx(900)

    def test_every_clip_gets_a_positive_width(self):
        widths = montage_widths([0.01] * 40, 500)
        assert all(w > 0 for w in widths)
        assert sum(widths) == pytest.approx(500)

    def test_many_clips_still_fit_the_track(self):
        """Con más clips que huecos para el suelo, el suelo se encoge pero caben todos."""
        widths = montage_widths([10.0] * 60, 600)
        assert len(widths) == 60
        assert sum(widths) == pytest.approx(600)
        assert min(widths) == pytest.approx(10)

    def test_degenerate_inputs(self):
        assert montage_widths([], 500) == []
        assert montage_widths([1.0, 2.0], 0) == []

    def test_zero_duration_is_treated_as_minimum(self):
        widths = montage_widths([0.0, 2.0], 600)
        assert all(w > 0 for w in widths)
        assert widths[1] > widths[0]
