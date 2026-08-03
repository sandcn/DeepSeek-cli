"""测试 ink/renderer.py — 屏幕坐标增量渲染（文档高于屏幕时的正确性）。

覆盖本次「增量渲染 + 屏幕坐标」修复：
  - S1 长文档输入：光标坐标按屏幕钳制，输入区正确重写（不整帧重写、不越屏错位）
  - S2 长文档头部动画：可见区上方的行（滚动区）跳过重写（不引发 cursor_up 越屏错乱）
  - S3 屏幕坐标模型：_to_screen/_bottom_row 偏移与钳制
  - S4 place_cursor 屏幕钳制
  - S5 终端模拟器端到端：短/长文档 typing 后可见区与目标帧一致

Mock 输出流（StringIO）+ 迷你终端模拟器（滚动/光标/清行），无终端依赖。
"""

from __future__ import annotations

import io
import re

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


class MiniTerm:
    """迷你 ANSI 终端：缓冲行 + 光标 + 滚动（\n 在底部时上滚）。"""

    def __init__(self, height: int):
        self.h = height
        self.buf: list[str] = []  # 全部行（含 scrollback）
        self.sr = 0  # 屏幕内光标行 0-based
        self.c = 0

    def _row(self) -> int:
        return max(0, len(self.buf) - self.h) + self.sr

    def _write(self, ch: str) -> None:
        row = self._row()
        while len(self.buf) <= row:
            self.buf.append("")
        line = self.buf[row]
        if len(line) <= self.c:
            line = line + " " * (self.c - len(line) + 1)
        self.buf[row] = line[: self.c] + ch + line[self.c + 1:]
        self.c += 1

    def feed(self, s: str) -> None:
        i = 0
        while i < len(s):
            ch = s[i]
            if ch == "\r":
                self.c = 0
                i += 1
            elif ch == "\n":
                if self.sr == self.h - 1:
                    self.buf.append("")
                else:
                    self.sr += 1
                i += 1
            elif ch == "\x1b":
                m = re.match(r"\x1b\[([0-9]*)([A-DJKRH])", s[i:])
                if m:
                    n = int(m.group(1)) if m.group(1) else 1
                    cmd = m.group(2)
                    if cmd == "A":
                        self.sr = max(0, self.sr - n)
                    elif cmd == "B":
                        self.sr = min(self.h - 1, self.sr + n)
                    elif cmd == "K":
                        row = self._row()
                        if row < len(self.buf):
                            self.buf[row] = self.buf[row][: self.c]
                    elif cmd == "J":
                        # ED 清屏：清除缓冲（全量重建路径，简化）
                        self.buf = []
                        self.sr = 0
                        self.c = 0
                    elif cmd == "H":
                        # CUP 归位（clear_screen 的 \033[H）
                        self.sr = 0
                        self.c = 0
                    i += m.end()
                else:
                    i += 1
            else:
                self._write(ch)
                i += 1

    def buffer(self) -> list[str]:
        out = [re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", r).rstrip() for r in self.buf]
        while out and out[-1] == "":
            out.pop()
        return out


def _replay(prev: Frame, new: Frame, height: int, place=None) -> list[str]:
    """首帧 + place_cursor + 第二帧 全输出重放，返回模拟终端 buffer。"""
    out = io.StringIO()
    r = InkRenderer(stream=out, height=height)
    r.render(prev)
    feed = out.getvalue()
    out.seek(0)
    out.truncate()
    if place:
        r.place_cursor(*place)
        feed += out.getvalue()
        out.seek(0)
        out.truncate()
    r.render(new)
    feed += out.getvalue()
    t = MiniTerm(height)
    t.feed(feed)
    return t.buffer()


class TestScreenOffsetModel:
    """S3 — 屏幕坐标偏移与钳制。"""

    def _new(self, height: int) -> InkRenderer:
        return InkRenderer(stream=io.StringIO(), height=height)

    def test_offset_short_doc_zero(self):
        r = self._new(8)
        assert r._screen_offset(5) == 0  # 文档短于屏幕：无偏移

    def test_offset_long_doc(self):
        r = self._new(8)
        # _write_full 后缓冲区 doc_h+1 行（末尾空白），偏移含之
        assert r._screen_offset(23) == 16  # 23+1-8

    def test_to_screen_short_doc_identity(self):
        r = self._new(8)
        assert r._to_screen(3, 5) == 3

    def test_to_screen_long_doc(self):
        r = self._new(8)
        assert r._to_screen(23, 23) == 7  # 内容末行在屏幕底部上一行
        assert r._to_screen(1, 23) == -15  # 头部在可见区上方（滚动区）

    def test_bottom_row_clamped(self):
        r = self._new(8)
        assert r._bottom_row(5) == 6  # 短文档：doc_h+1
        assert r._bottom_row(23) == 8  # 长文档：钳制到屏幕底部

    def test_height_zero_no_clamp(self):
        r = self._new(0)  # 无高度约束（测试/兼容）
        assert r._bottom_row(23) == 24
        assert r._to_screen(5, 23) == 5


class TestScreenLongDocTyping:
    """S1/S2 — 长文档输入与头部动画。"""

    def _new(self, height: int) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out, height=height), out

    def test_long_doc_input_rewrites_only_input_line(self):
        """长文档（>屏幕高）输入变化：仅重写输入行，不重写 committed 历史。"""
        H = 8
        N = 20
        prev_lines = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new_lines = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in2"]
        r, out = self._new(H)
        r.render(_frame(*prev_lines))
        # 光标放到输入行（buffer 1-based N+3）
        r.place_cursor(N + 3, 1)
        out.seek(0)
        out.truncate()
        r.render(_frame(*new_lines))
        val = out.getvalue()
        # 仅重写输入行（in1→in2），committed c 行不被重写
        assert "in2" in val
        assert "c1" not in val and "c19" not in val, (
            f"committed 行不应被重写: {val!r}"
        )
        assert val.count("\x1b[K") == 1, f"应仅重写 1 行: {val!r}"

    def test_long_doc_header_off_screen_skipped(self):
        """长文档头部变化（动画）：位于可见区上方（滚动区）→ 跳过不重写。"""
        H = 8
        N = 20
        prev_lines = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new_lines = ["h2"] + [f"c{i}" for i in range(N)] + ["status", "in2"]
        r, out = self._new(H)
        r.render(_frame(*prev_lines))
        r.place_cursor(N + 3, 1)
        out.seek(0)
        out.truncate()
        r.render(_frame(*new_lines))
        val = out.getvalue()
        # 头部（h2）不可达被跳过；仅重写输入行
        assert "h2" not in val, f"离屏头部不应被重写: {val!r}"
        assert "in2" in val
        assert val.count("\x1b[K") == 1

    def test_place_cursor_clamped_to_screen(self):
        """place_cursor 目标在可见区上方/下方时钳制到屏幕范围。"""
        r, out = self._new(8)
        r.render(_frame(*(["a"] * 20)))
        # 目标 buffer 行 21（1-based）→ 屏幕行 21-13=8（底部）
        r.place_cursor(21, 1)
        assert r.cursor_row == 8, f"长文档 place_cursor 应钳制到屏幕底部: {r.cursor_row}"


class TestScreenEndToEnd:
    """S5 — 终端模拟器端到端：typing 后可见区与目标帧一致。"""

    def test_short_doc_typing_visible_ok(self):
        prev = ["h1", "c1", "c2", "status", "in1"]
        new = ["h1", "c1", "c2", "status", "in2"]
        buf = _replay(_frame(*prev), _frame(*new), 10, place=(5, 1))
        assert buf == new

    def test_long_doc_typing_visible_ok(self):
        N = 20
        prev = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in2"]
        buf = _replay(_frame(*prev), _frame(*new), 8, place=(N + 3, 1))
        # 可见区（底部 8 行）与目标帧底部 8 行一致
        assert buf[-8:] == new[-8:]

    def test_long_doc_header_off_screen_visible_ok(self):
        N = 20
        prev = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = ["h2"] + [f"c{i}" for i in range(N)] + ["status", "in2"]
        buf = _replay(_frame(*prev), _frame(*new), 8, place=(N + 3, 1))
        # 可见区正确；scrollback 中的头部保持旧值（离屏跳过，无错乱）
        assert buf[-8:] == new[-8:]
        assert buf[0] == "h1"  # 头部在 scrollback 保持旧值（安全跳过）

    def test_append_grow_visible_ok(self):
        prev = ["h1", "c1", "status", "in1"]
        new = ["h1", "c1", "c2", "status", "in1"]
        buf = _replay(_frame(*prev), _frame(*new), 6, place=(4, 1))
        assert buf[-6:] == new[-6:]

    def test_shrink_visible_ok(self):
        prev = ["h1", "c1", "c2", "status", "in1"]
        new = ["h1", "c1", "status", "in1"]
        buf = _replay(_frame(*prev), _frame(*new), 8, place=(5, 1))
        assert buf[-4:] == new[-4:]

    def test_short_doc_header_plus_input_ok(self):
        prev = ["h1", "c1", "c2", "status", "in1"]
        new = ["h2", "c1", "c2", "status", "in2"]
        buf = _replay(_frame(*prev), _frame(*new), 10, place=(5, 1))
        assert buf == new

    def test_streaming_middle_insert_long_doc_ok(self):
        """长文档中间插入（流式）：新行插入后尾部下移，不覆盖上方内容。"""
        N = 7
        H = 5
        prev = ["h"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = ["h"] + [f"c{i}" for i in range(N)] + ["c_new", "status", "in1"]
        buf = _replay(_frame(*prev), _frame(*new), H, place=(N + 2, 1))
        assert buf == new
        assert buf[-H:] == new[-H:]

    def test_streaming_multiple_append_long_doc_ok(self):
        """长文档流式连续追加多行（每次在 status 前插入一行）。"""
        N = 7
        H = 5
        prev = ["h"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = ["h"] + [f"c{i}" for i in range(N)] + ["c_a", "c_b", "c_c", "status", "in1"]
        buf = _replay(_frame(*prev), _frame(*new), H, place=(N + 2, 1))
        assert buf == new

    def test_streaming_with_off_screen_header_ok(self):
        """长文档流式 + 头部动画：可见区正确，离屏头部跳过不覆盖。"""
        N = 7
        H = 5
        prev = ["h1"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = ["h2"] + [f"c{i}" for i in range(N)] + ["c_new", "status", "in1"]
        buf = _replay(_frame(*prev), _frame(*new), H, place=(N + 2, 1))
        assert buf[-H:] == new[-H:]
        assert buf[0] == "h1"  # 离屏头部保持旧值（安全跳过）

    def test_streaming_bottom_append_long_doc_ok(self):
        """长文档底部追加（尾部整体下移）。"""
        N = 7
        H = 5
        prev = ["h"] + [f"c{i}" for i in range(N)] + ["status", "in1"]
        new = prev + ["tail"]
        buf = _replay(_frame(*prev), _frame(*new), H, place=(N + 2, 1))
        assert buf == new


class TestShrinkIncremental:
    """长文档缩短（height>0）→ 增量塌缩（缓冲长度跟踪防偏移漂移）。

    终端缓冲无法删除行，缩短残留使缓冲长度 > doc_h+1——渲染器跟踪实际
    缓冲长度（_buffer_len）使屏幕偏移仍准确，缩短保持增量（不整屏重建）。
    """

    def test_shrink_incremental_no_clear_screen(self):
        H = 6
        prev = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        new = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1"]
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*prev))
        out.seek(0)
        out.truncate()
        r.render(_frame(*new))
        from src.tui._screen import clear_screen
        val = out.getvalue()
        assert not val.startswith(clear_screen()), (
            "长文档缩短应增量（不整屏重建），实际: %r" % val[:20]
        )
        # 缩短仅重写/清除有限行（远小于全文档）
        assert val.count("\n") < len(prev), f"缩短重写行数应远小于文档: {val!r}"
        # 重放后缓冲与目标帧一致
        buf = _replay(_frame(*prev), _frame(*new), H)
        assert buf == new

    def test_grow_shrink_grow_no_drift(self):
        """增长→缩短→再增长：缩短增量塌缩（残留缓冲 _buffer_len 跟踪）后
        再增长不偏移（无整屏重建）。"""
        H = 6
        base = ["h"] + [f"c{i}" for i in range(6)] + ["status", "in1"]
        grown = ["h"] + [f"c{i}" for i in range(6)] + ["x", "status", "in1"]
        # 增长：base→grown；缩短：grown→base；再增长：base→grown
        t = MiniTerm(H)
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*base))
        feed = out.getvalue()
        out.seek(0)
        out.truncate()
        r.render(_frame(*grown))          # 增长（中间插入 x）
        feed += out.getvalue()
        out.seek(0)
        out.truncate()
        r.render(_frame(*base))           # 缩短（重建）
        feed += out.getvalue()
        out.seek(0)
        out.truncate()
        r.render(_frame(*grown))          # 再增长
        feed += out.getvalue()
        t.feed(feed)
        buf = [l.rstrip() for l in t.buf]
        while buf and buf[-1] == "":
            buf.pop()
        assert buf == grown, f"增长-缩短-增长后缓冲应等于 grown，实际尾部: {buf[-6:]}"


class TestPartiallyVisibleRun:
    """差异区间跨可见区边界（起始行离屏、尾部可见）→ 只重写可见部分。

    user_select 弹窗高亮移动跨屏幕边界时，起始行位于滚动区（不可达）——
    修复前整段区间跳过导致可见部分残留陈旧内容（高亮不更新）。
    """

    def test_popup_spanning_screen_boundary_updates_visible_part(self):
        H = 6
        base = ["h"] + [f"r{i}" for i in range(13)] + ["optA", "optB", "optC",
                                                       "hint", "sep", "inp", "ts"]
        prev = list(base)
        new = list(base)
        new[14] = "optA2"   # 离屏（offset=16，index<16 不可达）
        new[15] = "optB2"   # 离屏
        new[16] = "▶optC"   # 可见（index 16 为可见区首行）
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*prev))
        r.place_cursor(20, 1)
        out.seek(0)
        out.truncate()
        r.render(_frame(*new))
        val = out.getvalue()
        # 仅重写可见部分（▶optC）；离屏行 optA2/optB2 不重写（不可达）
        assert "▶optC" in val
        assert "optA2" not in val
        assert "optB2" not in val
        # 重放后缓冲含更新后的可见行 optC（滚动区行可陈旧，不可见）
        buf = _replay(_frame(*prev), _frame(*new), H, place=(20, 1))
        assert any("▶optC" in row for row in buf)
