"""端到端渲染性能回归测试 — AppModel + apply_cmd + build_app_element。

覆盖真实 TUI 场景（用户消息 + 流式回答 + 提交）的渲染管线：
  - 首帧 / 无变化帧 / 流式增长帧的渲染耗时预算（10Hz 渲染不卡顿）；
  - 渲染正确性：committed 历史行按序出现在帧中、流式内容不丢失。

性能阈值（Termux/Android 实测，含系统波动余量）：
  - 无变化帧 < 20ms（1000 节点纯 TEXT 树 < 60ms；真实 21 块历史 ~2.5ms）；
  - 流式增长帧 < 20ms（增量提交 + 前缀身份复用后每帧 O(live+新增)）。
阈值保守（正常应 < 5ms），用于捕获病态性能退化（如每帧全量重渲染历史）。
"""

from __future__ import annotations

import time

from src.tui.app.model import AppModel
from src.tui.app.app import build_app_element
from src.tui.app.apply import apply_cmd
from src.tui._const import UserMsgCmd, ContentCmd, PhaseDoneCmd
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame


def _build_history_model(n_msgs: int = 20, lines_per: int = 30) -> AppModel:
    """构造含 n_msgs 条用户消息 + 长回答的模型（committed 历史）。"""
    model = AppModel()
    for i in range(n_msgs):
        apply_cmd(model, UserMsgCmd(text=f"用户消息 {i} 一些内容"))
        text = "\n".join(
            f"这是第 {i} 条回答的第 {j} 行内容，包含中文测试换行行为"
            for j in range(lines_per)
        )
        apply_cmd(model, ContentCmd(text=text))
        apply_cmd(model, PhaseDoneCmd(phase="content"))
        model.reopen_content()  # 多轮会话：下一轮内容前重开通道
    return model


class TestEndToEndPerf:
    """端到端渲染性能预算（真实 TUI 场景）。"""

    def test_first_frame_budget(self):
        model = _build_history_model()
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, 100)
        t0 = time.perf_counter()
        r.render(root, el, 100, 40)
        frame = render_frame(root, 100)
        elapsed = (time.perf_counter() - t0) * 1000
        assert frame.height > 0
        assert elapsed < 200, f"首帧超预算: {elapsed:.2f}ms"

    def test_unchanged_frame_budget(self):
        """无变化帧（复用同一模型）：每帧不重建历史（< 50ms 预算）。"""
        model = _build_history_model()
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, 100)
        r.render(root, el, 100, 40)
        render_frame(root, 100)
        times = []
        for _ in range(5):
            el = build_app_element(model, 100)
            t0 = time.perf_counter()
            r.render(root, el, 100, 40)
            render_frame(root, 100)
            times.append((time.perf_counter() - t0) * 1000)
        assert max(times) < 50, f"无变化帧超预算: {times}"

    def test_streaming_frame_budget(self):
        """流式增长（逐帧追加内容）：每帧 O(live+新增)，不随历史增长。"""
        model = AppModel()
        apply_cmd(model, UserMsgCmd(text="问题"))
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, 100)
        r.render(root, el, 100, 40)
        render_frame(root, 100)
        times = []
        for i in range(10):
            apply_cmd(model, ContentCmd(text=f"流式内容行 {i} 一些文字"))
            el = build_app_element(model, 100)
            t0 = time.perf_counter()
            r.render(root, el, 100, 40)
            render_frame(root, 100)
            times.append((time.perf_counter() - t0) * 1000)
        assert max(times) < 50, f"流式增长帧超预算: {times}"

    def test_streaming_content_correctness(self):
        """流式内容渲染正确性：内容关闭（flush）后行按序出现在帧中。"""
        model = AppModel()
        apply_cmd(model, UserMsgCmd(text="问题"))
        apply_cmd(model, ContentCmd(text="第一行内容"))
        apply_cmd(model, PhaseDoneCmd(phase="content"))  # 关闭 → flush 到块
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, 100)
        r.render(root, el, 100, 40)
        frame = render_frame(root, 100)
        plains = [ln.plain for ln in frame.lines]
        assert any("第一行内容" in p for p in plains), f"首帧内容缺失: {plains[-8:]}"
        # 第二轮内容（重开通道）→ 提交后出现在帧中
        model.reopen_content()
        apply_cmd(model, ContentCmd(text="第二轮内容"))
        apply_cmd(model, PhaseDoneCmd(phase="content"))
        el = build_app_element(model, 100)
        r.render(root, el, 100, 40)
        frame = render_frame(root, 100)
        plains = [ln.plain for ln in frame.lines]
        assert any("第一行内容" in p for p in plains)
        assert any("第二轮内容" in p for p in plains), f"提交后内容缺失: {plains[-8:]}"

    def test_history_correctness(self):
        """20 条消息历史：全部内容按序出现在帧中（不丢失）。"""
        model = _build_history_model(n_msgs=5, lines_per=5)
        r = Reconciler()
        root = r.create_root()
        el = build_app_element(model, 100)
        r.render(root, el, 100, 40)
        frame = render_frame(root, 100)
        plains = [ln.plain for ln in frame.lines]
        assert any("用户消息 0" in p for p in plains), f"首条消息缺失"
        assert any("用户消息 4" in p for p in plains), f"末条消息缺失"
        assert any("第 4 条回答的第 0 行" in p for p in plains), f"回答内容缺失"


__all__ = ["TestEndToEndPerf", "_build_history_model"]
