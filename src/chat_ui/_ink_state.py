"""InkState — React Ink-like 状态容器 + 命令分发。

Layer 0 — 独立于渲染管线，被 _engine / _renderer / _consumer 平等引用。
提供线程安全的命令→状态转换映射，InkState 实例作为渲染状态的单一可信源。

设计原则：
  - 纯 Python，零外部依赖
  - dataclass 声明式字段定义
  - threading.Lock 保护所有状态更新
  - 命令格式与 TuiEngine.push_cmd 兼容：(cid: int, *args)
  - 每个 _on_* 方法返回变更字段名，用于触发重渲染
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

from ._const import RenderCommand, _CLEAR_PARSE_LINE

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 命令处理映射
# ═══════════════════════════════════════════════════════════

_CMD_HANDLERS: dict[int, tuple[str, str]] = {
    RenderCommand.REASONING:       ("_on_reasoning",        "reasoning_text"),
    RenderCommand.CONTENT:         ("_on_content",          "content_text"),
    RenderCommand.PHASE_DONE:      ("_on_phase_done",       "phase"),
    RenderCommand.TOOL_OUTPUT:     ("_on_tool_output",      "tool_outputs"),
    RenderCommand.TOOL_SUMMARY:    ("_on_tool_summary",     "tool_summary"),
    RenderCommand.USER_MSG:        ("_on_user_message",     "user_message"),
    RenderCommand.PARSE_INFO:      ("_on_parse_info",       "parse_info"),
    RenderCommand.NOTIFICATION:    ("_on_notification",     "notifications"),
    RenderCommand.WRITE_LINE:      ("_on_write_line",       "write_lines"),
    RenderCommand.DISPLAY_MSGS:    ("_on_display_messages", "display_messages"),
    RenderCommand.TOOL_COUNT_INC:  ("_on_tool_count_inc",   "tool_count"),
    RenderCommand.TOOL_FAIL_INC:   ("_on_tool_fail_inc",    "tool_fail_count"),
    RenderCommand.ERROR:           ("_on_error",            "errors"),
    RenderCommand.TOOL_COUNT_DEC:  ("_on_tool_count_dec",   "tool_count"),
    RenderCommand.SUBAGENT_FRAME:  ("_on_subagent_frame",   "subagent_frame"),
}


# ═══════════════════════════════════════════════════════════
# InkState — 渲染状态容器
# ═══════════════════════════════════════════════════════════

@dataclass
class InkState:
    """React Ink-like 渲染状态容器。

    聚合所有流式渲染相关的可变状态，通过命令分发机制驱动状态变更。
    所有字段更新均在 threading.Lock 保护下执行，保证多线程安全。

    Attributes:
        reasoning_text: 推理流累积文本
        content_text: 内容流累积文本
        tool_outputs: 工具输出列表（最新的在前）
        notifications: 通知消息列表
        errors: 错误消息列表
        user_message: 当前用户消息
        phase: 当前阶段名（如 "reasoning", "answering"）
        tool_summary_successful: 工具成功汇总
        tool_summary_failed: 工具失败汇总
        tool_count: 活跃工具计数
        tool_fail_count: 失败工具计数
        parse_info: 解析进度信息
        subagent_frame: SubAgent 帧数据
        display_messages: 历史消息数据
        write_lines: 直接写入的文本行列表
        command_queue: 待处理命令队列
        _version: 单调递增版本号（每次状态变更 +1）
    """

    reasoning_text: str = ""
    content_text: str = ""
    tool_outputs: list[str] = field(default_factory=list)
    notifications: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    user_message: str = ""
    phase: str = ""
    tool_summary_successful: tuple = ()
    tool_summary_failed: tuple = ()
    tool_count: int = 0
    tool_fail_count: int = 0
    parse_info: str = ""
    subagent_frame: tuple = ()
    display_messages: tuple = ()
    write_lines: list[str] = field(default_factory=list)
    command_queue: list = field(default_factory=list)
    _version: int = 1

    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    # ── 批量命令处理 ──────────────────────────────────

    def apply_commands(self, commands: list[tuple]) -> list[str]:
        """批量处理命令列表，返回变更的字段名列表（去重，用于触发重渲染）。

        线程安全：所有命令在同一锁持有期间顺序执行，保证原子批量更新。

        Args:
            commands: 命令元组列表，每项格式为 (cid: int, *args)

        Returns:
            去重后的变更字段名列表
        """
        changed: set[str] = set()
        with self._lock:
            for cmd in commands:
                name = self._apply_command_unlocked(cmd)
                if name is not None:
                    changed.add(name)
        return sorted(changed)

    def apply_command(self, cmd: tuple) -> str | None:
        """处理单个命令，返回变更的字段名或 None。

        线程安全：整个处理过程在锁保护下执行。

        Args:
            cmd: 命令元组，格式为 (cid: int, *args)

        Returns:
            变更的字段名（str），无变更或未知命令时返回 None
        """
        with self._lock:
            return self._apply_command_unlocked(cmd)

    # ── 内部命令分发（调用方已持锁） ──────────────────

    def _apply_command_unlocked(self, cmd: tuple) -> str | None:
        """处理单个命令（无锁版本 — 调用方必须已持有 self._lock）。

        Args:
            cmd: 命令元组，格式为 (cid: int, *args)

        Returns:
            变更的字段名，未知命令返回 None
        """
        if not cmd:
            return None
        cid = cmd[0]
        entry = _CMD_HANDLERS.get(cid)
        if entry is None:
            _logger.debug("InkState: 未知命令 %s", cid)
            return None
        method_name, _field_name = entry
        handler = getattr(self, method_name)
        try:
            args = cmd[1:]
            handler(*args)
        except TypeError:
            _logger.debug(
                "InkState.%s 参数不匹配: cmd=%s", method_name, cmd, exc_info=True,
            )
            return None
        except Exception:
            _logger.debug(
                "InkState.%s 执行异常: cmd=%s", method_name, cmd, exc_info=True,
            )
            return None
        self._version += 1
        return _field_name

    # ── 流式清除 / 全局重置 ──────────────────────────

    def clear_streaming(self) -> None:
        """清除流式内容（reasoning_text, content_text, tool_outputs）。

        用于一轮对话开始前重置流式累积状态。
        """
        with self._lock:
            self.reasoning_text = ""
            self.content_text = ""
            self.tool_outputs.clear()
            self._version += 1

    def reset(self) -> None:
        """重置所有字段为默认值。

        用于会话完全重置场景，将 InkState 恢复到初始状态。
        """
        with self._lock:
            self.reasoning_text = ""
            self.content_text = ""
            self.tool_outputs.clear()
            self.notifications.clear()
            self.errors.clear()
            self.user_message = ""
            self.phase = ""
            self.tool_summary_successful = ()
            self.tool_summary_failed = ()
            self.tool_count = 0
            self.tool_fail_count = 0
            self.parse_info = ""
            self.subagent_frame = ()
            self.display_messages = ()
            self.write_lines.clear()
            self.command_queue.clear()
            self._version = 1

    # ── 命令处理方法 ─────────────────────────────────

    def _on_reasoning(self, text: str) -> None:
        """累积推理文本。

        Args:
            text: 待追加的推理文本片段
        """
        self.reasoning_text += text

    def _on_content(self, text: str) -> None:
        """累积内容文本。

        Args:
            text: 待追加的内容文本片段
        """
        self.content_text += text

    def _on_phase_done(self, phase: str) -> None:
        """设置当前阶段名。

        Args:
            phase: 阶段名称（如 "reasoning", "answering"）
        """
        self.phase = phase

    def _on_tool_output(self, text: str) -> None:
        """插入工具输出到列表头部（最新的在前）。

        Args:
            text: 工具输出文本
        """
        self.tool_outputs.insert(0, text)

    def _on_tool_summary(self, successful: tuple, failed: tuple) -> None:
        """设置工具执行汇总。

        Args:
            successful: 成功工具名/元信息的元组
            failed: 失败工具名/错误信息的元组
        """
        self.tool_summary_successful = successful
        self.tool_summary_failed = failed

    def _on_user_message(self, text: str) -> None:
        """设置当前用户消息。

        Args:
            text: 用户消息文本
        """
        self.user_message = text

    def _on_parse_info(self, tool_names: str, tokens: int, elapsed: float) -> None:
        """格式化解析进度信息。

        Args:
            tool_names: 工具名描述（传入 _CLEAR_PARSE_LINE 时清空 parse_info）
            tokens: token 计数
            elapsed: 耗时（秒）
        """
        if tokens == _CLEAR_PARSE_LINE:
            self.parse_info = ""
            return
        self.parse_info = f"{tool_names} {tokens}t {elapsed:.2f}s"

    def _on_notification(self, text: str) -> None:
        """追加通知消息。

        Args:
            text: 通知文本
        """
        self.notifications.append(text)

    def _on_write_line(self, text: str) -> None:
        """追加直接写入的文本行。

        Args:
            text: 待追加的单行文本
        """
        self.write_lines.append(text)

    def _on_display_messages(self, messages: list, speed: int) -> None:
        """设置历史消息数据。

        Args:
            messages: 消息列表
            speed: 显示速度
        """
        self.display_messages = (tuple(messages), speed)

    def _on_tool_count_inc(self) -> None:
        """活跃工具计数 +1。"""
        self.tool_count += 1

    def _on_tool_fail_inc(self) -> None:
        """失败工具计数 +1。"""
        self.tool_fail_count += 1

    def _on_error(self, message: str) -> None:
        """追加错误消息。

        Args:
            message: 错误描述文本
        """
        self.errors.append(message)

    def _on_tool_count_dec(self) -> None:
        """活跃工具计数 -1（下限为 0）。"""
        if self.tool_count > 0:
            self.tool_count -= 1

    def _on_subagent_frame(self, frame_lines: tuple) -> None:
        """设置 SubAgent 帧数据。

        Args:
            frame_lines: SubAgent 面板帧行数据
        """
        self.subagent_frame = frame_lines
