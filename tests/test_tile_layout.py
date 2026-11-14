"""Reparto de ancho de las tarjetas de una fila y del montaje (geometría pura, sin Qt)."""

import pytest

from video_concat.ui.layout_metrics import (
    MONTAGE_MIN_SEGMENT,
    THUMB_MAX,
    THUMB_MIN,
    TILE_GAP,
    montage_widths,
    tile_metrics,
)


def _fits(count: int, available: int) -> bool:
    width, _ = tile_metrics(count, available)
    return width * count + TILE_GAP * (count - 1) <= available


def test_single_clip_is_capped():
    """Una fila de un solo clip no estira su tarjeta sin límite."""
    width, thumb = tile_metrics(1, 2000)
    assert thumb == THUMB_MAX
    assert width < 2000


def test_small_groups_fit_on_one_line():
    for count in (1, 2, 3, 4, 5):
        assert _fits(count, 1000), f"{count} clips deberían caber en 1000 px"
    for count in (1, 2, 3):
        assert _fits(count, 769), f"{count} clips deberían caber en 769 px"


def test_thumbnail_shrinks_as_the_group_grows():
    assert tile_metrics(2, 769)[1] > tile_metrics(5, 769)[1]
    assert tile_metrics(5, 769)[1] >= tile_metrics(8, 769)[1]


def test_thumbnail_never_below_minimum():
    _, thumb = tile_metrics(40, 300)
    assert thumb == THUMB_MIN


def test_oversized_group_keeps_a_readable_width():
    """Si el grupo no cabe, cada tarjeta mantiene su ancho mínimo (y habrá scroll)."""
    width, thumb = tile_metrics(12, 760)
    assert thumb == THUMB_MIN
    assert width >= THUMB_MIN * 2
    assert not _fits(12, 760)


@pytest.mark.parametrize("count", [0, -1])
def test_degenerate_counts(count: int):
    width, thumb = tile_metrics(count, 760)
    assert width > 0
    assert thumb > 0


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
