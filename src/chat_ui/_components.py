"""组件层 — TuiComponent 基类 + 8 个消息流子类 + 4 个 @dataclass 数据模型。

从 _tui.py 拆分，包含所有消息流组件和底部栏组件的数据模型定义。
BottomBarProtocol 已移至 _protocols.py，此处保留兼容 re-export。
"""

from __future__ import annotations

import logging
import shutil
import time
import unicodedata
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter

from ._styled import StyledText

from ._const import (
    _STYLE_DIM, _STYLE_FAIL, _STYLE_WARN, _STYLE_SUCCESS, _STYLE_ERROR, _STYLE_BOLD,
    _THINKING_HEADER, _THINKING_SEPARATOR,
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

    children 参数允许构建声明式组件树，所有子类自动继承此能力。
    现有子类无需调用 super().__init__() 即可安全访问 children 属性。
    """

    _children: list["TuiComponent"]

    def __init__(self, children: list["TuiComponent"] | None = None):
        """初始化组件。

        Args:
            children: 子组件列表，默认空列表。所有现有子类无需修改即可兼容。
        """
        self._children = list(children) if children is not None else []

    def _ensure_children(self) -> list["TuiComponent"]:
        """惰性初始化 _children（兼容未调用 super().__init__() 的旧子类）。"""
        if not hasattr(self, '_children'):
            self._children = []
        return self._children

    @property
    def children(self) -> list["TuiComponent"]:
        """子组件列表（只读视图）。"""
        return self._ensure_children()

    def render(self) -> str | StyledText:
        raise NotImplementedError

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """通过 OutputAdapter 渲染组件，返回估计行数。

        默认实现调用 render() 并通过 adapter.write() 输出。
        子类可重写以绕过 render() 直接操作 adapter（如 ToolOutputBlock）。
        """
        output = self.render()
        if isinstance(output, (str, StyledText)):
            adapter.write(output)
            return _estimate_content_lines(str(output))
        return 0

    def render_children(self) -> str | StyledText:
        """遍历 children，调用每个子组件的 render()，返回拼接结果。

        返回类型为 str | StyledText，自动处理混合类型（部分子组件 render str，部分 render StyledText）。
        无子组件时返回空字符串。
        """
        ch = self._ensure_children()
        if not ch:
            return ""

        outputs: list[str | StyledText] = []
        for child in ch:
            result = child.render()
            if isinstance(result, (str, StyledText)):
                outputs.append(result)

        if not outputs:
            return ""

        # 全部为 str 时直接拼接（避免不必要的 Text 对象开销）
        if all(isinstance(o, str) for o in outputs):
            return "\n".join(o for o in outputs if isinstance(o, str))

        # 混合类型或全 StyledText：使用 StyledText.assemble 拼接
        assembled: list[str | StyledText] = []
        for i, o in enumerate(outputs):
            if i > 0:
                assembled.append("\n")
            assembled.append(o)
        return StyledText.assemble(*assembled) if assembled else ""

    def add_child(self, child: "TuiComponent") -> "TuiComponent":
        """链式添加子组件并返回 self。

        示例:
            box = Box().add_child(Text("hello")).add_child(Text("world"))
        """
        self._ensure_children().append(child)
        return self


# ═══════════════════════════════════════════════════════════
# 消息流组件
# ═══════════════════════════════════════════════════════════

class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> StyledText:
        return StyledText.assemble(("\n  > ", _STYLE_BOLD), (self.text, _STYLE_BOLD))

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
                    adapter.write(StyledText.from_ansi(clean))
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
            adapter.write(StyledText.assemble(("   ", _STYLE_DIM), (text, _STYLE_DIM)))
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
            adapter.write(StyledText.assemble(
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
            adapter.write(StyledText.assemble(
                ("  ! ", _STYLE_FAIL),
                (f"全部失败: {names}", _STYLE_FAIL),
            ))
        else:
            adapter.write(StyledText.assemble(
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
            adapter.write(StyledText.assemble(
                (f"    {name}", _STYLE_DIM),
                (f"  {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
            detail += 1
        if len(failed) > 3:
            adapter.write(StyledText.assemble(
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

    def render(self) -> StyledText:
        return StyledText.assemble(("\n  ! ", _STYLE_ERROR), (self.message, _STYLE_ERROR))

class NotificationBlock(TuiComponent):
    """系统通知块 — 绿色 · 前缀。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> StyledText:
        return StyledText.assemble(("\n  · ", _STYLE_SUCCESS), (self.text, _STYLE_SUCCESS))

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
                adapter.write(StyledText.from_ansi(text))
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

@dataclass
class StatusLine:
    """状态行 — 模型名 · tokens · 时间 · 工具计数。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    model: str = ""
    tokens: int = 0
    elapsed: float = 0.0
    tool_count: int = 0
    tool_fail: int = 0
    streaming: bool = False

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


@dataclass
class InputLine:
    """输入行 — > 提示符 + 用户输入文本 + 光标。

    由底部栏 _BottomBar 负责实际渲染，此组件为数据模型。
    """
    text: str = ""
    cursor_pos: int = 0

    def render(self) -> str:
        return f"> {self.text}"


@dataclass
class CompletionPopup:
    """补全弹窗 — 浮动在输入行上方的候选项列表。

    由底部栏 _CompletionPopup 负责实际渲染，此组件为数据模型。
    """
    items: list[str] = field(default_factory=list)
    selected: int = 0
    visible: bool = False

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


@dataclass
class SelectionMenu:
    """底部选择菜单 — 供 user_select / 消息编辑 / 命令面板等使用。

    由底部栏 _BottomBar.run_bottom_bar_selection() 实际渲染。
    """
    items: list[str] = field(default_factory=list)
    selected: int = 0
    visible: bool = False
    title: str = ""

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

_term_width_cache: tuple[float, int] = (0.0, 80)  # (timestamp, width)


def _get_terminal_width() -> int:
    """获取终端宽度，带 2 秒 TTL 缓存。"""
    global _term_width_cache
    now = time.monotonic()
    if now - _term_width_cache[0] < 2.0:
        return _term_width_cache[1]
    try:
        w = shutil.get_terminal_size().columns
    except Exception:
        w = 80
    if w <= 0:
        w = 80
    _term_width_cache = (now, w)
    return w


def _estimate_content_lines(text: str) -> int:
    """估算文本在终端中占用的行数，考虑 CJK 宽字符和终端宽度。

    对每行文本按字符宽度（CJK 宽字符 2 列，其他 1 列）计算实际占列数，
    除以终端宽度向上取整得出该行占用行数，累加所有行。
    若终端宽度获取失败则回退到纯换行计数。
    """
    if not text:
        return 1
    try:
        term_w = _get_terminal_width()
    except Exception:
        return text.count('\n') + 1
    if term_w <= 0:
        return text.count('\n') + 1
    total = 0
    for line in text.split('\n'):
        line_w = 0
        for ch in line:
            line_w += 2 if unicodedata.east_asian_width(ch) in 'WF' else 1
        total += max(1, (line_w + term_w - 1) // term_w)
    return total

