"""测试 _message_display 纯函数。

覆盖：
  - _role_icon 角色图标映射
  - _truncate 文本截断
  - _format_sandbox_text 沙盒信息格式化
  - _scroll_window 可见窗口计算
  - _msg_line 单行摘要生成
  - _make_message_lines 消息选择器行渲染
  - _role_tag 角色标签（256 色 + 窄屏降级）
  - 分隔线和角色标签的 256 色 ANSI 序列
"""

from __future__ import annotations

from src.ui.tui._message_display import (
    _scroll_window,
    _role_icon, _truncate, _format_sandbox_text,
    _msg_line, _make_message_lines, MessageDisplayContext,
    _role_tag, _make_gradient_sep, _make_think_sep, _make_think_end,
    _USER_TAG, _ASST_TAG, _TOOL_TAG,
)
from src.ui.tui._terminal import is_narrow


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


class TestRoleTag256:
    """_role_tag 角色标签 256 色升级测试。"""

    def test_user_tag_contains_256color(self):
        """宽屏时 USER 标签含 256 色背景和前景 ANSI 码。"""
        tag = _role_tag("user")
        assert "48;5;" in tag       # 背景色 ANSI
        assert "38;5;81" in tag     # 亮青前景色 (BRIGHT_CYAN_256)

    def test_assistant_tag_contains_256color(self):
        """宽屏时 ASSISTANT 标签含 256 色背景和前景 ANSI 码。"""
        tag = _role_tag("assistant")
        assert "48;5;" in tag       # 背景色 ANSI
        assert "38;5;47" in tag     # 亮绿前景色 (BRIGHT_GREEN_256)

    def test_tool_tag_contains_256color(self):
        """宽屏时 TOOL 标签含 256 色背景和前景 ANSI 码。"""
        tag = _role_tag("tool")
        assert "48;5;" in tag       # 背景色 ANSI
        assert "38;5;227" in tag    # 亮黄前景色

    def test_unknown_role_fallback(self):
        """未知角色返回无背景中性标签。"""
        tag = _role_tag("unknown")
        assert "48;5;" not in tag
        assert "\u00b7" in tag

    def test_all_tags_have_reset(self):
        """所有角色标签末尾含重置序列，确保背景色不溢出。"""
        for role in ("user", "assistant", "tool"):
            tag = _role_tag(role)
            assert tag.endswith("\033[0m"), f"{role} tag missing reset"

    def test_role_tag_breath_border_wide(self):
        """宽屏呼吸角色标签含左侧呼吸边框字符 ┃ (U+2503)。"""
        for role in ("user", "assistant", "tool"):
            tag = _role_tag(role, breath_frame=1)
            assert "\u2503" in tag, f"{role} breath tag missing border"
            assert "38;5;" in tag  # 含 ANSI 色码

    def test_role_tag_breath_narrow_no_border(self, monkeypatch):
        """窄屏时呼吸角色标签不含边框字符。"""
        monkeypatch.setattr("src.ui.tui._message_display.is_narrow", lambda: True)
        for role in ("user", "assistant", "tool"):
            tag = _role_tag(role, breath_frame=1)
            assert "\u2503" not in tag, f"{role} narrow tag should have no border"


class TestSeparator256:
    """分隔线函数 256 色渐变测试。"""

    def test_gradient_sep_contains_256color(self):
        """_make_gradient_sep() 返回值含 256 色前景码（38;5;）和 ━ 字符。"""
        sep = _make_gradient_sep(steps=6)
        assert "38;5;" in sep
        assert "\u2501" in sep  # 厚分隔线 ━
        assert "\033[0m" in sep  # 重置序列

    def test_gradient_sep_starts_cyan_ends_darkgray(self):
        """渐变分隔线起始色为青色(45)，结束色为深灰(237)。"""
        sep = _make_gradient_sep(steps=6)
        assert "38;5;45" in sep    # 青色起始
        assert "38;5;237" in sep   # 深灰结束

    def test_gradient_sep_custom_colors(self):
        """指定起始结束色的渐变分隔线正确生成。"""
        sep = _make_gradient_sep(start_color=29, end_color=114, steps=4)
        assert "38;5;29" in sep    # 薄荷起始
        assert "38;5;114" in sep   # 薄荷结束

    def test_gradient_sep_step_count(self):
        """指定 steps 的渐变分隔线字符数正确。"""
        sep = _make_gradient_sep(steps=10)
        # 10个颜色 + "  "前缀 + 重置序列，每个颜色由 38;5;NNNm 控制
        count = sep.count("\u2501")
        assert count == 10, f"expected 10 ━ chars, got {count}"

    def test_think_sep_contains_256color(self):
        """_make_think_sep() 含青色(45)和多段 256 色码。"""
        sep = _make_think_sep()
        assert "38;5;45" in sep    # 青色
        assert "38;5;237" in sep   # 深灰
        assert "\u26a1" in sep     # ⚡ 闪电图标

    def test_think_end_contains_256color(self):
        """_make_think_end() 含 256 色码和 ━ 字符。"""
        end = _make_think_end()
        assert "38;5;" in end
        assert "\u2501" in end

    def test_separators_have_reset(self):
        """所有分隔线函数返回值含重置序列。"""
        for make_fn in (_make_gradient_sep, _make_think_sep, _make_think_end):
            result = make_fn() if make_fn != _make_gradient_sep else _make_gradient_sep(steps=6)
            assert "\033[0m" in result, f"{make_fn.__name__} missing reset"


class TestMessageDisplayColors:
    """消息显示模块 256 色完整性测试。"""

    def test_user_tag_constant_has_background(self):
        """_USER_TAG 常量含背景色码。"""
        assert "48;5;235" in _USER_TAG   # 暗灰背景
        assert "38;5;81" in _USER_TAG    # 亮青文字

    def test_asst_tag_constant_has_background(self):
        """_ASST_TAG 常量含背景色码。"""
        assert "48;5;22" in _ASST_TAG    # 暗绿背景
        assert "38;5;47" in _ASST_TAG    # 亮绿文字

    def test_tool_tag_constant_has_background(self):
        """_TOOL_TAG 常量含背景色码。"""
        assert "48;5;94" in _TOOL_TAG    # 暗黄背景
        assert "38;5;227" in _TOOL_TAG   # 亮黄文字
