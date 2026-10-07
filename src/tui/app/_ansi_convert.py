"""AnsiLine → ink StyledRun 转换（共享辅助）。

轨迹检查器与 editmsg 预览区都需把 **渲染管线输出**（``renderer.ansi`` 的
``AnsiLine``，已带 markdown/代码高亮样式）转换为 ink 的 ``StyledRun`` 行。
此前该逻辑内联在 ``trace_view``（``_to_tui_style`` / ``_convert_ansi_row``）
——editmsg 预览复用同一实现时提取为独立模块（单一真源，避免两处漂移）。

2026-10-07 第三批（editmsg 预览 markdown 渲染）新增本模块。

依赖约束：仅依赖 renderer.ansi（helpers）+ tui 核心样式 + trace_styles
（reasoning 弱化样式）——无 app 反向依赖。
"""

from __future__ import annotations

from src.tui.core.style import Style
from src.tui.ink import StyledRun

from .trace_styles import _S_DIM

__all__ = ["_clamp_color", "_to_tui_style", "_convert_ansi_row"]


def _clamp_color(v):
    """renderer 色号钳制（bool / 越界 int / 非 int 非 tuple 色号 → None）。

    ``Style.__post_init__`` 对 bool 色号显式抛 ValueError（bool 是 int 子类
    但语义错误），故 bool 必须回退 None（修复前原样放行 → Style 构造异常
    传播中断渲染）。
    """
    if isinstance(v, bool) or not isinstance(v, int):
        return None
    return v if 0 <= v <= 255 else None


def _to_tui_style(rs, kind: str):
    """renderer.ansi.Style → tui.core.Style（reasoning 叠加暗灰弱化）。

    RGB 三元组 fg/bg → ``TrueColor``；reasoning 叠加 ``_S_DIM``（与聊天区
    推理块弱化语义一致）。非法色号钳制回退 None（异常数据不中断渲染）。
    """
    if rs is None:
        st = None
    else:
        fg = rs.fg
        if isinstance(fg, tuple):
            from src.tui.core.color import TrueColor
            try:
                fg = TrueColor(*fg)
            except Exception:
                fg = None
        else:
            fg = _clamp_color(fg)
        bg = rs.bg
        if isinstance(bg, tuple):
            from src.tui.core.color import TrueColor
            try:
                bg = TrueColor(*bg)
            except Exception:
                bg = None
        else:
            bg = _clamp_color(bg)
        st = Style(fg=fg, bg=bg, bold=rs.bold, italic=rs.italic,
                   dim=rs.dim, underline=rs.underline)
    if kind == "reasoning":
        st = _S_DIM if st is None else st.merge(_S_DIM)
    return st


def _convert_ansi_row(aline, right_w: int, kind: str) -> list:
    """单条 AnsiLine → StyledRun 行列表（超宽按 right_w 样式安全换行）。

    空行（无 runs / 纯文本为空——段落/结构分隔）→ 单个空格占位行（TEXT
    ``styled=[]`` 渲染 0 行不绘制——空行必须有占位才能保留段落/表格结构）。
    单 run 样式转换异常 → 该 run 回退默认样式（不中断整行渲染）。
    """
    if not getattr(aline, "runs", None) or not getattr(aline, "plain", ""):
        return [[StyledRun(" ", _to_tui_style(None, kind))]]
    from src.renderer.ansi.helpers import wrap_line
    out: list = []
    for wl in wrap_line(aline, right_w):
        runs: list = []
        for r in wl.runs:
            if not r.text:
                continue
            try:
                st = _to_tui_style(r.style, kind)
            except Exception:
                st = None
            runs.append(StyledRun(r.text, st))
        out.append(runs if runs else [StyledRun(" ", _to_tui_style(None, kind))])
    return out
