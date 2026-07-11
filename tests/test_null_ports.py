"""测试 src/core/ports/null 空端口实现。

注意：ports/null.py 已移除（僵尸端口清理），
空端口实现在 adapters/null.py 中。
"""

from contextlib import nullcontext

from src.core.adapters.null import _NullOutputPort, _NullPort


# ── _NullPort ─────────────────────────────────────

class TestNullPort:
    """_NullPort 通用空端口测试"""

    def test_is_web_false(self):
        port = _NullPort()
        assert port.is_web is False

    def test_write_no_error(self):
        port = _NullPort()
        port.write()
        port.write("text")
        port.write("text", "info", "test")

    def test_tool_methods_no_error(self):
        port = _NullPort()
        port.start()
        port.stop()
        port.tool_parsing("lbl", "tool")
        port.tool_start("lbl", "tool", "detail")
        port.tool_done("lbl")
        port.update_spinner("lbl")
        port.update_status("lbl", "status")

    def test_capture_and_print_returns_empty_string(self):
        port = _NullPort()
        result = port.capture_and_print(lambda: "hello")
        assert result == ""

    def test_capture_and_print_async_returns_empty_string(self):
        port = _NullPort()
        result = port.capture_and_print_async(lambda: "hello")
        assert result == ""

    def test_locked_returns_context_manager(self):
        port = _NullPort()
        cm = port.locked()
        assert isinstance(cm, nullcontext)

    def test_publish_subscribe_no_error(self):
        port = _NullPort()
        port.publish("event", {"data": 1})
        handler = lambda x: None
        port.subscribe("event", handler)
        port.unsubscribe("event", handler)

    def test_agent_methods_no_error(self):
        port = _NullPort()
        port.add_agent("lbl", "desc")
        port.update_agent_status("lbl", "running")
        port.update_model_phase("lbl", "thinking")
        port.update_usage("lbl", {"tokens": 100})
        port.update_speed("lbl", 1.5)
        port.update_live_input("lbl", 50)
        port.update_live_output("lbl", 30)
        port.tool_batch_start("lbl", ["tool1"])
        port.update_parse_info("lbl", "tool", 100, 0.5)


# ── _NullOutputPort ───────────────────────────────

class TestNullOutputPort:
    """_NullOutputPort 空输出端口测试"""

    def test_write_no_error(self):
        port = _NullOutputPort()
        port.write()
        port.write(text="hello")
        port.write(text="test", level="info", source="core")

    def test_write_with_lock_no_error(self):
        port = _NullOutputPort()
        port.write_with_lock()
        port.write_with_lock(text="locked", level="warn", source="plugin")

    def test_locked_returns_context_manager(self):
        port = _NullOutputPort()
        assert isinstance(port.locked(), nullcontext)

    def test_locked_context_manager_usable(self):
        port = _NullOutputPort()
        with port.locked():
            port.write("inside lock")

    def test_methods_return_none(self):
        port = _NullOutputPort()
        assert port.write() is None
        assert port.write_with_lock() is None
