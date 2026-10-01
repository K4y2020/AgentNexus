"""Optional GPU runtime for vocal separation and speech recognition.

``gpu_setup.py`` builds one virtualenv (CUDA torch, demucs, faster-whisper, cuBLAS)
and records it in ``ready.json``. This module only uses that environment: it never
installs anything, and every failure reaches the caller as ``GpuUnavailable`` so the
CPU path can take over. Each job runs in its own process with a timeout, one at a
time per machine, so a stuck driver or a full card cannot take a film run down.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

HOME_VAR = "CINE_GPU_ENV"  # directory of the GPU environment
MODE_VAR = "CINE_GPU"  # "off" keeps every step on the CPU
READY_FILE = "ready.json"
SCHEMA = 1
# A 40-minute film takes about 90 s on the card; these only bound a hang.
SEPARATE_TIMEOUT = 900
TRANSCRIBE_TIMEOUT = 900
LOCK_TIMEOUT = 600
_OFF = {"off", "0", "false", "no", "cpu"}
_WORKER = Path(__file__).with_name("gpu_whisper_worker.py")


class GpuUnavailable(RuntimeError):
    """The GPU cannot do this step; the caller runs it on the CPU instead."""


def env_dir() -> Path:
    override = os.environ.get(HOME_VAR, "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".agentnexus" / "cache" / "cine-gpu"


def python_exe(env: Path) -> Path:
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def status() -> dict[str, Any]:
    """``state`` is ready, off, not_set_up or broken; a broken one carries ``detail``."""
    if os.environ.get(MODE_VAR, "").strip().lower() in _OFF:
        return {"state": "off"}
    env = env_dir()
    marker = env / READY_FILE
    if not marker.is_file():
        return {"state": "not_set_up", "env": str(env)}
    try:
        ready = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "broken", "env": str(env), "detail": f"{READY_FILE} is unreadable"}
    python = python_exe(env)
    if not isinstance(ready, dict) or ready.get("schema") != SCHEMA:
        return {"state": "broken", "env": str(env), "detail": f"{READY_FILE} has another schema"}
    if ready.get("demucs") is not True or ready.get("whisper") is not True:
        return {"state": "broken", "env": str(env), "detail": "setup did not pass its tests"}
    if not python.is_file():
        return {"state": "broken", "env": str(env), "detail": f"{python} is missing"}
    return {
        "state": "ready",
        "env": str(env),
        "python": str(python),
        "gpu": ready.get("gpu"),
        "torch": ready.get("torch"),
    }


def _ready() -> dict[str, Any]:
    state = status()
    if state["state"] != "ready":
        raise GpuUnavailable(f"GPU environment is {state['state']}: {state.get('detail', '')}".strip())
    return state


@contextlib.contextmanager
def _exclusive(env: Path) -> Iterator[None]:
    """One GPU job at a time on this machine: two films must not fill the card together."""
    try:
        from filelock import FileLock, Timeout
    except ImportError:
        yield
        return
    lock = FileLock(str(env / "gpu.lock"))
    try:
        lock.acquire(timeout=LOCK_TIMEOUT)
    except Timeout as exc:
        raise GpuUnavailable(f"the GPU stayed busy for {LOCK_TIMEOUT} s") from exc
    except OSError as exc:
        raise GpuUnavailable(f"cannot take the GPU lock: {exc}") from exc
    try:
        yield
    finally:
        lock.release()


def _tail(raw: bytes | str | None) -> str:
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else (raw or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-3:])[-400:]


def _run(command: list[str], *, timeout: int, what: str) -> None:
    child = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    try:
        done = subprocess.run(
            command, capture_output=True, stdin=subprocess.DEVNULL, timeout=timeout, env=child
        )
    except subprocess.TimeoutExpired as exc:
        raise GpuUnavailable(f"{what} timed out after {timeout} s") from exc
    except OSError as exc:
        raise GpuUnavailable(f"{what} could not start: {exc}") from exc
    if done.returncode != 0:
        raise GpuUnavailable(f"{what} failed: {_tail(done.stderr)}")


def separate_vocals(audio: Path, out_dir: Path) -> None:
    """Split the vocal stem with demucs on the GPU, into the layout the CPU run writes."""
    state = _ready()
    command = [
        state["python"], "-m", "demucs", "-d", "cuda", "--two-stems", "vocals",
        "-n", "htdemucs", "-o", str(out_dir), str(audio),
    ]  # fmt: skip
    with _exclusive(Path(state["env"])):
        _run(command, timeout=SEPARATE_TIMEOUT, what="vocal separation")


def transcribe(
    audio: Path, *, model: str, language: str | None, initial_prompt: str | None = None
) -> tuple[list[SimpleNamespace], SimpleNamespace]:
    """Whisper on the GPU; returns the segments and info faster-whisper would."""
    state = _ready()
    with tempfile.TemporaryDirectory(prefix="cine-gpu-") as scratch:
        request, result = Path(scratch, "request.json"), Path(scratch, "result.json")
        request.write_text(
            json.dumps(
                {
                    "audio": str(audio),
                    "model": model,
                    "language": language,
                    "initial_prompt": initial_prompt,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with _exclusive(Path(state["env"])):
            _run(
                [state["python"], str(_WORKER), str(request), str(result)],
                timeout=TRANSCRIBE_TIMEOUT,
                what="speech recognition",
            )
        try:
            payload = json.loads(result.read_text(encoding="utf-8"))
            segments = [_segment(row) for row in payload["segments"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GpuUnavailable(f"speech recognition left no usable result: {exc}") from exc
    info = SimpleNamespace(language=payload.get("language"), duration=payload.get("duration"))
    return segments, info


def _segment(row: dict[str, Any]) -> SimpleNamespace:
    """The attributes the transcript serializer reads from a faster-whisper segment."""
    words = [SimpleNamespace(**word) for word in row.get("words") or []]
    return SimpleNamespace(start=row["start"], end=row["end"], text=row["text"], words=words)
