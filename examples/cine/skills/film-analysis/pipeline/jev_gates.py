"""Stage-level TypeSafe JEV gates for Cine production artifacts.

Jev is text-only.  This module therefore judges compact, structured creative
state and never claims to have inspected images, audio, or video.  Native
validators remain responsible for schema and deterministic contract checks.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable


STAGE_GATES: dict[str, dict[str, Any]] = {
    "outline": {
        "questions": {
            "hook_visible": ("noul", "Does the outline establish a concrete, visible or audible hook early enough to motivate continued viewing?"),
            "causal_progression": ("noul", "Does each major beat cause the next turn rather than merely listing events?"),
            "story_readiness": ("score", ["Not ready for downstream writing", "Partially ready; one or more story decisions remain vague", "Ready for characters, art, and script production"]),
        },
        "rules": {"hook_visible": 0.65, "causal_progression": 0.65, "story_readiness": 1.0},
    },
    "cast": {
        "questions": {
            "role_distinctness": ("score", ["Roles are interchangeable", "Roles are usable but overlap", "Each principal role has a distinct dramatic function and behavior"]),
            "state_assets_explicit": ("noul", "Are non-default character states explicit enough for downstream scene and storyboard continuity?"),
            "production_readiness": ("noul", "Can the cast document guide a production team without inventing missing identity or behavior details?"),
        },
        "rules": {"role_distinctness": 1.0, "state_assets_explicit": 0.65, "production_readiness": 0.65},
    },
    "art": {
        "questions": {
            "scene_anchor_completeness": ("score", ["Locations are generic or unstable", "Locations have some anchors but need clarification", "Locations have distinct, reusable visual anchors and lighting states"]),
            "prop_function": ("noul", "Do important props have a clear dramatic function and state changes instead of being decorative lists?"),
            "production_readiness": ("noul", "Can the art document guide consistent scene and prop generation without relying on unstated details?"),
        },
        "rules": {"scene_anchor_completeness": 1.0, "prop_function": 0.65, "production_readiness": 0.65},
    },
    "script": {
        "questions": {
            "dialogue_naturalness": ("score", ["Dialogue is unnatural or semantically unclear", "Dialogue is usable but needs a focused pass", "Dialogue is speakable, specific, and preserves each character's intent"]),
            "causal_continuity": ("noul", "In `artifacts.script`, does every line and action react to the beat immediately before it, so a viewer can follow why each thing is said and done?"),
            "visual_actionability": ("noul", "Can the scenes be staged through visible actions, objects, sound, and playable behavior rather than author explanation?"),
        },
        "rules": {"dialogue_naturalness": 1.0, "causal_continuity": 0.65, "visual_actionability": 0.65},
    },
    "storyboard": {
        "questions": {
            "beat_coverage": ("noul", "Does the storyboard visibly cover the script beats in order without silently dropping essential story actions or dialogue?"),
            "cross_shot_continuity": ("score", ["Adjacent shots are spatially or physically contradictory", "Continuity is mostly usable but has review risks", "Axis, positions, eyelines, motion, props, and action carry are coherent"]),
            "generation_feasibility": ("noul", "Are the shots and generation segments achievable from the supplied prompts without requiring unstated story or asset inventions?"),
            "visual_asset_anchors_present": ("noul", "In `artifacts.cast` and `artifacts.art`, does every character and scene referenced by the storyboard cuts have explicit visual image specifications (model sheet and environment prompt) so downstream video cards have valid reference image anchors rather than generating drifting faces?"),
        },
        "rules": {"beat_coverage": 0.65, "cross_shot_continuity": 1.0, "generation_feasibility": 0.65, "visual_asset_anchors_present": 0.65},
    },
}

# Gates that compare the artifact against the original source material. They
# only run when a source is supplied: without it the model has nothing to
# compare against, and asking anyway produces a confident answer about nothing.
#
# These are deliberately pointed rather than holistic. A broad question
# ("does this preserve the story?") scores a script whose character causality
# has been scrambled at ~0.87, because the document is still internally
# coherent; naming the specific lines, speakers and relationships is what
# surfaces the inversion.
SOURCE_GATES: dict[str, dict[str, Any]] = {
    "script": {
        "questions": {
            "source_baseline_usable": (
                "noul",
                "In `source_material`, does each bracketed speaker label cover exactly one "
                "character's utterance, with no single labeled segment mashing together lines "
                "that must belong to different people?",
            ),
            "speaker_attribution_faithful": (
                "noul",
                "Comparing `source_material` with `artifacts.script`: does every line that exists in both "
                "keep the same speaker, and is no line reassigned to a different character?",
            ),
            "reaction_order_faithful": (
                "noul",
                "Comparing `source_material` with `artifacts.script`: for each exchange, does the script keep "
                "the source's order of who responds to whom, so a line still answers the same cue it "
                "answered in the source?",
            ),
            "setup_payoff_preserved": (
                "noul",
                "Comparing `source_material` with `artifacts.script`: are the setup lines that make a later "
                "line land still present or replaced by an equivalent bridge, rather than deleted?",
            ),
            "source_order_fidelity": (
                "score",
                [
                    "The script reorders or reassigns lines so the source's cause and effect no longer holds",
                    "The script keeps the source's sequence but drops or blurs key connectors",
                    "The script preserves the source's speaker, order, and cause-and-effect relationships",
                ],
            ),
        },
        "rules": {
            "source_baseline_usable": 0.65,
            "speaker_attribution_faithful": 0.65,
            "reaction_order_faithful": 0.65,
            "setup_payoff_preserved": 0.65,
            "source_order_fidelity": 1.0,
        },
        # A baseline whose own speaker labels are unreliable cannot support a
        # fidelity verdict: the script would be marked unfaithful for not
        # reproducing an error. Report it instead of scoring against it.
        "blocking": "source_baseline_usable",
    },
}

SUPERVISOR_QUESTIONS = {
    "route_decision": (
        "choice",
        {
            "advance": "The artifact is ready for the next production stage",
            "repair": "A bounded creative repair should happen before advancing",
            "human_review": "The evidence is ambiguous and needs a human decision",
            "blocked": "The production cannot continue with the supplied state",
        },
    ),
    "next_stage": (
        "choice",
        {
            "assets": "Create or revise cast and art assets",
            "script": "Create or revise the structured script",
            "storyboard": "Create or revise the storyboard",
            "canvas": "Synchronize the verified storyboard to Seedance",
            "stop": "Stop until an external decision or input is supplied",
        },
    ),
    "repair_scope": (
        "choice",
        {
            "artifact": "Repair only the current artifact",
            "upstream": "Repair an upstream dependency before continuing",
            "provider": "Resolve a provider or asset capability issue",
            "human_review": "Ask for a human creative decision",
        },
    ),
    "retry_allowed": ("noul", "Is one bounded correction and revalidation cycle appropriate?"),
}

NEXT_STAGES = {
    "outline": {"assets"},
    "cast": {"script"},
    "art": {"script"},
    "script": {"storyboard"},
    "storyboard": {"canvas"},
}


def _find_api_key() -> str:
    value = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if value:
        return value
    candidates = [
        Path.cwd() / ".env",
        Path.home() / ".agentnexus" / ".env",
        Path.home() / ".env",
        Path(__file__).resolve().parents[5] / ".env",
    ]
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            for line in candidate.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("TYPESAFE_API_KEY="):
                    value = line.split("=", 1)[1].strip().strip('"').strip("'")
                    if value:
                        return value
        except OSError:
            continue
    return ""


def _transport_module():
    try:
        from . import jev_transport  # type: ignore[attr-defined]
    except ImportError:
        import jev_transport  # type: ignore[no-redef]
    return jev_transport


def _jev_client(client_factory, key: str, *, proxy_aware: bool):
    """Open an SDK client, optionally overriding its ambient HTTP transport."""
    if not proxy_aware:
        return client_factory(key)
    try:
        return client_factory(key, http_client=_transport_module().open_http_client())
    except TypeError:
        # Custom/injected factories accept the api key positionally only.
        return client_factory(key)


def _compact(value: Any, *, depth: int = 0, max_string: int = 3000) -> Any:
    """Keep JEV state bounded while preserving native field names and order.

    The depth limit is deliberately generous: dialogue-and-action beats sit
    around six levels down (stage -> episode -> scene -> flow -> line), and
    truncating there silently deletes the very text a causality judgment needs.
    A total character budget, rather than the depth cutoff, is what keeps a
    pathological document from producing an unbounded request.

    ``max_string`` is the per-string cap. Field captions and prompt text can
    stay short, but a whole source transcript has to survive intact or the
    source-comparison gates are judging a fragment.
    """
    budget = _CharBudget(199_900)
    result = _compact_value(value, budget, max_string=max_string)
    if budget.truncated:
        if isinstance(result, dict):
            result["_compaction_truncated"] = True
        elif isinstance(result, list):
            result.append("[compaction truncated]")
    return result


class _CharBudget:
    """Shared per-request character allowance for one compaction pass."""

    def __init__(self, limit: int):
        self.remaining = limit
        self.truncated = False

    def reserve(self, size: int) -> bool:
        if size > self.remaining:
            return False
        self.remaining -= size
        return True

    def take(self, value: str) -> str | None:
        size = len(json.dumps(value, ensure_ascii=False))
        if self.reserve(size):
            return value
        self.truncated = True
        suffix = "..."
        if self.remaining < len(json.dumps(suffix)):
            return "" if self.reserve(2) else None
        low, high = 0, len(value)
        while low < high:
            middle = (low + high + 1) // 2
            if len(json.dumps(value[:middle] + suffix, ensure_ascii=False)) <= self.remaining:
                low = middle
            else:
                high = middle - 1
        shortened = value[:low] + suffix
        self.reserve(len(json.dumps(shortened, ensure_ascii=False)))
        return shortened


_BUDGET_EXHAUSTED = object()


def _compact_value(
    value: Any, budget: "_CharBudget", *, depth: int = 0, max_string: int = 3000
) -> Any:
    if depth >= 32:
        budget.truncated = True
        value = "[nested data omitted]"
    if isinstance(value, str):
        if len(value) > max_string:
            value = value[: max_string - 3] + "..."
        result = budget.take(value)
        return result if result is not None else _BUDGET_EXHAUSTED
    if isinstance(value, (int, float, bool)) or value is None:
        return value if budget.reserve(len(json.dumps(value))) else _BUDGET_EXHAUSTED
    if isinstance(value, list):
        if not budget.reserve(2):
            return _BUDGET_EXHAUSTED
        items = []
        for item in value[:80]:
            separator = 2 if items else 0
            if not budget.reserve(separator):
                break
            compacted = _compact_value(item, budget, depth=depth + 1, max_string=max_string)
            if compacted is _BUDGET_EXHAUSTED:
                budget.remaining += separator
                break
            items.append(compacted)
        omitted = len(value) - len(items)
        if omitted:
            budget.truncated = True
            separator = 2 if items else 0
            if budget.reserve(separator):
                marker = budget.take(f"[{omitted} additional items omitted]")
                if marker is None:
                    budget.remaining += separator
                else:
                    items.append(marker)
        return items
    if isinstance(value, dict):
        if not budget.reserve(2):
            return _BUDGET_EXHAUSTED
        result = {}
        for key, item in value.items():
            if len(result) >= 80:
                break
            key = str(key)
            prefix = (2 if result else 0) + len(json.dumps(key, ensure_ascii=False)) + 2
            if not budget.reserve(prefix):
                break
            compacted = _compact_value(item, budget, depth=depth + 1, max_string=max_string)
            if compacted is _BUDGET_EXHAUSTED:
                budget.remaining += prefix
                break
            result[key] = compacted
        omitted = len(value) - len(result)
        if omitted:
            budget.truncated = True
            key = "_omitted_fields"
            prefix = (2 if result else 0) + len(json.dumps(key)) + 2
            if budget.reserve(prefix):
                compacted = _compact_value(omitted, budget)
                if compacted is _BUDGET_EXHAUSTED:
                    budget.remaining += prefix
                else:
                    result[key] = compacted
        return result
    return _compact_value(str(value), budget, depth=depth, max_string=max_string)


def _question_objects(stage: str, *, include_source: bool = False):
    from typesafe_sdk import Choice, Noul, Score

    questions = {}
    gates = [STAGE_GATES[stage]]
    if include_source and stage in SOURCE_GATES:
        gates.append(SOURCE_GATES[stage])
    for gate in gates:
        for name, (kind, value) in gate["questions"].items():
            if kind == "noul":
                questions[name] = Noul(instructions=value)
            elif kind == "choice":
                questions[name] = Choice(instructions=value, criteria=value if isinstance(value, dict) else {})
            else:
                questions[name] = Score(instructions=value, criteria=value if isinstance(value, list) else [])
    for name, (kind, value) in SUPERVISOR_QUESTIONS.items():
        if kind == "noul":
            questions[name] = Noul(instructions=value)
        else:
            questions[name] = Choice(instructions=f"For the current {stage} stage, {name}:", criteria=value)
    return questions


def gate_rules(stage: str, *, include_source: bool = False) -> dict[str, float]:
    rules = dict(STAGE_GATES[stage]["rules"])
    if include_source and stage in SOURCE_GATES:
        rules.update(SOURCE_GATES[stage]["rules"])
    return rules


def _answer_value(answer: Any) -> tuple[float, float | None]:
    if hasattr(answer, "noul"):
        return float(answer.noul), None
    if hasattr(answer, "score"):
        return float(answer.score), float(answer.confidence)
    if hasattr(answer, "choice"):
        return 1.0, float(answer.confidence)
    raise ValueError("JEV returned an unsupported answer type")


def _answer_dump(answer: Any) -> dict[str, Any]:
    if hasattr(answer, "model_dump"):
        return answer.model_dump(mode="json")
    return {key: value for key, value in vars(answer).items() if not key.startswith("_")}


def build_state(
    stage: str, paths: dict[str, Path], *, source_text: Path | None = None
) -> tuple[dict[str, Any], dict[str, str]]:
    if stage not in STAGE_GATES:
        raise ValueError(f"unknown JEV production stage: {stage}")
    documents = {}
    hashes = {}
    for name, path in paths.items():
        resolved = path.resolve(strict=True)
        raw = resolved.read_bytes()
        hashes[name] = hashlib.sha256(raw).hexdigest()
        documents[name] = _compact(json.loads(raw.decode("utf-8-sig")))
    state: dict[str, Any] = {
        "stage": stage,
        "artifacts": documents,
        "artifact_sha256": hashes,
        "instruction": "Judge only the supplied structured text state. Do not infer unseen images, audio, or video.",
    }
    if source_text is not None:
        resolved = source_text.resolve(strict=True)
        raw = resolved.read_bytes()
        hashes["source_material"] = hashlib.sha256(raw).hexdigest()
        # The transcript is the comparison baseline, so it keeps far more of
        # its length than a caption or prompt field would.
        state["source_material"] = _compact(raw.decode("utf-8-sig"), max_string=60_000)
        state["source_material_note"] = (
            "`source_material` is the original material being adapted. Judge `artifacts` against it; "
            "`artifacts` holds the adaptation."
        )
    return state, hashes


def run_stage_gate(
    stage: str,
    paths: dict[str, Path],
    *,
    model: str = "jev-latest",
    api_key: str | None = None,
    client_factory: Callable[[str], Any] | None = None,
    source_text: Path | None = None,
) -> dict[str, Any]:
    """Run one deterministic semantic gate and return a durable receipt payload.

    When ``source_text`` is supplied and the stage has source-comparison gates,
    the original material enters the state and the artifact is judged against
    it. Without it the model can only judge internal coherence, which cannot
    distinguish a faithful adaptation from a confident invention.
    """
    include_source = source_text is not None and stage in SOURCE_GATES
    state, hashes = build_state(stage, paths, source_text=source_text if include_source else None)
    key = (api_key or _find_api_key()).strip()
    if not key:
        return {
            "status": "unavailable",
            "stage": stage,
            "model": model,
            "input_sha256": hashes,
            "source_compared": include_source,
            "error": "TYPESAFE_API_KEY is not configured",
        }
    try:
        if client_factory is None:
            from typesafe_sdk import TypeSafeClient

            client_factory = lambda token, **kwargs: TypeSafeClient(api_key=token, **kwargs)
        response = None
        last_error: Exception | None = None
        for proxy_aware in (True, False):
            try:
                with _jev_client(client_factory, key, proxy_aware=proxy_aware) as client:
                    response = client.system_one(
                        state=state,
                        questions=_question_objects(stage, include_source=include_source),
                        model=model,
                    )
                break
            except Exception as exc:
                if not _transport_module().is_transport_error(exc) or not proxy_aware:
                    raise
                last_error = exc
        if response is None:
            raise last_error or RuntimeError("no JEV transport available")
        answers = getattr(response, "answers", None)
        if not isinstance(answers, dict):
            raise ValueError("JEV response did not contain answers")
        rules = gate_rules(stage, include_source=include_source)
        expected = set(rules) | set(SUPERVISOR_QUESTIONS)
        if set(answers) != expected:
            raise ValueError(f"JEV answer set mismatch: expected {sorted(expected)}, got {sorted(answers)}")
        evaluations = {}
        failures = []
        low_confidence = []
        for name, threshold in rules.items():
            value, confidence = _answer_value(answers[name])
            passed = value >= threshold
            if not passed:
                failures.append(f"{name}={value:.3f} < {threshold:.3f}")
            if confidence is not None and confidence < 0.55:
                low_confidence.append(f"{name} confidence={confidence:.3f}")
            evaluations[name] = {
                "value": value,
                "confidence": confidence,
                "threshold": threshold,
                "passed": passed,
            }
        status = "failed" if failures else "needs_review" if low_confidence else "passed"
        # A blocking precondition that fails invalidates the comparison itself,
        # so report it as such rather than scoring the artifact against a
        # baseline that cannot support a verdict.
        blocking = SOURCE_GATES.get(stage, {}).get("blocking") if include_source else None
        if blocking and not evaluations.get(blocking, {}).get("passed", True):
            status = "baseline_unusable"
            evaluations = {blocking: evaluations[blocking]}
            failures = [f"{blocking}={evaluations[blocking]['value']:.3f} < {evaluations[blocking]['threshold']:.3f}"]
        route_decision = str(getattr(answers["route_decision"], "choice", "stop"))
        next_stage = str(getattr(answers["next_stage"], "choice", "stop"))
        repair_scope = str(getattr(answers["repair_scope"], "choice", "artifact"))
        retry_allowed = float(getattr(answers["retry_allowed"], "noul", 0.0)) >= 0.65
        route_reasons = []
        if status == "baseline_unusable":
            route_reasons.append("source_baseline_speaker_labels_unreliable")
            route_decision = "repair"
            next_stage = "stop"
            repair_scope = "upstream"
        if status != "passed" and route_decision == "advance":
            route_decision = "repair"
            route_reasons.append("controller_overrode_advance_after_gate_failure")
        if status == "passed" and route_decision != "advance":
            status = "needs_review"
            route_reasons.append("controller_route_requires_review_before_handoff")
        if status == "passed" and route_decision == "advance" and next_stage not in NEXT_STAGES[stage]:
            route_reasons.append(f"invalid_next_stage:{next_stage}")
            next_stage = sorted(NEXT_STAGES[stage])[0]
        if route_decision != "advance":
            next_stage = "stop" if route_decision in {"human_review", "blocked"} else next_stage
        scheduler = {
            "decision": route_decision,
            "next_stage": next_stage,
            "repair_scope": repair_scope,
            "retry_allowed": retry_allowed,
            "reasons": route_reasons,
            "next_action": (
                f"advance_to_{next_stage}" if route_decision == "advance" else f"{route_decision}_{repair_scope}"
            ),
        }
        return {
            "status": status,
            "stage": stage,
            "model": str(getattr(response, "model", model)),
            "input_sha256": hashes,
            "source_compared": include_source,
            "evaluations": evaluations,
            "answers": {name: _answer_dump(answer) for name, answer in answers.items()},
            "failures": failures,
            "low_confidence": low_confidence,
            "scheduler": scheduler,
        }
    except Exception as exc:  # turn provider/SDK errors into an explicit gate result
        return {
            "status": "error",
            "stage": stage,
            "model": model,
            "input_sha256": hashes,
            "source_compared": include_source,
            "error": f"{type(exc).__name__}: {exc}",
        }


def write_receipt(directory: Path, receipt: dict[str, Any]) -> Path:
    target_dir = directory / ".cine-validation" / "jev"
    target_dir.mkdir(parents=True, exist_ok=True)
    token = hashlib.sha256(
        json.dumps(receipt.get("input_sha256", {}), sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    target = target_dir / f"{receipt['stage']}-{token}.json"
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=target_dir, delete=False) as file:
        temp = Path(file.name)
        json.dump(receipt, file, ensure_ascii=False, indent=2)
    try:
        os.replace(temp, target)
    finally:
        temp.unlink(missing_ok=True)
    return target
