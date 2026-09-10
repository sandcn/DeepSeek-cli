"""解析进度行（``⠙ Write 384t 1.01s``）10Hz 刷新回归测试（2026-09-10 用户需求）。

需求（用户报障）：工具参数接收进度行 ``⠙ Write 384t 1.01s`` **不会每 10Hz
刷新信息**——spinner 逐帧推进（时间基 10Hz），但 token 数/耗时两拍才动一次
（0.2s 跳变），视觉上「信息不刷新」。

根因：``ToolParseTracker._update_loop_async`` 以 ``asyncio.sleep(0.2)`` 推送
``update_parse_info``（5Hz），而 TUI 渲染循环（``TuiConfig.render_interval``）
与进度行 spinner（``TuiConfig.spinner_tick_hz``）均为 **10Hz**——信息刷新频率
只有渲染频率的一半。

修复：``ToolParseTracker.REFRESH_INTERVAL``（``0.1`` = 10Hz）——与渲染循环／
spinner 同频，每拍推送一次 ⇒ 每帧消费一次（进度行 token/耗时随 spinner 平滑
刷新）。本测试覆盖：
  1. 刷新频率常量 = 10Hz（与 TuiConfig 渲染间隔／spinner 帧率对齐）；
  2. 更新循环**每拍**推送（不合并多拍），间隔恒为 ``REFRESH_INTERVAL``；
  3. 追踪器 → 事件/命令 → AppModel.parse_line 全链路：每拍进度行文本都变化
     （信息逐拍刷新，而非 0.2s 一次跳变）；
  4. token 数/工具名随拍推送（信息完整）。
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace

import pytest

import src.api.stream_parse as sp
from src.api.interrupt_async import reset_interrupt_async
from src.api.stream_parse import ToolParseTracker
from src.tui._config import TuiConfig
from src.tui._const import ParseInfoCmd
from src.tui._dispatcher import EventDispatcher
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.events.event_types import ParseInfoEvent


class _StopLoop(Exception):
    """跳出更新循环的哨兵异常（非 CancelledError，直接传播到测试）。"""


def _args_map():
    """工具调用累积映射（含 ``_args_parts``，与 tool_calls.py 流式累积同构）。"""
    return {
        0: {
            "id": "call_1",
            "name": "write_file",
            "arguments": "",
            "_args_parts": ['{"file_path": "a.txt", "content": "hello"}'],
        },
    }


# ═══════════════════════════════════════════════════════════
# 1. 刷新频率 = 10Hz（与渲染循环/spinner 帧率对齐）
# ═══════════════════════════════════════════════════════════

class TestRefreshRate:
    """``REFRESH_INTERVAL`` 语义：解析信息刷新频率。"""

    def test_refresh_interval_is_10hz(self):
        """刷新间隔 0.1s ⇒ 10Hz（修复前 0.2s＝5Hz）。"""
        assert ToolParseTracker.REFRESH_INTERVAL == pytest.approx(0.1)
        assert 1.0 / ToolParseTracker.REFRESH_INTERVAL == pytest.approx(10.0)

    def test_aligned_with_render_loop_and_spinner(self):
        """与 TUI 渲染循环（render_interval）/spinner 帧率同频（10Hz）。"""
        cfg = TuiConfig.defaults()
        refresh_hz = 1.0 / ToolParseTracker.REFRESH_INTERVAL
        assert refresh_hz == pytest.approx(cfg.spinner_tick_hz)
        # 信息刷新不得慢于渲染循环（否则每帧取到的是陈旧信息 → 「不刷新」）
        assert ToolParseTracker.REFRESH_INTERVAL <= cfg.render_interval

    def test_interval_not_the_old_5hz(self):
        """回归护栏：不得回退为 0.2s（5Hz）半帧率。"""
        assert ToolParseTracker.REFRESH_INTERVAL < 0.2


# ═══════════════════════════════════════════════════════════
# 2. 更新循环：每拍推送 + 间隔恒为 10Hz
# ═══════════════════════════════════════════════════════════

def _install_fake_clock_and_sleep(monkeypatch, clock, sleeps, on_tick, stop_after):
    """注入假时钟 + 假 asyncio.sleep（确定性驱动更新循环，不依赖真实时间）。"""
    monkeypatch.setattr(sp, "time", SimpleNamespace(monotonic=lambda: clock["t"]))

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        on_tick()
        clock["t"] += seconds
        if len(sleeps) >= stop_after:
            raise _StopLoop

    monkeypatch.setattr(sp, "asyncio", SimpleNamespace(
        CancelledError=asyncio.CancelledError,
        sleep=fake_sleep,
    ))


class TestUpdateLoopRefresh:
    """更新循环：每拍推送 display（信息逐拍刷新）。"""

    async def test_pushes_every_tick_with_10hz_interval(self, monkeypatch):
        """每拍推送一次 update_parse_info，sleep 间隔恒为 0.1s（10Hz）。"""
        reset_interrupt_async()
        calls: list = []
        display = SimpleNamespace(
            update_parse_info=lambda label, names, tokens, elapsed: calls.append(
                (label, names, tokens, elapsed)
            ),
        )
        tracker = ToolParseTracker(_args_map(), display, "main")
        clock = {"t": 100.0}
        tracker._start_time = clock["t"]

        sleeps: list = []
        _install_fake_clock_and_sleep(
            monkeypatch, clock, sleeps, lambda: None, stop_after=3,
        )

        with pytest.raises(_StopLoop):
            await tracker._update_loop_async()

        # 3 拍 ⇒ 3 次推送（间隔前先刷新——信息不合并/不丢拍）
        assert len(calls) == 3
        # 间隔恒为 10Hz（修复前为 0.2）
        assert sleeps == [pytest.approx(0.1)] * 3

    async def test_elapsed_and_tokens_grow_each_tick(self, monkeypatch):
        """耗时逐拍增长、token 数随参数推送——进度行信息随时间实时刷新。"""
        reset_interrupt_async()
        calls: list = []
        display = SimpleNamespace(
            update_parse_info=lambda label, names, tokens, elapsed: calls.append(
                (names, tokens, elapsed)
            ),
        )
        tracker = ToolParseTracker(_args_map(), display, "main")
        clock = {"t": 100.0}
        tracker._start_time = clock["t"]

        sleeps: list = []
        _install_fake_clock_and_sleep(
            monkeypatch, clock, sleeps, lambda: None, stop_after=3,
        )

        with pytest.raises(_StopLoop):
            await tracker._update_loop_async()

        names = [c[0] for c in calls]
        tokens = [c[1] for c in calls]
        elapsed = [c[2] for c in calls]
        # 工具名 = 显示名（修复用户所见的 ``Write`` 前缀）
        assert names == ["Write"] * 3
        # token 数 > 0（信息随参数累积推送）
        assert all(t > 0 for t in tokens)
        # 耗时严格递增（0.00 → 0.10 → 0.20）：不是「冻结值」
        assert elapsed == [pytest.approx(0.0), pytest.approx(0.1), pytest.approx(0.2)]
        assert elapsed[0] < elapsed[1] < elapsed[2]


# ═══════════════════════════════════════════════════════════
# 3. 全链路：追踪器 → ParseInfoEvent/ParseInfoCmd → AppModel.parse_line
# ═══════════════════════════════════════════════════════════

def _line_text(model) -> str:
    return "".join(r.text for r in model.parse_line.runs)


class TestParseLineRefreshesEveryTick:
    """每拍进度行文本都变化（渲染循环每帧读到新信息）。"""

    async def test_line_text_changes_every_tick(self, monkeypatch):
        reset_interrupt_async()
        model = AppModel()
        dispatcher = EventDispatcher(
            push_cmd=lambda cmd: apply_cmd(model, cmd),
            filter_fn=lambda source: True,
        )

        class _Display:
            """display 桩：把 update_parse_info 转成 ParseInfoEvent → 分发层。"""

            def update_parse_info(self, label, tool_names, tokens, elapsed):
                dispatcher.list_handlers()[ParseInfoEvent](ParseInfoEvent(
                    label=label, tool_names=tool_names, tokens=tokens,
                    elapsed=elapsed, source="agent",
                ))

        tracker = ToolParseTracker(_args_map(), _Display(), "main")
        clock = {"t": 100.0}
        tracker._start_time = clock["t"]

        frames: list = []

        def on_tick():
            frames.append(_line_text(model))

        sleeps: list = []
        _install_fake_clock_and_sleep(
            monkeypatch, clock, sleeps, on_tick, stop_after=3,
        )

        with pytest.raises(_StopLoop):
            await tracker._update_loop_async()

        # 每拍渲染到的进度行都不同（信息逐拍刷新，非 0.2s 一跳）
        assert len(set(frames)) == 3, frames
        assert "Write" in frames[0]
        assert "0.00s" in frames[0]
        assert "0.10s" in frames[1]
        assert "0.20s" in frames[2]

    def test_parse_info_cmd_reaches_parse_line(self):
        """命令 → 模型链路：ParseInfoCmd 立即更新 model.parse_line 文本。"""
        model = AppModel()
        apply_cmd(model, ParseInfoCmd(
            tool_names="Write", tokens=384, elapsed=1.01,
        ))
        text = _line_text(model)
        assert "Write" in text
        assert "384t" in text
        assert "1.01s" in text

    def test_clear_cmd_removes_parse_line(self):
        """ParseInfoDone（清行命令）后进度行消失（不残留陈旧信息）。"""
        from src.tui._const import _CLEAR_PARSE_LINE

        model = AppModel()
        apply_cmd(model, ParseInfoCmd(tool_names="Write", tokens=384, elapsed=1.01))
        assert model.parse_line is not None
        apply_cmd(model, ParseInfoCmd(tool_names="", tokens=_CLEAR_PARSE_LINE,
                                     elapsed=0.0))
        assert model.parse_line is None
