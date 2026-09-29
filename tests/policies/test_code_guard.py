"""The packaged-code-only policy allows skill commands and blocks ad-hoc code."""

from __future__ import annotations

import pytest

from agentnexus.policies.builtins.code_guard import packaged_code_only

SKILL = r"C:\Users\Kay\AppData\Local\Temp\runner-specs-x\ag-v22\skills\film-analysis"
policy = packaged_code_only()


def verdict(tool: str, **arguments: str) -> str:
    return policy({"type": "tool_call", "data": {"name": tool, "arguments": arguments}})["result"]


@pytest.mark.parametrize(
    "command",
    [
        f'python "{SKILL}\\pipeline\\production.py" next production',
        f'$skill = "{SKILL}"\npython "$skill/pipeline/production.py" next production',
        f"powershell -Command \"python '{SKILL}\\pipeline\\production.py' next production\"",
        f'uv run --no-project --with "scenedetect>=0.6.4" --with opencv-python-headless '
        f'python "{SKILL}\\media_project.py" --output p',
        f'node "{SKILL}\\..\\cine-storyboard\\scripts\\cine-storyboard.mjs" '
        "validate storyboard.json",
        f'python "{SKILL}\\pipeline\\production.py" init --help',
        "Get-ChildItem -Force; ffprobe -v error inputs/source.mp4",
        f'Get-Content "{SKILL}\\SKILL.md"',
        "python --version",
    ],
)
def test_packaged_commands_are_allowed(command: str) -> None:
    assert verdict("sys_os_shell", command=command) == "ALLOW"


@pytest.mark.parametrize(
    "command",
    [
        # The observed bypass: reading validator source, then running a generator.
        "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8; "
        "node -e \"const fs = require('fs'); console.log(fs.readFileSync("
        f"'{SKILL}/../cine-storyboard/scripts/cine-storyboard.mjs','utf8'))\"",
        "node temp/gen_sb.mjs; python production.py next production",
        'python -c "import json; print(1)"',
        "python - < fix.py",
        "python -m json.tool storyboard.json",
        "python temp\\repair.py",
        f'Get-Content "{SKILL}\\pipeline\\production.py"',
        "powershell -EncodedCommand ZQBjAGgAbwA=",
        "powershell -File temp\\fix.ps1",
        'cmd /c "node gen.js"',
        "echo print(1) > fix.py",
        "Set-Content -Path gen.mjs -Value 'x'",
    ],
)
def test_ad_hoc_code_is_denied(command: str) -> None:
    assert verdict("sys_os_shell", command=command) == "DENY"


def test_writing_code_files_is_denied_but_data_is_not() -> None:
    assert verdict("sys_os_write", path="temp/gen_sb.mjs", content="x") == "DENY"
    assert verdict("sys_os_edit", path="fix.py") == "DENY"
    assert verdict("sys_os_write", path="production/storyboard.json", content="{}") == "ALLOW"
    assert verdict("sys_os_write", path="production/notes.md", content="x") == "ALLOW"


def test_reading_packaged_source_is_denied_but_docs_are_not() -> None:
    assert verdict("sys_os_read", path=f"{SKILL}\\pipeline\\jev_gates.py") == "DENY"
    assert verdict("sys_os_read", path=f"{SKILL}\\SKILL.md") == "ALLOW"
    # Code the agent's own workspace holds (a tool's input) is not packaged source.
    assert verdict("sys_os_read", path="inputs/source-transcript.json") == "ALLOW"


def test_denial_explains_what_to_do_instead() -> None:
    result = policy(
        {"type": "tool_call", "data": {"name": "sys_os_write", "arguments": {"path": "a.py"}}}
    )
    assert "native JSON" in result["reason"]


def test_other_events_and_tools_pass() -> None:
    assert policy({"type": "request", "data": {}})["result"] == "ALLOW"
    assert verdict("seedance_read_canvas", action="models") == "ALLOW"
