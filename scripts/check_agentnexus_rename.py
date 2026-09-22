#!/usr/bin/env python3
"""Check rename-sensitive wiring without importing the app or changing user state.

Historical data keys and compatibility aliases are allowed. The checks compare
producers with consumers and files with their declared names instead of banning
every occurrence of the old brand. Run with Python 3.12+ from any directory.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8-sig")


def check() -> list[str]:
    problems: list[str] = []
    project = tomllib.loads(read("pyproject.toml"))["project"]
    package = project["name"].replace("-", "_")

    for command, entrypoint in project["scripts"].items():
        module = entrypoint.split(":", 1)[0]
        module_path = ROOT / module.replace(".", "/")
        if not module_path.with_suffix(".py").is_file():
            problems.append(f"console script {command}: missing module {module}")

    for path in (ROOT / package).rglob("*.py"):
        if "static" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8-sig"), filename=str(path))
        for node in ast.walk(tree):
            imported = []
            if isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                imported = [node.module]
            for module in imported:
                if module.split(".")[0] == "omnigent":
                    problems.append(f"{path.relative_to(ROOT)}:{node.lineno}: old package import")

    vite = read("web/vite.config.ts")
    out_dir = re.search(r'outDir:\s*path.resolve\(__dirname,\s*"([^"]+)"', vite)
    if not out_dir:
        problems.append("web/vite.config.ts: cannot resolve the SPA output directory")
    else:
        bundle = (ROOT / "web" / out_dir[1]).resolve().relative_to(ROOT).as_posix()
        for file in ("deploy/docker/Dockerfile", "deploy/docker/Dockerfile.ubi"):
            docker = read(file)
            if f"COPY {package}/ ./{package}/" not in docker:
                problems.append(f"{file}: source COPY differs from package {package}")
            if f"COPY --from=web-builder /web/{bundle} ./{bundle}" not in docker:
                problems.append(f"{file}: SPA COPY differs from Vite output {bundle}")

    workspace_names = {
        json.loads(read(path))["name"]
        for path in (
            "web/package.json",
            "editors/vscode/package.json",
            "web/electron/package.json",
        )
    }
    for line in read("justfile").splitlines():
        if "pnpm" not in line:
            continue
        for name in re.findall(r"--filter\s+([\w-]+)(?=\s|$)", line):
            if name not in workspace_names:
                problems.append(f"justfile: unknown workspace filter {name}")

    for path in (ROOT / "web/electron/src").glob("*.js"):
        source = path.read_text(encoding="utf-8-sig")
        for name in re.findall(r'require\(["\x27](\.[^"\x27]+)["\x27]\)', source):
            target = path.parent / name
            choices = [target, Path(str(target) + ".js"), Path(str(target) + ".json")]
            if not any(choice.is_file() for choice in choices):
                problems.append(f"{path.relative_to(ROOT)}: missing relative require {name}")

    for page, preload in (
        ("setup/index.html", "src/preload.js"),
        ("find/index.html", "src/find_preload.js"),
        ("return-banner/index.html", "src/return_banner_preload.js"),
    ):
        html = read(f"web/electron/{page}")
        bridge = read(f"web/electron/{preload}") + read("web/electron/src/url.js")
        for name in re.findall(r"window\.((?:agentnexus|omnigent)\w+)", html):
            if not re.search(rf'(?:["\x27]{name}["\x27]|\.{name}\s*=)', bridge):
                problems.append(f"web/electron/{page}: bridge {name} is not exposed")

    for path in (ROOT / package / "onboarding/agent/skills").glob("*/SKILL.md"):
        name = re.search(r"^name:\s*([\w-]+)\s*$", path.read_text(encoding="utf-8"), re.M)
        if name and name[1] != path.parent.name:
            problems.append(f"{path.relative_to(ROOT)}: skill name differs from its directory")

    desktop = json.loads(read("web/electron/package.json"))
    schemes = desktop["build"]["protocols"][0]["schemes"]
    if len(schemes) != len(set(schemes)):
        problems.append("desktop deep-link schemes contain duplicate entries")
    parser = read("web/electron/src/deepLink.js")
    for scheme in schemes:
        if f'"{scheme}:"' not in parser:
            problems.append(f"desktop registers {scheme} but does not parse that scheme")

    registry = read(f"{package}/harness_plugins.py")
    placeholders = set(re.findall(r"echo ([a-z-]+-bench-ok)", registry))
    driver = read("tests/harness_bench/native_tui_driver.py")
    for placeholder in placeholders:
        if f'"{placeholder}"' not in driver:
            problems.append(f"harness bench driver cannot substitute {placeholder}")
    return problems


def main() -> int:
    try:
        problems = check()
    except (OSError, ValueError, KeyError, SyntaxError) as exc:
        print(f"Rename wiring check could not complete: {exc}", file=sys.stderr)
        return 2
    if problems:
        print("AgentNexus rename wiring failed:")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(
        "AgentNexus rename wiring: PASS (imports, CLI, Docker, workspaces, desktop, skills, bench)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
