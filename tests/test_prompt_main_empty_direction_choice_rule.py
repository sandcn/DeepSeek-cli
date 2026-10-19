"""空模式提词：方向性选择必须多用 user_select，默认全选。

用户要求：提词中增加「有方向上的选择问题，多用 user_select 的让用户做选择，
默认全选」。
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


def test_direction_choice_rule_defaults_to_select_all():
    """默认多选并默认全选，非交互场景按全选回退。"""
    bullet = _decision_bullet()
    assert_contains(
        bullet,
        "默认多选",
        "multi_select=True",
        "default_options",
        "默认全选",
        "超时",
        "无响应",
        "非交互环境",
        "按全选回退",
    )


def test_direction_choice_rule_keeps_tradeoff_semantics():
    """原有「破坏性/需要取舍的问题交由用户选择」语义不得丢失。"""
    bullet = _decision_bullet()
    assert_contains(bullet, "破坏性", "需要取舍", "让用户做选择")
