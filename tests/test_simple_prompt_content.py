"""简单模式提词内容回归测试。

用户需求：简单模式提词必须包含「所有用户指定内容全部完成才允许总结」红线。
"""

from __future__ import annotations

from tests._paths import prompts_dir

_SIMPLE_PROMPT_PATH = prompts_dir() / "prompts_export_main_simple.md"

#: 红线必须具备的核心约束片段
_RED_LINE_FRAGMENTS = (
    "强制所有用户指定的东西全部完成才允许总结",
    "只有用户指定的每一项内容都真正做完",
    "一律严禁总结",
    "禁止标记完成、禁止交付",
)


def _prompt_text() -> str:
    return _SIMPLE_PROMPT_PATH.read_text(encoding="utf-8")


def test_summary_red_line_present():
    """红线存在且包含全部核心约束片段。"""
    text = _prompt_text()
    for fragment in _RED_LINE_FRAGMENTS:
        assert fragment in text, f"缺少红线约束片段: {fragment}"


def test_summary_red_line_is_top_severity():
    """红线以最高等级措辞声明（一票否决）。"""
    text = _prompt_text()
    line = next(
        (ln for ln in text.splitlines() if "强制所有用户指定的东西全部完成才允许总结" in ln),
        "",
    )
    assert "红线" in line
    assert "一票否决" in line


def test_summary_red_line_after_full_implementation_rule():
    """顺序约束：总结红线必须排在「强制完整实现用户的所有要求」之后。"""
    text = _prompt_text()
    full_impl = text.find("强制完整实现用户的所有要求")
    summary_rule = text.find("强制所有用户指定的东西全部完成才允许总结")
    assert full_impl != -1
    assert summary_rule != -1
    assert full_impl < summary_rule


def test_summary_red_line_forbids_early_conclusion():
    """禁止提前收场的话术与「先汇总」变体都在约束范围内。"""
    text = _prompt_text()
    assert "基本完成" in text
    assert "先汇总一下" in text


def test_summary_red_line_uses_max_severity_wording():
    """总结红线必须使用最严重等级措辞（最高优先级 + 零容忍 + 不可协商 + 无豁免权）。"""
    text = _prompt_text()
    line = next(
        (ln for ln in text.splitlines() if "强制所有用户指定的东西全部完成才允许总结" in ln),
        "",
    )
    for fragment in ("最高优先级", "零容忍", "不可协商", "无豁免权", "没有任何从轻空间"):
        assert fragment in line, f"红线措辞缺少最严重等级片段: {fragment}"


def test_summary_requires_no_remaining_problem():
    """只有全部完成且确认没有问题了才允许总结，禁止在未验证时收尾。"""
    text = _prompt_text()
    line = next(
        (ln for ln in text.splitlines() if "强制所有用户指定的东西全部完成才允许总结" in ln),
        "",
    )
    assert "确认彻底没有问题之后" in line
    assert "实际验证通过" in line
    assert "一律判定该轮任务彻底失败" in line
    assert "作废该总结" in line
