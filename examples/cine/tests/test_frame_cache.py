"""Failed vision requests reuse frames without mixing sources or extraction settings."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline.frame_cache import FrameCache


def test_frames_survive_new_cache_instances_and_corruption_is_repaired(tmp_path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"video-a")
    directory = tmp_path / "cache"
    calls = []

    def extract():
        calls.append(True)
        return b"frame"

    assert FrameCache(media, directory).get(1.2344, 384, extract, quality=4) == b"frame"
    assert FrameCache(media, directory).get(1.234, 384, extract, quality=4) == b"frame"
    assert len(calls) == 1
    next(directory.rglob("*.jpg")).write_bytes(b"corrupted")
    assert FrameCache(media, directory).get(1.234, 384, extract, quality=4) == b"frame"
    assert len(calls) == 2
    FrameCache(media, directory).get(1.234, 384, extract, quality=5)
    assert len(calls) == 3
    FrameCache(media, directory).get(1.234, 384, extract, quality=5)
    assert len(calls) == 3
    FrameCache(media, directory).get(1.234, 512, extract, quality=4)
    FrameCache(media, directory).get(2, 384, extract, quality=4)
    media.write_bytes(b"video-b")
    FrameCache(media, directory).get(1.234, 384, extract, quality=4)
    assert len(calls) == 6
