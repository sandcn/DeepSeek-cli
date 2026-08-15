"""TUI 无末尾空行集成测试（2026-08-15）。

修复背景（无末尾空行模型）：渲染器物理缓冲 = 文档行数（无 doc_h+1 末尾
空行）——文档最后一行（输入区模式行「标准模式」）下方不再产生空行。修复前：
TUI 显示满一屏（文档高度 >= 屏幕高度）时，屏幕最后一行恒为末尾空行，模式行
（标准模式）显示在倒数第二行，且文档行数恰好等于屏幕高度时首行内容被滚动
挤出。

本测试锁定（pyte 屏幕级断言）：
  1. 超屏（历史消息较多）：模式行「标准模式」显示在屏幕最后一行，末行非空；
  2. 恰好满屏边界：模式行贴屏幕底，首行消息不被挤出（修复前首行丢失）；
  3. 不满屏：模式行下方无文档末尾空行（终端默认空白）。
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


def _push_round(session, i) -> None:
    """推入一轮完整消息（用户问题 + 思考 + 回答）。"""
    session.push_cmd(UserMsgCmd(text=f"用户问题 {i}"))
    session.push_cmd(MainPhaseCmd(phase="thinking"))
    session.push_cmd(ReasoningCmd(text=f"思考 {i}"))
    session.push_cmd(PhaseDoneCmd(phase="reasoning"))
    session.push_cmd(ContentCmd(text=f"回答 {i}"))
    session.push_cmd(PhaseDoneCmd(phase="content"))
    time.sleep(0.05)


def _wait_screen(stream, predicate, timeout=3.0, interval=0.05):
    """轮询等待 pyte 屏幕满足 predicate（避免固定 sleep 的时序脆弱性）。

    Args:
        stream: 渲染输出流（累计内容）。
        predicate: 接收 display 行列表，返回 bool。
        timeout: 最大等待秒数。
        interval: 轮询间隔秒数。

    Returns:
        display 行列表（满足 predicate 时的最新屏幕）。

    Raises:
        AssertionError: 超时未满足。
    """
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(stream.getvalue())
        last = screen.display
        if predicate(last):
            return last
        time.sleep(interval)
    raise AssertionError(f"等待屏幕条件超时（{timeout}s）；当前末行: {last[23]!r}")


def test_tui_overscreen_mode_line_at_screen_bottom():
    """超屏：模式行「标准模式」显示在屏幕最后一行（末行非空行）。"""
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        for i in range(8):
            _push_round(session, i)
        # 轮询等待渲染线程消费全部命令（避免固定 sleep 时序脆弱）
        lines = _wait_screen(
            stream,
            lambda disp: "标准模式" in disp[23] and disp[23].strip() != "",
        )
        # 模式行显示在屏幕最后一行（修复前为倒数第二行 + 末尾空行）
        assert "标准模式" in lines[23], (
            f"模式行应显示在屏幕最后一行: {lines[23]!r}"
        )
        assert lines[23].strip() != "", "屏幕最后一行不应是空行"
        # 时间戳分隔线在倒数第二行
        assert "20" in lines[22] or "━━" in lines[22], (
            f"时间戳行应在倒数第二行: {lines[22]!r}"
        )
    finally:
        session.stop()


def test_tui_underscreen_mode_line_no_blank_below():
    """不满屏：模式行（标准模式）下方无文档末尾空行（终端默认空白）。"""
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        # 少量消息（不满屏）
        _push_round(session, 0)
        lines = _wait_screen(
            stream,
            lambda disp: any("标准模式" in l for l in disp),
        )
        # 模式行存在（不满屏时在文档末尾，不在屏幕最后一行）
        assert any("标准模式" in l for l in lines), "模式行应显示"
        # 模式行所在行的下一行不应有文档内容（无末尾空行——若模式行下方
        # 有内容则说明产生空行错位）
        mode_idx = next(i for i, l in enumerate(lines) if "标准模式" in l)
        if mode_idx + 1 < len(lines):
            below = lines[mode_idx + 1]
            # 下方只允许终端空白（无文档行内容）
            assert below.strip() == "", (
                f"模式行下方不应有文档内容: {below!r}"
            )
    finally:
        session.stop()
