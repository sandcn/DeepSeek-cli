"""测试 WebSocketTarget — IOutputTarget 的 WebSocket 实现"""

from __future__ import annotations

from typing import Any, List
import pytest

from src.webui.output_target import WebSocketTarget
from src.ui.output_target import IOutputTarget, BufferTarget, NullTarget


# ═══════════════════════════════════════════════════════════════
# 辅助：消息收集器
# ═══════════════════════════════════════════════════════════════

class _MsgCollector:
    """模拟 WebSocket 发送通道，收集所有发送的消息。"""

    def __init__(self):
        self.messages: list[dict[str, Any]] = []

    def send(self, msg: dict[str, Any]) -> None:
        self.messages.append(msg)


# ═══════════════════════════════════════════════════════════════
# WebSocketTarget 基础测试
# ═══════════════════════════════════════════════════════════════

class TestWebSocketTarget:
    """WebSocketTarget 基础功能测试"""

    def test_implements_ioutputtarget(self):
        """WebSocketTarget 应满足 IOutputTarget Protocol"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        assert isinstance(target, IOutputTarget), (
            "WebSocketTarget 应实现 IOutputTarget Protocol"
        )

    def test_write_sends_command_output(self):
        """write() 应发送 command_output 消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write("hello")
        assert len(collector.messages) == 1
        msg = collector.messages[0]
        assert msg["type"] == "command_output"
        assert msg["text"] == "hello"
        assert msg["level"] == "info"

    def test_write_empty_skips(self):
        """write() 空字符串不应发送消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write("")
        assert len(collector.messages) == 0

    def test_write_line_sends_command_output(self):
        """write_line() 应发送 command_output 消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write_line("world")
        assert len(collector.messages) == 1
        msg = collector.messages[0]
        assert msg["type"] == "command_output"
        assert msg["text"] == "world"

    def test_write_line_empty_sends(self):
        """write_line() 空字符串也应发送（空行也是内容）"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write_line("")
        assert len(collector.messages) == 1, "空行应发送"

    def test_render_frame_sends_output_frame(self):
        """render_frame() 应发送 output_frame 消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        lines = ["line1", "line2", "line3"]
        result = target.render_frame(lines, last_lines=2)
        assert result == 3
        assert len(collector.messages) == 1
        msg = collector.messages[0]
        assert msg["type"] == "output_frame"
        assert msg["lines"] == ["line1", "line2", "line3"]
        assert msg["last_lines"] == 2

    def test_render_frame_empty_lines(self):
        """render_frame() 空行列表也可发送"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        result = target.render_frame([], last_lines=5)
        assert result == 0
        assert len(collector.messages) == 1
        msg = collector.messages[0]
        assert msg["type"] == "output_frame"
        assert msg["lines"] == []
        assert msg["last_lines"] == 5

    def test_render_frame_returns_line_count(self):
        """render_frame() 应返回行数供链式调用"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        assert target.render_frame(["a"], 0) == 1
        assert target.render_frame(["a", "b"], 1) == 2
        assert target.render_frame([], 2) == 0

    def test_terminal_width_default(self):
        """terminal_width 默认应为 120"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        assert target.terminal_width == 120

    def test_terminal_width_custom(self):
        """可自定义 terminal_width"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send, terminal_width=80)
        assert target.terminal_width == 80

    def test_terminal_width_min_40(self):
        """terminal_width 最小为 40"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send, terminal_width=10)
        assert target.terminal_width == 40

    def test_set_width(self):
        """set_width() 应更新宽度"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send, terminal_width=120)
        target.set_width(100)
        assert target.terminal_width == 100

    def test_set_width_clamp(self):
        """set_width() 应限制在 [40, 200] 范围内"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send, terminal_width=120)
        target.set_width(10)
        assert target.terminal_width == 40
        target.set_width(300)
        assert target.terminal_width == 200

    def test_send_raw(self):
        """send_raw() 应透传消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.send_raw({"type": "custom", "data": 42})
        assert len(collector.messages) == 1
        assert collector.messages[0] == {"type": "custom", "data": 42}

    def test_send_func_exception_does_not_crash(self):
        """send_func 抛异常时不应崩溃"""
        def _broken_send(_msg):
            raise RuntimeError("模拟发送失败")
        target = WebSocketTarget(_broken_send)
        # 不抛异常即可（send_func 的异常由调用方自行处理）
        target.write("hello")
        target.write_line("world")
        target.render_frame(["a"], 0)
        # 如果走到这里，说明没崩溃

    def test_write_multiple_lines(self):
        """多次写入应依次发送"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write_line("a")
        target.write_line("b")
        target.write_line("c")
        assert len(collector.messages) == 3
        assert [m["text"] for m in collector.messages] == ["a", "b", "c"]

    def test_mixed_write_and_render_frame(self):
        """write()/write_line()/render_frame() 混合调用应保持顺序"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        target.write_line("start")
        target.render_frame(["frame1"], 0)
        target.write("middle")
        target.render_frame(["frame2"], 1)
        target.write_line("end")
        assert len(collector.messages) == 5
        assert collector.messages[0]["type"] == "command_output"
        assert collector.messages[0]["text"] == "start"
        assert collector.messages[1]["type"] == "output_frame"
        assert collector.messages[2]["type"] == "command_output"
        assert collector.messages[2]["text"] == "middle"
        assert collector.messages[3]["type"] == "output_frame"
        assert collector.messages[4]["type"] == "command_output"
        assert collector.messages[4]["text"] == "end"


# ═══════════════════════════════════════════════════════════════
# 接口兼容性测试：IOutputTarget Protocol
# ═══════════════════════════════════════════════════════════════

class TestIOutputTargetProtocol:
    """验证 WebSocketTarget 满足 IOutputTarget 行为契约"""

    def test_protocol_structural_match(self):
        """结构类型匹配（duck typing）"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)

        # 接受 IOutputTarget 作为参数
        def use_target(t: IOutputTarget) -> bool:
            t.write("test")
            t.write_line("test")
            n = t.render_frame(["a"], 0)
            _ = t.terminal_width
            return True

        assert use_target(target)

    def test_interchangeable_with_other_output_targets(self):
        """与其他 IOutputTarget 实现互换验证

        验证 WebSocketTarget 的方法签名与 TerminalTarget/BufferTarget 一致。
        """
        collector = _MsgCollector()
        ws_target = WebSocketTarget(collector.send)
        buf_target = BufferTarget()

        # 相同的调用模式
        for t in [ws_target, buf_target]:
            t.write("hello")
            t.write_line("world")
            t.render_frame(["line1", "line2"], 0)
            _ = t.terminal_width

        # BufferTarget 收集到内容
        assert len(buf_target.lines) >= 2
        # WebSocketTarget 发送了消息
        assert len(collector.messages) >= 2

    def test_null_target_direct_import(self):
        """NullTarget 可直接 import 使用（验证已有组件可用性）"""
        null = NullTarget()
        null.write("should be dropped")
        null.write_line("also dropped")
        result = null.render_frame(["a", "b"], 0)
        assert result == 0
        assert null.terminal_width == 120

    def test_buffer_target_direct_import(self):
        """BufferTarget 可直接 import 使用（验证已有组件可用性）"""
        buf = BufferTarget()
        buf.write("hello")
        buf.write_line("world")
        result = buf.render_frame(["line1"], 0)
        assert result == 1
        assert len(buf.lines) >= 2


# ═══════════════════════════════════════════════════════════════
# 边界条件测试
# ═══════════════════════════════════════════════════════════════

class TestWebSocketTargetEdgeCases:
    """边界条件与鲁棒性"""

    def test_none_send_func(self):
        """缺少 send_func 应抛出 TypeError（符合预期）"""
        with pytest.raises(TypeError):
            WebSocketTarget(None)  # type: ignore[arg-type]

    def test_large_frame(self):
        """大量行的帧也应正常发送"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        large_lines = [f"line{i}" for i in range(1000)]
        result = target.render_frame(large_lines, last_lines=0)
        assert result == 1000
        assert len(collector.messages) == 1
        assert len(collector.messages[0]["lines"]) == 1000

    def test_very_long_line(self):
        """单行超长文本也应正常发送"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        long_text = "x" * 10000
        target.write(long_text)
        assert collector.messages[0]["text"] == long_text

    def test_special_chars(self):
        """含特殊字符的文本应正常发送"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        special = "hello\nworld\twith\u0000null\u001b[31mANSI"
        target.write(special)
        assert collector.messages[0]["text"] == special

    def test_repeated_render_frame(self):
        """连续 render_frame 调用应发送多条消息"""
        collector = _MsgCollector()
        target = WebSocketTarget(collector.send)
        last = 0
        for i in range(5):
            lines = [f"frame_{i}_line_{j}" for j in range(3)]
            last = target.render_frame(lines, last)
        assert len(collector.messages) == 5
        assert collector.messages[-1]["last_lines"] == 3
