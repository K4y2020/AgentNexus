"""Find an opening teaser: footage cut in from later in the film and used as a hook.

Some films open with a few seconds assembled from their own later scenes. Those
seconds are not events at 0:00. Read as such, they put a scene's outcome before
its cause, and their lines are heard again in the body. A teaser reuses footage
frame for frame, so it is found by looking for the opening's frames again later
in the film: a run of frames that returns, in order, at a fixed offset.

Only reuse of identical footage is detected. A teaser that was re-graded, cropped
or sped up, or one that only moves a line of speech forward, is not.
"""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path

METHOD = "frame_reuse"
STEP = 0.25  # seconds between sampled frames
NEAR = 12  # of 256 bits: still the same frame after re-encoding
MOVED_ON = 20  # of 256 bits: the film has left the frame, so a match is a return
MIN_CONTRAST = 6.0  # a flatter frame (black, white, a still title) matches everything
MIN_CLIP = 3  # sampled frames that must return together to count as a reused clip
MAX_GAP = 1  # missed samples tolerated inside a clip (a fade, a dropped frame)
BAND = 2  # samples either side of an offset that still count as the same offset
MAX_START = 3.0  # the first reused clip starts this soon after the film does
MAX_BRIDGE = 2.0  # seconds between two clips of one teaser
MIN_SHARE = 0.5  # share of the teaser's span that is reused footage
MIN_SOLO_CLIP = 2.0  # a lone clip must last this long to be a teaser
LONG_VIDEO = 1800.0  # seconds; a longer film is not scanned
HEAD_SHARE, HEAD_MIN, HEAD_MAX = 0.12, 6.0, 30.0  # how much of the opening is examined
ROW_SHARE = 0.6  # share of a transcript row inside the teaser that makes it a teaser row


def head_seconds(duration: float) -> float:
    return min(HEAD_MAX, max(HEAD_MIN, duration * HEAD_SHARE))


def sample_hashes(media: Path, step: float = STEP):
    """Hash a frame every ``step`` seconds: (hashes, times, contrast, duration).

    The hash is a 256-bit average hash of a 16x16 grey version of the frame, kept as
    an int so two frames compare with one XOR.
    """
    import cv2
    import numpy as np

    cap = cv2.VideoCapture(str(media))
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
        count = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0
        if fps <= 0 or count <= 0:
            raise ValueError("unreadable video")
        duration = count / fps
        if duration > LONG_VIDEO:
            raise ValueError(f"video is longer than {int(LONG_VIDEO // 60)} minutes")
        hashes: list[int] = []
        times: list[float] = []
        contrast: list[float] = []
        index, at = 0, 0.0
        while at < duration:
            target = round(at * fps)
            while index < target and cap.grab():
                index += 1
            ok, frame = cap.read()
            index += 1
            if not ok:
                break
            tiny = cv2.resize(
                cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 64), interpolation=cv2.INTER_AREA
            )
            small = cv2.resize(tiny, (16, 16), interpolation=cv2.INTER_AREA)
            bits = (small > small.mean()).flatten()
            hashes.append(int.from_bytes(np.packbits(bits).tobytes(), "big"))
            times.append(round(at, 3))
            contrast.append(float(tiny.std()))
            at += step
        return hashes, times, contrast, duration
    finally:
        cap.release()


def find_reused_clips(hashes, times, contrast, head, step: float = STEP) -> list[dict]:
    """Runs of the opening's frames that come back later in the film, in order.

    A frame only counts as returning when the film left it first: the frames of
    one static shot all look alike, and that is a shot, not a reuse. Every sample
    in the opening votes for the offset at which each of its matches lies; a clip
    is a run of consecutive samples voting for the same offset.
    """
    n = len(hashes)
    votes: dict[int, list[int]] = defaultdict(list)
    for i in range(n):
        if times[i] >= head:
            break
        if contrast[i] < MIN_CONTRAST:
            continue
        left = next(
            (k for k in range(i + 1, n) if (hashes[i] ^ hashes[k]).bit_count() > MOVED_ON), None
        )
        if left is None:
            continue
        for j in range(left, n):
            if contrast[j] >= MIN_CONTRAST and (hashes[i] ^ hashes[j]).bit_count() <= NEAR:
                votes[j - i].append(i)

    runs: list[tuple[int, int, int, int]] = []  # (hits, first, last, offset)
    for offset in votes:
        # A reused clip is re-timed by a frame or two, so its offset wanders: a sample
        # belongs to the band around ``offset`` when it matched anywhere inside it.
        indices = sorted(
            {i for d in range(offset - BAND, offset + BAND + 1) for i in votes.get(d, ())}
        )
        first = last = indices[0]
        hits = 1
        for i in indices[1:]:
            if i - last > MAX_GAP + 1:
                runs.append((hits, first, last, offset))
                first, hits = i, 0
            last = i
            hits += 1
        runs.append((hits, first, last, offset))

    clips: list[dict] = []
    taken: set[int] = set()
    # Neighbouring bands describe the same clip; the one with the most hits wins.
    for hits, first, last, offset in sorted((r for r in runs if r[0] >= MIN_CLIP), key=lambda r: (-r[0], r[1])):
        span = set(range(first, last + 1))
        if len(span & taken) > len(span) / 2:
            continue
        taken |= span
        clips.append(
            {
                "start": times[first],
                "end": round(times[last] + step, 3),
                "twin_start": times[first + offset],
                "twin_end": round(times[last + offset] + step, 3),
            }
        )
    return sorted(clips, key=lambda c: c["start"])


def pick_teaser(clips: list[dict]) -> dict | None:
    """The opening teaser among reused clips, or None.

    A teaser starts near the beginning and is a chain of clips close together whose
    footage comes back after the teaser ends. One long clip is enough; two or more
    short ones make a montage.
    """
    chain: list[dict] = []
    for clip in clips:
        if not chain:
            if clip["start"] > MAX_START:
                return None
            chain.append(clip)
        elif clip["start"] - chain[-1]["end"] <= MAX_BRIDGE:
            chain.append(clip)
        else:
            break
    # Footage that returns inside the teaser itself is a loop, not a reuse from later.
    while chain:
        end = chain[-1]["end"]
        kept = [c for c in chain if c["twin_start"] > end]
        if len(kept) == len(chain):
            break
        chain = kept
    if not chain:
        return None
    matched = sum(c["end"] - c["start"] for c in chain)
    span = chain[-1]["end"] - chain[0]["start"]
    if span <= 0 or matched / span < MIN_SHARE:
        return None
    if len(chain) < 2 and matched < MIN_SOLO_CLIP:
        return None
    return {"end_seconds": round(chain[-1]["end"], 2), "clips": chain, "matched_seconds": round(matched, 2)}


def detect_opening_teaser(media: Path) -> dict:
    """Scan a film's opening for reused footage. Never raises: indexing must not depend on it.

    ``status`` is ``found`` (with ``end_seconds`` and ``clips``), ``none``, or
    ``unavailable`` (with ``error``).
    """
    try:
        hashes, times, contrast, duration = sample_hashes(Path(media))
        head = head_seconds(duration)
        teaser = pick_teaser(find_reused_clips(hashes, times, contrast, head))
    except Exception as exc:  # noqa: BLE001 - a failed scan is reported, not raised
        return {"status": "unavailable", "method": METHOD, "error": f"{type(exc).__name__}: {exc}"}
    if teaser is None:
        return {"status": "none", "method": METHOD}
    return {"status": "found", "method": METHOD, **teaser}


def tag_rows(rows: list[dict], teaser: dict) -> int:
    """Mark the transcript rows that sit inside the teaser; returns how many were newly marked."""
    if teaser.get("status") != "found":
        return 0
    end = float(teaser.get("end_seconds") or 0.0)
    marked = 0
    for row in rows:
        start, stop = float(row["start"]), float(row["end"])
        if stop > start and not row.get("teaser") and (min(stop, end) - start) / (stop - start) >= ROW_SHARE:
            row["teaser"] = True
            marked += 1
    return marked
