"""工具完成汇总块 — ToolSummaryBlock。

在工具调用完成后显示汇总信息，包括成功/失败的工具及其错误详情。
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter

from rich.text import Text

from ..animation.animator import AnimatorContext
from ..core.style import Style
from ..core.text_utils import build_left_border_ansi, build_warning_pulse_ansi
from ..render_buffer import RenderBuffer
from ._base import TuiComponent


class ToolSummaryBlock(TuiComponent):
    """工具完成汇总块。"""
    def __init__(self, successful: tuple = (), failed: tuple = (), *, props: dict | None = None) -> None:
        super().__init__(props=props)
        self.successful = successful or ()
        self.failed = failed or ()

    def render(self, buffer: RenderBuffer | None = None) -> str | None:
        """渲染工具完成汇总内容为纯文本字符串。

        支持 buffer 参数：传入 buffer 时写入并返回 None；否则返回字符串。
        """
        failed = self._normalize_failed()
        total = len(self.successful) + len(failed)
        lines: list[str] = []

        if failed:
            frame = AnimatorContext.get_default().frame
            edge_ansi = build_left_border_ansi(frame, 23, 24)
            names = ", ".join(n for n, _ in failed)
            pulse_ansi = build_warning_pulse_ansi(
                AnimatorContext.get_default().breath_frame, "error",
            )
            pulse_wrap = pulse_ansi + "!\u2502\033[0m"

            if len(failed) == total:
                line = f"  {edge_ansi}   {pulse_wrap}全部失败: {names}"
            else:
                line = f"  {edge_ansi}   {pulse_wrap}{len(failed)}/{total} 失败: {names}"
            lines.append(line)

            for name, error in failed[:3]:
                short = ""
                if error:
                    short = error.split("\n")[0].strip()
                    if short and len(short) > 80:
                        short = short[:77] + "..."
                line = f"  {edge_ansi}    {name}"
                if short:
                    line += f"  {short}"
                lines.append(line)

            if len(failed) > 3:
                lines.append(f"  {edge_ansi}   ... 及其他 {len(failed) - 3} 个")
        elif self.successful:
            frame = AnimatorContext.get_default().frame
            edge_ansi = build_left_border_ansi(frame, 23, 24)
            lines.append(f"  {edge_ansi}   · {len(self.successful)}工具完成")

        result = "\n".join(lines)
        if buffer is not None:
            if result:
                buffer.write(0, 0, result)
            return None
        return result

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。

        委托 render(buffer) 获取渲染内容，再通过 Text.from_ansi() 转换为 Rich Text 输出。
        保持与原有 Rich 样式化输出的行为一致。
        """
        try:
            import shutil
            term_w = shutil.get_terminal_size().columns
        except Exception:
            term_w = 80
        buf = RenderBuffer(max(term_w, 80), 100)
        # 委托 render(buffer) 获取渲染内容
        self.render(buf)
        output = buf.render()
        if not output:
            return 0
        # 通过 Text.from_ansi 转换为 Rich Text 输出（保持向后兼容）
        try:
            adapter.write(Text.from_ansi(output))
        except Exception:
            adapter.write(output)
        return max(1, output.count('\n') + 1)

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
