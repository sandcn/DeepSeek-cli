"""SIGWINCH 回调注册表测试 — 架构改进方向 C（2026-08-16）。

覆盖：
  1. ``register_sigwinch_callback(cb, token=...)`` 按 token 去重（同 token
     重复注册不累积，替换为最新回调）；
  2. 多 token 并存（多 TUI 实例各持自身回调，互不干扰）；
  3. ``unregister_sigwinch_callback(token)`` 注销后回调不再触发（幂等）；
  4. ``InkSession._on_sigwinch`` 实例方法行为（force_refresh + 重绘请求）；
  5. ``InkSession.stop()`` 自动注销本会话回调（释放全局引用）。
"""

from __future__ import annotations

from unittest.mock import Mock

import pytest

import src.tui._screen as screen_mod
from src.tui._screen import (
    process_sigwinch,
    register_sigwinch_callback,
    unregister_sigwinch_callback,
)


@pytest.fixture(autouse=True)
def _clean_sigwinch_registry():
    """每个测试清理全局回调注册表（防跨测试污染）。"""
    screen_mod._sigwinch_callbacks.clear()
    yield
    screen_mod._sigwinch_callbacks.clear()


def _trigger_sigwinch(width: int = 100, height: int = 30) -> None:
    """模拟一次 SIGWINCH：置 pending 并消费。"""
    screen_mod._sigwinch_pending = True
    screen_mod._get_terminal_size = lambda: (width, height)
    assert process_sigwinch() is True


# ═══════════════════════════════════════════════════════════
# 1. token 去重
# ═══════════════════════════════════════════════════════════

def test_register_dedup_by_token():
    """同 token 重复注册：回调列表不累积（替换为最新回调）。"""
    calls: list = []
    cb1 = lambda w, h: calls.append(("cb1", w, h))
    cb2 = lambda w, h: calls.append(("cb2", w, h))
    token = object()
    register_sigwinch_callback(cb1, token=token)
    register_sigwinch_callback(cb2, token=token)
    assert len(screen_mod._sigwinch_callbacks) == 1
    _trigger_sigwinch(120, 40)
    # 只有最新回调被触发
    assert calls == [("cb2", 120, 40)]


def test_register_without_token_dedup_by_identity():
    """无 token：按 cb 身份去重（旧行为兼容）。"""
    calls: list = []
    cb = lambda w, h: calls.append((w, h))
    register_sigwinch_callback(cb)
    register_sigwinch_callback(cb)
    assert len(screen_mod._sigwinch_callbacks) == 1
    _trigger_sigwinch(80, 24)
    assert calls == [(80, 24)]


# ═══════════════════════════════════════════════════════════
# 2. 多实例独立回调
# ═══════════════════════════════════════════════════════════

def test_multi_session_independent_callbacks():
    """多 token（多 TUI 实例）回调并存，各自触发互不干扰。"""
    s1_calls: list = []
    s2_calls: list = []
    t1, t2 = object(), object()
    register_sigwinch_callback(lambda w, h: s1_calls.append((w, h)), token=t1)
    register_sigwinch_callback(lambda w, h: s2_calls.append((w, h)), token=t2)
    _trigger_sigwinch(90, 25)
    assert s1_calls == [(90, 25)]
    assert s2_calls == [(90, 25)]
    # 注销 t1 后仅 t2 触发
    unregister_sigwinch_callback(t1)
    _trigger_sigwinch(91, 26)
    assert s1_calls == [(90, 25)]  # t1 不再触发
    assert s2_calls == [(90, 25), (91, 26)]


# ═══════════════════════════════════════════════════════════
# 3. 注销幂等
# ═══════════════════════════════════════════════════════════

def test_unregister_idempotent():
    """注销不存在的 token 无副作用（幂等）。"""
    unregister_sigwinch_callback(object())  # 不抛异常
    unregister_sigwinch_callback(object())
    assert screen_mod._sigwinch_callbacks == []


def test_unregister_removes_callback():
    """注销后回调不再被触发。"""
    calls: list = []
    token = object()
    register_sigwinch_callback(lambda w, h: calls.append((w, h)), token=token)
    _trigger_sigwinch(100, 30)
    assert len(calls) == 1
    unregister_sigwinch_callback(token)
    _trigger_sigwinch(101, 31)
    assert len(calls) == 1


# ═══════════════════════════════════════════════════════════
# 4. InkSession._on_sigwinch 实例方法
# ═══════════════════════════════════════════════════════════

def test_ink_session_on_sigwinch():
    """_on_sigwinch 刷新宽度缓存 + 请求底部重绘。"""
    from src.tui.ink.session import InkSession

    session = InkSession(model=Mock())
    session._width_cache.force_refresh = Mock()
    session.request_bottom_redraw = Mock()
    session._on_sigwinch(100, 30)
    session._width_cache.force_refresh.assert_called_once()
    session.request_bottom_redraw.assert_called_once()


def test_ink_session_on_sigwinch_swallows_refresh_error():
    """_on_sigwinch 内部异常不抛出（信号上下文安全降级）。"""
    from src.tui.ink.session import InkSession

    session = InkSession(model=Mock())
    session._width_cache.force_refresh = Mock(side_effect=RuntimeError("boom"))
    # 不抛异常（内部 try/except 隔离）
    session._on_sigwinch(100, 30)


# ═══════════════════════════════════════════════════════════
# 5. InkSession.stop() 注销回调
# ═══════════════════════════════════════════════════════════

def test_ink_session_stop_unregisters_sigwinch():
    """stop() 注销本会话的 SIGWINCH 回调（释放全局引用）。"""
    from src.tui.ink.session import InkSession

    session = InkSession(model=Mock())
    # 模拟装配注册
    register_sigwinch_callback(session._on_sigwinch, token=session)
    assert len(screen_mod._sigwinch_callbacks) == 1
    session.stop()  # 内部应注销
    assert screen_mod._sigwinch_callbacks == []
