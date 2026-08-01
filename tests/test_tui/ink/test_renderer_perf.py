"""测试 ink/renderer.py — PERF-4 平移快路径 + 单帧重写行数上限。

Mock 输出流（StringIO），无终端依赖。断言精确 ANSI/光标序列。
"""

from __future__ import annotations

import io

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer, _MAX_REWRITE_ROWS


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
        """大差异（> _MAX_REWRITE_ROWS 行）降级为仅写末尾 _MAX_REWRITE_ROWS 行。"""
        r, out = self._new()
        # 首帧 1 行
        r.render(_frame("start"))
        out.seek(0)
        out.truncate()
        # 新帧 500 行（首行即差异 → 需重写 500 行 > 200 上限）
        big = _frame(*(f"L{i}" for i in range(500)))
        r.render(big)
        val = out.getvalue()
        # 重写行数不超过 _MAX_REWRITE_ROWS（每行写 1 次 _CLEAR_EOL）
        assert val.count("\x1b[K") <= _MAX_REWRITE_ROWS
        # 最后一行被写入
        assert val.rstrip().endswith("L499\x1b[K")
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
