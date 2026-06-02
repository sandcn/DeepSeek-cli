"""ContentRenderer 边缘情况单元测试

测试范围：
1. _do_parse_info():
   - tokens 为 float('inf') → "?"
   - tokens 为 float('nan') → "?"
   - tokens 为普通 int → "Nt"
2. _truncate_msg 基础功能
3. _RenderState.force_refresh_width 边界
4. ContentRenderer._check_and_refresh_width 边界

注：_do_tool_output / _render_failure_summary 测试已迁移到
test_chat_ui_controls.py（ToolOutputControl / ToolSummaryControl）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.chat_ui._renderers import ContentRenderer
from src.chat_ui._const import _CLEAR_PARSE_LINE, RenderCommand
from src.chat_ui._render_state import _RenderState, _ReasoningState


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture
def mock_ta():
    """Mock OutputAdapter"""
    return MagicMock()


@pytest.fixture
def mock_bb():
    """Mock _BottomBar"""
    return MagicMock()


@pytest.fixture
def renderer(mock_ta, mock_bb):
    """ContentRenderer 实例，_tool_adapter 替换为 mock"""
    rs = _RenderState()
    # 直接设置 _tool_adapter 绕过惰性初始化
    rs._tool_adapter = mock_ta
    r = ContentRenderer(rs, mock_bb, on_display_messages=None)
    yield r


# ═══════════════════════════════════════════════════════
# _do_parse_info 测试（通过 ParseInfoControl 代理）
# ═══════════════════════════════════════════════════════

class TestDoParseInfo:
    """_do_parse_info 边缘情况测试"""

    def test_inf_tokens_shows_question_mark(self, renderer, mock_ta):
        """tokens=float('inf') → 显示 "?" """
        renderer._do_parse_info("tool_test", float('inf'), 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "?" in text
        assert "inft" not in text

    def test_nan_tokens_shows_question_mark(self, renderer, mock_ta):
        """tokens=float('nan') → 显示 "?" """
        renderer._do_parse_info("tool_test", float('nan'), 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "?" in text
        assert "nant" not in text

    def test_normal_int_tokens(self, renderer, mock_ta):
        """普通 int tokens → 显示 "Nt" """
        renderer._do_parse_info("tool_test", 42, 1.5)
        mock_ta.write_raw.assert_called_once()
        text = mock_ta.write_raw.call_args[0][0]
        assert "42t" in text

    def test_clear_parse_line_sentinel(self, renderer, mock_ta):
        """tokens=_CLEAR_PARSE_LINE → write_raw('\\n') """
        renderer._do_parse_info("", _CLEAR_PARSE_LINE, 0.0)
        mock_ta.write_raw.assert_called_once_with("\n")


# ═══════════════════════════════════════════════════════
# _truncate_msg 基础功能测试
# ═══════════════════════════════════════════════════════

class TestTruncateMsg:
    """_truncate_msg 统一截断函数测试"""

    def test_short_msg_unchanged(self):
        """短消息不截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("hello", 10)
        assert result == "hello"

    def test_exact_length_unchanged(self):
        """长度刚好等于 max_len → 不截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("12345", 5)
        assert result == "12345"

    def test_long_msg_truncated(self):
        """超长消息截断并追加 ..."""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("x" * 100, 10)
        assert result == "x" * 10 + "..."
        assert len(result) == 13

    def test_empty_msg_empty_result(self):
        """空消息 → 空字符串"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("", 10)
        assert result == ""

    def test_max_len_zero(self):
        """max_len=0 → 全部截断"""
        from src.chat_ui._const import _truncate_msg
        result = _truncate_msg("hello", 0)
        assert result == "..."


# ═══════════════════════════════════════════════════════
# _RenderState.force_refresh_width 测试
# ═══════════════════════════════════════════════════════

class TestRenderStateForceRefreshWidth:
    """_RenderState.force_refresh_width() 测试"""

    def test_force_refresh_calls_tool_adapter(self):
        """工具适配器已初始化 → 调用其 force_refresh_width()"""
        rs = _RenderState()
        mock_adapter = MagicMock()
        rs._tool_adapter = mock_adapter
        rs.force_refresh_width()
        mock_adapter.force_refresh_width.assert_called_once()

    def test_force_refresh_skips_none_tool_adapter(self):
        """工具适配器未初始化 → 安全跳过"""
        rs = _RenderState()
        # 不应抛出异常
        rs.force_refresh_width()

    def test_force_refresh_calls_reasoning_renderer(self):
        """推理渲染器已创建 → 调用其 refresh_width()"""
        rs = _RenderState()
        mock_rr = MagicMock()
        rs.reasoning = mock_rr
        rs.force_refresh_width()
        mock_rr.refresh_width.assert_called_once()

    def test_force_refresh_skips_none_reasoning(self):
        """推理渲染器未创建 → 安全跳过"""
        rs = _RenderState()
        rs.reasoning = None
        rs.force_refresh_width()  # 不抛异常

    def test_force_refresh_calls_content_renderer(self):
        """内容渲染器已创建 → 调用其 refresh_width()"""
        rs = _RenderState()
        mock_cr = MagicMock()
        rs.content = mock_cr
        rs.force_refresh_width()
        mock_cr.refresh_width.assert_called_once()

    def test_force_refresh_skips_none_content(self):
        """内容渲染器未创建 → 安全跳过"""
        rs = _RenderState()
        rs.content = None
        rs.force_refresh_width()  # 不抛异常

    def test_force_refresh_all_three(self):
        """所有适配器均已初始化 → 全部调用"""
        rs = _RenderState()
        mock_ta = MagicMock()
        mock_rr = MagicMock()
        mock_cr = MagicMock()
        rs._tool_adapter = mock_ta
        rs.reasoning = mock_rr
        rs.content = mock_cr
        rs.force_refresh_width()
        mock_ta.force_refresh_width.assert_called_once()
        mock_rr.refresh_width.assert_called_once()
        mock_cr.refresh_width.assert_called_once()

    def test_force_refresh_mixed_state(self):
        """部分适配器未初始化 → 仅调用已初始化的"""
        rs = _RenderState()
        mock_ta = MagicMock()
        rs._tool_adapter = mock_ta
        rs.reasoning = None
        rs.content = None
        rs.force_refresh_width()  # 不应抛异常
        mock_ta.force_refresh_width.assert_called_once()


# ═══════════════════════════════════════════════════════
# ContentRenderer._check_and_refresh_width 测试
# ═══════════════════════════════════════════════════════

class TestCheckAndRefreshWidth:
    """ContentRenderer._check_and_refresh_width() 测试"""

    @pytest.fixture
    def mock_ta(self):
        return MagicMock()

    @pytest.fixture
    def mock_bb(self):
        return MagicMock()

    @pytest.fixture
    def renderer_with_mock_rs(self, mock_ta, mock_bb):
        """ContentRenderer 实例，_rs.force_refresh_width 被 mock"""
        rs = _RenderState()
        rs._tool_adapter = mock_ta
        r = ContentRenderer(rs, mock_bb)
        r._rs.force_refresh_width = MagicMock()
        return r

    def test_no_check_within_interval(self, renderer_with_mock_rs):
        """200ms 间隔内不检查终端大小"""
        r = renderer_with_mock_rs
        # 首次调用 _check_and_refresh_width
        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            r._check_and_refresh_width()

        # 立即再次调用 → 200ms 内不应再检查
        with patch('shutil.get_terminal_size') as mock_gs2:
            r._check_and_refresh_width()
            mock_gs2.assert_not_called()

    def test_size_change_triggers_refresh(self, renderer_with_mock_rs):
        """终端大小变化 → 调用 _rs.force_refresh_width()"""
        r = renderer_with_mock_rs
        r._last_width_check = 0  # 强制检查

        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            r._check_and_refresh_width()
            # 首次设置缓存（从 (0,0) → (100,40) 触发刷新）
            assert r._cached_term_size == (100, 40)

        # 重置 mock 以便精确验证第二次调用
        r._rs.force_refresh_width.reset_mock()

        # 模拟 resize 到 80 列
        r._last_width_check = 0  # 再次强制检查
        with patch('shutil.get_terminal_size') as mock_gs2:
            mock_gs2.return_value = MagicMock(columns=80, lines=30)
            r._check_and_refresh_width()
            # force_refresh_width 应被调用（尺寸 100→80）
            r._rs.force_refresh_width.assert_called_once()
            assert r._cached_term_size == (80, 30)

    def test_size_unchanged_no_refresh(self, renderer_with_mock_rs):
        """终端大小未变 → 不调用 _rs.force_refresh_width()"""
        r = renderer_with_mock_rs
        r._last_width_check = 0

        with patch('shutil.get_terminal_size') as mock_gs:
            mock_gs.return_value = MagicMock(columns=100, lines=40)
            r._check_and_refresh_width()
            # 首次设置缓存（从 (0,0) → (100,40) 触发刷新）
            assert r._cached_term_size == (100, 40)

        # 重置 mock 以便精确验证第二次调用
        r._rs.force_refresh_width.reset_mock()

        # 再次检查，尺寸不变
        r._last_width_check = 0
        with patch('shutil.get_terminal_size') as mock_gs2:
            mock_gs2.return_value = MagicMock(columns=100, lines=40)
            r._check_and_refresh_width()
            r._rs.force_refresh_width.assert_not_called()

    def test_shutil_exception_safe(self, renderer_with_mock_rs):
        """shutil.get_terminal_size() 异常 → 安全返回，不崩溃"""
        r = renderer_with_mock_rs
        r._last_width_check = 0

        with patch('shutil.get_terminal_size', side_effect=OSError("模拟异常")):
            # 不应抛出异常
            r._check_and_refresh_width()

        # force_refresh_width 不应被调用
        r._rs.force_refresh_width.assert_not_called()
