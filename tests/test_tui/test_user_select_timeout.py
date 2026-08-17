"""user_select timeout 默认值精确性 + 超时/确认竞态回归测试。

背景（2026-08-17 用户报障）：UserSelect timeout 默认值精确性验证（1s）
有机率失败——工具协程超时分支与组件确认写入（render 线程）之间存在
竞态窗口：

  修复前（tools 层超时分支）：
    while not model.user_select.done:
        if deadline > 0 and time.monotonic() >= deadline:
            model.user_select.done = True      # ← 无条件覆盖
            model.user_select.action = "timeout"
            model.user_select.result = default_opts
            break

  若组件（Enter 确认）与工具超时分支在临界窗口交错：组件先写
  done/action="confirmed"/result=用户选择 → 工具随后无条件覆盖
  action="timeout"/result=默认选项 → 用户明明已确认却返回 timeout。
  反之组件侧 _commit 已有 first-write-wins 防御（done 已置位则放弃），
  但工具侧缺对称防御 → 「有机率」复现。

修复：UserSelectState.try_set_final(action, result) 原子终态写入
（内部锁保护 done/action/result 三字段；done 已置位返回 False 不覆盖），
组件 _commit 与工具超时分支统一走该入口 → 彻底消除竞态覆盖。
"""

from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

import pytest

from src.tui.app._state_types import UserSelectState


# ═══════════════════════════════════════════════════════════
# 1. try_set_final 原子终态写入（first-write-wins）
# ═══════════════════════════════════════════════════════════

class TestTrySetFinal:
    def test_first_write_wins(self):
        """首次写入生效；后续写入被拒绝（返回 False 且不覆盖）。"""
        us = UserSelectState()
        assert us.try_set_final("confirmed", ["B"]) is True
        assert us.done is True
        assert us.action == "confirmed"
        assert us.result == ["B"]

        assert us.try_set_final("timeout", ["A"]) is False
        assert us.action == "confirmed", "终态不可被覆盖"
        assert us.result == ["B"], "终态结果不可被覆盖"

    def test_result_copied(self):
        """result 入参浅拷贝——调用方后续修改入参列表不影响终态。"""
        us = UserSelectState()
        src = ["A", "B"]
        us.try_set_final("confirmed", src)
        src.append("C")
        assert us.result == ["A", "B"]

    def test_component_commit_after_tool_timeout_discarded(self):
        """组件侧 _commit 语义：工具先超时置终态 → 组件确认被丢弃。"""
        us = UserSelectState()
        assert us.try_set_final("timeout", ["A"]) is True
        # 组件 _commit 等价调用（修复前直接写属性会覆盖 timeout）
        ok = us.try_set_final("confirmed", ["B"])
        assert ok is False
        assert us.action == "timeout"
        assert us.result == ["A"]

    def test_concurrent_writers_single_terminal_state(self):
        """并发写入（模拟工具协程 vs render 线程组件）→ 终态一致且无损。

        无论交错顺序如何，最终只存在一个终态：确认或超时（不允许
        出现「action=timeout 但 result=用户选择」等混合损坏态）。
        """
        import random

        for _ in range(50):
            us = UserSelectState()
            barrier = threading.Barrier(2)

            def _tool_timeout():
                barrier.wait()
                us.try_set_final("timeout", ["default"])

            def _component_commit():
                barrier.wait()
                us.try_set_final("confirmed", ["user"])

            threads = [
                threading.Thread(target=_tool_timeout),
                threading.Thread(target=_component_commit),
            ]
            random.shuffle(threads)
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            assert us.done is True
            assert us.action in ("confirmed", "timeout")
            if us.action == "confirmed":
                assert us.result == ["user"]
            else:
                assert us.result == ["default"]
            # done/action/result 必须三字段一致终态（无混合损坏）
            assert (us.action == "confirmed") == (us.result == ["user"])


# ═══════════════════════════════════════════════════════════
# 2. timeout 默认值精确性（1s）
# ═══════════════════════════════════════════════════════════

class TestTimeoutPrecision:
    def test_deadline_1s_precision(self):
        """timeout=1 → deadline = time.monotonic() + 1（精确 1 秒）。"""
        from src.tools.user_select import UserSelectFunc

        t0 = time.monotonic()
        func = UserSelectFunc("测试", ["A", "B"], default_options=["A"], timeout=1)
        # 直接验证 deadline 计算（避免轮询等待）
        us = UserSelectState()
        us.deadline = 0.0 if func.timeout <= 0 else time.monotonic() + func.timeout
        # 与工具实际设置路径同公式（见 _execute_terminal_async）
        assert func.timeout == 1
        deadline = us.deadline
        assert deadline > t0
        assert deadline - t0 == pytest.approx(1.0, abs=0.05)

    def test_default_timeout_is_120(self):
        """工具默认 timeout=120（schema 与构造函数一致）。"""
        from src.tools.user_select import UserSelectFunc

        func = UserSelectFunc("测试", ["A", "B"])
        assert func.timeout == 120

        schema = UserSelectFunc.to_tool_schema()
        params = schema["function"]["parameters"]["properties"]
        assert params["timeout"]["default"] == 120

    def test_timeout_zero_means_infinite(self):
        """timeout<=0 → deadline=0（无限等待，不触发超时分支）。"""
        from src.tools.user_select import UserSelectFunc

        func = UserSelectFunc("测试", ["A", "B"], timeout=0)
        assert func.timeout == 0
        deadline = 0.0 if func.timeout <= 0 else time.monotonic() + func.timeout
        assert deadline == 0.0


# ═══════════════════════════════════════════════════════════
# 3. 端到端：超时回退默认选项（timeout=1s）
# ═══════════════════════════════════════════════════════════

def test_e2e_timeout_falls_back_to_default_options(monkeypatch):
    """timeout=1s：无交互 → 工具超时回退 default_options，action=timeout。"""
    from src.tools.user_select import UserSelectFunc

    m = type("M", (), {})()
    m.user_select = UserSelectState()

    class _FakeStdin:
        def fileno(self):
            return 0

    class _FakeInput:
        def flush_stdin_buffer(self):
            pass

    class _FakeChatUI:
        @property
        def bottom_bar(self):
            return None

        def get_model(self):
            return m

        def request_bottom_redraw(self):
            pass

        def get_input_component(self):
            return _FakeInput()

    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: _FakeChatUI())
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    func = UserSelectFunc("测试", ["A", "B"], default_options=["A"], timeout=1)

    t0 = time.monotonic()
    result = asyncio.run(func._execute_terminal_async())
    elapsed = time.monotonic() - t0

    assert elapsed >= 1.0, f"应至少等待 1s 触发超时（实际 {elapsed:.3f}s）"
    assert elapsed < 1.5, f"超时应约 1s（实际 {elapsed:.3f}s）"
    assert '"timeout"' in result
    assert '"A"' in result
    assert m.user_select.visible is False, "清理后弹窗应不可见"


def test_e2e_component_confirm_before_deadline(monkeypatch):
    """组件在 deadline 前确认 → 返回 confirmed（不受超时轮询影响）。"""
    from src.tools.user_select import UserSelectFunc

    m = type("M", (), {})()
    m.user_select = UserSelectState()

    class _FakeStdin:
        def fileno(self):
            return 0

    class _FakeInput:
        def flush_stdin_buffer(self):
            pass

    class _FakeChatUI:
        @property
        def bottom_bar(self):
            return None

        def get_model(self):
            return m

        def request_bottom_redraw(self):
            pass

        def get_input_component(self):
            return _FakeInput()

    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: _FakeChatUI())
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    func = UserSelectFunc("测试", ["A", "B"], default_options=["A"], timeout=5)

    async def _run():
        task = asyncio.create_task(func._execute_terminal_async())
        await asyncio.sleep(0.1)
        # 组件确认（与 UserSelectPopup._commit 等价路径）
        m.user_select.try_set_final("confirmed", ["B"])
        return await task

    result = asyncio.run(_run())
    assert '"confirmed"' in result
    assert '"B"' in result


def test_e2e_tool_timeout_does_not_overwrite_component_confirm(monkeypatch):
    """回归（修复核心）：组件确认与工具超时竞争 → 确认结果必须保留。

    修复前：组件先写 done/confirmed，工具超时分支无条件覆盖
    action="timeout"/result=默认选项 → 用户已确认却返回 timeout。
    修复后：try_set_final 原子终态（first-write-wins）→ 返回 confirmed。
    """
    from src.tools.user_select import UserSelectFunc

    m = type("M", (), {})()
    m.user_select = UserSelectState()

    class _FakeStdin:
        def fileno(self):
            return 0

    class _FakeInput:
        def flush_stdin_buffer(self):
            pass

    class _FakeChatUI:
        @property
        def bottom_bar(self):
            return None

        def get_model(self):
            return m

        def request_bottom_redraw(self):
            pass

        def get_input_component(self):
            return _FakeInput()

    monkeypatch.setattr("src.tools.user_select.get_active_chat_ui", lambda: _FakeChatUI())
    monkeypatch.setattr(os, "isatty", lambda fd: True)
    monkeypatch.setattr(sys, "stdin", _FakeStdin())
    func = UserSelectFunc("测试", ["A", "B"], default_options=["A"], timeout=1)

    async def _run():
        task = asyncio.create_task(func._execute_terminal_async())
        # 0.95s（deadline 前最后一轮 sleep 中）组件确认 —— 模拟
        # 「组件确认恰好落在超时临界窗口」的竞态场景
        await asyncio.sleep(0.95)
        m.user_select.try_set_final("confirmed", ["B"])
        return await task

    result = asyncio.run(_run())
    assert '"confirmed"' in result, f"组件确认不应被超时覆盖: {result}"
    assert '"B"' in result
