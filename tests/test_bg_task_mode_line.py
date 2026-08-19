"""后台任务计数显示迁移 + 主 Agent 上下文使用百分比（2026-08-19 用户需求）单元测试。

需求：
  1. 删除 status 行（状态栏）的后台 bash / subagent 显示；
  2. 后台任务信息迁至「空模式」模式行**行首**；
  3. 显示格式 ``bash · 1 · subagent · 1``（bash/subagent 分别计数，
     没有就不显示；两者都无时行首不显示前缀）。
  4. 行首最前面加 mainagent 上下文使用百分比（``main · 45%``）——
     经 context_manager 全局快照 O(1) 无锁读取（性能好，渲染每帧零计算）。

覆盖：
  - ``_build_mode_line`` 行首前缀（main · N% · bash · N · subagent · N / 无则不显示）
  - ``_build_lines`` 从 props/全局快照读取并在模式行行首渲染（集成）
  - ``_input_snap_key`` 计数与上下文百分比进 use_memo deps（变化即时刷新）
  - ``_build_status_runs`` 状态栏不再显示后台任务（删除验证）
  - ``apply._do_bg_bash_count`` bash 与 subagent 分列更新
  - ``EventDispatcher._on_bg_bash_changed`` 分列聚合
  - ``BaseAgent`` 计数拆分与事件发布（bash/subagent 分列）
  - ``context_manager`` 全局上下文使用率快照（set/get/缓存同步点写入）
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from src.tui.app.input_area import _build_mode_line, _build_lines, _input_snap_key
from src.tui.app.status_bar import _build_status_runs
from src.tui.app.apply import apply_cmd
from src.tui.app.model import AppModel
from src.tui.app._state_types import StatusState


# ── 1. 模式行行首显示（_build_mode_line） ────────────────

class TestModeLineBgPrefix:
    """模式行行首前缀（main · N% · bash · N · subagent · N）。"""

    def _text(self, line) -> str:
        return "".join(r.text for r in line.runs)

    def test_bash_and_subagent_both_shown(self):
        """bash 与 subagent 都有：行首 ``bash · 1 · subagent · 1``。"""
        line = _build_mode_line(80, True, bash_count=1, subagent_count=1)
        text = self._text(line)
        assert "bash \u00b7 1 \u00b7 subagent \u00b7 1" in text
        assert "空模式" in text          # 右侧模式文本保留
        assert text.startswith("bash \u00b7 1")  # 行首为 bash

    def test_only_bash(self):
        """仅 bash：``bash · 2``，无 subagent 段。"""
        line = _build_mode_line(80, False, bash_count=2, subagent_count=0)
        text = self._text(line)
        assert "bash \u00b7 2" in text
        assert "subagent" not in text
        assert "标准模式" in text

    def test_only_subagent(self):
        """仅 subagent：``subagent · 3``，无 bash 段。"""
        line = _build_mode_line(80, True, bash_count=0, subagent_count=3)
        text = self._text(line)
        assert "subagent \u00b7 3" in text
        assert "bash" not in text
        assert "空模式" in text

    def test_none_hidden(self):
        """都无：行首不显示前缀（左侧空白，与旧版一致）。"""
        line = _build_mode_line(80, False, bash_count=0, subagent_count=0)
        text = self._text(line)
        assert "bash" not in text and "subagent" not in text
        assert "标准模式" in text
        assert text.strip() == "标准模式"

    def test_line_width_constant(self):
        """行宽恒 = width（行级 diff 行宽不变量）。"""
        for width in (80, 40, 24, 10):
            line = _build_mode_line(width, True, bash_count=1, subagent_count=1)
            assert line.width == width, f"width={width} 行宽 {line.width}"

    def test_negative_counts_hidden(self):
        """异常负计数不显示对应项（钳制语义）。"""
        line = _build_mode_line(80, False, bash_count=-1, subagent_count=0)
        text = self._text(line)
        assert "bash" not in text
        line2 = _build_mode_line(80, False, bash_count=0, subagent_count=-5)
        assert "subagent" not in self._text(line2)

    def test_mode_text_still_right_aligned(self):
        """模式文本仍位于最右侧（右侧填充空白）。"""
        line = _build_mode_line(80, True, bash_count=1, subagent_count=1)
        text = self._text(line)
        assert text.rstrip().endswith("空模式")
        # 前缀左侧 + 模式文本右侧
        assert text.index("bash") < text.index("空模式")

    def test_narrow_width_prefix_truncated(self):
        """极窄屏（width < 前缀宽）前缀截断不溢出（行宽不变量保持）。"""
        line = _build_mode_line(10, False, bash_count=12, subagent_count=34)
        assert line.width <= 10
        text = self._text(line)
        assert text.startswith("bash")  # 截断保留前缀开头

    # ── mainagent 上下文使用百分比（用户需求 2026-08-19） ──

    def test_ctx_percent_leading(self):
        """ctx 有值：行首最前 ``main · 45.0%``（1 位小数）。"""
        line = _build_mode_line(80, True, 45.3, bash_count=1, subagent_count=1)
        text = self._text(line)
        assert text.startswith("main \u00b7 45.3%")
        assert "main \u00b7 45.3% \u00b7 bash \u00b7 1 \u00b7 subagent \u00b7 1" in text
        assert "空模式" in text

    def test_ctx_percent_only(self):
        """仅 ctx（无后台任务）：``main · 45.0%`` + 右侧模式文本。"""
        line = _build_mode_line(80, False, 45.0, 0, 0)
        text = self._text(line)
        assert text.strip().startswith("main \u00b7 45.0%")
        assert "bash" not in text and "subagent" not in text
        assert text.rstrip().endswith("标准模式")

    def test_ctx_none_hidden(self):
        """ctx=None：不显示 main 段（bash/subagent 不受影响）。"""
        line = _build_mode_line(80, True, None, bash_count=2, subagent_count=0)
        text = self._text(line)
        assert "main" not in text
        assert "bash \u00b7 2" in text

    def test_ctx_zero_shown(self):
        """ctx=0（空会话刚初始化）：显示 ``main · 0.0%``（有值即显示）。"""
        line = _build_mode_line(80, False, 0.0, 0, 0)
        text = self._text(line)
        assert "main \u00b7 0.0%" in text

    def test_ctx_one_decimal_always(self):
        """百分比恒 1 位小数（整数百分比也补 .0）。"""
        line = _build_mode_line(80, False, 45.0, 0, 0)
        assert "45.0%" in self._text(line)
        line2 = _build_mode_line(80, False, 6.26, 0, 0)
        assert "6.3%" in self._text(line2)  # 1 位小数


# ── 2. _build_lines 集成（props 计数 → 模式行行首） ───────

class TestBuildLinesBgCount:
    """_build_lines 从 props 读取计数并在模式行行首渲染。"""

    def _fiber(self, width: int = 80, bash: int = 0, subagent: int = 0):
        props = {
            "text": "",
            "cursor_pos": 0,
            "completion": None,
            "status_active": False,
            "cpu": 3,
            "mem": 12,
            "width": width,
            "bg_bash_count": bash,
            "bg_subagent_count": subagent,
        }
        return SimpleNamespace(
            props=props,
            layout_box=SimpleNamespace(w=width, x=0, y=0),
        )

    def test_mode_line_first_has_bg_prefix(self):
        fiber = self._fiber(80, bash=1, subagent=2)
        lines = _build_lines(fiber)
        texts = ["".join(r.text for r in ln.runs) for ln in lines]
        mode_line = texts[-1]
        assert "bash \u00b7 1 \u00b7 subagent \u00b7 2" in mode_line
        assert "标准模式" in mode_line

    def test_bg_count_change_rebuilds(self):
        """计数变化 → snap_key 变化 → 模式行重建（不命中旧缓存）。"""
        fiber = self._fiber(80, bash=0, subagent=0)
        lines1 = _build_lines(fiber)
        assert "bash" not in "".join(r.text for r in lines1[-1].runs)
        # 同 fiber 修改 props 计数（模拟任务注册）
        fiber.props["bg_bash_count"] = 2
        lines2 = _build_lines(fiber)
        assert "bash \u00b7 2" in "".join(r.text for r in lines2[-1].runs)

    def test_default_props_no_prefix(self):
        """props 缺省计数（旧调用方/测试）→ 无前缀，模式行正常。"""
        fiber = SimpleNamespace(
            props={"text": "", "cursor_pos": 0, "completion": None,
                   "status_active": False, "cpu": 0, "mem": 0, "width": 80},
            layout_box=SimpleNamespace(w=80, x=0, y=0),
        )
        lines = _build_lines(fiber)
        assert "标准模式" in "".join(r.text for r in lines[-1].runs)

    def test_invalid_count_values_safe(self):
        """异常计数（str/None/inf）回退 0，不中断渲染。"""
        fiber = self._fiber(80, bash="bad", subagent=float("inf"))
        lines = _build_lines(fiber)
        text = "".join(r.text for r in lines[-1].runs)
        assert "bash" not in text and "subagent" not in text
        assert "标准模式" in text

    def test_ctx_percent_from_global_snapshot(self):
        """全局快照有值时模式行行首显示 ``main · 45.0%``（bash 之前）。"""
        from src.core.context_manager import set_context_usage_percent
        set_context_usage_percent(45.0)
        try:
            fiber = self._fiber(80, bash=1, subagent=0)
            lines = _build_lines(fiber)
            text = "".join(r.text for r in lines[-1].runs)
            assert text.startswith("main \u00b7 45.0%")
            assert "main \u00b7 45.0% \u00b7 bash \u00b7 1" in text
        finally:
            set_context_usage_percent(None)

    def test_ctx_percent_none_not_shown(self):
        """全局快照 None（默认）→ 行首无 main 段。"""
        from src.core.context_manager import set_context_usage_percent
        set_context_usage_percent(None)
        try:
            fiber = self._fiber(80, bash=1, subagent=1)
            lines = _build_lines(fiber)
            text = "".join(r.text for r in lines[-1].runs)
            assert "main" not in text
            assert "bash \u00b7 1 \u00b7 subagent \u00b7 1" in text
        finally:
            set_context_usage_percent(None)

    def test_ctx_percent_change_rebuilds(self):
        """ctx 全局快照变化 → snap_key 变化 → 模式行重建。"""
        from src.core.context_manager import set_context_usage_percent
        set_context_usage_percent(None)
        try:
            fiber = self._fiber(80)
            lines1 = _build_lines(fiber)
            assert "main" not in "".join(r.text for r in lines1[-1].runs)
            set_context_usage_percent(60.0)
            lines2 = _build_lines(fiber)
            assert "main \u00b7 60.0%" in "".join(r.text for r in lines2[-1].runs)
        finally:
            set_context_usage_percent(None)


# ── 3. _input_snap_key 计数进 deps ───────────────────────

class TestInputSnapKeyBgCount:
    """计数变化 → use_memo deps 变化（InputArea 重建）。"""

    def test_bash_count_in_key(self):
        k0 = _input_snap_key({"status_active": False}, 80, 123.0)
        k1 = _input_snap_key(
            {"status_active": False, "bg_bash_count": 3}, 80, 123.0)
        assert k0 != k1

    def test_subagent_count_in_key(self):
        k0 = _input_snap_key({"status_active": False}, 80, 123.0)
        k1 = _input_snap_key(
            {"status_active": False, "bg_subagent_count": 4}, 80, 123.0)
        assert k0 != k1

    def test_time_bucket_stays_last(self):
        """时间桶仍为元组末位（既有测试兼容：读 [-1]）。"""
        now = 123.456
        key = _input_snap_key({"status_active": False}, 80, now, False)
        assert key[-1] == int(now / 0.25)

    def test_invalid_count_safe(self):
        """异常计数不抛异常（回退 0）。"""
        key = _input_snap_key(
            {"status_active": False, "bg_bash_count": "x",
             "bg_subagent_count": float("inf")}, 80, 123.0)
        assert key[-1] == int(123.0 / 0.25)

    def test_ctx_percent_in_key(self):
        """上下文百分比变化 → deps 变化（InputArea 重建即时刷新）。"""
        from src.core.context_manager import set_context_usage_percent
        set_context_usage_percent(None)
        try:
            k0 = _input_snap_key({"status_active": False}, 80, 123.0)
            set_context_usage_percent(45)
            k1 = _input_snap_key({"status_active": False}, 80, 123.0)
            assert k0 != k1
        finally:
            set_context_usage_percent(None)


# ── 4. 状态栏不再显示后台任务（删除验证） ─────────────────

class TestStatusBarNoBgTask:
    """_build_status_runs 不再输出后台任务信息。"""

    def _model(self, active: bool, bg_bash: int = 3, bg_subagent: int = 2) -> AppModel:
        model = AppModel()
        model.status.status_active = active
        model.status.model_name = "test-model"
        model.status.bg_bash_count = bg_bash
        model.status.bg_subagent_count = bg_subagent
        model._status_snapshot_cache = (
            __import__("time").monotonic(),
            {"total_tokens": 100, "elapsed_seconds": 5.0, "per_second_speed": 10.0},
        )
        return model

    def test_idle_no_bg_task_text(self):
        """空闲：bg 计数不出现（删除后状态栏仅模型名）。"""
        model = self._model(False)
        runs = _build_status_runs(model, 0.0, "\u00b7", "")
        text = "".join(r.text for r in runs)
        assert "test-model" in text
        assert "\u21bb" not in text       # 旧 ↻ 图标不存在
        assert "bash" not in text and "subagent" not in text

    def test_active_no_bg_task_text(self):
        """活跃：统计区不含后台任务计数（无 ↻/bash/subagent）。"""
        model = self._model(True)
        runs = _build_status_runs(model, 0.0, "\u00b7", "")
        text = "".join(r.text for r in runs)
        assert "\u21bb" not in text
        assert "bash" not in text and "subagent" not in text

    def test_phase_labels_all_removed(self):
        """所有主 Agent 阶段标签（…思考/…回答/…解析/未知原文）均已删除。

        2026-08-19 用户需求：answering/thinking/parsing 及其他阶段在状态栏
        均不显示阶段标签（模型名后无阶段提示）。
        """
        for phase in ("answering", "thinking", "parsing", "unknown_x"):
            model = self._model(True)
            model.status.main_phase = phase
            runs = _build_status_runs(model, 0.0, "\u00b7", "")
            text = "".join(r.text for r in runs)
            assert "\u2026\u56de\u7b54" not in text   # …回答
            assert "\u2026\u601d\u8003" not in text   # …思考
            assert "\u2026\u89e3\u6790" not in text   # …解析
            assert f"\u2026{phase}" not in text        # …未知原文
            assert "test-model" in text


# ── 5. apply._do_bg_bash_count 分列更新 ───────────────────

class TestApplyBgCountSplit:
    """BgBashCountCmd 同时更新 bash 与 subagent 计数。"""

    def test_updates_both_counts(self):
        from src.tui._const import BgBashCountCmd
        model = AppModel()
        apply_cmd(model, BgBashCountCmd(count=2, subagent_count=3))
        assert model.status.bg_bash_count == 2
        assert model.status.bg_subagent_count == 3

    def test_negative_clamped(self):
        from src.tui._const import BgBashCountCmd
        model = AppModel()
        apply_cmd(model, BgBashCountCmd(count=-1, subagent_count=-2))
        assert model.status.bg_bash_count == 0
        assert model.status.bg_subagent_count == 0

    def test_invalid_values_fallback_zero(self):
        from src.tui._const import BgBashCountCmd
        model = AppModel()
        model.status.bg_bash_count = 5
        model.status.bg_subagent_count = 5
        apply_cmd(model, BgBashCountCmd(count=float("inf"), subagent_count="bad"))
        assert model.status.bg_bash_count == 0
        assert model.status.bg_subagent_count == 0

    def test_status_state_has_subagent_field(self):
        """StatusState 新增 bg_subagent_count 字段（默认 0）。"""
        st = StatusState()
        assert st.bg_subagent_count == 0
        assert st.bg_bash_count == 0


# ── 6. dispatcher 分列聚合 ────────────────────────────────

class TestDispatcherBgCountSplit:
    """EventDispatcher._on_bg_bash_changed 分列聚合 bash/subagent。"""

    def _make_dispatcher(self):
        from src.tui._dispatcher import EventDispatcher
        from src.tui.events.event_types import BackgroundTaskChangedEvent
        pushed = []

        class _FakeEvent(BackgroundTaskChangedEvent):
            pass

        d = EventDispatcher(pushed.append)
        return d, pushed, _FakeEvent

    def test_main_and_subagent_aggregated_separately(self):
        """main 发布 bash=2，subagent 发布 bash=1+sa=3 → bash=3 / subagent=3。"""
        from src.tui._const import BgBashCountCmd
        from src.tui.events.event_types import BackgroundTaskChangedEvent
        d, pushed, _ = self._make_dispatcher()
        d._on_bg_bash_changed(BackgroundTaskChangedEvent(
            label="main", count=2, subagent_count=0, source="agent"))
        d._on_bg_bash_changed(BackgroundTaskChangedEvent(
            label="agent-1", count=1, subagent_count=3, source="agent"))
        cmds = [c for c in pushed if isinstance(c, BgBashCountCmd)]
        assert cmds, "应推送 BgBashCountCmd"
        last = cmds[-1]
        assert last.count == 3          # 总 bash = 2 + 1
        assert last.subagent_count == 3  # 总 subagent = 3

    def test_zero_clears_label(self):
        """计数归零 → 对应 label 移除，总数为其余 label 之和。"""
        from src.tui._const import BgBashCountCmd
        from src.tui.events.event_types import BackgroundTaskChangedEvent
        d, pushed, _ = self._make_dispatcher()
        d._on_bg_bash_changed(BackgroundTaskChangedEvent(
            label="main", count=2, subagent_count=1, source="agent"))
        d._on_bg_bash_changed(BackgroundTaskChangedEvent(
            label="main", count=0, subagent_count=0, source="agent"))
        cmds = [c for c in pushed if isinstance(c, BgBashCountCmd)]
        last = cmds[-1]
        assert last.count == 0
        assert last.subagent_count == 0

    def test_invalid_count_defensive(self):
        """异常计数回退 0（不抛异常、不污染聚合表）。"""
        from src.tui.events.event_types import BackgroundTaskChangedEvent
        d, pushed, _ = self._make_dispatcher()
        d._on_bg_bash_changed(BackgroundTaskChangedEvent(
            label="main", count="bad", subagent_count=None, source="agent"))
        assert d._bg_bash_counts == {}
        assert d._bg_subagent_counts == {}


# ── 7. BaseAgent 计数拆分与事件发布 ──────────────────────

class TestBaseAgentBgCountSplit:
    """_count_running_bash_tasks / _count_running_subagent_tasks / 事件发布。"""

    def _agent(self) -> "object":
        from src.core.base_agent import BaseAgent
        agent = BaseAgent()
        agent.label = "main"
        agent._subagent_tasks = {}
        return agent

    def test_bash_and_subagent_count_separate(self):
        agent = self._agent()
        agent._background_tasks["bg-1"] = {"done": False}
        agent._background_tasks["bg-2"] = {"done": True}
        agent._subagent_tasks["sa-1"] = {"done": False}
        agent._subagent_tasks["sa-2"] = {"done": True}
        assert agent._count_running_bash_tasks() == 1
        assert agent._count_running_subagent_tasks() == 1

    def test_count_no_tables(self):
        agent = self._agent()
        agent._background_tasks = {}
        agent._subagent_tasks = {}
        assert agent._count_running_bash_tasks() == 0
        assert agent._count_running_subagent_tasks() == 0

    def test_publish_event_carries_both_counts(self):
        from src.tui.events.event_types import BackgroundTaskChangedEvent
        agent = self._agent()
        agent._background_tasks["bg-1"] = {"done": False}
        agent._subagent_tasks["sa-1"] = {"done": False}
        captured = []

        class _FakePort:
            def publish_event(self, event):
                captured.append(event)

        agent._event_port = _FakePort()
        agent._publish_background_task_event()
        assert captured, "应发布 BackgroundTaskChangedEvent"
        ev = captured[-1]
        assert isinstance(ev, BackgroundTaskChangedEvent)
        assert ev.label == "main"
        assert ev.count == 1        # bash 计数
        assert ev.subagent_count == 1  # subagent 计数

    def test_publish_without_port_noop(self):
        """无 _event_port → 发布空操作（不抛异常）。"""
        agent = self._agent()
        agent._background_tasks["bg-1"] = {"done": False}
        agent._publish_background_task_event()  # 不抛异常


# ── 8. app.py 传 props（真实组件链路） ────────────────────

class TestAppPropsBgCount:
    """App._normal_bottom_area 向 InputArea 传 bg 计数 props。"""

    def test_normal_bottom_area_passes_counts(self):
        from src.tui.app.app import _normal_bottom_area
        model = AppModel()
        model.status.bg_bash_count = 2
        model.status.bg_subagent_count = 4
        area = _normal_bottom_area(model, 80)
        input_el = area[-1]
        assert input_el.props["bg_bash_count"] == 2
        assert input_el.props["bg_subagent_count"] == 4


# ── 9. InputArea 真实渲染（Reconciler 链路） ──────────────

class TestInputAreaRealRender:
    """真实组件渲染：模式行行首显示 bash · N · subagent · N。"""

    def _render(self, rec, root, props, width: int = 80):
        from src.tui.ink.element import h
        from src.tui.ink.layout import layout_tree
        from src.tui.ink import components as _components
        from src.tui.app.input_area import InputArea
        el = h(InputArea, props)
        rec.render(root, el, width, 40)
        layout_tree(root, width)
        return _components.render_frame(root, width)

    def _frame_texts(self, frame) -> list:
        return ["".join(r.text for r in ln.runs) for ln in frame.lines]

    def test_mode_line_bg_prefix_rendered(self):
        from src.tui.ink.reconciler import Reconciler
        from src.tui.app.input_area import InputArea
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        props = {
            "text": "", "cursor_pos": 0, "prompt": "> ", "completion": None,
            "status_active": False, "cpu": 0, "mem": 0, "width": 80,
            "history_search": None, "bg_bash_count": 1, "bg_subagent_count": 2,
        }
        frame = self._render(rec, root, props)
        joined = "\n".join(self._frame_texts(frame))
        assert "bash \u00b7 1 \u00b7 subagent \u00b7 2" in joined
        assert "标准模式" in joined

    def test_count_change_rerender_updates_prefix(self):
        """同 root 二次渲染（计数变化）→ 模式行行首即时更新。"""
        from src.tui.ink.element import h
        from src.tui.ink.reconciler import Reconciler
        from src.tui.ink.layout import layout_tree
        from src.tui.ink import components as _components
        from src.tui.app.input_area import InputArea
        rec = Reconciler(schedule_callback=None)
        root = rec.create_root()
        props = {
            "text": "", "cursor_pos": 0, "prompt": "> ", "completion": None,
            "status_active": False, "cpu": 0, "mem": 0, "width": 80,
            "history_search": None, "bg_bash_count": 0, "bg_subagent_count": 0,
        }
        self._render(rec, root, props)
        # 任务注册：计数变化 → 新 props 二次渲染
        props["bg_bash_count"] = 3
        props["bg_subagent_count"] = 1
        frame = self._render(rec, root, props)
        joined = "\n".join(self._frame_texts(frame))
        assert "bash \u00b7 3 \u00b7 subagent \u00b7 1" in joined
        # 任务全部完成：计数归零 → 前缀消失
        props["bg_bash_count"] = 0
        props["bg_subagent_count"] = 0
        frame = self._render(rec, root, props)
        joined = "\n".join(self._frame_texts(frame))
        assert "bash" not in joined
        assert "标准模式" in joined


# ── 10. context_manager 全局上下文使用率快照 ─────────────

class TestContextUsagePercent:
    """context_manager 全局快照（set/get O(1) + 缓存同步点写入）。

    口径：上下文使用率 =（系统提词 + 工具列表 + 全部消息）估算 tokens /
    model_context_tokens（模型上下文窗口，默认 1M）。
    """

    @staticmethod
    def _cm(msgs, tools=None, ctx_tokens: int = 10000):
        """构造 ContextManager（MockConfigAdapter 控制上下文窗口，测试精确）。"""
        from src.core.adapters.config import MockConfigAdapter
        from src.core.context_manager import ContextManager
        cfg = MockConfigAdapter({"model_context_tokens": ctx_tokens})
        return ContextManager(msgs, "m", tools=tools, config_port=cfg)

    def test_get_set_roundtrip(self):
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        set_context_usage_percent(42.5)
        try:
            assert get_context_usage_percent() == 42.5
        finally:
            set_context_usage_percent(None)
        assert get_context_usage_percent() is None

    def test_default_value_safe(self):
        """默认值（未写入）为 None 或 int/float（类型安全，不抛异常）。"""
        from src.core.context_manager import get_context_usage_percent
        v = get_context_usage_percent()
        assert v is None or isinstance(v, (int, float))

    def test_ensure_cache_syncs_percent(self):
        """_ensure_cache resync 后写入全局（基于 token 与模型上下文窗口）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        try:
            # "a"*3000 ASCII → 900 tokens；窗口 10000 → 9.0%
            cm = self._cm([{"role": "user", "content": "a" * 3000}])
            assert estimate_tokens("a" * 3000) == 900
            cm._ensure_cache()
            assert get_context_usage_percent() == 9.0
        finally:
            set_context_usage_percent(None)

    def test_invalidate_cache_keeps_accurate(self):
        """invalidate_cache 后 refresh_usage 懒 resync 恢复精确值（消息未变）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        set_context_usage_percent(None)
        try:
            cm = self._cm([{"role": "user", "content": "a" * 3000}])
            cm._ensure_cache()
            assert get_context_usage_percent() == 9.0
            cm.invalidate_cache()
            # 消息仍在 → 懒 resync 后仍 9.0%（不因失效隐藏/归零）
            assert get_context_usage_percent() == 9.0
        finally:
            set_context_usage_percent(None)

    def test_empty_messages_sets_zero(self):
        """无消息（空闲/未跑）→ 全局 0%（常驻显示 main · 0%）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        set_context_usage_percent(50)
        try:
            cm = self._cm([])
            cm._ensure_cache()
            assert get_context_usage_percent() == 0
        finally:
            set_context_usage_percent(None)

    def test_init_sets_zero(self):
        """会话启动（ContextManager 创建）即写 0%——空闲/启动也显示。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        set_context_usage_percent(None)
        try:
            self._cm([])
            assert get_context_usage_percent() == 0
        finally:
            set_context_usage_percent(None)

    def test_startup_includes_system_and_tools(self):
        """启动即统计系统提词 + 工具列表（无对话消息也有基础占比）。"""
        import json as _json
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        try:
            sys_parts = ["## 规则\n" + "x" * 5000, "环境信息: /home/u"]
            tools = [
                {"type": "function", "function": {
                    "name": "read_file", "description": "读文件",
                    "parameters": {"type": "object", "properties": {}}}},
                {"type": "function", "function": {
                    "name": "bash", "description": "执行命令",
                    "parameters": {"type": "object", "properties": {}}}},
            ]
            msgs = [{"role": "system", "content": p} for p in sys_parts]
            cm = self._cm(msgs, tools=tools, ctx_tokens=10000)
            tokens = sum(estimate_tokens(p) for p in sys_parts) + sum(
                estimate_tokens(_json.dumps(t, ensure_ascii=False)) for t in tools)
            assert get_context_usage_percent() == round(tokens / 10000 * 100, 1)
        finally:
            set_context_usage_percent(None)

    def test_refresh_usage_dynamic(self):
        """消息追加 → refresh_usage → 全局 pct 动态上升（动态刷新）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        try:
            msgs = [{"role": "system", "content": "s" * 1000}]
            cm = self._cm(msgs, ctx_tokens=10000)
            p0 = get_context_usage_percent()
            assert p0 == round(estimate_tokens("s" * 1000) / 10000 * 100, 1)
            msgs.append({"role": "user", "content": "a" * 10000})
            cm.refresh_usage()
            p1 = get_context_usage_percent()
            assert p1 > p0                          # 动态上升
        finally:
            set_context_usage_percent(None)

    def test_set_tools_refresh(self):
        """set_tools 更新工具列表后刷新全局 pct（工具变化动态更新）。"""
        import json as _json
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.api.tokens import estimate_tokens
        set_context_usage_percent(None)
        try:
            cm = self._cm([], ctx_tokens=10000)
            assert get_context_usage_percent() == 0
            tools = [{"type": "function", "function": {"name": "bash"}}]
            cm.set_tools(tools)
            tokens = estimate_tokens(_json.dumps(tools[0], ensure_ascii=False))
            assert get_context_usage_percent() == round(tokens / 10000 * 100, 1)
        finally:
            set_context_usage_percent(None)

    def test_base_agent_message_append_dynamic_refresh(self):
        """BaseAgent 消息追加自动刷新上下文使用率（动态刷新接入点）。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.core.adapters.config import MockConfigAdapter
        from src.core.context_manager import ContextManager
        from src.core.base_agent import BaseAgent
        set_context_usage_percent(None)
        try:
            agent = BaseAgent()
            agent.messages = [{"role": "system", "content": "sys" * 1000}]
            cfg = MockConfigAdapter({"model_context_tokens": 10000})
            agent.context_manager = ContextManager(agent.messages, "m", config_port=cfg)
            p0 = get_context_usage_percent()
            agent.add_user_message("hello " * 5000)
            p1 = get_context_usage_percent()
            assert p1 > p0
            agent._append_assistant_message("answer " * 5000)
            p2 = get_context_usage_percent()
            assert p2 > p1
            agent._append_tool_result("call_1", "result " * 2000)
            p3 = get_context_usage_percent()
            assert p3 > p2
        finally:
            set_context_usage_percent(None)

    def test_refresh_usage_force_recompute(self):
        """force=True：system 条数相同内容变化（Ctrl+B 场景）时强制重算。"""
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        set_context_usage_percent(None)
        try:
            msgs = [{"role": "system", "content": "x" * 3000}]  # 900 tok → 9.0%
            cm = self._cm(msgs, ctx_tokens=10000)
            assert get_context_usage_percent() == 9.0
            # 条数相同、内容变小（空模式切换）
            msgs[0] = {"role": "system", "content": "y" * 1000}  # 300 tok → 3.0%
            cm.refresh_usage()              # 懒同步命中旧缓存（bug 场景）
            assert get_context_usage_percent() == 9.0
            cm.refresh_usage(force=True)    # 强制重算
            assert get_context_usage_percent() == 3.0
        finally:
            set_context_usage_percent(None)

    def test_rebuild_system_prompt_recomputes(self):
        """Ctrl+B 空模式切换（rebuild_system_prompt）后百分比重新计算。

        覆盖两个修复点：
          1. messages 就地更新（引用一致——ContextManager 持有同一列表）；
          2. refresh_usage(force=True) 强制 resync（条数不变内容变化）。
        """
        from src.core.agent import Agent
        from src.core.context_manager import (
            set_context_usage_percent, get_context_usage_percent,
        )
        from src.core.adapters.config import MockConfigAdapter
        from src.core.context_manager import ContextManager
        set_context_usage_percent(None)
        try:
            agent = Agent()
            cfg = MockConfigAdapter({"model_context_tokens": 100000})
            cm = ContextManager(agent.messages, "m", config_port=cfg, tools=agent.tools)
            agent.context_manager = cm
            assert cm.messages is agent.messages
            p0 = get_context_usage_percent()
            # Ctrl+B：system 内容变小（空模式）
            agent.build_system_prompt = lambda: ["空模式轻量规则" + "z" * 200]
            agent.rebuild_system_prompt()
            p1 = get_context_usage_percent()
            assert cm.messages is agent.messages, "rebuild 后引用应保持一致"
            assert p1 < p0, f"rebuild 后百分比应重新计算下降（{p0} → {p1}）"
        finally:
            set_context_usage_percent(None)

    def test_base_agent_without_cm_no_crash(self):
        """SubAgent 形态（无 context_manager）消息追加不崩溃。"""
        from src.core.base_agent import BaseAgent
        agent = BaseAgent()
        agent.add_user_message("hi")
        agent._append_assistant_message("ok")
        agent._append_tool_result("c1", "r")


async def test_async_noop():
    """占位（asyncio 导入兼容性锚点，无实际断言）。"""
    assert asyncio is not None
