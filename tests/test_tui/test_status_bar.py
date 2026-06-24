"""测试 StatusBar 纯渲染函数（来自 status_bar 模块）。

覆盖：
  - render_normal 普通模式状态栏
  - render_streaming_line 流式模式状态栏
  - build_normal_parts 信息段构建
"""

from __future__ import annotations

import time
from unittest.mock import patch

from src.chat_ui.parallel._text_formatter import TextFormatter
from src.chat_ui.tui._state import UISessionState, StreamingState, TUIStateTree
from src.chat_ui.tui.status_bar import (
    render_normal,
    render_streaming_line,
    build_normal_parts,
    StatusBar,
)


class TestRenderNormal:
    """render_normal 普通模式渲染测试。"""

    def test_minimal_state_returns_string(self):
        """最小状态下不报错。"""
        state = UISessionState()
        result = render_normal(state)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_model_appears_in_output(self):
        """模型名出现在渲染结果中。"""
        state = UISessionState(model="gpt-4")
        result = render_normal(state)
        assert "gpt-4" in result

    def test_no_model_shows_fallback(self):
        """无模型时显示 'no model'。"""
        state = UISessionState(model="")
        result = render_normal(state)
        assert "no model" in result

    def test_message_count_appears(self):
        """消息数 > 0 时显示计数。"""
        state = UISessionState(model="gpt-4", message_count=3)
        parts = build_normal_parts(state)
        count_found = any("3m" in p for p in parts)
        assert count_found


class TestStreamingLine:
    """render_streaming_line 流式渲染测试。"""

    def test_minimal_state_returns_string(self):
        """最小状态下不报错。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_model_appears(self):
        """模型名出现在渲染结果中。"""
        state = UISessionState(model="claude-3")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        assert "claude-3" in result

    def test_tokens_appear_when_set(self):
        """Token 计数出现在渲染结果中。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 6.0,  # 6s elapsed
            output_tokens=150,  # → auto speed ≈ 25 tok/s
        )
        result = render_streaming_line(state, streaming)
        assert "150" in result or "tok" in result

    def test_elapsed_time_under_minute(self):
        """不到 1 分钟显示秒。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 3.5,  # 3.5 秒前
        )
        result = render_streaming_line(state, streaming)
        assert "3.5" in result

    def test_speed_display_format(self):
        """速率 >= 10 显示整数。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        # speed = output_tokens / elapsed = 120 / 1.0 = 120.0 → 整数显示
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 1.0,
            output_tokens=120,
        )
        result = render_streaming_line(state, streaming)
        assert "120" in result
        assert "120." not in result  # 不应有小数点

    def test_low_speed_shows_decimal(self):
        """速率 < 1 显示两位小数。

        注意：StreamingState.elapsed 使用 time.monotonic()，
        测试需使用同一时钟源构造 start_time。
        """
        state = UISessionState(model="gpt-4")
        # speed = output_tokens / elapsed = 3 / 4.0 = 0.75 → 两位小数
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 4.0,
            output_tokens=3,
        )
        result = render_streaming_line(state, streaming)
        assert ".75" in result


class TestBuildNormalParts:
    """build_normal_parts 信息段构建测试。"""

    def test_empty_state_returns_model_only(self):
        """空状态返回仅含模型的信息段。"""
        state = UISessionState()
        parts = build_normal_parts(state)
        assert len(parts) >= 1

    def test_message_count_adds_part(self):
        """消息数 > 0 时增加信息段。"""
        state = UISessionState(model="gpt-4", message_count=5)
        parts = build_normal_parts(state)
        assert len(parts) >= 2  # model + msg count

    def test_tokens_add_parts(self):
        """Token 用量 > 0 时增加信息段。"""
        state = UISessionState(model="gpt-4", input_tokens=100, output_tokens=200)
        parts = build_normal_parts(state)
        assert len(parts) >= 2


class TestStatusBarInstance:
    """StatusBar 实例方法测试。"""

    def test_status_bar_render_normal(self):
        """测试 StatusBar.render() 在非流式模式下的输出。"""
        tree = TUIStateTree()
        tree.update_session(model="test-model", message_count=5)
        sb = StatusBar(tree)
        result = sb.render()
        assert "test-model" in result
        assert "5" in result

    def test_status_bar_render_streaming(self):
        """测试 StatusBar.render() 在流式模式下的输出。"""
        tree = TUIStateTree()
        tree.streaming.start()
        tree.update_session(model="test-model")
        sb = StatusBar(tree)
        result = sb.render()
        assert "test-model" in result
        assert "t/" in result

    def test_status_bar_start_stop_streaming(self):
        """测试 start_streaming/stop_streaming 的状态转换。"""
        tree = TUIStateTree()
        sb = StatusBar(tree)
        assert sb.streaming is False
        sb.start_streaming()
        assert sb.streaming is True
        sb.stop_streaming()
        assert sb.streaming is False

    def test_status_bar_update_streaming_tokens(self):
        """测试 update_streaming_tokens 更新 token 计数。"""
        tree = TUIStateTree()
        sb = StatusBar(tree)
        sb.start_streaming()
        sb.update_streaming_tokens(100)
        assert tree.streaming.output_tokens == 100


class TestFormatTokenCount:
    """format_token_count 格式化函数测试（委托 TextFormatter）。"""

    def test_format_token_count_zero(self):
        result = TextFormatter.format_token_count(0)
        assert result == "0"

    def test_format_token_count_k(self):
        result = TextFormatter.format_token_count(1500)
        assert "1.5k" in result

    def test_format_token_count_small(self):
        result = TextFormatter.format_token_count(42)
        assert result == "42"


class TestRenderNormalNarrow:
    """窄屏渲染测试（使用 monkeypatch 模拟窄屏）。"""

    def test_render_normal_narrow_no_ansi_corruption(self, monkeypatch):
        """测试窄屏渲染不会损坏 ANSI 转义序列。

        注：使用 monkeypatch 模拟窄屏环境，CI 慢速环境可能 flaky。
        """
        from src.chat_ui.tui.status_bar import render_normal
        monkeypatch.setattr("src.chat_ui.tui.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr(
            "src.chat_ui.tui.status_bar.get_terminal_width", lambda: 30,
        )
        state = UISessionState(model="test-model", message_count=10, status_text="processing")
        result = render_normal(state)
        assert isinstance(result, str)
        assert "\033[0m" in result  # 确保有样式重置

