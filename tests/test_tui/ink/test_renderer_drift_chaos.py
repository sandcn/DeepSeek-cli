"""渲染器漂移错乱回归测试（模糊测试锁定 BUG-67/68）。

用 MiniTerm 迷你终端模拟器重放渲染输出，验证增量渲染路径（有漂移增长/
缩短/等高）下**可见区内容正确性**：

  - BUG-67（_grow_drifted append 覆盖 rewrites）：物理缓冲超屏幕时 doc 行
    直接写到屏幕底部（物理行 buf_h0-1），覆盖 rewrites 刚写入的 doc 行
    buf_h0-1-drift1 —— 如 2→5 行增长中 'b' 被 'status' 覆盖、2→6 行增长中
    doc 行 3 'a' 被 doc 行 4 'c' 覆盖。
  - BUG-68（_rewrite_drifted 顶部对齐漂移）：doc 缩短后物理缓冲漂移且新 doc
    大部分在滚动区时保持顶部对齐——doc 0 固定在物理行 0，缩短后 doc 内容
    偏上滚出（滚动区不可达不重写），doc 中部行永久丢失（如 6→5→4 行序列中
    doc 行 2 'p3' 不可见）。

断言语义（与真实终端一致）：
  - 可见区（终端最后 height 行）不得包含目标帧 doc 之外的残留行；
  - 可见区非空行必须构成目标 doc 的**连续子序列**（顺序保持、无混叠）；
  - 不要求滚动区干净（无 delete-line 语义下滚动区保留旧行是终端正常行为）。

模糊测试（test_renderer_fuzz_3000 组）对随机帧序列做可见区合法性检查，
锁定增量渲染路径在任意增/删/改序列下不产生内容错位/混叠/残留。
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
                        self.buf = []
                        self.sr = 0
                        self.c = 0
                    elif cmd == "H":
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


def _replay(seq, height: int) -> tuple[MiniTerm, InkRenderer]:
    """渲染帧序列并重放，返回 (迷你终端, 渲染器)。"""
    t = MiniTerm(height)
    out = io.StringIO()
    r = InkRenderer(stream=out, height=height)
    for f in seq:
        r.render(f)
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
    return t, r


def _visible_view(t: MiniTerm, height: int) -> list[str]:
    """可见区（终端最后 height 行）。"""
    if len(t.buf) <= height:
        return list(t.buf)
    return list(t.buf[-height:])


def _is_valid_view(doc_lines: list[str], view: list[str]) -> bool:
    """可见区合法性：非空行必须是 doc 的连续子序列（顺序保持）。

    不要求 doc 全部可见（doc 高于屏幕时顶部行滚出滚动区正常）；但可见区
    不得出现 doc 之外的残留行 / 顺序颠倒 / 内容混叠。
    """
    non_empty = [v for v in view if v.strip() != ""]
    if not non_empty:
        return True  # 全空可见区（doc 可能为空或全部滚动）
    idx = 0
    for doc_line in doc_lines:
        if idx < len(non_empty) and doc_line == non_empty[idx]:
            idx += 1
    return idx == len(non_empty)


class TestGrowDriftedAppendOverwrite:
    """BUG-67 — _grow_drifted append 覆盖 rewrites 内容行。"""

    def test_grow_after_shrink_append_keeps_rewritten_row(self):
        """有漂移增长（2→5 行，H=3）：'b' 不应被 'status' 覆盖。

        修复前 append 把 doc 行 4 'status' 写到物理行 3（屏幕底部），覆盖
        rewrites 刚写入的 doc 行 3 'b'。
        """
        H = 3
        seq = [
            _frame("in1", "c"),
            _frame("in1", "c", "c"),
            _frame("in1", "c"),
            _frame("m1", "p3", "c", "b", "status"),
            _frame("a", "p3", "c", "b", "status"),
            _frame("b", "m1"),
        ]
        t, _ = _replay(seq, H)
        # 关键回归：增长后 'b'（doc 行 3）必须在缓冲中出现（修复前被 'status' 覆盖）
        buf = t.buffer()
        assert "b" in buf, f"'b' 被 'status' 覆盖: {buf}"
        # 可见区必须合法
        assert _is_valid_view([l.plain for l in seq[-1].lines], t.buf[-H:]), (
            f"可见区错乱: {t.buf[-H:]}"
        )

    def test_grow_multi_append_keeps_rewritten_middle(self):
        """有漂移增长 2→6 行（H=9）：doc 行 3 'a' 不应被 doc 行 4 'c' 覆盖。"""
        H = 9
        seq = [
            _frame("x0", "status"),
            _frame("x0", "status", "in2"),
            _frame("status", "in2"),
            _frame("a", "b", "b", "a", "c", "c1"),
            _frame("a", "b", "b", "a", "x0", "c", "c1"),
        ]
        t, _ = _replay(seq, H)
        buf = t.buffer()
        # 全部 7 行 doc 必须按顺序出现在缓冲（无覆盖）
        target = [l.plain for l in seq[-1].lines]
        idx = 0
        for line in buf:
            if idx < len(target) and line == target[idx]:
                idx += 1
        assert idx == len(target), f"doc 行被覆盖/丢失: {buf} vs {target}"


class TestRewriteDriftedTopAlignedSwitch:
    """BUG-68 — _rewrite_drifted 顶部对齐漂移导致 doc 行不可见。"""

    def test_shrink_small_doc_switches_bottom_aligned(self):
        """doc 缩短后大部分在滚动区（6→5→4 行，H=4）：doc 行 2 'p3' 必须可见。

        修复前保持顶部对齐，doc 0 固定在物理行 0，缩短后 doc 内容偏上滚出，
        doc 行 2 显示旧内容（滚动区不可达不重写）→ 'p3' 永久丢失。
        """
        H = 4
        seq = [
            _frame("x0", "c1", "in2", "p3", "a", "in1"),
            _frame("x0", "c1", "p3", "a", "in1"),
            _frame("c1", "p3", "a", "in1"),
            _frame("c1", "c1", "p3", "a", "in1"),
            _frame("m4", "c1", "p3", "a", "in1"),
        ]
        t, r = _replay(seq, H)
        view = t.buf[-H:]
        assert _is_valid_view([l.plain for l in seq[-1].lines], view), (
            f"可见区错乱: {view}"
        )
        # 关键回归：'p3'（doc 行 2）必须出现在可见区（修复前仅显示 'a','in1'）
        assert any("p3" in v for v in view), f"'p3' 不可见: {view}"


class TestRendererFuzz3000:
    """渲染器模糊测试：随机帧序列（增/删/改/替换）可见区合法性。"""

    def test_fuzz_random_sequences_visible_valid(self):
        random.seed(42)
        pool = ["a", "b", "c", "x0", "c1", "m1", "p3", "m4", "status", "in1", "in2"]
        checked = 0
        for _trial in range(1200):
            H = random.randint(3, 12)
            n0 = random.randint(1, H + 3)
            seq = [_frame(*random.sample(pool, min(n0, len(pool))))]
            cur = seq[0].lines
            for _step in range(random.randint(1, 8)):
                op = random.choice(["grow", "shrink", "edit", "replace"])
                lines = list(cur)
                if op == "grow" and len(lines) < H + 5:
                    lines.insert(random.randint(0, len(lines)), Line.of(random.choice(pool)))
                elif op == "shrink" and len(lines) > 1:
                    lines.pop(random.randint(0, len(lines) - 1))
                elif op == "edit" and lines:
                    lines[random.randint(0, len(lines) - 1)] = Line.of(random.choice(pool))
                elif op == "replace":
                    lines = [
                        Line.of(random.choice(pool))
                        for _ in range(random.randint(1, min(H + 2, len(pool))))
                    ]
                seq.append(_frame(*(l.plain for l in lines)))
                cur = lines
            # 逐帧重放并检查可见区合法性
            t = MiniTerm(H)
            out = io.StringIO()
            r = InkRenderer(stream=out, height=H)
            for f in seq:
                r.render(f)
                t.feed(out.getvalue())
                out.seek(0)
                out.truncate()
                view = t.buf[-H:] if len(t.buf) >= H else list(t.buf)
                doc_lines = [l.plain for l in f.lines]
                checked += 1
                assert _is_valid_view(doc_lines, view), (
                    f"可见区错乱: doc={doc_lines} view={view}"
                )
        assert checked > 3000  # 确保模糊规模足够（序列 × 帧）
