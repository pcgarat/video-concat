"""Conversión tiempo global ↔ clip del timeline (lógica pura)."""

from __future__ import annotations

from pathlib import Path

import pytest

from video_concat.ui.timeline import TimelineMap


def _map():
    return TimelineMap([(Path("a.mp4"), 2.0), (Path("b.mp4"), 3.0), (Path("c.mp4"), 1.0)])


def test_total_is_the_sum_of_durations():
    assert _map().total == pytest.approx(6.0)


def test_start_and_end_of_each_clip():
    timeline = _map()
    assert timeline.start(0) == pytest.approx(0.0)
    assert timeline.start(1) == pytest.approx(2.0)
    assert timeline.start(2) == pytest.approx(5.0)
    assert timeline.end(2) == pytest.approx(6.0)


def test_location_splits_global_time_into_clip_and_local_offset():
    timeline = _map()
    point = timeline.location(3.5)
    assert point is not None
    assert point.index == 1
    assert point.path == Path("b.mp4")
    assert point.local == pytest.approx(1.5)


def test_boundary_belongs_to_the_next_clip():
    """Exactamente 2.0 s ya es el principio de b, no el final de a."""
    timeline = _map()
    assert timeline.index_at(2.0) == 1
    assert timeline.local_time(1, 2.0) == pytest.approx(0.0)


def test_times_outside_the_timeline_are_clamped():
    timeline = _map()
    assert timeline.index_at(-4.0) == 0
    assert timeline.index_at(999.0) == 2
    assert timeline.location(999.0).local == pytest.approx(1.0)


def test_time_of_and_local_time_are_inverse():
    timeline = _map()
    for index in range(3):
        absolute = timeline.time_of(index, 0.5)
        assert timeline.index_at(absolute) == index
        assert timeline.local_time(index, absolute) == pytest.approx(0.5)


def test_zero_duration_does_not_break_the_sum():
    timeline = TimelineMap([(Path("a.mp4"), 0.0), (Path("b.mp4"), 1.0)])
    assert timeline.total == pytest.approx(1.001)
    assert timeline.index_at(0.0) == 0
    assert timeline.local_time(0, 0.5) == pytest.approx(0.001)


def test_empty_timeline_is_safe():
    timeline = TimelineMap([])
    assert not timeline
    assert timeline.total == 0.0
    assert timeline.location(1.0) is None
    assert timeline.index_at(1.0) == 0
    assert timeline.time_of(2, 1.0) == 0.0


def test_index_of_path():
    timeline = _map()
    assert timeline.index_of(Path("c.mp4")) == 2
    assert timeline.index_of(Path("nope.mp4")) is None


def _member(name: str, duration: float = 2.0):
    from video_concat.core.probe import MediaInfo, StreamInfo
    from video_concat.ui.timeline import TimelineMember

    return TimelineMember(
        media=MediaInfo(
            path=Path(name),
            duration=duration,
            format_name="mov,mp4",
            video=StreamInfo(codec_name="h264", codec_type="video", width=320, height=180),
            audio=None,
            size_bytes=1024,
        )
    )


class TestTimelineSlot:
    """Una ranura del timeline conserva todas las variantes de su fila."""

    def test_single_member_slot_is_not_a_variant_slot(self):
        from video_concat.ui.timeline import TimelineSlot

        slot = TimelineSlot(row=0, members=(_member("a.mp4"),))
        assert not slot.has_variants
        assert slot.media.path == Path("a.mp4")

    def test_variant_slot_exposes_the_active_member(self):
        from video_concat.ui.timeline import TimelineSlot

        slot = TimelineSlot(
            row=3,
            members=(_member("v1.mp4"), _member("v2.mp4")),
            active=1,
        )
        assert slot.has_variants
        assert slot.media.path == Path("v2.mp4")
        assert slot.paths == [Path("v1.mp4"), Path("v2.mp4")]

    def test_out_of_range_active_falls_back_to_the_first_member(self):
        from video_concat.ui.timeline import TimelineSlot

        slot = TimelineSlot(row=0, members=(_member("a.mp4"),), active=7)
        assert slot.media.path == Path("a.mp4")

    def test_index_of_locates_a_variant(self):
        from video_concat.ui.timeline import TimelineSlot

        slot = TimelineSlot(
            row=1, members=(_member("a.mp4"), _member("b.mp4")), active=0
        )
        assert slot.index_of(Path("b.mp4")) == 1
        assert slot.index_of(Path("z.mp4")) is None
