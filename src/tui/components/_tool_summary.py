"""工具完成汇总块 — ToolSummaryBlock。

在工具调用完成后显示汇总信息，包括成功/失败的工具及其错误详情。
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

from ..consumer.const import _STYLE_SUCCESS, _STYLE_FAIL, _STYLE_WARN, _STYLE_DIM
from ..core.animator import AnimatorContext
from ..core.style import Style
from ..core.text_utils import build_left_border_ansi, build_warning_pulse_ansi
from ._base import TuiComponent


class ToolSummaryBlock(TuiComponent):
    """工具完成汇总块。"""
    def __init__(self, successful: tuple, failed: tuple):
        self.successful = successful or ()
        self.failed = failed or ()

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        failed = self._normalize_failed()
        total = len(self.successful) + len(failed)
        if failed:
            return self._render_failure(failed, total, adapter)
        elif self.successful:
            _frame = AnimatorContext.get_default().frame
            edge_ansi = build_left_border_ansi(_frame, 23, 24)
            t = Text.from_ansi(f"  {edge_ansi}   ")
            t.append(f"· {len(self.successful)}工具完成", _STYLE_SUCCESS)
            adapter.write(t)
            return 1
        return 0

    def _normalize_failed(self) -> tuple:
        safe = []
        for item in self.failed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                error = str(item[1]) if item[1] is not None else ""
                if len(item) > 2:
                    extras = ", ".join(str(x) for x in item[2:])
                    error = f"{error} [{extras}]" if error else f"[{extras}]"
                safe.append((str(item[0]), error))
            else:
                safe.append((str(item), ""))
        return tuple(safe)

    def _render_failure(self, failed: tuple, total: int, adapter: "OutputAdapter") -> int:
        _frame = AnimatorContext.get_default().frame
        edge_ansi = build_left_border_ansi(_frame, 23, 24)
        names = ", ".join(n for n, _ in failed)
        # 脉动错误图标（动态呼吸色）
        try:
            pulse_ansi = build_warning_pulse_ansi(
                AnimatorContext.get_default().breath_frame, "error",
            )
        except Exception:
            pulse_ansi = Style(fg=196).to_ansi()  # 兜底红色
        # 首行：左边缘 + 脉动错误图标 + 状态描述
        pulse_wrap = pulse_ansi + "!\u2502\033[0m"
        if len(failed) == total:
            rich_text = Text.from_ansi(f"  {edge_ansi}   {pulse_wrap}")
            rich_text.append(f"全部失败: {names}", _STYLE_FAIL)
        else:
            rich_text = Text.from_ansi(f"  {edge_ansi}   {pulse_wrap}")
            rich_text.append(f"{len(failed)}/{total} 失败: {names}", _STYLE_WARN)
        adapter.write(rich_text)
        lines = 1
        detail = 0
        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    max_w = 80
                    s = short
                    w = 0
                    cut = len(s)
                    for i, ch in enumerate(s):
                        cw = 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
                        if w + cw > max_w - 3:
                            cut = i
                            break
                        w += cw
                    if cut < len(s):
                        short = s[:cut] + "..."
            # 详细行：左边缘 + 工具名 + 错误摘要
            t = Text.from_ansi(f"  {edge_ansi}   ")
            t.append(f"  {name}", _STYLE_DIM)
            if short:
                t.append(f"  {short}", _STYLE_DIM)
            adapter.write(t)
            detail += 1
        if len(failed) > 3:
            t = Text.from_ansi(f"  {edge_ansi}   ")
            t.append(f"... 及其他 {len(failed) - 3} 个", _STYLE_DIM)
            adapter.write(t)
            detail += 1
        return lines + detail

    def render(self) -> str:
        return f"ToolSummary(success={len(self.successful)}, fail={len(self.failed)})"
