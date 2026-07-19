"""测试 _RenderState 线程安全 — 锁保护 + close_all 幂等性。"""

from __future__ import annotations

import threading
import time

from src.tui.state.render_state import _RenderState, _ReasoningState


class TestRenderStateThreadSafety:
    """_RenderState 线程安全测试。"""

    def test_close_all_idempotent(self):
        """close_all() 幂等：多次调用不抛出异常。"""
        rs = _RenderState()
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
        rs = _RenderState()
        rs.close_reasoning()
        rs.close_content()
        # close_all 应正常工作
        rs.close_all()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_reasoning_idempotent(self):
        """close_reasoning() 幂等。"""
        rs = _RenderState()
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED

    def test_close_content_idempotent(self):
        """close_content() 幂等。"""
        rs = _RenderState()
        rs.close_content()
        assert rs.content is None
        rs.close_content()
        assert rs.content is None

    def test_reopen_reasoning_after_close(self):
        """close_reasoning → reopen_reasoning → 状态回到 INACTIVE。"""
        rs = _RenderState()
        rs.close_reasoning()
        assert rs.reasoning_state == _ReasoningState.CLOSED
        rs.reopen_reasoning()
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_reopen_reasoning_noop_when_not_closed(self):
        """reopen_reasoning() 在非 CLOSED 状态是空操作。"""
        rs = _RenderState()
        assert rs.reasoning_state == _ReasoningState.INACTIVE
        rs.reopen_reasoning()
        # 状态应保持 INACTIVE（reopen 只在 CLOSED→INACTIVE 时生效）
        assert rs.reasoning_state == _ReasoningState.INACTIVE

    def test_concurrent_close_reasoning(self):
        """两个线程并发调用 close_reasoning()，无异常。"""
        rs = _RenderState()
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
        rs = _RenderState()
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
        rs = _RenderState()
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
        rs = _RenderState()
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
        rs = _RenderState()
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
        rs = _RenderState()
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

    def test_concurrent_close_reasoning_and_close_all(self):
        """一个线程 close_reasoning，另一个 close_all，无异常。

        验证 close_all 的内联实现不会导致锁内调用锁的死锁。
        """
        rs = _RenderState()
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
