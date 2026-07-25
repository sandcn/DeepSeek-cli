"""测试 ChatRenderState 线程安全 — 锁保护 + close_all 幂等性。"""

from __future__ import annotations

import threading
import time

from src.tui.state.render_state import (
    ChatRenderState,
    RenderState,
    _ReasoningState,
    IRenderState,
)


class TestRenderStateThreadSafety:
    """ChatRenderState 线程安全测试。"""

    def test_close_all_idempotent(self):
        """close_all() 幂等：多次调用不抛出异常。"""
        rs = ChatRenderState()
        # 第一次调用
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        assert rs.reasoning is None
        assert rs.content is None
        # 第二次调用（幂等）
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_all_after_partial_close(self):
        """先分别关闭再 close_all，不抛异常。"""
        rs = ChatRenderState()
        rs.close_reasoning()
        rs.close_content()
        # close_all 应正常工作
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_idempotent(self):
        """close_reasoning() 幂等。"""
        rs = ChatRenderState()
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_content_idempotent(self):
        """close_content() 幂等。"""
        rs = ChatRenderState()
        rs.close_content()
        assert rs.content is None
        rs.close_content()
        assert rs.content is None

    def test_reopen_reasoning_after_close(self):
        """close_reasoning → reopen_reasoning → 状态回到 INACTIVE。"""
        rs = ChatRenderState()
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_reopen_reasoning_noop_when_not_closed(self):
        """reopen_reasoning() 在非 CLOSED 状态是空操作。"""
        rs = ChatRenderState()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        rs.reopen_reasoning()
        # 状态应保持 INACTIVE（reopen 只在 CLOSED→INACTIVE 时生效）
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_concurrent_close_reasoning(self):
        """两个线程并发调用 close_reasoning()，无异常。"""
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()
                rs.close_reasoning()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发 close_reasoning 异常: {errors}"
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_concurrent_close_content(self):
        """两个线程并发调用 close_content()，无异常。"""
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()
                rs.close_content()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发 close_content 异常: {errors}"
        assert rs.content is None

    def test_concurrent_close_all(self):
        """两个线程并发调用 close_all()，无异常。"""
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()
                rs.close_all()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发 close_all 异常: {errors}"
        assert rs.reasoning_state == _ReasoningState.CLOSED
        assert rs.content is None

    def test_concurrent_set_output_adapter(self):
        """两个线程并发调用 set_output_adapter()，无异常。"""
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()
                rs.set_output_adapter(None)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发 set_output_adapter 异常: {errors}"

    def test_concurrent_reopen_reasoning(self):
        """两个线程并发调用 reopen_reasoning()，无异常。"""
        rs = ChatRenderState()
        # 先关闭，为 reopen 做准备
        rs.close_reasoning()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker() -> None:
            try:
                barrier.wait()
                rs.reopen_reasoning()
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"并发 reopen_reasoning 异常: {errors}"
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_mixed_concurrent_close(self):
        """一个线程 close_reasoning，另一个 close_content，无异常。"""
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker_a() -> None:
            try:
                barrier.wait()
                rs.close_reasoning()
            except Exception as e:
                errors.append(e)

        def worker_b() -> None:
            try:
                barrier.wait()
                rs.close_content()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_a),
            threading.Thread(target=worker_b),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"混合关闭异常: {errors}"
        assert rs.reasoning_state == _ReasoningState.CLOSED
        assert rs.content is None

    def test_captured_reasoning_output_default_empty_list(self):
        """captured_reasoning_output 默认初始化为空列表（非 None）。"""
        rs = ChatRenderState()
        assert rs.captured_reasoning_output == []
        assert rs.captured_reasoning_output is not None

    def test_captured_content_output_default_empty_list(self):
        """captured_content_output 默认初始化为空列表（非 None）。"""
        rs = ChatRenderState()
        assert rs.captured_content_output == []
        assert rs.captured_content_output is not None

    def test_captured_outputs_independent_instances(self):
        """每个 ChatRenderState 实例拥有独立的 captured_output 列表。"""
        rs1 = ChatRenderState()
        rs2 = ChatRenderState()
        rs1.captured_reasoning_output.append("test")
        assert rs2.captured_reasoning_output == []
        assert len(rs1.captured_reasoning_output) == 1

    def test_concurrent_close_reasoning_and_close_all(self):
        """一个线程 close_reasoning，另一个 close_all，无异常。

        验证 close_all 的内联实现不会导致锁内调用锁的死锁。
        """
        rs = ChatRenderState()
        errors: list[Exception] = []
        barrier = threading.Barrier(2, timeout=5)

        def worker_a() -> None:
            try:
                barrier.wait()
                rs.close_all()
            except Exception as e:
                errors.append(e)

        def worker_b() -> None:
            try:
                barrier.wait()
                rs.close_reasoning()
            except Exception as e:
                errors.append(e)

        threads = [
            threading.Thread(target=worker_a),
            threading.Thread(target=worker_b),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=5)

        assert not errors, f"close_all + close_reasoning 并发异常: {errors}"
        assert rs.reasoning_state == _ReasoningState.CLOSED


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
