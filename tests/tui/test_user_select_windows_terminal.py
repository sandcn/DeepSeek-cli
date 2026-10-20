"""user_select 在原生 Windows 控制台弹出选择界面（回归测试，2026-10-20）。

用户现象：Windows 下运行 TUI（Windows Terminal / cmd）时，模型调用
``user_select`` 后**看不到弹窗**——工具直接返回 ``non_interactive``。

根因：``UserSelectFunc._execute_terminal_async`` 以 ``HAS_TERMIOS`` 作为
「终端是否可交互」的判据。原生 Windows 没有 termios（``HAS_TERMIOS``
恒为 False），但 TUI 的输入层已由 ``src._win_console``
（``ReadConsoleInputW`` + ``SetConsoleMode``）承载——不依赖 termios 即可
逐键读取。修复后判据改为 ``_interactive_stdin()``：

  - POSIX/Cygwin：stdin 是 TTY；
  - 原生 Windows：控制台句柄可用（``_win_console.supported()``）即可交互。

本文件固化：
  1. ``_interactive_stdin`` 的四种输入组合（TTY / 非 TTY / Windows 控制台 /
     Windows 非控制台）；
  2. Windows 控制台场景下 ``execute()`` **打开弹窗**（``model.user_selects``
     追加、``bottom_view="user_select"``），用户确认后返回 confirmed，
     不再静默回退 non_interactive；
  3. 真正的非交互环境（无 TTY 且无 Windows 控制台）仍回退 non_interactive
     （单次模式 / 管道输入行为不变）。
"""

from __future__ import annotations

import json

import pytest

from src.tui.app.model import AppModel


class _FakeInput:
    def flush_stdin_buffer(self):
        pass


class _FakeBottomBar:
    is_completion_visible = False


class _FakeChatUI:
    """最小 ChatUIConsumer 桩（get_model / request_bottom_redraw 协议）。"""

    def __init__(self, model):
        self._model = model
        self.bottom_bar = _FakeBottomBar()
        self.redraw_calls = 0

    def get_model(self):
        return self._model

    def get_input_component(self):
        return _FakeInput()

    def request_bottom_redraw(self):
        self.redraw_calls += 1

    def flush_input_router(self, _timeout=2.0):
        return True


class _FakeStdin:
    def __init__(self, fileno=0, raises=False):
        self._fileno = fileno
        self._raises = raises

    def fileno(self):
        if self._raises:
            raise ValueError("no fileno")
        return self._fileno


class _FakeSys:
    def __init__(self, stdin):
        self.stdin = stdin


def _patch_env(
    monkeypatch, *, has_termios, isatty, win_console_supported,
    fileno=0, fileno_raises=False,
):
    """构造 user_select 工具执行环境（终端/Windows 控制台/ChatUI 桩）。"""
    # 实现在 L0 的 src._win_console（tools 不得依赖 src.tui）；patch 真实模块
    from src import _win_console
    from src.tools import user_select as us_mod

    monkeypatch.setattr(us_mod, "HAS_TERMIOS", has_termios)
    monkeypatch.setattr(us_mod.os, "isatty", lambda _fd: isatty)
    monkeypatch.setattr(
        us_mod, "sys", _FakeSys(_FakeStdin(fileno, fileno_raises)),
    )
    monkeypatch.setattr(
        _win_console, "supported", lambda: win_console_supported, raising=False,
    )
    model = AppModel()
    fake = _FakeChatUI(model)
    monkeypatch.setattr(us_mod, "get_display_target", lambda: fake)
    return us_mod, model, fake


# ── 1. _interactive_stdin 判据 ─────────────────────────────

def test_interactive_stdin_posix_tty(monkeypatch):
    """POSIX：stdin 是 TTY → 可交互。"""
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=True, isatty=True, win_console_supported=False,
    )
    assert us_mod._interactive_stdin() is True


def test_interactive_stdin_posix_pipe(monkeypatch):
    """POSIX：stdin 非 TTY（管道/重定向）→ 不可交互。"""
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=True, isatty=False, win_console_supported=True,
    )
    # POSIX 分支不采信 Windows 控制台探测（HAS_TERMIOS=True）
    assert us_mod._interactive_stdin() is False


def test_interactive_stdin_native_windows_console(monkeypatch):
    """原生 Windows：termios 不可用但控制台句柄可用 → 可交互。"""
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=False, isatty=True, win_console_supported=True,
    )
    assert us_mod._interactive_stdin() is True


def test_interactive_stdin_native_windows_console_fallback(monkeypatch):
    """原生 Windows：isatty 为 False 但 Win32 控制台后端可用 → 可交互。

    isatty 在个别宿主（嵌入式/重定向了 Python 文件对象的控制台）可能为
    False，``_win_console.supported()`` 以控制台句柄为准兜底。
    """
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=False, isatty=False, win_console_supported=True,
    )
    assert us_mod._interactive_stdin() is True


def test_interactive_stdin_native_windows_no_console(monkeypatch):
    """原生 Windows：非控制台（重定向/服务）→ 不可交互。"""
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=False, isatty=False, win_console_supported=False,
    )
    assert us_mod._interactive_stdin() is False


def test_interactive_stdin_fileno_error(monkeypatch):
    """stdin 无 fileno（如被替换为 StringIO）→ 不可交互（不抛异常）。"""
    us_mod, _model, _fake = _patch_env(
        monkeypatch, has_termios=True, isatty=True, win_console_supported=False,
        fileno_raises=True,
    )
    assert us_mod._interactive_stdin() is False


# ── 2. Windows 控制台下 execute() 打开弹窗 ──────────────────

@pytest.mark.asyncio
async def test_execute_opens_popup_on_native_windows(monkeypatch):
    """原生 Windows 控制台：execute 打开弹窗并等待交互（不再直接回退）。

    轮询 sleep 中观测弹窗已挂起（``model.user_selects`` 长度 1、
    ``bottom_view="user_select"``），随后模拟组件确认 → 工具返回
    confirmed 且清理弹窗。
    """
    from src.tools.user_select import UserSelectFunc

    us_mod, model, fake = _patch_env(
        monkeypatch, has_termios=False, isatty=True, win_console_supported=True,
    )
    observed = {}

    async def fake_sleep(_sec):
        # 首次轮询：弹窗应已挂起（修复前这里只会是 non_interactive 直接返回）
        observed["queue_len"] = len(model.user_selects)
        observed["bottom_view"] = model.bottom_view
        observed["state_title"] = (
            model.user_selects[0].title if model.user_selects else ""
        )
        if model.user_selects and not model.user_selects[0].done:
            model.user_selects[0].try_set_final("confirmed", ["城市 A"])

    monkeypatch.setattr(us_mod.asyncio, "sleep", fake_sleep)

    result_json = await UserSelectFunc(
        title="选择城市", options=["城市 A", "城市 B"], timeout=120,
    ).execute()
    result = json.loads(result_json)

    assert observed["queue_len"] == 1, "Windows 控制台下应追加弹窗状态（弹窗显示）"
    assert observed["bottom_view"] == "user_select", "底部视图应切换为弹窗"
    assert observed["state_title"] == "选择城市"
    assert result == {"selected": ["城市 A"], "action": "confirmed"}
    # 弹窗关闭（全部完成）：队列清空 + bottom_view 复位
    assert model.user_selects == []
    assert model.bottom_view == ""
    assert fake.redraw_calls >= 1


@pytest.mark.asyncio
async def test_execute_falls_back_without_interactive_input(monkeypatch):
    """无 TTY 且无 Windows 控制台：仍回退 non_interactive（保持原语义）。"""
    from src.tools.user_select import UserSelectFunc

    us_mod, model, _fake = _patch_env(
        monkeypatch, has_termios=True, isatty=False, win_console_supported=False,
    )

    async def _fail_sleep(_sec):  # pragma: no cover - 不应进入轮询
        raise AssertionError("非交互环境不应打开弹窗轮询")

    monkeypatch.setattr(us_mod.asyncio, "sleep", _fail_sleep)

    result = json.loads(await UserSelectFunc(
        title="选择城市", options=["城市 A", "城市 B"],
        default_options=["城市 B"],
    ).execute())
    assert result == {"selected": ["城市 B"], "action": "non_interactive"}
    assert model.user_selects == []
    assert model.bottom_view == ""


@pytest.mark.asyncio
async def test_execute_falls_back_on_windows_without_console(monkeypatch):
    """Windows 非控制台（如服务/重定向）：回退 non_interactive。"""
    from src.tools.user_select import UserSelectFunc

    us_mod, model, _fake = _patch_env(
        monkeypatch, has_termios=False, isatty=False, win_console_supported=False,
    )

    async def _fail_sleep(_sec):  # pragma: no cover - 不应进入轮询
        raise AssertionError("非控制台环境不应打开弹窗轮询")

    monkeypatch.setattr(us_mod.asyncio, "sleep", _fail_sleep)

    result = json.loads(await UserSelectFunc(
        title="选择城市", options=["城市 A", "城市 B"],
    ).execute())
    assert result["action"] == "non_interactive"
    assert model.user_selects == []
