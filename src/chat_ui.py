"""ChatUI — 终端聊天消费者，订阅 DisplayEventBus 事件并渲染到终端。

架构：
  EventBus（事件线程） → _on_* handler → 入队 RenderCommand
  Reader 线程（10Hz）           → 出队 RenderCommand → _render() → 终端 I/O

事件处理与终端 I/O 解耦：handler 只做过滤+入队（非阻塞），
reader 线程串行消费所有渲染命令，保证输出有序。

流式输出期间底部栏：
  _BottomBar 使用 ANSI 滚动区域（DECSTBM）将终端分为上下两部分——
  上方内容区（行 1..H-3）正常滚动，底部 3 行固定显示输入界面。
  底部栏刷新通过 output_lock 与内容输出串行化，避免竞态。

公开 API — 外部定时刷新：
  refresh() — 供外部程序/timer 安全地定时刷新 TUI。
  ChatUIConsumer 启动后，任何线程/定时器均可安全调用 refresh()
  触发整屏重绘（ParallelDisplay 面板刷新 + 尺寸检测 + 底部栏重绘 + 光标定位）。
  与 Reader 线程共享 output_lock 串行化，不存在竞态。

内部子系统：
  _RenderState   — 渲染器生命周期管理（推理/内容/工具适配器）
  _CmplHandler   — Tab 补全交互逻辑（ESC 线程回调 → 底部栏更新）
  RenderCommand  — 渲染命令枚举（类型安全 + 自文档化，替代魔数整数）

注意：IncrementalRenderer（流式 Markdown）与 OutputAdapter（工具输出）
各自管理独立的 Rich Console 实例——写入串行化由 output_lock 保证，
宽度缓存独立刷新（5s TTL），不存在并发写入导致的撕裂。"""

from __future__ import annotations

import logging
import queue
import shutil
import sys
import threading
import time
from dataclasses import dataclass
from enum import Enum, IntEnum
from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from .api.escape_monitor import EscapeMonitor
    from .ui.parallel.display import ParallelDisplay

from .ui.events.event_bus import DisplayEventBus
from .ui.events.event_types import (
    ContentChunkEvent,
    DisplayEvent,
    ModelPhaseEvent,
    OutputEvent,
    ParseInfoDoneEvent,
    ParseInfoEvent,
    PhaseDoneEvent,
    ReasoningChunkEvent,
    ToolDoneEvent,
    ToolOutputChunkEvent,
    ToolStartedEvent,
    ToolSummaryEvent,
)
from rich.style import Style
from rich.text import Text

from wcwidth import wcswidth

from .api.renderer.output import OutputAdapter
from .ui._bottom_bar import _BottomBar, _compute_cursor_visual_pos
from .ui._completion import CompletionEngine
from .ui._lock import _try_acquire_output_lock, output_lock

_logger = logging.getLogger(__name__)

# ── 活跃实例引用（供交互式工具暂停/恢复） ────────────
_active_consumer: "ChatUIConsumer | None" = None

# ── 活跃 ParallelDisplay 引用（由 ParallelDisplay.start/stop 管理） ──
# 供 ChatUIConsumer._drain_queue 在每次渲染循环中驱动帧刷新，
# 取代 ParallelDisplay 原有的独立定时器机制。
_active_parallel_display: "ParallelDisplay | None" = None


def get_active_chat_ui() -> "ChatUIConsumer | None":
    """获取当前活跃的 ChatUIConsumer 实例，供交互式终端工具使用。

    user_select 等工具需要独占终端，通过此函数获取 ChatUIConsumer
    引用后可调用 suspend()/resume() 暂停/恢复后台渲染。
    """
    return _active_consumer


# ── 线程本地重入保护（防止 emit → logger → emit 递归） ──
_handler_reentrant = threading.local()


# ── ChatUIErrorHandler — 将 ERROR+ 日志投递到 ChatUI 上屏 ──

class ChatUIErrorHandler(logging.Handler):
    """自定义 logging Handler，捕获 ERROR+ 级别日志并投递到 ChatUI 上屏。

    通过模块级 _error_handler 实例注册到 root logger，
    在 emit() 中格式化 log record 并调用 get_active_chat_ui().on_error()。
    设计为纯入队操作（不 I/O），线程安全。

    防自引用循环保护（三层）：
      1. 线程本地重入标记 — emit 入口设置 _handler_reentrant.is_active，防止
         同一线程中 emit → on_error → logger → emit 递归
      2. record._chatui_reported 标记 — 在调用 on_error 前设置，防止同一
         record 被多个 handler 或跨线程二次处理
      3. on_error 自身仅执行队列入队操作，不产生日志调用
      三层保护确保 handler emit 绝不触发 logger → emit 死循环。

    延迟绑定：
      - ChatUI 实例通过 get_active_chat_ui() 延迟获取
      - ChatUI 未启动/已停止时 get_active_chat_ui() 返回 None → emit 静默跳过
    """

    def __init__(self, max_length: int = 200):
        super().__init__(level=logging.ERROR)
        self._max_length = max_length

    def emit(self, record: logging.LogRecord) -> None:
        """格式化 ERROR+ 日志记录并投递到 ChatUI 上屏。

        仅处理 ERROR/CRITICAL 级别，WARNING/INFO/DEBUG 跳过。
        空消息、空格式化结果、已被标记或线程重入中的 record 跳过。
        """
        # ★ 只处理 ERROR+ 级别（防御纵深：即使绕过 super().__init__(level=...)
        #   直接调用 emit()，此处也能保证正确过滤）
        if record.levelno < logging.ERROR:
            return

        # ★ 防自引用循环：线程重入检测（同线程 emit 递归阻断）
        if getattr(_handler_reentrant, 'is_active', False):
            return

        # ★ 防自引用循环：已标记的 record 跳过（跨调用/跨 handler 保护）
        if getattr(record, '_chatui_reported', False):
            return

        # ★ 格式化消息（格式: "模块名: 消息内容"）
        msg_content = record.getMessage()
        if not msg_content:
            return
        msg = f"{record.name}: {msg_content}"

        # ★ 截断超长消息
        if len(msg) > self._max_length:
            msg = msg[:self._max_length] + "..."

        # ★ 设置线程重入标记
        _handler_reentrant.is_active = True
        try:
            # ★ 延迟绑定：ChatUI 未激活时静默跳过
            consumer = get_active_chat_ui()
            if consumer is not None:
                consumer.on_error(msg)
            # ★ on_error 成功后设置 record 标记，防止同 record 被多个 handler
            #   或跨线程二次处理。若 on_error 抛出异常，标记不会误设——错误信
            #   息会被 _drain_queue 的 try/except 记录到日志（有 _chatui_reported
            #   保护不会递归），后续同 record 的 emit 不会被跳过。
            record._chatui_reported = True
        finally:
            # ★ 清除线程重入标记
            _handler_reentrant.is_active = False


# ── 注册到 root logger（模块级，全局生效） ────────────
_error_handler = ChatUIErrorHandler()
logging.getLogger().addHandler(_error_handler)


# ── 主 Agent 标识 ───────────────────────────────────────
_MAIN_LABEL = "assistant"
_MAIN_SOURCE = "agent"

# ── Rich Style 常量（供 OutputAdapter + Rich 渲染管线使用） ──
_STYLE_DIM = Style(dim=True)
_STYLE_DIM_GREY = Style(dim=True, color="grey58")
_STYLE_FAIL = Style(color="red")
_STYLE_WARN = Style(color="orange1")
_STYLE_SUCCESS = Style(color="green")
_STYLE_PARSE = Style(color="gold1")
_STYLE_PARSE_DIM = Style(color="grey74")
_STYLE_USER = Style(color="deep_sky_blue1")
_STYLE_BOLD = Style(bold=True)
_STYLE_ERROR = Style(color="red", bold=True)

_THINKING_HEADER = "\n  ── ◆ 思考 ◆ ──\n"

# ── 解析进度清除哨兵 ───────────────────────────────────
_CLEAR_PARSE_LINE = -1
_THINKING_SEPARATOR = "\n  " + "\u2500" * 40 + "\n"

# ── Reader 线程刷新间隔 ─────────────────────────────────
_READER_INTERVAL = 0.1  # 100ms = 10Hz


# ═══════════════════════════════════════════════════════════
# RenderCommand — 渲染命令枚举（IntEnum，类型安全 + 自文档化）
# ═══════════════════════════════════════════════════════════
class RenderCommand(IntEnum):
    """渲染命令类型，替代魔数整数。

    每个枚举值对应 _render() 分发的方法签名，
    值用于 _RENDER_DISPATCH 的 O(1) 字典查找。
    格式: (cmd_value, *args) — cmd_value 即枚举值。

    注意：值 3-5 为已废弃命令保留位（TOOL_STARTED/TOOL_DONE/
    PARSE_INFO_DONE），不重用以免产生歧义。
    """
    REASONING     = 0   # (0, text: str)
    CONTENT       = 1   # (1, text: str)
    PHASE_DONE    = 2   # (2, phase: str)
    TOOL_OUTPUT   = 6   # (6, text: str)
    TOOL_SUMMARY  = 7   # (7, successful: tuple, failed: tuple)
    USER_MSG      = 8   # (8, text: str)
    PARSE_INFO    = 9   # (9, tool_names: str, tokens: int, elapsed: float)
    CMD_OUTPUT    = 10  # (10, text: str)
    NOTIFICATION  = 11  # (11, text: str)
    WRITE_LINE    = 12  # (12, text: str)
    DISPLAY_MSGS  = 13  # (13, messages: list, speed: int)
    TOOL_COUNT_INC = 14  # (14,) — 工具计数+1
    TOOL_FAIL_INC  = 15  # (15,) — 工具失败计数+1
    ERROR          = 16  # (16, message: str) — 系统错误（红色 ◆ 样式）


# ═══════════════════════════════════════════════════════════
# _RenderState — 渲染器生命周期管理
# ═══════════════════════════════════════════════════════════

class _ReasoningState(Enum):
    """推理渲染器状态机，替代两个布尔值（thinking_header_printed + reasoning_closed）。

    状态转换：
      INACTIVE → 首个推理块到达 → ACTIVE（创建渲染器+打印标题）
      ACTIVE   → close_reasoning() → CLOSED（写入分隔线+关闭渲染器）
      INACTIVE → close_reasoning() → CLOSED（推理块从未到达即关闭）
      CLOSED   → reopen_reasoning() → INACTIVE（二次推理重新打开）
      CLOSED   → 其他转换不生效（幂等）
    """
    INACTIVE = "inactive"
    ACTIVE = "active"
    CLOSED = "closed"


@dataclass
class _RenderState:
    """管理推理/内容渲染器的创建、切换与关闭。

    集中在单一对象中管理所有渲染器状态，替代原来散落在 ChatUIConsumer
    中的多个 __rr/__cr/double-underscore 属性和 property getter/setter。
    每个 ChatUIConsumer 实例持有一个 _RenderState 实例。

    推理状态通过 _ReasoningState 三值枚举管理（INACTIVE/ACTIVE/CLOSED），
    替代旧版两个布尔值（thinking_header_printed + reasoning_closed），
    消除 4 种布尔组合中部分组合的歧义。
    """

    # ── 渲染器实例（None=未创建或已关闭） ──
    reasoning: "IncrementalRenderer | None" = None
    content: "IncrementalRenderer | None" = None

    # ── 推理状态机 ──
    reasoning_state: _ReasoningState = _ReasoningState.INACTIVE
    last_was_carriage: bool = False    # 上一行以 \r 结尾（进度条行内覆盖）

    # ── 工具输出适配器（延时初始化） ──
    _tool_adapter: "OutputAdapter | None" = None

    @staticmethod
    def _create_renderer(style: str = "") -> "IncrementalRenderer":
        """创建 IncrementalRenderer 实例。

        IncrementalRenderer 内部自行管理 Console 实例（走独立
        OutputAdapter + 全局 output_lock），与 _tool_adapter 各用
        各的 Console——两个渲染管线（流式 Markdown/工具输出）的
        宽度缓存独立刷新（5s TTL），写入串行化由 output_lock 保证。
        """
        from .api.renderer import IncrementalRenderer
        return IncrementalRenderer(
            style=style,
            _file=sys.__stdout__,
            typing_speed=1000,
            show_indicator=False,
        )

    def get_tool_adapter(self) -> "OutputAdapter":
        """获取或惰性创建工具输出适配器。"""
        if self._tool_adapter is None:
            from rich.console import Console
            from .terminal import get_safe_console_config
            console = Console(**get_safe_console_config(), file=sys.__stdout__)
            self._tool_adapter = OutputAdapter(console)
        return self._tool_adapter

    def get_reasoning(self) -> "IncrementalRenderer | None":
        """获取推理渲染器，惰性创建。

        状态机驱动：
        - INACTIVE → 创建渲染器 + 切换到 ACTIVE
        - ACTIVE   → 直接返回已有渲染器
        - CLOSED   → 返回 None（防止惰性重建）
        """
        if self.reasoning_state == _ReasoningState.CLOSED:
            return None
        if self.reasoning is None:
            self.reasoning = self._create_renderer(style="dim")
            self.reasoning_state = _ReasoningState.ACTIVE
        return self.reasoning

    def get_content(self) -> "IncrementalRenderer":
        """获取内容渲染器，惰性创建。"""
        if self.content is None:
            self.content = self._create_renderer()
        return self.content

    def close_reasoning(self) -> None:
        """关闭推理渲染器（写入分隔线后关闭）。幂等。"""
        if self.reasoning_state == _ReasoningState.CLOSED:
            return
        rr = self.reasoning
        if rr is not None:
            rr.write(_THINKING_SEPARATOR)
            rr.close()
            self.reasoning = None
        self.reasoning_state = _ReasoningState.CLOSED

    def reopen_reasoning(self) -> None:
        """重新打开推理渲染器，用于工具调用后的二次推理。

        将 CLOSED 状态重置为 INACTIVE，清除旧的渲染器引用，
        让后续推理内容重新走"创建渲染器 → 写标题 → 写内容"流程。
        幂等——已在 ACTIVE/INACTIVE 状态时无操作。
        """
        if self.reasoning_state != _ReasoningState.CLOSED:
            return
        self.reasoning = None
        self.reasoning_state = _ReasoningState.INACTIVE

    def close_content(self) -> None:
        """关闭内容渲染器。"""
        cr = self.content
        if cr is not None:
            cr.close()
            self.content = None

    def close_all(self) -> None:
        """关闭所有渲染器。"""
        self.close_reasoning()
        self.close_content()


# ═══════════════════════════════════════════════════════════
# _CmplHandler — Tab 补全交互逻辑
# ═══════════════════════════════════════════════════════════

class _CmplHandler:
    """Tab 补全交互处理器。

    由 EscapeMonitor 线程回调驱动，管理补全弹窗的
    首次激活、循环选择、关闭和上下键导航。

    与 CompletionEngine（纯计算型）分工：
      - CompletionEngine：计算补全候选项（命令/路径/参数）
      - _CmplHandler：管理补全 UI 交互流程（弹窗/循环/应用）
    """

    def __init__(self, bottom_bar: "_BottomBar", engine: "CompletionEngine"):
        self._bb = bottom_bar
        self._engine = engine

    def on_tab(self, text: str) -> str | None:
        """Tab 补全入口。

        补全弹窗已可见 → 循环到下一项。
        弹窗不可见 → 计算候选项，显示弹窗，返回首个匹配。
        """
        if self._bb.is_completion_visible:
            return self._cycle_tab(text)
        return self._first_tab(text)

    def on_dismiss(self) -> None:
        """关闭补全弹窗（ESC/非 Tab 按键触发）。"""
        self._bb.hide_completions()

    def on_navigate(self, delta: int, text: str) -> str | None:
        """上下键导航补全弹窗（delta: -1=上, +1=下）。

        text 参数由 EscapeMonitor 传入当前输入缓冲区文本，
        确保与 on_tab 使用同一来源的 text，消除 _last_text 过期风险。

        弹窗不可见时返回 None，EscapeMonitor 回退为正常上下键行为。
        """
        if not self._bb.is_completion_visible:
            return None
        self._bb.cycle_completion(delta)
        repl_text, start_pos, orig_prefix = self._bb.get_selected_completion()
        if not repl_text:
            return None
        return _apply_completion(
            text, repl_text, start_pos, orig_prefix,
        )

    # ── 内部方法 ──────────────────────────────────────

    def _cycle_tab(self, text: str) -> str | None:
        """已可见弹窗 → 循环到下一项。"""
        self._bb.cycle_completion(1)
        repl_text, start_pos, orig_prefix = self._bb.get_selected_completion()
        if not repl_text:
            return None
        return _apply_completion(text, repl_text, start_pos, orig_prefix)

    def _first_tab(self, text: str) -> str | None:
        """首次 Tab → 计算候选项，显示弹窗。"""
        items = self._engine.complete(text)
        if not items:
            self._bb.hide_completions()
            return None

        words = text.split()
        last_word = words[-1] if words else ""

        self._bb.show_completions(
            [item.display for item in items], 0,
            texts=[item.text for item in items],
            start_pos=items[0].start_pos,
            orig_prefix=last_word,
        )
        return _apply_completion(
            text, items[0].text, items[0].start_pos, last_word,
        )


def _apply_completion(
    text: str, repl_text: str, start_pos: int, orig_prefix: str,
) -> str:
    """将补全结果应用到输入文本（模块级纯函数）。

    三阶段定位 orig_prefix 的替换位置：
      1. rfind 全文搜索 — "最后一个匹配"语义天然对齐光标附近输入
      2. start_pos 裁剪回退 — 基于偏移量裁剪尾部后拼接
      3. 返回 repl_text — 兜底全替换
    """
    if orig_prefix:
        idx = text.rfind(orig_prefix)
        if idx >= 0:
            return text[:idx] + repl_text

    if start_pos < 0:
        trim_len = -start_pos
        if trim_len >= len(text):
            return repl_text
        return text[:len(text) - trim_len] + repl_text

    # start_pos > 0：保留供非 CompletionEngine 来源的调用（当前路径未触发）
    if start_pos > 0 and start_pos < len(text):
        return text[:start_pos] + repl_text
    return repl_text


# ── ChatUIConsumer 辅助 ───────────────────────────────

def _build_render_dispatch() -> dict[int, tuple[str, tuple[int, ...]]]:
    """构建渲染命令分发表（模块级函数，类定义时即初始化）。

    注：直接在类体内写字典字面量亦可，提取为独立函数仅为
    提升可读性——避免 ~20 行的字典字面量打断类属性声明区。
    """
    R = RenderCommand
    return {
        R.REASONING:      ("_do_reasoning",       (1,)),
        R.CONTENT:        ("_do_content",         (1,)),
        R.PHASE_DONE:     ("_do_phase_done",      (1,)),
        R.TOOL_OUTPUT:    ("_do_tool_output",     (1,)),
        R.TOOL_SUMMARY:   ("_do_tool_summary",    (1, 2)),
        R.USER_MSG:       ("_do_user_message",    (1,)),
        R.PARSE_INFO:     ("_do_parse_info",      (1, 2, 3)),
        R.CMD_OUTPUT:     ("_do_cmd_output",      (1,)),
        R.NOTIFICATION:   ("_do_notification",    (1,)),
        R.WRITE_LINE:     ("_do_write_line",      (1,)),
        R.DISPLAY_MSGS:   ("_do_display_messages", (1, 2)),
        R.TOOL_COUNT_INC: ("_do_tool_count_inc",  ()),
        R.TOOL_FAIL_INC:  ("_do_tool_fail_inc",   ()),
        R.ERROR:          ("_do_error",           (1,)),
    }


def _cmd_name(cid: int) -> str:
    """将 RenderCommand 枚举值转为可读命令名。

    返回枚举名的 `name` 属性（如 0→"REASONING"），
    未知 ID 时回退为字符串格式的整数值（如 "255"）。
    """
    try:
        return RenderCommand(cid).name
    except ValueError:
        return str(cid)


class ChatUIConsumer:
    """消费 DisplayEventBus 事件，通过渲染命令队列驱动终端输出。

    内部子系统：
      _rs     (_RenderState)   — 渲染器生命周期管理
      _cmpl   (_CmplHandler)   — Tab 补全交互

    Reader 线程以 10Hz 轮询命令队列，串行执行 _render()
    进行终端 I/O。事件 handler 只在 EventBus 回调线程中做过滤+入队。
    """

    # ── 渲染命令分发表（类级别，O(1) 查找） ──
    _RENDER_DISPATCH: ClassVar[dict[int, tuple[str, tuple[int, ...]]]] = _build_render_dispatch()

    # ── 事件处理器注册表（start/stop 复用） ──
    _EVENT_HANDLERS: tuple[tuple[type, str], ...] = (
        (ReasoningChunkEvent,    "_on_reasoning_chunk"),
        (ContentChunkEvent,      "_on_content_chunk"),
        (PhaseDoneEvent,         "_on_phase_done"),
        (ToolStartedEvent,       "_on_tool_started"),
        (ToolDoneEvent,          "_on_tool_done"),
        (ToolOutputChunkEvent,   "_on_tool_output"),
        (ToolSummaryEvent,       "_on_tool_summary"),
        (ParseInfoEvent,         "_on_parse_info"),
        (ParseInfoDoneEvent,     "_on_parse_info_done"),
        (ModelPhaseEvent,        "_on_model_phase"),
        (OutputEvent,            "_on_output"),
    )

    @staticmethod
    def _is_agent_source(source: str) -> bool:
        """判断事件来源是否与 Agent/SubAgent 相关。

        ChatUI 需要同时显示主 Agent 和 SubAgent 的工具调用状态：
        - 主 Agent 使用 source="agent"（_MAIN_SOURCE）
        - SubAgent 使用 source=self.label（例如 "agent-1", "agent-2"）

        返回 True 表示该来源应被 ChatUI 消费（工具计数/输出显示）。
        """
        return source == _MAIN_SOURCE or source.startswith("agent-")

    def __init__(self, event_bus: DisplayEventBus | None = None):
        self._bus = event_bus or DisplayEventBus.get_default()

        # ── 渲染命令队列（线程安全） ──
        self._cmd_queue: queue.Queue = queue.Queue()

        # ── Reader 线程 ──
        self._reader_thread: threading.Thread | None = None
        self._reader_running = False
        self._cmd_event = threading.Event()

        # ── 子系统 ──
        self._rs = _RenderState()          # 渲染器生命周期管理
        self._bottom_bar = _BottomBar()    # 终端底部固定输入栏
        self._cmpl = _CmplHandler(          # Tab 补全交互
            self._bottom_bar, CompletionEngine(),
        )

        # ★ P0 修复：预绑定所有事件处理器，确保 subscribe/unsubscribe
        #    使用同一 bound method 对象，消除 getattr() 每次创建新对象
        #    可能导致的 EventBus identity 比较失败→订阅泄漏问题。
        self._bound_handlers: dict[type, Any] = {
            event_type: getattr(self, handler_name)
            for event_type, handler_name in self._EVENT_HANDLERS
        }

        self._started = False

    # ═══════════════════════════════════════════════════════
    # 生命周期
    # ═══════════════════════════════════════════════════════

    def start(self) -> None:
        """订阅事件 + 启动 reader 线程。幂等。"""
        if self._started:
            return

        # ★ 先订阅事件处理器，再设置活跃标记
        #    顺序保障：在 _active_consumer 被外界可见前，ChatUIConsumer
        #    已完整订阅 EventBus，消除 OutputEvent 在过渡期丢失的竞态窗口。
        #    窗口期注意：subscribe 与 _active_consumer=self 之间存在微秒级窗口，
        #    OutputConsumer 在此时仍认为 ChatUI 未活跃而直写 stdout，导致该
        #    事件可能被 ChatUIConsumer 和 OutputConsumer 双重处理。但该窗口
        #    极窄（subscribe 到赋值之间数条语句），且 Reader 线程尚未启动，
        #    ChatUIConsumer 入队的命令在 reader 启动后才会渲染——双重处理
        #    时 OutputConsumer 已输出、ChatUIConsumer 待渲染，用户看到的
        #    是短暂的双重输出，不会丢失事件。权衡之下，消除事件丢失优先。
        # ★ 使用预绑定的 handler（self._bound_handlers），确保同一对象
        for event_type in self._bound_handlers:
            self._bus.subscribe(self._bound_handlers[event_type], event_type=event_type)

        global _active_consumer
        _active_consumer = self

        self._reader_running = True
        self._reader_thread = threading.Thread(target=self._reader, daemon=True)
        self._reader_thread.start()
        self._started = True

    def stop(self) -> None:
        """取消订阅 + 停止 reader + 关闭渲染器 + 拆除底部栏。幂等。"""
        if not self._started:
            return

        # 1) 先停 reader（与 suspend() 顺序一致）
        self._reader_running = False
        self._cmd_event.set()
        if self._reader_thread is not None:
            self._reader_thread.join(timeout=2.0)
            if not self._reader_thread.is_alive():
                self._reader_thread = None

        # 2) 先清除活跃标记，再取消订阅
        #    顺序保障：在取消事件订阅前先将 _active_consumer 置为 None，
        #    使 OutputConsumer 等降级路径在 ChatUIConsumer 仍处理事件
        #    的间隙正确接管输出，消除 OutputEvent 在过渡期丢失的竞态窗口
        global _active_consumer
        _active_consumer = None

        # 3) 取消订阅（reader 已停，不可能有新入队）
        # ★ 使用预绑定的 handler（self._bound_handlers），确保同一对象
        for event_type in self._bound_handlers:
            self._bus.unsubscribe(self._bound_handlers[event_type], event_type=event_type)

        # 4) flush 残留命令
        self.flush()

        # 5) teardown 底部栏（锁保护，与 suspend() 一致）
        with output_lock:
            self._bottom_bar.teardown()

        self._rs.close_all()
        self._started = False

    def suspend(self) -> None:
        """暂停渲染和终端设置，为交互式工具腾出终端。幂等。"""
        # ★ 先停 reader（flush 不会造任务阻塞在空队列上），再 flush 剩余命令
        if self._reader_running:
            self._reader_running = False
            self._cmd_event.set()
            if self._reader_thread is not None:
                self._reader_thread.join(timeout=2.0)
                if not self._reader_thread.is_alive():
                    self._reader_thread = None
        self.flush()
        # 确保 reader 线程已完全退出后再拆卸底部栏
        if self._reader_thread is not None and self._reader_thread.is_alive():
            self._reader_thread.join(timeout=0.1)
            if not self._reader_thread.is_alive():
                self._reader_thread = None
        with output_lock:
            self._bottom_bar.teardown()

    def resume(self) -> None:
        """恢复渲染和终端设置。仅在已 start() 但 reader 已停止时有效。"""
        if not self._started:
            return

        with output_lock:
            height = shutil.get_terminal_size().lines
            sys.__stdout__.write(f"\033[{height};1H")
            sys.__stdout__.flush()
            self._bottom_bar.setup()
            # 无条件恢复运行标记：join 超时后旧线程虽未结束，但 _reader_running=True
            # 使其继续运行；旧线程已结束时则创建新线程
            self._reader_running = True
            if self._reader_thread is None or not self._reader_thread.is_alive():
                self._reader_thread = threading.Thread(target=self._reader, daemon=True)
                self._reader_thread.start()

    # ═══════════════════════════════════════════════════════
    # 公开方法（线程安全：仅入队，不直接 I/O）
    # ═══════════════════════════════════════════════════════

    def on_user_message(self, text: str) -> None:
        """入队用户消息渲染命令。"""
        self._push_cmd((RenderCommand.USER_MSG, text))

    def on_notification(self, text: str) -> None:
        """入队系统通知渲染命令。"""
        self._push_cmd((RenderCommand.NOTIFICATION, text))

    def on_error(self, message: str) -> None:
        """入队系统错误渲染命令（红色 ◆ 样式）。

        由 ChatUIErrorHandler 在捕获 ERROR+ 级别日志时调用，
        也可由其他模块直接调用以显示运行时错误信息。

        线程安全：仅入队，不直接 I/O。
        """
        if not message:
            return
        self._push_cmd((RenderCommand.ERROR, message))

    def refresh(self) -> None:
        """公开刷新接口 — 供外部程序/timer 定时调用以刷新 TUI。

        安全地从任何线程调用：自行管理 output_lock 获取与释放。
        执行以下刷新操作：
          1. ParallelDisplay 面板刷新（若有活跃实例）
          2. 终端尺寸检测（check_resize）
          3. 底部栏重绘（force_redraw）
          4. 光标定位（_position_cursor）

        与 _drain_queue 不同：不消费命令队列，专供外部定时刷新。
        ParallelDisplay 面板刷新不持锁（内部自行用 try-lock 保护），
        尺寸检测与底部栏重绘用独立 output_lock 分步串行化。
        """
        # ★ 1. ParallelDisplay 面板刷新（无锁，内部自行用 timeout try-lock）
        pd = _active_parallel_display
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug("refresh: ParallelDisplay 刷新异常", exc_info=True)
                self._push_cmd((RenderCommand.ERROR, "ParallelDisplay 刷新失败，请查看日志获取详情"))

        # ★ 2. 终端尺寸检测
        with _try_acquire_output_lock(name="refresh.resize", timeout=1.0) as locked:
            resized = locked and self._bottom_bar.check_resize()

        # ★ 3. 底部栏重绘 + 光标定位（有活跃状态或尺寸变化时执行）
        if resized or self._bottom_bar.is_status_active:
            with _try_acquire_output_lock(name="refresh.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._position_cursor()

    def write_line(self, text: str) -> None:
        """入队通用文本行渲染命令，走统一渲染管线。"""
        self._push_cmd((RenderCommand.WRITE_LINE, text))

    def display_messages(self, messages: list[dict], speed: int = 0) -> None:
        """入队消息列表渲染命令。"""
        self._push_cmd((RenderCommand.DISPLAY_MSGS, messages, speed))

    def wait_for_user_input(
        self, monitor: "EscapeMonitor", prefill: str = "",
        timeout: float | None = None,
    ) -> str:
        """通过底部栏等待用户输入（阻塞同步调用）。

        参数:
            timeout: 超时秒数，None 表示无限等待。
                     超时后返回空字符串，避免 EscapeMonitor 故障时永久阻塞。
        """
        if prefill:
            monitor.set_prefill(prefill)
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            text = monitor.get_queued_input()
            if text is not None:
                return text
            if deadline is not None and time.monotonic() >= deadline:
                return ""
            time.sleep(0.05)

    # ── Tab 补全 ───────────────────────────────────────

    def setup_completion(self, monitor: "EscapeMonitor") -> None:
        """注册补全回调到 EscapeMonitor。"""
        monitor.set_completion_callback(self._cmpl.on_tab)
        monitor.set_dismiss_completion_callback(self._cmpl.on_dismiss)
        monitor.set_completion_navigate_callback(self._cmpl.on_navigate)

    # ── 底部栏 ────────────────────────────────────────

    def setup_bottom_bar(self) -> None:
        with output_lock:
            self._bottom_bar.setup()

    def teardown_bottom_bar(self) -> None:
        self._bottom_bar.teardown()

    def ensure_cursor_upper(self) -> None:
        """将光标移到内容区。调用方须持有 output_lock。"""
        self._bottom_bar.ensure_cursor_in_upper()

    def ensure_cursor_lower(self) -> None:
        """将光标移到输入行。调用方须持有 output_lock。"""
        self._bottom_bar.ensure_cursor_in_lower()

    def refresh_bottom_bar(self, text: str, cursor_pos: int = -1) -> None:
        self._bottom_bar.refresh(text, cursor_pos=cursor_pos)

    def redraw_bottom_bar(self) -> None:
        self._bottom_bar.redraw()

    def enable_status_refresh(self) -> None:
        self._bottom_bar.enable_status()

    def disable_status_refresh(self) -> None:
        self._bottom_bar.disable_status()

    def get_status_elapsed(self) -> float:
        return self._bottom_bar.get_status_elapsed()

    def reset_tool_count(self) -> None:
        self._bottom_bar.reset_tool_count()

    def set_model_name(self, name: str) -> None:
        """设置当前模型名字，更新底部栏状态行。"""
        self._bottom_bar.set_model_name(name)

    def _push_cmd(self, cmd: tuple) -> None:
        self._cmd_queue.put(cmd)
        self._cmd_event.set()

    # ═══════════════════════════════════════════════════════
    # 事件处理（EventBus 回调线程 → 仅过滤+入队）
    # ═══════════════════════════════════════════════════════

    def _on_reasoning_chunk(self, event: DisplayEvent) -> None:
        if not isinstance(event, ReasoningChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.REASONING, event.text))

    def _on_content_chunk(self, event: DisplayEvent) -> None:
        if not isinstance(event, ContentChunkEvent):
            return
        if event.label != _MAIN_LABEL or not event.text:
            return
        self._push_cmd((RenderCommand.CONTENT, event.text))

    def _on_phase_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, PhaseDoneEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        self._push_cmd((RenderCommand.PHASE_DONE, event.phase))

    def _on_tool_started(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolStartedEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.TOOL_COUNT_INC,))

    def _on_tool_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.success:
            self._push_cmd((RenderCommand.TOOL_FAIL_INC,))

    def _on_tool_output(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolOutputChunkEvent):
            return
        if not self._is_agent_source(event.source):
            return
        text = event.text.rstrip("\n")
        if text:
            self._push_cmd((RenderCommand.TOOL_OUTPUT, text))

    def _on_parse_info(self, event: DisplayEvent) -> None:
        if not isinstance(event, ParseInfoEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, event.tool_names, event.tokens, event.elapsed))

    def _on_parse_info_done(self, event: DisplayEvent) -> None:
        if not isinstance(event, ParseInfoDoneEvent):
            return
        if not self._is_agent_source(event.source):
            return
        self._push_cmd((RenderCommand.PARSE_INFO, "", _CLEAR_PARSE_LINE, 0.0))

    def _on_output(self, event: DisplayEvent) -> None:
        if not isinstance(event, OutputEvent):
            return
        if not event.text:
            return
        if event.source == "cmd":
            self._push_cmd((RenderCommand.CMD_OUTPUT, event.text))
        else:
            self._push_cmd((RenderCommand.WRITE_LINE, event.text))

    def _on_model_phase(self, event: DisplayEvent) -> None:
        """处理模型阶段变更事件，phase="error" 时渲染错误到上屏。

        拦截 ModelPhaseEvent 中的 phase="error" 事件，
        将错误消息通过 RenderCommand.ERROR 管道渲染为红色 [警告] 样式。

        过滤条件（四条件 AND）：
        1. isinstance 类型守卫
        2. label == _MAIN_LABEL（仅主 Agent，SubAgent 跳过）
        3. phase == "error"（非 error phase 跳过）
        4. info 非空（空消息跳过）
        """
        if not isinstance(event, ModelPhaseEvent):
            return
        if event.label != _MAIN_LABEL:
            return
        if event.phase != "error":
            return
        if not event.info:
            return

        # 截断超长 info 防止终端溢出
        _MAX_ERROR_LENGTH = 200
        info = (
            event.info[:_MAX_ERROR_LENGTH] + "..."
            if len(event.info) > _MAX_ERROR_LENGTH
            else event.info
        )
        self._push_cmd((RenderCommand.ERROR, info))

    def _on_tool_summary(self, event: DisplayEvent) -> None:
        if not isinstance(event, ToolSummaryEvent):
            return
        if not self._is_agent_source(event.source):
            return
        if not event.successful_tools and not event.failed_tools:
            return
        self._push_cmd((RenderCommand.TOOL_SUMMARY, event.successful_tools, event.failed_tools))

    # ═══════════════════════════════════════════════════════
    # Reader 线程（10Hz 消费循环）
    # ═══════════════════════════════════════════════════════

    def _reader(self) -> None:
        """Reader 线程入口。"""
        while self._reader_running:
            self._drain_queue()
            self._cmd_event.wait(timeout=_READER_INTERVAL)
            self._cmd_event.clear()

    def _drain_queue(self) -> None:
        """消费所有待处理渲染命令，执行上屏渲染 + 底部栏重绘。

        四阶段流水线：
          0. 尺寸检测（1s 超时，超时则跳过本轮尺寸检测）
          1. 上屏渲染（1s 超时，在锁内出队+渲染，避免命令丢失）
          2. ParallelDisplay 面板刷新（上屏渲染完成后立即刷新面板状态）
          3. 底部栏重绘 + 光标定位（1s 超时，超时则跳过本轮重绘）

        ParallelDisplay 刷新置于渲染阶段之后：先渲染上屏内容（工具输出/摘要等），
        再刷新 SubAgent UI 面板展示最新状态，确保面板状态与已渲染内容同步。
        """
        # ★ 尺寸检测：持锁调用以与 refresh()/force_redraw() 串行化
        with _try_acquire_output_lock(name="drain_queue.resize", timeout=1.0) as locked:
            resized = locked and self._bottom_bar.check_resize()

        # ★ 阶段 1：锁内批量出队 + 上屏渲染（出队与渲染原子化，消除命令丢失窗口）
        commands: list[tuple] = []
        with _try_acquire_output_lock(name="drain_queue.render", timeout=1.0) as locked:
            if locked:
                while True:
                    try:
                        commands.append(self._cmd_queue.get_nowait())
                        self._cmd_queue.task_done()
                    except queue.Empty:
                        break
                if commands:
                    self.ensure_cursor_upper()
                    for cmd in commands:
                        try:
                            self._render(cmd)
                        except Exception:
                            _logger.debug(
                                "drain_queue: 渲染命令 %s 失败", cmd,
                                exc_info=True,
                            )
                            self._push_cmd((
                                RenderCommand.ERROR,
                                f"渲染命令 {_cmd_name(cmd[0])} 失败，请查看日志获取详情",
                            ))
                    sys.__stdout__.flush()

        # ★ 阶段 2：ParallelDisplay 面板刷新（无锁，render_frame 内部用
        #   timeout try-lock 保护终端 I/O）。
        #   顺序说明：上屏渲染完成后立即刷新面板，确保 SubAgent 状态面板
        #   反映的是最新执行结果，不与底部栏重绘交错。
        pd = _active_parallel_display
        if pd is not None:
            try:
                pd.refresh()
            except Exception:
                _logger.debug(
                    "drain_queue: ParallelDisplay 刷新异常",
                    exc_info=True,
                )
                self._push_cmd((
                    RenderCommand.ERROR,
                    "drain_queue: ParallelDisplay 刷新失败，请查看日志获取详情",
                ))

        # ★ 阶段 3：底部栏重绘 + 光标定位
        # 分流策略：
        #   - 有命令/尺寸变化 → 全量重绘（force_redraw）+ 光标定位（锁内原子）
        #   - 仅流式活跃（无命令/无尺寸变化）→ 增量状态行刷新 + 光标定位
        #   流式期间 10Hz drain 中约 70%+ 的周期无新命令到达，免去全量
        #   底部栏（分隔线+输入区）重绘，将 I/O 从 ~5 行降至 0-1 行。
        #   _position_cursor() 在所有底部栏活跃路径中均执行，确保：
        #   - 流式期间 refresh_status_only 的 \0338 恢复后光标仍正确回位到输入区
        #   - 流式结束后首轮 drain 光标不会停滞在上屏内容区
        if commands or resized:
            with _try_acquire_output_lock(name="drain_queue.bottom", timeout=1.0) as locked:
                if locked:
                    self._bottom_bar.force_redraw()
                    self._position_cursor()
        elif self._bottom_bar.is_status_active:
            self._bottom_bar.refresh_status_only()
            self._position_cursor()

    def _position_cursor(self) -> None:
        """光标移回输入行，根据超长文本自动拆行定位（含最少3行输入区）。"""
        text, cursor_pos, h, w = self._bottom_bar.get_cursor_info()
        max_input = max(1, w - 4)

        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total_bottom = self._bottom_bar._bottom_lines
        r_cursor = max(1, h - total_bottom + 3 + vis_row)
        cursor_col = min(3 + vis_col, w)
        sys.__stdout__.write(f"\033[{r_cursor};{cursor_col}H")
        sys.__stdout__.flush()

    def flush(self, timeout: float = 5.0) -> None:
        """阻塞等待所有待处理渲染命令执行完毕。

        Reader 未运行时直接清空队列（无人消费，等待无意义），
        Reader 运行时创建临时 daemon 线程消费 queue.join() 等待。

        参数:
            timeout: 最大等待秒数，超时后返回。默认 5 秒，None 表示无限等待。
        """
        self._cmd_event.set()
        if self._reader_thread is None:
            # Reader 线程从未启动或已终止；直接清空队列避免虚假等待
            while not self._cmd_queue.empty():
                try:
                    self._cmd_queue.get_nowait()
                    self._cmd_queue.task_done()
                except queue.Empty:
                    break
            return
        # Reader 线程存在（可能仍在运行）；通过 queue.join() 等待消费完毕
        task_done = threading.Thread(
            target=self._cmd_queue.join, daemon=True,
        )
        task_done.start()
        task_done.join(timeout=timeout)

    # ═══════════════════════════════════════════════════════
    # 渲染分发（仅在 reader 线程中执行）
    # ═══════════════════════════════════════════════════════

    def _render(self, cmd: tuple) -> None:
        """根据命令类型分发到对应渲染方法（O(1) 字典查找）。"""
        cid = cmd[0]

        entry = self._RENDER_DISPATCH.get(cid)
        if entry is None:
            self._push_cmd((RenderCommand.ERROR, f"未知渲染命令: {_cmd_name(cid)}"))
            return

        method_name, arg_indices = entry
        method = getattr(self, method_name)
        args = tuple(cmd[i] for i in arg_indices)
        method(*args)

    # ── 渲染器访问（委托 _RenderState） ───────────────

    @property
    def _tool_adapter(self) -> OutputAdapter:
        return self._rs.get_tool_adapter()

    # ── 内容渲染 ──────────────────────────────────────

    def _do_reasoning(self, text: str) -> None:
        """渲染推理内容块。

        状态机驱动：
        - CLOSED → 重新打开推理渲染器（reopen_reasoning），
                    让工具调用后的二次推理能正常显示
        - INACTIVE → 惰性创建渲染器 + 打印思考标题 + 写入内容
        - ACTIVE → 直接写入内容
        """
        if self._rs.reasoning_state == _ReasoningState.CLOSED:
            self._rs.reopen_reasoning()
        # ★ 在 get_reasoning() 调用前保存 INACTIVE 标记，因为 get_reasoning()
        #    内部会将状态从 INACTIVE 切换为 ACTIVE
        is_first = self._rs.reasoning_state == _ReasoningState.INACTIVE
        rr = self._rs.get_reasoning()
        if rr is not None:
            if is_first:
                rr.write(_THINKING_HEADER)
            rr.write(text)

    def _do_content(self, text: str) -> None:
        if self._rs.reasoning_state not in (_ReasoningState.CLOSED, _ReasoningState.INACTIVE):
            self._rs.close_reasoning()
        self._rs.get_content().write(text)

    def _do_phase_done(self, phase: str) -> None:
        if phase == "reasoning":
            self._rs.close_reasoning()
        elif phase == "content":
            self._rs.close_content()

    # ── 工具渲染 ──────────────────────────────────────

    def _do_tool_count_inc(self) -> None:
        """通过命令队列入队的工具计数+1，Reader 线程串行执行。"""
        self._bottom_bar.increment_tool()

    def _do_tool_fail_inc(self) -> None:
        """通过命令队列入队的工具失败计数+1，Reader 线程串行执行。"""
        self._bottom_bar.increment_tool_fail()

    def _do_tool_output(self, text: str) -> None:
        """渲染工具执行输出（dim 样式 + 左侧竖线指示）。"""
        ta = self._tool_adapter
        if '\r' in text:
            ta.write_raw(text)
            # text 已在 _on_tool_output handler 中 rstrip('\n')，直接判断
            if text.endswith('\r'):
                self._rs.last_was_carriage = True
            else:
                # 含 \r 但不以 \r 结尾：write_raw 后光标不换行，
                # 补 \n 换行使后续 styled 输出正确在新行显示
                ta.write_raw('\n')
                self._rs.last_was_carriage = False
        else:
            if self._rs.last_was_carriage:
                ta.write_raw("\n")
                self._rs.last_was_carriage = False
            ta.write(Text.assemble(("  │ ", _STYLE_DIM_GREY), (text, _STYLE_DIM)))

    def _do_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """渲染工具执行汇总（着色图标 + 彩色计数）。"""
        ta = self._tool_adapter
        if self._rs.last_was_carriage:
            ta.write_raw("\n")
            self._rs.last_was_carriage = False

        total = len(successful) + len(failed)
        if failed:
            self._render_failure_summary(ta, failed, total)
        elif successful:
            ta.write(Text.assemble(
                ("  ● ", _STYLE_SUCCESS),
                (f"{len(successful)}个工具完成", _STYLE_SUCCESS),
            ))

    @staticmethod
    def _truncate_by_visual_width(s: str, max_width: int) -> str:
        """按终端列宽截断，保留的尾部替换为省略号。

        使用 wcswidth 计算视觉宽度（中文=2，英文=1），
        超过 max_width 时在截断位置前插入"..."。
        """
        if not s:
            return s
        w = 0
        cut = len(s)
        for i, ch in enumerate(s):
            cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
            if w + cw > max_width - 3:
                cut = i
                break
            w += cw
        if cut < len(s):
            return s[:cut] + "..."
        return s

    @classmethod
    def _render_failure_summary(cls, ta: OutputAdapter, failed: tuple, total: int) -> None:
        """渲染失败工具汇总行 + 失败详情（最多 3 条）。"""
        failed_names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            ta.write(Text.assemble(
                ("  ◆ ", _STYLE_FAIL),
                (f"全部失败: {failed_names}", _STYLE_FAIL),
            ))
        else:
            ta.write(Text.assemble(
                ("  ◆ ", _STYLE_WARN),
                (f"{len(failed)}/{total} 失败: {failed_names}", _STYLE_WARN),
            ))

        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    short = cls._truncate_by_visual_width(short, 80)
            ta.write(Text.assemble(
                (f"    {name}", _STYLE_DIM_GREY),
                (f": {short}", _STYLE_DIM) if short else ("", _STYLE_DIM),
            ))
        if len(failed) > 3:
            ta.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", _STYLE_DIM_GREY),
            ))

    def _do_parse_info(self, tool_names: str, tokens: int, elapsed: float) -> None:
        """渲染工具参数接收进度（行内覆盖）。

        使用 _CLEAR_PARSE_LINE（-1）作为清除哨兵——tokens < 0 时清除进度行。
        """
        if tokens == _CLEAR_PARSE_LINE:
            self._tool_adapter.write_raw("\n")
            return
        self._tool_adapter.write_raw(
            f"\r\033[K  \u25c7 {tool_names} {tokens}t {elapsed:.2f}s",
        )

    def _do_cmd_output(self, text: str) -> None:
        """渲染 / 命令执行输出，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

    def _do_user_message(self, text: str) -> None:
        """渲染用户消息（青色 ▸ 前缀 + 粗体）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ▸ ", _STYLE_USER),
            (text, _STYLE_BOLD),
        ))

    def _do_notification(self, text: str) -> None:
        """渲染系统通知（绿色 ● 前缀）。"""
        self._tool_adapter.write(Text.assemble(
            ("\n  ● ", _STYLE_SUCCESS),
            (text, _STYLE_SUCCESS),
        ))

    def _do_error(self, message: str) -> None:
        """渲染系统错误信息（红色 ◆ 样式）。

        由 RenderCommand.ERROR 命令触发（Reader 线程串行执行），
        通过 _tool_adapter.write() 输出到终端内容区。

        注意：此方法不应产生任何日志调用。若意外触发 logger，
        ChatUIErrorHandler.emit() 中的 _chatui_reported 标记
        会跳过自引用循环。
        """
        self._tool_adapter.write(Text.assemble(
            ("\n  ◆ ", _STYLE_ERROR),
            (message, _STYLE_ERROR),
        ))

    def _do_write_line(self, text: str) -> None:
        """渲染通用文本行，委托 _write_text_or_ansi。"""
        self._write_text_or_ansi(text)

    def _write_text_or_ansi(self, text: str) -> None:
        """按需渲染文本：含 ANSI 转义序列时解析着色，纯文本时直写。

        ANSI 路径：Text.from_ansi 解析 → OutputAdapter.write() → console.print 保证末尾换行。
        纯文本路径：write_raw 直写终端，显式追加 \\n，绕过 Rich 解析开销。
        注：检测 '\033[' 覆盖本项目所有 CSI 序列，非 CSI 转义序列（OSC/DEC 等）未使用。
        """
        if '\033[' in text:
            self._tool_adapter.write(Text.from_ansi(text))
        else:
            self._tool_adapter.write_raw(text + "\n")

    def _do_display_messages(self, messages: list[dict], speed: int) -> None:
        """渲染消息列表到上屏（截断/恢复后的重渲染）。"""
        from .ui.tui._message_display import _display_messages
        _display_messages(messages, speed=speed)
