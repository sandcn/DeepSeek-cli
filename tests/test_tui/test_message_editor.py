"""Tests for src/ui/tui/message_editor.py — _interactive_message_select 各分支输出验证

测试覆盖：
  - 空数据分支：publish_output 被调用，返回 ("quit", 0)
  - 无用户消息分支：publish_output 被调用，返回 ("quit", 0)
  - error action 分支：publish_output 被调用，返回 ("quit", 0)
  - cancel action 分支：publish_output 被调用，返回 ("quit", 0)
  - 无效索引分支：publish_output 被调用，返回 ("quit", 0)
  - 正常选择分支：返回 ("edit", real_idx)，publish_output 不被额外调用
  - _get_editor_msg_max_width 分支：动态宽度计算验证
"""

from unittest.mock import MagicMock, patch

from src.tui.core.ansi_utils import strip_ansi
from src.tui.pipeline import message_editor as _me
from src.tui.pipeline.message_editor import MessageEditor
from src.tui.pipeline.message_display import MessageDisplayContext


# ═══════════════════════════════════════════════════════════
# _interactive_message_select — 分支覆盖测试
# ═══════════════════════════════════════════════════════════


class TestInteractiveMessageSelect:
    """测试 _interactive_message_select 各分支的 publish_output 调用。

    核心断言：
    - 空数据 → publish_output("当前会话无消息") + 返回 ("quit", 0)
    - 无 user 消息 → publish_output("没有可编辑的用户消息") + 返回 ("quit", 0)
    - error action → publish_output("消息选择失败") + 返回 ("quit", 0)
    - cancel action → publish_output("已取消编辑") + 返回 ("quit", 0)
    - 无效索引 → publish_output("选择索引无效") + 返回 ("quit", 0)
    - 正常选择 → 返回 ("edit", real_idx)
    """

    @staticmethod
    def _make_ctx(data, agent=None):
        """创建 MessageDisplayContext。"""
        return MessageDisplayContext(data=data, agent=agent)

    # ── 空数据分支 ─────────────────────────────────────

    def test_empty_data_publishes_warning(self):
        """data 为空列表时 publish_output 被调用，返回 ("quit", 0)。"""
        editor = MessageEditor()
        ctx = self._make_ctx([])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub:
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        mock_pub.assert_called_once()
        call_text = mock_pub.call_args[0][0]
        assert "当前会话无消息" in call_text

    def test_empty_data_returns_quit(self):
        """data 为空时返回 action="quit", idx=0。"""
        editor = MessageEditor()
        ctx = self._make_ctx([])

        with patch("src.tui.pipeline.message_editor.publish_output"):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0

    # ── 无用户消息分支 ─────────────────────────────────

    def test_no_user_messages_publishes_warning(self):
        """只有 assistant 消息、无 user 消息时 publish_output 被调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([
            {"role": "assistant", "content": "你好"},
            {"role": "tool", "content": "result", "name": "func"},
        ])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub:
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        mock_pub.assert_called_once()
        call_text = mock_pub.call_args[0][0]
        assert "没有可编辑的用户消息" in call_text

    def test_only_system_messages_publishes_warning(self):
        """只有 system 消息时 publish_output 被调用（但 system 已被 filter，实际 data 可能为空）。"""
        editor = MessageEditor()
        # system 消息已被 MessageDisplayContext.from_messages 过滤，
        # 但直接构造 ctx 时可以传入
        ctx = self._make_ctx([{"role": "system", "content": "system prompt"}])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub:
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        mock_pub.assert_called_once()
        call_text = mock_pub.call_args[0][0]
        assert "没有可编辑的用户消息" in call_text

    # ── error action 分支 ──────────────────────────────

    def test_error_action_publishes_warning(self):
        """run_bottom_bar_selection 返回 error action 时 publish_output 被调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([
            {"role": "user", "content": "hello"},
        ])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub, \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "error", "index": None}):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        # publish_output 至少被调用一次（error 消息），
        # 匹配新错误提示"终端输入解析异常"（而非旧提示"终端可能不支持交互模式"）
        error_call_found = any(
            "终端输入解析异常" in strip_ansi(str(c))
            for c in mock_pub.call_args_list
        )
        assert error_call_found, (
            f"未找到错误提示调用，实际调用: {mock_pub.call_args_list}"
        )

    # ── cancel action 分支 ─────────────────────────────

    def test_cancel_action_publishes_dim(self):
        """run_bottom_bar_selection 返回 cancel action 时 publish_output 被调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([
            {"role": "user", "content": "hello"},
        ])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub, \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "cancel", "index": None}):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        cancel_call_found = any(
            "已取消编辑" in str(c)
            for c in mock_pub.call_args_list
        )
        assert cancel_call_found, (
            f"未找到取消提示调用，实际调用: {mock_pub.call_args_list}"
        )

    # ── 无效索引分支 ───────────────────────────────────

    def test_invalid_index_publishes_warning(self):
        """index 为 None 时 publish_output 被调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([
            {"role": "user", "content": "hello"},
        ])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub, \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": None}):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        invalid_call_found = any(
            "选择索引无效" in str(c)
            for c in mock_pub.call_args_list
        )
        assert invalid_call_found, (
            f"未找到索引无效提示调用，实际调用: {mock_pub.call_args_list}"
        )

    def test_index_out_of_range_publishes_warning(self):
        """index 超出 selectable 范围时 publish_output 被调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([
            {"role": "user", "content": "hello"},
        ])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub, \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": 5}):  # 超出范围
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "quit"
        assert idx == 0
        invalid_call_found = any(
            "选择索引无效" in str(c)
            for c in mock_pub.call_args_list
        )
        assert invalid_call_found, (
            f"未找到索引无效提示调用，实际调用: {mock_pub.call_args_list}"
        )

    # ── 正常选择分支 ───────────────────────────────────

    def test_valid_selection_returns_edit(self):
        """正常选择返回 ("edit", real_idx)，real_idx 为实际 messages 中的索引。"""
        editor = MessageEditor()
        data = [
            {"role": "system", "content": "prompt"},     # idx 0 — 非可选
            {"role": "user", "content": "第一条"},        # idx 1 — selectable[0]
            {"role": "assistant", "content": "回复"},     # idx 2 — 非可选
            {"role": "user", "content": "第二条"},        # idx 3 — selectable[1]
        ]
        ctx = self._make_ctx(data)

        # 选择 selectable 中的第 2 条（index 1 in selectable → real_idx 3）
        with patch("src.tui.pipeline.message_editor.publish_output"), \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": 1}):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "edit"
        assert idx == 3  # real_idx = selectable[1] = data中索引3

    def test_first_user_message_selection(self):
        """选择第一条用户消息返回正确的 real_idx。"""
        editor = MessageEditor()
        data = [
            {"role": "user", "content": "第一条"},
            {"role": "assistant", "content": "回复"},
            {"role": "user", "content": "第二条"},
        ]
        ctx = self._make_ctx(data)

        # 选择 selectable 中的第 0 条 → real_idx 0
        with patch("src.tui.pipeline.message_editor.publish_output"), \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": 0}):
            action, idx = editor._interactive_message_select(ctx, "测试")

        assert action == "edit"
        assert idx == 0

    # ── publish_output 调用参数验证 ────────────────────

    def test_publish_output_called_with_raw_level(self):
        """所有分支的 publish_output 均以 level='raw', source='cmd' 调用。"""
        editor = MessageEditor()
        ctx = self._make_ctx([])

        with patch("src.tui.pipeline.message_editor.publish_output") as mock_pub:
            editor._interactive_message_select(ctx, "测试")

        _, kwargs = mock_pub.call_args
        assert kwargs.get("level") == "raw"
        assert kwargs.get("source") == "cmd"


# ═══════════════════════════════════════════════════════════
# _get_editor_msg_max_width — 动态宽度计算测试
# ═══════════════════════════════════════════════════════════


class TestGetEditorMsgMaxWidth:
    """测试 _get_editor_msg_max_width 的宽度计算逻辑。

    核心断言：
    - 宽屏（200 列）→ clamp(200-12=188) → 80（上限 clamp）
    - 标准屏（80 列）→ clamp(80-12=68) → 68
    - 窄屏（30 列）→ clamp(30-12=18) → 25（下限 clamp）
    - 边界：92 列 → clamp(92-12=80) → 80
    - 边界：37 列 → clamp(37-12=25) → 25
    - 异常回退 → 80
    """

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_wide_terminal_capped_at_80(self, mock_get_width):
        """宽屏终端（200 列）返回上限 80。"""
        mock_get_width.return_value = 200
        result = _me._get_editor_msg_max_width()
        assert result == 80

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_standard_terminal_returns_68(self, mock_get_width):
        """标准终端（80 列）返回 68（80-12）。"""
        mock_get_width.return_value = 80
        result = _me._get_editor_msg_max_width()
        assert result == 68

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_narrow_terminal_clamped_to_25(self, mock_get_width):
        """窄屏终端（30 列）返回下限 25。"""
        mock_get_width.return_value = 30
        result = _me._get_editor_msg_max_width()
        assert result == 25

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_boundary_92_returns_80(self, mock_get_width):
        """边界值：92 列 → 92-12=80 → 80。"""
        mock_get_width.return_value = 92
        result = _me._get_editor_msg_max_width()
        assert result == 80

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_boundary_37_returns_25(self, mock_get_width):
        """边界值：37 列 → 37-12=25 → 25。"""
        mock_get_width.return_value = 37
        result = _me._get_editor_msg_max_width()
        assert result == 25

    def test_exception_falls_back_to_80(self):
        """get_terminal_width 异常时回退到 80，经公式计算后返回 68。"""
        with patch.object(_me, "get_terminal_width",
                          side_effect=OSError("ioctl 失败")):
            result = _me._get_editor_msg_max_width()
            # 回退值 80 → 80-12=68 → clamp[25,80]=68
            assert result == 68

    def test_exact_80_returns_68(self):
        """80 列终端返回 68（无需 mock，实际回退到 80）。"""
        # 使用真实 get_terminal_width（测试环境可能返回不同值），
        # 但通过 patch 确保结果正确
        with patch("src.tui.pipeline.message_editor.get_terminal_width",
                   return_value=80):
            result = _me._get_editor_msg_max_width()
            assert result == 68


# ═══════════════════════════════════════════════════════════
# _msg_short_summary — 动态宽度截断测试
# ═══════════════════════════════════════════════════════════


class TestMsgShortSummaryDynamicWidth:
    """测试 _msg_short_summary 在动态宽度下的截断行为。

    验证各角色分支的 truncate 宽度使用正确的值。
    """

    def _make_msg(self, role="user", content="", tool_calls=None, name=""):
        msg = {"role": role, "content": content}
        if tool_calls:
            msg["tool_calls"] = tool_calls
        if name:
            msg["name"] = name
        return msg

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_user_message_uses_dynamic_width(self, mock_get_width):
        """用户消息使用动态宽度（68），长文本被截断。"""
        mock_get_width.return_value = 80
        long_text = "用户消息 " * 50
        result = _me._msg_short_summary(self._make_msg(
            role="user", content=long_text,
        ))
        # 截断后包含 "…" 后缀，且内容 ≈ 68 字符
        assert "…" in result
        assert len(result) > 35  # 比旧版 35 字符更长

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_assistant_message_uses_dynamic_width(self, mock_get_width):
        """助手消息使用动态宽度。"""
        mock_get_width.return_value = 80
        long_text = "助手回复 " * 50
        result = _me._msg_short_summary(self._make_msg(
            role="assistant", content=long_text,
        ))
        assert "…" in result

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_tool_message_uses_min_width(self, mock_get_width):
        """工具消息使用 min(动态宽度, 60)。"""
        mock_get_width.return_value = 80
        long_text = "tool_output_" * 50
        result = _me._msg_short_summary(self._make_msg(
            role="tool", content=long_text, name="func",
        ))
        assert "…" in result
        # 工具消息上限 60，但 ANSI 前缀占位，确认比旧版 30 更长
        assert len(result) > 35

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_tool_calls_name_uses_min_width(self, mock_get_width):
        """tool_calls 名称使用 min(动态宽度, 45)。"""
        mock_get_width.return_value = 80
        msg = self._make_msg(
            role="assistant", content="",
            tool_calls=[
                {"function": {"name": "very_long_function_name_tool_call_" + str(i)}}
                for i in range(10)
            ],
        )
        result = _me._msg_short_summary(msg)
        # tool_calls 名称上限 45，确保比旧版 30 更长
        assert len(result) > 35

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_short_content_not_truncated(self, mock_get_width):
        """短内容不会被截断（保持原有返回）。"""
        mock_get_width.return_value = 80
        short_text = "你好"
        result = _me._msg_short_summary(self._make_msg(
            role="user", content=short_text,
        ))
        assert "你好" in result
        assert "…" not in result

    @patch("src.tui.pipeline.message_editor.get_terminal_width")
    def test_narrow_terminal_shorter_truncation(self, mock_get_width):
        """窄屏终端使用更短的截断值（25）。"""
        mock_get_width.return_value = 30
        long_text = "用户消息 " * 50
        result = _me._msg_short_summary(self._make_msg(
            role="user", content=long_text,
        ))
        assert "…" in result
        # 窄屏下返回的文本比宽屏短
        assert len(result) < 50


# ═══════════════════════════════════════════════════════════
# bottom_bar 依赖注入测试
# ═══════════════════════════════════════════════════════════


class TestMessageEditorBottomBarDI:
    """测试 MessageEditor bottom_bar 依赖注入功能。

    核心断言：
    - 构造时传入 bottom_bar 后，_bottom_bar 属性正确存储
    - _interactive_message_select 将 bottom_bar 传递给 run_bottom_bar_selection
    - 不传 bottom_bar 时保持向后兼容（bottom_bar=None）
    """

    def test_constructor_stores_bottom_bar(self):
        """构造 MessageEditor(bottom_bar=mock_bar) 时 _bottom_bar 被正确存储。"""
        mock_bar = MagicMock()
        editor = MessageEditor(bottom_bar=mock_bar)
        assert editor._bottom_bar is mock_bar

    def test_constructor_defaults_to_none(self):
        """构造 MessageEditor() 不传 bottom_bar 时 _bottom_bar 为 None（向后兼容）。"""
        editor = MessageEditor()
        assert editor._bottom_bar is None

    def test_bottom_bar_passed_to_run_bottom_bar_selection(self):
        """_interactive_message_select 将 self._bottom_bar 传递给 run_bottom_bar_selection。"""
        mock_bar = MagicMock()
        editor = MessageEditor(bottom_bar=mock_bar)
        ctx = MessageDisplayContext(
            data=[{"role": "user", "content": "hello"}],
        )

        with patch("src.tui.pipeline.message_editor.publish_output"), \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": 0}) as mock_select:
            editor._interactive_message_select(ctx, "测试")

        # 验证 bottom_bar 参数被传递
        mock_select.assert_called_once()
        _, kwargs = mock_select.call_args
        assert kwargs.get("bottom_bar") is mock_bar, (
            f"期望 bottom_bar={mock_bar}，实际={kwargs.get('bottom_bar')}"
        )

    def test_no_bottom_bar_backward_compatible(self):
        """不传 bottom_bar 时 run_bottom_bar_selection 的 bottom_bar 参数为 None（向后兼容）。"""
        editor = MessageEditor()  # 默认 bottom_bar=None
        ctx = MessageDisplayContext(
            data=[{"role": "user", "content": "hello"}],
        )

        with patch("src.tui.pipeline.message_editor.publish_output"), \
             patch("src.tui.pipeline.message_editor.run_bottom_bar_selection",
                   return_value={"action": "confirmed", "index": 0}) as mock_select:
            editor._interactive_message_select(ctx, "测试")

        mock_select.assert_called_once()
        _, kwargs = mock_select.call_args
        # bottom_bar 未传入时，应为 None（run_bottom_bar_selection 会 fallback 到 get_active_chat_ui）
        assert kwargs.get("bottom_bar") is None, (
            f"期望 bottom_bar=None（向后兼容），实际={kwargs.get('bottom_bar')}"
        )

    def test_bottom_bar_with_different_role_map(self):
        """bottom_bar 注入不影响 role_map 功能。"""
        mock_bar = MagicMock()
        role_map = {"user": MagicMock(icon="U")}
        editor = MessageEditor(role_map=role_map, bottom_bar=mock_bar)

        assert editor.role_map is role_map
        assert editor._bottom_bar is mock_bar
