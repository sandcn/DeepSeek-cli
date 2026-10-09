"""Mermaid 流式渲染性能断言的鲁棒化回归测试。

需求（2026-10-10）：``tests/test_stream_markdown_render_fixes_v6.py`` 的
``test_mermaid_stream_budget`` 原先只用「单次测量的绝对耗时 < 0.5s」判定——
无负载实测 0.21~0.30s、16 worker 并行满负载 0.51s，属余量不足的脆弱计时
断言（同一提交在满负载下偶发失败，与业务改动无关）。

修复：① 总耗时改为「预热 + 多轮取最小值」（``_min_elapsed``）并留足余量；
② 追加「后段单帧成本 vs 前段单帧成本」的复杂度判定
（``_frame_worst_costs``）——节流生效时后段单帧不随源码长度增长，且该
比值与 CPU 负载无关。

本文件同时以**行为级**断言固化 Mermaid 预览的节流 / 结果缓存契约
（不依赖时间，避免再次引入脆弱判定）。
"""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.test_stream_markdown_render_fixes_v6 as v6
from src.renderer.ansi import AnsiStreamRenderer
from src.renderer.ansi.mermaid import clear_mermaid_cache, render_mermaid_block
from tests.test_stream_markdown_render_fixes_v6 import (
    _frame_worst_costs,
    _min_elapsed,
    _one_shot,
    _stream,
)

V6_TEST_FILE = Path(__file__).resolve().parent / "test_stream_markdown_render_fixes_v6.py"

THROTTLE_KEY = ("mermaid",)


def _long_mermaid_src(nodes: int = 60) -> str:
    body = "".join(f"  A{i}[节点{i}]-->B{i}[节点{i}]\n" for i in range(nodes))
    return "```mermaid\ngraph TD\n" + body + "```\n\n"


# ══════════════════════════════════════════════════════════
# 计时断言的鲁棒化（测量辅助）
# ══════════════════════════════════════════════════════════


class TestRobustTimingHelper:

    def test_min_elapsed_takes_minimum_of_rounds(self, monkeypatch):
        """``_min_elapsed`` 须预热一轮 + 多轮取最小值（剔除负载抖动）。"""
        calls: list[int] = []

        def fake_elapsed(src, chunk=4, width=100):
            calls.append(chunk)
            return 0.9 - 0.2 * len(calls)

        monkeypatch.setattr(v6, "_elapsed", fake_elapsed)
        got = v6._min_elapsed("x", chunk=8, rounds=3)
        assert got == pytest.approx(0.1)
        assert len(calls) == 4, "应为 1 轮预热 + 3 轮测量"
        assert all(c == 8 for c in calls)

    def test_min_elapsed_rounds_parameter(self, monkeypatch):
        """轮数参数生效（rounds=1 时只测 1 轮，外加预热）。"""
        calls: list[int] = []

        def fake_elapsed(src, chunk=4, width=100):
            calls.append(chunk)
            return float(len(calls))

        monkeypatch.setattr(v6, "_elapsed", fake_elapsed)
        assert v6._min_elapsed("x", rounds=1) == pytest.approx(2.0)
        assert len(calls) == 2

    def test_frame_worst_costs_shape(self):
        """``_frame_worst_costs`` 返回（前段, 后段）单帧最差成本，均为非负。"""
        early, late = _frame_worst_costs(_long_mermaid_src(8), chunk=32)
        assert early >= 0.0
        assert late >= 0.0


class TestMermaidBudgetTestIsRobust:
    """mermaid 计时断言本身须使用鲁棒判定（元测试，防回退）。"""

    @pytest.fixture(scope="class")
    def budget_test_source(self) -> str:
        text = V6_TEST_FILE.read_text(encoding="utf-8")
        start = text.index("def test_mermaid_stream_budget")
        end = text.index("def ", start + 10)
        return text[start:end]

    def test_not_single_absolute_measurement(self, budget_test_source: str):
        """不得再退回「单次测量 + 绝对阈值 0.5s」的脆弱写法。"""
        assert "assert elapsed < 0.5" not in budget_test_source
        assert "_elapsed(src, chunk=32)" not in budget_test_source

    def test_uses_min_elapsed(self, budget_test_source: str):
        """总耗时须用多轮取最小的 ``_min_elapsed``。"""
        assert "_min_elapsed(src, chunk=8)" in budget_test_source

    def test_uses_relative_frame_cost(self, budget_test_source: str):
        """须有「后段单帧 / 前段单帧」复杂度判定（与 CPU 负载无关）。"""
        assert "_frame_worst_costs(src, chunk=8)" in budget_test_source
        assert "early * 20" in budget_test_source


# ══════════════════════════════════════════════════════════
# Mermaid 结果缓存（同源码复用布局结果）
# ══════════════════════════════════════════════════════════


class TestMermaidResultCache:

    def test_same_source_reuses_cached_result(self):
        """同一源码（含 dropped）重复渲染复用同一结果对象。"""
        clear_mermaid_cache()
        src = "graph TD\n  A-->B\n"
        first = render_mermaid_block(src)
        assert render_mermaid_block(src) is first
        assert render_mermaid_block(src, dropped=0) is first

    def test_changed_source_and_dropped_are_distinct_entries(self):
        """源码变化 / 截断行数不同 → 不同缓存条目。"""
        clear_mermaid_cache()
        src = "graph TD\n  A-->B\n"
        first = render_mermaid_block(src)
        assert render_mermaid_block(src + "  B-->C\n") is not first
        assert render_mermaid_block(src, dropped=3) is not first

    def test_clear_cache_forces_rebuild(self):
        """``clear_mermaid_cache`` 后重建（测试用清理接口有效）。"""
        clear_mermaid_cache()
        src = "graph LR\n  X-->Y\n"
        first = render_mermaid_block(src)
        clear_mermaid_cache()
        assert render_mermaid_block(src) is not first


# ══════════════════════════════════════════════════════════
# 流式预览节流（行为级：对象复用 / 重排时机）
# ══════════════════════════════════════════════════════════


class TestMermaidPreviewThrottle:

    def test_throttle_state_recorded_on_stream(self):
        """未闭合 Mermaid 块流式期间记录节流槽（行数 + 首行锚点）。"""
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\ngraph TD\n" + "  A0[节点0]-->B0[节点0]\n" * 60)
        state = r._preview_throttle.get(THROTTLE_KEY)
        assert state is not None, "缺少 Mermaid 节流槽"
        total, first_line, rows = state
        assert total >= 60
        assert first_line == "graph TD"
        assert rows

    def test_small_delta_reuses_preview_rows(self):
        """小增量（含仅活动行增长）复用上次预览行对象（节流生效）。"""
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\ngraph TD\n" + "  A0[节点0]-->B0[节点0]\n" * 60)
        rows0 = r._preview_throttle[THROTTLE_KEY][2]
        total0 = r._preview_throttle[THROTTLE_KEY][0]
        # 仅活动行增长（行数不变）
        r.write("  A60[节点60]-->B60[节点60]")
        assert r._preview_throttle[THROTTLE_KEY][2] is rows0
        # 新增 1 行（< 步长 total//8）同样复用
        r.write("\n  A61[节点61]-->B61[节点61]")
        state = r._preview_throttle[THROTTLE_KEY]
        assert state[2] is rows0
        assert state[0] >= total0

    def test_large_delta_rerenders(self):
        """增量达到步长（total//8）→ 重新布局（不复用陈旧图形）。"""
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\ngraph TD\n" + "  A0[节点0]-->B0[节点0]\n" * 60)
        rows0 = r._preview_throttle[THROTTLE_KEY][2]
        total0 = r._preview_throttle[THROTTLE_KEY][0]
        extra = "".join(f"  C{i}[节点{i}]-->D{i}[节点{i}]\n" for i in range(total0 // 8 + 2))
        r.write("\n" + extra)
        state = r._preview_throttle[THROTTLE_KEY]
        assert state[2] is not rows0, "大增量应重排"
        assert state[0] > total0

    def test_first_line_change_rerenders(self):
        """换块（首行变化）→ 立即重排并更新首行锚点。"""
        r = AnsiStreamRenderer(width=100)
        r.write("```mermaid\ngraph TD\n" + "  A0[节点0]-->B0[节点0]\n" * 60)
        rows0 = r._preview_throttle[THROTTLE_KEY][2]
        r.write("```\n\n```mermaid\ngraph LR\n  X0[x]-->Y0[y]\n")
        state = r._preview_throttle.get(THROTTLE_KEY)
        assert state is not None
        assert state[1] == "graph LR"
        assert state[2] is not rows0

    def test_close_clears_throttle(self):
        """块闭合后清空节流槽（下一块不得复用上一块图形）。"""
        r = AnsiStreamRenderer(width=100)
        body = "".join(f"  A{i}[节点{i}]-->B{i}[节点{i}]\n" for i in range(10))
        r.write("```mermaid\ngraph TD\n" + body)
        assert r._preview_throttle.get(THROTTLE_KEY) is not None
        r.write("```\n\n")
        r.close()
        assert r._preview_throttle.get(THROTTLE_KEY) is None

    def test_stream_commit_matches_one_shot(self):
        """流式提交内容与一次性渲染一致（节流只影响中间预览）。"""
        src = _long_mermaid_src(20)
        assert _stream(src, chunk=32) == _one_shot(src)


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
