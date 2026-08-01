"""test_base_display — 零覆盖模块最小测试（方向5 步骤5.5）。

覆盖 ``_BaseDisplay``：output_target 注入/读取、capture_and_print 捕获、
可选回调默认 no-op（tool_batch_start/tool_parsing/update_parse_info 等）。
"""

from __future__ import annotations

from src.tui._base_display import BaseDisplay


class _ConcreteDisplay(BaseDisplay):
    """BaseDisplay 最小实现（实现抽象方法 update_status）。"""

    def __init__(self, output_target=None):
        super().__init__(output_target=output_target)
        self.updates = []

    def update_status(self, label: str, status: str) -> None:
        self.updates.append((label, status))


class TestBaseDisplay:
    """_BaseDisplay 最小测试。"""

    def test_output_target_injection(self):
        """构造注入 output_target，属性可读。"""
        target = object()
        d = _ConcreteDisplay(output_target=target)
        assert d.output_target is target

    def test_output_target_default_none(self):
        """未注入时 output_target 为 None。"""
        d = _ConcreteDisplay()
        assert d.output_target is None

    def test_update_status_abstract_impl(self):
        """抽象方法 update_status 由子类实现可调用。"""
        d = _ConcreteDisplay()
        d.update_status("a", "running")
        assert d.updates == [("a", "running")]

    def test_capture_and_print(self):
        """capture_and_print 捕获 display_func 追加的行。"""
        d = _ConcreteDisplay()
        out = d.capture_and_print(lambda lines: lines.extend(["l1", "l2"]))
        assert out == "l1\nl2"

    def test_capture_and_print_async_falls_back_sync(self):
        """capture_and_print_async 默认回退到同步实现。"""
        d = _ConcreteDisplay()
        out = d.capture_and_print_async(lambda lines: lines.append("x"))
        assert out == "x"

    def test_optional_callbacks_noop(self):
        """可选回调默认 no-op 不抛（tool_batch_start/tool_parsing/...）。"""
        d = _ConcreteDisplay()
        d.tool_batch_start("a", ["read_file"])
        d.tool_parsing("a", "read_file", "args")
        d.update_parse_info("a", "rf", 10, 0.5)
        d.parse_info_done("a")
        d.add_agent("a", "desc", "running")
        d.update_agent_status("a", "done")

    def test_capture_and_print_returns_joined_lines(self):
        """capture_and_print 空行列表返回空字符串。"""
        d = _ConcreteDisplay()
        assert d.capture_and_print(lambda lines: None) == ""
