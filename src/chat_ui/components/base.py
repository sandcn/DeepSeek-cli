"""组件层 — TuiComponent 基类 + 8 个消息流子类 + 4 个 @dataclass 数据模型。

从 _tui.py 拆分，包含所有消息流组件和底部栏组件的数据模型定义。
BottomBarProtocol 已移至 _protocols.py，此处保留兼容 re-export。
"""

from __future__ import annotations

import logging
import shutil
import time
import unicodedata
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...api.renderer.output import OutputAdapter
    from ..vdom.types import HookState

from ..infrastructure.styled import StyledText

from ..commands.const import (
    _STYLE_DIM, _STYLE_FAIL, _STYLE_WARN, _STYLE_SUCCESS, _STYLE_ERROR, _STYLE_BOLD,
    _THINKING_HEADER, _CLAUDE_THINKING_HEADER, _THINKING_SEPARATOR,
    _MAX_ERROR_LENGTH, _MAX_OUTPUT_LEN,
)

from ..state.render_state import _ReasoningState

from ..infrastructure.utils import _truncate_msg

from ..infrastructure.protocol import BottomBarProtocol  # 兼容 re-export（定义已移至 _protocols.py）
from ..state.state_tree import StatusLine, InputLine, CompletionPopup, SelectionMenu  # re-export（定义已移至 _data_models.py）

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

    # ── Hooks 支持（惰性初始化，旧子类不触发） ──────────
    _hooks: list["HookState"] | None = None   # hook 状态列表（惰性创建）
    _hook_index: int = 0                       # 当前 hook 调用索引（每次 render 前重置）
    _dirty: bool = False                       # 脏标记（状态变更时置 True）
    _mounted: bool = False                     # 是否已挂载

    def __init__(self, children: list["TuiComponent"] | None = None):
        """初始化组件。

        Args:
            children: 子组件列表，默认空列表。所有现有子类无需修改即可兼容。
        """
        self._children = list(children) if children is not None else []
        # _hooks / _hook_index / _dirty / _mounted 使用类属性默认值，
        # 不在 __init__ 中重复赋值以避免与类属性默认值冗余。

    def _ensure_children(self) -> list["TuiComponent"]:
        """惰性初始化 _children（兼容未调用 super().__init__() 的旧子类）。"""
        if not hasattr(self, '_children'):
            self._children = []
        return self._children

    # ── Hooks 支持方法 ────────────────────────────────

    def _ensure_hooks(self) -> list["HookState"]:
        """惰性初始化 _hooks 列表。

        仅在组件首次使用 hooks 时创建，确保不触发 hooks 的旧子类零开销。

        Returns:
            hooks 状态列表（首次访问时自动创建为空列表）。
        """
        if self._hooks is None:
            self._hooks = []
        return self._hooks

    def _reset_hooks(self) -> None:
        """每次 render 前重置 hook_index。

        应在 render() 或 render_vnode() 入口处调用，
        确保 hooks 按正确顺序匹配。

        @todo: 需要 VNode 渲染流程集成 — 当前由组件开发者手动调用，
        后续应在 VNodeRenderStrategy.render_commands() 遍历组件树时自动调用
        _reset_hooks()，类似 React 的 beginWork() 机制。
        """
        self._hook_index = 0

    def _cleanup(self) -> None:
        """Unmount 时清理所有 hooks 资源。

        调用全局 hooks 运行时清理该组件的所有 effect cleanup 函数，
        并标记组件为未挂载状态。
        """
        from ..vdom.hooks import get_hooks_runtime
        get_hooks_runtime().cleanup_component(self)
        self._mounted = False

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

    # ── 生命周期（Phase 7） ──────────────────────────

    @property
    def key(self) -> str:
        """稳定标识符 — 用于 VNode Diff 的 key 匹配。

        默认返回类名，子类可重写以提供更稳定的标识。
        """
        return type(self).__name__

    def mount(self) -> None:
        """组件挂载到 VNode 树时调用。

        子类可重写以初始化资源（如订阅事件、打开文件等）。
        默认设置 _mounted=True 标记挂载状态。
        """
        self._mounted = True

    def unmount(self) -> None:
        """组件从 VNode 树移除时调用。

        子类可重写以清理资源（如取消订阅、关闭文件等）。
        默认 no-op。
        """
        pass

    def update(self, props: dict) -> bool:
        """接收新 props，返回 True 表示需要重渲染。

        子类可重写以判断 props 是否实际变化，
        避免不必要的 render 调用。

        Args:
            props: 新的属性字典

        Returns:
            True 如果组件需要重渲染，False 如果可跳过
        """
        return False  # 默认不消费更新，子类按需重写为 True

    # @dead_code: VNode 渲染路径要求所有子类覆盖此方法。
    # 当前仅 ThinkingBlock / AnswerBlock / InputBarComponent 已覆盖，
    # 其余 6 个子类（UserMsgBlock / ToolOutputBlock / ToolSummaryBlock /
    # ErrorBlock / NotificationBlock / WriteLineBlock）使用默认实现，
    # 其 VNode 产出未经 diff 优化。待所有子类覆盖后可改为 raise NotImplementedError。
    def render_vnode(self) -> "VNode":
        """产出 VNode — 声明式渲染的主入口。

        默认调用 render() 并将结果包装为 VNode。
        子类可重写以直接产出 VNode 树（跳过 render()）。
        """
        from ..vdom.vnode import VNode
        result = self.render()
        return VNode(
            type=type(self).__name__,
            key=self.key,
            props={"text": str(result)} if result else {},
        )


# ═══════════════════════════════════════════════════════════
# 消息流组件
# ═══════════════════════════════════════════════════════════

class UserMsgBlock(TuiComponent):
    """用户消息块 — "> text" 加粗样式（Claude Code 风格下使用 ❯ 前缀）。"""
    def __init__(self, text: str):
        self.text = text

    def render(self) -> StyledText:
        # Claude Code 风格：使用 ❯ 前缀
        try:
            from ..infrastructure.claude_style import _is_claude_style_enabled, CLAUDE_PROMPT_ICON
            if _is_claude_style_enabled():
                return StyledText.assemble(
                    ("\n  " + CLAUDE_PROMPT_ICON + " ", _STYLE_BOLD),
                    (self.text, _STYLE_BOLD)
                )
        except ImportError:
            pass
        # 默认风格：使用 > 前缀
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
            # ── Claude Code 风格门控（惰性导入）─────────────
            from ..infrastructure.claude_style import _is_claude_style_enabled
            if _is_claude_style_enabled():
                from ..infrastructure.ansi import style
                header = style(_CLAUDE_THINKING_HEADER, dim=True, italic=True)
            else:
                header = _THINKING_HEADER
            rr.write(header)
            lines += _estimate_content_lines(header)
        rr.write(text)
        lines += _estimate_content_lines(text)
        return lines

    def close(self) -> None:
        self._rs.close_reasoning()

    @property
    def key(self) -> str:
        return "thinking"

    def update(self, props: dict) -> bool:
        """接收新 props。ThinkingBlock 的 render 由 write() 驱动，
        update() 返回 True 仅表示状态已变更。"""
        return True

    def render_vnode(self) -> "VNode":
        from ..vdom.vnode import VNode
        return VNode(
            type="thinking_block",
            key=self.key,
            props={
                "text": "",  # ThinkingBlock 通过 IncrementalRenderer 输出
                "phase": self._rs.reasoning_state.name if self._rs else "INACTIVE",
            },
        )

    def render(self) -> str:
        return ""

class AnswerBlock(TuiComponent):
    """助手回答块 — 流式 Markdown 渲染。

    Claude Code 风格（CHAT_UI_CLAUDE_STYLE=1）下：
    - write() 累积原始 Markdown 文本到 _pending_text
    - render_vnode() 产出已渲染的 ANSI 样式字符串
    - 非 Claude 路径行为不变（通过 IncrementalRenderer 渲染）
    """
    def __init__(self, rs: "_RenderState"):
        self._rs = rs
        self._pending_text: str = ""
        self._claude_checked: bool = False
        self._claude_mode: bool = False
        # 增量渲染缓存：避免每帧对全部累积文本重复渲染（O(n²)→O(n)）
        self._last_rendered_len: int = 0
        self._cached_rendered: str = ""

    def _check_claude_mode(self) -> bool:
        """惰性检测 Claude Code 风格门控（仅首次调用时检查）。"""
        if not self._claude_checked:
            self._claude_checked = True
            try:
                from ..infrastructure.claude_style import _is_claude_style_enabled
                self._claude_mode = _is_claude_style_enabled()
            except ImportError:
                self._claude_mode = False
        return self._claude_mode

    def write(self, text: str) -> int:
        """写入内容，返回估计行数。

        Claude 风格下累积原始 Markdown 文本到 _pending_text，
        同时写入 IncrementalRenderer 保持 Direct 路径兼容。
        """
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        if self._check_claude_mode():
            self._pending_text += text
        self._rs.get_content().write(text)
        return _estimate_content_lines(text)

    def close(self) -> None:
        self._rs.close_content()

    @property
    def key(self) -> str:
        return "answer"

    def update(self, props: dict) -> bool:
        return True

    def render_vnode(self) -> "VNode":
        from ..vdom.vnode import VNode
        # Claude Code 风格：产出已渲染的 ANSI 样式文本
        text = ""
        if self._check_claude_mode() and self._pending_text:
            cur_len = len(self._pending_text)
            # 增量渲染：仅对新增部分调用 render_markdown，追加到缓存
            if cur_len > self._last_rendered_len:
                from ..infrastructure.markdown_renderer import render_markdown
                delta = self._pending_text[self._last_rendered_len:]
                delta_rendered = render_markdown(delta)
                self._cached_rendered += delta_rendered
                self._last_rendered_len = cur_len
            text = self._cached_rendered
        return VNode(
            type="answer_block",
            key=self.key,
            props={
                "text": text,
                "phase": "content",
            },
        )

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
# React Ink 风格输入栏组件（Phase 8+：声明式底部栏）
# ═══════════════════════════════════════════════════════════

class InputBarComponent(TuiComponent):
    """React Ink 风格的输入栏组件。

    封装 InputLine + 状态行的声明式渲染。
    通过 render_vnode() 产出 VNode 参与 diff，
    仅变更时触发底部栏重绘。
    """
    def __init__(self, text: str = "", cursor_pos: int = 0):
        super().__init__(children=None)
        self.text = text
        self.cursor_pos = cursor_pos
        self._props = {}

    @property
    def key(self) -> str:
        return "input_bar"

    def update(self, props: dict) -> bool:
        new_text = props.get("text", "")
        new_pos = props.get("cursor_pos", 0)
        if new_text == self.text and new_pos == self.cursor_pos:
            return False
        self.text = new_text
        self.cursor_pos = new_pos
        self._props = props
        return True

    def render_vnode(self) -> "VNode":
        from ..vdom.vnode import VNode
        return VNode(
            type="input_bar",
            key=self.key,
            props={
                "text": self.text,
                "cursor_pos": self.cursor_pos,
            },
        )

    def render(self) -> str:
        """渲染为底部栏输入行格式。

        支持三种占位符文本（正常/流式/补全）和 Claude Code 风格适配。
        优先从 _props 读取属性，回退到实例属性以保持向后兼容。
        """
        text = self._props.get("text", self.text) if self._props else self.text
        cursor_pos = self._props.get("cursor_pos", self.cursor_pos) if self._props else self.cursor_pos

        from ..infrastructure.bottom_theme import (
            _COLOR_ACCENT, _COLOR_DIM, _COLOR_RESET,
            _PLACEHOLDER_TEXT, _PLACEHOLDER_COMPACT, _PLACEHOLDER_STREAMING,
        )

        # Claude Code 风格检测
        try:
            from ..infrastructure.claude_style import _is_claude_style_enabled, CLAUDE_PROMPT_ICON
        except ImportError:
            _is_claude_style_enabled = lambda: False
            CLAUDE_PROMPT_ICON = "\u276f"

        claude = _is_claude_style_enabled()
        prompt = CLAUDE_PROMPT_ICON if claude else "\u276f"
        is_streaming = self._props.get("is_streaming", False) if self._props else False
        has_completion = self._props.get("has_completion", False) if self._props else False

        if text:
            return f"{_COLOR_ACCENT}{prompt}{_COLOR_RESET} {text}"
        else:
            if is_streaming:
                ph = "Type a message..." if claude else _PLACEHOLDER_STREAMING
            elif has_completion:
                ph = "Type a message..." if claude else _PLACEHOLDER_COMPACT
            else:
                ph = "Type a message..." if claude else _PLACEHOLDER_TEXT
            return f"{_COLOR_ACCENT}{prompt}{_COLOR_RESET} {_COLOR_DIM}{ph}{_COLOR_RESET}"


# ═══════════════════════════════════════════════════════════
# 行数估算辅助（内部使用）
# ═══════════════════════════════════════════════════════════

# 模块级缓存（由 TuiEngine.__init__ 注入，未注入时使用临时回退）
_term_width_cache: dict | None = None
_TERM_WIDTH_TTL = 2.0  # 2 秒 TTL


def _get_terminal_width(cache: dict | None = None) -> int:
    """获取终端宽度，支持缓存。

    Args:
        cache: 可选的缓存字典 {'value': int, 'ts': float}。
               传入时使用该缓存（如 TuiEngine 实例属性），
               不传且模块级缓存为 None 时创建临时回退缓存。
    """
    _cache = cache if cache is not None else _term_width_cache
    if _cache is None:
        _cache = {"value": 80, "ts": 0.0}
    now = time.monotonic()

    if _cache["value"] > 0 and (now - _cache["ts"]) < _TERM_WIDTH_TTL:
        return _cache["value"]

    try:
        w = shutil.get_terminal_size().columns
    except Exception:
        w = 80
    if w <= 0:
        w = 80
    _cache["value"] = w
    _cache["ts"] = now

    return _cache["value"]


def _estimate_content_lines(text: str, cache: dict | None = None) -> int:
    """估算文本在终端中占用的行数，考虑 CJK 宽字符和终端宽度。

    对每行文本按字符宽度（CJK 宽字符 2 列，其他 1 列）计算实际占列数，
    除以终端宽度向上取整得出该行占用行数，累加所有行。
    若终端宽度获取失败则回退到纯换行计数。

    Args:
        text: 要估算行数的文本。
        cache: 可选的宽度缓存字典。不传时使用模块级回退缓存。
    """
    if not text:
        return 1
    try:
        term_w = _get_terminal_width(cache)
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

