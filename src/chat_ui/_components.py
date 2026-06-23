"""组件层 — TuiComponent 基类 + 11 个子类。

从 _tui.py 拆分，包含所有消息流组件和底部栏组件的数据模型定义。
BottomBarProtocol 已移至 _protocols.py，此处保留兼容 re-export。
"""

from __future__ import annotations

import logging
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter

from rich.style import Style
from rich.text import Text

from ._const import (
    _STYLE_DIM, _STYLE_FAIL, _STYLE_WARN, _STYLE_SUCCESS, _STYLE_ERROR, _STYLE_BOLD,
    _THINKING_HEADER,
    _MAX_ERROR_LENGTH, _MAX_OUTPUT_LEN,
)

from ._render_state import _ReasoningState

from ._utils import _truncate_msg

from ._protocols import BottomBarProtocol  # 兼容 re-export（定义已移至 _protocols.py）

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 组件基类
# ═══════════════════════════════════════════════════════════

class TuiComponent:
    """React Ink-like 渲染组件基类。

    子类至少实现 render()，可选重写 render_to_adapter() 以直接操作 OutputAdapter。
    """
    def render(self) -> str | Text:
        raise NotImplementedError

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现调用 render() 并通过 adapter.write() 输出。
        子类可重写以绕过 render() 直接操作 adapter（如 ToolOutputBlock）。
        """
        output = self.render()
        if isinstance(output, (str, Text)):
            adapter.write(output)
            return _estimate_content_lines(str(output))
        return 0


# ═══════════════════════════════════════════════════════════
# 消息流组件
# ═══════════════════════════════════════════════════════════

class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  > ", _STYLE_BOLD), (self.text, _STYLE_BOLD))


class ThinkingBlock(TuiComponent):
    """思考/推理内容块 — 流式追加写入 IncrementalRenderer。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入推理内容，返回估计行数。"""
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is None:
            return 0
        lines = 0
        if is_first:
            rr.write(_THINKING_HEADER)
            lines += _estimate_content_lines(_THINKING_HEADER)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    def render(self) -> str:
        return ""


class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。"""
    def __init__(self, rs: "_RenderState"):
        self._rs = rs

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。"""
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    def render(self) -> str:
        return ""


class ToolOutputBlock(TuiComponent):
    """工具执行输出块。"""
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        text = self.text
        if len(text) > _MAX_OUTPUT_LEN:
            text = text[:_MAX_OUTPUT_LEN] + "...(truncated)"
        has_carriage = '\r' in text
        if has_carriage:
            if '\033[' in text:
                clean = text.replace('\r', '')
                try:
                    adapter.write(Text.from_ansi(clean))
                except Exception:
                    _logger.debug("tool_output ANSI 解析失败, 回退 raw 输出", exc_info=True)
                    adapter.write_raw(clean)
            else:
                adapter.write_raw(text.split('\r')[-1])
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                clean = text.replace('\r', '') if '\033[' in text else text.split('\r')[-1]
                return _estimate_content_lines(clean)
            return 0
        else:
            adapter.write(Text.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text


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
            adapter.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(self.successful)}工具完成", _STYLE_SUCCESS),
            ))
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
        names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            adapter.write(Text.assemble(
                ("  ! ", _STYLE_FAIL),
                (f"全部失败: {names}", _STYLE_FAIL),
            ))
        else:
            adapter.write(Text.assemble(
                ("  ! ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {names}", _STYLE_WARN),
            ))
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
            adapter.write(Text.assemble(
                (f"    {name}", _STYLE_DIM),
                (f"  {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
            detail += 1
        if len(failed) > 3:
            adapter.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM),
            ))
            detail += 1
        return lines + detail

    def render(self) -> str:
        return f"ToolSummary(success={len(self.successful)}, fail={len(self.failed)})"


class ErrorBlock(TuiComponent):
    """错误提示块 — 红色 ! 前缀。"""
    def __init__(self, message: str):
        self.message = _truncate_msg(message, _MAX_ERROR_LENGTH)

    def render(self) -> Text:
        return Text.assemble(("\n  ! ", _STYLE_ERROR), (self.message, _STYLE_ERROR))


class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> Text:
        return Text.assemble(("\n  · ", _STYLE_SUCCESS), (self.text, _STYLE_SUCCESS))


class WriteLineBlock(TuiComponent):
    """单行输出块 — 支持 ANSI 转义序列。

    用于 OutputEvent / write_line 等非消息流的样式化行输出。
    """
    def __init__(self, text: str):
        self.text = text

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        text = self.text
        if '\033[' in text:
            try:
                adapter.write(Text.from_ansi(text))
            except Exception:
                _logger.debug("write_line ANSI 解析失败, 回退 raw 输出", exc_info=True)
                adapter.write_raw(text + "\n")
                return _estimate_content_lines(text)
            return _estimate_content_lines(text)
        else:
            adapter.write_raw(text + "\n")
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text


# ═══════════════════════════════════════════════════════════
# 底部栏组件
# ═══════════════════════════════════════════════════════════

class StatusLine(TuiComponent):
    """状态行 — 模型名 · tokens · 时间 · 工具计数。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.model: str = ""
        self.tokens: int = 0
        self.elapsed: float = 0.0
        self.tool_count: int = 0
        self.tool_fail: int = 0
        self.streaming: bool = False

    def render(self) -> str:
        """渲染为单行状态文本。"""
        parts = []
        if self.model:
            parts.append(self.model)
        if self.tokens:
            parts.append(f"{self.tokens}t")
        if self.elapsed:
            parts.append(f"{self.elapsed:.1f}s")
        if self.tool_count:
            s = f"⚙{self.tool_count}"
            if self.tool_fail:
                s += f"!{self.tool_fail}"
            parts.append(s)
        return " · ".join(parts) if parts else ""


class InputLine(TuiComponent):
    """输入行 — > 提示符 + 用户输入文本 + 光标。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.text: str = ""
        self.cursor_pos: int = 0

    def render(self) -> str:
        return f"> {self.text}"


class CompletionPopup(TuiComponent):
    """补全弹窗 — 浮动在输入行上方的候选项列表。

    由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False

    def show(self, items: list[str], selected: int = 0) -> None:
        self.items = items
        self.selected = selected
        self.visible = True

    def hide(self) -> None:
        self.visible = False
        self.items.clear()

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = []
        for i, item in enumerate(self.items):
            prefix = "→ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)


class SelectionMenu(TuiComponent):
    """底部选择菜单 — 供 user_select / 消息编辑 / 命令面板等使用。

    由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
    """
    def __init__(self):
        self.items: list[str] = []
        self.selected: int = 0
        self.visible: bool = False
        self.title: str = ""

    def render(self) -> str:
        if not self.visible:
            return ""
        lines = [f"  {self.title}"] if self.title else []
        for i, item in enumerate(self.items):
            prefix = "▶ " if i == self.selected else "  "
            lines.append(f"{prefix}{item}")
        return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# 行数估算辅助（内部使用）
# ═══════════════════════════════════════════════════════════

def _estimate_content_lines(text: str) -> int:
    if not text:
        return 1
    return text.count('\n') + 1
