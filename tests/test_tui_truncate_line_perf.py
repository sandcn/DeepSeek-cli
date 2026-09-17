"""truncate_line 线性化 + committed 前缀截断缓存回归测试（TUI 性能修复）。

背景（本轮修复的两个性能缺陷，均在渲染热路径）：

1. ``ink/_runs_utils.truncate_line`` 原实现逐字符 ``out.append(ch, style)``：
   ``Line.append`` 在同样式时与上一 run 合并 → 重建 ``StyledRun(last.text + ch)``
   并对其整体重测显示宽度（``StyledRun.__post_init__`` → ``wcswidth_simple``）
   → 单行截断退化为 **O(n²)**。实测：400 列 11.9ms/次、800 列 46ms/次。
   ``truncate_line`` 在多处每帧调用（committed 前缀超宽守卫 / 画布行溢出自卫
   ``_to_line`` / 状态栏 / 输入区模式行 / 补全弹窗 / 轨迹视图），单帧多次调用
   直接击穿 10Hz 帧预算。修复：run 级累积后一次 append（O(n)，产出 runs 与
   逐字符 append 完全一致——Line.append 对同样式相邻段自动合并）。

2. ``ink/components.render_frame`` 的超宽前缀路径（``all_ok=False``）原**每帧**
   对全部前缀行重新截断（大历史每帧 O(全前缀字符) + 列表重建），且重建列表
   每帧为新对象 → ``stable_prefix`` 行级 diff 区间跳过永久失效（每帧全前缀
   逐行比较）。修复：截断结果挂在 committed fiber 上，命中条件为「同前缀列表
   对象 + 已缓存长度 <= 当前长度 + 宽度相同」，命中时**只截断新增行**；缓存
   列表对象跨帧稳定 → stable_prefix 区间跳过与 Line 身份短路同时生效。

量化（400 行 × 160 列超宽 committed 前缀、每帧追加 1 行）：
修复前 ~0.90s/帧（36s/40 帧）→ 修复后 ~0.6ms/帧。
"""

from __future__ import annotations

import io
import time

from src.tui._width import wcswidth_simple
from src.tui.app.app import App
from src.tui.app.model import AppModel
from src.tui.core.style import Style
from src.tui.ink import Line, StyledRun, components as _components, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.renderer import InkRenderer
from src.tui.ink._runs_utils import truncate_line

_WIDE = Style(fg=41)
_DIM = Style(fg=242)


def _reference_truncate_line(line: Line, max_width: int) -> Line:
    """逐字符 append 的参考实现（修复前语义，用于等价性比对）。

    ★ 独立实现（review 意见）：不复用被测模块的 ``_first_logical_line_runs``
    ——参考实现自带「首个逻辑行」归一（该依赖自身出错时等价性断言仍会通过，
    失去参考独立性）。
    """
    if max_width <= 0:
        return Line()
    if line.width <= max_width:
        return line.clone()
    # 首个逻辑行归一（含 \n 时丢弃首个 \n 之后内容；与实现同语义但独立实现）
    first: list[StyledRun] = []
    for run in line.runs:
        idx = run.text.find("\n")
        if idx < 0:
            first.append(run)
            continue
        if idx > 0:
            first.append(StyledRun(run.text[:idx], run.style))
        break
    out = Line()
    width = 0
    for run in first:
        for ch in run.text:
            cw = wcswidth_simple(ch)
            if width + cw > max_width:
                return out
            out.append(ch, run.style)
            width += cw
    return out


def _runs_equal(a: Line, b: Line) -> bool:
    return len(a.runs) == len(b.runs) and all(
        ra.text == rb.text and ra.style == rb.style for ra, rb in zip(a.runs, b.runs)
    )


class TestTruncateLineEquivalence:
    """截断语义等价（新实现 O(n) 不得改变产出 runs 结构/样式）。"""

    CASES = [
        Line([StyledRun("hello world", None)]),
        Line([StyledRun("中文内容内容", None)]),
        Line([StyledRun("mixed 中文 abc 内容", None)]),
        Line([StyledRun("emoji ⚡ 图标 🎉", None)]),
        Line([StyledRun("aaa", _WIDE), StyledRun("bbb", _DIM), StyledRun("ccc", _WIDE)]),
        Line([StyledRun("aaa", _WIDE), StyledRun("bbb", _WIDE)]),  # 相邻同样式 run
        Line([StyledRun("line1\nline2", None)]),                  # 首逻辑行截断
        Line([StyledRun("", None), StyledRun("xyz", None)]),
        Line(),
    ]

    def test_equivalence_all_widths(self):
        for line in self.CASES:
            for max_width in (0, 1, 2, 3, 5, 8, 11, 40):
                got = truncate_line(line, max_width)
                exp = _reference_truncate_line(line, max_width)
                assert _runs_equal(got, exp), (
                    f"截断结果不一致 line={line.plain!r} max_width={max_width} "
                    f"got={got.runs!r} exp={exp.runs!r}"
                )
                assert got.width <= max(0, max_width) or max_width <= 0, (
                    f"截断结果超宽: {got.width} > {max_width}"
                )

    def test_wide_char_not_split(self):
        """CJK 宽字符不被拆半（截断点在字符边界）。"""
        line = Line([StyledRun("中文字符串", None)])
        allowed = ("", "中", "中文", "中文字", "中文字符", "中文字符串")
        for max_width in range(0, 11):
            out = truncate_line(line, max_width)
            assert out.width <= max(0, max_width)
            assert out.plain in allowed

    def test_identity_short_line(self):
        """未超宽行返回 clone（值相等，不与原对象同引用）。"""
        line = Line([StyledRun("short", None)])
        out = truncate_line(line, 80)
        assert out is not line
        assert _runs_equal(out, line)


class TestTruncateLineLinear:
    """O(n) 规模化守卫（修复前 O(n²)：8000 列约需 ~4.6s）。"""

    def test_large_line_truncation_fast(self):
        text = "中文内容" * 2000  # 16000 显示列
        line = Line([StyledRun(text, None)])
        t0 = time.perf_counter()
        out = truncate_line(line, 8000)
        elapsed = time.perf_counter() - t0
        assert out.width <= 8000
        assert elapsed < 0.5, f"truncate_line 退化为超线性（{elapsed:.3f}s / 8000 列）"


def _build_model(n_history: int, width: int = 120) -> AppModel:
    model = AppModel()
    model.width = width
    for i in range(n_history):
        model.committed_lines.append(
            Line([StyledRun(f"历史消息 {i}: " + "内容" * 40, None)])
        )
    return model


def _frame_fn(model: AppModel, width: int = 120):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    renderer = InkRenderer(stream=io.StringIO(), height=40)

    def frame():
        element = h(App, {"model": model, "width": width})
        rec.render(root, element, width, 40)
        f = _components.render_frame(root, width)
        renderer.render(f)
        return f

    return frame


class TestTruncatedPrefixCache:
    """render_frame 超宽前缀截断缓存（内容正确 + 增量 + 身份稳定）。"""

    def test_content_matches_per_line_truncation(self):
        model = _build_model(40)
        frame = _frame_fn(model)
        for step in range(6):
            if step:
                model.committed_lines.append(Line([StyledRun(f"新行 {step}", None)]))
            f = frame()
            # committed host 位于文档 y=1（其上是 TopHeader），前缀区间 [1, 1+N)
            exp = [
                truncate_line(ln, 120) if ln.width > 120 else ln
                for ln in model.committed_lines
            ]
            got = f.lines[1:1 + len(exp)]
            assert len(got) == len(exp)
            assert all(_runs_equal(a, b) for a, b in zip(exp, got))
            assert all(ln.width <= 120 for ln in f.lines)

    def test_prefix_lines_identity_stable_across_frames(self):
        """同一前缀在连续帧复用同一 Line 对象 → 行级 diff 身份短路 + 区间跳过。"""
        model = _build_model(20)
        frame = _frame_fn(model)
        f1 = frame()
        model.committed_lines.append(Line([StyledRun("新行", None)]))
        f2 = frame()
        f3 = frame()
        n = len(model.committed_lines)
        assert all(f1.lines[1 + i] is f2.lines[1 + i] for i in range(n - 1))
        assert all(f2.lines[1 + i] is f3.lines[1 + i] for i in range(n))
        assert f3._stable_prefix_offset == 1
        assert f3._stable_prefix_len == n

    def test_only_new_lines_truncated(self, monkeypatch):
        """增量：追加 1 行时只截断新增行（修复前每帧重截断全部前缀）。"""
        calls = {"n": 0}
        from src.tui.ink import helpers as _helpers

        real = _helpers.truncate_line

        def _counting(line, max_width):
            calls["n"] += 1
            return real(line, max_width)

        monkeypatch.setattr(_helpers, "truncate_line", _counting)
        model = _build_model(30)
        frame = _frame_fn(model)
        frame()
        first = calls["n"]
        assert first >= 30  # 首次全量截断（30 行超宽前缀）
        calls["n"] = 0
        model.committed_lines.append(Line([StyledRun("新行", None)]))
        frame()
        assert calls["n"] <= 2, f"增量帧仍全量重截断（{calls['n']} 次）"

    def test_width_change_rebuilds_correctly(self):
        model = _build_model(30)
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        renderer = InkRenderer(stream=io.StringIO(), height=40)

        def frame(w):
            element = h(App, {"model": model, "width": w})
            rec.render(root, element, w, 40)
            f = _components.render_frame(root, w)
            renderer.render(f)
            return f

        for w in (120, 100, 60, 40, 90, 120):
            f = frame(w)
            exp = [
                truncate_line(ln, w) if ln.width > w else ln
                for ln in model.committed_lines
            ]
            got = f.lines[1:1 + len(exp)]
            assert len(got) == len(exp)
            assert all(_runs_equal(a, b) for a, b in zip(exp, got))
            assert max(ln.width for ln in f.lines) <= w

    def test_prefix_rebuild_after_reflow_object_change(self):
        """前缀列表对象更换（reflow/重建）→ 缓存失效并重新截断（无陈旧行）。"""
        model = _build_model(15)
        frame = _frame_fn(model)
        frame()
        # 模拟 reflow：committed_lines 换成新列表对象（行更宽）
        model.committed_lines = [
            Line([StyledRun("重排后的超宽行" * 30, None)]) for _ in range(15)
        ]
        f = frame()
        exp = [
            truncate_line(ln, 120) if ln.width > 120 else ln
            for ln in model.committed_lines
        ]
        got = f.lines[1:1 + len(exp)]
        assert all(_runs_equal(a, b) for a, b in zip(exp, got))
        assert all(ln.width <= 120 for ln in f.lines)
