"""Fail-closed native validation for AgentNexus's Seedance submission bridge."""

import asyncio
import hashlib
import importlib.util
import json
import re
from pathlib import Path


class ProductionRejected(ValueError):
    def __init__(self, code, detail=None):
        super().__init__(code)
        self.code = code
        self.detail = detail


def inside(root, value):
    if not isinstance(value, str) or not value.strip():
        raise ProductionRejected("CINE_PRODUCTION_PATH_REQUIRED")
    path = (root / value).resolve(strict=True)
    if not path.is_relative_to(root):
        raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE")
    return path


def native_checker(skills_dir):
    # This path comes from the registered agent spec, never from tool arguments.
    if skills_dir is None:
        raise ProductionRejected("CINE_VALIDATOR_UNAVAILABLE")
    path = Path(skills_dir) / "film-analysis/pipeline/production.py"
    if not path.is_file():
        raise ProductionRejected("CINE_VALIDATOR_UNAVAILABLE")
    spec = importlib.util.spec_from_file_location("_cine_production_gate", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def assert_current(proof):
    for path, expected in proof["input_hashes"].items():
        try:
            actual = hashlib.sha256(Path(path).read_bytes()).hexdigest()
        except OSError as exc:
            raise ProductionRejected("CINE_INPUTS_CHANGED") from exc
        if actual != expected:
            raise ProductionRejected("CINE_INPUTS_CHANGED")


async def validate_submission(
    server_client,
    conversation_id,
    *,
    skills_dir,
    production_dir,
    source_text,
    production_stage,
    production_pointer,
    generation_kind,
    prompt=None,
    project_id=None,
):
    if generation_kind not in ("image", "video"):
        raise ProductionRejected("CINE_GENERATION_KIND_INVALID")
    patterns = {
        "cast": r"/characters/(0|[1-9]\d*)/image/(sheet|prompt)",
        "art": r"/(scenes|props)/(0|[1-9]\d*)/image/(sheet|prompt)",
        "storyboard": (
            r"/episodes/(0|[1-9]\d*)/segments/(0|[1-9]\d*)/h3Prompt"
            if generation_kind == "video"
            else r"/episodes/(0|[1-9]\d*)/segments/(0|[1-9]\d*)/cuts/(0|[1-9]\d*)/frame"
        ),
    }
    if production_stage not in patterns or (
        generation_kind == "video" and production_stage != "storyboard"
    ):
        raise ProductionRejected("CINE_PRODUCTION_STAGE_REQUIRED")
    if not isinstance(production_pointer, str) or not re.fullmatch(
        patterns[production_stage], production_pointer
    ):
        raise ProductionRejected("CINE_PROMPT_REFERENCE_REQUIRED")
    response = await server_client.get(f"/v1/sessions/{conversation_id}", timeout=10)
    response.raise_for_status()
    session = response.json()
    if session.get("id") != conversation_id or not session.get("workspace"):
        raise ProductionRejected("CINE_SESSION_WORKSPACE_REQUIRED")
    bound_project = (session.get("labels") or {}).get("seedance.project_id")
    if project_id is not None and project_id != bound_project:
        raise ProductionRejected("CINE_PROJECT_BINDING_MISMATCH")
    root = Path(session["workspace"]).resolve(strict=True)
    directory = inside(root, production_dir)
    text_path = inside(root, source_text) if source_text else None
    if not directory.is_dir():
        raise ProductionRejected("CINE_PRODUCTION_DIRECTORY_REQUIRED")
    # Canonical filenames only; referenced inputs may not escape via symlinks.
    stages = {
        "cast": ("outline", "cast"),
        "art": ("outline", "cast", "art"),
        "storyboard": ("outline", "cast", "art", "script", "storyboard"),
    }[production_stage]
    for stage in stages:
        inside(root, str(directory / f"{stage}.json"))
    artifact = directory / f"{production_stage}.json"
    before = hashlib.sha256(artifact.read_bytes()).hexdigest()
    try:
        selected = json.loads(artifact.read_text(encoding="utf-8"))
        for token in production_pointer.split("/")[1:]:
            selected = selected[int(token)] if isinstance(selected, list) else selected[token]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProductionRejected("CINE_PROMPT_REFERENCE_INVALID") from exc
    if not isinstance(selected, str) or not selected.strip():
        raise ProductionRejected("CINE_PROMPT_REFERENCE_EMPTY")
    if prompt is not None and prompt != selected:
        raise ProductionRejected("CINE_PROMPT_MISMATCH")
    check = native_checker(skills_dir)
    reports = []
    for stage in stages:
        report = await asyncio.to_thread(check, directory, stage, text_path)
        reports.append(report)
        if report.get("status") != "native_validated":
            raise ProductionRejected("CINE_NATIVE_VALIDATION_FAILED", reports)
    hashes = {}
    for report in reports:
        for row in report["stages"]:
            for path, value in row["input_hashes"].items():
                if path in hashes and hashes[path] != value:
                    raise ProductionRejected("CINE_INPUTS_CHANGED")
                hashes[path] = value
            hashes[row["command"][1]] = row["validator_sha256"]
    if hashes.get(str(artifact)) != before:
        raise ProductionRejected("CINE_INPUTS_CHANGED")
    proof = {
        "prompt": selected,
        "input_hashes": hashes,
        "report_paths": [r["report_path"] for r in reports],
        "production_stage": production_stage,
        "production_pointer": production_pointer,
    }
    assert_current(proof)
    return proof
