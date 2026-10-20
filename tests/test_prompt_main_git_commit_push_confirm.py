"""空模式提词：提交与推送前必须由用户用 user_select 选择。"""

from __future__ import annotations

from tests._prompt_semantics import assert_contains, read_prompt


def _git_bullet() -> str:
    """定位提词中唯一的 Git 规则条目及其全部子项（避免用全文关键词凑数）。"""
    lines = read_prompt().splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith("- **Git**")]
    assert len(starts) == 1, f"应有且仅有一条 Git 规则，实际: {starts}"
    block = [lines[starts[0]]]
    for line in lines[starts[0] + 1:]:
        if not line.startswith("  - "):
            break
        block.append(line)
    return "\n".join(block)


def test_git_rule_uses_user_select_before_commit_or_push():
    """Git 条目必须写明：commit / push 之前先用 user_select 让用户选择。"""
    bullet = _git_bullet()
    assert_contains(bullet, "必须", "user_select", "commit", "push", "前")


def test_git_rule_asks_user_to_agree():
    """Git 条目必须让用户明确选择是否同意执行。"""
    bullet = _git_bullet()
    assert_contains(bullet, "同意执行")


def test_git_rule_requires_message_confirmation():
    """提交信息须经用户确认，不得自行拟定后直接提交。"""
    bullet = _git_bullet()
    assert_contains(bullet, "提交信息", "确认", "不得自行拟定")


def test_git_rule_covers_scope_message_and_fallback():
    """用户选择需覆盖范围与提交信息；未授权时不得替用户提交。"""
    bullet = _git_bullet()
    assert_contains(bullet, "范围", "提交信息", "取消", "不得")


def test_git_rule_allows_user_to_cancel():
    """用户必须可以取消：取消时保留工作区改动，不得跳过确认直接提交。"""
    bullet = _git_bullet()
    assert_contains(bullet, "用户取消任一环节时", "保持改动留在工作区", "不得跳过 `user_select`")
    assert "不得跳过 `user_select` 直接提交或推送" in bullet


def test_git_rule_default_options_decline():
    """超时/非交互回退「取消/不提交」，不得替用户推进远程写操作。"""
    bullet = _git_bullet()
    assert_contains(bullet, "default_options", "置为「取消/不提交」", "按「取消/不提交」处理")
