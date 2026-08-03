"""渲染器增量路径渲染错乱回归测试（BUG-64/66/65）。

覆盖模糊测试发现的渲染错乱与输出历史重复：
  - BUG-64：``_grow_drifted`` 顶部对齐旧布局切换底部对齐时 drift0 误用
    底部对齐公式 → 物理行映射误判 → 必要重写被跳过 → 内容行从屏幕消失
    （4→3→4 行序列中 'x0' 丢失）。
  - BUG-66：底部对齐（``_top_aligned=False``，文档进入屏幕内后的负偏移
    模型）时缩短走常规 diff 路径（假设物理行 q = doc 行 q）→ 重写位置
    错误 → 内容行与残留行混叠（4→5→4→5→4 序列中 'c0/x0/c2' 错位）。
  - BUG-65：resize（reset(full=True)）/suspend（软重置）后重新渲染同一
    文档，``_emit_new_lines`` 从 0 行全量回调 → 输出历史重复写入整篇文档。

用 MiniTerm 迷你终端模拟器重放渲染输出，断言可见区与目标帧一致。
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


def _replay(seq, height: int) -> tuple[list[str], InkRenderer]:
    """渲染帧序列并重放，返回 (终端缓冲, 渲染器)。"""
    t = MiniTerm(height)
    out = io.StringIO()
    r = InkRenderer(stream=out, height=height)
    for f in seq:
        r.render(f)
        t.feed(out.getvalue())
        out.seek(0)
        out.truncate()
    return t.buffer(), r


class TestGrowDriftedTopAlignedSwitch:
    """BUG-64 — _grow_drifted 顶部对齐切换底部对齐时 drift0 误判。"""

    def test_grow_after_regular_shrink_keeps_middle_line(self):
        """常规缩短（顶部对齐，_screen_offset==0 走常规 diff 路径）后增长进入
        屏幕内（_grow_drifted 切换底部对齐）：中间行 'x0' 不应从屏幕消失。

        修复前 ``_grow_drifted`` else 分支用底部对齐公式 ``buf_h0-prev_h-1``
        推导旧行位置——顶部对齐旧布局实际 drift=0，物理行 1 显示旧 doc1
        （'x0'）却被按 doc0（'c1'）比较 → 跳过一次必要重写 → 'x0' 丢失。
        """
        H = 7
        seq = [
            _frame("c0", "c1", "c2"),
            _frame("c0", "x0", "c1", "c2"),
            _frame("c0", "x0", "c1", "m1"),
            _frame("x0", "c1", "m1"),          # 常规缩短（4→3，进入屏幕内）
            _frame("p3", "x0", "c1", "m1"),    # 增长（_grow_drifted 切换底部对齐）
            _frame("p3", "m4", "c1", "m1"),    # 等高（漂移后物理映射重写）
        ]
        buf, _ = _replay(seq, H)
        target = ["p3", "m4", "c1", "m1"]
        # 全部目标行必须按顺序出现在终端缓冲
        idx = 0
        for line in buf:
            if idx < len(target) and line == target[idx]:
                idx += 1
        assert idx == len(target), f"目标行未完整显示: {target} vs 缓冲 {buf}"
        # 关键回归断言：渲染 frame4（增长进入屏幕内）后 'x0' 不应从屏幕消失。
        # 修复前 _grow_drifted 误判旧行位置跳过重写 → 终端 'x0' 被 'c1' 覆盖。
        t = MiniTerm(H)
        out = io.StringIO()
        r = InkRenderer(stream=out, height=H)
        for f in seq[:5]:
            r.render(f)
            t.feed(out.getvalue())
            out.seek(0)
            out.truncate()
        buf4 = t.buffer()
        assert "x0" in buf4, f"frame4 后 'x0' 不应丢失: {buf4}"


class TestShrinkBottomAlignedUsesPhysicalMapping:
    """BUG-66 — 底部对齐时屏幕内缩短必须走 _rewrite_drifted。"""

    def test_shrink_bottom_aligned_no_content_mixing(self):
        """底部对齐（文档进入屏幕内后）时缩短：常规 diff 路径假设「物理行
        q = doc 行 q」会按错误位置重写（内容写到物理行 1-4，实际应在
        2-6）→ 内容行与残留行混叠。修复后走 _rewrite_drifted 物理映射，
        可见区与目标帧一致。
        """
        H = 9
        seq = [
            _frame("c0", "c1", "c2", "c3"),
            _frame("c0", "x0", "c1", "c2", "c3"),
            _frame("p1", "c0", "x0", "c1", "c2", "c3"),
            _frame("c0", "x0", "c1", "c2", "c3"),
            _frame("c0", "x0", "c2", "c3"),
            _frame("p4", "c0", "x0", "c2", "c3"),   # _grow_drifted → 底部对齐
            _frame("c0", "x0", "c2", "c3"),        # 底部对齐 + 屏幕内缩短
        ]
        buf, r = _replay(seq, H)
        target = ["c0", "x0", "c2", "c3"]
        # 可见区（底部对齐，buf_h=7, drift=2）应显示 doc 0-3 + 末尾空行
        expected = ["", "", "c0", "x0", "c2", "c3"]
        visible = buf[-H:]
        assert visible == expected, f"可见区错乱: 预期 {expected} 实际 {visible}"
        # 关键行 'c0'/'x0'/'c2' 不应与残留混叠（曾显示 c2/c3 残留）
        assert "c0" in visible and "x0" in visible, f"内容行丢失: {visible}"


class TestHistoryEmitDedup:
    """BUG-65 — resize/suspend 后输出历史不重复。"""

    def _renderer(self):
        tracked = []

        def cb(line: str) -> None:
            tracked.append(line)

        out = io.StringIO()
        r = InkRenderer(stream=out, height=10, line_callback=cb)
        return r, tracked

    def test_reset_full_no_history_duplicate(self):
        """resize（reset(full=True)）后全量重写文档：仅回调新增行。"""
        r, tracked = self._renderer()
        r.render(_frame("h", "c1", "c2", "status", "in1"))
        assert len(tracked) == 5
        r.reset(full=True)  # resize 触发
        r.render(_frame("h", "c1", "c2", "status", "in2"))
        assert len(tracked) == 5, f"resize 后历史应不重复: {len(tracked)}"

    def test_grow_still_emits_new(self):
        """增长仍回调新增行（非重复抑制）。"""
        r, tracked = self._renderer()
        r.render(_frame("h", "c1", "c2", "status", "in1"))
        r.render(_frame("h", "c1", "c2", "status", "in1", "newline"))
        assert len(tracked) == 6

    def test_suspend_resume_no_history_duplicate(self):
        """suspend（软重置）后重新渲染同一文档：历史不重复。"""
        r, tracked = self._renderer()
        r.render(_frame("h", "c1", "c2", "status", "in1"))
        r.suspend()
        r.render(_frame("h", "c1", "c2", "status", "in1"))
        assert len(tracked) == 5, f"suspend 后历史应不重复: {len(tracked)}"

    def test_soft_reset_then_grow_emits_only_new(self):
        """软重置后增长：仅回调新增行。"""
        r, tracked = self._renderer()
        r.render(_frame("h", "c1", "c2", "status", "in1"))
        r.reset(full=False)
        r.render(_frame("h", "c1", "c2", "status", "in1", "extra"))
        assert len(tracked) == 6, f"软重置+增长应只回调新增行: {len(tracked)}"
