"""Native Cine draft seeding and validation. Never submits media generation."""

import argparse
import hashlib
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
STAGES = {
    "outline": "novel-outline",
    "cast": "novel-characters",
    "art": "novel-art",
    "script": "novel-script",
    "storyboard": "novel-storyboard",
}
INPUTS = {
    "outline": (),
    "cast": (),
    "art": ("cast",),
    "script": ("outline", "art", "cast"),
    "storyboard": ("script", "outline", "cast", "art"),
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path):
    return digest(path) if path.exists() or path.is_symlink() else None


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def stage_path(directory, stage, *, must_exist=True):
    """Resolve an explicit binding or one unambiguous legacy artifact."""
    directory = directory.resolve()
    manifest = directory / "production.json"
    if manifest.exists() or manifest.is_symlink():
        if not manifest.resolve().is_relative_to(directory):
            raise ValueError("production.json must stay inside the production directory")
        document = read(manifest)
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("production.json schema_version must be 1")
        artifacts = document.get("artifacts") if isinstance(document, dict) else None
        entry = artifacts.get(stage) if isinstance(artifacts, dict) else None
        relative = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(relative, str) or not relative.strip():
            raise ValueError(f"Missing explicit {stage} binding in production.json")
        if Path(relative).is_absolute():
            raise ValueError(f"{stage} binding must be a relative path")
        selected = (directory / relative).resolve()
        if not selected.is_relative_to(directory) or selected == manifest.resolve():
            raise ValueError(f"{stage} binding escapes or aliases production.json")
        if selected.exists() and not selected.is_file():
            raise ValueError(f"{stage} binding is not a file: {selected}")
        if must_exist and not selected.is_file():
            raise FileNotFoundError(f"Bound {stage} artifact is missing: {selected}")
        return selected
    canonical = directory / f"{stage}.json"
    candidates = sorted(
        [path for path in [canonical, *directory.glob(f"*-{stage}.json")] if path.exists()]
    )
    if any(
        not path.is_file() or not path.resolve().is_relative_to(directory) for path in candidates
    ):
        raise ValueError(f"Invalid or escaping {stage} artifact")
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        raise ValueError(
            f"Ambiguous {stage} artifacts: {[p.name for p in candidates]}; "
            "select artifacts.<stage>.path in production.json"
        )
    if not must_exist:
        return canonical
    raise FileNotFoundError(f"Missing {stage}.json or a single *-{stage}.json in {directory}")


def atomic_json(path, value):
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False, suffix=".tmp"
    ) as file:
        temp = Path(file.name)
        json.dump(value, file, ensure_ascii=False, indent=2)
    try:
        os.replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def script_path(stage):
    name = STAGES[stage]
    return SKILLS / name / "scripts" / f"{name}.mjs"


def shape_errors(stage, doc):
    """Catch contract drift early; semantic validation belongs to the native CLI."""
    if not isinstance(doc, dict):
        return ["root must be an object"]
    errors = []
    if not isinstance(doc.get("source"), str) or not doc["source"].strip():
        errors.append("source must be a non-empty string")
    required = {
        "outline": ("characters", "scenes", "beats", "episodes"),
        "cast": ("characters",),
        "art": ("scenes",),
        "script": ("episodes",),
        "storyboard": ("episodes",),
    }
    for key in required[stage]:
        if not isinstance(doc.get(key), list):
            errors.append(f"{key} must be an array (use native seed, not a custom schema)")
    return errors


def init_outline(directory, source, episodes, seconds, genre, adapt_mode):
    if (
        not source.strip()
        or not genre.strip()
        or episodes < 1
        or not math.isfinite(seconds)
        or seconds <= 0
    ):
        raise ValueError(
            "source, genre, positive episodes and finite positive seconds are required"
        )
    directory.mkdir(parents=True, exist_ok=True)
    path = stage_path(directory, "outline", must_exist=False)
    value = {
        "source": source,
        "lang": "zh",
        "params": {
            "episodes": episodes,
            "minutesPerEpisode": seconds / 60,
            "genre": genre,
            "adaptMode": adapt_mode,
            "preferences": [],
        },
        "adaptation": {"core": "", "keep": [], "cut": [], "merge": [], "risks": []},
        "characters": [],
        "scenes": [],
        "props": [],
        "beats": [],
        "episodes": [
            {
                "ep": ep,
                "synopsis": "",
                "hook": "",
                "suspense": "",
                "sceneIds": [],
                "characterIds": [],
                "propIds": [],
                "crowdPlan": "",
                "warnings": [],
            }
            for ep in range(1, episodes + 1)
        ],
    }
    # Exclusive creation prevents a retry from replacing a user's filled outline.
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
    return {"status": "draft_seeded", "path": str(path), "validated": False}


def seed(directory, stage, timeout=30, *, replace_empty=False):
    if stage == "outline":
        raise ValueError("use init for the initial outline")
    manifest = directory / "production.json"
    if not manifest.resolve().is_relative_to(directory.resolve()):
        raise ValueError("production.json must stay inside the production directory")
    binding_hash = fingerprint(manifest)
    target = stage_path(directory, stage, must_exist=False)
    replace_target = False
    if target.exists():
        existing = read(target)
        empty_storyboard = stage == "storyboard" and all(
            isinstance(ep, dict) and ep.get("segments") == []
            for ep in existing.get("episodes", [])
        )
        if not replace_empty or not empty_storyboard:
            raise FileExistsError(f"refusing to overwrite {target}")
        replace_target = True
    upstream = "script" if stage == "storyboard" else "outline"
    path = stage_path(directory, upstream)
    problems = shape_errors(upstream, read(path))
    if problems:
        raise ValueError("invalid upstream shape: " + "; ".join(problems))
    node = shutil.which("node")
    if not node:
        raise ValueError("node is unavailable; no substitute seeder was run")
    tool = script_path(stage)
    hashes = {str(p): fingerprint(p) for p in (path, tool, directory / "production.json")}
    hashes[str(manifest)] = binding_hash
    command = [node, str(tool), "seed", str(path)]
    result = subprocess.run(
        command,
        cwd=directory,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if result.returncode:
        raise ValueError(f"native seed exited {result.returncode}: {result.stderr}")
    document = json.loads(result.stdout)
    problems = shape_errors(stage, document)
    if problems:
        raise ValueError("invalid native seed output: " + "; ".join(problems))
    if any(fingerprint(Path(p)) != h for p, h in hashes.items()):
        raise ValueError("upstream changed during seeding; retry after edits settle")
    target.parent.mkdir(parents=True, exist_ok=True)
    if replace_target:
        target.unlink()
    with target.open("x", encoding="utf-8") as file:
        json.dump(document, file, ensure_ascii=False, indent=2)
    return {
        "status": "draft_seeded",
        "path": str(target),
        "validated": False,
        "command": command,
        "input_hashes": hashes,
    }


def check_stage(directory, stage, source_text=None, timeout=30):
    tool = script_path(stage)
    row = {
        "stage": stage,
        "status": "blocked",
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "command": [],
        "input_hashes": {},
        "skipped_checks": ["exact_source_quotes"]
        if stage == "cast" and source_text is None
        else [],
    }
    try:
        manifest = directory / "production.json"
        if not manifest.resolve().is_relative_to(directory.resolve()):
            raise ValueError("production.json must stay inside the production directory")
        binding_hash = fingerprint(manifest)
        paths = {key: stage_path(directory, key) for key in (stage, *INPUTS[stage])}
        files = [*paths.values(), directory / "production.json"]
        if stage == "cast" and source_text is not None:
            files.append(source_text)
        row["input_hashes"] = {str(p): fingerprint(p) for p in files}
        row["input_hashes"][str(manifest)] = binding_hash
        row["validator_sha256"] = digest(tool)
        problems = []
        for key in (stage, *INPUTS[stage]):
            problems.extend(f"{key}: {p}" for p in shape_errors(key, read(paths[key])))
        if problems:
            row.update(status="invalid_shape", errors=problems)
            return row
        node = shutil.which("node")
        if not node:
            row["stderr"] = "node is unavailable; no substitute validator was run"
            return row
        command = [node, str(tool), "validate", str(files[0])]
        if stage == "cast" and source_text is not None:
            command.append(str(source_text))
        for key in INPUTS[stage]:
            command.extend([f"--{key}", str(paths[key])])
        if stage == "storyboard":
            command.append("--no-log")
        row["command"] = command
        result = subprocess.run(
            command,
            cwd=directory,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        row.update(
            exit_code=result.returncode,
            stdout=result.stdout,
            stderr=result.stderr,
            status="passed" if result.returncode == 0 else "failed",
        )
        if (
            any(fingerprint(Path(p)) != h for p, h in row["input_hashes"].items())
            or digest(tool) != row["validator_sha256"]
        ):
            row["status"] = "inputs_changed"
    except subprocess.TimeoutExpired as exc:
        partial = exc.stdout or b""
        row.update(
            status="timeout",
            stderr=str(exc),
            stdout=partial.decode("utf-8", errors="replace")
            if isinstance(partial, bytes)
            else partial,
        )
    except (OSError, ValueError, TypeError) as exc:
        row.update(status="blocked", stderr=str(exc))
    return row


def check(directory, stage="all", source_text=None, timeout=30):
    report_dir = directory / ".cine-validation"
    report_dir.mkdir(exist_ok=True)
    if not report_dir.resolve().is_relative_to(directory.resolve()):
        raise ValueError("validation report directory must stay inside the production directory")
    stages = list(STAGES) if stage == "all" else [stage]
    rows = [check_stage(directory, s, source_text, timeout) for s in stages]
    for row in rows:
        if row["status"] == "passed":
            try:
                if any(fingerprint(Path(path)) != h for path, h in row["input_hashes"].items()):
                    row["status"] = "inputs_changed"
            except OSError:
                row["status"] = "inputs_changed"
    passed = all(r["status"] == "passed" for r in rows)
    skipped = any(r["skipped_checks"] for r in rows)
    report = {
        "schema_version": 1,
        "run_id": uuid.uuid4().hex,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "status": "native_validated"
        if passed and not skipped
        else "incomplete"
        if passed
        else "failed",
        "stages": rows,
        "production_authorized": False,
        "unverified": [
            "source_readiness",
            "upstream_pins",
            "adaptation_quality",
            "visual_quality",
            "provider_compatibility",
            "generated_media",
        ],
    }
    target = report_dir / f"{report['run_id']}.json"
    atomic_json(target, report)
    # This is a convenience pointer, not a cached authorization decision.
    atomic_json(report_dir / "latest.json", report)
    return {**report, "report_path": str(target)}


def finalize(directory, source_text=None, timeout=30):
    """Revalidate native artifacts, refresh the manifest graph, then hand off."""
    report = check(directory, "all", source_text, timeout)
    if report["status"] != "native_validated":
        raise ValueError("native validation must pass before finalizing production pins")
    manifest_path = directory / "production.json"
    manifest = read(manifest_path)
    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, dict):
        raise ValueError("production.json artifacts are required")
    dependencies = {
        "source_material": (),
        "outline": ("source_material",),
        "cast": ("outline",),
        "art": ("outline",),
        "script": ("outline", "cast", "art"),
        "storyboard": ("script", "cast", "art"),
        "mapping": ("source_material", "script", "storyboard"),
    }
    if manifest.get("mode") == "original":
        dependencies.pop("source_material")
        dependencies["outline"] = ()
        dependencies["mapping"] = ("script", "storyboard")
    if set(artifacts) != set(dependencies):
        raise ValueError("production.json artifact set does not match its mode")
    hashes = {}
    for name, inputs in dependencies.items():
        relative = artifacts[name].get("path")
        if not isinstance(relative, str) or Path(relative).is_absolute():
            raise ValueError(f"invalid artifact path: {name}")
        path = (directory / relative).resolve(strict=True)
        if not path.is_relative_to(directory.resolve()):
            raise ValueError(f"artifact escapes production directory: {name}")
        hashes[name] = digest(path)
        artifacts[name]["sha256"] = hashes[name]
        artifacts[name]["inputs"] = {upstream: hashes[upstream] for upstream in inputs}
    atomic_json(manifest_path, manifest)
    try:
        from .handoff import validate
    except ImportError:  # Direct CLI execution from the pipeline directory.
        from handoff import validate

    result = validate(manifest_path)
    return {"status": "production_finalized", "native_report": report, "handoff": result}


def main(argv=None):
    # Windows inherits a legacy console code page even though every artifact
    # and child validator is UTF-8. Keep the CLI contract machine-readable.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="action", required=True)
    init = sub.add_parser("init")
    init.add_argument("directory", type=Path)
    init.add_argument("--source", required=True)
    init.add_argument("--episodes", type=int, required=True)
    init.add_argument("--seconds", type=float, required=True)
    init.add_argument("--genre", required=True)
    init.add_argument("--adapt-mode", choices=["忠实", "抽核", "借壳"], required=True)
    for action in ("seed", "check"):
        parser = sub.add_parser(action)
        parser.add_argument("directory", type=Path)
        parser.add_argument(
            "--stage",
            choices=[*STAGES, "all"] if action == "check" else list(STAGES),
            default="all" if action == "check" else None,
            required=action == "seed",
        )
        if action == "seed":
            parser.add_argument(
                "--replace-empty",
                action="store_true",
                help="Replace only an untouched storyboard seed with empty segments.",
            )
        if action == "check":
            parser.add_argument("--source-text", type=Path)
    final = sub.add_parser("finalize")
    final.add_argument("directory", type=Path)
    final.add_argument("--source-text", type=Path)
    args = p.parse_args(argv)
    try:
        directory = args.directory.resolve()
        if args.action == "init":
            result = init_outline(
                directory, args.source, args.episodes, args.seconds, args.genre, args.adapt_mode
            )
        elif args.action == "seed":
            result = seed(directory, args.stage, replace_empty=args.replace_empty)
        elif args.action == "check":
            result = check(
                directory, args.stage, args.source_text.resolve() if args.source_text else None
            )
        else:
            result = finalize(directory, args.source_text.resolve() if args.source_text else None)
        print(json.dumps(result, ensure_ascii=False))
        return int(
            (args.action == "check" and result["status"] != "native_validated")
            or (args.action == "finalize" and result["status"] != "production_finalized")
        )
    except (OSError, ValueError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "blocked", "error": str(exc)}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
