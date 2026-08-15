"""残留 Enter 丢弃标记修复回归测试（2026-08-16，editmsg 按回车有时不能编辑）。

修复背景：``_enter_residual_pending`` 标记本为 CR+LF 终端设计——CR 触发
enter 事件后紧随的 LF 是同一按键的残留字节，须在窗口内丢弃防误提交/误确认。
但 Python 3.9 ``tty.setcbreak`` 只关 ICANON+ECHO、**不关 ICRNL**（POSIX/Cygwin
驱动将 Enter 的 ``\\r`` 转换为 ``\\n``）——程序读到的是 **LF (0x0a)**，LF
本身就是完整按键，不存在"残留"。修复前无条件置标记：``/editmsg`` 提交回车
（LF）置标记后，0.5s 窗口内用户在弹窗按 Enter 确认（LF）会被
``_dispatch_byte`` 误吞 → 弹窗无响应（"按回车有时不能编辑消息"，需再按一次）。

修复：``_mark_enter_residual`` 仅当触发 enter 的原始字节为 CR（0x0d）时置
标记丢弃紧随 LF；LF 触发（LF-only 终端）或 CSI u 增强键盘协议（完整序列无
CR+LF 字节对）不置标记。

测试约定：构造 InputIO / InputBufferEditor / InputParser / InputDispatcher
直接调用，不经真实 stdin（与 test_review_input.py 同约定）。
"""

from __future__ import annotations

from pathlib import Path

from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser, KeyEvent


def _make_dispatcher() -> tuple[InputDispatcher, InputBufferEditor]:
    """构造测试用 dispatcher（不经真实 stdin）。"""
    io = InputIO(fd=0)
    be = InputBufferEditor(history_file=Path("unused"))
    parser = InputParser(io=io)
    disp = InputDispatcher(io=io, buffer_editor=be, parser=parser)
    return disp, be


# ═══════════════════════════════════════════════════════════
# 核心修复：LF-only 终端（Python 3.9 setcbreak+ICRNL）不置残留标记
# ═══════════════════════════════════════════════════════════

def test_lf_enter_not_mark_residual():
    """核心修复：LF-only 终端 Enter 读到 LF（0x0a）——LF 即完整按键，无
    残留——提交后**不**置 ``_enter_residual_pending``（修复前无条件置标记，
    0.5s 窗口内用户下一次真实 Enter 被误吞）。"""
    disp, be = _make_dispatcher()
    be.set_buffer("/editmsg")
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\n"))
    assert be.get_queued_input() == "/editmsg"
    # 修复点：LF 触发不置标记
    assert disp._enter_residual_pending is False
    assert disp._enter_residual_deadline == 0.0


def test_cr_enter_marks_residual_lf_discarded():
    """回归：CR 触发 enter（CR+LF 终端）置残留标记；紧随 LF 在窗口内被
    ``_dispatch_byte`` 丢弃（不触发第二次 enter 提交）——既有行为不变。"""
    disp, be = _make_dispatcher()
    be.set_buffer("hello")
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\r"))
    assert disp._enter_residual_pending is True
    assert disp._enter_residual_deadline > 0.0
    assert be.get_queued_input() == "hello"
    # 紧随 LF 被丢弃：不产生第二次提交
    be.drain_all()
    disp._dispatch_byte(0x0a)
    assert be.get_queued_input() is None
    assert disp._enter_residual_pending is False


def test_csi_u_enter_not_mark_residual():
    """CSI u 增强键盘协议 Enter（\\x1b[13;1u，kitty/wezterm）——完整序列
    无 CR+LF 字节对，不置残留标记（窗口内用户后续 Enter 不误吞）。"""
    disp, be = _make_dispatcher()
    be.set_buffer("cmd")
    disp._dispatch_key_event(KeyEvent(
        kind="enter", modifier=1, keycode=13, raw=b"\x1b[13;1u",
    ))
    assert be.get_queued_input() == "cmd"
    assert disp._enter_residual_pending is False


# ═══════════════════════════════════════════════════════════
# Bug 场景：editmsg 弹窗确认 Enter 不被 LF-only 残留标记误吞
# ═══════════════════════════════════════════════════════════

def test_editmsg_confirm_enter_not_swallowed_lf_terminal():
    """Bug 回归（LF-only 终端）：``/editmsg`` 提交回车（LF）后，弹窗确认
    Enter（LF）必须正常到达 router——修复前提交置标记，0.5s 窗口内确认
    Enter 被 ``_dispatch_byte`` 丢弃 → 弹窗无响应（"按回车有时不能编辑
    消息"）。"""
    disp, be = _make_dispatcher()
    # 1. 输入 /editmsg 并按 Enter（LF-only 终端：读到 0x0a）
    be.set_buffer("/editmsg")
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\n"))
    assert be.get_queued_input() == "/editmsg"
    assert disp._enter_residual_pending is False  # 修复点：LF 触发不置标记
    # 2. 编排器消费命令，进入编辑模式（message_editor 抑制 Enter）
    be.drain_all()
    disp.set_suppress_enter(True)
    # 3. 用户按 Enter 确认选择（LF-only 终端：读到 0x0a）——
    #    注册 router 模拟 UserSelectPopup 消费确认
    consumed: list[str] = []
    disp.set_input_hook_router(lambda ev: (consumed.append(ev.kind), True)[1])
    disp._dispatch_byte(0x0a)
    assert consumed == ["enter"], "确认 Enter（LF）被残留标记误吞"
    assert disp._enter_residual_pending is False


def test_editmsg_confirm_enter_lf_discarded_crlf_terminal():
    """回归（CR+LF 终端）：``/editmsg`` 提交（CR）置标记丢弃紧随 LF；弹窗
    确认 Enter（CR）触发 router 消费后，残留 LF 仍被丢弃（不误提交 prefill）
    ——既有 CR+LF 语义不受修复影响。"""
    disp, be = _make_dispatcher()
    # 1. /editmsg 提交（CR 触发）
    be.set_buffer("/editmsg")
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\r"))
    assert disp._enter_residual_pending is True
    # 2. 紧随 LF 被丢弃（CR+LF 终端的残留字节）
    disp._dispatch_byte(0x0a)
    assert disp._enter_residual_pending is False
    # 3. 弹窗确认 Enter（CR）：router 消费
    be.drain_all()
    disp.set_suppress_enter(True)
    consumed: list[str] = []
    disp.set_input_hook_router(lambda ev: (consumed.append(ev.kind), True)[1])
    disp._dispatch_byte(0x0d)
    assert consumed == ["enter"]
    assert disp._enter_residual_pending is True  # router 消费 CR 后置标记
    # 4. 残留 LF 被丢弃（不误提交）
    disp._dispatch_byte(0x0a)
    assert disp._enter_residual_pending is False


def test_editmsg_suppress_enter_lf_confirm_not_marked():
    """弹窗期间（suppress_enter=True）Enter 走抑制分支：LF-only 终端确认
    Enter（LF）不置残留标记——修复前置标记后 0.5s 窗口内 prefill 提交
    Enter（LF）被误吞。"""
    disp, be = _make_dispatcher()
    be.set_buffer("x")
    disp.set_suppress_enter(True)
    # 模拟弹窗确认 Enter 经 dismiss 回调路径（router 未消费 → 抑制分支）
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\n"))
    assert disp._enter_residual_pending is False


# ═══════════════════════════════════════════════════════════
# 搜索模式 Enter：LF 触发不置标记
# ═══════════════════════════════════════════════════════════

def test_search_enter_lf_not_mark_residual():
    """搜索模式 Enter 应用匹配：LF 触发不置残留标记（修复前置标记会在
    LF-only 终端吞掉用户退出搜索后的下一次 Enter）。"""
    disp, be = _make_dispatcher()
    assert be.search_enter("q") is True
    disp._dispatch_key_event(KeyEvent(kind="enter", raw=b"\n"))
    assert not be.is_search_active()
    assert disp._enter_residual_pending is False
