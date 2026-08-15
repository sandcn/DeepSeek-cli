"""InkSession 空闲 10Hz 渲染测试（2026-08-16 需求变更）。

修复背景：渲染循环原先在「空闲（无命令/无动画/无脏标记）」时跳过渲染
（``_should_render`` 的 ``if not self._dirty: return False`` 短路），保持
CPU ~0——但**没有流式输出（等待用户输入/空闲）期间 TUI 不再刷新**。

需求：没有流式输出的时间 TUI 也要每秒 10Hz 刷新。修复：移除空闲跳过短路，
``_should_render`` 在 ``render_interval`` 到期时无论脏/动画/命令与否都渲染，
空闲时渲染循环也以 10Hz 持续推进（组件树缓存下无变化帧 diff 零输出）。

本测试锁定：
  1. 空闲（无脏/无 force/无动画）且 interval 已到期 → 渲染；
  2. 空闲且 interval 未到期 → 仍受节流（不忙等）；
  3. 渲染循环空闲时持续调用 ``_render_frame``（端到端 10Hz）。
"""

from __future__ import annotations

import io
import time

from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession


def _make_session() -> tuple[InkSession, AppModel]:
    """构造空闲 InkSession（AppModel 默认空闲状态）。"""
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    model = AppModel()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=io.StringIO(),
    )
    session.set_line_tracker(None)
    return session, model


def _assert_idle(session) -> None:
    """断言 session 处于空闲状态（无动画需求，_needs_animation 返回 False）。"""
    assert session._dirty is False
    assert not session._bottom_redraw_requested.is_set()
    assert session._needs_animation() is False


# ── _should_render 空闲语义 ─────────────────────────────

def test_should_render_idle_renders_when_interval_elapsed():
    """空闲（无脏/无 force/无动画）且 interval 已到期 → 渲染（10Hz 持续推进）。"""
    session, _ = _make_session()
    _assert_idle(session)
    session._last_bottom_redraw = time.monotonic() - 1.0  # interval 已满

    assert session._should_render(changed=False) is True


def test_should_render_idle_respects_interval():
    """空闲且 interval 未到期 → 仍受节流（不忙等，保留 10Hz 批处理节奏）。"""
    session, _ = _make_session()
    _assert_idle(session)
    session._last_bottom_redraw = time.monotonic()  # 刚渲染过

    assert session._should_render(changed=False) is False


def test_should_render_idle_clears_dirty():
    """空闲渲染后清空 _dirty（保持 dirty 语义：渲染完成标记）。"""
    session, _ = _make_session()
    session._last_bottom_redraw = time.monotonic() - 1.0
    session._dirty = True

    assert session._should_render(changed=False) is True
    assert session._dirty is False


# ── 渲染循环空闲 10Hz（端到端） ─────────────────────────

def test_idle_render_loop_keeps_rendering():
    """渲染循环空闲时持续调用 _render_frame（约 10Hz，不因空闲跳过而停摆）。"""
    session, _ = _make_session()
    renders = []
    orig = session._render_frame

    def spy():
        renders.append(time.monotonic())
        return orig()

    session._render_frame = spy
    session.start()
    time.sleep(0.2)  # 渲染线程稳定 + 首帧渲染
    try:
        baseline = len(renders)
        # 空闲（无命令 push）：0.35s 内应产生约 3 次渲染（10Hz）
        time.sleep(0.35)
        count = len(renders) - baseline
        assert count >= 2, (
            f"空闲时渲染循环应持续 10Hz 渲染（0.35s 至少 2 次），实际 {count} 次"
        )
    finally:
        session.stop()
