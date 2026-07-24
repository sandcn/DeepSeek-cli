"""测试 _message_display 纯函数。

覆盖：
  - _role_icon 角色图标映射
  - truncate 文本截断
  - _format_sandbox_text 沙盒信息格式化
  - _scroll_window 可见窗口计算
  - _msg_line 单行摘要生成
  - _make_message_lines 消息选择器行渲染
  - _role_tag 角色标签（256 色 + 窄屏降级）
  - 分隔线和角色标签的 256 色 ANSI 序列
"""

from __future__ import annotations
from unittest.mock import MagicMock, patch

from src.tui.core.text_utils import truncate
from src.tui.pipeline.message_display import (
    _scroll_window,
    _role_icon, _format_sandbox_text,
    _msg_line, _make_message_lines, MessageDisplayContext,
    _role_tag, _make_gradient_sep, _make_think_sep, _make_think_end,
    _USER_TAG, _ASST_TAG, _TOOL_TAG,
)
from src.tui.terminal.terminal import is_narrow


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
    """truncate 文本截断测试。"""

    def test_short_text_not_truncated(self):
        result = truncate("hello", max_len=20)
        assert result == "hello"
        assert "…" not in result

    def test_long_text_truncated(self):
        text = "a" * 100
        result = truncate(text, max_len=10)
        assert len(result) == 11  # 10 chars + "…"
        assert result.endswith("…")

    def test_newlines_replaced(self):
        result = truncate("hello\nworld", max_len=60)
        assert "\n" not in result
        assert "hello world" in result

    def test_none_input(self):
        result = truncate(None, max_len=20)
        assert result == ""

    def test_empty_string(self):
        result = truncate("", max_len=20)
        assert result == ""

    def test_boundary_exact_width(self):
        text = "a" * 20
        result = truncate(text, max_len=20)
        assert result == text
        assert "…" not in result

    def test_boundary_one_over(self):
        text = "a" * 21
        result = truncate(text, max_len=20)
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
        monkeypatch.setattr("src.tui.pipeline.message_display.is_narrow", lambda: True)
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


class TestBuildMessagesHeader:
    """验证 _build_messages_header() 提取方法（步骤 7：拆分 _display_messages）。

    核心场景：
      1. 返回尾部装饰线字符串
      2. 标题被写入输出
      3. 窄屏时自动缩短宽度
    """

    def test_returns_sep_string(self):
        """_build_messages_header 应返回尾部装饰线字符串。"""
        from src.tui.pipeline.message_display import _build_messages_header
        with patch("src.tui.pipeline.message_display._manager") as mock_manager:
            sep = _build_messages_header()
        assert isinstance(sep, str), "返回类型应为 str"
        assert "\u2501" in sep, "装饰线应包含 ━ 字符"
        assert len(sep) > 0, "装饰线不应为空"

    def test_writes_header_to_output(self):
        """标题应被写入 _manager。"""
        from src.tui.pipeline.message_display import _build_messages_header, _manager
        original = _manager
        try:
            mock_manager = MagicMock()
            with patch("src.tui.pipeline.message_display._manager", mock_manager):
                _build_messages_header()
            mock_manager.write_line.assert_called_once()
            header_arg = mock_manager.write_line.call_args[0][0]
            assert "\n" in header_arg, "标题应以换行为前缀"
            assert "\u2770" in header_arg, "标题应包含 ❰ 字符"
            assert "\u2771" in header_arg, "标题应包含 ❱ 字符"
        finally:
            pass

    def test_returns_sep_with_correct_width(self):
        """返回的尾部装饰线宽度应与 header 函数使用的 width 一致。"""
        from src.tui.pipeline.message_display import _build_messages_header
        with patch("src.tui.pipeline.message_display._manager"):
            sep = _build_messages_header()
        # sep 应为 "\u2501" * narrow_sep_width(50)
        assert sep.startswith("\u2501"), "装饰线应以 ━ 开头"
        assert sep.endswith("\u2501"), "装饰线应以 ━ 结尾"
        assert len(sep) == len(sep), "装饰线应为纯 ━ 字符"


class TestRenderMessageItem:
    """验证 _render_message_item() 提取方法（步骤 7：拆分 _display_messages）。

    核心场景：
      1. tool_calls 消息路由到 _display_tool_calls
      2. tool 角色消息显示工具内容
      3. user 角色消息路由到 _display_user
      4. assistant 角色消息路由到 _display_assistant
      5. role_map 中有自定义 display_func 时优先使用
    """

    def test_tool_calls_routes_to_display_tool_calls(self):
        """tool_calls 消息应路由到 _display_tool_calls。"""
        from src.tui.pipeline.message_display import _render_message_item
        with patch("src.tui.pipeline.message_display._display_tool_calls") as mock_display:
            _render_message_item(
                i=0, m={"role": "assistant", "tool_calls": [{"function": {"name": "test_fn"}}], "content": ""},
                data=[],
            )
        mock_display.assert_called_once()

    def test_user_role_routes_to_display_user(self):
        """user 角色消息应路由到 _display_user。"""
        from src.tui.pipeline.message_display import _render_message_item
        with patch("src.tui.pipeline.message_display._display_user") as mock_display:
            _render_message_item(
                i=0, m={"role": "user", "content": "hello"},
                data=[],
            )
        mock_display.assert_called_once()

    def test_assistant_role_routes_to_display_assistant(self):
        """assistant 角色消息应路由到 _display_assistant。"""
        from src.tui.pipeline.message_display import _render_message_item
        with patch("src.tui.pipeline.message_display._display_assistant") as mock_display:
            _render_message_item(
                i=0, m={"role": "assistant", "content": "hello"},
                data=[],
            )
        mock_display.assert_called_once()

    def test_tool_role_shows_content_preview(self):
        """tool 角色消息应显示内容预览。"""
        from src.tui.pipeline.message_display import _render_message_item, _TOOL_CONTENT_PREVIEW_LEN
        with patch("src.tui.pipeline.message_display._manager") as mock_manager:
            _render_message_item(
                i=0, m={"role": "tool", "content": "tool output"},
                data=[],
            )
        # tool 角色应写入两次（分隔线 + 工具内容）
        assert mock_manager.write_line.call_count >= 2

    def test_role_map_custom_display_func_used_first(self):
        """role_map 中有自定义 display_func 时应优先使用。"""
        from src.tui.pipeline.message_display import _render_message_item, RoleConfig
        mock_display = MagicMock()
        role_map = {"user": RoleConfig(icon="\u25cf", tag_func=lambda bf: "", display_func=mock_display)}
        _render_message_item(
            i=0, m={"role": "user", "content": "hello"},
            data=[], role_map=role_map,
        )
        mock_display.assert_called_once()
