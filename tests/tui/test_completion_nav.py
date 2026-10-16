"""src/tui/_completion_nav — _CompletionNavHandler 补全导航策略单元测试。

覆盖：
  - handle_tab：回调补全 / 回调异常 / 结果未变不写缓冲 / 无回调插制表符
  - handle_arrow_up/down：导航回调 / 回退历史浏览 / 异常兜底
  - handle_page_nav / handle_shift_tab_reverse：页步进 / 反向循环
  - handle_editmsg_tab：只循环不高亮写入
  - dismiss_completion / maybe_dismiss_completion（_suppress_enter 抑制）
  - trigger_auto_completion：自动补全回调
"""

from __future__ import annotations

import pytest

from src.tui._completion_nav import _CompletionNavHandler


class _FakeBufferEditor:
    """记录缓冲操作的假编辑器。"""

    def __init__(self, text="current"):
        self.text = text
        self.set_calls = []
        self.echo_calls = []
        self.up_calls = 0
        self.down_calls = 0
        self.tab_calls = 0

    def get_current_text(self):
        return self.text

    def set_buffer(self, text):
        self.set_calls.append(text)
        self.text = text

    def _echo(self, text):
        self.echo_calls.append(text)

    def _up(self):
        self.up_calls += 1

    def _down(self):
        self.down_calls += 1

    def handle_char(self, ch):
        if ch == "\t":
            self.tab_calls += 1
        else:
            self.text += ch


class _FakeDispatcher:
    """宿主 dispatcher 桩：回调可注入/替换。"""

    def __init__(self, editor):
        self._buffer_editor = editor
        self._completion_callback = None
        self._completion_navigate_callback = None
        self._dismiss_completion_callback = None
        self._auto_completion_callback = None
        self._suppress = False
        self.dismiss_calls = 0

    def get_suppress_enter(self):
        return self._suppress

    def _dismiss_completion(self):
        self.dismiss_calls += 1


@pytest.fixture
def nav():
    editor = _FakeBufferEditor()
    d = _FakeDispatcher(editor)
    return _CompletionNavHandler(d), d, editor


# ── handle_tab ───────────────────────────────────────────

def test_tab_without_callback_inserts_tab(nav):
    h, d, editor = nav
    h.handle_tab()
    assert editor.tab_calls == 1


def test_tab_callback_returns_text_unchanged(nav):
    """结果等于原文本 → 不写缓冲（修复：首次 Tab 不清除提交状态）。"""
    h, d, editor = nav
    d._completion_callback = lambda text: text  # 返回原样
    h.handle_tab()
    assert editor.set_calls == []
    assert editor.tab_calls == 0


def test_tab_callback_returns_new_text(nav):
    h, d, editor = nav
    d._completion_callback = lambda text: "completed!"
    d._auto_completion_callback = lambda text: None
    h.handle_tab()
    assert editor.set_calls == ["completed!"]
    assert editor.echo_calls == ["completed!"]


def test_tab_callback_returns_none_inserts_tab(nav):
    h, d, editor = nav
    d._completion_callback = lambda text: None
    h.handle_tab()
    assert editor.tab_calls == 1
    assert editor.set_calls == []


def test_tab_callback_raises_inserts_tab(nav):
    h, d, editor = nav

    def boom(text):
        raise RuntimeError("cb failed")

    d._completion_callback = boom
    h.handle_tab()
    assert editor.tab_calls == 1  # 异常兜底插入制表符


# ── handle_arrow_up / down ───────────────────────────────

def test_arrow_up_navigates(nav):
    h, d, editor = nav
    seen = []
    d._completion_navigate_callback = lambda delta, text: seen.append(delta) or "nav-up"
    d._auto_completion_callback = lambda text: None
    h.handle_arrow_up()
    assert seen == [-1]
    assert editor.set_calls == ["nav-up"]


def test_arrow_up_falls_back_to_history(nav):
    """无导航回调 → 历史浏览 _up。"""
    h, d, editor = nav
    h.handle_arrow_up()
    assert editor.up_calls == 1


def test_arrow_up_callback_no_change(nav):
    """导航回调返回原文本 → 不写缓冲。"""
    h, d, editor = nav
    d._completion_navigate_callback = lambda delta, text: text
    h.handle_arrow_up()
    assert editor.set_calls == []
    assert editor.up_calls == 0


def test_arrow_up_callback_raises_falls_back(nav):
    h, d, editor = nav

    def boom(delta, text):
        raise RuntimeError("nav failed")

    d._completion_navigate_callback = boom
    h.handle_arrow_up()
    assert editor.up_calls == 1  # 异常 → 历史浏览


def test_arrow_down_navigates(nav):
    h, d, editor = nav
    seen = []
    d._completion_navigate_callback = lambda delta, text: seen.append(delta) or "nav-down"
    h.handle_arrow_down()
    assert seen == [1]
    assert editor.set_calls == ["nav-down"]


def test_arrow_down_falls_back_to_history(nav):
    h, d, editor = nav
    h.handle_arrow_down()
    assert editor.down_calls == 1


# ── 翻页 / Shift+Tab ─────────────────────────────────────

def test_page_nav_steps_by_page(nav):
    h, d, editor = nav
    seen = []
    d._completion_navigate_callback = lambda delta, text: seen.append(delta) or "paged"
    h.handle_page_nav(1)
    assert seen == [1]  # 页面步进由调用方传入（通常 ±5）


def test_page_nav_noop_without_callback(nav):
    h, d, editor = nav
    h.handle_page_nav(1)
    assert editor.set_calls == []


def test_page_nav_unchanged_noop(nav):
    h, d, editor = nav
    d._completion_navigate_callback = lambda delta, text: text
    h.handle_page_nav(-1)
    assert editor.set_calls == []


def test_shift_tab_reverse(nav):
    h, d, editor = nav
    seen = []
    d._completion_navigate_callback = lambda delta, text: seen.append(delta) or "rev"
    h.handle_shift_tab_reverse()
    assert seen == [-1]
    assert editor.set_calls == ["rev"]


def test_shift_tab_noop_without_callback(nav):
    h, d, editor = nav
    h.handle_shift_tab_reverse()
    assert editor.set_calls == []
    assert editor.tab_calls == 0  # 不插入制表符


def test_editmsg_tab_cycles_without_write(nav):
    h, d, editor = nav
    seen = []
    d._completion_navigate_callback = lambda delta, text: seen.append(delta) or text
    h.handle_editmsg_tab()
    assert seen == [1]
    assert editor.set_calls == []  # 不写缓冲


def test_editmsg_tab_without_callback_noop(nav):
    h, d, editor = nav
    h.handle_editmsg_tab()
    assert editor.set_calls == []


# ── dismiss / auto completion ────────────────────────────

def test_dismiss_completion_calls_callback(nav):
    h, d, editor = nav
    called = []
    d._dismiss_completion_callback = lambda: called.append(1)
    h.dismiss_completion()
    assert called == [1]


def test_dismiss_completion_exception_silent(nav):
    h, d, editor = nav

    def boom():
        raise RuntimeError("dismiss failed")

    d._dismiss_completion_callback = boom
    h.dismiss_completion()  # 不抛异常


def test_maybe_dismiss_completion_calls_host(nav):
    h, d, editor = nav
    h.maybe_dismiss_completion()
    assert d.dismiss_calls == 1


def test_maybe_dismiss_suppressed_by_editmsg(nav):
    h, d, editor = nav
    d._suppress = True  # editmsg 选择期间
    h.maybe_dismiss_completion()
    assert d.dismiss_calls == 0


def test_trigger_auto_completion(nav):
    h, d, editor = nav
    seen = []
    d._auto_completion_callback = lambda text: seen.append(text)
    h.trigger_auto_completion()
    assert seen == ["current"]


def test_trigger_auto_completion_no_callback(nav):
    h, d, editor = nav
    h.trigger_auto_completion()  # no-op


def test_trigger_auto_completion_exception_silent(nav):
    h, d, editor = nav

    def boom(text):
        raise RuntimeError("auto failed")

    d._auto_completion_callback = boom
    h.trigger_auto_completion()  # 不抛异常
