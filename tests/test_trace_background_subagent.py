"""轨迹 Trace 回车显示后台 subagent 测试（2026-08-19）。

需求：轨迹 Trace 中后台 subagent 也要能回车显示（进入其嵌套轨迹）。
根因（2026-08-19 修复）：``SubAgentSpawner._spawn_subagent`` 的
``display.add_agent`` 未透传 ``dispatch_label``（spec["tool_label"]，
subagent 调用 tool_call_id）——只写入了 SubAgent 实例
（``sa.dispatch_label``），面板槽位 dispatch_label 恒为空 → 后台
subagent（独立模式 run）无法与主轨迹中派发它的 subagent 工具记录
匹配合并 → 生成独立 subagent 记录，选中派发工具记录回车无反应。

修复后主轨迹合并到工具记录（subagent_label 设置）→ Enter 进入
后台 subagent 轨迹；槽位内容缺失时兜底占位记录（不空白）。

固化项：
  1. spawn 的 display.add_agent 透传 dispatch_label；
  2. 后台 subagent 合并进主轨迹 subagent 调用 tool 记录（subagent_label）；
  3. Enter（build_subagent_trace_records）进入后台 subagent 轨迹有内容；
  4. 完成后（面板 store 清空）回车仍可显示轨迹（存档保留）；
  5. 槽位消息/活动记录全部缺失时兜底占位（不空白）。
"""

from __future__ import annotations

from src.core.internal.agent._subagent_spawner import SubAgentSpawner
from src.tui._subagent_panel import SubAgentPanelController


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeDisplay:
    """模拟 EventBusDisplayProxy：add_agent 直接把槽位写入面板 store。"""

    def __init__(self, ctrl):
        self.ctrl = ctrl
        self.calls = []

    def add_agent(self, label, description, status="running",
                  agent_type="execute", dispatch_label=""):
        self.calls.append((label, description, dispatch_label))
        self.ctrl._store.add_agent(
            label=label, description=description, status=status,
            agent_type=agent_type, dispatch_label=dispatch_label,
        )


class _FakePort:
    def publish_event(self, event):
        pass


class _FakeSubAgent:
    """最小 SubAgent 替身（含 register_subagent 需要的字段）。"""

    def __init__(self, label, description, prompt, parent_agent, model=None,
                 agent_type="execute", dispatch_label=""):
        self.label = label
        self.description = description
        self.prompt = prompt
        self.agent_type = agent_type
        self.dispatch_label = dispatch_label
        self.messages = [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": prompt},
        ]
        self.display = None


class _FakeModel:
    """消息源模式主 Agent 模型（派发后台 subagent 的 tool_call + 返回）。"""

    blocks = []
    status = "idle"
    fullscreen = "trace"

    def __init__(self, tool_call_id="call-bg-1", task_id="sa-abc"):
        self._msgs = [
            {"role": "user", "content": "派发一个后台任务"},
            {"role": "assistant", "content": "好的",
             "tool_calls": [{"id": tool_call_id, "type": "function",
                             "function": {"name": "subagent",
                                          "arguments": '{"description": "后台任务"}'}}]},
            {"role": "tool", "tool_call_id": tool_call_id,
             "content": f'{{"task_id": "{task_id}", "status": "running"}}'},
        ]

    @property
    def message_source(self):
        return lambda: self._msgs


def _make_ctrl():
    """返回全局单例控制器（trace 构建内部经 get_default() 读取），并清理残留。"""
    ctrl = SubAgentPanelController.get_default()
    if ctrl._active:
        ctrl.stop(clear_panel=False)
    ctrl._store.clear()
    ctrl.clear_trace_archive()
    return ctrl


def _cleanup(ctrl):
    if ctrl._active:
        ctrl.stop(clear_panel=False)
    ctrl._store.clear()
    ctrl.clear_trace_archive()


def _spawn_bg(ctrl, label="sa-abc", tool_label="call-bg-1", desc="后台任务",
              prompt="后台指令"):
    """经 spawner.spawn 创建后台 subagent 槽位并注册（模拟 ParallelExecutor）。"""
    disp = _FakeDisplay(ctrl)
    spawner = SubAgentSpawner(
        parent_agent=object(), agent_factory=_FakeSubAgent, event_port=_FakePort(),
    )
    sa = spawner.spawn(
        {"description": desc, "prompt": prompt, "agent_type": "execute",
         "label": label, "tool_label": tool_label},
        0, disp,
    )
    ctrl.register_subagent(label, sa)
    return sa


# ── 1. spawn 透传 dispatch_label（修复核心） ───────────────

def test_spawn_subagent_passes_dispatch_label():
    """spawn 的 display.add_agent 透传 dispatch_label（后台 subagent 合并依据）。

    修复前 add_agent 未传 dispatch_label → 面板槽位恒为空 → 后台 subagent
    无法与主轨迹 subagent 调用记录匹配合并（回车无反应）。
    """
    ctrl = _make_ctrl()
    try:
        disp = _FakeDisplay(ctrl)
        spawner = SubAgentSpawner(
            parent_agent=object(), agent_factory=_FakeSubAgent, event_port=_FakePort(),
        )
        sa = spawner.spawn(
            {"description": "后台任务", "prompt": "p", "agent_type": "execute",
             "label": "sa-abc", "tool_label": "call-bg-1"},
            0, disp,
        )
        # SubAgent 实例与面板槽位 dispatch_label 一致（tool_call_id）
        assert sa.dispatch_label == "call-bg-1"
        assert disp.calls[0] == ("sa-abc", "后台任务", "call-bg-1")
        assert ctrl._store._agents["sa-abc"].dispatch_label == "call-bg-1"
        # 无 tool_label（独立执行/历史恢复）→ 空串（独立记录，零回归）
        disp2 = _FakeDisplay(ctrl)
        spawner.spawn(
            {"description": "d", "prompt": "p", "agent_type": "execute",
             "label": "sa-x", "tool_label": ""},
            0, disp2,
        )
        assert disp2.calls[0][2] == ""
    finally:
        _cleanup(ctrl)


# ── 2. 后台 subagent 合并进主轨迹工具记录（回车可进入） ────

def test_background_subagent_merges_into_tool_record():
    """后台 subagent 槽位带 dispatch_label → 主轨迹合并进 subagent 调用记录。

    合并后 tool 记录带 subagent_label（Enter 可进入后台 subagent 轨迹）；
    不再生成独立 subagent 记录（不分两条）。
    """
    from src.tui.app.trace import build_trace_records

    ctrl = _make_ctrl()
    try:
        sa = _spawn_bg(ctrl)
        sa.messages.append({"role": "assistant", "content": "后台分析结果"})

        records, rows = build_trace_records(_FakeModel())
        tools = [r for r in records if getattr(r, "kind", "") == "tool"]
        assert len(tools) == 1
        rec = tools[0]
        assert getattr(rec, "tool_call_id", "") == "call-bg-1"
        # 合并后携带 subagent_label → 主轨迹 Enter 可进入
        assert getattr(rec, "subagent_label", "") == "sa-abc"
        # 合并内容：subagent 摘要（label · desc）出现在结果/详情中
        assert "sa-abc" in (rec.result or "")
        assert "后台任务" in (rec.result or "")
        # 无独立 subagent 记录（合并语义）
        assert not [r for r in records if getattr(r, "kind", "") == "subagent"]
    finally:
        _cleanup(ctrl)


def test_background_subagent_enter_shows_trace():
    """回车进入后台 subagent 轨迹：完整内容（system/user/assistant/工具）。

    模拟 TraceView Enter 语义（model.trace_subagent_label = label →
    build_subagent_trace_records）——后台 subagent 轨迹与 mainagent 同构，
    有内容（不空白）。
    """
    from src.tui.app.trace import build_subagent_trace_records

    ctrl = _make_ctrl()
    try:
        sa = _spawn_bg(ctrl)
        sa.messages += [
            {"role": "assistant", "content": "分析结果", "reasoning_content": "思考过程"},
            {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "type": "function",
                                                                  "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}]},
            {"role": "tool", "tool_call_id": "c1", "content": "file content"},
            {"role": "assistant", "content": "最终回答"},
        ]

        records, rows = build_subagent_trace_records("sa-abc")
        kinds = [r.kind for r in records]
        assert kinds[0] == "tools"  # 固定 #0 工具列表
        assert "system" in kinds
        assert "user" in kinds
        assert "reasoning" in kinds
        assert "content" in kinds
        assert "tool" in kinds
        assert len(records) >= 6
    finally:
        _cleanup(ctrl)


# ── 3. 完成后（store 清空）回车仍可显示（存档保留） ───────

def test_background_subagent_enter_after_stop():
    """后台 subagent 完成、面板 stop 清空 store 后，回车仍可显示轨迹。

    主轨迹记录数据源 = 面板 store + 轨迹存档（_trace_archive）；完成后
    store 清空但存档保留 → Enter 进入后台 subagent 轨迹不空白。
    """
    from src.tui.app.trace import build_subagent_trace_records

    ctrl = _make_ctrl()
    try:
        ctrl.ensure_active()
        sa = _spawn_bg(ctrl)
        sa.messages.append({"role": "assistant", "content": "后台结果"})
        ctrl._store.update_status("sa-abc", "done")
        # 模拟 ParallelExecutor.run finally：stop 清空 store（存档保留）
        ctrl.stop(clear_panel=True)
        assert "sa-abc" not in ctrl._store._agents

        records, rows = build_subagent_trace_records("sa-abc")
        assert records, "完成后回车轨迹不应空白"
        assert "sa-abc" in ctrl._trace_archive
    finally:
        _cleanup(ctrl)


# ── 4. 槽位内容缺失兜底（不空白） ───────────────────────

def test_build_subagent_trace_records_fallback_placeholder():
    """槽位存在但消息/提词/工具历史/结果全部缺失 → 兜底占位记录（不空白）。"""
    from src.tui.app.trace import build_subagent_trace_records

    ctrl = _make_ctrl()
    try:
        ctrl._store.add_agent(
            label="sa-empty", description="空任务", status="done",
            agent_type="execute", dispatch_label="",
        )
        # 未 register（messages 空）+ 无 prompt/工具/结果 → 回退路径：
        # 至少 #0 工具列表记录（不空白）；工具列表缺失时兜底占位记录
        records, rows = build_subagent_trace_records("sa-empty")
        assert records, "槽位无内容时回车轨迹不应空白"
        assert rows == records
    finally:
        _cleanup(ctrl)


def test_build_subagent_trace_records_exception_fallback():
    """messages 数据异常（_records_from_messages 抛错）→ 回退不崩溃。

    修复前无 try/except：异常消息数据导致 Enter 后轨迹构建崩溃/空白。
    """
    from src.tui.app.trace import build_subagent_trace_records

    ctrl = _make_ctrl()
    try:
        ctrl._store.add_agent(
            label="sa-bad", description="坏数据", status="done",
            agent_type="execute", dispatch_label="",
        )
        slot = ctrl._store._agents["sa-bad"]
        slot.prompt = "提词仍在"  # 活动记录回退数据
        # 注入异常 content 结构（非 str/非标准 list blocks）
        slot.messages = [
            {"role": "user", "content": "正常"},
            {"role": "assistant", "content": {"weird": object()}},
        ]
        records, rows = build_subagent_trace_records("sa-bad")
        assert records, "异常数据下回车轨迹不应空白"
    finally:
        _cleanup(ctrl)
