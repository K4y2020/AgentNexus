"""Build the optional GPU environment for Cine's speech recognition and vocal separation.

Run it once, yourself. It downloads several gigabytes (CUDA torch about 2 GB, cuBLAS
about 0.6 GB, plus the Whisper model when it is not cached yet); a film run never
downloads anything. Afterwards film_analyze runs Whisper and demucs on the GPU, about
ten times faster, and falls back to the CPU whenever a GPU step fails.

    python gpu_setup.py             build the environment, test it, mark it ready
    python gpu_setup.py --check     test an existing environment again
    python gpu_setup.py --remove    delete the environment
    python gpu_setup.py --dry-run   print the commands and change nothing

The environment lives in ~/.agentnexus/cache/cine-gpu (CINE_GPU_ENV moves it);
CINE_GPU=off makes film runs ignore it.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any, Callable

try:
    from . import gpu_runtime as runtime
except ImportError:  # run as a script, outside the package
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gpu_runtime as runtime

PYTHON = "3.12"
PACKAGES = ["demucs", "numpy<2", "faster-whisper", "nvidia-cublas-cu12", "nvidia-cuda-runtime-cu12"]
MODEL = "large-v3-turbo"
WORKER = Path(__file__).with_name("gpu_whisper_worker.py")
_VERSIONS = (
    "import json, importlib.metadata as m, torch;"
    "ok = torch.cuda.is_available();"
    "print(json.dumps({'torch': torch.__version__, 'cuda': ok,"
    " 'device': torch.cuda.get_device_name(0) if ok else None,"
    " 'faster_whisper': m.version('faster-whisper'), 'ctranslate2': m.version('ctranslate2'),"
    " 'cublas': m.version('nvidia-cublas-cu12')}))"
)


class SetupError(RuntimeError):
    """A step failed; the message says what to fix."""


def find_gpu(run: Callable[..., Any] | None = None) -> dict[str, Any] | None:
    """The first NVIDIA card nvidia-smi reports, or None."""
    query = "--query-gpu=name,driver_version,memory.total"
    try:
        done = (run or subprocess.run)(
            ["nvidia-smi", query, "--format=csv,noheader,nounits"],
            capture_output=True, encoding="utf-8", errors="replace", timeout=30,
        )  # fmt: skip
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    line = next((raw for raw in (done.stdout or "").splitlines() if raw.strip()), "")
    parts = [part.strip() for part in line.split(",")]
    if len(parts) != 3 or not parts[2].isdigit():
        return None
    return {"name": parts[0], "driver": parts[1], "memory_mb": int(parts[2])}


def create_command(env: Path) -> list[str]:
    return ["uv", "venv", "--python", PYTHON, "--allow-existing", str(env)]


def install_command(env: Path, torch: str | None, extra: list[str]) -> list[str]:
    """One install, so uv resolves torch and everything that pins it together.

    Without a torch requirement of its own, uv picks the CUDA build that matches the
    driver (``--torch-backend auto``); a pinned requirement (a wheel URL, say) is used as given.
    """
    command = ["uv", "pip", "install", "--python", str(runtime.python_exe(env)), "--link-mode", "copy"]
    if torch is None:
        command += ["--torch-backend", "auto"]
    return [*command, torch or "torch", *PACKAGES, *extra]


def _write_tone(path: Path, seconds: int = 3, rate: int = 44100) -> None:
    with wave.open(str(path), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(rate)
        frames = bytearray()
        for i in range(seconds * rate):
            t = i / rate
            sample = int(4500 * (math.sin(2 * math.pi * 440 * t) + math.sin(2 * math.pi * 880 * t)))
            frames += struct.pack("<hh", sample, sample)
        out.writeframes(bytes(frames))


class _Steps:
    """Runs the commands, or only prints them for --dry-run."""

    def __init__(self, dry_run: bool):
        self.dry_run = dry_run

    def show(self, message: str) -> None:
        print(message, flush=True)

    def run(self, command: list[str], *, what: str, capture: bool = False) -> str:
        self.show(f"  $ {' '.join(command)}")
        if self.dry_run:
            return ""
        try:
            done = subprocess.run(
                command, capture_output=capture, encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL
            )
        except OSError as exc:
            raise SetupError(f"{what}: {exc}") from exc
        if done.returncode != 0:
            detail = runtime._tail(done.stderr) if capture else f"exit code {done.returncode}"
            raise SetupError(f"{what} failed: {detail}")
        return done.stdout or ""


def _test(steps: _Steps, env: Path, model: str) -> dict[str, Any]:
    """Prove the environment: torch sees the card, demucs separates, Whisper decodes."""
    python = runtime.python_exe(env)
    steps.show("Testing the environment")
    raw = steps.run([str(python), "-c", _VERSIONS], what="reading versions", capture=True)
    info = {} if steps.dry_run else json.loads(raw.strip().splitlines()[-1])
    if not steps.dry_run and not info.get("cuda"):
        raise SetupError(
            f"torch {info.get('torch')} cannot see the GPU; pass --torch with a CUDA build "
            "of torch (a wheel URL or a requirement)"
        )
    steps.run([str(python), str(WORKER), "--selftest", model], what="Whisper on the GPU", capture=True)
    with tempfile.TemporaryDirectory(prefix="cine-gpu-probe-") as scratch:
        tone, out = Path(scratch, "tone.wav"), Path(scratch, "out")
        if not steps.dry_run:
            _write_tone(tone)
        steps.run(
            [str(python), "-m", "demucs", "-d", "cuda", "--two-stems", "vocals", "-n", "htdemucs", "-o", str(out), str(tone)],
            what="demucs on the GPU", capture=True,
        )  # fmt: skip
        if not steps.dry_run and not any(out.rglob("vocals.wav")):
            raise SetupError("demucs on the GPU wrote no vocal stem")
    return info


def _mark_ready(env: Path, gpu: dict[str, Any], info: dict[str, Any], model: str) -> None:
    ready = {
        "schema": runtime.SCHEMA,
        "created": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "gpu": gpu["name"],
        "driver": gpu["driver"],
        "memory_mb": gpu["memory_mb"],
        "torch": info.get("torch"),
        "faster_whisper": info.get("faster_whisper"),
        "ctranslate2": info.get("ctranslate2"),
        "cublas": info.get("cublas"),
        "model": model,
        "demucs": True,
        "whisper": True,
    }
    (env / runtime.READY_FILE).write_text(json.dumps(ready, indent=2) + "\n", encoding="utf-8")


def build(env: Path, *, torch: str | None, extra: list[str], model: str, check_only: bool, dry_run: bool) -> int:
    steps = _Steps(dry_run)
    gpu = find_gpu()
    if gpu is None and not dry_run:
        steps.show("No NVIDIA GPU found (nvidia-smi is missing or reports none); nothing to set up.")
        return 2
    gpu = gpu or {"name": "(dry run)", "driver": "-", "memory_mb": 0}
    if shutil.which("uv") is None and not dry_run:
        steps.show("uv is required (https://docs.astral.sh/uv/) and was not found on PATH.")
        return 2
    steps.show(f"GPU: {gpu['name']}, driver {gpu['driver']}, {gpu['memory_mb']} MiB")
    steps.show(f"Environment: {env}")
    marker = env / runtime.READY_FILE
    if not dry_run:
        marker.unlink(missing_ok=True)  # a failed run must not leave the old "ready" behind
    try:
        if not check_only:
            if not runtime.python_exe(env).is_file() or dry_run:
                steps.show("Creating the environment")
                if not dry_run:
                    env.parent.mkdir(parents=True, exist_ok=True)
                steps.run(create_command(env), what="creating the environment")
            steps.show("Installing (a few gigabytes on the first run)")
            steps.run(install_command(env, torch, extra), what="installing packages")
        elif not runtime.python_exe(env).is_file():
            raise SetupError(f"no environment at {env}; run without --check to build it")
        info = _test(steps, env, model)
        if not dry_run:
            _mark_ready(env, gpu, info, model)
    except SetupError as exc:
        steps.show(f"FAILED: {exc}")
        return 1
    if dry_run:
        steps.show("Dry run: nothing changed.")
    else:
        steps.show("Ready: film runs will use the GPU (CINE_GPU=off turns that off).")
    return 0


def remove(env: Path) -> int:
    if not env.exists():
        print(f"Nothing at {env}.")
        return 0
    if not (env / "pyvenv.cfg").is_file():
        print(f"{env} is not a virtual environment; refusing to delete it.")
        return 1
    shutil.rmtree(env)
    print(f"Removed {env}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--env", type=Path, default=None, help="environment folder (default: see CINE_GPU_ENV)")
    parser.add_argument("--check", action="store_true", help="test the existing environment only")
    parser.add_argument("--remove", action="store_true", help="delete the environment")
    parser.add_argument("--dry-run", action="store_true", help="print the commands, change nothing")
    parser.add_argument("--torch", default=None, help="torch requirement to use instead of the automatic CUDA pick")
    parser.add_argument("--extra", action="append", default=[], help="another package to install (repeatable)")
    parser.add_argument("--model", default=MODEL, help="Whisper model to test with")
    args = parser.parse_args(argv)
    env = args.env or runtime.env_dir()
    if args.remove:
        return remove(env)
    return build(env, torch=args.torch, extra=args.extra, model=args.model, check_only=args.check, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
