"""测试 ink/renderer.py — PERF-4 平移快路径 + 单帧重写行数上限。

Mock 输出流（StringIO），无终端依赖。断言精确 ANSI/光标序列。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer, _MAX_REWRITE_ROWS
from src.tui._screen import clear_screen


def _frame(*plain_lines: str) -> Frame:
    return Frame(Line.of(l) for l in plain_lines)


class TestShiftedTailSkipsRewrite:
    """PERF-4 — 尾部内容平移快路径（仅新增 delta 行导致尾部整体下移且相同）。"""

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_shifted_tail_skips_rewrite_regression(self):
        """仅新增 1 行导致尾部平移 → 输出流不包含对平移行的重写，仅含 delta 新行。"""
        r, out = self._new()
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        # prev=[a,b,c]，new=[a,b,c,d]（delta=1，尾部相同）
        r.render(_frame("a", "b", "c", "d"))
        val = out.getvalue()
        # 仅写 delta 新行（第 4 行 "d"），不重写平移行 a/b/c
        assert val == "\rd\x1b[K\n", (
            f"平移快路径应仅写 delta 新行，实际: {val!r}"
        )
        assert val.count("\x1b[K") == 1
        assert r.cursor_row == 5

    def test_tail_shifted_append_multiple_regression(self):
        """新增 2 行 → 仅写 2 个 delta 新行。"""
        r, out = self._new()
        r.render(_frame("x", "y"))
        out.seek(0)
        out.truncate()
        r.render(_frame("x", "y", "z", "w"))
        val = out.getvalue()
        assert val == "\rz\x1b[K\n\rw\x1b[K\n"
        assert val.count("\x1b[K") == 2
        assert r.cursor_row == 5

    def test_shift_detection_helper_regression(self):
        """_is_tail_shifted 值相等判定（非仅身份）。"""
        r, _ = self._new()
        prev = _frame("a", "b", "c")
        new = _frame("a", "x", "b", "c")
        # prev.lines[1:3] == new.lines[2:4]（b,c 平移）→ True
        assert r._is_tail_shifted(prev, new, 1, 1) is True
        # 尾部不同 → False
        new2 = _frame("a", "x", "b", "D")
        assert r._is_tail_shifted(prev, new2, 1, 1) is False

    def test_middle_insertion_falls_back_to_full_rewrite_regression(self):
        """中间插入（i < prev_h）尾部必须重写（终端无 insert-line 语义）。"""
        r, out = self._new()
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        # prev=[a,b,c]，new=[a,X,b,c]：i=1 < prev_h=3 → 常规全量重写路径
        r.render(_frame("a", "X", "b", "c"))
        val = out.getvalue()
        # 重写 X,b,c（3 行）
        assert val == "\x1b[2A" + "\rX\x1b[K\n" + "\rb\x1b[K\n" + "\rc\x1b[K\n"
        assert r.cursor_row == 5


class TestMaxRewriteRowsFallback:
    """PERF-4 — 单帧重写行数上限兜底（防病态大重写冻结 UI）。"""

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_max_rewrite_rows_fallback_regression(self):
        """大差异（> _MAX_REWRITE_ROWS 行）降级为全量 clear + 全量重建（1.5 修复）。

        旧行为「仅写末尾 _MAX_REWRITE_ROWS 行 + _CLEAR_EOL」在文档中间残留旧行；
        修复后全量 clear + 全量重建（重建路径不写 _CLEAR_EOL），画布与目标帧
        一致、无残留。
        """
        r, out = self._new()
        # 首帧 1 行
        r.render(_frame("start"))
        out.seek(0)
        out.truncate()
        # 新帧 500 行（首行即差异 → 需重写 500 行 > 200 上限）
        big = _frame(*(f"L{i}" for i in range(500)))
        r.render(big)
        val = out.getvalue()
        # 全量 clear 开头（ED2 + CUP），全量重建（500 行，每行 \r 前缀）
        assert val.startswith("\x1b[2J\x1b[H")
        assert val.count("\r") == 500
        # 最后一行被写入
        assert val.rstrip().endswith("L499")
        # 光标位置更新正确
        assert r.cursor_row == 501
        # 尾部内容可恢复：下一帧与 500 行帧一致 → 无输出
        out.seek(0)
        out.truncate()
        r.render(big)
        assert out.getvalue() == ""

    def test_max_rewrite_rows_constant_regression(self):
        """_MAX_REWRITE_ROWS 常量为 200。"""
        assert _MAX_REWRITE_ROWS == 200

    def test_rewrite_degrade_no_stale_lines_regression(self):
        """超限降级全量 clear + 重建：画布与目标帧一致、无旧行残留（1.5 修复）。

        修复前「仅写末尾 _MAX_REWRITE_ROWS 行」跳过首差异行之前的静态内容，
        中间行残留旧帧行；修复后全量 clear + 全量重建，全部行重写且下一帧
        与目标帧一致时无输出。
        """
        r, out = self._new()
        r.render(_frame("start"))
        out.seek(0)
        out.truncate()
        big = _frame(*(f"L{i}" for i in range(500)))
        r.render(big)
        val = out.getvalue()
        # 全量 clear 开头（ED2 + CUP）
        assert val.startswith(clear_screen()), (
            f"降级应全量 clear 开头，实际: {val[:20]!r}"
        )
        # 全部 500 行被写入（每行 \r 前缀；旧实现仅写末尾 200 行 → 首行 L0 缺失）
        assert val.count("\r") == 500, (
            f"全量重建应写 500 行，实际 {val.count(chr(13))} 行"
        )
        assert "L0" in val, "首行 L0 应被写入（修复前跳写末尾 200 行不写 L0）"
        assert val.rstrip().endswith("L499"), "末行 L499 应被写入"
        # 光标位置更新正确（全量重建后位于文档底部）
        assert r.cursor_row == 501
        # 尾部内容可恢复：下一帧与 500 行帧一致 → 无输出
        out.seek(0)
        out.truncate()
        r.render(big)
        assert out.getvalue() == ""

    def test_rewrite_degrade_emit_only_new_lines_regression(self):
        """降级全量重建仅回调新增行（prev_h..new_h），不重复回调已有行。"""
        r, out = self._new()
        emitted: list[str] = []
        r.set_line_callback(lambda text: emitted.append(text))
        r.render(_frame("start"))
        emitted.clear()
        big = _frame(*(f"L{i}" for i in range(500)))
        r.render(big)
        # 首帧 1 行 + 新帧 500 行 → 仅回调 499 个新增行（L1..L499），
        # 首行 L0 是已有行（prev_h=1）不重复回调。
        assert emitted[0] == "L1\n", f"首个新增行应为 L1，实际 {emitted[0]!r}"
        assert emitted[-1] == "L499\n", f"末个新增行应为 L499，实际 {emitted[-1]!r}"
        assert len(emitted) == 499


class TestBufferedSingleWrite:
    """方向1 — 整帧缓冲输出：render() 单帧仅一次 write + 一次 flush。"""

    def test_render_single_write_after_first_frame_regression(self):
        """首帧后 diff 路径单帧 write 调用次数为 1（缓冲合并，防闪烁/撕裂）。"""
        out = io.StringIO()
        r = InkRenderer(stream=out)
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        # 记录 write 调用次数
        original_write = r._stream.write
        write_count = {"n": 0}

        def counting_write(data):
            write_count["n"] += 1
            return original_write(data)

        r._stream.write = counting_write
        r.render(_frame("a", "b", "c", "d"))  # 平移快路径
        assert write_count["n"] == 1, (
            f"diff 路径应单次 write（缓冲合并），实际 {write_count['n']} 次"
        )

    def test_render_single_write_rewrite_path_regression(self):
        """常规重写路径单帧 write 调用次数为 1（缓冲合并）。"""
        out = io.StringIO()
        r = InkRenderer(stream=out)
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        original_write = r._stream.write
        write_count = {"n": 0}

        def counting_write(data):
            write_count["n"] += 1
            return original_write(data)

        r._stream.write = counting_write
        r.render(_frame("a", "X", "b"))  # 中间插入 → 常规重写路径
        assert write_count["n"] == 1

    def test_write_full_single_write_regression(self):
        """首帧全量写入单次 write（缓冲合并）。"""
        out = io.StringIO()
        r = InkRenderer(stream=out)
        original_write = r._stream.write
        write_count = {"n": 0}

        def counting_write(data):
            write_count["n"] += 1
            return original_write(data)

        r._stream.write = counting_write
        r.render(_frame("a", "b", "c"))
        assert write_count["n"] == 1
        assert r.cursor_row == 4

    def test_buffered_output_content_preserved_regression(self):
        """缓冲合并不改变输出内容（既有 diff 行为回归）。"""
        out = io.StringIO()
        r = InkRenderer(stream=out)
        r.render(_frame("a", "b", "c"))
        out.seek(0)
        out.truncate()
        r.render(_frame("a", "X", "b", "c"))
        val = out.getvalue()
        assert val == "\x1b[2A" + "\rX\x1b[K\n" + "\rb\x1b[K\n" + "\rc\x1b[K\n"


class TestInputRouterCache:
    """方向C 步骤6 — _build_input_router 签名缓存（同签名复用 router 对象）。"""

    def _capture(self):
        from src.tui.ink.hooks import set_input_router_callback
        captured = []
        set_input_router_callback(lambda router: captured.append(router))
        return captured

    def test_router_cached_same_signature_regression(self):
        """同签名两次构建返回同一 router 对象（免每帧重建闭包）。"""
        from src.tui.ink.hooks import use_input
        from src.tui.ink.element import h, TEXT
        from src.tui.ink.reconciler import Reconciler

        captured = self._capture()
        handler = lambda ev: True  # 稳定身份（跨渲染同一函数对象）

        def Comp(props):
            use_input(handler, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            r.render(root, h(Comp), 80, 24)
            assert len(captured) == 2
            assert captured[0] is not None
            assert captured[0] is captured[1]  # 同签名 → 同一 router 对象
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)

    def test_router_rebuilt_on_handler_change_regression(self):
        """handler 变化（签名变）→ 重建 router（缓存失效）。"""
        from src.tui.ink.hooks import use_input
        from src.tui.ink.element import h, TEXT
        from src.tui.ink.reconciler import Reconciler

        captured = self._capture()

        class Wrap:
            handler = lambda ev: True

        def Comp(props):
            use_input(Wrap.handler, True)
            return h(TEXT, {"children": "x"})

        try:
            r = Reconciler()
            root = r.create_root()
            r.render(root, h(Comp), 80, 24)
            r.render(root, h(Comp), 80, 24)
            assert captured[0] is captured[1]  # 同签名命中缓存
            Wrap.handler = lambda ev: False    # handler 变化 → 签名变
            r.render(root, h(Comp), 80, 24)
            assert captured[2] is not captured[1]  # 重建
        finally:
            from src.tui.ink.hooks import set_input_router_callback
            set_input_router_callback(None)


class TestWrapLargeLineSmoke:
    """方向2 P2 — 大输入换行性能冒烟（防逐字符 O(n²) 回归）。

    冒烟阈值宽松（CI 抖动容忍），仅防 ``buf += ch`` 类逐字符 str 拼接
    导致的 O(n²) 回归；行内容拼接正确性一并断言。
    """

    def test_wrap_large_line_smoke(self):
        """100k 字符单行 wrap 完成 < 1s 且行内容拼接正确。"""
        import time

        from src.tui.ink.helpers import wrap_runs_by_width
        from src.tui.ink.output import StyledRun

        text = "a" * 100_000
        runs = [StyledRun(text, None)]
        start = time.monotonic()
        lines = wrap_runs_by_width(runs, 80)
        elapsed = time.monotonic() - start
        assert elapsed < 1.0, (
            f"100k 字符 wrap 耗时 {elapsed:.3f}s（疑似逐字符 O(n²) 回归）"
        )
        # 100000 整除 80 → 恰好 1250 行，每行 80 字符
        assert len(lines) == 1250
        assert "".join(l.plain for l in lines) == text


class TestFastPathGuards:
    """方向1 步骤3 — renderer 平移快路径守卫 + 首帧空帧光标。"""

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_translate_fast_path_no_scroll_regression(self):
        """光标位于 prev 文档底部之上（n_move<0）→ 放弃平移快路径走常规路径。

        场景：place_cursor 把光标移到文档中部（row 2 < prev_h+1=4），下一帧
        尾部下移（新增 delta 行）——修复前快路径 ``n_move<0`` 从下方写 delta
        行可能越过屏幕底部触发滚动；修复后走常规差异路径（安全侧）。
        """
        r, out = self._new()
        r.render(_frame("a", "b", "c"))
        assert r.cursor_row == 4
        # place_cursor 把光标移到文档中部（row 2）
        r.place_cursor(2, 1)
        out.seek(0)
        out.truncate()
        # 新帧尾部下移：["a","b","c"] → ["a","b","c","d"]（delta=1）
        r.render(_frame("a", "b", "c", "d"))
        val = out.getvalue()
        # 常规差异路径：从 cursor_row=2 上移到行 i+1=4（cursor_down 2）→ 写 d
        # 无越界滚动序列（\x1b[9999;1H 或 CUD 越界）
        assert "\x1b[9999" not in val, f"不应出现越底滚动序列: {val!r}"
        assert val.endswith("\rd\x1b[K\n"), f"应写 delta 新行 d: {val!r}"
        assert r.cursor_row == 5, f"渲染后光标应在文档底部下一行，实际 {r.cursor_row}"

    def test_first_frame_empty_cursor_row_regression(self):
        """首帧空 Frame 也更新 _cursor_row（=1）——下一帧移动量正确（无多余移动）。

        修复前空帧不置位（_cursor_row=0）→ 下一帧平移快路径 ``n_move=-1``
        cursor_down 产生多余移动。
        """
        r, out = self._new()
        r.render(_frame())  # 空帧
        assert r.cursor_row == 1, f"空帧后 _cursor_row 应为 1（height+1），实际 {r.cursor_row}"
        out.seek(0)
        out.truncate()
        r.render(_frame("a"))  # 第二帧 1 行
        val = out.getvalue()
        # 从 cursor_row=1 到行 i=0：n_move = 1-1 = 0 → 无移动，直接写
        assert val == "\ra\x1b[K\n", (
            f"第二帧应无多余光标移动（仅写 a 行），实际: {val!r}"
        )
        assert r.cursor_row == 2
