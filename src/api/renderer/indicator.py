"""StreamingIndicator — 流式生成视觉反馈指示器。

流式输出时在终端显示 "▊ 正在生成..." 光标动画。
首个内容 token 到达时自动清除。
"""

from __future__ import annotations

import threading
import time


class StreamingIndicator:
    """流式光标指示器。

    行为：
      start()              → 显示光标动画
      stop()               → 清除光标，停止动画
      on_first_content()   → 首个内容到达时停止并清除

    线程安全策略：
      所有共享状态通过 threading.Event 管理，利用 Event 内置的内存屏障
      保证跨线程可见性，无需额外加锁。
      - _terminated: 终止标记，set() 后对所有线程立即可见
      - _has_shown:   是否实际显示过光标，用于决定是否需要清除终端行
      - _running:     运行状态，控制 _tick 循环启停
    """

    CURSOR_CHARS = ["▊", "▋", "▌", "▍", "▎", "▏", "▎", "▍", "▌", "▋"]

    def __init__(self, output_adapter):
        self._output = output_adapter
        self._timer: threading.Timer | None = None
        self._idx = 0
        self._running = threading.Event()
        self._label = ""
        # Event 自带内存屏障：set() 写入对所有后续 is_set() 读取立即可见
        self._has_shown = threading.Event()   # 标记是否实际显示过光标
        self._terminated = threading.Event()  # 终止标志，阻止清除后再写入

    def start(self, label: str = "正在生成"):
        """启动光标动画。"""
        if self._running.is_set():
            return
        self._running.set()
        self._idx = 0
        self._label = label
        # 重置状态变量，允许新的启动周期
        self._terminated.clear()
        self._has_shown.clear()
        self._schedule_tick()

    def _schedule_tick(self):
        self._timer = threading.Timer(0.3, self._tick)
        self._timer.daemon = True
        self._timer.start()

    def _show(self):
        """显示一帧光标动画。"""
        # 单次检查 _terminated，Event.is_set() 自带内存屏障保证可见性
        if self._terminated.is_set():
            return
        self._has_shown.set()
        char = self.CURSOR_CHARS[self._idx % len(self.CURSOR_CHARS)]
        self._output.clear_line()
        self._output.write_raw(f"\033[90m  {char} {self._label}...\033[0m")

    def _tick(self):
        if not self._running.is_set():
            return
        self._idx += 1
        self._show()
        # 再次检查 _running 状态，防止 on_first_content 在 _show 和
        # _schedule_tick 之间清除了 _running 导致额外定时器
        if self._running.is_set():
            self._schedule_tick()

    def on_first_content(self):
        """首个内容 token 到达，清除指示器。"""
        # 先设终止标记，对所有线程立即可见，阻止 _show 继续写入
        self._terminated.set()
        was_running = self._running.is_set()
        self.stop()
        if was_running and self._has_shown.is_set():
            self._output.clear_line()

    def stop(self):
        """停止动画并清除终端行。"""
        self._terminated.set()
        was_running = self._running.is_set()
        self._running.clear()
        if self._timer:
            self._timer.cancel()
            self._timer = None
        # ★ 修复：关闭时清除终端上残留的 "正在生成..." 行
        # on_first_content() 只在新 token 到达时才清除，如果 close() 时
        # 已无新 token 触发 on_first_content，指示器行会残留。此处确保
        # 任何关闭路径都清理干净。
        if was_running and self._has_shown.is_set():
            self._output.clear_line()
