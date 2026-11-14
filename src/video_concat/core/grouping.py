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


def _cluster(clips: list[GroupedClip], threshold: float) -> list[list[int]]:
    """
    Agrupa por parecido de extremos, acotando cada grupo a su referencia.

    Sin la acotación, la transitividad del union-find fusiona A con C cuando
    `A~B` y `B~C` superan el umbral pero `A~C` no, y como sólo hay un clip
    activo por fila el tercero nunca podría concatenarse: una pérdida que
    ninguna acción de la UI puede deshacer.
    """
    buckets: list[list[int]] = []
    for i, clip in enumerate(clips):
        if not clip.comparable:
            buckets.append([i])
            continue
        assert clip.frames is not None
        placed = False
        for bucket in buckets:
            head = clips[bucket[0]]
            if not head.comparable:
                continue
            assert head.frames is not None
            opening = similarity(clip.frames.first, head.frames.first)
            closing = similarity(clip.frames.last, head.frames.last)
            if opening >= threshold and closing >= threshold:
                bucket.append(i)
                placed = True
                break
        if not placed:
            buckets.append([i])
    return buckets


def continuation_score(source: EdgeFrames | None, candidate: EdgeFrames | None) -> float:
    """Cuánto continúa `candidate` a `source`: primer fotograma frente a último de `source`."""
    if source is None or candidate is None:
        return 0.0
    return similarity(source.last, candidate.first)


def continues(source: EdgeFrames | None, candidate: EdgeFrames | None, threshold: float) -> bool:
    """¿`candidate` arranca donde termina `source`? Criterio del filtro «mostrar siguientes»."""
    return continuation_score(source, candidate) >= threshold


def follower_paths(groups: list[ClipGroup], anchor: Path | str, threshold: float) -> set[Path]:
    """
    Clips que arrancan donde termina el clip ancla, más el propio ancla.

    El ancla siempre se conserva: es la tarjeta que lleva el interruptor y sin
    ella no habría forma de desactivar el filtro. Un ancla inexistente devuelve
    el conjunto vacío, que el llamante interpreta como «sin filtro».
    """
    anchor_path = Path(anchor)
    reference = next(
        (m for g in groups for m in g.members if m.media.path == anchor_path), None
    )
    if reference is None:
        return set()
    kept = {reference.media.path}
    for group in groups:
        for member in group.members:
            if member.media.path == reference.media.path:
                continue
            if continues(reference.frames, member.frames, threshold):
                kept.add(member.media.path)
    return kept


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
    """
    Orden narrativo: cada fila va tras la que termina donde ella empieza.

    Las filas sin firmas comparables no participan: si entraran, todas sus
    aristas valdrían 0 y la de menor `best_incoming` (justo una de ellas)
    arrancaría la cadena, colocando un clip ilegible delante del plano de
    apertura real. Se encadenan aparte y se devuelven al final.
    """
    chained = [g for g in groups if g.first_signature is not None]
    stuck = [g for g in groups if g.first_signature is None]
    if len(chained) < 2:
        return chained + stuck

    links = [
        [0.0 if i == j else _continuity(a, b) for j, b in enumerate(chained)]
        for i, a in enumerate(chained)
    ]
    def best_incoming(idx: int) -> float:
        return max((links[i][idx] for i in range(len(chained)) if i != idx), default=0.0)

    # Arranca la fila que menos continúa a otra: el principio de la cadena.
    start = min(range(len(chained)), key=lambda i: (round(best_incoming(i), 4), i))

    ordered_idx = [start]
    remaining = set(range(len(chained))) - {start}
    while remaining:
        current = ordered_idx[-1]
        strong = [j for j in remaining if links[current][j] >= threshold]
        pool = strong or list(remaining)
        nxt = max(pool, key=lambda j: (round(links[current][j], 4), -j))
        ordered_idx.append(nxt)
        remaining.remove(nxt)

    return [chained[i] for i in ordered_idx] + stuck


def _apply_ends_alike(groups: list[ClipGroup], threshold: float) -> list[ClipGroup]:
    """
    Regla explícita: si el último fotograma de una fila coincide con el de otra,
    van seguidas, y el orden entre ambas es el que ya traían.

    La relación de finales coincidentes es simétrica, así que exigir "A antes
    que B" para todo par con el mismo final no tiene solución única: se agrupa
    por finales parecidos y se respeta el orden previo dentro de cada bloque.
    Se aplica como desempate, nunca por encima de la continuidad: si la cadena
    ya puso a `anchor` seguida de la fila que empieza exactamente donde acaba,
    no se cuela entre ellas una fila que sólo comparte el fotograma final.
    """
    if len(groups) < 2:
        return groups

    ordered = list(groups)
    for _ in range(len(ordered)):
        changed = False
        for i in range(len(ordered) - 1):
            anchor, successor = ordered[i], ordered[i + 1]
            for j in range(i + 2, len(ordered)):
                candidate = ordered[j]
                alike = _ends_alike(anchor, candidate)
                if alike < threshold:
                    continue
                # La regla gana cuando el parecido de finales supera a la
                # continuidad que ofrece el sucesor actual: dos filas que acaban
                # igual pertenecen juntas, aunque otra empiece donde acaba ésta.
                if max(alike, _continuity(anchor, candidate)) <= _continuity(
                    anchor, successor
                ):
                    continue
                ordered.insert(i + 1, ordered.pop(j))
                changed = True
                break
            if changed:
                break
        if not changed:
            break
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
