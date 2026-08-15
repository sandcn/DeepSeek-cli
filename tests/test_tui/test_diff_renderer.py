"""diff 渲染出口按终端宽度截断测试（H3）。

修复背景（2026-08-15 H3）：``_write_diff_line`` 出口无截断，ctx/add/del
长行在窄终端 wraparound。修复：``render_diff``/``_render_diff_summary``
新增 ``max_width`` 参数（None=不截断，保持旧调用/``render_diff_to_ansi``
纯函数行为），经 ``ansi_to_line`` + ``truncate_line`` 组合在出口截断；
``show_file_diff``（终端场景）经 ``TerminalWidthCache`` 取终端宽度传入。

本测试锁定：max_width 截断生效且 ANSI 合法、默认不传 max_width 兼容
（输出与 ``render_diff_to_ansi`` 基线一致）、行号列/增删标记保留。
"""

from __future__ import annotations

import difflib

from src.tui._diff_renderer import (
    render_diff,
    render_diff_to_ansi,
    _render_diff_summary,
    _write_diff_line,
)
from src.renderer.ansi.helpers import visual_width, strip_ansi


def _make_diff() -> list:
    """构造含长 add/del/ctx 行的 unified diff 列表。"""
    old = "line1\n" + "x" * 60 + "\nline3\nline4\nline5\nline6\n"
    new = "line1\n" + "y" * 60 + "\nline3\nline4\nline5\nline6\n"
    return list(difflib.unified_diff(
        old.splitlines(), new.splitlines(),
        fromfile="a/f.py", tofile="b/f.py",
        lineterm="", n=3,
    ))


class _Collector:
    """收集 write_line 调用的简单输出目标（与 render_diff_to_ansi 同型）。"""

    _target: list = []

    @classmethod
    def write_line(cls, text: str) -> None:
        cls._target.append(text)


def test_render_diff_max_width_truncates_regression():
    """H3 传 max_width=30：每行宽 <= 30，且行号列/增删标记保留。"""
    diff_list = _make_diff()
    collected: list = []
    _Collector._target = collected
    render_diff(diff_list, 1, lexer_name="", output_target=_Collector, width=40, max_width=30)
    _render_diff_summary(diff_list, output_target=_Collector, width=40, max_width=30)
    assert collected, "应有输出行"
    for line in collected:
        assert visual_width(strip_ansi(line)) <= 30, f"超宽 {visual_width(strip_ansi(line))}: {line!r}"
    # 增删行结构保留：行号列（│）与 -/+ 标记仍存在（截断在行尾，前缀不受影响）
    text = "\n".join(strip_ansi(l) for l in collected)
    assert "│" in text, "行号列丢失"
    assert "-" in text and "+" in text, "增删标记丢失"


def test_render_diff_default_no_truncate_regression():
    """H3 不传 max_width：输出与 render_diff_to_ansi 基线一致（未截断）。"""
    old = "line1\n" + "x" * 60 + "\nline3\nline4\nline5\nline6\n"
    new = "line1\n" + "y" * 60 + "\nline3\nline4\nline5\nline6\n"
    diff_list = _make_diff()
    collected: list = []
    _Collector._target = collected
    # 与 render_diff_to_ansi 内部路径一致：render_diff + _render_diff_summary，
    # 且 width 用相同收缩值（sep_w = min(40, max(10, w*2))，w=1 → 10）
    sep_w = min(40, max(10, 1 * 2))
    render_diff(diff_list, 1, lexer_name="py", output_target=_Collector, width=sep_w)
    _render_diff_summary(diff_list, output_target=_Collector, width=sep_w)
    base = list(collected)
    while base and base[-1] == "":
        base.pop()  # render_diff_to_ansi 尾部移除空行，对比前同样处理
    ansi_out = render_diff_to_ansi("f.py", old, new)
    assert "\n".join(base) == ansi_out, "默认（不传 max_width）输出应与 render_diff_to_ansi 基线一致"
    # 基线含未截断长行（>30 列），证明默认无截断
    assert any(visual_width(strip_ansi(l)) > 30 for l in ansi_out.split("\n")), "默认应不截断（存在 >30 列行）"


def test_write_diff_line_truncate_ansi_regression():
    """H3 _write_diff_line 含 ANSI 长行截断后：宽度 <= 指定宽度且 ANSI 合法。"""
    collected: list = []
    _Collector._target = collected
    long_text = "\x1b[31m" + "z" * 50 + "\x1b[0m"
    _write_diff_line(long_text, _Collector, width=30)
    out = collected[0]
    assert visual_width(strip_ansi(out)) <= 30, f"截断后宽 {visual_width(strip_ansi(out))}"
    assert "\x1b" not in strip_ansi(out), "截断后无断裂 SGR（残留孤立 ESC）"


def test_write_diff_line_default_no_truncate_regression():
    """H3 _write_diff_line width=None（默认）：原样输出不截断。"""
    collected: list = []
    _Collector._target = collected
    long_text = "\x1b[31m" + "z" * 50 + "\x1b[0m"
    _write_diff_line(long_text, _Collector)
    assert collected[0] == long_text, "默认 width=None 应原样输出"
