"""gradient — Gradient 渐变文本控件（React Ink 生态 ink-gradient 等价物）。

逐字符线性渐变：文本按显示宽度均分色标区间，逐字符 ``lerp_color`` 插值。
用于标题/品牌文案等视觉强调场景（TopHeader 渐变同源实现，经本控件复用）。

依赖：仅 core.color.lerp_color + core.style.Style + element/output（Layer 0/1）。
"""

from __future__ import annotations

import logging

from src.tui.core.color import lerp_color
from src.tui.core.style import Style
from ..element import TEXT, Element, h
from ..output import StyledRun

_logger = logging.getLogger(__name__)

__all__ = ["Gradient"]


def _gradient_runs(text: str, colors) -> list:
    """逐字符渐变 runs（复用 header 的 lerp_color 插值语义）。"""
    # ★ 2026-08-06：colors 非迭代（int/float 标量）时回退无样式——修复前
    #   ``for c in colors`` 对 `colors=45` 抛 TypeError 渲染崩溃（Gradient
    #   的 props 守卫只防 None/空列表，标量 truthy 穿透）。
    # ★ P1-1（review）：非迭代/str/bytes colors 返回 ``[StyledRun(text, None)]``
    #   （保留文本无样式）而非 []——修复前返回 [] 被 Gradient 外层判为空结果
    #   → 渲染空文本（``colors=45`` / ``colors="red"`` 时文本整体消失）。
    if not text:
        return []
    if not colors or not hasattr(colors, "__iter__") or isinstance(colors, (str, bytes)):
        return [StyledRun(text, None)]
    # ★ P3（review）：colors 含非 int 值（str/float/None 等）时 ``int(c)`` 抛
    #   ValueError——转换失败跳过该项（保留其余色标）；全部失败回退无样式。
    stops: list[int] = []
    for c in colors:
        if c is None:
            continue
        try:
            stops.append(int(c))
        except (TypeError, ValueError, OverflowError):
            continue
    if not stops:
        return [StyledRun(text, None)]
    n = len(text)
    if n <= 1:
        return [StyledRun(text, Style(fg=stops[0]))]
    if len(stops) == 1:
        return [StyledRun(text, Style(fg=stops[0]))]
    seg = len(stops) - 1
    runs = []
    for i, ch in enumerate(text):
        t = i / (n - 1)
        pos = t * seg
        idx = int(pos)
        if idx >= seg:
            idx = seg - 1
        color = lerp_color(stops[idx], stops[idx + 1], pos - idx)
        runs.append(StyledRun(ch, Style(fg=color)))
    return runs


def Gradient(props: dict) -> Element:
    """渐变文本控件。

    Props:
        text: 渐变文本（str）。
        colors: 色标列表（256 色号 int 列表，如 ``[45, 39, 141, 213]``；
            至少 2 个才能渐变，1 个/空回退纯色/无样式）。
        style: 基础样式（与渐变 fg 合并——渐变 fg 优先；style 的其他属性
            保留）。
        styled: 预计算 StyledRun 列表（可选）——提供时**直接使用**（不再
            按 text/colors 重新渐变），供宿主按预算截断后注入（如 TopHeader
            窄屏截断渐变标题：宽屏 use_memo 缓存引用、窄屏截断后经本 prop
            注入，视觉等价且缓存引用稳定）。style 仍合并（覆盖 styled run
            样式）。

    Returns:
        TEXT 元素（渐变 StyledRun 序列）。
    """
    # ★ styled 注入模式（控件化支持）：宿主提供预计算 runs（截断/缓存场景）
    #   时直接使用——不再重新渐变（text/colors 仅作描述性输入，可省略）。
    styled = props.get("styled")
    style = props.get("style")
    if styled is not None:
        runs = list(styled)
        if style is not None and runs:
            runs = [
                StyledRun(r.text, style.merge(r.style)) if r.style is not None else StyledRun(r.text, style)
                for r in runs
            ]
        return h(TEXT, {"styled": runs})
    text = props.get("text")
    text = "" if text is None else str(text)
    colors = props.get("colors", [45, 39, 141, 213])
    if not text:
        return h(TEXT, {"children": ""})
    if not colors:
        # 空色标：无样式文本（保留内容，不渐变）
        return h(TEXT, {"children": text})
    runs = _gradient_runs(text, colors)
    if not runs:
        return h(TEXT, {"children": ""})
    if style is not None and runs:
        runs = [
            StyledRun(r.text, style.merge(r.style)) if r.style is not None else StyledRun(r.text, style)
            for r in runs
        ]
    return h(TEXT, {"styled": runs})
