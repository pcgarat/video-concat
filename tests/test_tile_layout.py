"""Reparto de ancho de las tarjetas de una fila (geometría pura, sin Qt)."""

import pytest

from video_concat.ui.layout_metrics import THUMB_MAX, THUMB_MIN, TILE_GAP, tile_metrics


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
