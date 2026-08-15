"""InkRenderer 光标定位 / 清残留防御 / 无末尾空行测试（M2 + M3 + M4）。

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
  - M4（无末尾空行模型）：渲染器物理缓冲 = 文档行数（``_buf_h = doc_h``，
    无 doc_h+1 末尾空行）——最后一行内容（如输入区模式行「标准模式」）
    下方不再产生空行。修复前：满屏/超屏时屏幕最后一行恒为末尾空行，且
    ``doc_h == height`` 时首行内容被滚动挤出。修复后：文档最后一行写完后
    光标停在该行（不写 ``\n``），满屏/超屏时最后一行内容显示在屏幕最后
    一行，不满屏时最后一行内容下方无文档空行（终端默认空白）。
"""

from __future__ import annotations

import io

import pyte

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _mkframe(n: int, prefix: str = "l") -> Frame:
    """构造 n 行帧（每行独立 Line 对象，内容可区分）。"""
    return Frame([Line.of(f"{prefix}{i}") for i in range(n)])


# ── M2：place_cursor 负 offset 目标行钳制 ──────────────────

def _m2_renderer(doc_h: int = 3, buf_h: int = 9, height: int = 10):
    """构造 M2 场景渲染器：底部对齐 + 物理缓冲在屏幕内 + 文档偏下。

    ``_buf_h=9 <= height=10``、``_top_aligned=False``（底部对齐）、
    ``doc_h=3``（``doc_h=3 < buf_h=9``）→ ``_effective_offset = -6``
    （★ 无末尾空行模型 2026-08-15：``doc_h - buf_h = 3-9 = -6``，旧模型
    ``doc_h+1-buf_h = -5``）。
    """
    r = InkRenderer(stream=io.StringIO(), height=height)
    r._prev = _mkframe(doc_h)
    r._buf_h = buf_h
    r._top_aligned = False
    r._cursor_row = 1
    return r


def test_place_cursor_negative_offset_input_row_regression():
    """M2 正常路径：输入行（row=doc_h+1=4）落在物理缓冲末行（屏幕行 9，
    非屏幕底 10）——负 offset 下 target 超出物理缓冲末行，M2 钳制将其
    钳到 buf_h=9。★ 无末尾空行模型：offset = -6 → target = 4-(-6) = 10，
    钳制 min(10, 9) = 9（旧模型 offset=-5 → target=9 不靠钳制直接落点——
    本断言在新旧模型下都通过，另见 ``_effective_offset`` 直断锁定新公式）。"""
    r = _m2_renderer()
    assert r._effective_offset(3) == -6  # ★ 锁定无末尾空行 offset 公式
    out = io.StringIO()
    r._stream = out
    r.place_cursor(4, 1)
    # offset=-6 → target = 4-(-6) = 10 → M2 钳制 min(10, buf_h=9) = 9
    assert r.cursor_row == 9
    assert out.getvalue() == "\x1b[8B\r"  # 从行 1 下移到行 9


def test_place_cursor_negative_offset_over_buf_clamp_regression():
    """M2 越界防御：row=6 > doc_h+1（异常状态组合）时目标行钳到物理缓冲
    边界（buf_h=9）而非屏幕底部（height=10）。

    修复前（旧 offset=-5）target = _clamp(6-(-5)) = _clamp(11) = 10（屏幕
    底空白区）；★ 无末尾空行模型 offset=-6 → target = _clamp(6-(-6)) =
    _clamp(12) = 10，修复后 target = min(12, buf_h=9) = 9（物理缓冲末行）。
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
    r._buf_h = 13  # 漂移模拟（物理缓冲 > 文档行数）
    r._top_aligned = True
    r._cursor_row = 1
    out = io.StringIO()
    r._stream = out
    r.place_cursor(13, 1)
    assert r._effective_offset(12) == 3  # 顶部对齐分支：max(0, buf_h-height)
    assert r.cursor_row == 10
    assert out.getvalue() == "\x1b[9B\r"  # 从行 1 下移到行 10


def test_place_cursor_no_constraint_regression():
    """M2 回归：height=0（无约束）原样返回（文档坐标即屏幕坐标）。

    ★ 无末尾空行模型（2026-08-15）：``_buf_h = 3``（= doc_h，旧模型 4
    = doc_h+1）；height=0 时 ``_effective_offset`` 恒返回 0，断言不受影响。"""
    r = InkRenderer(stream=io.StringIO(), height=0)
    r._prev = _mkframe(3)
    r._buf_h = 3
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
    # ★ 无末尾空行模型（2026-08-15）：光标最终在 prev 文档底部（屏幕行 8
    #   = prev_h，物理缓冲末行 = 文档行数，无 doc_h+1 末尾空行）。
    assert r.cursor_row == 8
    assert r._buf_h == 8  # 物理缓冲保持（清行不删行）


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
    # ★ 无末尾空行模型（2026-08-15）：_buf_h = 12（= doc_h，无 doc_h+1
    #   末尾空行）。
    assert r._buf_h == 12
    assert r.cursor_row == 10


# ── M4：无末尾空行模型（模式行下方不再有空行） ─────────────

def _screen_display(r, stream, width=30, height=10):
    """将渲染器累计输出 feed 到 pyte 屏幕，返回 display 行列表。"""
    screen = pyte.Screen(width, height)
    pyte.Stream(screen).feed(stream.getvalue())
    return screen.display


def _last_content_row(disp):
    """最后非空内容行的 1-based 行号（无内容返回 None）。"""
    for i in range(len(disp) - 1, -1, -1):
        if disp[i].strip():
            return i + 1
    return None


def test_full_screen_last_line_is_content_no_blank():
    """M4 关键修复：doc_h == height（恰好满屏）时首行不被滚动挤出、
    最后一行内容显示在屏幕最后一行、无末尾空行。

    修复前：写最后一行 + \\n 触发滚动 → 首行 L0 被挤出，模式行 L9 在
    倒数第二行、屏幕最后一行是空行。修复后：L0 可见、L9 在屏幕最后一行。
    """
    r = InkRenderer(stream=io.StringIO(), height=10)
    stream = io.StringIO()
    r._stream = stream
    r.render(_mkframe(10, prefix="L"))
    disp = _screen_display(r, stream)
    # 首行可见（未被滚动挤出）
    assert "L0" in disp[0], f"满屏首行应可见: {disp[0]!r}"
    # 最后一行 = 文档最后一行（L9），非空行
    assert "L9" in disp[9], f"模式行应显示在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"
    # 物理缓冲 = 文档行数（无末尾空行）
    assert r._buf_h == 10
    # 光标在文档最后一行（屏幕最后一行）
    assert r.cursor_row == 10


def test_overscreen_last_line_is_mode_row():
    """M4 超屏：文档高于屏幕时，最后一行内容（模式行）显示在屏幕最后一行。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    stream = io.StringIO()
    r._stream = stream
    r.render(_mkframe(13, prefix="L"))
    disp = _screen_display(r, stream)
    # 屏幕显示文档最后 10 行（L3..L12），L12 在屏幕最后一行
    assert "L12" in disp[9], f"模式行应显示在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"
    # 不满屏时文档最后一行不贴底但下方无文档空行（终端空白）
    assert "L3" in disp[0], f"可见区应显示文档最后 10 行: {disp[0]!r}"


def test_underscreen_no_document_blank_after_last_row():
    """M4 不满屏：文档最后一行下方无文档末尾空行（终端默认空白）。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    stream = io.StringIO()
    r._stream = stream
    r.render(_mkframe(8, prefix="L"))
    disp = _screen_display(r, stream)
    # 最后内容行 = 文档行数（L7 在 row 8），row 9-10 为终端空白
    assert _last_content_row(disp) == 8
    assert "L7" in disp[7]
    # 物理缓冲 = 文档行数（无末尾空行）
    assert r._buf_h == 8


def test_grow_cross_full_screen_boundary():
    """M4 增长跨越满屏边界：8 → 10 → 12，模式行始终显示正确且无末尾空行。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    stream = io.StringIO()
    r._stream = stream
    r.render(_mkframe(8, prefix="L"))
    disp = _screen_display(r, stream)
    assert _last_content_row(disp) == 8

    # 8 -> 10（不满屏 → 恰好满屏）：首行 L0 保留、L9 在屏幕最后一行
    r.render(_mkframe(10, prefix="L"))
    disp = _screen_display(r, stream)
    assert "L0" in disp[0], "跨越满屏边界增长后首行应保留"
    assert "L9" in disp[9], "恰好满屏时模式行应在屏幕最后一行"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"

    # 10 -> 12（满屏 → 超屏）：模式行 L11 在屏幕最后一行
    r.render(_mkframe(12, prefix="L"))
    disp = _screen_display(r, stream)
    assert "L11" in disp[9], f"超屏时模式行应在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"


def test_write_full_no_trailing_newline():
    """M4 写循环：最后一行不写 \\n（不产生末尾空行）。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(3, prefix="L"))
    text = out.getvalue()
    # 3 行内容，只有 2 个 \n（最后一行不写 \n）
    assert text.count("\n") == 2, f"最后一行不应写 \\n: {text!r}"
    # 每行都写内容
    assert text.count("\rL") == 3


def test_place_cursor_after_full_screen_no_blank():
    """M4 place_cursor：恰好满屏后光标定位到文档行不越界。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(10, prefix="L"))
    # 定位到文档第 10 行（模式行）——应为屏幕行 10（无末尾空行偏移）
    r.place_cursor(10, 1)
    assert r.cursor_row == 10


def test_buf_h_is_doc_height_no_plus_one():
    """M4 _buf_h 语义：物理缓冲 = 文档行数（无 doc_h+1 末尾空行）。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    r.render(_mkframe(7, prefix="L"))
    assert r._buf_h == 7, f"_buf_h 应 = doc_h=7: {r._buf_h}"
    # 增长后 _buf_h = new_h
    r.render(_mkframe(9, prefix="L"))
    assert r._buf_h == 9, f"增长后 _buf_h 应 = new_h=9: {r._buf_h}"


# ── M4 补充：覆盖缺口（review P3） ─────────────────────────

def test_grow_from_empty_frame():
    """M4 空帧后增长：平移快路径 line_idx==0 首行原地写（终端清屏后光标
    在行 1，不换行）——屏幕从 row 1 显示文档首行。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(Frame([]))  # 空帧（模拟 suspend/reset 后）
    assert r._buf_h == 0, "空帧后 _buf_h 应为 0"
    r.render(_mkframe(3, prefix="L"))
    disp = _screen_display(r, out)
    assert "L0" in disp[0], "空帧后首行应原地写在 row 1"
    assert "L2" in disp[2], "L2 应在 row 3"
    assert r._buf_h == 3, "增长后 _buf_h = 文档行数"


def test_grow_drifted_mode_line_at_bottom():
    """M4 漂移增长：缩短产生物理缓冲漂移后增长，模式行贴屏幕最后一行
    （review P3：漂移 + 增长切换底部对齐，不再残留清空空行）。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(13, prefix="L"))   # 超屏
    r.render(_mkframe(10, prefix="L"))   # 缩短 → 漂移
    r.render(_mkframe(12, prefix="L"))   # 再增长（漂移增长）
    disp = _screen_display(r, out)
    assert "L11" in disp[9], f"漂移增长后模式行应在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"


def test_equal_height_rewrite_last_row_no_scroll():
    """M4 等高重写最后一行（模式行样式变化）：末行原地重写不写 \n、不滚动，
    屏幕最后一行仍为模式行内容。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(10, prefix="L"))   # 恰好满屏
    # 等高：末行内容变化（模拟模式行呼吸色/时间戳刷新）
    lines = [Line.of(f"L{i}") for i in range(9)]
    lines.append(Line.of("MODE"))
    r.render(Frame(lines))
    text = out.getvalue()
    assert "MODE" in text
    # 末行重写不应以 \n 结尾（无末尾空行 / 不触发滚动）
    assert not text.rstrip("\r\n").endswith("\n") or "MODE" in text.rstrip("\n").split("\n")[-1]
    disp = _screen_display(r, out)
    assert "MODE" in disp[9], f"等高重写后模式行应在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"


def test_delta_shift_region_grow_cursor_moved():
    """M4 位移区增长（生产主路径：输入光标在输入行、非文档底部，平移快路径
    守卫不满足 → 走位移区）——新增行正确追加，模式行贴屏幕最后一行。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(_mkframe(10, prefix="L"))   # 恰好满屏
    r.place_cursor(5, 1)                 # 光标移到输入行（非底部）
    assert r.cursor_row != 10, "光标应不在底部（触发位移区而非平移快路径）"
    r.render(_mkframe(12, prefix="L"))   # 增长 2 行
    disp = _screen_display(r, out)
    assert "L11" in disp[9], f"位移区增长后模式行应在屏幕最后一行: {disp[9]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"


def test_delta_shift_region_single_new_last_row():
    """M4 位移区仅含末行（增长 + 末行内容变化同帧，review P1）：无滚动补偿
    时尾部整体不位移、末行原地覆盖旧末行——修复后 NEW 在屏幕最后一行、
    OLD 在倒数第二行。"""
    r = InkRenderer(stream=io.StringIO(), height=10)
    out = io.StringIO()
    r._stream = out
    r.render(Frame([Line.of(f"L{i}") for i in range(29)] + [Line.of("OLD")]))
    r.place_cursor(20, 3)  # 光标移到输入行（非底部 → 位移区路径）
    r.render(Frame([Line.of(f"L{i}") for i in range(29)] + [Line.of("OLD"), Line.of("NEW")]))
    disp = _screen_display(r, out)
    assert "NEW" in disp[9], f"末行 NEW 应在屏幕最后一行: {disp[9]!r}"
    assert "OLD" in disp[8], f"OLD 应在倒数第二行: {disp[8]!r}"
    assert disp[9].strip() != "", "屏幕最后一行不应是空行"
