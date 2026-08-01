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
