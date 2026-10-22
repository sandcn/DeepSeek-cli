"""帧驱动注册表：单次调用执行全部 60Hz 工作、去重与注销、异常隔离。"""

from __future__ import annotations

from src.core.frame_tick import FrameTickRegistry


def test_run_executes_all_registered_callbacks_once() -> None:
    registry = FrameTickRegistry()
    seen: list[str] = []
    registry.register(lambda: seen.append("a"))
    registry.register(lambda: seen.append("b"))

    registry.run()

    assert seen == ["a", "b"]


def test_register_is_idempotent_for_same_bound_method() -> None:
    class Probe:
        def __init__(self) -> None:
            self.calls = 0

        def tick(self) -> None:
            self.calls += 1

    registry = FrameTickRegistry()
    probe = Probe()
    registry.register(probe.tick)
    registry.register(probe.tick)

    assert len(registry) == 1
    registry.run()
    assert probe.calls == 1

    registry.unregister(probe.tick)
    assert len(registry) == 0
    registry.run()
    assert probe.calls == 1


def test_callback_exception_does_not_break_other_callbacks() -> None:
    registry = FrameTickRegistry()
    seen: list[str] = []

    def boom() -> None:
        raise RuntimeError("boom")

    registry.register(boom)
    registry.register(lambda: seen.append("ok"))

    registry.run()

    assert seen == ["ok"]


def test_clear_removes_all_callbacks() -> None:
    registry = FrameTickRegistry()
    registry.register(lambda: None)
    registry.clear()

    assert len(registry) == 0
