"""Speech recognition on the GPU, run by gpu_runtime.py inside the GPU environment.

It talks only through two JSON files (request in, result out), so nothing depends on
the console code page, and it imports nothing from the pipeline: the GPU environment
has faster-whisper and the NVIDIA libraries, not the rest of Cine's dependencies.

    python gpu_whisper_worker.py REQUEST.json RESULT.json
    python gpu_whisper_worker.py --selftest [MODEL]
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _cuda_library_dirs() -> list[Path]:
    """Folders of the pip-installed NVIDIA runtime libraries (cuBLAS, CUDA runtime)."""
    import importlib.util

    spec = importlib.util.find_spec("nvidia")
    roots = list(spec.submodule_search_locations or []) if spec else []
    leaf = "bin" if os.name == "nt" else "lib"
    return [d for root in roots for d in sorted(Path(root).glob(f"*/{leaf}")) if d.is_dir()]


def prepare_cuda() -> None:
    """Make cuBLAS findable, and turn a missing library into an error, not a dialog."""
    if os.name == "nt":
        import ctypes

        # Without this, Windows opens a modal "DLL not found" box and the process hangs.
        ctypes.windll.kernel32.SetErrorMode(0x8001)
    folders = _cuda_library_dirs()
    if os.name == "nt":
        for folder in folders:
            os.add_dll_directory(str(folder))
        os.environ["PATH"] = os.pathsep.join([*map(str, folders), os.environ.get("PATH", "")])
        return
    import ctypes

    for folder in folders:
        for library in sorted(folder.glob("lib*.so*")):
            try:
                ctypes.CDLL(str(library), mode=ctypes.RTLD_GLOBAL)
            except OSError:
                continue


def _engine(model: str):
    from faster_whisper import WhisperModel

    try:
        # The model is normally cached already; skip the network round trips then.
        return WhisperModel(model, device="cuda", compute_type="float16", local_files_only=True)
    except Exception:  # noqa: BLE001 - not cached yet (or a path): fetch it
        return WhisperModel(model, device="cuda", compute_type="float16")


def transcribe(request: dict) -> dict:
    engine = _engine(request["model"])
    options = {"language": request.get("language"), "vad_filter": True, "word_timestamps": True}
    if request.get("initial_prompt"):
        options["initial_prompt"] = request["initial_prompt"]
    segments, info = engine.transcribe(request["audio"], **options)
    rows = [
        {
            "start": segment.start,
            "end": segment.end,
            "text": segment.text,
            "words": [
                {"word": w.word, "start": w.start, "end": w.end} for w in (segment.words or [])
            ],
        }
        for segment in segments
    ]
    return {
        "language": getattr(info, "language", None),
        "duration": getattr(info, "duration", None),
        "segments": rows,
    }


def selftest(model: str) -> None:
    """Load the model on the card and decode noise, so cuBLAS and the kernels really run."""
    import numpy as np

    engine = _engine(model)
    noise = (np.random.default_rng(0).standard_normal(16000 * 4) * 0.05).astype("float32")
    segments, _ = engine.transcribe(noise, language="zh", vad_filter=False)
    list(segments)
    print("ok")


def main(argv: list[str]) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    prepare_cuda()
    if argv[:1] == ["--selftest"]:
        selftest(argv[1] if len(argv) > 1 else "large-v3-turbo")
        return 0
    request = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    Path(argv[1]).write_text(
        json.dumps(transcribe(request), ensure_ascii=False), encoding="utf-8"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
