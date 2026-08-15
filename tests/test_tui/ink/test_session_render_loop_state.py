"""渲染循环显式状态机测试 — 架构改进方向 E（2026-08-16）。

``_drain_queue`` 从隐式顺序改写为 ``RenderLoopPhase`` 六阶段显式迁移：

  SIGWINCH → INPUT → PANELS → SYSTEM_STATS → DRAIN_COMMANDS
  → APPLY → RENDER

本测试锁定：
  1. ``RenderLoopPhase`` 枚举迁移链（顺序即阶段流）；
  2. ``_drain_queue`` 按阶段顺序驱动各 ``_phase_*`` 方法（调用序断言）；
  3. DRAIN_COMMANDS 锁不可用 → 跳过本帧（返回 False，不渲染）；
  4. ``_drain_commands_locked`` 排空语义（命令收集/批上限/锁外返回）；
  5. ``_drain_queue`` 返回本帧是否有命令变更（changed 语义保持）。
"""

from __future__ import annotations

import io
from unittest.mock import Mock, patch

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession, RenderLoopPhase
from src.tui._const import WriteLineCmd


def _make_session():
    """构造最小 InkSession（AppModel 真实组件，锁/渲染 mock 由用例注入）。"""
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    session = InkSession(
        model=Mock(),
        apply_cmd=lambda m, c: None,
        build_tree=lambda m, w: None,
        config=TuiConfig.defaults(),
        stream=io.StringIO(),
    )
    session.set_line_tracker(None)
    return session


# ═══════════════════════════════════════════════════════════
# 1. RenderLoopPhase 枚举迁移链
# ═══════════════════════════════════════════════════════════

def test_render_loop_phase_enum_sequence():
    """RenderLoopPhase 六阶段迁移链：顺序即阶段流（SIGWINCH → … → RENDER）。"""
    assert list(RenderLoopPhase) == [
        RenderLoopPhase.SIGWINCH,
        RenderLoopPhase.INPUT,
        RenderLoopPhase.PANELS,
        RenderLoopPhase.SYSTEM_STATS,
        RenderLoopPhase.DRAIN_COMMANDS,
        RenderLoopPhase.APPLY,
        RenderLoopPhase.RENDER,
    ]
    # RENDER 为终态（迁移链中无后继）
    assert RenderLoopPhase.RENDER.value == "render"


# ═══════════════════════════════════════════════════════════
# 2. _drain_queue 按阶段顺序驱动
# ═══════════════════════════════════════════════════════════

def test_drain_queue_drives_phases_in_order():
    """_drain_queue 按 SIGWINCH→INPUT→PANELS→SYSTEM_STATS→DRAIN→APPLY→RENDER 顺序执行。"""
    session = _make_session()
    order: list[str] = []

    session._phase_process_sigwinch = Mock(side_effect=lambda: order.append("SIGWINCH"))
    session._phase_process_input = Mock(side_effect=lambda: order.append("INPUT"))
    session._phase_pre_update_panels = Mock(side_effect=lambda: order.append("PANELS"))
    session._update_system_stats = Mock(side_effect=lambda: order.append("SYSTEM_STATS"))
    session._apply_commands = Mock(side_effect=lambda cmds: order.append("APPLY"))
    session._should_render = Mock(side_effect=lambda changed: True)
    session._render_frame = Mock(side_effect=lambda: order.append("RENDER"))
    # DRAIN_COMMANDS：无命令 → 仍进入 APPLY（changed=False 时无 apply 调用）
    session._drain_commands_locked = Mock(return_value=([], False, True))

    result = session._drain_queue()

    # 阶段调用顺序
    assert order[:4] == ["SIGWINCH", "INPUT", "PANELS", "SYSTEM_STATS"]
    assert order[-1] == "RENDER"
    # 各阶段恰好调用一次
    session._phase_process_sigwinch.assert_called_once()
    session._phase_process_input.assert_called_once()
    session._phase_pre_update_panels.assert_called_once()
    session._update_system_stats.assert_called_once()
    session._render_frame.assert_called_once()
    # 无命令 → APPLY 阶段不应用命令
    session._apply_commands.assert_not_called()
    assert result is False  # changed=False


def test_drain_queue_applies_commands_in_apply_phase():
    """有命令时 APPLY 阶段应用命令并返回 changed=True。"""
    session = _make_session()
    cmd = WriteLineCmd(text="x")
    session._apply_commands = Mock()
    session._drain_commands_locked = Mock(return_value=([cmd], True, True))
    session._should_render = Mock(return_value=False)  # 本帧不渲染
    result = session._drain_queue()
    session._apply_commands.assert_called_once_with([cmd])
    assert result is True


# ═══════════════════════════════════════════════════════════
# 3. 锁不可用 → 跳过本帧
# ═══════════════════════════════════════════════════════════

def test_drain_queue_skips_frame_when_lock_unavailable():
    """DRAIN_COMMANDS 锁不可用 → 跳过本帧（返回 False，不渲染不应用）。"""
    session = _make_session()
    session._phase_process_sigwinch = Mock()
    session._phase_process_input = Mock()
    session._phase_pre_update_panels = Mock()
    session._update_system_stats = Mock()
    # 锁超时：locked=False
    session._drain_commands_locked = Mock(return_value=([], False, False))
    session._apply_commands = Mock()
    session._render_frame = Mock()

    result = session._drain_queue()

    assert result is False
    session._apply_commands.assert_not_called()
    session._render_frame.assert_not_called()


# ═══════════════════════════════════════════════════════════
# 4. _drain_commands_locked 排空语义
# ═══════════════════════════════════════════════════════════

def test_drain_commands_locked_collects_commands():
    """_drain_commands_locked 排空队列命令并返回 (commands, changed, locked=True)。"""
    session = _make_session()
    session.push_cmd(WriteLineCmd(text="a"))
    session.push_cmd(WriteLineCmd(text="b"))

    commands, changed, locked = session._drain_commands_locked()

    assert locked is True
    assert changed is True
    texts = [getattr(c, "text", "") for c in commands]
    assert texts == ["a", "b"]
    assert session._cmd_queue.empty()


def test_drain_commands_locked_empty_queue():
    """队列空时返回 ([], False, True)。"""
    session = _make_session()
    commands, changed, locked = session._drain_commands_locked()
    assert commands == []
    assert changed is False
    assert locked is True


def test_drain_commands_locked_lock_timeout():
    """锁超时返回 ([], False, False)——调用方跳过本帧。"""
    session = _make_session()
    with patch(
        "src.tui.ink.session._try_acquire_output_lock",
        return_value=_FakeLockCtx(locked=False),
    ):
        commands, changed, locked = session._drain_commands_locked()
    assert commands == []
    assert changed is False
    assert locked is False


# ═══════════════════════════════════════════════════════════
# 5. 渲染失败指数退避（RENDER 阶段异常路径）
# ═══════════════════════════════════════════════════════════

def test_drain_queue_render_failure_backoff():
    """RENDER 阶段渲染失败 → 置脏重试 + 连续失败计数递增。"""
    session = _make_session()
    session._drain_commands_locked = Mock(return_value=([], False, True))
    session._should_render = Mock(return_value=True)
    session._render_frame = Mock(side_effect=RuntimeError("boom"))
    session._consecutive_render_failures = 0
    with patch("src.tui.ink.session.time.sleep"):
        result = session._drain_queue()
    assert result is False
    assert session._consecutive_render_failures == 1
    assert session._dirty is True  # 失败帧补置脏标记（下一拍重试）


class _FakeLockCtx:
    """模拟 _try_acquire_output_lock 上下文管理器（locked 可控）。"""

    def __init__(self, locked: bool):
        self._locked = locked

    def __enter__(self):
        return self._locked

    def __exit__(self, *exc):
        return False
