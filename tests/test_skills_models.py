"""技能模型与渲染测试 — 覆盖 src/skills/models.py 与 render.py。

验证技能名称校验、调用策略归一化与 <skill_content> 渲染。
"""

import pytest

from src.skills.models import (
    InvocationPolicy,
    is_model_invocable,
    is_skill_name,
    is_user_invocable,
    normalize_invocation,
)
from src.skills.render import _escape_attr, escape_text, render_skill_content
from src.skills.models import SkillDefinition


# ── is_skill_name ─────────────────────────────────────────

def test_is_skill_name_valid():
    assert is_skill_name("my-skill") is True
    assert is_skill_name("a") is True
    assert is_skill_name("foo-bar-baz") is True


def test_is_skill_name_invalid():
    assert is_skill_name("My Skill") is False
    assert is_skill_name("-leading") is False
    assert is_skill_name("trailing-") is False
    assert is_skill_name("") is False


# ── normalize_invocation ──────────────────────────────────

def test_normalize_invocation_none():
    policy = normalize_invocation(None)
    assert policy.model_invocable is True
    assert policy.user_invocable is True


def test_normalize_invocation_dict():
    policy = normalize_invocation({"model_invocable": False})
    assert policy.model_invocable is False
    assert policy.user_invocable is True


def test_normalize_invocation_passthrough():
    p = InvocationPolicy(model_invocable=False, user_invocable=False)
    assert normalize_invocation(p) is p


def test_normalize_invocation_invalid_type():
    with pytest.raises(TypeError):
        normalize_invocation("bad")


# ── is_model_invocable / is_user_invocable ────────────────

def test_is_model_invocable():
    p = InvocationPolicy(model_invocable=False)
    from src.skills.models import SkillSummary
    skill = SkillSummary(name="x", description="", invocation=p, source="", provider="")
    assert is_model_invocable(skill) is False
    assert is_user_invocable(skill) is True


# ── 转义 ──────────────────────────────────────────────────

def test_escape_attr():
    assert _escape_attr('a"b<c&') == "a&quot;b&lt;c&amp;"


def test_escape_text():
    assert escape_text("a<b>c&d") == "a&lt;b&gt;c&amp;d"


# ── render_skill_content ──────────────────────────────────

def test_render_skill_content():
    skill = SkillDefinition(
        name="my-skill",
        description="",
        invocation=InvocationPolicy(),
        source="",
        provider="test",
        content="do stuff",
    )
    result = render_skill_content(skill)
    assert '<skill_content name="my-skill">' in result
    assert "do stuff" in result
    assert "<skill_instructions>" in result
    assert "</skill_content>" in result
