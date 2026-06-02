"""chat_ui 渲染状态模块单元测试 — _RenderState 状态机与生命周期。

测试覆盖：
  - get_reasoning(): INACTIVE→ACTIVE→CLOSED 三态转换、惰性创建、ControlList 注册
  - get_content(): 惰性创建、重复调用同实例、ControlList 注册
  - close_reasoning(): 写入分隔线、close、ControlList remove、幂等、None 安全
  - reopen_reasoning(): CLOSED→INACTIVE 重置、非 CLOSED 无操作
  - close_content(): close、ControlList remove、None 安全
  - close_all(): close_reasoning + close_content + flush tool_adapter
  - force_refresh_width(): 遍历所有活跃渲染器、异常隔离
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
    from src.chat_ui._render_state import _RenderState
    return _RenderState(**overrides)


def _make_mock_markdown_control():
    """创建模拟 MarkdownControl（write/close/refresh_width 均为 MagicMock）。"""
    ctrl = MagicMock()
    ctrl.write = MagicMock()
    ctrl.close = MagicMock()
    ctrl.refresh_width = MagicMock()
    return ctrl


# ═══════════════════════════════════════════════════════════
# TestRenderStateGetReasoning — 推理渲染器获取
# ═══════════════════════════════════════════════════════════

class TestRenderStateGetReasoning:
    """_RenderState.get_reasoning() 状态机转换测试。

    覆盖状态转换：
      INACTIVE → 创建渲染器 + 切换到 ACTIVE
      ACTIVE   → 返回已有渲染器（不重复创建）
      CLOSED   → 返回 None（防止惰性重建）
    以及 ControlList 注册行为。
    """

    def test_initial_state_inactive(self):
        """初始状态为 INACTIVE，reasoning 为 None。"""
        rs = _make_render_state()
        from src.chat_ui._const import _ReasoningState

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    def test_get_reasoning_creates_and_switches_to_active(self):
        """首次 get_reasoning() → 创建渲染器 + 切换到 ACTIVE。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl) as mock_create:
            result = rs.get_reasoning()

        assert result is mock_ctrl
        assert rs.reasoning is mock_ctrl
        from src.chat_ui._const import _ReasoningState
        assert rs.reasoning_state == _ReasoningState.ACTIVE
        mock_create.assert_called_once_with(style="dim")

    def test_get_reasoning_returns_same_instance(self):
        """再次调用返回同一实例（不重复创建）。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl) as mock_create:
            first = rs.get_reasoning()
            second = rs.get_reasoning()

        assert first is second
        assert first is mock_ctrl
        mock_create.assert_called_once()  # 只创建一次

    def test_get_reasoning_closed_returns_none(self):
        """CLOSED 状态 → 返回 None。"""
        from src.chat_ui._const import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED)

        result = rs.get_reasoning()
        assert result is None

    def test_get_reasoning_closed_does_not_create(self):
        """CLOSED 状态即使 reasoning=None 也不创建渲染器。"""
        from src.chat_ui._const import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED, reasoning=None)
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl) as mock_create:
            result = rs.get_reasoning()

        assert result is None
        mock_create.assert_not_called()

    def test_get_reasoning_registers_to_control_list(self):
        """创建后注册到 ControlList。"""
        rs = _make_render_state()
        mock_list = MagicMock()
        rs.on_control_created = mock_list.add
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl):
            rs.get_reasoning()

        mock_list.add.assert_called_once_with(mock_ctrl)

    def test_get_reasoning_skips_register_when_no_control_list(self):
        """on_control_created 为 None 时不注册（不抛异常）。"""
        rs = _make_render_state()
        rs.on_control_created = None
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl):
            result = rs.get_reasoning()

        assert result is mock_ctrl  # 创建成功


# ═══════════════════════════════════════════════════════════
# TestRenderStateGetContent — 内容渲染器获取
# ═══════════════════════════════════════════════════════════

class TestRenderStateGetContent:
    """_RenderState.get_content() 惰性创建与 ControlList 注册测试。

    覆盖：
      - 首次调用创建渲染器并注册
      - 重复调用返回同一实例
      - _control_list=None 时安全跳过注册
    """

    def test_get_content_creates_on_first_call(self):
        """首次 get_content() → 创建渲染器。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl) as mock_create:
            result = rs.get_content()

        assert result is mock_ctrl
        assert rs.content is mock_ctrl
        mock_create.assert_called_once_with()  # 无参调用

    def test_get_content_returns_same_instance(self):
        """重复调用返回同一实例（不重复创建）。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl) as mock_create:
            first = rs.get_content()
            second = rs.get_content()

        assert first is second
        mock_create.assert_called_once()

    def test_get_content_registers_to_control_list(self):
        """创建后注册到 ControlList。"""
        rs = _make_render_state()
        mock_list = MagicMock()
        rs.on_control_created = mock_list.add
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl):
            rs.get_content()

        mock_list.add.assert_called_once_with(mock_ctrl)

    def test_get_content_skips_register_when_no_control_list(self):
        """on_control_created 为 None 时不注册（不抛异常）。"""
        rs = _make_render_state()
        rs.on_control_created = None
        mock_ctrl = _make_mock_markdown_control()

        with patch.object(rs, "_create_markdown_control", return_value=mock_ctrl):
            result = rs.get_content()

        assert result is mock_ctrl
        assert rs.content is mock_ctrl


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseReasoning — 关闭推理渲染器
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseReasoning:
    """_RenderState.close_reasoning() 关闭推理渲染器测试。

    覆盖：
      - 正常关闭：写入分隔线 → close → ControlList remove → 状态 CLOSED
      - reasoning=None 时安全关闭（无需写分隔线）
      - 幂等：CLOSED 状态重复调用无副作用
      - _control_list=None 时安全跳过 remove
    """

    def test_close_reasoning_writes_separator_and_closes(self):
        """关闭时写入分隔线、close、ControlList remove、状态→CLOSED。"""
        from src.chat_ui._const import _THINKING_SEPARATOR, _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.ACTIVE)
        mock_ctrl = _make_mock_markdown_control()
        rs.reasoning = mock_ctrl
        mock_list = MagicMock()
        rs.on_control_removed = mock_list.remove

        rs.close_reasoning()

        mock_ctrl.write.assert_called_once_with(_THINKING_SEPARATOR)
        mock_ctrl.close.assert_called_once()
        mock_list.remove.assert_called_once_with(mock_ctrl)
        assert rs.reasoning is None
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_from_inactive(self):
        """从 INACTIVE 状态关闭 → CLOSED，reasoning=None 时不写分隔线。"""
        from src.chat_ui._const import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.INACTIVE, reasoning=None)
        mock_list = MagicMock()
        rs.on_control_removed = mock_list.remove

        rs.close_reasoning()

        assert rs.reasoning_state == _ReasoningState.CLOSED
        assert rs.reasoning is None
        # 没有渲染器，不调用 write/close/remove

    def test_close_reasoning_idempotent(self):
        """CLOSED 状态重复关闭——幂等（无副作用）。"""
        from src.chat_ui._const import _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.CLOSED, reasoning=None)

        # 第二次关闭不应抛异常
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_skips_remove_when_no_control_list(self):
        """on_control_removed=None 时安全跳过 remove（不抛异常）。"""
        from src.chat_ui._const import _THINKING_SEPARATOR, _ReasoningState
        rs = _make_render_state(reasoning_state=_ReasoningState.ACTIVE)
        mock_ctrl = _make_mock_markdown_control()
        rs.reasoning = mock_ctrl
        rs.on_control_removed = None

        rs.close_reasoning()

        mock_ctrl.write.assert_called_once_with(_THINKING_SEPARATOR)
        mock_ctrl.close.assert_called_once()
        assert rs.reasoning is None
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
    """

    def test_reopen_reasoning_from_closed(self):
        """CLOSED → INACTIVE，reasoning 置为 None。"""
        from src.chat_ui._const import _ReasoningState
        mock_ctrl = _make_mock_markdown_control()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.CLOSED,
            reasoning=mock_ctrl,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    def test_reopen_reasoning_from_active_no_op(self):
        """ACTIVE 状态调用 reopen — 无操作，保持 ACTIVE。"""
        from src.chat_ui._const import _ReasoningState
        mock_ctrl = _make_mock_markdown_control()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.ACTIVE,
            reasoning=mock_ctrl,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.ACTIVE
        assert rs.reasoning is mock_ctrl  # 不受影响

    def test_reopen_reasoning_from_inactive_no_op(self):
        """INACTIVE 状态调用 reopen — 无操作，保持 INACTIVE。"""
        from src.chat_ui._const import _ReasoningState
        rs = _make_render_state(
            reasoning_state=_ReasoningState.INACTIVE,
            reasoning=None,
        )

        rs.reopen_reasoning()

        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None

    def test_reopen_reasoning_full_cycle(self):
        """close → reopen → get_reasoning 完整路径：CLOSED→INACTIVE→重新创建→ACTIVE。"""
        from src.chat_ui._const import _ReasoningState
        mock_ctrl = _make_mock_markdown_control()
        mock_callback = MagicMock()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.CLOSED,
            reasoning=mock_ctrl,
            on_control_created=mock_callback.add,
            on_control_removed=mock_callback.remove,
        )

        # step 1: reopen → INACTIVE
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        assert rs.reasoning is None  # 旧引用已清除

        # step 2: get_reasoning → 创建新渲染器 → ACTIVE
        reasoning = rs.get_reasoning()
        assert reasoning is not None
        assert reasoning is not mock_ctrl  # 新实例
        assert rs.reasoning_state == _ReasoningState.ACTIVE

    def test_reopen_reasoning_then_get_reasoning_controls_registered(self):
        """reopen → get_reasoning 新控件注册到 ControlList。"""
        from src.chat_ui._const import _ReasoningState
        mock_on_created = MagicMock()
        rs = _make_render_state(
            reasoning_state=_ReasoningState.CLOSED,
            reasoning=_make_mock_markdown_control(),
            on_control_created=mock_on_created,
        )

        rs.reopen_reasoning()
        reasoning = rs.get_reasoning()

        assert reasoning is not None
        # get_reasoning 在新创建控件时调用 on_control_created()
        mock_on_created.assert_called_once_with(reasoning)


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseContent — 关闭内容渲染器
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseContent:
    """_RenderState.close_content() 关闭内容渲染器测试。

    覆盖：
      - 正常关闭：close + ControlList remove + content=None
      - content=None 时安全关闭（不抛异常）
      - _control_list=None 时安全跳过 remove
    """

    def test_close_content_closes_and_removes(self):
        """关闭时调用 close、ControlList remove、content 置 None。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()
        rs.content = mock_ctrl
        mock_list = MagicMock()
        rs.on_control_removed = mock_list.remove

        rs.close_content()

        mock_ctrl.close.assert_called_once()
        mock_list.remove.assert_called_once_with(mock_ctrl)
        assert rs.content is None

    def test_close_content_when_none(self):
        """content=None 时 close_content() 安全跳过（不抛异常）。"""
        rs = _make_render_state(content=None)
        rs.close_content()  # 不应抛异常
        assert rs.content is None

    def test_close_content_skips_remove_when_no_control_list(self):
        """on_control_removed=None 时安全跳过 remove。"""
        rs = _make_render_state()
        mock_ctrl = _make_mock_markdown_control()
        rs.content = mock_ctrl
        rs.on_control_removed = None

        rs.close_content()

        mock_ctrl.close.assert_called_once()
        assert rs.content is None


# ═══════════════════════════════════════════════════════════
# TestRenderStateCloseAll — 关闭全部
# ═══════════════════════════════════════════════════════════

class TestRenderStateCloseAll:
    """_RenderState.close_all() 关闭所有渲染器测试。

    覆盖：
      - 关闭推理 + 关闭内容 + flush 工具适配器
      - 全部关闭后 reasoning=None, content=None
      - 适配器为 None 时安全跳过
    """

    def test_close_all_closes_everything(self):
        """关闭推理、内容，flush 工具适配器，全部置为 None。"""
        from src.chat_ui._const import _ReasoningState
        mock_reasoning = _make_mock_markdown_control()
        mock_content = _make_mock_markdown_control()
        mock_adapter = MagicMock()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            reasoning_state=_ReasoningState.ACTIVE,
            content=mock_content,
            _tool_adapter=mock_adapter,
        )
        mock_list = MagicMock()
        rs.on_control_removed = mock_list.remove

        rs.close_all()

        # 推理关闭
        mock_reasoning.write.assert_called_once()
        mock_reasoning.close.assert_called_once()
        assert rs.reasoning is None
        assert rs.reasoning_state == _ReasoningState.CLOSED

        # 内容关闭
        mock_content.close.assert_called_once()
        assert rs.content is None

        # 工具适配器 flush
        mock_adapter.flush.assert_called_once()

    def test_close_all_without_tool_adapter(self):
        """_tool_adapter=None 时 safe skip（不抛异常）。"""
        mock_reasoning = _make_mock_markdown_control()
        mock_content = _make_mock_markdown_control()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            content=mock_content,
            _tool_adapter=None,
        )

        rs.close_all()  # 不应抛异常

        assert rs.reasoning is None
        assert rs.content is None

    def test_close_all_flush_exception_isolation(self):
        """tool_adapter.flush() 异常被捕获，不影响其他关闭操作。"""
        mock_reasoning = _make_mock_markdown_control()
        mock_content = _make_mock_markdown_control()
        mock_adapter = MagicMock()
        mock_adapter.flush.side_effect = RuntimeError("flush failed")
        rs = _make_render_state(
            reasoning=mock_reasoning,
            content=mock_content,
            _tool_adapter=mock_adapter,
        )

        rs.close_all()  # 不应抛异常

        mock_reasoning.close.assert_called_once()
        mock_content.close.assert_called_once()
        mock_adapter.flush.assert_called_once()


# ═══════════════════════════════════════════════════════════
# TestRenderStateForceRefreshWidth — 强制刷新宽度
# ═══════════════════════════════════════════════════════════

class TestRenderStateForceRefreshWidth:
    """_RenderState.force_refresh_width() 宽度刷新测试。

    覆盖：
      - 遍历所有活跃渲染器调用 refresh_width
      - 部分渲染器为 None 时安全跳过
      - 单个渲染器异常不阻塞其他渲染器
    """

    def test_refresh_width_all_active(self):
        """所有活跃渲染器均调用 refresh_width。"""
        mock_reasoning = _make_mock_markdown_control()
        mock_content = _make_mock_markdown_control()
        mock_adapter = MagicMock()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            content=mock_content,
            _tool_adapter=mock_adapter,
        )

        rs.force_refresh_width()

        mock_adapter.force_refresh_width.assert_called_once()
        mock_reasoning.refresh_width.assert_called_once()
        mock_content.refresh_width.assert_called_once()

    def test_refresh_width_partial_active(self):
        """部分渲染器为 None 时安全跳过。"""
        rs = _make_render_state(reasoning=None, content=None, _tool_adapter=None)
        rs.force_refresh_width()  # 不应抛异常

    def test_refresh_width_exception_isolation(self):
        """单个 refresh_width 异常不阻塞其他渲染器。"""
        mock_reasoning = _make_mock_markdown_control()
        mock_reasoning.refresh_width.side_effect = RuntimeError("fail")
        mock_content = _make_mock_markdown_control()
        mock_adapter = MagicMock()
        rs = _make_render_state(
            reasoning=mock_reasoning,
            content=mock_content,
            _tool_adapter=mock_adapter,
        )

        rs.force_refresh_width()  # 不应抛异常

        mock_adapter.force_refresh_width.assert_called_once()
        mock_content.refresh_width.assert_called_once()


# ═══════════════════════════════════════════════════════════
# TestRenderStateGetToolAdapter — 工具输出适配器获取
# ═══════════════════════════════════════════════════════════

class TestRenderStateGetToolAdapter:
    """_RenderState.get_tool_adapter() 惰性创建测试。

    覆盖：
      - 首次调用创建 OutputAdapter
      - 重复调用返回同一实例
    """

    def test_get_tool_adapter_creates_on_first_call(self):
        """首次调用创建 OutputAdapter 并缓存。"""
        rs = _make_render_state()
        # get_tool_adapter 内部使用 rich.console.Console + OutputAdapter，
        # 验证返回非 None 且类型正确即可
        adapter = rs.get_tool_adapter()
        from src.api.renderer.output import OutputAdapter
        assert isinstance(adapter, OutputAdapter)
        assert rs._tool_adapter is adapter

    def test_get_tool_adapter_returns_same_instance(self):
        """重复调用返回同一实例（不重复创建）。"""
        rs = _make_render_state()
        first = rs.get_tool_adapter()
        second = rs.get_tool_adapter()
        assert first is second

    def test_get_tool_adapter_memoized(self):
        """_tool_adapter 非 None 时直接返回缓存值。"""
        rs = _make_render_state()
        mock_adapter = MagicMock()
        rs._tool_adapter = mock_adapter
        result = rs.get_tool_adapter()
        assert result is mock_adapter
