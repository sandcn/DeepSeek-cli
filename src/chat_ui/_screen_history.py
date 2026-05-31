"""上屏历史管理器 — 记录终端渲染历史并在 resize 后重放。

提取自 _renderers.py ContentRenderer，将上屏历史记录 + 累积缓冲区
管理封装为独立职责类 ScreenHistoryManager。

职责：
  - 记录所有上屏渲染命令到 _history 列表
  - 推理/内容文本通过累积缓冲区在阶段边界合并写入单条记录
  - 终端 resize 后遍历历史重绘上屏内容

Layer 2 — 依赖 _const（Style 常量）+ 可选 display_messages 回调。
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Callable

from rich.text import Text

if TYPE_CHECKING:
    from ..api.renderer.output import OutputAdapter

# ── 上屏历史最大记录数 ─────────────────────────────
_MAX_HISTORY = 10000
"""上屏历史记录最多保留 10000 条，防止极长会话内存无限增长。"""


class ScreenHistoryManager:
    """上屏历史记录管理器。

    记录所有上屏渲染命令的历史，供终端 resize 后重新绘制上屏内容。
    推理/内容文本通过累积缓冲区在阶段边界写入单条记录，
    避免逐块记录导致的重放碎片化。

    使用方式（由 ContentRenderer 组合）：
      self._shm = ScreenHistoryManager(...)
      self._shm.append_reasoning(text)    # → 累积到 _reasoning_accum
      self._shm.append_content(text)      # → 累积到 _content_accum
      self._shm.flush_reasoning()         # → 合并写入 _history
      self._shm.flush_content()           # → 合并写入 _history
      self._shm.record(kind, *args)       # → 直接记录非累积内容
      self._shm.replay(tool_adapter, ...) # → resize 后重放
    """

    def __init__(
        self,
        on_display_messages: Callable[[list, int], None] | None = None,
    ):
        """初始化上屏历史管理器。

        Args:
            on_display_messages: 可选回调，重放 display_msgs 记录时调用。
                                 由 ContentRenderer 在构造时传入，
                                 消除 replay_upper_screen 对 tui._message_display
                                 的直接 import 依赖。
        """
        self._history: list[tuple] = []
        self._reasoning_accum: list[str] = []
        self._content_accum: list[str] = []
        self._on_display_messages = on_display_messages

    # ── 公开属性 ────────────────────────────────────

    @property
    def screen_history(self) -> list[tuple]:
        """上屏历史记录列表。"""
        return self._history

    @property
    def on_display_messages(self) -> Callable[..., None] | None:
        """display_messages 回调（由 ContentRenderer 注入时设置）。"""
        return self._on_display_messages


    # ── 累积与刷新 ──────────────────────────────────

    # ── 内部辅助 ───────────────────────────────────

    def _trim_history(self) -> None:
        """裁剪历史记录，防止极长会话内存无限增长。"""
        if len(self._history) > _MAX_HISTORY:
            self._history = self._history[-_MAX_HISTORY:]

    # ── 累积与刷新 ──────────────────────────────────

    def append_reasoning(self, text: str) -> None:
        """追加推理文本到累积缓冲区。"""
        self._reasoning_accum.append(text)

    def append_content(self, text: str) -> None:
        """追加内容文本到累积缓冲区。"""
        self._content_accum.append(text)

    def flush_reasoning(self) -> None:
        """将累积的推理文本保存为单条历史记录并清空缓冲区。"""
        if self._reasoning_accum:
            full = ''.join(self._reasoning_accum)
            self._history.append(('reasoning_block', full))
            self._trim_history()
            self._reasoning_accum.clear()

    def flush_content(self) -> None:
        """将累积的内容文本保存为单条历史记录并清空缓冲区。"""
        if self._content_accum:
            full = ''.join(self._content_accum)
            self._history.append(('content_block', full))
            self._trim_history()
            self._content_accum.clear()

    def flush_all(self) -> None:
        """刷新所有累积缓冲区。"""
        self.flush_reasoning()
        self.flush_content()

    # ── 直接记录非累积内容 ─────────────────────────

    def record(self, kind: str, *args) -> None:
        """记录非累积类型到上屏历史。

        先 flush 所有累积缓冲区，确保顺序正确。
        kind 参数与 replay() 中的分发逻辑对应。
        """
        self.flush_all()
        self._history.append((kind, *args))
        self._trim_history()

    # ── 清空 ────────────────────────────────────────

    def clear(self) -> None:
        """清空上屏历史记录和累积缓冲区。"""
        self._history.clear()
        self._reasoning_accum.clear()
        self._content_accum.clear()

    # ── 重放 ────────────────────────────────────────

    def replay(self, tool_adapter: "OutputAdapter", bottom_bar) -> None:
        """终端尺寸变化后重放上屏历史内容。

        在 output_lock 保护下调用。清空上屏区域后按保存顺序
        重新渲染所有历史内容。

        Args:
            tool_adapter: OutputAdapter 实例（来自 _RenderState）
            bottom_bar: 底部栏实例（用于获取终端尺寸信息）
        """
        if not self._history:
            return

        out = sys.__stdout__

        # ── 清空上屏区域（行 1 → scroll_end） ──
        height = bottom_bar._term_height()
        total = bottom_bar._bottom_lines
        scroll_end = max(1, height - total)
        for r in range(1, scroll_end + 1):
            out.write(f"\033[{r};1H\033[K")
        out.write("\033[1;1H")

        from ..api.renderer import IncrementalRenderer

        for record in self._history:
            kind = record[0]

            if kind == 'reasoning_block':
                rr = IncrementalRenderer(
                    style="dim", _file=sys.__stdout__,
                    typing_speed=0, show_indicator=False,
                )
                rr.write(_THINKING_HEADER)
                rr.write(record[1])
                rr.write(_THINKING_SEPARATOR)
                rr.close()
            elif kind == 'content_block':
                cr = IncrementalRenderer(
                    _file=sys.__stdout__,
                    typing_speed=0, show_indicator=False,
                )
                cr.write(record[1])
                cr.close()
            elif kind == 'tool_output':
                self._write_record_tool_output(tool_adapter, record)
            elif kind == 'tool_summary':
                self._write_record_tool_summary(tool_adapter, record)
            elif kind == 'user_msg':
                tool_adapter.write(Text.assemble(
                    ("\n  > ", _STYLE_BOLD),
                    (record[1], _STYLE_BOLD),
                ))
            elif kind == 'notification':
                tool_adapter.write(Text.assemble(
                    ("\n  · ", _STYLE_SUCCESS),
                    (record[1], _STYLE_SUCCESS),
                ))
            elif kind == 'error':
                tool_adapter.write(Text.assemble(
                    ("\n  ! ", _STYLE_ERROR),
                    (record[1], _STYLE_ERROR),
                ))
            elif kind == 'cmd_output':
                self._write_record_ansi_text(tool_adapter, record[1])
            elif kind == 'write_line':
                self._write_record_ansi_text(tool_adapter, record[1])
            elif kind == 'display_msgs':
                cb = self.on_display_messages
                if cb is not None:
                    cb(record[1], record[2])

        out.flush()

    # ── 重放内部辅助 ────────────────────────────────

    @staticmethod
    def _write_record_tool_output(ta: "OutputAdapter", record: tuple) -> None:
        """重放工具输出记录。"""
        ta.write(Text.assemble(
            ("   ", _STYLE_DIM),
            (record[1], _STYLE_DIM),
        ))

    @staticmethod
    def _write_record_tool_summary(ta: "OutputAdapter", record: tuple) -> None:
        """重放工具汇总记录。"""
        successful, failed = record[1], record[2]
        total = len(successful) + len(failed)
        if failed:
            failed_names = ", ".join(n for n, _ in failed)
            if len(failed) == total:
                ta.write(Text.assemble(
                    ("  ! ", _STYLE_FAIL),
                    (f"全部失败: {failed_names}", _STYLE_FAIL),
                ))
            else:
                ta.write(Text.assemble(
                    ("  ! ", _STYLE_WARN),
                    (f"{len(failed)}/{total} 失败: {failed_names}", _STYLE_WARN),
                ))
        elif successful:
            ta.write(Text.assemble(
                ("  · ", _STYLE_SUCCESS),
                (f"{len(successful)}工具完成", _STYLE_SUCCESS),
            ))

    @staticmethod
    def _write_record_ansi_text(ta: "OutputAdapter", text: str) -> None:
        """重放含 ANSI 或纯文本的记录。"""
        if '\033[' in text:
            ta.write(Text.from_ansi(text))
        else:
            ta.write_raw(text + "\n")


# ── Style 常量（重放时使用） ────────────────────────

from ._const import (
    _STYLE_BOLD,
    _STYLE_DIM,
    _STYLE_ERROR,
    _STYLE_FAIL,
    _STYLE_SUCCESS,
    _STYLE_WARN,
    _THINKING_HEADER,
    _THINKING_SEPARATOR,
)
