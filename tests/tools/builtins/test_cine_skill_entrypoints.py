"""Cine's creative workflow lives in its skills and reaches the agent through load_skill."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from agentnexus.runtime.prompt import (
    build_instructions,
    build_instructions_nullable,
    raw_author_instructions,
)
from agentnexus.spec.parser import _parse_skill, parse
from agentnexus.tools.base import ToolContext
from agentnexus.tools.builtins.load_skill import LoadSkillTool, format_skill_meta_text
from agentnexus.tools.builtins.read_skill_file import ReadSkillFileTool

CINE = Path(__file__).resolve().parents[3] / "examples" / "cine"


def _instructions() -> str:
    return parse(CINE).instructions


def _core(name: str) -> str:
    """The mandatory creative process a skill returns through load_skill."""
    content = _parse_skill(CINE / "skills" / name / "SKILL.md").content
    return content.split("<!-- cine-core:start -->")[1].split("<!-- cine-core:end -->")[0].strip()


def test_cine_uses_gateway_neutral_harness_for_configurable_models():
    root = Path(__file__).resolve().parents[3] / "examples" / "cine"
    spec = parse(root)
    assert spec.executor.type == "agentnexus"
    assert spec.executor.config["harness"] == "openai-agents"


@pytest.mark.parametrize(
    "name",
    [
        "film-analysis",
        "cine-outline",
        "cine-script",
        "cine-storyboard",
        "cine-characters",
        "cine-art",
    ],
)
def test_cine_exposes_guidance_but_not_tool_implementations(name):
    root = Path(__file__).resolve().parents[3] / "examples/cine/skills"
    skill = _parse_skill(root / name / "SKILL.md")
    assert skill.resource_access == "documentation"
    ctx = ToolContext(task_id="", conversation_id="", agent_id="")
    load = LoadSkillTool([skill], skills_filter="none").invoke(json.dumps({"name": name}), ctx)
    listing = load.split("## Available files")[-1]
    assert ".mjs" not in listing and ".py" not in listing
    reader = ReadSkillFileTool([skill])
    path = (
        "references/production-handoff.md" if name == "film-analysis" else "references/schema.md"
    )
    assert not reader.invoke(json.dumps({"skill_name": name, "path": path}), ctx).startswith(
        "Error:"
    )
    code = "media_project.py" if name == "film-analysis" else f"scripts/{name}.mjs"
    assert "DOCUMENTATION_ONLY" in reader.invoke(
        json.dumps({"skill_name": name, "path": code}), ctx
    )


@pytest.mark.parametrize("name", ["cine-script", "cine-storyboard"])
@pytest.mark.parametrize("surface", ["load", "slash", "body_only"])
def test_cine_core_is_delivered_without_reference_reads(name, surface):
    root = Path(__file__).resolve().parents[3] / "examples" / "cine" / "skills"
    skill = _parse_skill(root / name / "SKILL.md")
    start = "<!-- cine-core:start -->"
    end = "<!-- cine-core:end -->"
    assert skill.content.count(start) == skill.content.count(end) == 1
    core = skill.content.split(start)[1].split(end)[0].strip()
    assert core.count("#### ") >= 5
    assert len(core) > 1000
    if surface == "slash":
        result = format_skill_meta_text(skill, "test request")
    else:
        if surface == "body_only":
            # No resources available: mandatory instructions must still survive.
            skill = replace(skill, skill_dir=None)
        tool = LoadSkillTool([skill], skills_filter="none")
        ctx = ToolContext(task_id="", conversation_id="", agent_id="")
        result = tool.invoke(json.dumps({"name": name}), ctx)
    assert core in result
    assert not core.startswith("Error:")


@pytest.mark.parametrize("name", ["cine-script", "cine-storyboard"])
@pytest.mark.parametrize("surface", ["responses", "nullable", "startup"])
def test_cine_prompt_routes_creative_stages_through_load_skill(name, surface):
    spec = parse(CINE)
    if surface == "responses":
        result = build_instructions(spec, None, [])
    elif surface == "nullable":
        result = build_instructions_nullable(spec, None, [])
    else:
        result = raw_author_instructions(spec)
    assert result is not None
    # The skill owns its creative process; the prompt points to it instead of a copy.
    assert "cine-preloaded" not in result
    assert _core(name) not in result
    assert "load_skill" in result and name in result


def test_storyboard_core_distinguishes_h3_reference_semantics():
    instructions = _core("cine-storyboard")
    assert "retention_analysis" in instructions
    assert "Ref2VA" in instructions
    assert "<Subject N>" in instructions
    assert "<Picture N>" in instructions
    assert "不写观众留存或开场钩子" in instructions
    assert "摄影机看得见不等于对手看得见" in instructions
    assert "[Shot 2] At 00:05.000" in instructions
    assert "范围替代原生切点标记" in instructions


def test_cine_keeps_budget_cuts_and_explicit_sync_separate():
    instructions = _instructions()
    for boundary in ("Estimates are budgets, not acting", "Repair only the stage that failed"):
        assert boundary in instructions
    assert "剧本不默认给动作分配 `start/end` 时间码" in _core("cine-script")
    storyboard = _core("cine-storyboard")
    for boundary in ("戏剧节拍、镜头、生成段不一一对应", "不能删除用户指定的踩点或口型要求"):
        assert boundary in storyboard


def test_cine_continuity_review_keeps_intentional_changes_and_ellipsis():
    instructions = _core("cine-script") + _core("cine-storyboard")
    for boundary in (
        "支撑反转的道具须有来源、持有者和必要交接",
        "普通取放可以合理省略",
        "机制未说明时标明待确认",
        "区分人物/道具身份与当前状态",
        "匹配剪辑只精确锁定承担连接作用",
        "没写秒表也可能过载",
        "区分确定断裂、合理省略、待确认意图和生成风险",
        "不默认删掉配角，也不撤掉全部人数约束",
        "不自动增加字幕、对白或铺垫来解释",
    ):
        assert boundary in instructions


def test_cine_evaluation_separates_story_from_generation_constraints():
    root = Path(__file__).resolve().parents[3]
    fixture = json.loads(
        (root / "tests/fixtures/cine_creative_eval.json").read_text(encoding="utf-8")
    )
    cases = {case["id"]: case for case in fixture["cases"]}
    assert len(cases) == len(fixture["cases"])
    assert all(case.get("stage") for case in cases.values())
    mixed = cases["comedy_prop_handoff_review"]
    story = cases["comedy_script_handoff_review"]
    downstream = cases["generation_cast_constraint_scope"]
    assert mixed["stage"] == "video_prompt_review"
    assert mixed["eligible_for_script_review_comparison"] is False
    assert story["stage"] == "script_review"
    assert story["eligible_for_script_review_comparison"] is True
    assert "无额外人物" not in story["prompt"]
    assert "老邻居" in story["prompt"]
    assert downstream["stage"] == "generation_constraint_adaptation"
    assert downstream["eligible_for_script_review_comparison"] is False


def test_cine_intake_requires_provenance_and_lazy_stage_loading():
    assert "Load only film-analysis during intake" in _instructions()
    film_analysis = _parse_skill(CINE / "skills" / "film-analysis" / "SKILL.md").content
    for boundary in (
        "source-transcript.txt",
        "dialogue_provenance",
        "Never create `inputs/source.txt`",
    ):
        assert boundary in film_analysis


def test_script_core_prioritizes_readability_and_blocks_timing_gate_evasion():
    instructions = _core("cine-script")
    for boundary in (
        "先交付一份能直接读的剧本",
        "陌生观众读本",
        "先拆人物，再写对白",
        "临时“场内说话卡”",
        "写完抽掉 `speaker` 名称复读主要台词",
        "有 `cast.json` 时必须读取实际出场角色",
        "中文对白口播复查",
        "口语化是受约束的最小改写",
        "事实命题、对象、肯否定、条件、时间、语气功能和利害关系",
        "不能冒充对白润色",
        "如果一句话必须依赖括号里的 `delivery` 才像人话",
        "口语自然不等于堆",
        "演员张嘴说得出",
        "不能冒充动作或 `delivery`",
        "换个角色也能说的主题金句",
        "紧跟可见动作又复述其含义的台词",
        "首稿尽量落在目标 ±5%",
        "禁止通过提高 `charsPerSecond`",
    ):
        assert boundary in instructions
