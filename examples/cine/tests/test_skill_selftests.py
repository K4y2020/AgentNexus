"""Run each cine skill's Node self-test so the native validators are covered in CI."""

import shutil
import subprocess
from pathlib import Path

import pytest

SKILLS = Path(__file__).resolve().parents[1] / "skills"
SELFTESTS = sorted(SKILLS.glob("*/scripts/selftest.mjs"))
NODE = shutil.which("node")


def test_every_native_skill_has_a_selftest():
    assert [path.parts[-3] for path in SELFTESTS] == [
        "cine-art",
        "cine-characters",
        "cine-outline",
        "cine-script",
        "cine-storyboard",
    ]


@pytest.mark.skipif(NODE is None, reason="skill self-tests require node")
@pytest.mark.parametrize("selftest", SELFTESTS, ids=[path.parts[-3] for path in SELFTESTS])
def test_skill_selftest_passes(selftest):
    result = subprocess.run(
        [NODE, str(selftest)],
        cwd=selftest.parent,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-2000:]
