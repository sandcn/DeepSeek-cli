"""空模式提词：提交与推送前必须由用户用 user_select 选择。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, prompt_bullets


def _git_bullet() -> str:
    """定位提词中唯一的 Git 规则条目（避免用全文关键词凑数）。"""
    bullets = [line for line in prompt_bullets() if line.startswith("- **Git**")]
    assert len(bullets) == 1, f"应有且仅有一条 Git 规则，实际: {bullets}"
    return bullets[0]


def test_git_rule_uses_user_select_before_commit_or_push():
    """Git 条目必须写明：commit / push 之前先用 user_select 让用户选择。"""
    bullet = _git_bullet()
    assert_contains(bullet, "必须", "user_select", "commit", "push", "前")


def test_git_rule_covers_scope_message_and_fallback():
    """用户选择需覆盖范围与提交信息；未授权时不得替用户提交。"""
    bullet = _git_bullet()
    assert_contains(bullet, "范围", "提交信息", "取消", "不得")
