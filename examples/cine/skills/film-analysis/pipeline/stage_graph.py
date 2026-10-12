"""The Cine production stages and the documents each one depends on.

Standard library only: the Topic pipeline imports it, and the runner's
submission gate loads it with the rest of the pipeline.
"""

from __future__ import annotations

from typing import NamedTuple


class Stage(NamedTuple):
    skill: str
    # Documents its native validator cross-checks, in CLI flag order.
    validator_inputs: tuple[str, ...] = ()
    # Document its native seed starts from.
    seed_from: str | None = None


# Execution order: every document a stage reads comes before it.
STAGES = {
    "outline": Stage("cine-outline"),
    "cast": Stage("cine-characters", seed_from="outline"),
    "art": Stage("cine-art", ("cast",), seed_from="outline"),
    "script": Stage("cine-script", ("outline", "art", "cast"), seed_from="outline"),
    "storyboard": Stage("cine-storyboard", ("script", "outline", "cast", "art"), seed_from="script"),
}
ORDER = tuple(STAGES)
CANVAS = "canvas"

# What each pinned artifact in production.json derives from, in pin order.
# Validator inputs differ on purpose: art reads the cast only so its prompts
# cannot name a character, but art is derived from the outline.
PROVENANCE = {
    "source_material": (),
    "outline": ("source_material",),
    "cast": ("outline",),
    "art": ("outline",),
    "script": ("outline", "cast", "art"),
    "storyboard": ("script", "cast", "art"),
    "mapping": ("source_material", "script", "storyboard"),
}


def provenance(mode: str) -> dict[str, tuple[str, ...]]:
    """The pin graph for a production mode; an original story has no source."""
    if mode != "original":
        return dict(PROVENANCE)
    return {
        name: tuple(item for item in inputs if item != "source_material")
        for name, inputs in PROVENANCE.items()
        if name != "source_material"
    }


def next_stage(stage: str) -> str:
    """The stage that follows ``stage``; the canvas follows the storyboard."""
    position = ORDER.index(stage) + 1
    return ORDER[position] if position < len(ORDER) else CANVAS


def validation_scope(stage: str) -> tuple[str, ...]:
    """``stage`` and every document its validators read, directly or not."""
    needed: set[str] = set()

    def include(name: str) -> None:
        if name not in needed:
            needed.add(name)
            for upstream in STAGES[name].validator_inputs:
                include(upstream)

    include(stage)
    return tuple(name for name in ORDER if name in needed)
