"""Source media identity shared by the index, the transcript and their readers.

``source.json`` identifies media by its size plus the SHA-256 of its first and
last 256 KiB (see ``probe.probe_media``). A transcript records the same
fingerprint of the file it was made from, so every reader can tell whether it
belongs to the committed source. A file name says nothing: every upload may be
called ``source.mp4``.

Standard library only, so it can be imported from the package, from the MCP
server, and by scripts that load a pipeline module on its own.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

CHUNK_SIZE = 256 * 1024  # 256 KiB for head/tail hash
FINGERPRINT_FIELDS = ("size_bytes", "sha256_head", "sha256_tail")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def _sha256_chunk(path: Path, *, tail: bool = False) -> str:
    """SHA-256 of the first (or last) CHUNK_SIZE bytes of a file."""
    size = path.stat().st_size
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        if tail and size > CHUNK_SIZE:
            fh.seek(-CHUNK_SIZE, 2)
        data = fh.read(CHUNK_SIZE)
    h.update(data)
    return h.hexdigest()


def media_fingerprint(path: Path) -> dict[str, Any]:
    """The identity ``source.json`` records for this file."""
    path = Path(path)
    return {
        "size_bytes": path.stat().st_size,
        "sha256_head": _sha256_chunk(path, tail=False),
        "sha256_tail": _sha256_chunk(path, tail=True),
    }


def fingerprint_of(record: Any) -> dict[str, Any] | None:
    """The fingerprint fields of a source.json dict or SourceMediaRecord.

    Returns None unless every field is present and well formed, so a partial
    or corrupted record can never compare equal to anything.
    """
    if record is None:
        return None
    if isinstance(record, dict):
        values = {field: record.get(field) for field in FINGERPRINT_FIELDS}
    else:
        values = {field: getattr(record, field, None) for field in FINGERPRINT_FIELDS}
    size = values["size_bytes"]
    if isinstance(size, bool) or not isinstance(size, int) or size < 0:
        return None
    if not all(
        isinstance(values[field], str) and _HEX64.fullmatch(values[field])
        for field in ("sha256_head", "sha256_tail")
    ):
        return None
    return values


def transcript_matches_source(transcript: Any, source: Any) -> bool:
    """True only when the transcript's recorded fingerprint equals the source's.

    A transcript without a fingerprint (made before this was recorded, or by
    another tool) cannot be tied to the source and does not match.
    """
    if not isinstance(transcript, dict):
        return False
    recorded = fingerprint_of(transcript.get("source_fingerprint"))
    expected = fingerprint_of(source)
    return recorded is not None and recorded == expected


def cache_key(fingerprint: dict[str, Any]) -> str:
    """A short, path-safe key for caches derived from one source file."""
    joined = "|".join(str(fingerprint[field]) for field in FINGERPRINT_FIELDS)
    return hashlib.sha256(joined.encode("ascii")).hexdigest()[:24]
