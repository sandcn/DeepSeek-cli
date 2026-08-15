"""InkSession CLEAR_MSGS 后全量重写测试（2026-08-15 修复）。

修复背景：/editmsg /deitmsg 编辑后经 CLEAR_MSGS（reset_display）+ DISPLAY_MSGS
整篇重建聊天区。修复前渲染器对「文档高度大减 + 内容全变」的帧走
``_rewrite_drifted`` 漂移路径：首差异行 0 触发底部对齐切换（BUG-68 语义），
物理缓冲（buf_h）与文档高度严重不匹配（漂移），后续增量增长
（``_grow_drifted``）只重写「新旧内容不同」的行——状态栏/输入区等新旧内容
相同的行不重写 → 屏幕布局错乱（状态栏丢失、内容错位、大量空行）。

修复：``_apply_commands`` 检测到 CLEAR_MSGS 时置 ``_resize_pending`` →
下一帧 ``_render_frame`` 经 ``reset(full=True)`` **全量重写**（非漂移增量），
clear+display 后文档从干净起点重建，后续新内容正常增量渲染。

本测试锁定：
  1. CLEAR_MSGS 后渲染走全量重写（渲染器 prev 被重置）；
  2. /editmsg 重渲染 + 新短内容后：短思/短答/状态栏/输入区均正常显示
     （无布局错乱）。
"""

from __future__ import annotations

import io
import time

import pyte

from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.ink.session import InkSession
from src.tui._const import (
    UserMsgCmd, MainPhaseCmd, ReasoningCmd, ContentCmd, PhaseDoneCmd,
    ClearMsgsCmd, DisplayMsgsCmd,
)


def _make_session(height=24, width=80):
    cache = TerminalWidthCache.get_default()
    cache._width = width
    cache._height = height
    model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    session.set_line_tracker(None)
    return session, model, stream


def _push_history(session, rounds=8) -> None:
    for i in range(rounds):
        session.push_cmd(UserMsgCmd(text=f"用户问题 {i}"))
        session.push_cmd(MainPhaseCmd(phase="thinking"))
        session.push_cmd(ReasoningCmd(text=f"思考 {i}"))
        session.push_cmd(PhaseDoneCmd(phase="reasoning"))
        session.push_cmd(ContentCmd(text=f"回答 {i}"))
        session.push_cmd(PhaseDoneCmd(phase="content"))
        time.sleep(0.05)


def _push_editmsg(session, rounds=8) -> None:
    """模拟 /editmsg：清空显示 + 重渲染历史（删除第 2 条）。"""
    msgs = []
    for i in range(rounds):
        if i == 2:
            continue
        msgs.append({"role": "user", "content": f"用户问题 {i}"})
        msgs.append({
            "role": "assistant", "content": f"回答 {i}",
            "reasoning_content": f"思考 {i}",
        })
    session.push_cmd(ClearMsgsCmd())
    session.push_cmd(DisplayMsgsCmd(messages=msgs))


def _push_short_content(session) -> None:
    session.push_cmd(MainPhaseCmd(phase="thinking"))
    session.push_cmd(ReasoningCmd(text="短思"))
    session.push_cmd(PhaseDoneCmd(phase="reasoning"))
    session.push_cmd(ContentCmd(text="短答"))
    session.push_cmd(PhaseDoneCmd(phase="content"))


def test_clear_msgs_sets_resize_pending():
    """CLEAR_MSGS 命令应用后置 _resize_pending（触发下一帧全量重写）。"""
    session, model, _ = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        _push_history(session, rounds=3)
        time.sleep(0.3)
        # 直接应用 CLEAR_MSGS（绕过渲染线程异步时序，验证 apply 路径）
        session._resize_pending = False
        session._apply_commands([ClearMsgsCmd()])
        assert session._resize_pending is True, (
            "CLEAR_MSGS 应用后应置 _resize_pending"
        )
        assert len(model.blocks) == 0, "reset_display 应清空聊天块"
    finally:
        session.stop()


def test_editmsg_render_layout_correct():
    """/editmsg 重渲染 + 新短内容后：短思/短答/状态栏/输入区均正常显示（无错乱）。"""
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        _push_history(session, rounds=8)
        time.sleep(0.4)
        stream.seek(0); stream.truncate(0)

        _push_editmsg(session, rounds=8)
        time.sleep(0.5)
        _push_short_content(session)
        time.sleep(0.8)

        out = stream.getvalue()
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(out)
        lines = screen.display
        # 新短内容显示
        assert any("短思" in l for l in lines), "短思应显示"
        assert any("短答" in l for l in lines), "短答应显示"
        # 状态栏/输入区完整（修复前丢失）
        assert any("输入消息" in l for l in lines), "输入区应显示"
        assert any("标准模式" in l for l in lines), "模式行应显示"
        # 布局紧凑：无大量连续空行（修复前 5+ 连续空行错乱）
        max_blank_streak = 0
        cur = 0
        for l in lines:
            if l.strip() == "":
                cur += 1
                max_blank_streak = max(max_blank_streak, cur)
            else:
                cur = 0
        assert max_blank_streak <= 2, f"布局错乱：连续 {max_blank_streak} 个空行"
        # 模型状态包含短内容
        tail = [l.plain for l in model.committed_lines]
        assert "短思" in tail and "短答" in tail
    finally:
        session.stop()


def test_editmsg_without_short_content_still_ok():
    """/editmsg 重渲染本身（无新内容）布局也正常。"""
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        _push_history(session, rounds=6)
        time.sleep(0.4)
        stream.seek(0); stream.truncate(0)

        _push_editmsg(session, rounds=6)
        time.sleep(0.8)

        out = stream.getvalue()
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(out)
        lines = screen.display
        # 状态栏/输入区完整
        assert any("输入消息" in l for l in lines), "输入区应显示"
        assert any("标准模式" in l for l in lines), "模式行应显示"
        # 重渲染后的历史可见（删除第 2 条后的内容）
        assert any("回答 5" in l for l in lines), "历史内容应显示"
    finally:
        session.stop()
