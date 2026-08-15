"""Code Review 修复回归测试（2026-08-15 review 方向）。

覆盖 src/tui 子代理面板 / 补全引擎 / 系统监控模块的 review 修复：
  - P1-1/P2-7：_subagent_state tool_id 精确匹配（交叉 start/done、重复记录合并）
  - P1-2/P2-6：_system_monitor macOS iostat idle 列 / vm_stat total-free 口径
  - P2-1：_SystemMonitor._start_bg_refresh 并发幂等（锁保护）
  - P2-2：CompletionEngine._fetch_themes 异常返回 []
  - P2-3：_complete_path prefix="~" 枚举 home
  - P2-4：_panel_refresh 推送失败后重试（_last_pushed_frame 推送成功后更新）
  - P2-5：_subagent_render 动效参数惰性读取（运行期修改 TuiConfig 生效）

用 mock/unittest 隔离文件系统与平台差异；不修改其他已有测试文件。
"""

from __future__ import annotations

import os
import threading
import time
from types import SimpleNamespace
from unittest.mock import patch

from src.tui._completion_engine import CompletionEngine
from src.tui._subagent_panel import SubAgentPanelController
from src.tui._subagent_render import _fade_type_style, build_agent_lines
from src.tui._subagent_state import _AgentSlot, StateStore
from src.tui._system_monitor import _SystemMonitor


# ═══════════════════════════════════════════════════════════
# P1-1 / P2-7：_subagent_state tool_id 精确匹配
# ═══════════════════════════════════════════════════════════

def test_same_name_tool_cross_start_done_closes_correct_record():
    """同名工具连续调用（A start → B start → A done）：A 的 done 精确闭合 A 的
    记录，B 仍 running（修复前 A done 闭合 B → A 记录残留 running）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.start_tool("agent-1", "search", "A args", tool_id="call_a")
    store.start_tool("agent-1", "search", "B args", tool_id="call_b")
    store.done_tool("agent-1", "search", True, tool_id="call_a")
    slot = store._agents["agent-1"]
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert len(slot.tool_history) == 2
    assert by_id["call_a"].phase == "done"
    assert by_id["call_a"].end_time > 0
    assert by_id["call_b"].phase == "running"
    assert by_id["call_b"].end_time == 0.0


def test_cross_tool_done_reverse_order_all_closed():
    """完全乱序闭合（A start → B start → B done → A done）：全部正确闭合，
    无残留 running（修复前 A 记录残留 running → 面板 10Hz 空转渲染）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.start_tool("agent-1", "read_file", "a", tool_id="call_a")
    store.start_tool("agent-1", "read_file", "b", tool_id="call_b")
    store.done_tool("agent-1", "read_file", True, tool_id="call_b")
    store.done_tool("agent-1", "read_file", True, tool_id="call_a")
    slot = store._agents["agent-1"]
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert by_id["call_a"].phase == "done"
    assert by_id["call_b"].phase == "done"
    assert by_id["call_a"].end_time > 0
    assert by_id["call_b"].end_time > 0


def test_done_tool_fallback_by_tool_name_when_no_tool_id():
    """无 tool_id（旧调用方）：降级按 tool_name 匹配闭合（向后兼容）；
    重复 start（无 tool_id）不新建重复 running 记录（P2-7）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.start_tool("agent-1", "bash", "first")
    store.start_tool("agent-1", "bash", "second")
    slot = store._agents["agent-1"]
    # P2-7：重复 start（无 tool_id）合并到已有 running 记录，不新建
    assert len(slot.tool_history) == 1
    assert slot.tool_history[0].detail == "second"
    store.done_tool("agent-1", "bash", True)
    assert slot.tool_history[0].phase == "done"
    assert slot.tool_history[0].end_time > 0


def test_done_tool_with_tool_id_fallback_keeps_other_record():
    """带 tool_id 记录与无 tool_id 记录并存：done（无 tool_id）按 tool_name
    降级闭合无 tool_id 记录，不触碰带 tool_id 的 running 记录。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.start_tool("agent-1", "grep", "A", tool_id="call_a")
    store.start_tool("agent-1", "grep", "B", tool_id="")
    store.done_tool("agent-1", "grep", True, tool_id="")
    slot = store._agents["agent-1"]
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert by_id[""].phase == "done"
    assert by_id["call_a"].phase == "running"


def test_parsing_event_with_tool_id_updates_correct_record():
    """流式 parsing 事件带 tool_id：A/B 同名工具 parsing 乱序，各更新各的
    （修复前仅按 tool_name 匹配 → 更新错记录）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.update_tool_parsing("agent-1", "read", "A", tool_id="call_a")
    store.update_tool_parsing("agent-1", "read", "B", tool_id="call_b")
    store.update_tool_parsing("agent-1", "read", "A2", tool_id="call_a")
    slot = store._agents["agent-1"]
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert len(slot.tool_history) == 2
    assert by_id["call_a"].detail == "A2"
    assert by_id["call_b"].detail == "B"


def test_start_tool_converts_parsing_record_by_tool_id():
    """parsing → running 转换按 tool_id 命中（不产生重复记录）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.update_tool_parsing("agent-1", "search", "{", tool_id="call_a")
    store.update_tool_parsing("agent-1", "search", "{q", tool_id="call_a")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 1
    assert slot.tool_history[0].detail == "{q"
    store.start_tool("agent-1", "search", "q", tool_id="call_a")
    assert len(slot.tool_history) == 1
    assert slot.tool_history[0].phase == "running"


def test_start_tool_no_duplicate_running_record():
    """P2-7：重复 start 事件（同 tool_id）不新建重复 running 记录。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.start_tool("agent-1", "bash", "first", tool_id="call_1")
    store.start_tool("agent-1", "bash", "second", tool_id="call_1")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 1
    assert slot.tool_history[0].phase == "running"
    assert slot.tool_history[0].detail == "second"
    assert slot.tool_history[0].tool_id == "call_1"


# ═══════════════════════════════════════════════════════════
# BUG（2026-08-16）：面板"显示多一行"——流式 parsing 带 tool_id、
# 执行 start 不带 tool_id 时同一次工具调用分裂为两条记录
# ═══════════════════════════════════════════════════════════

def test_stream_parsing_and_execute_events_merge_single_record():
    """流式 parsing(带 tool_id) + 执行 parsing/start/done(不带 tool_id) 事件
    序列：同一次工具调用只产生一条 done 记录。

    背景：SubAgent 工具调用的流式 parsing 事件（api/stream/handlers/
    tool_calls.py）带 tool_id（_stream_label），而执行阶段 on_before/on_after
    曾不传 tool_id——start 无 tool_id 时 fallback=False 不认领带 id 的 parsing
    记录 → 每次调用分裂为两条记录（带 id parsing 残留 + 无 id running→done），
    面板同一工具显示两行（✔ done + ◌ parsing 残留）。修复后 start_tool
    fallback=True 降级认领，仅一条记录。
    """
    store = StateStore()
    store.add_agent("agent-1", "desc")
    # 流式阶段：ToolCallsHandler 带 tool_id 发布 parsing
    store.update_tool_parsing("agent-1", "read_file", "", tool_id="call_x")
    # 执行阶段：on_before/on_after（旧调用方无 tool_id）
    store.update_tool_parsing("agent-1", "read_file", "/a.py", tool_id="")
    store.start_tool("agent-1", "read_file", "/a.py", tool_id="")
    store.done_tool("agent-1", "read_file", True, tool_id="")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 1, \
        f"同一次调用应只有 1 条记录，实际 {len(slot.tool_history)}"
    rec = slot.tool_history[0]
    assert rec.phase == "done"
    assert rec.tool_id == "call_x"
    assert rec.detail == "/a.py"
    assert rec.end_time > 0


def test_two_same_name_calls_with_mixed_tool_id_keep_separate():
    """两次同名工具调用（带 id 与不带 id 混合事件）：各闭合各的记录，
    不因 start_tool fallback=True 误合并（隔离不变量保持）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    # 调用1：完整带 id 生命周期
    store.update_tool_parsing("agent-1", "read_file", "", tool_id="call_a")
    store.start_tool("agent-1", "read_file", "/a.py", tool_id="call_a")
    store.done_tool("agent-1", "read_file", True, tool_id="call_a")
    # 调用2：纯无 id 生命周期（旧调用方）
    store.update_tool_parsing("agent-1", "read_file", "/b.py", tool_id="")
    store.start_tool("agent-1", "read_file", "/b.py", tool_id="")
    store.done_tool("agent-1", "read_file", True, tool_id="")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 2
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert by_id["call_a"].phase == "done"
    assert by_id["call_a"].detail == "/a.py"
    assert by_id[""].phase == "done"
    assert by_id[""].detail == "/b.py"


def test_late_parsing_event_after_done_not_duplicated():
    """工具已完成（done）后同 tool_id 的迟到 parsing 事件：不新建残留
    parsing 记录（修复前新建 ◌ parsing 行使面板同一工具显示两行）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.update_tool_parsing("agent-1", "read_file", "", tool_id="call_x")
    store.start_tool("agent-1", "read_file", "/a.py", tool_id="call_x")
    store.done_tool("agent-1", "read_file", True, tool_id="call_x")
    # 迟到 parsing（同 tool_id，工具已闭合）
    store.update_tool_parsing("agent-1", "read_file", "/a.py", tool_id="call_x")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 1
    assert slot.tool_history[0].phase == "done"


def test_parsing_new_tool_id_after_done_still_created():
    """前次调用 done 后，新调用（不同 tool_id）的 parsing 事件仍正常新建
    （迟到防御不误伤新调用）。"""
    store = StateStore()
    store.add_agent("agent-1", "desc")
    store.update_tool_parsing("agent-1", "read_file", "", tool_id="call_a")
    store.start_tool("agent-1", "read_file", "/a.py", tool_id="call_a")
    store.done_tool("agent-1", "read_file", True, tool_id="call_a")
    store.update_tool_parsing("agent-1", "read_file", "", tool_id="call_b")
    slot = store._agents["agent-1"]
    assert len(slot.tool_history) == 2
    by_id = {r.tool_id: r for r in slot.tool_history}
    assert by_id["call_a"].phase == "done"
    assert by_id["call_b"].phase == "parsing"


# ═══════════════════════════════════════════════════════════
# P1-2 / P2-6：_system_monitor macOS
# ═══════════════════════════════════════════════════════════

class _CmdResult:
    """subprocess.run 返回值的鸭子类型（stdout/returncode）。"""

    def __init__(self, stdout: str, returncode: int = 0):
        self.stdout = stdout
        self.returncode = returncode


def test_read_cpu_macos_parses_idle_column_by_header():
    """macOS iostat 输出：idle 列按标题行定位（us sy id 1m 5m 15m），
    取 idle=91 → CPU 9.0（修复前取 parts[-1]=15m load average → ≈98% 错误）。"""
    mon = _SystemMonitor()
    output = (
        "          cpu     load average\n"
        "    us sy id   1m   5m   15m\n"
        "    4  3 93  1.68  1.99  1.85\n"
        "    5  4 91  1.68  1.99  1.85\n"
    )
    with patch("src.tui._system_monitor.subprocess.run",
               return_value=_CmdResult(output)):
        val = mon._read_cpu_macos()
    assert abs(val - 9.0) < 1e-9


def test_read_cpu_macos_with_disk_columns():
    """iostat 输出含磁盘列（KB/t tps MB/s us sy id ...）：标题行定位仍正确
    （硬编码索引方案在含磁盘列时会错位）。"""
    mon = _SystemMonitor()
    output = (
        "          disk0       cpu     load average\n"
        "    KB/t tps  MB/s  us sy id   1m   5m   15m\n"
        "   33.58  89  2.92   4  3 93  1.62  1.83  1.72\n"
        "   33.58  89  2.92   5  3 92  1.62  1.83  1.72\n"
    )
    with patch("src.tui._system_monitor.subprocess.run",
               return_value=_CmdResult(output)):
        val = mon._read_cpu_macos()
    assert abs(val - 8.0) < 1e-9


def test_read_mem_macos_uses_total_minus_free():
    """P2-6：内存使用率 = total - free（完整口径）——修复前
    active+wired+stored_in_compressor 低估真实使用（且 macOS 实际关键字为
    "Pages occupied by compressor"，旧关键字解析恒 0）。"""
    mon = _SystemMonitor()
    total = 16 * 1024 ** 3
    page_size = 4096
    free_pages = 100000
    outputs = {
        ("sysctl", "-n", "hw.memsize"): _CmdResult(str(total)),
        ("vm_stat",): _CmdResult(
            "Mach Virtual Memory Statistics: (page size of 4096 bytes)\n"
            "Pages free:                               100000.\n"
            "Pages active:                             200000.\n"
            "Pages wired down:                         30000.\n"
            "Pages occupied by compressor:             4000.\n"
        ),
    }

    def fake_run(cmd, *a, **kw):
        return outputs[tuple(cmd)]

    with patch("src.tui._system_monitor.subprocess.run", side_effect=fake_run):
        val = mon._read_mem_macos()
    expected = 100.0 * (total - free_pages * page_size) / total
    assert abs(val - expected) < 1e-9
    # 旧口径（active+wired+compressed）会系统性低估
    old = 100.0 * (200000 + 30000 + 4000) * page_size / total
    assert val > old


# ═══════════════════════════════════════════════════════════
# P2-1：_start_bg_refresh 并发幂等
# ═══════════════════════════════════════════════════════════

def test_bg_refresh_started_once_under_concurrency():
    """并发首次调用 _start_bg_refresh 只启动一个后台线程（锁保护）——
    修复前无锁，双线程同时通过检查可启动双后台线程。

    注意：``_SystemMonitor.threading`` 是全局 threading 模块的引用，直接
    patch ``src.tui._system_monitor.threading.Thread`` 会同时 patch 全局
    ``threading.Thread``——故真实测试线程必须在 patch 上下文**外**创建
    （patch 内创建会拿到 _FakeThread，无 start/join）。
    """
    mon = _SystemMonitor()
    created: list = []

    class _FakeThread:
        def __init__(self, *a, **kw):
            self.started = False

        def start(self):
            self.started = True

    def fake_thread(*a, **kw):
        t = _FakeThread()
        created.append(t)
        return t

    barrier = threading.Barrier(8)
    errors: list = []

    def worker():
        try:
            barrier.wait()
            mon._start_bg_refresh()
        except Exception as e:  # pragma: no cover
            errors.append(e)

    # 真实线程在 patch 上下文外创建（不受 patch 影响）
    ts = [threading.Thread(target=worker) for _ in range(8)]
    with patch("src.tui._system_monitor.threading.Thread",
               side_effect=fake_thread):
        for t in ts:
            t.start()
        for t in ts:
            t.join()

    assert not errors
    assert len(created) == 1, \
        f"并发启动应只创建 1 个后台线程，实际 {len(created)}"
    assert mon._bg_started is True
    assert created[0].started is True


# ═══════════════════════════════════════════════════════════
# P2-2：_fetch_themes 异常返回 []
# ═══════════════════════════════════════════════════════════

def test_fetch_themes_returns_empty_on_exception():
    """CommandUiAdapter 不可用/构造异常时 _fetch_themes 返回 []（与
    _fetch_sessions/_fetch_models 一致），不冒泡崩溃补全路径。"""
    with patch("src.tui._completion_engine._THEME_ADAPTER", None), \
         patch("src.core.commands._ui_adapter.CommandUiAdapter", None):
        result = CompletionEngine._fetch_themes()
    assert result == []


# ═══════════════════════════════════════════════════════════
# P2-3：_complete_path prefix="~" 枚举 home
# ═══════════════════════════════════════════════════════════

def test_complete_path_tilde_enumerates_home(tmp_path):
    """prefix="~" 视作枚举 home 目录（修复前返回空候选或错误匹配 home
    同级目录）。候选替换文本带展开后的 home 绝对路径前缀。"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "notes.txt").write_text("x")
    engine = CompletionEngine(commands_source=lambda: [])
    with patch("src.tui._completion_engine.os.path.expanduser",
               return_value=str(tmp_path)):
        items = engine._complete_path("~")
    assert items, "prefix='~' 应枚举 home 目录"
    texts = {it.text for it in items}
    assert f"{tmp_path}{os.sep}docs{os.sep}" in texts
    assert f"{tmp_path}{os.sep}notes.txt" in texts
    assert items[0].start_pos == -len("~")


# ═══════════════════════════════════════════════════════════
# P2-4：_panel_refresh 推送失败重试
# ═══════════════════════════════════════════════════════════

def test_panel_refresh_retries_on_push_failure():
    """推送失败后 _last_pushed_frame 不更新（推送成功后才更新）——下一帧重试
    （修复前先更新后推送：失败后变更检测 ``lines == _last_pushed_frame`` 为
    False → 不再重试 → 帧永久丢失）。"""
    ctrl = SubAgentPanelController()
    ctrl._cb_registered = True  # 跳过 _register_panel_refresh（避免 chat_ui 副作用）
    ctrl._dirty = True
    ctrl._last_emit_time = 0.0  # 绕过节流
    ctrl._render_frame = lambda: []
    pushed: list = []

    def failing_push(lines):
        pushed.append(lines)
        raise RuntimeError("push failed")

    ctrl._push_frame = failing_push
    ctrl._panel_refresh()
    # 推送失败 → 不更新 _last_pushed_frame；脏标记保留（下一帧重试）
    assert ctrl._last_pushed_frame is None
    assert ctrl._dirty is True

    ctrl._push_frame = lambda lines: pushed.append(lines)
    ctrl._last_emit_time = 0.0
    ctrl._panel_refresh()
    assert len(pushed) == 2
    assert ctrl._last_pushed_frame == []
    assert ctrl._dirty is False


# ═══════════════════════════════════════════════════════════
# P2-5：动效参数惰性读取
# ═══════════════════════════════════════════════════════════

def test_fade_params_lazy_read_from_config():
    """_fade_type_style 惰性读取 TuiConfig（运行期修改 fade_start_color 生效，
    不再消费模块级快照 238）。"""
    cfg = SimpleNamespace(
        fade_duration_sec=1.0, fade_start_color=100, spinner_tick_hz=5.0,
    )
    with patch("src.tui._config.TuiConfig.defaults", return_value=cfg):
        style = _fade_type_style("execute", 0.0)
    assert style.fg == 100


def test_spinner_hz_lazy_read_from_config():
    """build_agent_lines 惰性读取 TuiConfig.spinner_tick_hz（运行期修改生效，
    不再消费模块级快照 10.0）。"""
    cfg = SimpleNamespace(
        fade_duration_sec=0.6, fade_start_color=238, spinner_tick_hz=5.0,
    )
    slot = _AgentSlot("agent-1", "desc")  # 默认 status="running"
    with patch("src.tui._config.TuiConfig.defaults", return_value=cfg), \
         patch("src.tui._subagent_render._fx.spinner_char") as m_spin:
        build_agent_lines(slot, time.time(), is_last=True, max_history=3)
    m_spin.assert_called_once_with(5.0)
