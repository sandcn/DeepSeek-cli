"""输入框拖放文件路径（用户需求 2026-10-07）集成测试。

终端把拖入的文件以「粘贴」形式注入 stdin（本项目未启用 bracketed paste，
整段突发字符走 ``try_read_paste`` → ``InputDispatcher._insert_pasted_text``）。
本文件覆盖：

  - 单/双引号包裹、反斜杠转义的路径 → 去引号 + 双引号规范化插入；
  - file:// URI 与 Windows 原生路径（Cygwin/MSYS 下转 POSIX）；
  - 多文件 → 每行一个；
  - 非路径文本（自然语言）原样插入（零行为变化）；
  - 开关关闭（``set_drop_path_normalize(False)``）→ 一律原样插入；
  - 规范化异常 → 回退原样插入（不阻断输入）。
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_parser import InputParser


def _make_dispatcher(pipe_r: int):
    """构造真实 InputDispatcher（pipe fd 模拟 stdin）。"""
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


def _drain_all(dispatcher: InputDispatcher, rounds: int = 6, gap: float = 0.03):
    """模拟渲染线程逐帧 process_events（含后续 pending 轮次）。"""
    for _ in range(rounds):
        time.sleep(gap)
        dispatcher.process_events()


# ── 1. 基本形态 ─────────────────────────────────────────

def test_single_quoted_dropped_file(tmp_path):
    """单引号包裹的含空格路径 → 去引号 + 双引号输出。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, ("'%s'" % f).encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"%s"' % f
    assert be.get_queued_input() is None
    os.close(w)
    os.close(r)


def test_double_quoted_dropped_file(tmp_path):
    """双引号包裹（Windows Terminal 形态）→ 统一双引号输出。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, ('"%s"' % f).encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"%s"' % f
    os.close(w)
    os.close(r)


def test_backslash_escaped_dropped_file(tmp_path):
    """mintty 默认反斜杠转义 → 还原空格 + 双引号输出。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    escaped = str(f).replace(" ", "\\ ")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, escaped.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"%s"' % f
    os.close(w)
    os.close(r)


def test_multiple_dropped_files_each_line(tmp_path):
    """多文件拖入 → 每个路径一行（含空格者双引号包裹）。"""
    f1 = tmp_path / "a b.txt"
    f2 = tmp_path / "c.txt"
    f1.write_text("x", encoding="utf-8")
    f2.write_text("y", encoding="utf-8")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, ("'%s' %s" % (f1, f2)).encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"%s"\n%s' % (f1, f2)
    os.close(w)
    os.close(r)


def test_file_uri_dropped_file(tmp_path):
    """file:// URI（VS Code/Web 终端形态，含 %20）→ 本地路径。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    uri = "file://" + str(f).replace(" ", "%20")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, uri.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"%s"' % f
    os.close(w)
    os.close(r)


@pytest.mark.skipif(
    not sys.platform.startswith(("cygwin", "msys")),
    reason="Windows 路径 → POSIX 仅在 Cygwin/MSYS 转换",
)
def test_windows_path_converted_on_cygwin():
    """Windows 原生路径（资源管理器拖入）→ POSIX 路径。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, '"C:\\Users\\me\\a b.txt"'.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '"/cygdrive/c/Users/me/a b.txt"'
    os.close(w)
    os.close(r)


# ── 2. 与既有输入内容 / Enter 的协作 ─────────────────────

def test_dropped_path_inserted_after_typed_text(tmp_path):
    """已输入文本 + 拖放路径 → 追加插入（不覆盖既有内容）。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, "看下 ".encode("utf-8"))
    _drain_all(d)
    os.write(w, ("'%s'" % f).encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == '看下 "%s"' % f
    os.close(w)
    os.close(r)


def test_dropped_path_then_enter_submits(tmp_path):
    """拖放路径后按回车 → 提交规范化后的路径（Enter 被正常分发）。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, ("'%s'\r" % f).encode("utf-8"))
    _drain_all(d)

    assert be.get_queued_input() == '"%s"' % f
    assert be.get_current_text() == ""
    os.close(w)
    os.close(r)


# ── 3. 零回归：非路径文本 / 开关关闭 / 异常回退 ───────────

def test_natural_language_paste_unchanged(tmp_path):
    """自然语言粘贴（含路径但不整体是路径）→ 原样插入。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    payload = "分析 %s 的内容" % f
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, payload.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == payload
    os.close(w)
    os.close(r)


def test_code_paste_unchanged():
    """代码粘贴 → 原样插入（不被误判为路径列表）。"""
    payload = "def f():\n    return 1"
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, payload.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == payload
    os.close(w)
    os.close(r)


def test_disabled_keeps_raw_text(tmp_path):
    """开关关闭 → 拖放文本原样插入（含引号，零行为变化）。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    raw = "'%s'" % f
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    d.set_drop_path_normalize(False)
    os.write(w, raw.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == raw
    os.close(w)
    os.close(r)


def test_normalize_exception_falls_back(tmp_path, monkeypatch):
    """规范化抛异常 → 回退原样插入（不阻断输入）。"""
    import src.tui._input_dispatcher as mod

    def _boom(_text):
        raise RuntimeError("boom")

    monkeypatch.setattr(mod, "normalize_dropped_paths", _boom)
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    raw = "'%s'" % f
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, raw.encode("utf-8"))
    _drain_all(d)

    assert be.get_current_text() == raw
    os.close(w)
    os.close(r)


# ── 4. 渲染链路：拖放路径在输入框可见 ─────────────────────

def _render_input_area(text: str, width: int = 100) -> str:
    """真实 Ink 渲染 InputArea，返回整帧纯文本。"""
    from src.tui.ink import components as _components
    from src.tui.ink.element import h
    from src.tui.ink.layout import layout_tree
    from src.tui.ink.reconciler import Reconciler
    from src.tui.app.input_area import InputArea

    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    props = {
        "text": text, "cursor_pos": len(text), "prompt": "> ",
        "completion": None, "status_active": False, "cpu": 0, "mem": 0,
        "width": width, "history_search": None,
        "bg_bash_count": 0, "bg_subagent_count": 0,
    }
    rec.render(root, h(InputArea, props), width, 40)
    layout_tree(root, width)
    frame = _components.render_frame(root, width)
    return "\n".join("".join(r.text for r in ln.runs) for ln in frame.lines)


def test_dropped_path_visible_in_rendered_input_area(tmp_path):
    """拖放规范化后的路径在输入框渲染中可见（单文件引号形态）。"""
    f = tmp_path / "a b.txt"
    f.write_text("x", encoding="utf-8")
    rendered = _render_input_area('"%s"' % f)
    assert ('"%s"' % f) in rendered


def test_multiple_dropped_paths_render_two_lines(tmp_path):
    """多文件拖放 → 渲染为两行（每行一个路径）。"""
    f1 = tmp_path / "a b.txt"
    f2 = tmp_path / "c.txt"
    f1.write_text("x", encoding="utf-8")
    f2.write_text("y", encoding="utf-8")
    text = '"%s"\n%s' % (f1, f2)
    rendered = _render_input_area(text)
    lines = [ln for ln in rendered.split("\n") if str(f1) in ln or str(f2) in ln]
    assert len(lines) == 2


# ── 5. 开关访问器 ───────────────────────────────────────
def test_switch_accessor_default_on():
    r, w = os.pipe()
    d, _be = _make_dispatcher(r)
    assert d.get_drop_path_normalize() is True
    d.set_drop_path_normalize(False)
    assert d.get_drop_path_normalize() is False
    d.set_drop_path_normalize(True)
    assert d.get_drop_path_normalize() is True
    os.close(w)
    os.close(r)


def test_input_facade_delegates():
    """Input 外观委托设置开关（装配注入路径）。"""
    import src.tui._input as input_mod

    inp = input_mod.Input(fd=-1, history_file=Path("/dev/null"))
    inp.set_drop_path_normalize(False)
    assert inp._dispatcher.get_drop_path_normalize() is False
    inp.set_drop_path_normalize(True)
    assert inp._dispatcher.get_drop_path_normalize() is True


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
