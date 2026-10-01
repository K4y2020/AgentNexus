"""Persist source frames for retries, keyed by source, seek and extraction settings."""

import hashlib
import json
import os
import tempfile
from contextlib import suppress
from pathlib import Path

try:
    from .source_identity import cache_key, media_fingerprint
except ImportError:
    from source_identity import cache_key, media_fingerprint


class FrameCache:
    def __init__(self, media, directory=None):
        self.directory = None
        if directory:
            with suppress(OSError):
                self.directory = Path(directory) / cache_key(media_fingerprint(Path(media)))

    def get(self, seconds, width, extract, *, quality):
        if self.directory is None:
            return extract()
        settings = {"seek": f"{seconds:.3f}", "width": width, "quality": quality, "version": 2}
        token = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
        target = self.directory / f"{token}.jpg"
        receipt = target.with_suffix(".json")
        try:
            data = target.read_bytes()
            saved = json.loads(receipt.read_text(encoding="utf-8"))
            if data and saved == {**settings, "sha256": hashlib.sha256(data).hexdigest()}:
                return data
        except (OSError, ValueError):
            pass
        data = extract()
        if not data:
            raise RuntimeError(f"empty frame at {seconds:.3f}s")
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            self._write(target, data)
            self._write(
                receipt,
                json.dumps({**settings, "sha256": hashlib.sha256(data).hexdigest()}).encode(),
            )
        except OSError:
            pass
        return data

    @staticmethod
    def _write(path, data):
        fd, name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(name, path)
        finally:
            Path(name).unlink(missing_ok=True)
