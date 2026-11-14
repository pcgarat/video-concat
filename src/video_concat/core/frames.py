"""Extracción y comparación de los fotogramas extremos de un clip."""

from __future__ import annotations

import math
import shutil
import subprocess
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

FRAME_SIZE = 128
GRID = 32
DEFAULT_THRESHOLD = 0.93


class FrameExtractError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FrameSignature:
    """Huella de un fotograma: rejilla GRID×GRID de colores RGB normalizados."""

    rgb: tuple[float, ...]

    @property
    def size(self) -> int:
        return GRID

    @property
    def mean_rgb(self) -> tuple[float, float, float]:
        n = len(self.rgb) // 3
        return (
            sum(self.rgb[0::3]) / n,
            sum(self.rgb[1::3]) / n,
            sum(self.rgb[2::3]) / n,
        )


def signature_from_rgb(raw: bytes, size: int = FRAME_SIZE) -> FrameSignature:
    """Reduce un frame RGB de `size`×`size` a una rejilla GRID×GRID normalizada."""
    expected = size * size * 3
    if len(raw) != expected:
        raise FrameExtractError(f"Frame RGB inesperado: {len(raw)} bytes (esperaba {expected})")

    step = max(1, size // GRID)
    cells = step * step * 255.0
    rgb: list[float] = []
    for cell_y in range(GRID):
        y0 = min(cell_y * step, size - step)
        for cell_x in range(GRID):
            x0 = min(cell_x * step, size - step)
            r = g = b = 0
            for dy in range(step):
                row = (y0 + dy) * size * 3
                for dx in range(step):
                    i = row + (x0 + dx) * 3
                    r += raw[i]
                    g += raw[i + 1]
                    b += raw[i + 2]
            rgb.extend((r / cells, g / cells, b / cells))
    return FrameSignature(rgb=tuple(rgb))


def distance(a: FrameSignature, b: FrameSignature) -> float:
    """
    Distancia perceptual en [0, 1]; 0 = idénticos.

    Equivale al RMSE de la rejilla, pero `math.dist` lo resuelve en C: es ~18x
    más rápido que el bucle equivalente en Python y el agrupado es O(n²).
    """
    if len(a.rgb) != len(b.rgb):
        raise ValueError("Firmas de distinto tamaño")
    return min(1.0, math.dist(a.rgb, b.rgb) / math.sqrt(len(a.rgb)))


def similarity(a: FrameSignature, b: FrameSignature) -> float:
    """Similitud en [0, 1]. 1.0 = idénticos, 0.0 = opuestos."""
    return 1.0 - distance(a, b)


@dataclass(frozen=True, slots=True)
class EdgeFrames:
    """Fotogramas extremos de un clip: firma para comparar y RGB para pintarlos."""

    first: FrameSignature
    last: FrameSignature
    first_rgb: bytes
    last_rgb: bytes
    preview_size: int = FRAME_SIZE


def _video_filters(size: int) -> str:
    return (
        f"scale={size}:{size}:force_original_aspect_ratio=decrease:flags=area,"
        f"pad={size}:{size}:(ow-iw)/2:(oh-ih)/2"
    )


def _run_ffmpeg(args: list[str], timeout: float, path: Path) -> bytes:
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FrameExtractError("ffmpeg no encontrado en PATH")
    try:
        result = subprocess.run(
            [ffmpeg, "-v", "error", *args],
            capture_output=True,
            check=False,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise FrameExtractError(f"ffmpeg agotó el tiempo en {path.name}") from exc
    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", "replace").strip().splitlines()
        raise FrameExtractError(detail[-1] if detail else f"ffmpeg falló en {path.name}")
    return result.stdout


def _raw_args(path: Path, *, seek: list[str], frames: int | None) -> list[str]:
    args = [*seek, "-i", str(path), "-an", "-sn"]
    if frames is not None:
        args.extend(["-frames:v", str(frames)])
    args.extend(["-vf", _video_filters(FRAME_SIZE), "-f", "rawvideo", "-pix_fmt", "rgb24", "-"])
    return args


def _grab_first(path: Path, timeout: float) -> bytes:
    raw = _run_ffmpeg(_raw_args(path, seek=[], frames=1), timeout, path)
    return raw[: FRAME_SIZE * FRAME_SIZE * 3]


def _grab_last(path: Path, duration: float, timeout: float) -> bytes:
    """
    Decodifica la cola del clip y devuelve el último frame completo.

    Con `duration <= 0` el contenedor no declara duración: buscar desde
    `duration - 0.25` daría `-ss 0.000`, es decir el primer fotograma, y el
    clip quedaría etiquetado como «primero == último». En ese caso sólo se
    usa `-sseof`, y si no produce frames se falla en vez de mentir.
    """
    frame_bytes = FRAME_SIZE * FRAME_SIZE * 3
    seeks = [["-sseof", "-0.25"]]
    if duration > 0:
        seeks.append(["-ss", f"{max(0.0, duration - 0.25):.3f}"])

    last_error: FrameExtractError | None = None
    for seek in seeks:
        try:
            raw = _run_ffmpeg(_raw_args(path, seek=seek, frames=None), timeout, path)
        except FrameExtractError as exc:
            last_error = exc
            continue
        usable = len(raw) - (len(raw) % frame_bytes)
        if usable:
            return raw[usable - frame_bytes : usable][:frame_bytes]
        last_error = FrameExtractError(f"Sin fotogramas utilizables en {path.name}")
    assert last_error is not None
    raise last_error


def extract_edge_frames(path: Path, duration: float = 0.0, timeout: float = 60.0) -> EdgeFrames:
    """Extrae y firma el primer y el último fotograma de un clip."""
    path = path.resolve()
    if not path.is_file():
        raise FrameExtractError(f"No existe el fichero: {path}")

    first_raw = _grab_first(path, timeout)
    last_raw = _grab_last(path, duration, timeout)
    return EdgeFrames(
        first=signature_from_rgb(first_raw),
        last=signature_from_rgb(last_raw),
        first_rgb=first_raw,
        last_rgb=last_raw,
    )


def extract_many(
    entries: list[tuple[Path, float]],
    max_workers: int = 4,
    on_progress: Callable[[int, int], None] | None = None,
    cancel: threading.Event | None = None,
) -> dict[Path, EdgeFrames | Exception]:
    """
    Extrae en paralelo; devuelve el error por clip en lugar de abortar la tanda.

    `cancel` permite parar entre clips. Es la alternativa cooperativa a
    `QThread.terminate()`: matar el hilo dejaría los ffmpeg hijos huérfanos.
    """
    if not entries:
        return {}
    workers = max(1, min(max_workers, len(entries)))
    results: dict[Path, EdgeFrames | Exception] = {}
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(extract_edge_frames, p, d): p for p, d in entries}
        for fut in as_completed(futures):
            path = futures[fut]
            try:
                results[path] = fut.result()
            except Exception as exc:  # noqa: BLE001 — se devuelve al llamante
                results[path] = exc
            done += 1
            if on_progress is not None:
                on_progress(done, len(entries))
            if cancel is not None and cancel.is_set():
                break
    return results
