"""Built-in tool: load a skill's instructions by name."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agentnexus.spec.types import SkillSpec
from agentnexus.tools.base import Tool, ToolContext
from agentnexus.tools.builtins._arguments import parse_json_object_arguments


# Legacy platform skill names remain readable until 2.0; advertise only new names.
LEGACY_SKILL_NAMES = {
    "build-omnigent": "build-agentnexus",
    "omnigent-knowledge": "agentnexus-knowledge",
}


class LoadSkillTool(Tool):
    """
    Built-in tool that loads a skill's full instructions by name.

    Looks up the skill from bundled skills (in the agent spec)
    and host-scope skills (``.claude/skills/``, ``.agents/skills/``,
    ``~/.claude/skills/``, ``~/.agents/skills/``). Returns the
    skill content with an optional resource file listing appended.

    :param skills: The agent's bundled skill list.
    :param agent_root: Path to the agent's working directory,
        used to discover host-scope skills. ``None`` skips
        host-scope discovery.
    :param skills_filter: The agent spec's ``skills_filter``
        value (``"all"``/``"none"``/list). Controls which
        host-scope skills are included.
    """

    def __init__(
        self,
        skills: list[SkillSpec],
        agent_root: Path | None = None,
        skills_filter: str | list[str] = "all",
    ) -> None:
        """
        Initialize with bundled + host-scope skills.

        :param skills: Parsed skills from the agent spec.
        :param agent_root: Agent working directory for host
            skill discovery.
        :param skills_filter: Host-scope skill filter from
            the agent spec.
        """
        all_skills = list(skills)
        # Discover host-scope skills. Use agent_root when provided,
        # but fall back to cwd — in production the server process
        # runs from the user's project, so cwd finds .claude/skills/
        # even when agent_root is a cache dir.
        discovery_root = agent_root or Path.cwd()
        from agentnexus.spec.parser import discover_host_skills

        bundled_names = {s.name for s in skills}
        for hs in discover_host_skills(discovery_root, skills_filter):
            if hs.name not in bundled_names:
                all_skills.append(hs)
        self._skills = all_skills
        self._skills_by_name: dict[str, SkillSpec] = {s.name: s for s in all_skills}

    @property
    def skills(self) -> list[SkillSpec]:
        """All discovered skills (bundled + host-scope)."""
        return self._skills

    @classmethod
    def name(cls) -> str:
        """
        :returns: ``"load_skill"``.
        """
        return "load_skill"

    @classmethod
    def description(cls) -> str:
        """
        :returns: Human-readable description of the tool.
        """
        return "Load a skill's full instructions by name."

    def get_schema(self) -> dict[str, Any]:
        """
        Return the OpenAI-format schema for ``load_skill``.

        The description includes the list of available skill
        names so the LLM knows what it can load.

        :returns: A tool schema dict.
        """
        skill_names = [s.name for s in self._skills]
        return {
            "type": "function",
            "function": {
                "name": "load_skill",
                "description": (
                    "Load a skill's full instructions by "
                    "name. Available skills: "
                    f"{', '.join(skill_names)}"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": ("The skill name to load"),
                        },
                    },
                    "required": ["name"],
                },
            },
        }

    def invoke(self, arguments: str, ctx: ToolContext) -> str:
        """
        Look up a skill by name and return its content.

        If the skill has bundled resource files, appends
        a listing of available files to the content.

        :param arguments: JSON with ``"name"`` key, e.g.
            ``'{"name": "code-review"}'``.
        :param ctx: Server-side execution context (unused by
            skill tools, required by the :class:`Tool` interface).
        :returns: The skill content string, or an error
            message if the skill is not found.
        """
        args, error = parse_json_object_arguments(arguments)
        if error is not None:
            return f"Error: {error}"
        assert args is not None

        skill_name = args.get("name")
        if skill_name is None or skill_name == "":
            return "Error: missing required 'name' argument"
        if not isinstance(skill_name, str):
            return "Error: 'name' must be a string"
        skill = self._skills_by_name.get(skill_name) or self._skills_by_name.get(
            LEGACY_SKILL_NAMES.get(skill_name, skill_name)
        )
        if skill is None:
            available = list(self._skills_by_name.keys())
            return f"Error: skill {skill_name!r} not found. Available skills: {available}"
        resources = list_skill_resources(skill)
        return format_skill_content(skill, resources)


def skill_resource_is_readable(skill: SkillSpec, rel_path: str) -> bool:
    """Keep a documentation-only skill's implementation out of resource reads."""
    if skill.resource_access == "all":
        return True
    if skill.skill_dir is None:
        return False
    root = skill.skill_dir.resolve()
    resolved = (root / rel_path.replace("\\", "/")).resolve()
    if not resolved.is_relative_to(root):
        return False
    relative = resolved.relative_to(root)
    if len(relative.parts) == 1:
        return relative.suffix.lower() == ".md"
    return relative.parts[0] in {"references", "examples"} and relative.suffix.lower() in {
        ".md",
        ".txt",
        ".json",
        ".yaml",
        ".yml",
    }


def list_skill_resources(skill: SkillSpec) -> list[str]:
    """
    List resource files in a skill's directory.

    Covers auxiliary files beside ``SKILL.md`` (a common layout for
    skills that split long guidance across sibling documents) plus
    everything under ``references/``, ``scripts/``, and ``assets/``.
    Returns relative paths suitable for ``read_skill_file``.

    :param skill: The skill to scan.
    :returns: List of relative path strings, e.g.
        ``["DEEPENING.md", "references/style-guide.md"]``. Root-level
        files come first, each group sorted. Empty if the skill has no
        ``skill_dir`` or no resource files.
    """
    if skill.skill_dir is None or not skill.skill_dir.is_dir():
        return []
    files: list[str] = []
    for fp in sorted(skill.skill_dir.iterdir()):
        # Dotfiles are editor/OS cruft, and SKILL.md is the skill itself.
        if fp.is_file() and fp.name != "SKILL.md" and not fp.name.startswith("."):
            files.append(fp.name)
    for subdir_name in ("references", "scripts", "assets"):
        subdir = skill.skill_dir / subdir_name
        if not subdir.is_dir():
            continue
        for fp in sorted(subdir.rglob("*")):
            if fp.is_file():
                rel = fp.relative_to(skill.skill_dir).as_posix()
                files.append(rel)
    return [path for path in files if skill_resource_is_readable(skill, path)]


def format_skill_content(
    skill: SkillSpec,
    resource_files: list[str],
) -> str:
    """
    Format the skill content for the LLM, appending a
    resource listing if the skill has bundled files.

    :param skill: The skill to format.
    :param resource_files: List of relative paths to
        bundled resource files, e.g.
        ``["references/style-guide.md"]``.
    :returns: The skill content, optionally followed by
        an ``## Available files`` section.
    """
    if not resource_files and (skill.skill_dir is None or skill.resource_access == "all"):
        return skill.content

    lines = [
        skill.content,
        "",
        f"Skill directory: {skill.skill_dir}",
        "Resolve bundled scripts and resources relative to this directory; "
        "no filesystem search is needed.",
        "",
    ]
    if skill.resource_access == "documentation":
        lines.extend(
            [
                "Packaged programs are execution entrypoints, not reading resources. "
                "Run the documented command directly; read only task-relevant guidance below.",
                "",
            ]
        )
    if resource_files:
        lines.extend(["## Available files", "Use read_skill_file only for task-relevant files:"])
    for path in resource_files:
        lines.append(f"- {path}")
    return "\n".join(lines)


def find_skill_by_name(skills: list[SkillSpec], name: str) -> SkillSpec | None:
    """
    Return the skill with the requested name.

    :param skills: Discovered skills for an agent (bundled + host),
        e.g. the merged list from :attr:`LoadSkillTool.skills`.
    :param name: Skill name to match exactly, e.g. ``"code-review"``.
    :returns: The matching :class:`SkillSpec`, or ``None`` when the
        command references a skill the agent does not expose.
    """
    for skill in skills:
        if skill.name == name:
            return skill
    canonical = LEGACY_SKILL_NAMES.get(name)
    if canonical:
        return next((skill for skill in skills if skill.name == canonical), None)
    return None


def format_skill_meta_text(skill: SkillSpec, arguments: str) -> str:
    """
    Build the hidden user-message text injected for a skill invocation.

    The format follows Codex's durable skill wrapper: the complete
    skill content is enclosed in ``<skill>`` so clients can identify
    it later, and slash-command arguments are appended in a separate
    ``<user_request>`` block so the agent sees the actual request.

    The embedded ``<path>`` and the resource listing are resolved
    against ``skill.skill_dir``, so this MUST run on the host where the
    harness executes (the runner) — the paths are read at runtime by
    the ``read_skill_file`` tool, which resolves relative to the same
    ``skill_dir``.

    :param skill: Skill being invoked, e.g. ``SkillSpec(name="grill-me",
        ...)``.
    :param arguments: Raw arguments typed after the slash command,
        e.g. ``"review this plan"``. Empty string when none.
    :returns: Hidden message text for a single ``input_text`` block.
    """
    resource_files = list_skill_resources(skill)
    content = format_skill_content(skill, resource_files)
    lines = ["<skill>", f"<name>{skill.name}</name>"]
    if skill.skill_dir is not None:
        lines.append(f"<path>{skill.skill_dir / 'SKILL.md'}</path>")
    lines.extend([content, "</skill>"])
    if arguments:
        lines.extend(["", "<user_request>", arguments, "</user_request>"])
    return "\n".join(lines)
