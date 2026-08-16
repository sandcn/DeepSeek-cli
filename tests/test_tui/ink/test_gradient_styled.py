"""Gradient 控件 styled 注入模式测试（全面控件化方案B）。

背景（2026-08-16 方案B）：TopHeader 渐变标题经标准控件 ``Gradient``
渲染——宽屏经 ``styled`` 注入模式复用 use_memo 缓存引用（引用稳定、
diff 身份短路），窄屏截断后同样注入截断 runs。本测试锁定 styled 注入
模式行为（直接使用注入 runs、style 合并、无 styled 时走 text/colors
渐变路径）。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink.output import StyledRun
from src.tui.ink.widgets.gradient import Gradient, _gradient_runs


def _collect_styled_text(el) -> str:
    """TEXT 元素 styled runs 拼接文本。"""
    runs = el.props.get("styled") or []
    return "".join(r.text for r in runs)


def test_gradient_styled_injection_uses_provided_runs():
    """styled 注入模式：直接使用提供 runs（不重新渐变 text/colors）。"""
    runs = [StyledRun("A", Style(fg=45)), StyledRun("B", Style(fg=213))]
    el = Gradient({"styled": runs})
    assert el.type == "text"
    assert _collect_styled_text(el) == "AB"
    assert list(el.props["styled"]) == runs


def test_gradient_styled_with_style_merges():
    """styled 注入 + style：style 合并（bold 保留，fg 被注入 run 覆盖）。"""
    runs = [StyledRun("X", Style(fg=45))]
    el = Gradient({"styled": runs, "style": Style(bold=True)})
    out_runs = el.props["styled"]
    assert len(out_runs) == 1
    merged = out_runs[0].style
    assert merged.bold is True
    assert merged.fg == 45  # 注入 run fg 保留（style.merge 后者覆盖前者）


def test_gradient_styled_no_style_passthrough():
    """styled 注入无 style：runs 原样（不复制/修改）。"""
    runs = [StyledRun("Z", Style(fg=1))]
    el = Gradient({"styled": runs})
    assert el.props["styled"] == runs


def test_gradient_text_colors_fallback():
    """无 styled 时走 text+colors 渐变路径（回归——原行为不变）。"""
    el = Gradient({"text": "DeepSeek CLI", "colors": [45, 39, 141, 213]})
    assert el.type == "text"
    text = _collect_styled_text(el)
    assert text == "DeepSeek CLI"
    # 逐字符渐变：每个字符一个 StyledRun，色号在色标插值范围内
    runs = el.props["styled"]
    assert len(runs) == len("DeepSeek CLI")
    assert all(r.style is not None and r.style.fg is not None for r in runs)


def test_gradient_runs_helper_matches_component():
    """_gradient_runs 与 Gradient text 路径输出一致（单一真源）。"""
    text = "DeepSeek CLI"
    colors = [45, 39, 141, 213]
    el = Gradient({"text": text, "colors": colors})
    assert [r.style.fg for r in el.props["styled"]] == [
        r.style.fg for r in _gradient_runs(text, colors)
    ]
