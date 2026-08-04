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
from ..hooks import use_memo
from ..output import StyledRun

_logger = logging.getLogger(__name__)

__all__ = ["Gradient"]


def _gradient_runs(text: str, colors) -> list:
    """逐字符渐变 runs（复用 header 的 lerp_color 插值语义）。"""
    if not text or not colors:
        return []
    stops = [int(c) for c in colors if c is not None]
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

    Returns:
        TEXT 元素（渐变 StyledRun 序列）。
    """
    text = props.get("text")
    text = "" if text is None else str(text)
    colors = props.get("colors", [45, 39, 141, 213])
    # ★ 标准控件性能（阶段4）：use_memo 缓存渐变 runs——deps 用
    # ``(text, colors 元组)``（str/原始类型值比较；不同 list 同值命中），
    # 同文本同色标跨帧复用**同一 runs 列表引用**（TEXT ``_paint_cache``
    # 引用级命中，TopHeader 渐变标题每帧零重建）。use_memo 无条件调用
    # （hooks 顺序不变式），分支在缓存之后。
    colors_key = tuple(int(c) for c in colors) if colors else ()
    runs = use_memo(
        lambda: _gradient_runs(text, colors),
        (text, colors_key),
    )
    if not text:
        return h(TEXT, {"children": ""})
    if not colors:
        # 空色标：无样式文本（保留内容，不渐变）
        return h(TEXT, {"children": text})
    if not runs:
        return h(TEXT, {"children": ""})
    style = props.get("style")
    if style is not None and runs:
        runs = [
            StyledRun(r.text, style.merge(r.style)) if r.style is not None else StyledRun(r.text, style)
            for r in runs
        ]
    return h(TEXT, {"styled": runs})
