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
    path = (root / value).resolve()
    if not path.is_relative_to(root):
        raise ProductionRejected("CINE_PRODUCTION_PATH_ESCAPE")
    if not path.exists():
        raise ProductionRejected("CINE_PRODUCTION_FILE_MISSING", {"path": str(path)})
    return path


def native_checker(skills_dir):
    # This path comes from the registered agent spec, never from tool arguments.
    if skills_dir is None:
        raise ProductionRejected("CINE_VALIDATOR_UNAVAILABLE")
    path = Path(skills_dir) / "film-analysis/pipeline/production.py"
    if not path.is_file():
        raise ProductionRejected("CINE_VALIDATOR_UNAVAILABLE")
    spec = importlib.util.spec_from_file_location("_cine_production_gate", path)
    if spec is None or spec.loader is None:
        raise ProductionRejected("CINE_VALIDATOR_UNAVAILABLE")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assert_current(proof):
    for path, expected in proof["input_hashes"].items():
        try:
            file = Path(path)
            actual = (
                hashlib.sha256(file.read_bytes()).hexdigest()
                if file.exists() or file.is_symlink()
                else None
            )
        except OSError as exc:
            raise ProductionRejected("CINE_INPUTS_CHANGED") from exc
        if actual != expected:
            raise ProductionRejected("CINE_INPUTS_CHANGED")


def storyboard_state_requirements(storyboard, cast, production_pointer):
    """Resolve non-default character states to their required V3 asset nodes."""
    if storyboard.get("stateContractVersion", 0) < 1:
        return []
    match = re.fullmatch(
        r"/episodes/(0|[1-9]\d*)/segments/(0|[1-9]\d*)/h3Prompt",
        production_pointer,
    )
    if not match:
        return []
    try:
        segment = storyboard["episodes"][int(match.group(1))]["segments"][int(match.group(2))]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProductionRejected("CINE_PROMPT_REFERENCE_INVALID") from exc
    characters = {row.get("id"): row for row in cast.get("characters", [])}
    required = {}
    for cut in segment.get("cuts", []):
        for character_id, state_id in (cut.get("characterStates") or {}).items():
            if state_id == "default":
                continue
            state = next(
                (
                    row
                    for row in characters.get(character_id, {}).get("states", [])
                    if row.get("id") == state_id
                ),
                None,
            )
            node_id = state.get("assetNodeId") if isinstance(state, dict) else None
            if not isinstance(node_id, str) or not node_id.strip():
                raise ProductionRejected(
                    "CINE_CHARACTER_STATE_ASSET_REQUIRED",
                    {"character": character_id, "state": state_id},
                )
            required[(character_id, state_id)] = node_id
    return [
        {"character": character, "state": state, "asset_node_id": node}
        for (character, state), node in required.items()
    ]


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
    if production_stage not in patterns:
        raise ProductionRejected("CINE_PRODUCTION_STAGE_REQUIRED")
    if generation_kind == "video" and production_stage != "storyboard":
        raise ProductionRejected("CINE_GENERATION_KIND_MISMATCH")
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
    # Scope checks to native dependencies; a character sheet does not need episode gates.
    stages = {
        "cast": ("cast",),
        "art": ("cast", "art"),
        "storyboard": ("outline", "cast", "art", "script", "storyboard"),
    }[production_stage]
    native = native_checker(skills_dir)
    try:
        paths = {stage: inside(root, str(native.stage_path(directory, stage))) for stage in stages}
    except (OSError, ValueError) as exc:
        if isinstance(exc, ProductionRejected):
            raise
        raise ProductionRejected(
            "CINE_PRODUCTION_ARTIFACT_REQUIRED", {"message": str(exc)}
        ) from exc
    artifact = paths[production_stage]
    before = hashlib.sha256(artifact.read_bytes()).hexdigest()
    try:
        document = json.loads(artifact.read_text(encoding="utf-8"))
        selected = document
        for token in production_pointer.split("/")[1:]:
            selected = selected[int(token)] if isinstance(selected, list) else selected[token]
    except (ValueError, KeyError, IndexError, TypeError) as exc:
        raise ProductionRejected("CINE_PROMPT_REFERENCE_INVALID") from exc
    if not isinstance(selected, str) or not selected.strip():
        raise ProductionRejected("CINE_PROMPT_REFERENCE_EMPTY")
    if prompt is not None and prompt != selected:
        raise ProductionRejected("CINE_PROMPT_MISMATCH")
    check = native.check
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
    if production_stage == "storyboard" and generation_kind == "video":
        proof["state_requirements"] = storyboard_state_requirements(
            document,
            json.loads(paths["cast"].read_text(encoding="utf-8")),
            production_pointer,
        )
    assert_current(proof)
    return proof
