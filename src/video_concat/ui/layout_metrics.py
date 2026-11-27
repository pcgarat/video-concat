"""Geometría pura de las tarjetas y del timeline (sin dependencias de Qt)."""

from __future__ import annotations

# Tarjeta horizontal del panel de clips: dos miniaturas a la izquierda y la
# ficha a la derecha. El zoom del panel escala ambas cosas a la vez.
CARD_ZOOM_MIN = 0.75
CARD_ZOOM_MAX = 1.4
CARD_PADDING = 8
CARD_THUMB_GAP = 4
CARD_COLUMN_GAP = 8
CARD_TEXT_WIDTH = 150
CARD_BASE_THUMB = 44


def clamp_zoom(zoom: float) -> float:
    return max(CARD_ZOOM_MIN, min(float(zoom), CARD_ZOOM_MAX))


def card_metrics(zoom: float) -> tuple[int, int]:
    """
    `(ancho, lado_miniatura)` de una tarjeta horizontal para un zoom dado.

    El ancho crece con las miniaturas, no con el texto: la columna de ficha se
    queda en un ancho legible y el nombre se recorta con «…» si no cabe.
    """
    zoom = clamp_zoom(zoom)
    side = int(round(CARD_BASE_THUMB * zoom))
    width = (
        side * 2
        + CARD_THUMB_GAP
        + CARD_COLUMN_GAP
        + CARD_TEXT_WIDTH
        + 2 * CARD_PADDING
    )
    return width, side


def card_font_sizes(zoom: float) -> tuple[float, float, float]:
    """Tamaños (nombre, detalle, estado) en px para el zoom del panel."""
    zoom = clamp_zoom(zoom)
    return (11.0 + 2.0 * zoom, 9.0 + 1.2 * zoom, 9.0 + 1.0 * zoom)


# Montaje: cada clip ocupa un tramo proporcional a su duración.
MONTAGE_EDGE = 8
MONTAGE_CAPTION_H = 16
MONTAGE_HEIGHT = 104
MONTAGE_FRAME_MAX = 40
MONTAGE_MIN_SEGMENT = 14
MONTAGE_PLAYHEAD_W = 2


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
