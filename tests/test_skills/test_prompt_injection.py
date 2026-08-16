"""系统提示词技能章节测试 — 环境信息之后注入一次，主 Agent 与 SubAgent 均可用。"""

import pytest

from src.skills import build_skills_prompt_section
from src.skills.registry import SkillRegistry
import src.skills.registry as registry_module


def _make_skill(root, name: str, description: str, **extra) -> None:
    d = root / name
    d.mkdir(parents=True, exist_ok=True)
    lines = ["---", f"name: {name}", f"description: {description}"]
    lines.extend(f"{k}: {v}" for k, v in extra.items())
    lines.append("---")
    lines.append(f"# {name} 正文")
    (d / "SKILL.md").write_text("\n".join(lines), encoding="utf-8")


@pytest.fixture
def project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    return proj


@pytest.fixture
def registry(project, monkeypatch):
    _make_skill(project / ".skills", "alpha", "技能 A")
    _make_skill(project / ".skills", "beta", "仅用户",
                **{"disable-model-invocation": "true"})
    registry = SkillRegistry()
    registry._cwd = str(project)
    return registry


def _set_auto_load(monkeypatch, names):
    rc = {"skills": {"auto_load": names}}
    monkeypatch.setattr(registry_module, "get_rc", lambda: rc)


# ── build_skills_prompt_section ─────────────────────────

def test_section_lists_model_invocable_skills(registry):
    section = build_skills_prompt_section(cwd=registry._cwd, registry=registry)
    assert "## 可用技能（Skills）" in section
    assert "- `alpha`: 技能 A" in section
    assert "- `beta`" not in section  # 非模型可调用不列出
    assert "skill" in section  # 使用说明


def test_section_includes_autoload_content(registry, monkeypatch):
    _set_auto_load(monkeypatch, ["alpha"])
    section = build_skills_prompt_section(cwd=registry._cwd, registry=registry)
    assert "### 已自动加载的技能" in section
    assert "<skill_content name=\"alpha\">" in section
    assert "# alpha 正文" in section


def test_section_empty_without_skills(registry, tmp_path):
    empty_proj = tmp_path / "empty-proj"
    (empty_proj / ".git").mkdir(parents=True)
    assert build_skills_prompt_section(cwd=str(empty_proj), registry=registry) == ""


def test_section_empty_when_disabled(project, registry, monkeypatch):
    _make_skill(project / ".skills", "alpha", "技能 A")
    monkeypatch.setattr(registry_module, "get_rc", lambda: {"skills": {"enabled": False}})
    assert build_skills_prompt_section(cwd=str(project), registry=registry) == ""


def test_section_autoload_skips_non_model_invocable(project, registry, monkeypatch):
    _set_auto_load(monkeypatch, ["beta"])  # beta disable-model-invocation
    _make_skill(project / ".skills", "beta", "仅用户",
                **{"disable-model-invocation": "true"})
    section = build_skills_prompt_section(cwd=str(project), registry=registry)
    assert "已自动加载" not in section


# ── builder 注入（主 Agent 与 SubAgent 提示词） ─────────

def test_build_system_prompt_includes_skills_after_env_info(registry, monkeypatch):
    """主 Agent：技能章节注入，且位于环境信息之后。"""
    from src import prompt_builder as pb
    monkeypatch.setattr(pb.builder, "build_skills_prompt_section",
                        lambda cwd=None: "## 可用技能（Skills）\n- `alpha`: 技能 A")
    parts = pb.build_system_prompt(cwd=registry._cwd)
    text = "\n".join(parts)
    assert "## 可用技能（Skills）" in text
    env_idx = text.find("工作目录")
    skill_idx = text.find("## 可用技能")
    assert skill_idx > env_idx >= 0, "技能章节必须在环境信息之后"


def test_build_subagent_prompts_include_skills(registry, monkeypatch):
    """每个 SubAgent 提示词都注入技能章节。"""
    from src import prompt_builder as pb
    monkeypatch.setattr(pb.builder, "build_skills_prompt_section",
                        lambda cwd=None: "## 可用技能（Skills）\n- `alpha`: 技能 A")
    builders = [
        pb.build_subagent_system_prompt,
        pb.build_map_agent_system_prompt,
        pb.build_review_agent_system_prompt,
        pb.build_plan_agent_system_prompt,
        pb.build_execute_agent_system_prompt,
    ]
    for build in builders:
        parts = build(cwd=registry._cwd)
        assert any("## 可用技能（Skills）" in p for p in parts), build.__name__


def test_subagent_prompts_section_after_env_info(registry, monkeypatch):
    from src import prompt_builder as pb
    monkeypatch.setattr(pb.builder, "build_skills_prompt_section",
                        lambda cwd=None: "## 可用技能（Skills）")
    parts = pb.build_execute_agent_system_prompt(cwd=registry._cwd)
    text = "\n".join(parts)
    assert text.find("工作目录") < text.find("## 可用技能")


def test_build_system_prompt_no_section_without_skills(monkeypatch, tmp_path):
    from src import prompt_builder as pb
    monkeypatch.setattr(pb.builder, "build_skills_prompt_section",
                        lambda cwd=None, registry=None: "")
    proj = tmp_path / "proj"
    (proj / ".git").mkdir(parents=True)
    parts = pb.build_system_prompt(cwd=str(proj))
    assert not any("## 可用技能（Skills）" in p for p in parts)


# ── skill 工具 ──────────────────────────────────────────

def test_skill_tool_schema():
    from src.tools.skill_tool import SkillFunc
    schema = SkillFunc.to_tool_schema()
    fn = schema["function"]
    assert fn["name"] == "skill"
    assert "name" in fn["parameters"]["properties"]
    assert fn["parameters"]["required"] == ["name"]


async def test_skill_tool_execute(registry, monkeypatch):
    from src.tools import skill_tool as skill_tool_module
    from src.tools.skill_tool import SkillFunc
    monkeypatch.setattr(skill_tool_module, "default_registry", lambda: registry)
    monkeypatch.setattr(skill_tool_module.os, "getcwd", lambda: registry._cwd)

    tool = SkillFunc.from_args({"name": "alpha"})
    tool.agent = None
    result = await tool.execute()
    assert "<skill_content name=\"alpha\">" in result
    assert "# alpha 正文" in result

    tool = SkillFunc.from_args({"name": "beta"})
    result = await tool.execute()
    assert result.startswith("(技能")
    assert "不允许模型调用" in result

    tool = SkillFunc.from_args({"name": "missing"})
    result = await tool.execute()
    assert "不存在" in result


async def test_skill_tool_execute_missing_arg():
    from src.tools.skill_tool import SkillFunc
    tool = SkillFunc()
    result = await tool.execute()
    assert "缺失" in result


# ── 运行时注册技能可被目录列出 ─────────────────────────

def test_runtime_skill_in_catalog(registry):
    registry.register("runtime-demo", "运行时演示技能", "# 正文")
    section = build_skills_prompt_section(cwd=registry._cwd, registry=registry)
    assert "- `runtime-demo`" in section
    assert "- `beta`" not in section
