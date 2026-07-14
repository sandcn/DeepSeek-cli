"""chat_ui 渲染状态模块单元测试 — _RenderState 状态机与生命周期。

测试覆盖：
  - get_reasoning(): INACTIVE→ACTIVE→CLOSED 三态转换、惰性创建 IncrementalRenderer
  - get_content(): 惰性创建 IncrementalRenderer、重复调用同实例
  - close_reasoning(): 写入分隔线、close、幂等、None 安全
  - reopen_reasoning(): CLOSED→INACTIVE 重置、非 CLOSED 无操作
  - close_content(): close、None 安全
  - close_all(): close_reasoning + close_content
"""

from __future__ import annotations

import sys
import pytest
from unittest.mock import MagicMock, patch

# ── 将项目根目录加入 sys.path（Termux 环境需要）───
sys.path.insert(0, "/home/DeepSeek-cli")


# ═══════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════

def _make_render_state(**overrides):
    """创建 _RenderState 实例，字段可覆盖。

    用法:
        rs = _make_render_state()
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED)
    """
    from src.tui.consumer.render_state import _RenderState
    return _RenderState(**overrides)


def _make_mock_incremental_renderer():
    """创建模拟 IncrementalRenderer（write/close 均为 MagicMock）。"""
    renderer = MagicMock()
    renderer.write = MagicMock()
    renderer.close = MagicMock()
    renderer.refresh_width = MagicMock()
    return renderer


# ═══════════════════════════════════════════════════════════
# TestRenderStateGetReasoning — 推理渲染器获取
# ═══════════════════════════════════════════════════════════

class TestRenderStateGetReasoning:
    """_RenderState.get_reasoning() 状态机转换测试。

    覆盖状态转换：
      INACTIVE → 创建 IncrementalRenderer + 切换到 ACTIVE
      ACTIVE   → 返回已有渲染器（不重复创建）
      CLOSED   → 返回 None（防止惰性重建）
    """

    def test_initial_state_inactive(self):
        """初始状态为 INACTIVE，reasoning 为 None。"""
        rs = _make_render_state()
        from src.tui.consumer.render_state import _ReasoningState

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    @patch("src.renderer.IncrementalRenderer")
    def test_get_reasoning_creates_and_switches_to_active(self, MockRenderer):
        """首次 get_reasoning() → 创建 IncrementalRenderer + 切换到 ACTIVE。"""
        mock_rr = _make_mock_incremental_renderer()
        MockRenderer.return_value = mock_rr
        rs = _make_render_state()

        result = rs.get_reasoning()

        assert result is mock_rr
        assert rs.reasoning is mock_rr
        from src.tui.consumer.render_state import _ReasoningState
        assert rs.reasoning_state == _ReasoningState.ACTIVE
        MockRenderer.assert_called_once_with(
            style="dim",
            _file=sys.__stdout__,
            typing_speed=1000,
            show_indicator=False,
        )

    def test_get_reasoning_returns_same_instance(self):
        """再次调用返回同一实例（不重复创建）。"""
        rs = _make_render_state()
        mock_rr = _make_mock_incremental_renderer()
        rs.reasoning = mock_rr
        from src.tui.consumer.render_state import _ReasoningState
        rs.reasoning_state = _ReasoningState.ACTIVE

        first = rs.get_reasoning()
        second = rs.get_reasoning()

        assert first is second
        assert first is mock_rr

    def test_get_reasoning_closed_returns_none(self):
        """CLOSED 状态 → 返回 None。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED)

        result = rs.get_reasoning()
        assert result is None

    @patch("src.renderer.IncrementalRenderer")
    def test_get_reasoning_closed_does_not_create(self, MockRenderer):
        """CLOSED 状态即使 reasoning=None 也不创建渲染器。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED, reasoning=None)

        result = rs.get_reasoning()

        assert result is None
        MockRenderer.assert_not_called()


# ═══════════════════════════════════════════════════════════
# TestRenderStateGetContent — 内容渲染器获取
# ═══════════════════════════════════════════════════════════

class TestRenderStateGetContent:
    """_RenderState.get_content() 惰性创建 & 重复调用同实例测试。"""

    @patch("src.renderer.IncrementalRenderer")
    def test_get_content_creates_on_first_call(self, MockRenderer):
        """首次 get_content() → 创建 IncrementalRenderer。"""
        mock_cr = _make_mock_incremental_renderer()
        MockRenderer.return_value = mock_cr
        rs = _make_render_state()

        result = rs.get_content()

        assert result is mock_cr
        assert rs.content is mock_cr
        MockRenderer.assert_called_once_with(
            style="",
            _file=sys.__stdout__,
            typing_speed=1000,
            show_indicator=False,
            output_adapter=None,
        )

    def test_get_content_returns_same_instance(self):
        """重复调用返回同一实例（不重复创建）。"""
        rs = _make_render_state()
        mock_cr = _make_mock_incremental_renderer()
        rs.content = mock_cr

        first = rs.get_content()
        second = rs.get_content()

        assert first is second
        assert first is mock_cr


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseReasoning — 关闭推理渲染器
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseReasoning:
    """_RenderState.close_reasoning() 关闭推理渲染器测试。

    覆盖：
      - 正常关闭：写入分隔线 → close → 状态 CLOSED
      - reasoning=None 时安全关闭（无需写分隔线）
      - 幂等：CLOSED 状态重复调用无副作用
    """

    def test_close_reasoning_writes_separator_and_closes(self):
        """关闭时写入分隔线、close、状态→CLOSED。"""
        from src.tui.consumer.const import _THINKING_SEPARATOR
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.ACTIVE)
        mock_rr = _make_mock_incremental_renderer()
        rs.reasoning = mock_rr

        rs.close_reasoning()

        mock_rr.write.assert_called_once_with(_THINKING_SEPARATOR)
        mock_rr.close.assert_called_once()
        assert rs.reasoning is None
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_from_inactive(self):
        """从 INACTIVE 状态关闭 → CLOSED，reasoning=None 时不写分隔线。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.INACTIVE, reasoning=None)

        rs.close_reasoning()

        assert rs.reasoning_state == _ReasoningState.CLOSED
        assert rs.reasoning is None
        # 没有渲染器，不调用 write/close

    def test_close_reasoning_idempotent(self):
        """CLOSED 状态重复关闭——幂等（无副作用）。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED, reasoning=None)

        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED


# ═══════════════════════════════════════════════════════════
# TestRenderStateReopenReasoning — 重新打开推理渲染器
# ═══════════════════════════════════════════════════════════

class TestRenderStateReopenReasoning:
    """_RenderState.reopen_reasoning() 重新打开推理渲染器测试。

    覆盖：
      - CLOSED → INACTIVE，清除旧引用
      - ACTIVE 时无操作（保持当前状态）
      - INACTIVE 时无操作（保持当前状态）
      - 完整周期：close → reopen → get_reasoning 重新创建
    """

    def test_reopen_reasoning_from_closed(self):
        """CLOSED → INACTIVE，reasoning 置为 None。"""
        from src.tui.consumer.render_state import _ReasoningState
        mock_rr = _make_mock_incremental_renderer()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.CLOSED,
            reasoning=mock_rr,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    def test_reopen_reasoning_from_active_no_op(self):
        """ACTIVE 状态调用 reopen — 无操作，保持 ACTIVE。"""
        from src.tui.consumer.render_state import _ReasoningState
        mock_rr = _make_mock_incremental_renderer()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.ACTIVE,
            reasoning=mock_rr,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.ACTIVE
        assert rs.reasoning is mock_rr  # 不受影响

    def test_reopen_reasoning_from_inactive_no_op(self):
        """INACTIVE 状态调用 reopen — 无操作，保持 INACTIVE。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(
            reasoning_state=_ReasoningState.INACTIVE,
            reasoning=None,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    @patch("src.renderer.IncrementalRenderer")
    def test_reopen_reasoning_full_cycle(self, MockRenderer):
        """close → reopen → get_reasoning 完整路径：CLOSED→INACTIVE→重新创建→ACTIVE。"""
        from src.tui.consumer.render_state import _ReasoningState
        mock_rr_new = _make_mock_incremental_renderer()
        MockRenderer.return_value = mock_rr_new
        rs = _make_render_state(
            reasoning_state=_ReasoningState.CLOSED,
            reasoning=_make_mock_incremental_renderer(),
        )

        # step 1: reopen → INACTIVE
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None  # 旧引用已清除

        # step 2: get_reasoning → 创建新渲染器 → ACTIVE
        reasoning = rs.get_reasoning()
        assert reasoning is not None
        assert reasoning is mock_rr_new
        assert reasoning is rs.reasoning
        assert rs.reasoning_state == _ReasoningState.ACTIVE


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseContent — 关闭内容渲染器
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseContent:
    """_RenderState.close_content() 关闭内容渲染器测试。

    覆盖：
      - 正常关闭：close + content=None
      - content=None 时安全关闭（不抛异常）
    """

    def test_close_content_closes_and_clears(self):
        """关闭时调用 close、content 置 None。"""
        rs = _make_render_state()
        mock_cr = _make_mock_incremental_renderer()
        rs.content = mock_cr

        rs.close_content()

        mock_cr.close.assert_called_once()
        assert rs.content is None

    def test_close_content_when_none(self):
        """content=None 时 close_content() 安全跳过（不抛异常）。"""
        rs = _make_render_state(content=None)
        rs.close_content()  # 不应抛异常
        assert rs.content is None


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseAll — 关闭全部
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseAll:
    """_RenderState.close_all() 关闭所有渲染器测试。

    覆盖：
      - 关闭推理 + 关闭内容
      - 全部关闭后 reasoning=None, content=None
      - 单个 close 异常被 try/except 隔离
    """

    def test_close_all_closes_everything(self):
        """关闭推理、内容，全部置为 None。"""
        from src.tui.consumer.render_state import _ReasoningState
        mock_reasoning = _make_mock_incremental_renderer()
        mock_content = _make_mock_incremental_renderer()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            reasoning_state=_ReasoningState.ACTIVE,
            content=mock_content,
        )

        rs.close_all()

        # 推理关闭
        mock_reasoning.write.assert_called_once()
        mock_reasoning.close.assert_called_once()
        assert rs.reasoning is None
        assert rs.reasoning_state == _ReasoningState.CLOSED

        # 内容关闭
        mock_content.close.assert_called_once()
        assert rs.content is None

    def test_close_all_when_none(self):
        """推理/内容为 None 时 safe skip（不抛异常）。"""
        rs = _make_render_state(
            reasoning=None,
            content=None,
        )

        rs.close_all()  # 不应抛异常

        assert rs.reasoning is None
        assert rs.content is None

    def test_close_all_isolates_exception(self):
        """单个 close 异常被捕获，不影响其他关闭操作。"""
        mock_reasoning = _make_mock_incremental_renderer()
        mock_reasoning.close.side_effect = RuntimeError("close failed")
        mock_content = _make_mock_incremental_renderer()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            content=mock_content,
        )

        rs.close_all()  # 不应抛异常

        mock_content.close.assert_called_once()


# ═══════════════════════════════════════════════════════════
# TestReasoningStateTransitions — 状态转换集中验证
# ═══════════════════════════════════════════════════════════

class TestReasoningStateTransitions:
    """_ReasoningState.can_transition_to() 状态转换验证测试。

    覆盖：
      - 4 种合法转换 → True
      - 5 种非法转换 → False
      - 高層方法中合法转换通过断言
    """

    # ── 合法转换 ──

    def test_inactive_to_active_is_legal(self):
        """INACTIVE → ACTIVE 是合法转换。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.INACTIVE.can_transition_to(_ReasoningState.ACTIVE) is True

    def test_active_to_closed_is_legal(self):
        """ACTIVE → CLOSED 是合法转换。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.ACTIVE.can_transition_to(_ReasoningState.CLOSED) is True

    def test_inactive_to_closed_is_legal(self):
        """INACTIVE → CLOSED 是合法转换。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.INACTIVE.can_transition_to(_ReasoningState.CLOSED) is True

    def test_closed_to_inactive_is_legal(self):
        """CLOSED → INACTIVE 是合法转换。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.CLOSED.can_transition_to(_ReasoningState.INACTIVE) is True

    # ── 非法转换（5 种）──

    def test_inactive_to_inactive_is_illegal(self):
        """INACTIVE → INACTIVE 是非法转换（自环）。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.INACTIVE.can_transition_to(_ReasoningState.INACTIVE) is False

    def test_active_to_active_is_illegal(self):
        """ACTIVE → ACTIVE 是非法转换（自环）。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.ACTIVE.can_transition_to(_ReasoningState.ACTIVE) is False

    def test_active_to_inactive_is_illegal(self):
        """ACTIVE → INACTIVE 是非法转换（跳过 CLOSED）。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.ACTIVE.can_transition_to(_ReasoningState.INACTIVE) is False

    def test_closed_to_active_is_illegal(self):
        """CLOSED → ACTIVE 是非法转换（跳过 INACTIVE）。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.CLOSED.can_transition_to(_ReasoningState.ACTIVE) is False

    def test_closed_to_closed_is_illegal(self):
        """CLOSED → CLOSED 是非法转换（自环）。"""
        from src.tui.consumer.render_state import _ReasoningState
        assert _ReasoningState.CLOSED.can_transition_to(_ReasoningState.CLOSED) is False

    # ── 集成测试：高層方法中的断言不破坏合法转换 ──

    def test_get_reasoning_assertion_passes(self):
        """get_reasoning() 中 INACTIVE→ACTIVE 断言通过。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.INACTIVE, reasoning=None)
        with patch("src.renderer.IncrementalRenderer") as MockRenderer:
            mock_rr = _make_mock_incremental_renderer()
            MockRenderer.return_value = mock_rr
            result = rs.get_reasoning()
            assert result is mock_rr
            assert rs.reasoning_state == _ReasoningState.ACTIVE

    def test_close_reasoning_assertion_passes_from_inactive(self):
        """close_reasoning() 中 INACTIVE→CLOSED 断言通过。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.INACTIVE)
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_assertion_passes_from_active(self):
        """close_reasoning() 中 ACTIVE→CLOSED 断言通过。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.ACTIVE,
                                reasoning=_make_mock_incremental_renderer())
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_reopen_reasoning_assertion_passes(self):
        """reopen_reasoning() 中 CLOSED→INACTIVE 断言通过。"""
        from src.tui.consumer.render_state import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED)
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
