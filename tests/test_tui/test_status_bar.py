"""测试 src/tui/app/status_bar.py — PERF-3 use_memo 子树缓存 + PERF-5 快照节流。

纯逻辑断言（桩 model + mock），无终端依赖。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.tui.ink.reconciler import Reconciler
from src.tui.ink.element import h
from src.tui.app.status_bar import StatusBar, _build_status_runs, _snapshot


class _Status:
    """AppModel.status 桩（PERF-3 deps 关键字段）。"""

    def __init__(self):
        self.status_active = True
        self.model_name = "test-model"
        self.tool_total = 0
        self.tool_count = 0
        self.tool_fail = 0


class _Model:
    def __init__(self):
        self.status = _Status()
        self.subagent_lines = []
        self.input_text = ""
        self.input_cursor = 0


def _render_twice_same_bucket(model):
    """同一 1s 时间桶内连续渲染两次 StatusBar。"""
    with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
        r = Reconciler()
        root = r.create_root()
        el = h(StatusBar, {"model": model, "width": 80})
        r.render(root, el, 80, 24)
        r.render(root, el, 80, 24)
    return r, root


class TestStatusBarMemo:
    """PERF-3 — use_memo 子树缓存。"""

    def test_status_bar_memoizes_runs_regression(self):
        """同状态桶内 _build_status_runs 只调用 1 次（组件树重建短路）。"""
        model = _Model()
        with patch("src.tui.app.status_bar._build_status_runs", wraps=_build_status_runs) as mock_br:
            _render_twice_same_bucket(model)
            assert mock_br.call_count == 1, (
                f"同状态桶内 _build_status_runs 应只调用 1 次，实际 {mock_br.call_count}"
            )

    def test_status_bar_recomputes_on_status_change_regression(self):
        """状态字段变化（跨桶）→ 重新计算。"""
        model = _Model()
        with patch("src.tui.app.status_bar._build_status_runs", wraps=_build_status_runs) as mock_br:
            with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
                r = Reconciler()
                root = r.create_root()
                el = h(StatusBar, {"model": model, "width": 80})
                r.render(root, el, 80, 24)
                model.status.tool_total = 3
                model.status.tool_count = 1
                r.render(root, el, 80, 24)
            assert mock_br.call_count == 2

    def test_status_bar_renders_without_error_regression(self):
        """StatusBar 渲染不抛异常（组件可被 reconciler 调和）。"""
        model = _Model()
        r, root = _render_twice_same_bucket(model)
        assert root.child is not None

    def test_fade_not_frozen_within_bucket_regression(self):
        """同一 1s 桶内渐显推进 0.2s → _build_status_runs 再次调用且 dot 色号变化。

        BEAUTY-1：修复前 use_memo deps 仅含 1s 时间桶（不含 dot_elapsed），同桶内
        渐显冻结、桶边界跳变；修复后渐显窗口按 0.1s 桶刷新（平滑渐显），
        渐显结束后回 1s 桶（PERF-3 缓存语义保持）。
        """
        model = _Model()
        # render1: ref=100.0 / elapsed=100.0 / deps=100.0 / time_glow=100.0；
        # render2: elapsed=100.2 / deps=100.2 / time_glow=100.2（同一 1s 桶内推进 0.2s）
        # 注：patch 作用于全局 time 模块，time_glow（_theme）也消费同一时间序列。
        # P2-12：side_effect 对剩余调用返回固定值（100.2）——渲染路径任何新增
        # 单调时钟调用不再触发 StopIteration（修复前依赖精确次数消费，脆弱）。
        times = iter([100.0, 100.0, 100.0, 100.0, 100.2, 100.2, 100.2])
        captured: list = []

        def _capture(*args, **kwargs):
            result = _build_status_runs(*args, **kwargs)
            captured.append(result)
            return result

        with patch("src.tui.app.status_bar._snapshot", return_value={}):
            with patch("src.tui.app.status_bar.time.monotonic", side_effect=lambda: next(times, 100.2)):
                with patch("src.tui.app.status_bar._build_status_runs", side_effect=_capture) as mock_br:
                    r = Reconciler()
                    root = r.create_root()
                    el = h(StatusBar, {"model": model, "width": 80})
                    r.render(root, el, 80, 24)
                    r.render(root, el, 80, 24)
                    assert mock_br.call_count == 2, (
                        f"渐显窗口内同 1s 桶推进 0.2s 应重算，实际 {mock_br.call_count}"
                    )
        # dot 色号变化（渐显插值推进：elapsed 0.0 → start 238；0.2 → 插值色）
        assert len(captured) == 2
        assert captured[0][0].style.fg != captured[1][0].style.fg, (
            f"dot 色号应随渐显推进变化: {captured[0][0].style.fg} vs {captured[1][0].style.fg}"
        )

    def test_model_name_change_resets_fade_regression(self):
        """BEAUTY-1 完善 — 切换模型名（Ctrl+N）→ 模型点渐显重置。

        修复前 fade 键仅含 ``status_active``——model_name 从 A 切到 B 时旧
        fade 状态残留，新模型名直接以呼吸色显示（无渐显过渡）；修复后 fade
        键含 model_name，切换后 dot_elapsed 从 0 重新渐显（dot 色号回起始
        暗色 238）。
        """
        model = _Model()
        times = iter([100.0, 100.0, 100.2, 100.2])
        captured: list = []

        def _capture(*args, **kwargs):
            result = _build_status_runs(*args, **kwargs)
            captured.append(result)
            return result

        with patch("src.tui.app.status_bar._snapshot", return_value={}):
            with patch("src.tui.app.status_bar.time.monotonic", side_effect=lambda: next(times, 100.2)):
                with patch("src.tui.app.status_bar._build_status_runs", side_effect=_capture) as mock_br:
                    r = Reconciler()
                    root = r.create_root()
                    el = h(StatusBar, {"model": model, "width": 80})
                    r.render(root, el, 80, 24)
                    # 切换模型名 → fade 重置（dot_elapsed 归零重新渐显）
                    model.status.model_name = "new-model"
                    r.render(root, el, 80, 24)
                    assert mock_br.call_count == 2, (
                        f"model_name 变化应触发重算，实际 {mock_br.call_count}"
                    )
        # 两次渲染 dot_elapsed 均从 0 开始（fade 重置）→ dot 色号均 = 起始暗色 238
        assert captured[0][0].style.fg == 238, (
            f"首次渲染 dot 应为起始暗色 238，实际 {captured[0][0].style.fg}"
        )
        assert captured[1][0].style.fg == 238, (
            f"model_name 切换后渐显应重置（dot 回起始暗色 238），"
            f"实际 {captured[1][0].style.fg}"
        )


class TestSnapshotTTLConstant:
    """方向D 步骤16 — 快照 TTL 常量化（_SNAPSHOT_TTL）。"""

    def test_snapshot_ttl_constant_exists_positive(self):
        """TTL 常量存在且 >0（显示节奏与快照对齐）。"""
        import src.tui.app.status_bar as sb
        assert hasattr(sb, "_SNAPSHOT_TTL")
        assert sb._SNAPSHOT_TTL > 0

    def test_snapshot_uses_ttl_constant(self):
        """_snapshot 源码引用 _SNAPSHOT_TTL（非硬编码 1.0）。"""
        import inspect
        import src.tui.app.status_bar as sb
        assert sb._SNAPSHOT_TTL == 1.0  # 语义不变（≤1Hz）
        src = inspect.getsource(sb._snapshot)
        assert "_SNAPSHOT_TTL" in src


class TestSnapshotThrottle:
    """PERF-5 — _snapshot() TTL 缓存（≤1Hz 查询底层函数）。"""

    def _reset_cache(self):
        import src.tui.app.status_bar as sb
        sb._snapshot_cache = (0.0, {})

    def test_snapshot_throttled_regression(self):
        """1s 内连续两次 _snapshot 查询底层函数仅 1 次。"""
        self._reset_cache()
        from src.tui._snapshot import _get_snapshot
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            with patch("src.tui._snapshot._get_snapshot", return_value=lambda: {"x": 1}) as mock_fn:
                _snapshot()
                _snapshot()
                assert mock_fn.call_count == 1, (
                    f"1s 内 _snapshot 应只查询底层 1 次，实际 {mock_fn.call_count}"
                )

    def test_snapshot_recomputes_after_ttl_regression(self):
        """超过 1s TTL 后重新查询底层函数。"""
        self._reset_cache()
        from src.tui._snapshot import _get_snapshot

        times = iter([100.0, 100.0, 101.5])  # 第三次超出 1s
        with patch("src.tui.app.status_bar.time.monotonic", side_effect=lambda: next(times)):
            with patch("src.tui._snapshot._get_snapshot", return_value=lambda: {"x": 1}) as mock_fn:
                _snapshot()
                _snapshot()
                _snapshot()
                assert mock_fn.call_count == 2

    def test_snapshot_returns_cached_data_regression(self):
        """TTL 内返回缓存 data（值一致）。"""
        self._reset_cache()
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            with patch("src.tui._snapshot._get_snapshot", return_value=lambda: {"v": 7}):
                d1 = _snapshot()
            # 同桶：命中缓存（底层函数不再被查询）
            with patch("src.tui._snapshot._get_snapshot", return_value=lambda: {"v": 99}) as mock_fn:
                d2 = _snapshot()
                mock_fn.assert_not_called()
            assert d2 == {"v": 7}  # 缓存数据（非新查询）
            assert d1 == d2


class TestStatusBarTruncate:
    """方向4 — 状态行溢出截断（超长 runs 截断至 width，修复前静默裁剪）。"""

    def test_overflow_status_line_truncated(self):
        """超长状态 runs → 输出行宽度 ≤ width。"""
        from src.tui.ink.components import render_frame
        model = _Model()
        model.status.model_name = "M" * 200  # 超长模型名（status_active=False → model_part）
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            r = Reconciler()
            root = r.create_root()
            el = h(StatusBar, {"model": model, "width": 80})
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
        # 状态行（第二行）宽度 ≤ 80（截断生效）
        status_line = frame.lines[1]
        assert status_line.width <= 80, (
            f"状态行宽度应 ≤ 80，实际 {status_line.width}"
        )

    def test_fit_status_line_unchanged(self):
        """未超宽状态行不变（回归：截断不破坏正常显示）。"""
        from src.tui.ink.components import render_frame
        model = _Model()
        model.status.model_name = "test-model"
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            r = Reconciler()
            root = r.create_root()
            el = h(StatusBar, {"model": model, "width": 80})
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
        status_line = frame.lines[1]
        assert "test-model" in status_line.plain
        assert status_line.width <= 80


class TestStatusBarSeparatorWidth:
    """方向6 — 分隔线宽度统一（铺满 width，与状态行缩进基准一致）。"""

    def test_separator_width_equals_width_regression(self):
        """分隔线行宽 == width（修复前 width-2 与状态行 col2 缩进不一致）。"""
        from src.tui.ink.components import render_frame
        model = _Model()
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            r = Reconciler()
            root = r.create_root()
            el = h(StatusBar, {"model": model, "width": 80})
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
        sep_line = frame.lines[0]
        assert sep_line.width == 80, (
            f"分隔线行宽应 == width(80)，实际 {sep_line.width}"
        )
        assert sep_line.plain.startswith("\u2501")

    def test_status_line_prefix_two_cols_regression(self):
        """状态行前缀 2 列 + 内容 ≤ width（分隔线全宽、状态行缩进 2 列）。"""
        from src.tui.ink.components import render_frame
        model = _Model()
        with patch("src.tui.app.status_bar.time.monotonic", return_value=100.0):
            r = Reconciler()
            root = r.create_root()
            el = h(StatusBar, {"model": model, "width": 80})
            r.render(root, el, 80, 24)
            frame = render_frame(root, 80)
        status_line = frame.lines[1]
        assert status_line.width <= 80
        # 前缀 2 列（空）+ 状态指示字符（`·` 空闲 / spinner 活跃）+ 模型名
        assert status_line.plain.startswith("  ")
        assert status_line.plain[2] != " "
        assert "test-model" in status_line.plain
