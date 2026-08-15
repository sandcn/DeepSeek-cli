"""InkRenderer 光标定位 / 清残留防御测试（M2 + M3）。

修复背景（2026-08-15）：
  - M2（place_cursor 负 offset 钳制）：底部对齐 + 文档偏下
    （``_buf_h <= _height`` 且 ``doc_h+1 < _buf_h``）时 ``_effective_offset``
    为负，``target = row - offset = row + |offset|`` 偏大——row > doc_h+1
    （补全弹窗/多行输入高度与帧行数时序不一致等状态组合）时 target 超过
    物理缓冲末行，直接 ``_clamp`` 把可达输入行钳到屏幕底部空白区（越界
    方向：offset 负 → target 偏大 → >height 钳到底；<1 钳到顶不发生）。
    修复：负 offset 时目标行额外按物理缓冲边界钳制（``[1, _buf_h]``），
    随后 ``_clamp`` 保证 [1, height]；正常路径（row <= doc_h+1）target <=
    ``_buf_h``，本钳制不生效（零回归）。
  - M3（缩短清残留越底滚动兜底）：常规缩短 ``delta<0`` 清残留循环
    ``clear_line()+cursor_down(1)`` 次数 = ``prev_h-new_h``，差异行数超过
    可见区高度时 cursor_down 越过屏幕底部触发滚动。修复：循环内到底部
    （``current_row >= height``）后只清当前行不再 cursor_down（不可达残留
    行跳过，物理缓冲 ``_buf_h`` 已精确跟踪）；height=0 无约束守卫不生效。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _mkframe(n: int, prefix: str = "l") -> Frame:
    """构造 n 行帧（每行独立 Line 对象，内容可区分）。"""
    return Frame([Line.of(f"{prefix}{i}") for i in range(n)])


# ── M2：place_cursor 负 offset 目标行钳制 ──────────────────

def _m2_renderer(doc_h: int = 3, buf_h: int = 9, height: int = 10):
    """构造 M2 场景渲染器：底部对齐 + 物理缓冲在屏幕内 + 文档偏下。

    ``_buf_h=9 <= height=10``、``_top_aligned=False``（底部对齐）、
    ``doc_h=3``（``doc_h+1=4 < buf_h=9``）→ ``_effective_offset = -5``。
    """
    r = InkRenderer(stream=io.StringIO(), height=height)
    r._prev = _mkframe(doc_h)
    r._buf_h = buf_h
    r._top_aligned = False
    r._cursor_row = 1
    return r


def test_place_cursor_negative_offset_input_row_regression():
    """M2 正常路径：输入行（row=doc_h+1=4）落在物理缓冲末行（屏幕行 9，
    非屏幕底 10）——负 offset 下 target 恰好 = buf_h，钳制不改变行为。"""
    r = _m2_renderer()
    out = io.StringIO()
    r._stream = out
    r.place_cursor(4, 1)
    # offset=-5 → target = 4-(-5) = 9 = buf_h（物理缓冲末行）
    assert r.cursor_row == 9
    assert out.getvalue() == "\x1b[8B\r"  # 从行 1 下移到行 9


def test_place_cursor_negative_offset_over_buf_clamp_regression():
    """M2 越界防御：row=6 > doc_h+1（异常状态组合）时目标行钳到物理缓冲
    边界（buf_h=9）而非屏幕底部（height=10）。

    修复前 target = _clamp(6-(-5)) = _clamp(11) = 10（屏幕底空白区）；
    修复后 target = min(11, buf_h=9) = 9（物理缓冲末行，文档内可达位置）。
    """
    r = _m2_renderer()
    out = io.StringIO()
    r._stream = out
    r.place_cursor(6, 1)
    assert r.cursor_row == 9  # 物理缓冲末行（而非屏幕底 10）
    assert out.getvalue() == "\x1b[8B\r"  # 从行 1 下移到行 9（而非行 10）


def test_place_cursor_positive_offset_regression():
    """M2 回归：正 offset（文档高于屏幕，顶部对齐）行为不变——
    输入行（row=doc_h+1=13）target = 13 - 3 = 10（可见区底）。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    r._prev = _mkframe(12)
    r._buf_h = 13
    r._top_aligned = True
    r._cursor_row = 1
    out = io.StringIO()
    r._stream = out
    r.place_cursor(13, 1)
    assert r._effective_offset(12) == 3  # max(0, 13-10)
    assert r.cursor_row == 10
    assert out.getvalue() == "\x1b[9B\r"  # 从行 1 下移到行 10


def test_place_cursor_no_constraint_regression():
    """M2 回归：height=0（无约束）原样返回（文档坐标即屏幕坐标）。"""
    r = InkRenderer(stream=io.StringIO(), height=0)
    r._prev = _mkframe(3)
    r._buf_h = 4
    r._top_aligned = True
    r._cursor_row = 1
    out = io.StringIO()
    r._stream = out
    r.place_cursor(3, 1)
    assert r.cursor_row == 3
    assert out.getvalue() == "\x1b[2B\r"  # 从行 1 下移到行 3


# ── M3：缩短清残留越底滚动兜底 ────────────────────────────

def test_shorten_clear_residual_normal_diff_regression():
    """M3 回归：常规缩短（差异行数 < 可见区高度）输出不变——清残留
    clear_line+cursor_down 各 prev_h-new_h=6 对，光标最终在文档底部。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    r.render(_mkframe(8))
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(2))
    text = out.getvalue()
    # 8→2 常规缩短（文档在屏幕内、顶部对齐，走常规 diff 路径）：
    # 差异 6 行清残留 = 6 对 clear_line（\r\x1b[K）+ cursor_down（\x1b[1B）
    assert text.count("\r\x1b[K") == 6
    assert text.count("\x1b[1B") == 6
    # 光标最终在 prev 文档底部（屏幕行 9 = prev_h+1）
    assert r.cursor_row == 9
    assert r._buf_h == 9  # 物理缓冲保持（清行不删行）


def test_shorten_clear_residual_beyond_viewport_regression():
    """M3 越底兜底：差异行数超过可见区高度时清残留路径不越过屏幕底部
    （无越底 cursor_down，不清屏不滚动）。

    构造 12 行（高于屏幕）→ 2 行（缩短进入屏幕内），走漂移物理映射路径
    （``_rewrite_drifted``，自底向上 cursor_up 定位 + 写行，不写 ``\n``）——
    断言输出中无越底 cursor_down（0 次），物理缓冲保持、光标在屏幕底。
    """
    r = InkRenderer(stream=io.StringIO(), height=10)
    r.render(_mkframe(12))
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(2))
    text = out.getvalue()
    # 漂移物理映射路径不写 cursor_down（越底滚动由 \n 驱动——本路径无 \n）
    assert text.count("\r\x1b[K") > 0  # 有清行输出
    assert "\x1b[1B" not in text  # 无 cursor_down（不越底）
    # 状态一致：物理缓冲保持（清行不删行）、光标在屏幕底
    assert r._buf_h == 13
    assert r.cursor_row == 10
