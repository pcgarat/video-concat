"""Agrupado de clips por similitud de fotogramas extremos y orden sugerido."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from video_concat.core.frames import DEFAULT_THRESHOLD, EdgeFrames, FrameSignature, similarity
from video_concat.core.probe import MediaInfo


@dataclass(frozen=True, slots=True)
class GroupedClip:
    """Un clip dentro de una fila, con la razón por la que no se pudo comparar."""

    media: MediaInfo
    frames: EdgeFrames | None = None
    problem: str = ""

    @property
    def comparable(self) -> bool:
        return self.frames is not None


@dataclass(frozen=True, slots=True)
class ClipGroup:
    """Fila de la UI: clips que abren y cierran con el mismo fotograma."""

    members: tuple[GroupedClip, ...]
    score: float = 1.0
    origin: str = "único"
    order: int = field(default=0, compare=False)

    @property
    def reference(self) -> GroupedClip:
        return self.members[0]

    @property
    def first_signature(self) -> FrameSignature | None:
        return self.reference.frames.first if self.reference.frames else None

    @property
    def last_signature(self) -> FrameSignature | None:
        return self.reference.frames.last if self.reference.frames else None

    def resolve(self, index: int) -> GroupedClip:
        """Miembro activo de la fila; cae al primero si el índice no es válido."""
        if 0 <= index < len(self.members):
            return self.members[index]
        return self.reference


def _merge(index_of: dict[int, int], a: int, b: int) -> None:
    ra, rb = index_of[a], index_of[b]
    if ra == rb:
        return
    for key, root in index_of.items():
        if root == rb:
            index_of[key] = ra


def _cluster(clips: list[GroupedClip], threshold: float) -> list[list[int]]:
    """Union-find sobre clips que coinciden a la vez en primer y último fotograma."""
    index_of = {i: i for i in range(len(clips))}
    for i, left in enumerate(clips):
        for j in range(i + 1, len(clips)):
            right = clips[j]
            if not (left.comparable and right.comparable):
                continue
            assert left.frames is not None and right.frames is not None
            opening = similarity(left.frames.first, right.frames.first)
            closing = similarity(left.frames.last, right.frames.last)
            if opening >= threshold and closing >= threshold:
                _merge(index_of, i, j)

    buckets: dict[int, list[int]] = {}
    for i in range(len(clips)):
        buckets.setdefault(index_of[i], []).append(i)
    return [buckets[root] for root in sorted(buckets)]


def _continuity(left: ClipGroup, right: ClipGroup) -> float:
    """Cuánto continúa `right` a `left`: su primer fotograma frente al último de `left`."""
    last = left.last_signature
    first = right.first_signature
    if last is None or first is None:
        return 0.0
    return similarity(last, first)


def _ends_alike(left: ClipGroup, right: ClipGroup) -> float:
    """Cuánto se parecen los fotogramas finales de dos filas."""
    a, b = left.last_signature, right.last_signature
    if a is None or b is None:
        return 0.0
    return similarity(a, b)


def _chain(groups: list[ClipGroup], threshold: float) -> list[ClipGroup]:
    """Orden narrativo: cada fila va tras la que termina donde ella empieza."""
    if len(groups) < 2:
        return groups

    links = [
        [0.0 if i == j else _continuity(a, b) for j, b in enumerate(groups)]
        for i, a in enumerate(groups)
    ]

    def best_incoming(idx: int) -> float:
        return max((links[i][idx] for i in range(len(groups)) if i != idx), default=0.0)

    # Arranca la fila que menos continúa a otra: el principio de la cadena.
    start = min(range(len(groups)), key=lambda i: (round(best_incoming(i), 4), i))

    ordered_idx = [start]
    remaining = set(range(len(groups))) - {start}
    while remaining:
        current = ordered_idx[-1]
        strong = [j for j in remaining if links[current][j] >= threshold]
        pool = strong or list(remaining)
        nxt = max(pool, key=lambda j: (round(links[current][j], 4), -j))
        ordered_idx.append(nxt)
        remaining.remove(nxt)

    return [groups[i] for i in ordered_idx]


def _apply_ends_alike(groups: list[ClipGroup], threshold: float) -> list[ClipGroup]:
    """
    Regla explícita del usuario: si el último fotograma de un clip coincide con el
    último de otro, su fila va justo antes de la fila del otro.
    """
    consumed: set[int] = set()
    ordered: list[ClipGroup] = []
    for i, anchor in enumerate(groups):
        if i in consumed:
            continue
        consumed.add(i)
        ordered.append(anchor)
        for j in range(i + 1, len(groups)):
            if j in consumed:
                continue
            if _ends_alike(anchor, groups[j]) >= threshold:
                consumed.add(j)
                ordered.append(groups[j])
    return ordered


def _order_groups(groups: list[ClipGroup], threshold: float) -> list[ClipGroup]:
    """Encadena por continuidad y después aplica la regla de finales coincidentes."""
    return _apply_ends_alike(_chain(groups, threshold), threshold)


def group_clips(
    items: list[MediaInfo],
    frames: dict[Path | str, EdgeFrames | Exception] | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    preserve_input_order: bool = False,
) -> list[ClipGroup]:
    """
    Agrupa clips cuyo primer y último fotograma coinciden (o casi) y devuelve
    las filas ordenadas.

    Con `preserve_input_order` se respeta el orden recibido en lugar de encadenar
    por continuidad: así el reordenado manual del usuario no se deshace.
    """
    if not items:
        return []

    lookup = frames or {}

    def frames_for(media: MediaInfo) -> tuple[EdgeFrames | None, str]:
        found = lookup.get(media.path)
        if found is None:
            found = lookup.get(str(media.path))
        if isinstance(found, EdgeFrames):
            return found, ""
        if isinstance(found, Exception):
            return None, f"No se pudieron leer los fotogramas: {found}"
        return None, "Fotogramas aún sin analizar"

    clip_list = [
        GroupedClip(media=m, frames=f, problem=p) for m in items for f, p in [frames_for(m)]
    ]

    groups: list[ClipGroup] = []
    for bucket in _cluster(clip_list, threshold):
        members = [clip_list[i] for i in bucket]
        comparable = [c for c in members if c.comparable]
        if not comparable:
            for member in members:
                groups.append(ClipGroup((member,), 0.0, member.problem or "sin comparación"))
            continue
        ref = comparable[0]
        assert ref.frames is not None
        ranked = [ref] + sorted(
            (c for c in comparable[1:]),
            key=lambda c: -similarity(ref.frames.last, c.frames.last),  # type: ignore[union-attr]
        )
        rest = [c for c in members if not c.comparable]
        score = min(
            (
                min(
                    similarity(ref.frames.first, c.frames.first),  # type: ignore[union-attr]
                    similarity(ref.frames.last, c.frames.last),  # type: ignore[union-attr]
                )
                for c in ranked[1:]
            ),
            default=1.0,
        )
        origin = "coincidencia exacta" if score >= 0.995 else f"casi coincidencia ({score:.2f})"
        groups.append(ClipGroup(tuple(ranked + rest), score, origin))

    ordered = groups if preserve_input_order else _order_groups(groups, threshold)
    return [
        ClipGroup(g.members, g.score, g.origin, order=i) for i, g in enumerate(ordered)
    ]
