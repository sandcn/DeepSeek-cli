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
        # 仅写 delta 新行（第 4 行 "d"），不重写平移行 a/b/c；满宽行 wrap
        # 修复：\n 前 \r 归位。
        assert val == "\r\x1b[Kd\r\n", (
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
        assert val == "\r\x1b[Kz\r\n\r\x1b[Kw\r\n"
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
        # 重写 X,b,c（3 行）；满宽行 wrap 修复：\n 前 \r 归位
        assert val == "\x1b[2A" + "\r\x1b[KX\r\n" + "\r\x1b[Kb\r\n" + "\r\x1b[Kc\r\n"
        assert r.cursor_row == 5


class TestMaxRewriteRowsIncremental:
    """PERF-4 — 单帧重写行数超限：仍走增量路径（非 resize 均增量）。

    超限不再降级为全量 clear + 全量重建（闪烁）——增量路径本就只写变化行
    且无 clear_screen；超限仅记 warning（阈值保留防静默病态大重写）。
    """

    def _new(self) -> tuple[InkRenderer, io.StringIO]:
        out = io.StringIO()
        return InkRenderer(stream=out), out

    def test_max_rewrite_rows_incremental_regression(self):
        """大差异（> _MAX_REWRITE_ROWS 行）仍走增量路径——不降级为全量
        clear + 全量重建（1.5 修复 + 非 resize 增量强化）。

        旧行为（1.5 修复前）「仅写末尾 _MAX_REWRITE_ROWS 行 + _CLEAR_EOL」
        在文档中间残留旧行；1.5 修复后改为全量 clear + 重建（闪烁）。现改为
        **仍按增量路径重写全部变化行**（无 clear_screen，不闪烁），画布与
        目标帧一致、无残留。
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
        # 非 resize 增量：超限**不** clear_screen（无闪烁）
        assert not val.startswith("\x1b[2J\x1b[H"), (
            f"超限应仍走增量路径（无 clear_screen），实际: {val[:20]!r}"
        )
        # 增量路径写全部 500 个变化行（每行一个 \x1b[K 行尾清除；满宽行 wrap
        # 修复后行首/行尾各一 \r，行数按 \x1b[K 计数）
        assert val.count("\x1b[K") == 500
        # 首行/末行均被写入（末行以 \n 结尾，末尾可能跟光标归位序列）
        assert "L0" in val
        assert "L499" in val
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

    def test_rewrite_no_stale_lines_regression(self):
        """超限增量重写全部变化行：画布与目标帧一致、无旧行残留（1.5 修复）。

        修复前「仅写末尾 _MAX_REWRITE_ROWS 行」跳过首差异行之前的静态内容，
        中间行残留旧帧行；现增量路径重写全部变化行（无 clear_screen），
        下一帧与目标帧一致时无输出。
        """
        r, out = self._new()
        r.render(_frame("start"))
        out.seek(0)
        out.truncate()
        big = _frame(*(f"L{i}" for i in range(500)))
        r.render(big)
        val = out.getvalue()
        # 非 resize 增量：无全量 clear（ED2+CUP）
        assert not val.startswith(clear_screen()), (
            f"超限应无 clear_screen，实际: {val[:20]!r}"
        )
        # 全部 500 行被写入（每行一个 \x1b[K；满宽行 wrap 修复后行首/行尾各一
        # \r，行数按 \x1b[K 计数；旧实现仅写末尾 200 行 → 首行 L0 缺失）
        assert val.count("\x1b[K") == 500, (
            f"增量应写 500 行，实际 {val.count(chr(0x1b))} 行"
        )
        assert "L0" in val, "首行 L0 应被写入（修复前跳写末尾 200 行不写 L0）"
        assert "L499" in val, "末行 L499 应被写入"
        # 光标位置更新正确（增量后位于文档底部）
        assert r.cursor_row == 501
        # 尾部内容可恢复：下一帧与 500 行帧一致 → 无输出
        out.seek(0)
        out.truncate()
        r.render(big)
        assert out.getvalue() == ""

    def test_rewrite_degrade_emit_only_new_lines_regression(self):
        """超限增量重写仅回调新增行（prev_h..new_h），不重复回调已有行。"""
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
        # 满宽行 wrap 修复：写行结尾 \r\n（\n 前 \r 归位）
        assert val == "\x1b[2A" + "\r\x1b[KX\r\n" + "\r\x1b[Kb\r\n" + "\r\x1b[Kc\r\n"


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
        assert val.endswith("\r\x1b[Kd\r\n"), f"应写 delta 新行 d: {val!r}"
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
        assert val == "\r\x1b[Ka\r\n", (
            f"第二帧应无多余光标移动（仅写 a 行），实际: {val!r}"
        )
        assert r.cursor_row == 2


class TestLineRenderCache:
    """PERF-24 — Line.render() ANSI 渲染缓存（同 Line 对象跨帧零重建）。

    Line 为可变对象（append 修改 runs），渲染缓存须在修改时失效；
    clone 复制缓存（runs 未变）。StyledRun/Style frozen → 缓存确定性安全。
    """

    def test_render_cache_returns_same_string(self):
        """同 Line 对象 render() 结果稳定（缓存命中）。"""
        from src.tui.ink.output import Line, StyledRun
        from src.tui.core.style import Style
        ln = Line([StyledRun("abc", Style(fg=45)), StyledRun("def", None)])
        first = ln.render()
        second = ln.render()
        assert first == second == "\x1b[38;5;45mabc\x1b[0mdef"

    def test_append_invalidates_cache(self):
        """append 修改 runs 后缓存失效（新内容正确渲染）。"""
        from src.tui.ink.output import Line
        ln = Line.of("abc")
        assert ln.render() == "abc"
        ln.append("def", None)
        assert ln.render() == "abcdef"
        # 相邻同 style 合并路径（替换 runs[-1]）
        ln.append("ghi")
        assert ln.render() == "abcdefghi"
        # 不同 style 追加路径（append 新 run）
        from src.tui.core.style import Style
        ln.append("XYZ", Style(fg=45))
        assert ln.render() == "abcdefghi\x1b[38;5;45mXYZ\x1b[0m"

    def test_append_run_invalidates_cache(self):
        """append_run 经 append 失效缓存。"""
        from src.tui.ink.output import Line, StyledRun
        ln = Line.of("a")
        assert ln.render() == "a"
        ln.append_run(StyledRun("b", None))
        assert ln.render() == "ab"

    def test_clone_copies_render_cache(self):
        """clone 复制渲染缓存（runs 相同 → 结果相同）；后续 append 不互相污染。"""
        from src.tui.ink.output import Line
        ln = Line.of("hello")
        ln.render()  # 填充缓存
        c = ln.clone()
        assert c.render() == "hello"
        ln.append(" world")
        assert ln.render() == "hello world"
        assert c.render() == "hello", "clone 应独立于原行后续修改"

    def test_render_cache_empty_line(self):
        """空行 render() 稳定返回空串。"""
        from src.tui.ink.output import Line
        ln = Line()
        assert ln.render() == ""
        ln.append("x")
        assert ln.render() == "x"
        assert Line().render() == ""


class TestRenderPipelineSmoke:
    """PERF-24 — 完整渲染管线性能冒烟（防 O(n²)/缓存失效回归）。

    大历史 + 流式场景 300 帧渲染总耗时上限宽松阈值（CI 抖动容忍）——
    仅防结构性性能回归（如 Element/fiber 缓存失效导致的逐帧全量重建）。
    """

    def _build_model(self):
        from src.tui.app.model import AppModel
        model = AppModel()
        model.width = 100
        for i in range(100):
            rr = model.ensure_content()
            rr.write(f"历史回答 {i}：一段较长的中文内容用于测试渲染性能表现。\n\n补充说明第二段。\n" * 2)
            model.close_content()
            model.reopen_content()
        model.open_tool_box("t1", "bash", "grep -r pattern /src")
        for i in range(40):
            model.append_tool_output("t1", f"output line {i}: some tool output content here\n")
        from src.tui.ink.output import Line, StyledRun
        model.subagent_lines = [Line([StyledRun("● 子代理任务", None)])]
        rr = model.ensure_content()
        rr.write("流式生成的第一行内容。\n")
        return model

    def test_full_pipeline_300_frames_smoke(self):
        """300 帧完整渲染（构建+调和+布局+绘制+diff+输出）< 2.0s（含 CI 抖动容差）。"""
        import io
        import time

        from src.tui.app.app import build_app_element
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink import components as _components
        from src.tui.ink.renderer import InkRenderer

        model = self._build_model()
        r = Reconciler()
        root = r.create_root()
        renderer = InkRenderer(stream=io.StringIO())

        def render_one(append=True):
            if append:
                rr = model.ensure_content()
                rr.write("新的流式内容行 appended。\n")
            model.width = 100
            el = build_app_element(model, 100)
            r.render(root, el, 100, 40)
            frame = _components.render_frame(root, 100)
            renderer.render(frame)
            renderer.place_cursor(2, 1)

        render_one()
        for _ in range(10):
            render_one(append=False)
        t0 = time.monotonic()
        for f in range(300):
            render_one(append=(f % 3 == 0))
        elapsed = time.monotonic() - t0
        # 300 帧 × 10Hz 预算（100ms/帧）应有极大余量；宽松阈值防 CI 抖动误报
        assert elapsed < 2.0, f"300 帧完整渲染耗时 {elapsed:.2f}s（疑似缓存失效回归）"
