"""解析进度：由 render 帧驱动，不再自建 asyncio 60Hz 任务。"""

from __future__ import annotations

from src.api.stream_parse import ToolParseTracker
from src.core.frame_tick import get_frame_tick_registry


class _Display:
    def __init__(self) -> None:
        self.infos: list[tuple] = []
        self.done: list[str] = []

    def update_parse_info(self, label, tool_name, tokens, elapsed) -> None:
        self.infos.append((label, tool_name, tokens, elapsed))

    def parse_info_done(self, label) -> None:
        self.done.append(label)


async def test_tracker_registers_on_frame_tick_and_unregisters_on_finalize() -> None:
    display = _Display()
    tracker = ToolParseTracker({"0": {"name": "Read"}}, display, "main")
    registry = get_frame_tick_registry()

    await tracker.start()
    try:
        assert len(registry) == 1
        assert tracker._task is None
        assert len(display.infos) == 1

        registry.run()
        assert len(display.infos) == 2
    finally:
        await tracker.finalize()

    assert len(registry) == 0
    assert display.done == ["main"]


async def test_tracker_tick_isolated_from_push_errors() -> None:
    class _Broken(_Display):
        def update_parse_info(self, label, tool_name, tokens, elapsed) -> None:
            raise RuntimeError("boom")

    tracker = ToolParseTracker({"0": {"name": "Read"}}, _Broken(), "main")
    await tracker.start()
    registry = get_frame_tick_registry()
    try:
        registry.run()  # 异常只记日志，不抛出/不中断后续帧
    finally:
        await tracker.finalize()
    assert len(registry) == 0
