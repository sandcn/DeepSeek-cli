"""``ToolParseTracker`` 推送循环韧性回归测试（2026-09-20 解析进度行卡住修复）。

修复背景：``ToolParseTracker._update_loop_async`` 循环体除 ``CancelledError``
外无异常捕获——任一单拍异常（如 ``tool_calls_map`` 条目缺 ``name`` 键的
KeyError、非字符串工具名导致 ``','.join`` 的 TypeError）都会**静默终止整个
推送任务**（仅留 "Task exception was never retrieved"），此后解析进度行永远
停在最后一次成功推送的数值上（工具参数仍在接收 → 用户看到「⠹ WriteFile 25t
0.11s 卡住，数据有传过来」）。

修复：单拍计算抽为 ``_push_once``／``_snapshot_progress``，异常被捕获（5s
限频 warning）后**继续下一拍**；``finalize`` 同样防御（异常条目不得中断流
收尾的进度行清除与渲染器收尾）。本测试覆盖：异常条目期间推送继续、非字符串
工具名不中断、finalize 仍清除进度行、正常路径行为不变。
"""

from __future__ import annotations

import asyncio

import pytest

from src.api.interrupt_async import reset_interrupt_async
from src.api.stream_parse import ToolParseTracker, _PUSH_ERR_LOG_INTERVAL


class _RecordingDisplay:
    def __init__(self):
        self.updates: list = []
        self.done = 0

    def update_parse_info(self, label, name, tokens, elapsed):
        self.updates.append((name, tokens, elapsed))

    def parse_info_done(self, label):
        self.done += 1


def _entry(name="write_file", args="x" * 40, entry_id="c1"):
    e = {"id": entry_id, "arguments": "", "_args_parts": [args],
         "_stream_label": entry_id}
    if name is not None:
        e["name"] = name
    return e


def _run(calls_map, seconds: float = 0.35):
    """启动 tracker 推送若干拍后 finalize，返回显示桩。"""
    display = _RecordingDisplay()
    tracker = ToolParseTracker(calls_map, display, "main")

    async def _go():
        reset_interrupt_async()
        await tracker.start()
        await asyncio.sleep(seconds)
        await tracker.finalize()

    asyncio.run(_go())
    return display


class TestTrackerResilience:
    """单拍异常不得终止推送循环。"""

    def test_missing_name_key_does_not_kill_loop(self):
        """条目缺 ``name`` 键（KeyError）后循环继续推送。"""
        calls_map = {0: _entry(name="write_file"), 1: _entry(name=None)}
        display = _run(calls_map, seconds=0.45)
        assert len(display.updates) >= 3, "异常条目终止了推送循环"
        assert display.done == 1, "finalize 未清除进度行"

    def test_non_string_tool_name_does_not_kill_loop(self):
        """非字符串工具名（join TypeError）后循环继续推送。"""
        calls_map = {0: _entry(name=123)}
        display = _run(calls_map, seconds=0.45)
        assert len(display.updates) >= 3
        assert display.done == 1

    def test_recovery_after_bad_entry_removed(self):
        """异常条目移除后恢复正常推送（进度行数字继续刷新）。"""
        calls_map = {0: _entry(name="write_file"), 1: _entry(name=None)}
        display = _RecordingDisplay()
        tracker = ToolParseTracker(calls_map, display, "main")

        async def _go():
            await tracker.start()
            await asyncio.sleep(0.25)
            mid = len(display.updates)
            del calls_map[1]
            await asyncio.sleep(0.25)
            await tracker.finalize()
            return mid

        mid = asyncio.run(_go())
        assert mid >= 2
        assert len(display.updates) > mid + 1

    def test_finalize_clears_progress_line_on_bad_entry(self):
        """异常条目下 finalize 仍推送完成事件（进度行被清除）。"""
        display = _run({0: _entry(name=None)}, seconds=0.15)
        assert display.done == 1

    def test_normal_path_unchanged(self):
        """正常路径行为不变：约 10Hz 推送，最终清除。"""
        display = _run({0: _entry(name="write_file")}, seconds=0.45)
        assert len(display.updates) >= 3
        assert display.updates[0][0] == "WriteFile"
        assert display.done == 1

    def test_display_exception_does_not_kill_loop(self):
        """显示层异常（update_parse_info 抛错）不影响后续推送。"""

        class _Bad(_RecordingDisplay):
            def update_parse_info(self, *a, **k):
                raise RuntimeError("display boom")

        display = _Bad()
        tracker = ToolParseTracker({0: _entry()}, display, "main")

        async def _go():
            await tracker.start()
            await asyncio.sleep(0.3)
            await tracker.finalize()

        asyncio.run(_go())
        assert display.done == 1

    def test_error_log_interval_constant(self):
        """异常告警限频常量为正（防持续异常刷屏）。"""
        assert _PUSH_ERR_LOG_INTERVAL > 0

    def test_snapshot_progress_shape(self):
        """``_snapshot_progress`` 返回 (显示名串, token 估算)。"""
        tracker = ToolParseTracker({0: _entry(name="write_file")})
        name_str, tokens = tracker._snapshot_progress()
        assert name_str == "WriteFile"
        assert tokens > 0

    def test_snapshot_progress_empty_map(self):
        """空映射回退默认工具名（不抛异常）。"""
        tracker = ToolParseTracker({})
        name_str, tokens = tracker._snapshot_progress()
        assert name_str == "工具"
        assert tokens == 0
