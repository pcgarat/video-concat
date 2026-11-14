"""Geometría pura de la tira de clips de una fila (sin dependencias de Qt)."""

from __future__ import annotations

TILE_PADDING = 6
TILE_PLAY_SIZE = 20
TILE_HEAD_SPACING = 6
TILE_GAP = 8
THUMB_MIN = 52
THUMB_MAX = 84
# Lo que ocupa la cabecera de la tarjeta además de las dos miniaturas.
THUMB_ROW_EXTRA = TILE_PLAY_SIZE + TILE_HEAD_SPACING + 2 * TILE_PADDING
# Tope de ancho: evita tarjetas gigantes en grupos de uno o dos clips.
TILE_CAP = THUMB_MAX * 2 + THUMB_ROW_EXTRA


def tile_metrics(count: int, available: int) -> tuple[int, int]:
    """
    Reparte `available` px entre `count` tarjetas en una sola línea.

    Devuelve `(ancho_tarjeta, lado_miniatura)`. La miniatura se encoge hasta
    `THUMB_MIN` para que el grupo quepa; si ni así cabe, la tarjeta se queda en
    su ancho mínimo y la fila permite desplazarse (nunca se comprime el texto).
    """
    if count <= 0:
        return TILE_CAP, THUMB_MAX
    slot = (available - TILE_GAP * (count - 1)) / count
    side = int((slot - THUMB_ROW_EXTRA) // 2)
    side = max(THUMB_MIN, min(side, THUMB_MAX))
    return side * 2 + THUMB_ROW_EXTRA, side
