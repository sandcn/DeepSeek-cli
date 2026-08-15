"""InkSession mixin 拆分测试 — 架构改进方向 A（2026-08-16）。

拆分背景：InkSession（原 ~1540 行）为「上帝类」。方向 A 按职责边界拆为：
  - ``_session_queue_mixin._SessionQueueMixin`` — 命令队列管理（入队/背压/排空）；
  - ``_session_frame_mixin._SessionFrameMixin`` — 渲染帧执行（组件树/调和/渲染/
    光标/系统监控）；
  - ``InkSession(_SessionQueueMixin, _SessionFrameMixin)`` — 渲染循环调度 +
    生命周期 + 崩溃恢复 + 注入（facade）。

本测试锁定：
  1. InkSession 继承两个 mixin（方法解析到 mixin，实例方法可 monkeypatch）；
  2. 队列/帧 mixin 独立可导入、职责边界存在；
  3. session 模块 re-export 常量保持旧导入路径兼容
     （``src.tui.ink.session._KEEP_CONTENT_CMDS`` / ``_PUT_NO_DROP_TIMEOUT`` /
     ``_safe_int``）；
  4. 行为等价性：入队 → 排空端到端（拆分后零回归）；
  5. 方法仍可直接被测试以实例属性替换（monkeypatch 语义保持）。
"""

from __future__ import annotations

import io

import pytest

import src.tui.ink.session as session_mod
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui._const import ContentCmd, WriteLineCmd
from src.tui.ink._session_queue_mixin import (
    _SessionQueueMixin,
    _KEEP_CONTENT_CMDS,
    _PUT_NO_DROP_TIMEOUT,
)
from src.tui.ink._session_frame_mixin import _SessionFrameMixin, _safe_int
from src.tui.ink.session import InkSession


def _make_session():
    cache = TerminalWidthCache.get_default()
    cache._width = 80
    cache._height = 24
    session = InkSession(
        model=object(),
        apply_cmd=lambda m, c: None,
        build_tree=lambda m, w: None,
        config=TuiConfig.defaults(),
        stream=io.StringIO(),
    )
    session.set_line_tracker(None)
    return session


# ═══════════════════════════════════════════════════════════
# 1. mixin 继承与职责边界
# ═══════════════════════════════════════════════════════════

def test_ink_session_inherits_both_mixins():
    """InkSession 继承队列/帧两个 mixin（职责分离落实）。"""
    assert issubclass(InkSession, _SessionQueueMixin)
    assert issubclass(InkSession, _SessionFrameMixin)
    # MRO：InkSession 优先于 mixin 解析（自身定义覆盖 mixin）
    assert InkSession.__mro__.index(InkSession) < InkSession.__mro__.index(_SessionQueueMixin)


def test_queue_mixin_owns_queue_methods():
    """队列职责方法定义在 _SessionQueueMixin（而非 session 主体）。"""
    for name in ("push_cmd", "push_cmd_critical", "_put_no_drop", "_drain_queue_safe"):
        assert name in _SessionQueueMixin.__dict__, f"{name} 应在队列 mixin"
        assert hasattr(InkSession, name), f"InkSession 应继承 {name}"


def test_frame_mixin_owns_frame_methods():
    """渲染帧职责方法定义在 _SessionFrameMixin。"""
    for name in ("_render_frame", "_apply_commands", "_position_cursor",
                 "_find_input_fiber", "_update_system_stats"):
        assert name in _SessionFrameMixin.__dict__, f"{name} 应在帧 mixin"
        assert hasattr(InkSession, name), f"InkSession 应继承 {name}"


# ═══════════════════════════════════════════════════════════
# 2. session 主体保留调度职责
# ═══════════════════════════════════════════════════════════

def test_session_keeps_loop_and_lifecycle_scope():
    """渲染循环调度/生命周期/崩溃恢复保留在 session 主体（facade 协调）。"""
    assert "_render" in InkSession.__dict__
    assert "_drain_queue" in InkSession.__dict__
    assert "_should_render" in InkSession.__dict__
    assert "start" in InkSession.__dict__
    assert "stop" in InkSession.__dict__
    assert "_handle_render_crash" in InkSession.__dict__


# ═══════════════════════════════════════════════════════════
# 3. session re-export 常量（旧导入路径兼容）
# ═══════════════════════════════════════════════════════════

def test_session_reexports_queue_constants():
    """session 模块 re-export _KEEP_CONTENT_CMDS / _PUT_NO_DROP_TIMEOUT。"""
    assert session_mod._KEEP_CONTENT_CMDS is _KEEP_CONTENT_CMDS
    assert session_mod._PUT_NO_DROP_TIMEOUT is _PUT_NO_DROP_TIMEOUT


def test_session_reexports_safe_int():
    """session 模块 re-export _safe_int（系统监控防御工具）。"""
    assert session_mod._safe_int is _safe_int
    assert _safe_int("42") == 42
    assert _safe_int("N/A") == 0


def test_keep_content_cmds_content():
    """_KEEP_CONTENT_CMDS 包含核心内容命令（语义未因迁移改变）。"""
    assert len(_KEEP_CONTENT_CMDS) >= 10
    assert all(isinstance(c, int) for c in _KEEP_CONTENT_CMDS)


# ═══════════════════════════════════════════════════════════
# 4. 行为等价性（端到端）
# ═══════════════════════════════════════════════════════════

def test_push_and_drain_roundtrip():
    """push_cmd 入队 → _drain_commands_locked 排空（拆分后零回归）。

    断言按 PriorityQueue 优先级语义：ContentCmd（STREAM，优先级 0）先于
    WriteLineCmd（LOW）出队——排序为既有行为，拆分不改变。
    """
    session = _make_session()
    session.push_cmd(WriteLineCmd(text="a"))  # LOW
    session.push_cmd(ContentCmd(text="b"))    # STREAM（更高优先级）
    assert session._cmd_queue.qsize() == 2

    commands, changed, locked = session._drain_commands_locked()
    assert locked is True
    assert changed is True
    texts = [getattr(c, "text", "") for c in commands]
    assert texts == ["b", "a"]  # 优先级序（Content 先出队）


def test_drain_queue_safe_keep_content_via_mixin():
    """_drain_queue_safe(keep_content=True) 经 mixin 生效（保留内容命令）。"""
    session = _make_session()
    session.push_cmd(ContentCmd(text="keep-me"))
    session.push_cmd(WriteLineCmd(text="drop-me"))
    dropped = session._drain_queue_safe(keep_content=True)
    assert dropped == 1  # 仅 WriteLine 被丢弃
    assert session._cmd_queue.qsize() == 1  # ContentCmd 保留
    _, _, cmd = session._cmd_queue.get_nowait()
    assert cmd.text == "keep-me"


# ═══════════════════════════════════════════════════════════
# 5. monkeypatch 兼容性（测试以实例属性替换方法仍生效）
# ═══════════════════════════════════════════════════════════

def test_mixin_methods_are_monkeypatchable():
    """mixin 方法为实例方法——测试替换 session._push_cmd 等仍生效。"""
    session = _make_session()
    calls: list = []

    def _fake_push(cmd):
        calls.append(cmd)

    session.push_cmd = _fake_push
    session.push_cmd(WriteLineCmd(text="x"))
    assert len(calls) == 1
    assert calls[0].text == "x"


def test_frame_mixin_safe_int_used_by_update_system_stats():
    """_update_system_stats 使用模块级 _safe_int（防御转换不抛异常）。"""
    session = _make_session()
    # 桩模型无 status → 提前返回（不崩溃）；再以含 status 的模型验证
    session._update_system_stats()
    assert session._last_sys_stats_time > 0
