"""测试 src/webui/display.py — WebDisplay 类"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.ui.base_display import BaseDisplay
from src.webui._base_sender import BaseWebSocketSender
from src.webui.display import WebDisplay, pending_selects


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def make_display() -> tuple[WebDisplay, MagicMock]:
    """创建 WebDisplay 实例及其 mock send_func。"""
    send_func = MagicMock()
    display = WebDisplay(send_func)
    return display, send_func


# ═══════════════════════════════════════════════════════════════
# 实例化与继承测试
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayInit:
    """验证 WebDisplay 构造与继承关系。"""

    def test_is_web_true(self) -> None:
        display, _ = make_display()
        assert display.is_web is True

    def test_inherits_from_base_display(self) -> None:
        display, _ = make_display()
        assert isinstance(display, BaseDisplay)

    def test_inherits_from_base_web_socket_sender(self) -> None:
        display, _ = make_display()
        assert isinstance(display, BaseWebSocketSender)

    def test_send_func_stored(self) -> None:
        send_func = MagicMock()
        display = WebDisplay(send_func)
        assert display._send is send_func

    def test_pending_selects_exists(self) -> None:
        """模块级 pending_selects 变量存在且为 PendingSelectRegistry。"""
        from src.webui._pending_selects import PendingSelectRegistry
        assert isinstance(pending_selects, PendingSelectRegistry)


# ═══════════════════════════════════════════════════════════════
# 生命周期方法
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayLifecycle:
    def test_start_sends_display_started(self) -> None:
        display, send_func = make_display()
        display.start()
        send_func.assert_called_once_with({"type": "display_started"})

    def test_stop_default(self) -> None:
        display, send_func = make_display()
        display.stop()
        send_func.assert_called_once_with({"type": "display_stopped", "final": False})

    def test_stop_final_true(self) -> None:
        display, send_func = make_display()
        display.stop(final=True)
        send_func.assert_called_once_with({"type": "display_stopped", "final": True})


# ═══════════════════════════════════════════════════════════════
# 工具调用方法
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayToolMethods:
    def test_tool_parsing_basic(self) -> None:
        display, send_func = make_display()
        display.tool_parsing("agent-1", "read_file", '{"path": "x.py"}')
        send_func.assert_called_once_with({
            "type": "tool_parsing", "label": "agent-1",
            "tool_name": "read_file", "arguments": '{"path": "x.py"}',
        })

    def test_tool_parsing_default_arguments(self) -> None:
        display, send_func = make_display()
        display.tool_parsing("agent-1", "read_file")
        sent = send_func.call_args[0][0]
        assert sent["arguments"] == ""

    def test_tool_start_basic(self) -> None:
        display, send_func = make_display()
        display.tool_start("agent-1", "read_file")
        sent = send_func.call_args[0][0]
        assert sent["type"] == "tool_started"
        assert sent["label"] == "agent-1"
        assert sent["tool_name"] == "read_file"
        assert sent["detail"] == ""
        assert sent["metadata"] == {}

    def test_tool_start_with_detail(self) -> None:
        display, send_func = make_display()
        display.tool_start("agent-1", "read_file", detail="reading x.py",
                           metadata={"lines": 100})
        sent = send_func.call_args[0][0]
        assert sent["detail"] == "reading x.py"
        assert sent["metadata"] == {"lines": 100}

    def test_tool_done_basic(self) -> None:
        display, send_func = make_display()
        display.tool_done("agent-1")
        sent = send_func.call_args[0][0]
        assert sent["type"] == "tool_done"
        assert sent["label"] == "agent-1"
        assert sent["tool_name"] == ""
        assert sent["success"] is True
        assert sent["metadata"] == {}

    def test_tool_done_failure(self) -> None:
        display, send_func = make_display()
        display.tool_done("agent-1", success=False)
        sent = send_func.call_args[0][0]
        assert sent["success"] is False

    def test_tool_done_with_metadata(self) -> None:
        display, send_func = make_display()
        display.tool_done("agent-1", metadata={"output": "..."})
        sent = send_func.call_args[0][0]
        assert sent["metadata"] == {"output": "..."}


# ═══════════════════════════════════════════════════════════════
# 状态与阶段方法
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayStatusMethods:
    def test_update_status(self) -> None:
        display, send_func = make_display()
        display.update_status("agent-1", "running")
        send_func.assert_called_once_with({
            "type": "tool_status", "label": "agent-1", "status": "running",
        })

    def test_update_model_phase_basic(self) -> None:
        display, send_func = make_display()
        display.update_model_phase("agent-1", "thinking")
        send_func.assert_called_once_with({
            "type": "model_phase", "label": "agent-1", "phase": "thinking", "info": "",
        })

    def test_update_model_phase_with_info(self) -> None:
        display, send_func = make_display()
        display.update_model_phase("agent-1", "generating", info="10 tokens")
        sent = send_func.call_args[0][0]
        assert sent["info"] == "10 tokens"

    def test_update_usage_basic(self) -> None:
        display, send_func = make_display()
        display.update_usage("agent-1", {"input": 100, "output": 50})
        sent = send_func.call_args[0][0]
        assert sent["type"] == "usage_update"
        assert sent["usage"] == {"input": 100, "output": 50}
        assert sent["replace"] is False

    def test_update_usage_replace(self) -> None:
        display, send_func = make_display()
        display.update_usage("agent-1", {"input": 200}, replace=True)
        sent = send_func.call_args[0][0]
        assert sent["replace"] is True


# ═══════════════════════════════════════════════════════════════
# 实时指标方法
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayMetricsMethods:
    def test_update_speed(self) -> None:
        display, send_func = make_display()
        display.update_speed("agent-1", 15.5)
        send_func.assert_called_once_with({
            "type": "speed_update", "label": "agent-1", "speed": 15.5,
        })

    def test_update_live_input(self) -> None:
        display, send_func = make_display()
        display.update_live_input("agent-1", 42)
        send_func.assert_called_once_with({
            "type": "live_input", "label": "agent-1", "tokens": 42,
        })

    def test_update_live_output(self) -> None:
        display, send_func = make_display()
        display.update_live_output("agent-1", 128)
        send_func.assert_called_once_with({
            "type": "live_output", "label": "agent-1", "tokens": 128,
        })


# ═══════════════════════════════════════════════════════════════
# 扩展方法
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayExtendedMethods:
    def test_tool_batch_start(self) -> None:
        display, send_func = make_display()
        display.tool_batch_start("agent-1", ["read_file", "write_file"])
        send_func.assert_called_once_with({
            "type": "tool_batch_start", "label": "agent-1",
            "names": ["read_file", "write_file"],
        })

    def test_tool_batch_start_empty(self) -> None:
        display, send_func = make_display()
        display.tool_batch_start("agent-1", [])
        sent = send_func.call_args[0][0]
        assert sent["names"] == []

    def test_update_parse_info(self) -> None:
        display, send_func = make_display()
        display.update_parse_info("agent-1", "read_file", 100, 0.5)
        send_func.assert_called_once_with({
            "type": "parse_info", "label": "agent-1",
            "tool_name": "read_file", "tokens": 100, "elapsed": 0.5,
        })

    def test_update_agent_status(self) -> None:
        display, send_func = make_display()
        display.update_agent_status("agent-1", "done")
        send_func.assert_called_once_with({
            "type": "agent_status", "label": "agent-1", "status": "done",
        })

    def test_add_agent_basic(self) -> None:
        display, send_func = make_display()
        display.add_agent("agent-1", "解析代码")
        sent = send_func.call_args[0][0]
        assert sent["type"] == "agent_added"
        assert sent["label"] == "agent-1"
        assert sent["description"] == "解析代码"
        assert sent["status"] == "running"

    def test_add_agent_with_status(self) -> None:
        display, send_func = make_display()
        display.add_agent("agent-1", "解析代码", status="done")
        sent = send_func.call_args[0][0]
        assert sent["status"] == "done"


# ═══════════════════════════════════════════════════════════════
# capture_and_print
# ═══════════════════════════════════════════════════════════════

class TestWebDisplayCaptureAndPrint:
    def test_callable_returns_result(self) -> None:
        display, _ = make_display()
        result = display.capture_and_print(lambda: "hello web")
        assert result == "hello web"

    def test_none_returns_empty_string(self) -> None:
        display, _ = make_display()
        result = display.capture_and_print(None)
        assert result == ""

    def test_non_callable_returns_empty_string(self) -> None:
        display, _ = make_display()
        result = display.capture_and_print("not a function")
        assert result == ""


# ═══════════════════════════════════════════════════════════════
# WebDisplay 调用 send_json 的一致性
# ═══════════════════════════════════════════════════════════════

class TestWebDisplaySendJsonConsistency:
    """确保每个方法都调用了 send_json（即 mock send_func）且只调用一次。"""

    def test_start_called_once(self) -> None:
        display, send_func = make_display()
        display.start()
        send_func.assert_called_once()

    def test_stop_called_once(self) -> None:
        display, send_func = make_display()
        display.stop()
        send_func.assert_called_once()

    def test_tool_parsing_called_once(self) -> None:
        display, send_func = make_display()
        display.tool_parsing("a", "b")
        send_func.assert_called_once()

    def test_tool_start_called_once(self) -> None:
        display, send_func = make_display()
        display.tool_start("a", "b")
        send_func.assert_called_once()

    def test_tool_done_called_once(self) -> None:
        display, send_func = make_display()
        display.tool_done("a")
        send_func.assert_called_once()

    def test_update_status_called_once(self) -> None:
        display, send_func = make_display()
        display.update_status("a", "b")
        send_func.assert_called_once()

    def test_update_model_phase_called_once(self) -> None:
        display, send_func = make_display()
        display.update_model_phase("a", "b")
        send_func.assert_called_once()

    def test_update_usage_called_once(self) -> None:
        display, send_func = make_display()
        display.update_usage("a", {})
        send_func.assert_called_once()

    def test_update_speed_called_once(self) -> None:
        display, send_func = make_display()
        display.update_speed("a", 1.0)
        send_func.assert_called_once()

    def test_update_live_input_called_once(self) -> None:
        display, send_func = make_display()
        display.update_live_input("a", 1)
        send_func.assert_called_once()

    def test_update_live_output_called_once(self) -> None:
        display, send_func = make_display()
        display.update_live_output("a", 1)
        send_func.assert_called_once()

    def test_tool_batch_start_called_once(self) -> None:
        display, send_func = make_display()
        display.tool_batch_start("a", [])
        send_func.assert_called_once()

    def test_update_parse_info_called_once(self) -> None:
        display, send_func = make_display()
        display.update_parse_info("a", "b", 0, 0.0)
        send_func.assert_called_once()

    def test_update_agent_status_called_once(self) -> None:
        display, send_func = make_display()
        display.update_agent_status("a", "b")
        send_func.assert_called_once()

    def test_add_agent_called_once(self) -> None:
        display, send_func = make_display()
        display.add_agent("a", "b")
        send_func.assert_called_once()
