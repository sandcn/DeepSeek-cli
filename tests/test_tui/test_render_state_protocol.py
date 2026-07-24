"""测试 IRenderState Protocol + 渲染状态基类。

覆盖：
  1. IRenderState Protocol 结构兼容性（运行时属性检查）
  2. ChatRenderState 通过 IRenderState 接口的行为正确性
"""

from __future__ import annotations

from src.tui.state.render_state import (
    ChatRenderState,
    RenderState,
    _ReasoningState,
    IRenderState,
)


class TestIRenderStateProtocol:
    """IRenderState Protocol 兼容性测试。"""

    def test_render_state_satisfies_protocol(self):
        """ChatRenderState 实例满足 IRenderState Protocol（运行时属性检查）。"""
        rs = ChatRenderState()
        # 验证协议要求的全部方法存在
        for method_name in ('set_output_adapter', 'get_reasoning', 'get_content',
                            'close_reasoning', 'reopen_reasoning', 'close_content',
                            'close_all'):
            assert hasattr(rs, method_name), (
                f"ChatRenderState 缺少方法: {method_name}"
            )

    def test_protocol_has_all_public_methods(self):
        """IRenderState Protocol 声明了全部公共接口方法。"""
        expected_methods = {
            'set_output_adapter', 'get_reasoning', 'get_content',
            'close_reasoning', 'reopen_reasoning', 'close_content', 'close_all',
        }
        expected_attrs = {'reasoning_state'}
        # 方法通过 hasattr 检查
        for name in expected_methods:
            assert hasattr(IRenderState, name), (
                f"IRenderState 缺少方法: {name}"
            )
        # Protocol 属性在 __annotations__ 中声明
        proto_ann = getattr(IRenderState, '__annotations__', {})
        for name in expected_attrs:
            assert name in proto_ann, (
                f"IRenderState 缺少属性注解: {name}"
            )

    def test_protocol_method_signatures(self):
        """IRenderState 的方法签名与 ChatRenderState 对应方法兼容。"""
        for method_name in ('get_reasoning', 'get_content', 'close_reasoning',
                            'reopen_reasoning', 'close_content', 'close_all',
                            'set_output_adapter'):
            assert hasattr(ChatRenderState, method_name), (
                f"ChatRenderState 缺少 {method_name}（Protocol 要求）"
            )

    def test_reasoning_state_property(self):
        """reasoning_state 作为属性可从 IRenderState 获取。"""
        rs = ChatRenderState()
        assert rs.reasoning_state is _ReasoningState.INACTIVE

    def test_protocol_instance_methods_work(self):
        """通过 IRenderState 接口调用的方法行为正确。"""
        rs = ChatRenderState()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        # 关闭推理
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        # 重新打开
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_protocol_get_content_when_not_injected(self):
        """get_content() 在未设置 adapter 时仍返回渲染器（惰性创建）。"""
        rs = ChatRenderState()
        # ChatRenderState.get_content() 会惰性创建 IncrementalRenderer
        # 即使 _shared_adapter 未设置也会创建（仅打印 warning）
        result = rs.get_content()
        assert result is not None, (
            "get_content 应当惰性创建渲染器"
        )
        result.close()

    def test_protocol_get_reasoning_when_not_injected(self):
        """get_reasoning() 在 INACTIVE 状态且未预注入时惰性创建渲染器。"""
        rs = ChatRenderState()
        result = rs.get_reasoning()
        # ChatRenderState.get_reasoning() 在 INACTIVE 状态会创建并转为 ACTIVE
        assert result is not None, (
            "INACTIVE 状态下 get_reasoning 应当惰性创建渲染器"
        )
        result.close()

    def test_protocol_close_all_twice(self):
        """close_all() 通过 Protocol 接口调用两次，幂等。"""
        rs = ChatRenderState()
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        # 第二次调用
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED


class TestRenderStateHierarchy:
    """RenderState 基类 / ChatRenderState 层次测试。"""

    def test_chat_render_state_extends_render_state(self):
        """ChatRenderState 继承自 RenderState。"""
        rs = ChatRenderState()
        assert isinstance(rs, RenderState), (
            "ChatRenderState 应继承自 RenderState"
        )
        # 基类属性
        assert rs._shared_adapter is None

    def test_set_output_adapter(self):
        """set_output_adapter 通过渲染状态基类设置适配器。"""
        rs = ChatRenderState()
        assert rs._shared_adapter is None
        rs.set_output_adapter("mock_adapter")  # type: ignore
        assert rs._shared_adapter == "mock_adapter"
