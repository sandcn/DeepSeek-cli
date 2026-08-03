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


class TestShrinkRebuild:
    """长文档缩短（height>0）→ 增量缩短（不清屏重建）。

    用户需求「除 resize 外均增量」：文档高于屏幕时缩短不再全量 clear+重建
    （闪烁），改为增量重写可见区变化行 + 清残留（``_rewrite_drifted``）——
    物理缓冲无法删除行，缩短后缓冲长度保持（``_buf_h`` 精确跟踪漂移），
    后续增长/等高重写按真实物理偏移定位（不漂移错位）。缩短进入屏幕内
    （new_h+1 <= height）同样增量（文档底部对齐可见区底部，负偏移模型）。
    """

    def test_shrink_incremental_no_clear(self):
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
        assert clear_screen() not in val, (
            "长文档缩短应增量（不 clear_screen 重建），实际: %r" % val
        )
        # 只重写可见区变化行（删除 extra 后可见区变化行清空）
        assert "extra" not in val
        # 未变化的可见区上方行不重写（h/c0/c1 等 scrollback 行）
        assert "h" not in val and "c0" not in val and "c1" not in val
        # 重放后可见区（底部 H 行）：顶部对齐（doc 0 在物理行 0）下缩短仅清
        # 残留——可见区显示 doc 6-10（c6..in1）+ 残留空行（extra 变空白）。
        # 对比旧底部对齐（可见区=新文档底部 [c5..in1]）：顶部对齐避免弹窗/尾
        # 部上方（历史消息）全量重写（消除补全弹窗 items 变化时闪烁）。
        t = MiniTerm(H)
        out2 = io.StringIO()
        r2 = InkRenderer(stream=out2, height=H)
        r2.render(_frame(*prev))
        feed = out2.getvalue()
        out2.seek(0)
        out2.truncate()
        r2.render(_frame(*new))
        feed += out2.getvalue()
        t.feed(feed)
        visible = [l.rstrip() for l in t.buf[-H:]]
        assert visible == ["c6", "c7", "status", "in1", "", ""], (
            f"顶部对齐缩短应只清残留，实际: {visible!r}"
        )

    def test_grow_shrink_grow_no_drift(self):
        """增长→缩短→再增长：缩短增量重建重置缓冲，后续增长不偏移。"""
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
        r.render(_frame(*base))           # 缩短（增量，不清屏）
        from src.tui._screen import clear_screen
        assert clear_screen() not in out.getvalue(), "缩短应增量（无 clear_screen）"
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

    def test_shrink_entering_screen_incremental(self):
        """缩短进入屏幕内（new_h+1 <= height）仍增量（不 clear_screen 重建）。

        物理缓冲无法删除行且文档无法自然回到屏幕顶部——文档底部对齐可见区
        底部（上方空行区清空），place_cursor 经 `_effective_offset`（负偏移）
        定位到文档物理位置；不闪烁（用户需求「除 resize 外均增量」）。
        """
        H = 6
        prev = ["h"] + [f"c{i}" for i in range(6)] + ["status", "in1"]  # 9 行
        new = ["h", "c0"]  # 2 行（进入屏幕内）
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        r.render(_frame(*prev))
        out.seek(0)
        out.truncate()
        r.render(_frame(*new))
        from src.tui._screen import clear_screen
        val = out.getvalue()
        assert clear_screen() not in val, (
            "缩短进入屏幕内应增量（不 clear_screen 重建），实际: %r" % val
        )
        # 文档行已重写到可见区（h/c0），残留旧行被清空
        assert "h" in val and "c0" in val
        # 重放后：文档显示在可见区底部（物理缓冲漂移），上方为空行区
        t = MiniTerm(H)
        out2 = io.StringIO()
        r2 = InkRenderer(stream=out2, height=H)
        r2.render(_frame(*prev))
        feed = out2.getvalue()
        out2.seek(0)
        out2.truncate()
        r2.render(_frame(*new))
        feed += out2.getvalue()
        t.feed(feed)
        visible = [l.rstrip() for l in t.buf[-H:]]
        assert visible[-3:] == ["h", "c0", ""], f"文档应在可见区底部: {visible!r}"
        assert visible[:-3] == ["", "", ""], f"文档上方应为空行区: {visible!r}"
        # place_cursor 定位到文档物理位置（新文档第 2 行 = 屏幕底部上一行）
        r2.place_cursor(2, 1)
        assert r2.cursor_row == H - 1, f"光标应在文档物理位置: {r2.cursor_row}"


class TestDriftedIncremental:
    """漂移后增量渲染（缩短产生的物理缓冲漂移，不清屏重建的后续帧）。

    覆盖缩短后的连续缩短、等高重写、增长（吸收漂移）、增长-缩短震荡——
    全部保持增量（无 clear_screen）且可见区正确（scrollback 残留可陈旧）。
    锁定用户需求「除 resize 外均增量」。

    **顶部对齐（补全弹窗闪烁修复）**：文档仍高于屏幕（``doc_h+1 > height``）
    时缩短/等高/增长走「顶部对齐局部重写」——物理行 q 直接显示 doc 行 q，
    弹窗/尾部上方（历史消息）永不重写；可见区显示 doc 中部 + 残留空行
    （缩短后尾部内容上移、底部残留清空）。doc 进入屏幕内
    （``doc_h+1 <= height``）切换为底部对齐（文档底部对齐可见区底部，负偏移
    模型，完整文档可见——``test_enter_screen_incremental_lifecycle`` 锁定）。
    """

    H = 6

    def _simulate(self, seq):
        """按帧序列渲染，返回 (MiniTerm, InkRenderer, 是否出现 clear_screen)。"""
        t = MiniTerm(self.H)
        out = io.StringIO()
        r = InkRenderer(stream=out, height=self.H)
        clear_seen = False
        for i, lines in enumerate(seq):
            r.render(_frame(*lines))
            feed = out.getvalue()
            if "\x1b[2J" in feed and i > 0:
                clear_seen = True
            t.feed(feed)
            out.seek(0)
            out.truncate()
        return t, r, clear_seen

    def _visible(self, t):
        return [l.rstrip() for l in t.buf[-self.H:]]

    def test_consecutive_shrinks(self):
        """连续缩短（12→11→10→9 行）：全程增量，可见区正确（只清残留）。"""
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12, f11, f10, f9 = doc, doc[:-1], doc[:-2], doc[:-3]
        t, r, cs = self._simulate([f12, f11, f10, f9])
        assert not cs
        # 顶部对齐：物理行 q → doc q；连续缩短依次清除 in1/status/extra，
        # doc 0-6 位置不变（不重写历史），可见区 = doc 6-7 + 残留空行。
        assert self._visible(t) == ["c6", "c7", "", "", "", ""], self._visible(t)

    def test_shrink_then_equal_height_rewrite(self):
        """缩短后等高重写（漂移保持，物理映射重写）。"""
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12, f11, f10 = doc, doc[:-1], doc[:-2]
        mod = list(f10)
        mod[8] = "STATUS2"  # 中间行修改（漂移后物理映射位置）
        t, r, cs = self._simulate([f12, f11, f10, mod])
        assert not cs
        # 顶部对齐等高：物理行 q → doc q，仅重写变化行（doc8: c7→STATUS2）。
        assert self._visible(t) == ["c6", "STATUS2", "status", "", "", ""], self._visible(t)

    def test_shrink_then_grow_absorbs_drift(self):
        """大漂移后增长吸收漂移（8→11 行）：可见区正确、物理缓冲对齐。"""
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12, f8, f11 = doc, doc[:-4], doc[:-1]
        t, r, cs = self._simulate([f12, f8, f11])
        assert not cs
        # 顶部对齐增长：物理行 q → doc q，新行追加到残留位置；可见区 =
        # doc 6-10（c6..in1）+ 残留空行（doc 0-5 保持 scrollback 不变）。
        assert self._visible(t) == ["c6", "c7", "status", "in1", "", ""], self._visible(t)
        # 物理缓冲不小于新文档需要（含末尾空行）
        assert r._buf_h >= len(f11) + 1

    def test_shrink_grow_oscillation(self):
        """增长-缩短震荡（漂移反复出现/吸收）：全程增量、可见区正确。"""
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12, f11, f10 = doc, doc[:-1], doc[:-2]
        g12 = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra2"]
        t, r, cs = self._simulate([f12, f11, g12, f11, g12, f10])
        assert not cs
        # 顶部对齐：震荡只重写弹窗/尾部区域，doc 0-6 不变；最终 f10 后
        # 可见区 = doc 6-7 + 残留空行。
        assert self._visible(t) == ["c6", "c7", "status", "", "", ""], self._visible(t)

    def test_shrink_then_grow_in_place(self):
        """缩短后原地增长（追加到漂移缓冲）：可见区正确。"""
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12, f8, f9 = doc, doc[:-4], doc[:-3]
        t, r, cs = self._simulate([f12, f8, f9])
        assert not cs
        # 顶部对齐：f8（8行）→ f9（9行）追加 status 到残留位置；可见区 =
        # doc 6-7 + 残留空行。
        assert self._visible(t) == ["c6", "c7", "", "", "", ""], self._visible(t)

    def test_enter_screen_incremental_lifecycle(self):
        """进入屏幕内完整生命周期：长→进入屏幕内→屏幕内增长→出屏→再进入。

        全程增量（无 clear_screen）；文档进入屏幕内后底部对齐可见区底部
        （负偏移模型），place_cursor 经 `_effective_offset` 定位到文档物理位置。
        """
        doc = ["h"] + [f"c{i}" for i in range(8)] + ["status", "in1", "extra"]
        f12 = doc
        s2 = ["h", "c0"]          # 进入屏幕内（2 行）
        s3 = ["h", "c0", "c1"]    # 屏幕内增长（3 行）
        s1 = ["h"]                # 再缩短进入屏幕内（1 行）
        t, r, cs = self._simulate([f12, s2, s3, f12, s1])
        assert not cs
        exp = [""] * (self.H - 2) + ["h", ""]
        assert self._visible(t) == exp, self._visible(t)
        # place_cursor 定位到文档物理位置（1 行文档 → 屏幕底部上一行）
        r.place_cursor(1, 1)
        assert r.cursor_row == self.H - 1, r.cursor_row

    def test_middle_delete_then_grow_visible_ok(self):
        """中间删除（触发底部对齐切换）后增长：可见区正确。

        模糊测试锁定：长文档**中间**删除（首差异行 <= buf_top → BUG-68 切换
        底部对齐）后增长（drift 1→0，文档顶部对齐物理缓冲顶部）——可见区
        显示 doc 6-10 + 末尾空行（顶部对齐映射），不丢失 doc 中部行。
        """
        doc = ["R0", "R1", "R2", "R3", "R4", "R5", "R6", "X7-13", "L8", "L9", "L10"]
        mid_del = ["R0", "R1", "R3", "R4", "R5", "R6", "X7-13", "L8", "L9", "L10"]
        grown = ["R0", "R1", "R3", "R4", "R5", "R6", "X7-13", "L8", "L9", "L10", "L11"]
        t, r, cs = self._simulate([doc, mid_del, grown])
        assert not cs
        # 增长后顶部对齐（drift=0）：H=6 可见区 = doc 6-11（X7-13..L11 + 空行）。
        # scrollback 中物理行 2-5 可陈旧（缩短切换底部对齐时的滚动区残留——
        # 非全屏模型接受 scrollback 残留可陈旧，见 TestDriftedIncremental doc）。
        assert self._visible(t) == ["X7-13", "L8", "L9", "L10", "L11", ""], (
            self._visible(t)
        )


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
