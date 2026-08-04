"""渲染器模糊不变量回归测试 — 随机帧序列 + 迷你终端重放验证。

覆盖渲染器的核心正确性契约（随机生成帧序列，经 MiniTerm 模拟器重放
渲染器输出，验证终端可见区与目标帧一致）：

  - 屏幕内契约（``doc_h+1 <= height``）：完整文档行按序出现在可见区，
    可见区无陈旧残留行（顺序正确 + 无混叠）——文档在屏幕内时用户应看到
    全部内容。
  - 高于屏幕契约：文档末尾行必须在可见区（底部内容不丢失）。
  - 高文档 → 缩短进入屏幕内（底部对齐契约）：完整文档底部对齐可见区底部
    （`test_shrink_into_screen_bottom_aligned`，精确可见区断言）。

模糊测试使用独立 MiniTerm 迷你 ANSI 终端模拟器（缓冲行 + 光标 + 滚动），
与 test_render_chaos_fixes.py 的 MiniTerm 同族但初始化全空屏（更接近真实
终端初始状态）。渲染器输出（含 ANSI 光标序列）逐字符重放，验证终端最终
状态。

**已验证的渲染器路径**：首帧全量写入、常规增长/缩短、漂移物理映射重写
（``_rewrite_drifted``/``_grow_drifted``）、底部对齐负偏移模型、屏幕内
等高 diff、行级替换/删除/插入。
"""

from __future__ import annotations

import io
import random
import re

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


class MiniTerm:
    """迷你 ANSI 终端模拟器（初始化全空屏）。

    与 test_render_chaos_fixes.py 的 MiniTerm 同族——缓冲行 + 光标 + 滚动
    （``\\n`` 在屏幕底部触发上滚）。差异：本模拟器初始化 ``height`` 行空屏
    （真实终端初始状态），``visible()`` 恒返回 ``height`` 行可见区。
    """

    def __init__(self, height: int):
        self.h = height
        self.buf: list[str] = [""] * height
        self.sr = 0  # 屏幕内光标行 0-based
        self.c = 0

    def _row(self) -> int:
        return (len(self.buf) - self.h) + self.sr

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
                m = re.match(r"\x1b\[([0-9;]*)([A-Za-z])", s[i:])
                if m:
                    args, cmd = m.group(1), m.group(2)
                    if cmd == "A":
                        self.sr = max(0, self.sr - int(args or 1))
                    elif cmd == "B":
                        self.sr = min(self.h - 1, self.sr + int(args or 1))
                    elif cmd == "C":
                        self.c += int(args or 1)
                    elif cmd == "D":
                        self.c = max(0, self.c - int(args or 1))
                    elif cmd == "K":
                        row = self._row()
                        while len(self.buf) <= row:
                            self.buf.append("")
                        line = self.buf[row]
                        if args in ("", "0"):
                            self.buf[row] = line[: self.c]
                        elif args == "1":
                            self.buf[row] = " " * len(line)
                        else:
                            self.buf[row] = ""
                    i += m.end()
                else:
                    i += 1
            else:
                self._write(ch)
                i += 1

    def visible(self) -> list[str]:
        """可见区（最后 height 行，剥 ANSI 序列 + 右端空白裁掉）。"""
        return [
            re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", r).rstrip()
            for r in self.buf[-self.h:]
        ]


def _render_seq(seq, height: int) -> list[str]:
    """渲染帧序列并重放，返回最终可见区。"""
    t = MiniTerm(height)
    out = io.StringIO()
    r = InkRenderer(stream=out, height=height)
    for lines in seq:
        r.render(_frame(*lines))
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
    return t.visible()


class TestShrinkIntoScreenBottomAligned:
    """高文档 → 缩短进入屏幕内：底部对齐契约（完整文档可见，精确断言）。"""

    def test_shrink_to_screen_precise_visible(self):
        """高文档（> 屏幕）缩短到屏幕内：可见区 = doc + 末尾空行（占满屏幕）。

        渲染器契约：文档从高于屏幕缩短进入屏幕内时切换为底部对齐——完整
        文档底部对齐可见区底部（``_rewrite_drifted`` 切换 ``_top_aligned``）。
        本场景 doc 7 行 + 末尾空行 = 8 行恰好占满屏幕（无顶部空行）。
        """
        H = 8
        seq = [
            [f"T{i}" for i in range(H + 2)],            # 高于屏幕（10 行）
            [f"T{i}" for i in range(H + 2) if i != 0],  # 9 行
            [f"T{i}" for i in range(H - 1)],            # 7 行（进入屏幕内）
        ]
        vis = _render_seq(seq, H)
        exp = [f"T{i}" for i in range(H - 1)] + [""]
        assert vis == exp, f"缩短进入屏幕内应底部对齐: 预期 {exp} 实际 {vis}"

    def test_shrink_into_screen_multiple_steps(self):
        """多次缩短进入屏幕内后，文档始终完整可见（底部对齐保持）。"""
        H = 8
        seq = [
            [f"L{i}" for i in range(H + 3)],
            [f"L{i}" for i in range(H + 1)],
            [f"L{i}" for i in range(H - 2)],
            [f"L{i}" for i in range(H - 4)],
        ]
        vis = _render_seq(seq, H)
        final_doc = [f"L{i}" for i in range(H - 4)]
        exp = [""] * (H - len(final_doc) - 1) + final_doc + [""]
        assert vis == exp, f"底部对齐保持: 预期 {exp} 实际 {vis}"

    def test_shrink_into_screen_then_grow_keeps_visible(self):
        """缩短进入屏幕内后增长（仍屏幕内）：完整文档可见、顺序正确。"""
        H = 8
        seq = [
            [f"L{i}" for i in range(H + 2)],
            [f"L{i}" for i in range(H - 2)],
            [f"L{i}" for i in range(H - 2) if i != 3],
            [f"L{i}" for i in range(H - 1)],
        ]
        vis = _render_seq(seq, H)
        final_doc = [f"L{i}" for i in range(H - 1)]
        vis_nonempty = [x for x in vis if x]
        assert vis_nonempty == final_doc, f"增长后可见区: 预期 {final_doc} 实际 {vis_nonempty}"


class TestScreenInsideInvariants:
    """屏幕内契约（``doc_h+1 <= height``）：完整文档可见 + 无残留。"""

    def test_random_screen_inside_sequences(self):
        """随机序列（含越界往返）最终屏幕内：全部行按序在可见区、无残留。"""
        H = 8
        for trial in range(60):
            rng = random.Random(trial * 104729 + 7)
            seq = []
            doc = [f"S{trial}-{i}" for i in range(rng.randint(0, H * 2))]
            seq.append(list(doc))
            for s in range(rng.randint(5, 15)):
                op = rng.random()
                n = len(doc)
                if op < 0.25 and n > 0:
                    i = rng.randrange(n)
                    del doc[i]
                elif op < 0.5:
                    i = rng.randrange(n + 1)
                    doc.insert(i, f"N{trial}-{s}")
                elif op < 0.7 and n > 0:
                    i = rng.randrange(n)
                    doc[i] = f"M{trial}-{s}"
                elif op < 0.85 and n > 0:
                    k = min(n, 1 + rng.randrange(3))
                    del doc[n - k:]
                elif n > 0:
                    for _ in range(rng.randint(1, 3)):
                        i = rng.randrange(len(doc) + 1)
                        doc.insert(i, f"X{trial}-{s}")
                seq.append(list(doc))
            vis = _render_seq(seq, H)
            final_doc = seq[-1]
            if len(final_doc) + 1 > H:
                # 高于屏幕：末尾行必须可见
                if final_doc and final_doc[-1] not in vis:
                    assert False, f"trial={trial} 末尾行丢失: doc={final_doc} vis={vis}"
                continue
            # 屏幕内：全部行按序在可见区
            vis_nonempty = [x for x in vis if x]
            idx = 0
            for line in final_doc:
                if not line:
                    continue
                while idx < len(vis_nonempty) and vis_nonempty[idx] != line:
                    idx += 1
                assert idx < len(vis_nonempty), (
                    f"trial={trial} 内容丢失 '{line}': doc={final_doc} vis={vis}"
                )
                idx += 1
            # 无残留：可见区非空行都来自 doc
            for line in vis_nonempty:
                assert line in final_doc, (
                    f"trial={trial} 残留行 '{line}': doc={final_doc} vis={vis}"
                )


class TestHighDocumentTailVisible:
    """高于屏幕契约：文档末尾行始终可见（底部内容不丢失）。"""

    def test_grow_beyond_screen_tail_visible(self):
        """文档增长越过屏幕边界：末尾行必须在可见区。"""
        H = 5
        seq = [
            ["a", "b", "c", "d", "e", "f", "g"],
            ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k"],
            ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j", "k", "l"],
        ]
        vis = _render_seq(seq, H)
        assert "l" in vis and "k" in vis, f"高于屏幕末尾行丢失: {vis}"

    def test_replace_tail_beyond_screen(self):
        """高于屏幕时替换末尾行：新末尾行出现在可见区（无陈旧）。"""
        H = 5
        seq = [
            [f"L{i}" for i in range(9)],
            [f"L{i}" if i < 8 else "NEW" for i in range(9)],
        ]
        vis = _render_seq(seq, H)
        assert "NEW" in vis, f"末尾替换未显示: {vis}"
        # 旧的最后一行 L8 不应仍在可见区（除非被 NEW 占据位置——L8 被替换）
        vis_nonempty = [x for x in vis if x]
        assert "L8" not in vis_nonempty, f"陈旧行残留: {vis}"


class TestWideCharNoCorruption:
    """CJK/emoji 宽字符 + 随机帧序列：行宽不变量 + 内容不丢失。"""

    def test_cjk_random_sequences(self):
        """含 CJK 的行随机增删改：宽字符行不丢失、不残留。"""
        H = 6
        words = ["中文测试", "emoji👍", "aaa", "héllo", "中a中", "👍👍"]
        for trial in range(40):
            rng = random.Random(trial * 31 + 5)
            seq = []
            doc = [rng.choice(words) for _ in range(rng.randint(1, H + 2))]
            seq.append(list(doc))
            for s in range(rng.randint(4, 12)):
                op = rng.random()
                n = len(doc)
                if op < 0.3 and n > 0:
                    del doc[rng.randrange(n)]
                elif op < 0.55:
                    doc.insert(rng.randrange(n + 1), rng.choice(words))
                elif op < 0.8 and n > 0:
                    doc[rng.randrange(n)] = rng.choice(words)
                elif n > 0:
                    doc.pop()
                seq.append(list(doc))
            vis = _render_seq(seq, H)
            final_doc = seq[-1]
            if len(final_doc) + 1 <= H:
                vis_nonempty = [x for x in vis if x]
                assert vis_nonempty == final_doc, (
                    f"trial={trial} 屏幕内 CJK 错位: doc={final_doc} vis={vis}"
                )
            else:
                assert final_doc[-1] in vis, f"trial={trial} CJK 末尾丢失: {vis}"


__all__ = [
    "MiniTerm",
    "_frame",
    "_render_seq",
    "TestShrinkIntoScreenBottomAligned",
    "TestScreenInsideInvariants",
    "TestHighDocumentTailVisible",
    "TestWideCharNoCorruption",
]


class TestResizeInvariants:
    """resize（reset(full=True) + set_height）场景：重渲染后可见区正确。"""

    def test_resize_then_rerender_screen_inside(self):
        """resize 后立即重渲染当前帧：屏幕内文档完整可见（首帧全量写入）。"""
        H = 6
        t = MiniTerm(H)
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        doc = ["a", "b", "c"]
        # 先渲染（高于或等于屏幕）
        r.render(_frame(*doc))
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
        # resize 到更大高度 + 全量重置 + 重渲染
        t = MiniTerm(10)
        r.set_height(10)
        r.reset(full=True)
        r.render(_frame(*doc))
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
        vis = t.visible()
        # 文档 3 行 + 末尾空行，屏幕 10 行 → 顶部对齐首帧（内容从顶部开始）
        assert vis[:4] == ["a", "b", "c", ""], f"resize 后可见区: {vis}"

    def test_resize_smaller_doc_beyond_screen_tail_visible(self):
        """resize 到更小高度（文档高于屏幕）：末尾行仍可见。"""
        H = 8
        t = MiniTerm(H)
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        doc = ["L0", "L1", "L2", "L3", "L4", "L5"]
        r.render(_frame(*doc))
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
        # resize 到 3 行（doc 6 行高于屏幕）
        t = MiniTerm(3)
        r.set_height(3)
        r.reset(full=True)
        r.render(_frame(*doc))
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
        vis = t.visible()
        assert "L5" in vis, f"resize 后末尾行丢失: {vis}"

    def test_random_resize_sequences(self):
        """随机增删改 + 随机 resize：最终屏幕内完整可见 / 高于屏幕末尾可见。"""
        for trial in range(30):
            rng = random.Random(trial * 31337 + 3)
            H = rng.choice([4, 6, 8])
            t = MiniTerm(H)
            out = io.StringIO()
            r = InkRenderer(stream=out, height=H)
            doc: list[str] = []
            for s in range(rng.randint(3, 8)):
                op = rng.random()
                n = len(doc)
                if op < 0.25:
                    i = rng.randrange(n + 1) if n else 0
                    doc.insert(i, f"R{trial}-{s}")
                elif op < 0.45 and n > 0:
                    del doc[rng.randrange(n)]
                elif op < 0.65 and n > 0:
                    doc[rng.randrange(n)] = f"M{trial}-{s}"
                elif op < 0.75:
                    doc.append(f"A{trial}-{s}")
                elif op < 0.85 and n > 0:
                    doc.pop()
                doc = doc[: H * 2]
                r.render(_frame(*doc))
                t.feed(out.getvalue())
                out.seek(0)
                out.truncate()
                if rng.random() < 0.35:
                    new_h = rng.choice([4, 6, 8])
                    t = MiniTerm(new_h)
                    H = new_h
                    r.set_height(new_h)
                    r.reset(full=True)
                    r.render(_frame(*doc))
                    t.feed(out.getvalue())
                    out.seek(0)
                    out.truncate()
            vis = t.visible()
            if len(doc) + 1 <= H:
                vis_ne = [x for x in vis if x]
                assert vis_ne == doc, (
                    f"trial={trial} 屏幕内错位: doc={doc} vis={vis}"
                )
            else:
                if doc and doc[-1] not in vis:
                    assert False, f"trial={trial} 末尾丢失: doc={doc} vis={vis}"
