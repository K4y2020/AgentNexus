"""An opening assembled from later footage is found, and only then."""

import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import opening_teaser as ot  # noqa: E402

STEP = ot.STEP


def _hash(rng):
    return rng.getrandbits(256)


def _noisy(value, rng, bits=2):
    for position in rng.sample(range(256), bits):
        value ^= 1 << position
    return value


def _film(seconds=60, seed=7):
    """A film of unique frames: (hashes, times, contrast)."""
    rng = random.Random(seed)
    count = int(seconds / STEP)
    return [_hash(rng) for _ in range(count)], [i * STEP for i in range(count)], [50.0] * count


def _splice(hashes, at, source, length, rng):
    """Overwrite ``length`` frames from ``at`` with (slightly re-encoded) copies from ``source``."""
    for k in range(length):
        hashes[at + k] = _noisy(hashes[source + k], rng)


def _teaser(hashes, times, contrast, head=10.0):
    return ot.pick_teaser(ot.find_reused_clips(hashes, times, contrast, head))


# ---------------------------------------------------------------------------
# Frames that return later
# ---------------------------------------------------------------------------


def test_a_montage_of_later_clips_is_a_teaser():
    hashes, times, contrast = _film()
    rng = random.Random(1)
    _splice(hashes, 2, 200, 5, rng)  # 0.5-1.75 s reuses 50 s
    _splice(hashes, 10, 120, 6, rng)  # 2.5-4.0 s reuses 30 s

    teaser = _teaser(hashes, times, contrast)

    assert teaser is not None
    assert [c["twin_start"] for c in teaser["clips"]] == [50.0, 30.0]
    assert teaser["clips"][0]["start"] == 0.5 and teaser["end_seconds"] == 4.0


def test_a_reused_clip_that_is_re_timed_by_a_frame_or_two_is_one_clip():
    hashes, times, contrast = _film()
    rng = random.Random(2)
    for k, source in enumerate([200, 201, 201, 203, 204, 204, 205, 207]):  # wanders, never exact
        hashes[2 + k] = _noisy(hashes[source], rng)

    teaser = _teaser(hashes, times, contrast)

    assert teaser is not None and len(teaser["clips"]) == 1
    assert teaser["clips"][0]["end"] - teaser["clips"][0]["start"] >= 2.0


def test_a_film_that_never_repeats_its_opening_has_no_teaser():
    hashes, times, contrast = _film()
    assert ot.find_reused_clips(hashes, times, contrast, 10.0) == []


def test_one_static_shot_is_not_a_reuse_of_itself():
    """Every frame of a shot that does not move looks like the next one."""
    hashes, times, contrast = _film()
    for i in range(0, 60):  # 15 s of one still shot
        hashes[i] = hashes[0]
    assert ot.find_reused_clips(hashes, times, contrast, 10.0) == []


def test_flat_frames_match_everything_and_are_ignored():
    hashes, times, contrast = _film()
    rng = random.Random(3)
    _splice(hashes, 2, 200, 8, rng)
    for i in range(2, 10):
        contrast[i] = 1.0  # a black opening that also occurs later
    assert ot.find_reused_clips(hashes, times, contrast, 10.0) == []


# ---------------------------------------------------------------------------
# What makes reused footage a teaser
# ---------------------------------------------------------------------------


def _clip(start, end, twin):
    return {"start": start, "end": end, "twin_start": twin, "twin_end": twin + (end - start)}


def test_a_lone_short_clip_is_not_enough():
    assert ot.pick_teaser([_clip(0.5, 1.5, 100.0)]) is None


def test_a_lone_long_clip_is_a_teaser():
    teaser = ot.pick_teaser([_clip(0.0, 3.0, 100.0)])
    assert teaser is not None and teaser["end_seconds"] == 3.0


def test_the_first_clip_must_start_near_the_beginning():
    assert ot.pick_teaser([_clip(6.0, 9.0, 100.0), _clip(9.0, 12.0, 140.0)]) is None


def test_clips_far_apart_are_not_one_teaser():
    teaser = ot.pick_teaser([_clip(0.5, 2.5, 100.0), _clip(20.0, 24.0, 150.0)])
    assert teaser is not None and teaser["end_seconds"] == 2.5


def test_footage_returning_inside_the_teaser_is_a_loop_not_a_teaser():
    assert ot.pick_teaser([_clip(0.0, 2.0, 3.0), _clip(2.0, 4.0, 3.5)]) is None


def test_a_mostly_unmatched_span_is_not_a_teaser():
    assert ot.pick_teaser([_clip(0.5, 1.25, 100.0), _clip(3.0, 3.75, 140.0)]) is None


# ---------------------------------------------------------------------------
# Transcript rows
# ---------------------------------------------------------------------------


def _rows(*spans):
    return [{"start": a, "end": b, "text": f"line {i}"} for i, (a, b) in enumerate(spans)]


def test_rows_inside_the_teaser_are_marked_and_the_rest_are_not():
    rows = _rows((0.1, 4.7), (3.0, 5.0), (3.0, 7.0), (5.0, 6.0), (20.0, 22.0))
    teaser = {"status": "found", "end_seconds": 4.75}

    assert ot.tag_rows(rows, teaser) == 2
    # The third row only starts inside the teaser: less than 60% of it is.
    assert [bool(r.get("teaser")) for r in rows] == [True, True, False, False, False]


def test_a_row_is_marked_only_once():
    rows = _rows((0.0, 2.0))
    teaser = {"status": "found", "end_seconds": 4.0}
    assert ot.tag_rows(rows, teaser) == 1
    assert ot.tag_rows(rows, teaser) == 0


@pytest.mark.parametrize("status", ["none", "unavailable"])
def test_nothing_is_marked_without_a_teaser(status):
    rows = _rows((0.0, 2.0))
    assert ot.tag_rows(rows, {"status": status}) == 0
    assert "teaser" not in rows[0]


# ---------------------------------------------------------------------------
# Whole films
# ---------------------------------------------------------------------------

try:
    import cv2
    import numpy as np
except ImportError:  # the frame-level tests above need no video library
    cv2 = np = None

needs_video = pytest.mark.skipif(cv2 is None, reason="needs opencv-python and numpy")

FPS = 10
SIZE = (48, 64)  # rows, columns


def _pattern(seed):
    """A blocky pattern that survives video compression, one per second of film."""
    rng = np.random.default_rng(seed)
    coarse = rng.integers(0, 255, size=(4, 4), dtype=np.uint8)
    return cv2.resize(coarse, (SIZE[1], SIZE[0]), interpolation=cv2.INTER_NEAREST)


def _write_reusing_film(path, seconds, reused_seconds):
    """Body second ``s`` shows pattern ``s``; the opening shows the ``reused_seconds`` patterns first."""
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"MJPG"), FPS, (SIZE[1], SIZE[0]), isColor=False
    )
    assert writer.isOpened()
    frames = [_pattern(s) for s in reused_seconds]
    frames += [_pattern(s) for s in range(seconds)]
    for pattern in frames:
        for _ in range(FPS):
            writer.write(pattern)
    writer.release()
    return path


@needs_video
def test_a_film_opening_with_its_own_later_seconds_is_found(tmp_path):
    film = _write_reusing_film(tmp_path / "film.avi", 40, reused_seconds=[25, 30, 12])

    result = ot.detect_opening_teaser(film)

    assert result["status"] == "found" and result["method"] == "frame_reuse"
    assert 2.5 <= result["end_seconds"] <= 3.5
    twins = sorted(round(c["twin_start"]) for c in result["clips"])
    # The reused seconds sit 3 s later in the file than in the body; the twins are those.
    assert twins == [15, 28, 33]


@needs_video
def test_a_film_that_does_not_reuse_its_opening_has_none(tmp_path):
    film = _write_reusing_film(tmp_path / "film.avi", 40, reused_seconds=[])
    assert ot.detect_opening_teaser(film) == {"status": "none", "method": "frame_reuse"}


@needs_video
def test_an_unreadable_file_is_reported_not_raised(tmp_path):
    broken = tmp_path / "broken.mp4"
    broken.write_bytes(b"not a video")
    result = ot.detect_opening_teaser(broken)
    assert result["status"] == "unavailable" and result["error"]


@needs_video
def test_a_very_long_film_is_not_scanned(tmp_path, monkeypatch):
    film = _write_reusing_film(tmp_path / "film.avi", 20, reused_seconds=[])
    monkeypatch.setattr(ot, "LONG_VIDEO", 5.0)
    result = ot.detect_opening_teaser(film)
    assert result["status"] == "unavailable" and "longer than" in result["error"]
