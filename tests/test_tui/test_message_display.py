"""测试 _message_display 纯函数。

覆盖：
  - _role_icon 角色图标映射
  - _truncate 文本截断
  - _format_sandbox_text 沙盒信息格式化
  - _scroll_window 可见窗口计算
  - _msg_line 单行摘要生成
  - _make_message_lines 消息选择器行渲染
"""

from __future__ import annotations

from src.ui.picker import scroll_window as _scroll_window
from src.ui.tui._message_display import (
    _role_icon, _truncate, _format_sandbox_text,
    _msg_line, _make_message_lines, MessageDisplayContext,
)


def _make_msg(role: str, content: str = "",
              tool_calls: list | None = None) -> dict:
    """模块级辅助函数：构建测试用消息字典。"""
    msg: dict = {"role": role, "content": content}
    if tool_calls:
        msg["tool_calls"] = tool_calls
    return msg


class TestRoleIcon:
    """_role_icon 角色图标映射测试。"""

    def test_user_icon(self):
        assert _role_icon("user") == "\u25cf"       # ●

    def test_assistant_icon(self):
        assert _role_icon("assistant") == "\u25c6"  # ◆

    def test_tool_icon(self):
        assert _role_icon("tool") == "\u2699"       # ⚙

    def test_system_icon(self):
        assert _role_icon("system") == "\u00b7"     # ·

    def test_unknown_role(self):
        assert _role_icon("unknown") == "\u00b7"    # ·

    def test_empty_role(self):
        assert _role_icon("") == "\u00b7"           # ·


class TestTruncate:
    """_truncate 文本截断测试。"""

    def test_short_text_not_truncated(self):
        result = _truncate("hello", width=20)
        assert result == "hello"
        assert "…" not in result

    def test_long_text_truncated(self):
        text = "a" * 100
        result = _truncate(text, width=10)
        assert len(result) == 11  # 10 chars + "…"
        assert result.endswith("…")

    def test_newlines_replaced(self):
        result = _truncate("hello\nworld", width=60)
        assert "\n" not in result
        assert "hello world" in result

    def test_none_input(self):
        result = _truncate(None, width=20)
        assert result == ""

    def test_empty_string(self):
        result = _truncate("", width=20)
        assert result == ""

    def test_boundary_exact_width(self):
        text = "a" * 20
        result = _truncate(text, width=20)
        assert result == text
        assert "…" not in result

    def test_boundary_one_over(self):
        text = "a" * 21
        result = _truncate(text, width=20)
        assert len(result) == 21  # 20 + "…"
        assert result.endswith("…")


class TestFormatSandboxText:
    """_format_sandbox_text 沙盒信息格式化测试。"""

    def test_none_input(self):
        assert _format_sandbox_text(None) == ""

    def test_empty_info(self):
        assert _format_sandbox_text({"count": 0, "file_changes": []}) == ""

    def test_with_file_changes(self):
        info = {
            "count": 2,
            "file_changes": [
                {"file_path": "/tmp/test.py", "change_type": "modified"},
                {"file_path": "/tmp/new.txt", "change_type": "created"},
            ],
        }
        result = _format_sandbox_text(info)
        assert "沙盒" in result
        assert "test.py" in result
        assert "new.txt" in result
        assert "modified" in result
        assert "created" in result

    def test_single_change(self):
        info = {
            "count": 1,
            "file_changes": [
                {"file_path": "/tmp/f.py", "change_type": "deleted"},
            ],
        }
        result = _format_sandbox_text(info)
        assert "沙盒" in result
        assert "f.py" in result
        assert "deleted" in result


class TestScrollWindow:
    """_scroll_window 可见窗口计算测试。"""

    def test_total_less_than_max(self):
        result = _scroll_window(0, {"max": 15, "scroll": 0}, 10)
        assert result == (0, 10)

    def test_cursor_in_view(self):
        result = _scroll_window(5, {"max": 15, "scroll": 0}, 30)
        assert result == (0, 15)

    def test_cursor_below_offset(self):
        state = {"max": 15, "scroll": 0}
        result = _scroll_window(20, state, 30)
        assert result == (6, 21)
        assert state["scroll"] == 6

    def test_cursor_above_offset(self):
        state = {"max": 15, "scroll": 10}
        result = _scroll_window(5, state, 30)
        assert result[0] <= 5 < result[1]
        assert state["scroll"] == 5

    def test_start_boundary(self):
        state = {"max": 15, "scroll": 0}
        result = _scroll_window(0, state, 30)
        assert result == (0, 15)

    def test_end_boundary(self):
        state = {"max": 15, "scroll": 15}
        result = _scroll_window(29, state, 30)
        assert result == (15, 30)


class TestMsgLine:
    """_msg_line 消息摘要生成测试。"""

    def _make_ctx(self, data: list[dict]) -> MessageDisplayContext:
        """构建测试用 MessageDisplayContext（无 agent/idx_map）。"""
        return MessageDisplayContext(data=data)

    def test_user_message(self):
        msg = _make_msg("user", "hello world")
        ctx = self._make_ctx([msg])
        icon, role, text = _msg_line(msg, 0, ctx)
        assert icon == "\u25cf"        # ●
        assert role == "user"
        assert "hello world" in text

    def test_assistant_message(self):
        msg = _make_msg("assistant", "some response")
        ctx = self._make_ctx([msg])
        icon, role, text = _msg_line(msg, 0, ctx)
        assert icon == "\u25c6"        # ◆
        assert role == "assistant"
        assert "some response" in text

    def test_tool_calls_message(self):
        msg = _make_msg("assistant", "", tool_calls=[
            {"function": {"name": "read_file"}},
            {"function": {"name": "bash"}},
        ])
        ctx = self._make_ctx([msg])
        icon, role, text = _msg_line(msg, 0, ctx)
        assert icon == "\u25c6"        # ◆ (assistant 角色)
        assert "read_file" in text
        assert "bash" in text

    def test_empty_content(self):
        ctx = self._make_ctx([_make_msg("user", "")])
        icon, role, text = _msg_line(ctx.data[0], 0, ctx)
        assert text == ""


class TestMakeMessageLines:
    """_make_message_lines 消息选择器行渲染测试。"""

    def test_single_message(self):
        data = [_make_msg("user", "hello")]
        ctx = MessageDisplayContext(data=data)
        lines = _make_message_lines(
            items=[0], cursor=0, state={"max": 15, "scroll": 0},
            ctx=ctx, title="Test", tag="", is_current=True,
        )
        assert len(lines) >= 3  # title + sep + 1 msg + hint
        assert any("hello" in str(l) for l in lines)

    def test_multiple_messages(self):
        data = [
            _make_msg("user", "first"),
            _make_msg("assistant", "response"),
            _make_msg("user", "second"),
        ]
        ctx = MessageDisplayContext(data=data)
        lines = _make_message_lines(
            items=[0, 2], cursor=0, state={"max": 15, "scroll": 0},
            ctx=ctx, title="Test", tag=" (current)", is_current=True,
        )
        assert any("first" in str(l) for l in lines)
        assert any("second" in str(l) for l in lines)
        assert any("current" in str(l) for l in lines)

    def test_scroll_indicator(self):
        """超过 max 时显示滚动指示器。"""
        data = [_make_msg("user", f"msg{i}") for i in range(20)]
        ctx = MessageDisplayContext(data=data)
        lines = _make_message_lines(
            items=list(range(20)), cursor=0, state={"max": 15, "scroll": 0},
            ctx=ctx, title="List", tag="", is_current=False,
        )
        text = "".join(str(line) for _, line in lines)
        assert "更多" in text

    def test_title_includes_count(self):
        data = [_make_msg("user", "test")]
        ctx = MessageDisplayContext(data=data)
        lines = _make_message_lines(
            items=[0], cursor=0, state={"max": 15, "scroll": 0},
            ctx=ctx, title="Messages", tag="", is_current=True,
        )
        text = "".join(str(line) for _, line in lines)
        assert "Messages" in text
