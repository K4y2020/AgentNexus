"""One table holds the production stages; every reader derives from it."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[1] / "skills/film-analysis"))
from pipeline import stage_graph
from pipeline.stage_graph import CANVAS, ORDER, STAGES

SKILLS = Path(__file__).parents[1] / "skills"


def test_every_stage_reads_only_documents_written_before_it():
    for position, (stage, spec) in enumerate(STAGES.items()):
        earlier = set(ORDER[:position])
        assert set(spec.validator_inputs) <= earlier, stage
        assert spec.seed_from is None or spec.seed_from in earlier, stage


def test_every_stage_names_a_skill_with_a_native_validator():
    for spec in STAGES.values():
        assert (SKILLS / spec.skill / "scripts" / f"{spec.skill}.mjs").is_file()


def test_stages_advance_in_order_and_end_on_the_canvas():
    assert [stage_graph.next_stage(stage) for stage in ORDER] == [*ORDER[1:], CANVAS]


@pytest.mark.parametrize("mode", ["original", "faithful", "adaptation"])
def test_provenance_only_pins_earlier_artifacts(mode):
    graph = stage_graph.provenance(mode)
    names = list(graph)
    for position, (name, inputs) in enumerate(graph.items()):
        assert set(inputs) <= set(names[:position]), name
    assert ("source_material" in graph) is (mode != "original")


def test_provenance_returns_a_fresh_graph():
    stage_graph.provenance("adaptation")["outline"] = ()
    assert stage_graph.PROVENANCE["outline"] == ("source_material",)


def test_validation_scope_follows_validator_inputs_transitively():
    assert stage_graph.validation_scope("cast") == ("cast",)
    assert stage_graph.validation_scope("art") == ("cast", "art")
    assert stage_graph.validation_scope("storyboard") == ORDER
