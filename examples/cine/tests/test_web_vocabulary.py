"""The web adaptation view labels storyboard and mapping values with local tables.

web/ is built without examples/, so it cannot import the skills' vocabulary;
these tests keep its copies equal to the definitions.
"""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import handoff

REPO = Path(__file__).resolve().parents[3]
WEB_LABELS = REPO / "web/src/lib/cineReview.ts"
STORYBOARD = REPO / "examples/cine/skills/cine-storyboard/scripts/cine-storyboard.mjs"
NODE = shutil.which("node")


def web_table(name):
    match = re.search(
        rf"export const {name}: Record<string, string> = \{{(.*?)\}};", WEB_LABELS.read_text("utf-8"), re.S
    )
    assert match, f"{name} not found in {WEB_LABELS}"
    return dict(re.findall(r'"?([\w-]+)"?: "([^"]+)"', match.group(1)))


@pytest.mark.skipif(NODE is None, reason="reading the storyboard vocabulary requires node")
def test_web_shot_sizes_match_the_storyboard():
    script = (
        f"const m = await import({json.dumps(STORYBOARD.as_uri())});"
        "console.log(JSON.stringify(Object.fromEntries("
        "Object.entries(m.SHOT_SIZES).map(([k, v]) => [k, v.zh]))));"
    )
    done = subprocess.run(
        [NODE, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=True,
    )
    assert web_table("SHOT_SIZE_LABELS") == json.loads(done.stdout)


def test_web_mapping_kinds_match_the_handoff():
    assert set(web_table("MAPPING_KIND_LABELS")) == set(handoff.MAPPING_KINDS)
