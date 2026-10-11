"""Load the film-analysis pipeline modules that a runner-side Cine gate needs.

The pipeline ships in the agent bundle, not in agentnexus. Each pipeline
directory is imported as its own package named after a digest of its files, so
sibling imports resolve without touching sys.path and an updated bundle never
reuses modules loaded from an older one.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import sys
import threading
from importlib.machinery import ModuleSpec
from pathlib import Path
from types import ModuleType
from typing import Any

SKILL = "film-analysis"
PIPELINE = Path(SKILL) / "pipeline"
# Pipeline modules core may run; everything else stays in the Topic.
CORE_MODULES = frozenset(
    {"production", "source_identity", "draft_check", "generation", "transcribe", "media_assembly"}
)

_lock = threading.Lock()


class SkillModuleUnavailable(ValueError):
    """The registered bundle has no loadable pipeline module of that name."""


def film_analysis_skills_dir(agent_spec: Any) -> Path | None:
    """The skills directory of the agent's registered film-analysis skill, if any."""
    for skill in getattr(agent_spec, "skills", None) or []:
        if skill.name == SKILL and skill.skill_dir is not None:
            return Path(skill.skill_dir).parent
    return None


def pipeline_module(skills_dir: str | Path | None, name: str) -> ModuleType:
    """Import ``name`` from the bundle's film-analysis pipeline.

    ``skills_dir`` must come from the registered agent spec, never from tool
    arguments: the module runs in this process.
    """
    if skills_dir is None:
        raise SkillModuleUnavailable("no film-analysis skill is registered")
    if name not in CORE_MODULES:
        raise SkillModuleUnavailable(f"core does not load pipeline module {name!r}")
    directory = (Path(skills_dir) / PIPELINE).resolve()
    if not (directory / f"{name}.py").is_file():
        raise SkillModuleUnavailable(f"{name}.py is missing from {directory}")
    try:
        return importlib.import_module(f"{_package(directory)}.{name}")
    except (ImportError, SyntaxError, OSError) as exc:
        raise SkillModuleUnavailable(f"{name}: {exc}") from exc


def _package(directory: Path) -> str:
    """Register ``directory`` as a package keyed by its content and return its name."""
    digest = hashlib.sha256(str(directory).encode("utf-8"))
    for path in sorted(directory.glob("*.py")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    name = f"_cine_pipeline_{digest.hexdigest()[:16]}"
    with _lock:
        if name not in sys.modules:
            # The package's own __init__ is never run: it imports the whole
            # indexer, which the gate does not need.
            spec = ModuleSpec(name, None, is_package=True)
            spec.submodule_search_locations = [str(directory)]
            sys.modules[name] = importlib.util.module_from_spec(spec)
    return name
