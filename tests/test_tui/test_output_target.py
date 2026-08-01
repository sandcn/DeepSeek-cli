"""test_output_target — 零覆盖模块最小测试（方向5 步骤5.5）。

覆盖 ``_output_target.IOutputTarget`` 协议：runtime_checkable 鸭子类型判定、
实现类行为。
"""

from __future__ import annotations

from src.tui._output_target import IOutputTarget


class _ConcreteTarget:
    """IOutputTarget 协议实现（最小）。"""

    def __init__(self):
        self.lines = []
        self.flushed = 0

    def write_line(self, text: str) -> None:
        self.lines.append(text)

    def flush(self) -> None:
        self.flushed += 1

    def display_messages(self, messages, speed: int = 0) -> None:
        self.lines.append(f"msgs:{len(messages)}")


class TestOutputTargetProtocol:
    """IOutputTarget 协议最小测试。"""

    def test_protocol_runtime_checkable(self):
        """协议可经 isinstance 运行时判定（runtime_checkable）。"""
        assert isinstance(_ConcreteTarget(), IOutputTarget)

    def test_protocol_negative(self):
        """不实现协议方法的类不满足协议。"""

        class _NoTarget:
            pass

        assert not isinstance(_NoTarget(), IOutputTarget)

    def test_implementation_behavior(self):
        """实现类 write_line/flush/display_messages 行为。"""
        t = _ConcreteTarget()
        t.write_line("hello")
        t.flush()
        t.display_messages([{"role": "user"}], speed=2)
        assert t.lines == ["hello", "msgs:1"]
        assert t.flushed == 1

    def test_protocol_signatures_exist(self):
        """协议方法签名（write_line/flush/display_messages）存在。"""
        assert IOutputTarget.write_line is not None
        assert IOutputTarget.flush is not None
        assert IOutputTarget.display_messages is not None
