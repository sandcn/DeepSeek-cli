"""统一渲染输出端口 — RenderOutput 装饰 OutputAdapter（src/tui/_output.py）。

★ 归属与职责（2026-07-31 步骤2-A 渲染输出路径统一）：
  OutputAdapter 为核心渲染器（保留），本模块在其上叠加三个关注点，
  采用装饰器模式组合扩展，不复制渲染逻辑：
    1. 受控紧急路径（write_emergency）— 仅队列满/render 崩溃调用
    2. 显式行跟踪回调（set_line_tracker）— 内容写后回调 tracker.track()
    3. 内容写统一委托（write/write_raw/write_text/batch_write/...）
       — 保持 OutputAdapter 锁外预渲染+锁内写语义

缓冲职责矩阵（2026-07-31 步骤2-子步骤1 差异盘点）：
  OutputAdapter._captured_output   — 渲染后 ANSI 捕获（RenderBuffer 集成）
  ChatRenderState.captured_*       — 渲染器捕获（reasoning/content 双通道）
  _StdoutLineTracker._ring         — 输出历史（行跟踪）
  RenderBuffer（_buffer.py）       — 布局合成（底部栏/补全弹窗）

写入点清单（2026-07-31 步骤2-子步骤1 差异盘点）：
  ① OutputAdapter 系列（write/batch_write/write_raw/write_line/print/flush/
     clear_line/write_inline）— 内容行与布局控制
  ② _emergency_write（_renderer/_renderer.py _do_parse_info +
     _engine.py 队列满/崩溃）— _do_parse_info 改走 write_raw（子步骤5）；
     _engine.py 队列满/崩溃统一至 write_emergency（子步骤7）
  ③ _screen.write_stdout — 仅紧急路径使用（docstring 收紧，子步骤7）
  ④ _position_cursor 的 adapter/stdout 双路径 — 布局控制，保留
  ⑤ _do_parse_info 直写 — 改走 write_raw（行内覆盖语义，子步骤5）
  ⑥ _StdoutLineTracker 全局劫持 — 改为显式 track 回调（子步骤6）
  ⑦ 底部栏直接 sys.__stdout__ 写（_draw_input_lines/_redraw_cycle_only/
     _do_sync_bottom_lines 等）— 布局控制非内容行，不进内容管线

限频降级说明：
  紧急输出限频（同类型 5s 内至多 1 次）在极端拥塞时可能丢失次要紧急消息
  —— 丢失消息为可接受降级。
"""

from __future__ import annotations

import logging
import sys
import threading
import time

_logger = logging.getLogger(__name__)


def _plain_text(renderable) -> str:
    """将 renderable 转为用于行跟踪的文本内容。

    str 原样返回（含 ANSI）；rich Text 取 plain（剥离样式）；
    其他对象 str() 兜底。
    """
    if isinstance(renderable, str):
        return renderable
    plain = getattr(renderable, "plain", None)
    if isinstance(plain, str):
        return plain
    return str(renderable)


class RenderOutput:
    """统一渲染输出端口 — 装饰 OutputAdapter，叠加三个关注点。

    装饰器模式：完整转发 OutputAdapter 公开调用面（write/batch_write/
    write_raw/write_line/print/flush/clear_line/write_inline/width/
    force_refresh_width），不复制渲染逻辑。内容写路径在委托后回调
    ``tracker.track()``，由 tracker 负责完整行检测与输出历史持久化。

    Args:
        adapter: 被装饰的 OutputAdapter 实例。
        emergency_interval: 同类型紧急写限频间隔（秒），默认 5.0。
    """

    def __init__(self, adapter, emergency_interval: float = 5.0):
        self._adapter = adapter
        self._emergency_interval = emergency_interval
        self._tracker = None
        self._emergency_lock = threading.Lock()
        self._last_emergency_write: dict[str, float] = {}

    # ── 行跟踪回调 ──────────────────────────────────

    def set_line_tracker(self, tracker) -> None:
        """注入显式行跟踪器。

        每次内容写完成后回调 ``tracker.track(ansi_text)``。
        完整行检测与历史持久化由 tracker 负责（_StdoutLineTracker）。
        """
        self._tracker = tracker

    def _emit_track(self, data: str) -> None:
        """内容写完成回调 tracker（防御性异常吞没，不破坏写路径）。"""
        if self._tracker is None or not data:
            return
        try:
            self._tracker.track(data)
        except Exception:
            _logger.debug("行跟踪回调异常", exc_info=True)

    # ── 内容写路径（委托 + track） ────────────────────

    def write(self, renderable) -> None:
        """流式输出 Rich renderable（委托 OutputAdapter.write + track）。

        保持 OutputAdapter 的锁外预渲染 + 锁内快速写入语义，
        ANSI 安全（含 \\x1b 的纯字符串自动转换为 Rich Text）。
        """
        self._adapter.write(renderable)
        self._emit_track(_plain_text(renderable))

    def write_text(self, text: str, level: str = "info") -> None:
        """内容文本输出（委托 OutputAdapter.write，ANSI 安全 + track）。

        Args:
            text: 要输出的文本（支持 ANSI 转义序列）。
            level: 输出级别（预留参数，当前不影响写入行为）。
        """
        if not text:
            return
        self._adapter.write(text)
        self._emit_track(text)

    def write_raw(self, text: str) -> None:
        """快速输出纯文本（委托 OutputAdapter.write_raw + track）。

        保持行内覆盖语义（\\r/\\033[K 等控制序列直写，不经 Rich 渲染）。
        """
        if not text:
            return
        self._adapter.write_raw(text)
        self._emit_track(text)

    def batch_write(self, renderables: list) -> None:
        """批量输出多个 renderable（委托 OutputAdapter.batch_write + track）。"""
        if not renderables:
            return
        self._adapter.batch_write(renderables)
        for renderable in renderables:
            self._emit_track(_plain_text(renderable))

    def write_line(self, text: str = "") -> None:
        """输出纯文本行（委托 OutputAdapter.write_line + track）。"""
        self._adapter.write_line(text)
        self._emit_track(f"{text}\n")

    def write_inline(self, text) -> None:
        """线程安全输出 Rich Text 到当前行（不换行，委托 + track）。"""
        self._adapter.write_inline(text)
        self._emit_track(_plain_text(text))

    def print(self, *args, **kwargs) -> None:
        """直接代理 console.print（委托 OutputAdapter.print + track）。"""
        self._adapter.print(*args, **kwargs)
        if args:
            self._emit_track(_plain_text(args[0]))

    # ── 布局/冲刷（不 track） ────────────────────────

    def clear_line(self) -> None:
        """清除当前行（布局控制，不 track）。"""
        self._adapter.clear_line()

    def flush(self) -> None:
        """刷出底层输出（不 track）。"""
        self._adapter.flush()

    # ── 宽度 ─────────────────────────────────────────

    @property
    def width(self) -> int:
        """终端宽度（委托底层 OutputAdapter）。"""
        return self._adapter.width

    def force_refresh_width(self) -> None:
        """强制刷新终端宽度缓存（委托底层 OutputAdapter）。"""
        self._adapter.force_refresh_width()

    # ── 捕获转发（IncrementalRenderer captured_output 机制） ──

    @property
    def _captured_output(self):
        """渲染后 ANSI 捕获列表（转发底层 OutputAdapter）。"""
        return self._adapter._captured_output

    @_captured_output.setter
    def _captured_output(self, value):
        """设置渲染后 ANSI 捕获列表（转发底层 OutputAdapter）。

        供 IncrementalRenderer(output_adapter=..., captured_output=...) 绑定，
        确保共享 adapter 模式下 captured 列表仍由底层 OutputAdapter 填充。
        """
        self._adapter._captured_output = value

    # ── 受控紧急路径 ─────────────────────────────────

    def write_emergency(self, text: str, stream: str = "stderr") -> None:
        """受控紧急输出 — 仅队列满/render 崩溃调用。

        固定写 ``sys.__stderr__``（stream="stdout" 时写 ``sys.__stdout__``），
        加限频防刷屏：同类型（按 stream）紧急写 ``emergency_interval`` 秒内
        至多 1 次，超限静默丢弃。

        限频降级说明：极端拥塞时可能丢失次要紧急消息——丢失消息为
        可接受降级（标注于模块 docstring）。
        """
        if not text:
            return
        now = time.monotonic()
        with self._emergency_lock:
            last = self._last_emergency_write.get(stream, 0.0)
            if now - last < self._emergency_interval:
                return
            self._last_emergency_write[stream] = now
        try:
            f = sys.__stderr__ if stream == "stderr" else sys.__stdout__
            f.write(text)
            f.flush()
        except (OSError, ValueError):
            pass
