"""测试 IRenderState Protocol + 渲染器工厂函数。

覆盖：
  1. IRenderState Protocol 结构兼容性（isinstance + 运行时检查）
  2. _create_reasoning_renderer / _create_content_renderer 默认参数正确性
"""

from __future__ import annotations

from typing import get_type_hints

from src.tui.state.render_state import (
    _RenderState,
    _ReasoningState,
    IRenderState,
)


class TestIRenderStateProtocol:
    """IRenderState Protocol 兼容性测试。"""

    def test_render_state_satisfies_protocol(self):
        """_RenderState 实例满足 IRenderState Protocol（isinstance 检查）。"""
        rs = _RenderState()
        assert isinstance(rs, IRenderState), (
            "_RenderState 应满足 IRenderState Protocol"
        )

    def test_protocol_has_all_public_methods(self):
        """IRenderState Protocol 声明了全部公共接口方法。"""
        protocol_methods = {
            name for name in dir(IRenderState)
            if not name.startswith('_') or name in ('__call__',)
        }
        # Protocol 的运行时属性包括 __init_subclass__ 等 dunder，
        # 因此以公开方法名（reasoning_state / set_output_adapter / ...）为准
        expected = {
            'reasoning_state',
            'set_output_adapter',
            'get_reasoning',
            'get_content',
            'close_reasoning',
            'reopen_reasoning',
            'close_content',
            'close_all',
        }
        # Protocol 使用 ... (Ellipsis) 作为方法体，运行时以描述器形式存在
        for name in expected:
            assert hasattr(IRenderState, name), (
                f"IRenderState 缺少方法: {name}"
            )

    def test_protocol_method_signatures(self):
        """IRenderState 的方法签名与 _RenderState 对应方法兼容。"""
        # get_reasoning() -> IncrementalRenderer | None
        # get_content() -> IncrementalRenderer
        # set_output_adapter(adapter) -> None
        # close_reasoning() -> None
        # reopen_reasoning() -> None
        # close_content() -> None
        # close_all() -> None
        for method_name in ('get_reasoning', 'get_content', 'close_reasoning',
                            'reopen_reasoning', 'close_content', 'close_all',
                            'set_output_adapter'):
            assert hasattr(_RenderState, method_name), (
                f"_RenderState 缺少 {method_name}（Protocol 要求）"
            )

    def test_reasoning_state_property(self):
        """reasoning_state 作为只读属性可从 IRenderState 获取。"""
        rs = _RenderState()
        # 验证 reasoning_state 属性存在且返回正确类型
        assert rs.reasoning_state is _ReasoningState.INACTIVE

    def test_protocol_instance_methods_work(self):
        """通过 IRenderState 接口调用的方法行为正确。"""
        rs: IRenderState = _RenderState()  # 类型兼容赋值
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        # 关闭推理
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        # 重新打开
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_protocol_get_content_when_not_injected(self):
        """get_content() 在未预注入渲染器时返回 None（非崩溃）。"""
        rs: IRenderState = _RenderState()
        result = rs.get_content()
        assert result is None, (
            "未预注入时 get_content 应返回 None"
        )

    def test_protocol_get_reasoning_when_not_injected(self):
        """get_reasoning() 在未预注入且 INACTIVE 状态时返回 None（非崩溃）。"""
        rs: IRenderState = _RenderState()
        result = rs.get_reasoning()
        assert result is None, (
            "未预注入时 get_reasoning 应返回 None"
        )

    def test_protocol_close_all_twice(self):
        """close_all() 通过 Protocol 接口调用两次，幂等。"""
        rs: IRenderState = _RenderState()
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        # 第二次调用
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED


class TestReasoningRendererFactory:
    """_create_reasoning_renderer / _create_content_renderer 工厂函数测试。"""

    def test_create_reasoning_renderer_returns_renderer(self):
        """_create_reasoning_renderer 返回 IncrementalRenderer 实例。"""
        from src.tui.engine.renderer import _create_reasoning_renderer
        from src.renderer import IncrementalRenderer
        renderer = _create_reasoning_renderer()
        assert isinstance(renderer, IncrementalRenderer), (
            "工厂函数应返回 IncrementalRenderer 实例"
        )
        # 验证默认参数：show_indicator 为 False
        assert renderer._show_indicator is False
        renderer.close()

    def test_create_content_renderer_returns_renderer(self):
        """_create_content_renderer 返回 IncrementalRenderer 实例。"""
        from src.tui.engine.renderer import _create_content_renderer
        from src.renderer import IncrementalRenderer
        renderer = _create_content_renderer(adapter=None)
        assert isinstance(renderer, IncrementalRenderer), (
            "工厂函数应返回 IncrementalRenderer 实例"
        )
        assert renderer._show_indicator is False
        renderer.close()
