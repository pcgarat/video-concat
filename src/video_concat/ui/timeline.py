"""Timeline: mapa de tiempos (lógica pura) y ranuras del montaje."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from video_concat.core.frames import EdgeFrames
from video_concat.core.probe import MediaInfo

# Un clip de duración 0 rompería la suma acumulada y el reparto del cursor.
MIN_DURATION = 0.001


@dataclass(frozen=True, slots=True)
class TimePoint:
    """Punto del timeline: qué clip suena y con qué desplazamiento dentro de él."""

    index: int
    path: Path
    local: float


@dataclass(frozen=True, slots=True)
class TimelineMember:
    """Una de las variantes que puede ocupar una ranura del timeline."""

    media: MediaInfo
    frames: EdgeFrames | None = None


@dataclass(frozen=True, slots=True)
class TimelineSlot:
    """
    Un tramo del timeline: la fila del panel que lo aporta y su clip activo.

    El timeline enseña un clip por fila, pero una fila puede tener variantes:
    `members` conserva todas para poder sustituir el activo sin volver al panel.
    """

    row: int
    members: tuple[TimelineMember, ...]
    active: int = 0

    @property
    def active_member(self) -> TimelineMember:
        if not self.members:
            raise ValueError("ranura del timeline sin members")
        return self.members[self.active if 0 <= self.active < len(self.members) else 0]

    @property
    def media(self) -> MediaInfo:
        return self.active_member.media

    @property
    def frames(self) -> EdgeFrames | None:
        return self.active_member.frames

    @property
    def has_variants(self) -> bool:
        return len(self.members) > 1

    @property
    def paths(self) -> list[Path]:
        return [member.media.path for member in self.members]

    def index_of(self, path: Path) -> int | None:
        for position, member in enumerate(self.members):
            if member.media.path == path:
                return position
        return None


class TimelineMap:
    """
    Mapa de tiempos del montaje completo.

    El reproductor encadena los clips de uno en uno, pero la interfaz habla
    siempre en tiempo global (posición del cursor sobre el timeline). Tener la
    conversión en un objeto puro permite probarla sin tocar multimedia.
    """

    def __init__(self, clips: list[tuple[Path, float]]) -> None:
        self._paths = [path for path, _duration in clips]
        self._durations = [max(float(d), MIN_DURATION) for _path, d in clips]
        self._starts: list[float] = []
        accumulated = 0.0
        for duration in self._durations:
            self._starts.append(accumulated)
            accumulated += duration
        self._total = accumulated

    def __len__(self) -> int:
        return len(self._paths)

    def __bool__(self) -> bool:
        return bool(self._paths)

    @property
    def paths(self) -> list[Path]:
        return list(self._paths)

    @property
    def durations(self) -> list[float]:
        return list(self._durations)

    @property
    def total(self) -> float:
        return self._total

    def start(self, index: int) -> float:
        if not self._starts:
            return 0.0
        index = max(0, min(index, len(self._starts) - 1))
        return self._starts[index]

    def end(self, index: int) -> float:
        if not self._paths:
            return 0.0
        index = max(0, min(index, len(self._paths) - 1))
        return self._starts[index] + self._durations[index]

    def index_of(self, path: Path) -> int | None:
        try:
            return self._paths.index(path)
        except ValueError:
            return None

    def index_at(self, time: float) -> int:
        """Clip que ocupa `time`; los tiempos fuera de rango se recortan."""
        if not self._paths:
            return 0
        time = max(0.0, float(time))
        for index in range(len(self._paths) - 1):
            if time < self._starts[index] + self._durations[index]:
                return index
        return len(self._paths) - 1

    def local_time(self, index: int, time: float) -> float:
        if not self._paths:
            return 0.0
        index = max(0, min(index, len(self._paths) - 1))
        offset = max(0.0, float(time) - self._starts[index])
        # Un tiempo más allá del final del último clip se queda en su último frame.
        return min(offset, self._durations[index])

    def time_of(self, index: int, local: float = 0.0) -> float:
        if not self._paths:
            return 0.0
        index = max(0, min(index, len(self._paths) - 1))
        local = max(0.0, min(float(local), self._durations[index]))
        return self._starts[index] + local

    def location(self, time: float) -> TimePoint | None:
        if not self._paths:
            return None
        index = self.index_at(time)
        return TimePoint(index, self._paths[index], self.local_time(index, time))

    def progress(self, index: int, local: float) -> float:
        """Fracción 0..1 de `local` dentro del clip `index`."""
        if not self._paths:
            return 0.0
        index = max(0, min(index, len(self._paths) - 1))
        return max(0.0, min(1.0, float(local) / self._durations[index]))
