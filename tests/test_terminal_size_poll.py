"""终端尺寸主动轮询兜底（SIGWINCH 缺失时宽度跟随窗口）回归测试。

背景（2026-10-05 用户需求：工具卡要达到终端宽度，终端变宽时随之增加）：
部分终端/平台窗口 resize **不发送 SIGWINCH**（Cygwin pty 实测 winsize 变化
不触发信号），``TerminalWidthCache`` 默认 TTL 60s 内宽度陈旧——工具卡/布局
宽度长时间不随窗口变化。修复：
  1. ``TerminalWidthCache.poll(max_age)`` — 陈旧超过 max_age 秒时重探尺寸，
     返回宽/高是否变化；``_override``（render() 显式尺寸）时不探测；
  2. ``InkSession._poll_terminal_size`` — 渲染帧每帧调用（按
     ``_SIZE_POLL_INTERVAL`` 节流），无需 SIGWINCH 也能在 ~0.2s 内感知
     终端尺寸变化；变化经既有 ``width != _last_render_width`` 分支触发
     reflow + 全量重绘，工具卡宽度随之跟随。
"""

from __future__ import annotations

import io
from types import SimpleNamespace

import pytest

import src.tui._screen as screen_mod
from src.tui._screen import TerminalWidthCache
from src.tui.ink import hooks as H
from src.tui.ink._session_frame_mixin import _SIZE_POLL_INTERVAL
from src.tui.ink.session import InkSession


@pytest.fixture(autouse=True)
def _isolate_hook_globals():
    """保存/还原 hooks 全局状态（InkSession.__init__ 会写入全局注入）。"""
    saved = {
        "_input_router_callback": H._input_router_callback,
        "_app_control": H._app_control,
        "_stdin_accessor": H._stdin_accessor,
        "_stdout_accessor": H._stdout_accessor,
        "_stderr_accessor": H._stderr_accessor,
    }
    yield
    for name, value in saved.items():
        setattr(H, name, value)


# ── 1. TerminalWidthCache.poll 语义 ──────────────────────

class TestTerminalWidthCachePoll:
    def test_fresh_cache_not_polled(self, monkeypatch):
        """刚构造（未超过 max_age）时不重探，返回 False。"""
        monkeypatch.setattr(screen_mod, "_get_terminal_size", lambda: (100, 30))
        cache = TerminalWidthCache(ttl=60.0)
        assert cache.poll(0.2) is False
        assert cache.get_width() == 100

    def test_stale_change_detected(self, monkeypatch):
        """陈旧后重探，尺寸变化返回 True 且缓存更新。"""
        sizes = {"v": (100, 30)}
        monkeypatch.setattr(screen_mod, "_get_terminal_size", lambda: sizes["v"])
        cache = TerminalWidthCache(ttl=60.0)
        # 模拟距上次刷新已超过 max_age
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        sizes["v"] = (160, 40)
        assert cache.poll(0.2) is True
        assert cache.get_width() == 160
        assert cache.get_height() == 40

    def test_stale_unchanged_returns_false(self, monkeypatch):
        """陈旧但尺寸未变时返回 False（无变化信号）。"""
        monkeypatch.setattr(screen_mod, "_get_terminal_size", lambda: (100, 30))
        cache = TerminalWidthCache(ttl=60.0)
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        assert cache.poll(0.2) is False

    def test_height_only_change_detected(self, monkeypatch):
        """仅高度变化也返回 True（resize 只改高度场景）。"""
        sizes = {"v": (100, 30)}
        monkeypatch.setattr(screen_mod, "_get_terminal_size", lambda: sizes["v"])
        cache = TerminalWidthCache(ttl=60.0)
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        sizes["v"] = (100, 50)
        assert cache.poll(0.2) is True
        assert cache.get_height() == 50

    def test_override_skips_probe(self, monkeypatch):
        """显式尺寸覆盖（render() width/height）时不探测真实终端。"""
        calls = []

        def _size():
            calls.append(1)
            return (999, 999)

        monkeypatch.setattr(screen_mod, "_get_terminal_size", _size)
        cache = TerminalWidthCache(ttl=60.0)
        cache.set_dimensions(width=88, height=20)
        n = len(calls)
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        assert cache.poll(0.2) is False
        assert len(calls) == n  # 未探测
        assert cache.get_width() == 88


# ── 2. InkSession._poll_terminal_size 节流 ───────────────

class TestSessionSizePoll:
    def _session(self, cache):
        return InkSession(
            model=SimpleNamespace(width=0),
            stream=io.StringIO(),
            width_cache=cache,
        )

    def test_throttled_to_interval(self, monkeypatch):
        """同一间隔内多次调用只探测一次。"""
        cache = TerminalWidthCache(ttl=60.0)
        session = self._session(cache)
        calls = []
        monkeypatch.setattr(cache, "poll", lambda age: calls.append(age) or False)
        session._last_size_poll = 0.0
        session._poll_terminal_size()
        session._poll_terminal_size()
        assert len(calls) == 1
        assert calls[0] == _SIZE_POLL_INTERVAL
        # 节流时间戳推进到"过期"后再调用 → 再次探测
        session._last_size_poll = 0.0
        session._poll_terminal_size()
        assert len(calls) == 2

    def test_poll_exception_swallowed(self, monkeypatch):
        """poll 异常不抛出（渲染帧不中断）。"""
        cache = TerminalWidthCache(ttl=60.0)

        def _boom(age):
            raise RuntimeError("poll boom")

        monkeypatch.setattr(cache, "poll", _boom)
        session = self._session(cache)
        session._last_size_poll = 0.0
        session._poll_terminal_size()  # 不抛异常


# ── 3. 端到端：无 SIGWINCH 时渲染帧宽度跟随窗口 ──────────

class TestRenderFrameFollowsResizeWithoutSigwinch:
    def test_render_frame_uses_new_width(self, monkeypatch):
        """SIGWINCH 未送达：尺寸变化后渲染帧使用新宽度（工具卡行满宽跟随）。"""
        from src.tui.app.app import build_app_element
        from src.tui.app.model import AppModel

        sizes = {"v": (80, 24)}
        monkeypatch.setattr(
            screen_mod, "_get_terminal_size", lambda: sizes["v"],
        )
        cache = TerminalWidthCache(ttl=60.0)  # 初始 80
        model = AppModel()
        model.open_tool_box(
            "t", "bash",
            "cd /home/lmy/simple/mc/src && timeout 600 make SYSTEM=WIN",
        )
        session = InkSession(
            model=model,
            build_tree=build_app_element,
            width_cache=cache,
            stream=io.StringIO(),
        )
        captured = []
        orig_render = session._ink_renderer.render

        def _cap(frame):
            captured.append(frame)
            return orig_render(frame)

        monkeypatch.setattr(session._ink_renderer, "render", _cap)

        # 第一帧（尺寸 80）
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.width == 80
        title80 = next(
            (ln for ln in captured[-1].lines if "Bash" in ln.plain), None,
        )
        assert title80 is not None
        assert title80.width == 80

        # 模拟窗口变宽（无 SIGWINCH），并推进轮询时间
        sizes["v"] = (160, 40)
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.width == 160
        title160 = next(
            (ln for ln in captured[-1].lines if "Bash" in ln.plain), None,
        )
        assert title160 is not None
        assert title160.width == 160
        assert all(ln.width <= 160 for ln in captured[-1].lines)

    def test_committed_tool_card_follows_resize_without_sigwinch(
        self, monkeypatch,
    ):
        """已提交工具卡在无 SIGWINCH 时也随窗口加宽（reflow 重排）。"""
        from src.tui.app.app import build_app_element
        from src.tui.app.model import AppModel

        sizes = {"v": (80, 24)}
        monkeypatch.setattr(
            screen_mod, "_get_terminal_size", lambda: sizes["v"],
        )
        cache = TerminalWidthCache(ttl=60.0)
        model = AppModel()
        model.open_tool_box("t", "bash", "ls")
        model.append_tool_output("t", "out")
        model.close_tool_box("t", True)
        session = InkSession(
            model=model,
            build_tree=build_app_element,
            width_cache=cache,
            stream=io.StringIO(),
        )
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.committed_lines[0].width == 80

        sizes["v"] = (160, 40)
        cache._last_width_fetch = 0.0
        cache._last_height_fetch = 0.0
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.committed_lines[0].width == 160
        assert all(ln.width <= 160 for ln in model.committed_lines)

    def test_render_frame_keeps_width_when_unchanged(self, monkeypatch):
        """尺寸未变时轮询不产生宽度抖动。"""
        from src.tui.app.app import build_app_element
        from src.tui.app.model import AppModel

        monkeypatch.setattr(
            screen_mod, "_get_terminal_size", lambda: (120, 30),
        )
        cache = TerminalWidthCache(ttl=60.0)
        model = AppModel()
        session = InkSession(
            model=model,
            build_tree=build_app_element,
            width_cache=cache,
            stream=io.StringIO(),
        )
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.width == 120
        cache._last_width_fetch = 0.0
        session._last_size_poll = 0.0
        session._render_frame()
        assert model.width == 120
