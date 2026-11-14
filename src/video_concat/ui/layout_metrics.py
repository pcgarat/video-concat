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


# Montaje: cada clip ocupa un tramo proporcional a su duración.
MONTAGE_EDGE = 8
MONTAGE_CAPTION_H = 14
MONTAGE_HEIGHT = 62
MONTAGE_FRAME_MAX = 32
MONTAGE_MIN_SEGMENT = 14


def montage_widths(
    durations: list[float], available: float, min_width: float = MONTAGE_MIN_SEGMENT
) -> list[float]:
    """
    Anchos de los tramos del montaje: proporcionales a la duración, con suelo.

    Reparte `available` entre los clips según su duración, pero ningún tramo baja
    de `min_width`: los que se quedarían por debajo se fijan al suelo y su hueco
    se reparte entre el resto (water-filling). Si ni con el suelo caben todos, el
    suelo se reduce a `available / n` para que el montaje entero siga a la vista.
    """
    n = len(durations)
    if n <= 0 or available <= 0:
        return []
    floor = min(min_width, available / n)
    weights = {i: max(d, 0.01) for i, d in enumerate(durations)}
    widths = [0.0] * n
    free = list(range(n))
    remaining = float(available)
    while free:
        total = sum(weights[i] for i in free) or 1.0
        below = [i for i in free if remaining * weights[i] / total < floor]
        if not below:
            for i in free:
                widths[i] = remaining * weights[i] / total
            break
        for i in below:
            widths[i] = floor
            remaining -= floor
            free.remove(i)
    return widths
