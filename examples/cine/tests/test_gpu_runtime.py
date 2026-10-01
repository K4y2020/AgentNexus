"""The optional GPU runtime: when it is used, and that every failure lands on the CPU path."""

import importlib.util
import json
import subprocess
import sys
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "skills/film-analysis"))
from pipeline import gpu_runtime, gpu_setup, gpu_whisper_worker, transcribe  # noqa: E402

OK = SimpleNamespace(returncode=0, stdout="", stderr=b"")


@pytest.fixture(autouse=True)
def _cpu_only(monkeypatch):
    """These tests never reach a real GPU environment, whichever machine runs them."""
    monkeypatch.setenv("CINE_GPU", "off")


def _ready_env(tmp_path, monkeypatch, **ready):
    """A GPU environment folder that setup would have marked ready."""
    env = tmp_path / "gpu-env"
    python = gpu_runtime.python_exe(env)
    python.parent.mkdir(parents=True)
    python.write_text("", encoding="utf-8")
    marker = {"schema": 1, "gpu": "Test GPU", "torch": "2.0+cu", "demucs": True, "whisper": True}
    (env / "ready.json").write_text(json.dumps({**marker, **ready}), encoding="utf-8")
    monkeypatch.setenv("CINE_GPU", "auto")
    monkeypatch.setenv("CINE_GPU_ENV", str(env))
    return env


# --- which environment counts as ready -------------------------------------------


def test_the_suite_runs_with_the_gpu_off():
    assert gpu_runtime.status() == {"state": "off"}


@pytest.mark.parametrize("value", ["off", "OFF", "0", "false", "no", "cpu"])
def test_an_off_switch_wins_over_a_ready_environment(tmp_path, monkeypatch, value):
    _ready_env(tmp_path, monkeypatch)
    monkeypatch.setenv("CINE_GPU", value)
    assert gpu_runtime.status()["state"] == "off"


def test_a_ready_environment_is_reported_with_its_python(tmp_path, monkeypatch):
    env = _ready_env(tmp_path, monkeypatch)
    state = gpu_runtime.status()
    assert state["state"] == "ready"
    assert state["python"] == str(gpu_runtime.python_exe(env))
    assert state["gpu"] == "Test GPU"


def test_a_machine_without_setup_is_simply_not_set_up(tmp_path, monkeypatch):
    monkeypatch.setenv("CINE_GPU", "auto")
    monkeypatch.setenv("CINE_GPU_ENV", str(tmp_path / "nothing-here"))
    assert gpu_runtime.status()["state"] == "not_set_up"


@pytest.mark.parametrize(
    "damage",
    ["garbage", "schema", "demucs", "whisper", "python"],
)
def test_a_damaged_environment_is_broken_not_ready(tmp_path, monkeypatch, damage):
    env = _ready_env(tmp_path, monkeypatch)
    marker = env / "ready.json"
    if damage == "garbage":
        marker.write_text("{not json", encoding="utf-8")
    elif damage == "schema":
        marker.write_text(json.dumps({"schema": 99, "demucs": True, "whisper": True}), encoding="utf-8")
    elif damage in ("demucs", "whisper"):
        data = json.loads(marker.read_text(encoding="utf-8"))
        data[damage] = False
        marker.write_text(json.dumps(data), encoding="utf-8")
    else:
        gpu_runtime.python_exe(env).unlink()
    state = gpu_runtime.status()
    assert state["state"] == "broken"
    assert state["detail"]


# --- running the jobs ----------------------------------------------------------------


def _record(monkeypatch, outcome=OK):
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(subprocess, "run", run)
    return calls


def test_separation_runs_demucs_on_cuda_in_the_gpu_environment(tmp_path, monkeypatch):
    env = _ready_env(tmp_path, monkeypatch)
    calls = _record(monkeypatch)
    gpu_runtime.separate_vocals(tmp_path / "audio.wav", tmp_path / "sep")
    argv, kwargs = calls[0]
    assert argv == [
        str(gpu_runtime.python_exe(env)), "-m", "demucs", "-d", "cuda", "--two-stems", "vocals",
        "-n", "htdemucs", "-o", str(tmp_path / "sep"), str(tmp_path / "audio.wav"),
    ]  # fmt: skip
    assert kwargs["timeout"] == gpu_runtime.SEPARATE_TIMEOUT
    assert kwargs["stdin"] == subprocess.DEVNULL
    assert kwargs["env"]["PYTHONUTF8"] == "1"


def test_a_failed_job_says_what_the_process_reported(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    failed = SimpleNamespace(returncode=1, stdout=b"", stderr="RuntimeError: CUDA out of memory\n".encode())
    _record(monkeypatch, failed)
    with pytest.raises(gpu_runtime.GpuUnavailable, match="out of memory"):
        gpu_runtime.separate_vocals(tmp_path / "a.wav", tmp_path / "sep")


def test_a_hung_job_is_cut_off_at_the_timeout(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    _record(monkeypatch, subprocess.TimeoutExpired(["x"], 5))
    with pytest.raises(gpu_runtime.GpuUnavailable, match="timed out"):
        gpu_runtime.separate_vocals(tmp_path / "a.wav", tmp_path / "sep")


def test_a_job_that_cannot_start_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    _record(monkeypatch, FileNotFoundError("python.exe"))
    with pytest.raises(gpu_runtime.GpuUnavailable, match="could not start"):
        gpu_runtime.separate_vocals(tmp_path / "a.wav", tmp_path / "sep")


def test_a_step_without_a_ready_environment_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("CINE_GPU", "auto")
    monkeypatch.setenv("CINE_GPU_ENV", str(tmp_path / "nothing-here"))
    with pytest.raises(gpu_runtime.GpuUnavailable, match="not_set_up"):
        gpu_runtime.transcribe(tmp_path / "a.wav", model="m", language="zh")


def _worker_writes(result: dict):
    """A stand-in for the GPU process: reads the request file, writes the result file."""
    seen = {}

    def run(argv, **_kwargs):
        request, out = Path(argv[-2]), Path(argv[-1])
        seen["argv"] = argv
        seen["request"] = json.loads(request.read_text(encoding="utf-8"))
        out.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return OK

    return run, seen


def test_speech_recognition_round_trips_chinese_through_the_files(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    words = [{"word": "你好", "start": 0.5, "end": 1.0}, {"word": "，世界", "start": 1.0, "end": 1.6}]
    payload = {
        "language": "zh",
        "duration": 12.5,
        "segments": [{"start": 0.5, "end": 1.6, "text": "你好，世界", "words": words}],
    }
    run, seen = _worker_writes(payload)
    monkeypatch.setattr(subprocess, "run", run)

    segments, info = gpu_runtime.transcribe(
        tmp_path / "vocals.wav", model="large-v3-turbo", language="zh", initial_prompt="人物关系：母女"
    )

    assert seen["request"] == {
        "audio": str(tmp_path / "vocals.wav"),
        "model": "large-v3-turbo",
        "language": "zh",
        "initial_prompt": "人物关系：母女",
    }
    assert seen["argv"][1] == str(gpu_runtime._WORKER)
    assert (info.language, info.duration) == ("zh", 12.5)
    [segment] = segments
    assert (segment.start, segment.end, segment.text) == (0.5, 1.6, "你好，世界")
    assert [(w.word, w.start, w.end) for w in segment.words] == [("你好", 0.5, 1.0), ("，世界", 1.0, 1.6)]


@pytest.mark.parametrize("left", ["nothing", "garbage", "wrong shape"])
def test_a_result_that_is_missing_or_unusable_is_unavailable(tmp_path, monkeypatch, left):
    _ready_env(tmp_path, monkeypatch)

    def run(argv, **_kwargs):
        out = Path(argv[-1])
        if left == "garbage":
            out.write_text("{oops", encoding="utf-8")
        elif left == "wrong shape":
            out.write_text(json.dumps({"segments": [{"start": 0}]}), encoding="utf-8")
        return OK

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(gpu_runtime.GpuUnavailable, match="usable result"):
        gpu_runtime.transcribe(tmp_path / "a.wav", model="m", language="zh")


def test_two_jobs_never_hold_the_card_at_once(tmp_path, monkeypatch):
    env = _ready_env(tmp_path, monkeypatch)
    monkeypatch.setattr(gpu_runtime, "LOCK_TIMEOUT", 0.2)
    with gpu_runtime._exclusive(env):
        with pytest.raises(gpu_runtime.GpuUnavailable, match="busy"):
            with gpu_runtime._exclusive(env):
                pass
    with gpu_runtime._exclusive(env):  # released: the next job goes ahead
        pass


def test_a_lock_that_cannot_be_taken_is_unavailable_not_a_crash(tmp_path, monkeypatch):
    import filelock

    env = _ready_env(tmp_path, monkeypatch)

    def refuse(*_args, **_kwargs):
        raise PermissionError("read-only folder")

    monkeypatch.setattr(filelock.FileLock, "acquire", refuse)
    with pytest.raises(gpu_runtime.GpuUnavailable, match="cannot take the GPU lock"):
        with gpu_runtime._exclusive(env):
            pass


# --- the worker inside the GPU environment -------------------------------------------


def test_worker_finds_the_pip_installed_cublas_folder(tmp_path, monkeypatch):
    leaf = "bin" if sys.platform == "win32" else "lib"
    cublas = tmp_path / "site" / "nvidia" / "cublas" / leaf
    cublas.mkdir(parents=True)
    (tmp_path / "site" / "nvidia" / "cuda_runtime" / leaf).mkdir(parents=True)
    monkeypatch.syspath_prepend(str(tmp_path / "site"))
    monkeypatch.delitem(sys.modules, "nvidia", raising=False)
    importlib.invalidate_caches()
    found = gpu_whisper_worker._cuda_library_dirs()
    assert cublas in found
    assert len(found) == 2


class _FakeEngine:
    loads: list = []

    def __init__(self, model, **kwargs):
        if kwargs.get("local_files_only") and not self.cached:
            raise OSError("not in the local cache")
        _FakeEngine.loads.append((model, kwargs))

    cached = True

    def transcribe(self, audio, **options):
        _FakeEngine.options = (audio, options)
        word = SimpleNamespace(word="早安", start=0.0, end=0.8)
        segment = SimpleNamespace(start=0.0, end=0.8, text="早安", words=[word])
        return iter([segment]), SimpleNamespace(language="zh", duration=3.0)


@pytest.fixture
def fake_whisper(monkeypatch):
    _FakeEngine.loads = []
    _FakeEngine.cached = True
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_FakeEngine))
    monkeypatch.setattr(gpu_whisper_worker, "prepare_cuda", lambda: None)
    return _FakeEngine


def test_worker_decodes_on_cuda_with_the_cpu_runs_options(fake_whisper):
    out = gpu_whisper_worker.transcribe(
        {"audio": "a.wav", "model": "large-v3-turbo", "language": "zh", "initial_prompt": "人名"}
    )
    model, kwargs = fake_whisper.loads[-1]
    assert (model, kwargs["device"], kwargs["compute_type"]) == ("large-v3-turbo", "cuda", "float16")
    assert fake_whisper.options == (
        "a.wav",
        {"language": "zh", "vad_filter": True, "word_timestamps": True, "initial_prompt": "人名"},
    )
    assert out["segments"][0]["words"] == [{"word": "早安", "start": 0.0, "end": 0.8}]
    assert (out["language"], out["duration"]) == ("zh", 3.0)


def test_worker_passes_no_prompt_unless_there_is_one(fake_whisper):
    gpu_whisper_worker.transcribe({"audio": "a.wav", "model": "m", "language": None})
    assert "initial_prompt" not in fake_whisper.options[1]
    assert fake_whisper.options[1]["language"] is None


def test_worker_fetches_a_model_that_is_not_cached_yet(fake_whisper):
    fake_whisper.cached = False
    # The first attempt insists on the local cache and fails; the second may download.
    fake_whisper.loads.clear()

    class Loader(fake_whisper):
        def __init__(self, model, **kwargs):
            if kwargs.get("local_files_only"):
                raise OSError("not in the local cache")
            fake_whisper.loads.append((model, kwargs))

    sys.modules["faster_whisper"] = SimpleNamespace(WhisperModel=Loader)
    gpu_whisper_worker._engine("large-v3-turbo")
    assert "local_files_only" not in fake_whisper.loads[-1][1]


def test_worker_main_writes_utf8_json(tmp_path, fake_whisper):
    request, result = tmp_path / "request.json", tmp_path / "result.json"
    request.write_text(json.dumps({"audio": "a.wav", "model": "m", "language": "zh"}), encoding="utf-8")
    assert gpu_whisper_worker.main([str(request), str(result)]) == 0
    assert json.loads(result.read_text(encoding="utf-8"))["segments"][0]["text"] == "早安"


# --- transcribe(): GPU first, CPU whenever the GPU cannot ------------------------------


class _CpuWhisper:
    used = 0

    def __init__(self, *_args, **kwargs):
        assert kwargs["device"] == "cpu"

    def transcribe(self, audio, **_kwargs):
        _CpuWhisper.used += 1
        word = SimpleNamespace(word="cpu 的话", start=0.0, end=1.0)
        segment = SimpleNamespace(start=0.0, end=1.0, text="cpu 的话", words=[word])
        return [segment], SimpleNamespace(language="zh")


def _cpu_world(monkeypatch, tmp_path):
    """Fake ffmpeg, CPU demucs and CPU Whisper; report which commands ran."""
    ran = []
    _CpuWhisper.used = 0
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_CpuWhisper))

    def run(argv, **_kwargs):
        ran.append(argv[0])
        if argv[0] == "uv":
            out = Path(argv[argv.index("-o") + 1])
            vocals = out / "htdemucs" / "audio" / "vocals.wav"
            vocals.parent.mkdir(parents=True, exist_ok=True)
            vocals.write_text("cpu stem", encoding="utf-8")
        return OK

    monkeypatch.setattr(subprocess, "run", run)
    return ran


def _film(tmp_path):
    media = tmp_path / "inputs" / "source.mp4"
    media.parent.mkdir(exist_ok=True)
    media.write_bytes(b"a film")
    return media


def _transcribe(tmp_path, media):
    return transcribe.transcribe(
        media, tmp_path / "inputs" / "source-transcript", tmp_path,
        model="small", language="zh", separate_vocals=True,
    )  # fmt: skip


def _gpu_world(monkeypatch):
    """GPU functions that succeed: a stem on disk and one recognised line."""
    calls = []

    def separate(audio, out_dir):
        calls.append("separate")
        stem = Path(out_dir) / "htdemucs" / "audio" / "vocals.wav"
        stem.parent.mkdir(parents=True, exist_ok=True)
        stem.write_text("gpu stem", encoding="utf-8")

    def recognise(audio, **kwargs):
        calls.append(("recognise", Path(audio).read_text(encoding="utf-8"), kwargs["model"]))
        word = SimpleNamespace(word="gpu 的话", start=0.0, end=1.0)
        segment = SimpleNamespace(start=0.0, end=1.0, text="gpu 的话", words=[word])
        return [segment], SimpleNamespace(language="zh", duration=1.0)

    monkeypatch.setattr(gpu_runtime, "separate_vocals", separate)
    monkeypatch.setattr(gpu_runtime, "transcribe", recognise)
    return calls


def test_a_ready_gpu_does_both_steps_and_the_cpu_does_neither(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    ran = _cpu_world(monkeypatch, tmp_path)
    calls = _gpu_world(monkeypatch)

    result = _transcribe(tmp_path, _film(tmp_path))

    assert result["acceleration"] == {"separation": "gpu", "asr": "gpu", "fallbacks": []}
    assert ran == ["ffmpeg"]  # audio extraction only: no CPU demucs
    assert _CpuWhisper.used == 0
    assert calls[1] == ("recognise", "gpu stem", "small")  # the GPU heard the GPU stem
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert [row["text"] for row in saved["segments"]] == ["gpu 的话"]


def test_a_failing_gpu_is_redone_on_the_cpu_and_says_why(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    ran = _cpu_world(monkeypatch, tmp_path)

    def separate(audio, out_dir):
        half = Path(out_dir) / "htdemucs" / "audio" / "vocals.wav"
        half.parent.mkdir(parents=True, exist_ok=True)
        half.write_text("half written", encoding="utf-8")
        raise gpu_runtime.GpuUnavailable("vocal separation failed: CUDA out of memory")

    def recognise(*_args, **_kwargs):
        raise gpu_runtime.GpuUnavailable("speech recognition timed out after 900 s")

    monkeypatch.setattr(gpu_runtime, "separate_vocals", separate)
    monkeypatch.setattr(gpu_runtime, "transcribe", recognise)

    result = _transcribe(tmp_path, _film(tmp_path))

    accel = result["acceleration"]
    assert (accel["separation"], accel["asr"]) == ("cpu", "cpu")
    assert len(accel["fallbacks"]) == 2
    assert "out of memory" in accel["fallbacks"][0]
    assert "timed out" in accel["fallbacks"][1]
    assert ran == ["ffmpeg", "uv"]  # the CPU demucs ran, and replaced the half-written stem
    stem = tmp_path / "inputs/separated"
    assert [p.read_text(encoding="utf-8") for p in stem.rglob("vocals.wav")] == ["cpu stem"]
    saved = json.loads((tmp_path / "inputs/source-transcript.json").read_text(encoding="utf-8"))
    assert [row["text"] for row in saved["segments"]] == ["cpu 的话"]


@pytest.mark.parametrize("state", ["off", "not_set_up"])
def test_no_gpu_means_the_cpu_path_without_any_noise(tmp_path, monkeypatch, state):
    _cpu_world(monkeypatch, tmp_path)
    calls = _gpu_world(monkeypatch)
    if state == "not_set_up":
        monkeypatch.setenv("CINE_GPU", "auto")
        monkeypatch.setenv("CINE_GPU_ENV", str(tmp_path / "nothing-here"))
    result = _transcribe(tmp_path, _film(tmp_path))
    assert result["acceleration"] == {"separation": "cpu", "asr": "cpu", "fallbacks": []}
    assert calls == []


def test_a_broken_environment_is_reported_once_and_the_cpu_takes_over(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch, whisper=False)
    _cpu_world(monkeypatch, tmp_path)
    calls = _gpu_world(monkeypatch)
    result = _transcribe(tmp_path, _film(tmp_path))
    accel = result["acceleration"]
    assert (accel["separation"], accel["asr"]) == ("cpu", "cpu")
    assert len(accel["fallbacks"]) == 1
    assert "gpu_setup.py --check" in accel["fallbacks"][0]
    assert calls == []


def test_a_stem_from_an_earlier_run_is_reported_as_cached(tmp_path, monkeypatch):
    _ready_env(tmp_path, monkeypatch)
    _cpu_world(monkeypatch, tmp_path)
    _gpu_world(monkeypatch)
    media = _film(tmp_path)
    _transcribe(tmp_path, media)
    calls = _gpu_world(monkeypatch)
    second = _transcribe(tmp_path, media)
    assert second["acceleration"]["separation"] == "cached"
    assert second["acceleration"]["asr"] == "gpu"
    assert "separate" not in calls


def test_the_mcp_tool_hands_the_acceleration_report_to_the_agent(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "film_analysis_server", ROOT / "tools" / "mcp" / "film_analysis_server.py"
    )
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    saved = inputs / "source-transcript.json"
    saved.write_text(json.dumps({"segments": []}), encoding="utf-8")
    report = {"separation": "gpu", "asr": "gpu", "fallbacks": []}
    monkeypatch.setattr(
        transcribe,
        "transcribe",
        lambda *_a, **_k: {
            "status": "qualified_asr_complete",
            "json_path": str(saved),
            "acceleration": report,
        },
    )
    monkeypatch.setattr(server, "_opening_teaser", lambda _video, _plan=None: {"status": "none"})
    monkeypatch.setattr(server, "_attribute", lambda _payload, _inputs: {"status": "attributed"})

    outcome = server._transcribe(inputs / "source.mp4", tmp_path)

    assert outcome["acceleration"] == report


# --- gpu_setup.py ---------------------------------------------------------------------


def test_the_gpu_is_read_from_nvidia_smi():
    def smi(stdout, code=0):
        return lambda *_a, **_k: SimpleNamespace(returncode=code, stdout=stdout)

    assert gpu_setup.find_gpu(smi("NVIDIA GeForce RTX 3060 Ti, 591.86, 8192\n")) == {
        "name": "NVIDIA GeForce RTX 3060 Ti",
        "driver": "591.86",
        "memory_mb": 8192,
    }
    assert gpu_setup.find_gpu(smi("", code=9)) is None
    assert gpu_setup.find_gpu(smi("no devices\n")) is None
    assert gpu_setup.find_gpu(smi("a, b, lots\n")) is None

    def missing(*_a, **_k):
        raise FileNotFoundError("nvidia-smi")

    assert gpu_setup.find_gpu(missing) is None


def test_install_lets_uv_pick_the_cuda_build_unless_told_otherwise(tmp_path):
    env = tmp_path / "env"
    auto = gpu_setup.install_command(env, None, [])
    assert auto[:3] == ["uv", "pip", "install"]
    assert auto[auto.index("--python") + 1] == str(gpu_runtime.python_exe(env))
    assert auto[auto.index("--link-mode") + 1] == "copy"  # independent of uv's cache
    assert auto[auto.index("--torch-backend") + 1] == "auto"
    assert "torch" in auto
    assert {"demucs", "faster-whisper", "nvidia-cublas-cu12", "numpy<2"} <= set(auto)

    wheel = "torch @ https://example.test/torch-2.14.0+cu130-cp312-win_amd64.whl"
    pinned = gpu_setup.install_command(env, wheel, ["extra-pkg"])
    assert "--torch-backend" not in pinned
    assert wheel in pinned
    assert "torch" not in pinned
    assert pinned[-1] == "extra-pkg"


def test_the_test_tone_is_three_seconds_of_stereo(tmp_path):
    tone = tmp_path / "tone.wav"
    gpu_setup._write_tone(tone)
    with wave.open(str(tone), "rb") as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getnframes()) == (2, 44100, 3 * 44100)


def test_dry_run_prints_the_commands_and_changes_nothing(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gpu_setup, "find_gpu", lambda *_a: {"name": "Card", "driver": "1", "memory_mb": 8})
    env = tmp_path / "env"
    assert gpu_setup.main(["--env", str(env), "--dry-run"]) == 0
    printed = capsys.readouterr().out
    assert "uv venv" in printed
    assert "uv pip install" in printed
    assert "--selftest" in printed
    assert "demucs" in printed
    assert not env.exists()


def test_no_gpu_ends_setup_before_anything_is_touched(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(gpu_setup, "find_gpu", lambda *_a: None)
    assert gpu_setup.main(["--env", str(tmp_path / "env")]) == 2
    assert "No NVIDIA GPU" in capsys.readouterr().out
    assert not (tmp_path / "env").exists()


def _setup_world(monkeypatch, *, whisper_ok=True, cuda=True):
    """uv, torch, the worker and demucs, faked; returns the commands that ran."""
    ran = []
    monkeypatch.setattr(gpu_setup, "find_gpu", lambda *_a: {"name": "Card", "driver": "590", "memory_mb": 8192})
    monkeypatch.setattr(gpu_setup.shutil, "which", lambda _name: "uv")

    def run(argv, **_kwargs):
        ran.append(argv)
        if argv[:2] == ["uv", "venv"]:
            python = gpu_runtime.python_exe(Path(argv[-1]))
            python.parent.mkdir(parents=True, exist_ok=True)
            python.write_text("", encoding="utf-8")
        elif "-c" in argv:
            versions = {"torch": "2.14.0+cu130", "cuda": cuda, "device": "Card",
                        "faster_whisper": "1.2", "ctranslate2": "4.8", "cublas": "12.9"}  # fmt: skip
            return SimpleNamespace(returncode=0, stdout=json.dumps(versions) + "\n", stderr="")
        elif "--selftest" in argv and not whisper_ok:
            return SimpleNamespace(returncode=1, stdout="", stderr="cublas64_12.dll is not found")
        elif "--two-stems" in argv:
            out = Path(argv[argv.index("-o") + 1])
            (out / "htdemucs" / "tone").mkdir(parents=True)
            (out / "htdemucs" / "tone" / "vocals.wav").write_bytes(b"x")
        return SimpleNamespace(returncode=0, stdout="ok\n", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    return ran


def test_setup_marks_the_environment_ready_only_after_every_test_passes(tmp_path, monkeypatch):
    ran = _setup_world(monkeypatch)
    env = tmp_path / "env"
    assert gpu_setup.main(["--env", str(env)]) == 0
    ready = json.loads((env / "ready.json").read_text(encoding="utf-8"))
    assert (ready["demucs"], ready["whisper"], ready["torch"]) == (True, True, "2.14.0+cu130")
    assert ready["gpu"] == "Card"
    assert any(argv[:3] == ["uv", "pip", "install"] for argv in ran)
    monkeypatch.setenv("CINE_GPU", "auto")
    monkeypatch.setenv("CINE_GPU_ENV", str(env))
    assert gpu_runtime.status()["state"] == "ready"


def test_a_failed_test_leaves_no_ready_marker_and_removes_an_old_one(tmp_path, monkeypatch, capsys):
    _setup_world(monkeypatch, whisper_ok=False)
    env = tmp_path / "env"
    env.mkdir()
    (env / "ready.json").write_text('{"schema": 1, "demucs": true, "whisper": true}', encoding="utf-8")
    assert gpu_setup.main(["--env", str(env)]) == 1
    assert "FAILED" in capsys.readouterr().out
    assert not (env / "ready.json").exists()


def test_torch_without_cuda_is_refused_with_a_way_out(tmp_path, monkeypatch, capsys):
    _setup_world(monkeypatch, cuda=False)
    assert gpu_setup.main(["--env", str(tmp_path / "env")]) == 1
    out = capsys.readouterr().out
    assert "cannot see the GPU" in out
    assert "--torch" in out
    assert not (tmp_path / "env" / "ready.json").exists()


def test_check_needs_an_environment_and_installs_nothing(tmp_path, monkeypatch, capsys):
    ran = _setup_world(monkeypatch)
    assert gpu_setup.main(["--env", str(tmp_path / "env"), "--check"]) == 1
    assert "no environment" in capsys.readouterr().out
    assert not any(argv[:2] == ["uv", "pip"] for argv in ran)


def test_remove_only_deletes_something_that_is_a_virtualenv(tmp_path, capsys):
    not_ours = tmp_path / "precious"
    not_ours.mkdir()
    (not_ours / "notes.txt").write_text("keep", encoding="utf-8")
    assert gpu_setup.main(["--env", str(not_ours), "--remove"]) == 1
    assert (not_ours / "notes.txt").exists()

    ours = tmp_path / "ours"
    ours.mkdir()
    (ours / "pyvenv.cfg").write_text("home = x", encoding="utf-8")
    assert gpu_setup.main(["--env", str(ours), "--remove"]) == 0
    assert not ours.exists()
    assert gpu_setup.main(["--env", str(ours), "--remove"]) == 0  # already gone
