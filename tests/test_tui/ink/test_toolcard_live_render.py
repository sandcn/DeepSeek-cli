"""live 工具卡渲染崩溃回归测试（BUG-80）。

根因：全面控件化（阶段6 方案B，commit df35a54）把工具卡迁移为
``ToolCard → Panel(border=0)``（无边框模式），但 ``widgets/_panel.py``
**漏导入 ``Column``**——无边框分支 ``h(Column, None, inner)`` 每次渲染
抛 ``NameError: name 'Column' is not defined``。工具卡开放（live）期间
**每一帧**渲染都崩溃 → 渲染线程崩溃恢复（``max_recover_attempts=3``，
每次 0.5s）耗尽后**永久终止** → 工具调用之后所有新增内容不再渲染
（用户报障「调用 bash 后 TUI 显示空白行」的根因——bash 卡 live 渲染
崩溃 + 恢复预算耗尽）。

修复：``_panel.py`` 补 ``from ..widgets.layout import Column``。

覆盖：
- ``Panel(border=0)`` 无边框模式直接渲染不抛异常（单元）；
- session 级：工具卡开放期间 live 渲染标题+输出正常、关闭后后续
  content 回答正常上屏（修复前渲染线程死亡 → 屏幕空白）；
- 渲染线程在工具卡序列后仍存活（``_render_running``，修复前 False）。
"""
from __future__ import annotations

import io
import time

import pyte

from src.tui._config import TuiConfig
from src.tui._const import (
    UserMsgCmd, MainPhaseCmd, ReasoningCmd, ContentCmd, PhaseDoneCmd,
    ToolOpenCmd, ToolOutputCmd, ToolCloseCmd, ToolCountIncCmd, ToolCountDecCmd,
)
from src.tui._screen import TerminalWidthCache
from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.ink.session import InkSession
from src.tui.ink.widgets._panel import Panel


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


def _wait_screen(stream, predicate, timeout=3.0, interval=0.05):
    """轮询等待 pyte 屏幕满足 predicate（与 test_renderer_no_blank 同模式）。"""
    deadline = time.monotonic() + timeout
    last = []
    while time.monotonic() < deadline:
        screen = pyte.Screen(80, 24)
        pyte.Stream(screen).feed(stream.getvalue())
        last = screen.display
        if predicate(last):
            return last
        time.sleep(interval)
    raise AssertionError(f"等待屏幕条件超时（{timeout}s）；末行: {last[23]!r}")


# ── 单元：Panel 无边框模式 ───────────────────────────────

def test_panel_borderless_renders_without_name_error():
    """Panel(border=0) 无边框模式直接渲染（修复前 NameError: Column）。"""
    from src.tui.ink.widgets.layout import Column
    el = Panel({"border": 0})
    assert el is not None
    assert el.type is Column, (
        f"无边框 Panel 应渲染为 Column 组件: {el.type}"
    )


# ── session 级：工具卡 live 渲染 + 后续内容上屏 ──────────

def test_tool_card_live_render_and_aftermath_visible():
    """工具卡 live 渲染不崩溃，关闭后新增回答正常上屏（BUG-80）。

    修复前：live 工具卡每帧渲染抛 NameError → 崩溃恢复 3 次耗尽 →
    渲染线程永久终止 → 工具调用后的 content 回答不渲染（屏幕空白行）。
    """
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        session.push_cmd(UserMsgCmd(text="请执行命令"))
        session.push_cmd(MainPhaseCmd(phase="thinking"))
        session.push_cmd(ReasoningCmd(text="让我跑个命令"))
        session.push_cmd(PhaseDoneCmd(phase="reasoning"))
        session.push_cmd(ToolOpenCmd(tool_name="bash", tool_id="t1", detail="ls -la"))
        session.push_cmd(ToolCountIncCmd())
        # 输出多行（触发 bash 尾显示 + 增量提交路径）
        for i in range(70):
            session.push_cmd(ToolOutputCmd(tool_id="t1", text=f"line{i}"))
        session.push_cmd(ToolCloseCmd(tool_id="t1", success=True))
        session.push_cmd(ToolCountDecCmd())
        session.push_cmd(ContentCmd(text="工具执行完毕，这是回答"))
        session.push_cmd(PhaseDoneCmd(phase="content"))

        lines = _wait_screen(
            stream,
            lambda disp: any("这是回答" in l for l in disp),
        )
        # 渲染线程必须存活（修复前 Panel NameError 崩溃 3 次后永久终止）
        assert session._render_running, (
            "渲染线程应存活——工具卡 live 渲染不再崩溃"
        )
        # 工具卡标题行（含工具名）正常显示
        assert any("Bash" in l for l in lines), (
            f"工具卡标题应显示: {lines}"
        )
        # 工具输出内容正常显示（bash 尾显示保留最后一行）
        assert any("line69" in l for l in lines), (
            f"工具输出应显示: {lines}"
        )
        # 工具调用之后新增的回答正常上屏（修复前为空白）
        assert any("这是回答" in l for l in lines), (
            f"工具调用后的回答应显示（修复前空白）: {lines}"
        )
    finally:
        session.stop()


def test_tool_card_open_renders_live_title_and_output():
    """工具卡开放（live）期间标题与输出实时渲染（修复前每次 live 帧崩溃）。"""
    session, model, stream = _make_session()
    session.start()
    time.sleep(0.15)
    try:
        session.push_cmd(UserMsgCmd(text="跑一下"))
        session.push_cmd(ToolOpenCmd(tool_name="bash", tool_id="t1", detail="echo hi"))
        session.push_cmd(ToolCountIncCmd())
        session.push_cmd(ToolOutputCmd(tool_id="t1", text="hello from bash"))
        # 工具卡未关闭：live 渲染路径（ToolCard → Panel border=0）
        lines = _wait_screen(
            stream,
            lambda disp: any("hello from bash" in l for l in disp),
        )
        assert any("Bash" in l for l in lines), (
            f"live 工具卡标题应显示: {lines}"
        )
        assert session._render_running, "渲染线程应存活"
        # 清理：关闭工具卡 + 关闭 reasoning/content 通道避免 stop 竞态
        session.push_cmd(ToolCloseCmd(tool_id="t1", success=True))
        session.push_cmd(ToolCountDecCmd())
    finally:
        session.stop()
