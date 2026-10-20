"""空模式提词：方向性选择必须多用 user_select，默认项须可安全回退。

用户要求：提词中增加「有方向上的选择问题，多用 user_select 的让用户做选择」；
方向性选择默认全选，选项互斥时改用单选并以「推荐 / 同意继续执行」为默认，
超时 / 无响应 / 非交互一律按默认项回退并视为用户已同意。
"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, prompt_bullets


def _decision_bullet() -> str:
    """定位提词中唯一的决策条目（避免用全文关键词凑数）。"""
    bullets = [line for line in prompt_bullets() if line.startswith("- **决策")]
    assert len(bullets) == 1, f"应有且仅有一条决策规则，实际: {bullets}"
    return bullets[0]


def test_direction_choice_rule_requires_user_select():
    """方向性选择问题必须多用 user_select 让用户做选择。"""
    bullet = _decision_bullet()
    assert_contains(
        bullet,
        "方向上的选择问题",
        "必须多用 `user_select`",
        "让用户做选择",
        "不得替用户拍板",
    )


def test_direction_choice_rule_uses_safe_defaults():
    """方向性选择默认全选；选项互斥时改单选并以「推荐 / 同意继续执行」为默认。"""
    bullet = _decision_bullet()
    assert_contains(
        bullet,
        "方向性选择默认多选",
        "multi_select=True",
        "default_options",
        "一律设为全部选项（默认全选，不因方案互斥而少勾）",
        "选项互斥时改用单选",
        "置为「推荐 / 同意继续执行」一侧（默认同意）",
        "超时",
        "无响应",
        "非交互环境",
        "即视为用户已同意全部方向，直接执行，不得因等待而中止",
    )


def test_direction_choice_rule_keeps_tradeoff_semantics():
    """原有「破坏性/需要取舍的问题交由用户选择」语义不得丢失。"""
    bullet = _decision_bullet()
    assert_contains(bullet, "破坏性", "需要取舍", "让用户做选择")
