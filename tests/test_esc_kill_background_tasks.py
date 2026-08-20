"""ESC 中断杀掉所有后台 bash 和 subagent 测试（2026-08-21 用户需求）。

需求：按 ESC 后要杀掉所有后台 bash 和 subagent。

实现机制：
  1. BaseAgent.__init__ 把实例注册到全局活跃 Agent 注册表
     （src.core.base_agent._active_agents，weakref.WeakSet 自动清理）；
  2. BaseAgent._kill_all_background_tasks：取消本 Agent 全部后台任务
     （bash _background_tasks + subagent _subagent_tasks，含 managed_by_tool），
     对已记录 pid 且进程仍存活的 bash 记录兜底杀进程树（移出事件循环线程），
     最后清空任务表；
  3. 模块级 kill_all_active_background_tasks：遍历全局注册表统一杀掉
     所有活跃 Agent（主 Agent + 各 SubAgent）的后台任务（带去重标志）；
  4. 独立 kill_background 标志（api.interrupt_async）：
     - 纯 Esc（kind="escape"）中断时置位 + 跨线程调度杀任务；
     - Ctrl+C / 双 Esc / clawbot /stop / 网络错误等普通中断只终止当前
       生成，不杀后台任务（P0/P1 修复）；
  5. 调用点：Agent.run() 的 interrupted 分支（仅 kill 标志置位时）、
     _wait_background_tasks 中断检查（同上）、render 线程跨线程调度。
"""

from __future__ import annotations

import asyncio
import gc
import threading
import weakref
from types import SimpleNamespace

from src.core.agent import Agent
from src.core.base_agent import (
    BaseAgent,
    _active_agents,
    kill_all_active_background_tasks,
    schedule_kill_all_background_tasks,
)
from src.core.adapters.interrupt import MockInterruptAdapter
from src.core.subagent import SubAgent
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_parser import InputParser
from src.api.interrupt_async import (
    is_kill_background_requested,
    request_kill_background,
    reset_kill_background,
    reset_interrupt_async,
)


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeMainAgent(BaseAgent):
    """最小主 Agent 桩：模拟主 Agent——显式初始化 subagent 后台任务表。

    _subagent_tasks 仅主 Agent 独有（后台 subagent 仅主 Agent 可派发）；
    bash 表 _background_tasks 由 BaseAgent 统一初始化。
    """

    def __init__(self):
        super().__init__()
        self._subagent_tasks: dict[str, dict] = {}


class _FakeProcess:
    """fake asyncio.subprocess.Process：returncode None = 进程存活。"""

    def __init__(self, returncode=None):
        self.returncode = returncode


def _make_bash_rec(task, pid=None, process=None, managed=False, done=False):
    """构造 bash 后台任务记录（结构与 bash.py _promote_to_background 一致）。"""
    return {
        "task": task,
        "command": "sleep 100",
        "cwd": None,
        "created_at": 0.0,
        "done": done,
        "result": "",
        "status": "running",
        "process": process,
        "pid": pid,
        "mode": "pipe",
        "master_fd": None,
        "stdin_writer": None,
        "io_lock": None,
        "read_buffer": "",
        **({"managed_by_tool": True} if managed else {}),
    }


def _make_sa_rec(task, managed=False, done=False):
    """构造 subagent 后台任务记录（结构与 subagent.py _execute_background 一致）。"""
    return {
        "task": task,
        "command": "subagent(任务A)",
        "description": "任务A",
        "agent_type": "execute",
        "created_at": 0.0,
        "done": done,
        "result": "",
        "status": "running",
        "read_buffer": "",
        **({"managed_by_tool": True} if managed else {}),
    }


async def _long_running():
    await asyncio.sleep(100)


def _bare_subagent() -> SubAgent:
    """构造不跑 __init__ 的 SubAgent（仅用于注册表 + 后台表组合测试）。"""
    return SubAgent.__new__(SubAgent)


def _make_dispatcher(kill_cb=None, interrupt_cb=None) -> InputDispatcher:
    """构造带 mock 依赖的 InputDispatcher（用于 _do_interrupt 测试）。"""
    io = SimpleNamespace(
        stop=threading.Event(),
        set_interrupted=lambda: None,
        clear_interrupted=lambda: None,
        _flush_stdin_residual=lambda: None,
    )
    buffer_editor = SimpleNamespace(
        is_search_active=lambda: False,
        has_queued_input=lambda: False,
        reset=lambda clear_queue=True: None,
        _echo=lambda text: None,
    )
    d = InputDispatcher(io, buffer_editor, InputParser())
    d.set_interrupt_callback(interrupt_cb)
    d.set_kill_background_callback(kill_cb)
    return d


# ── 0. kill_background 独立标志（api 层） ────────────────

def test_kill_background_flag_independent():
    """kill_background 标志独立于普通中断信号。"""
    reset_kill_background()
    reset_interrupt_async()
    assert is_kill_background_requested() is False
    request_kill_background()
    assert is_kill_background_requested() is True
    # 独立：置位 kill 标志不置位普通中断
    from src.api.interrupt_async import is_interrupted
    assert is_interrupted() is False
    reset_kill_background()
    assert is_kill_background_requested() is False


def test_reset_interrupt_async_clears_kill_background():
    """reset_interrupt_async（每轮开始）同时清除 kill_background 标志。"""
    request_kill_background()
    assert is_kill_background_requested() is True
    reset_interrupt_async()
    assert is_kill_background_requested() is False


# ── 1. 全局活跃 Agent 注册表 ─────────────────────────────

def test_agent_registered_in_active_set():
    """BaseAgent 构造后实例注册到全局活跃注册表（WeakSet）。"""
    agent = _FakeMainAgent()
    assert agent in _active_agents


def test_active_set_weak_ref_auto_cleanup():
    """注册表为弱引用：实例被 GC 后自动移除（无显式注销需求）。"""
    agent = _FakeMainAgent()
    ref = weakref.ref(agent)
    assert ref() is agent
    del agent
    gc.collect()
    assert ref() is None  # 实例已被回收（WeakSet 不阻止 GC）


# ── 2. _kill_all_background_tasks：杀 bash 后台任务 ──────

async def test_kill_background_bash_task_cancelled():
    """运行中的 bash 后台任务被取消，任务表被清空，返回取消数量。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    count = await agent._kill_all_background_tasks()
    assert count == 1
    assert task.cancelled()
    assert agent._background_tasks == {}
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_kill_background_bash_kills_process_tree(monkeypatch):
    """对已记录 pid 且进程仍存活的 bash 记录兜底杀进程树（pid 复用安全红线）。"""
    killed: list[int] = []

    def _fake_kill(pid):
        killed.append(pid)

    monkeypatch.setattr("src.tools.bash.kill_process_tree", _fake_kill)

    agent = _FakeMainAgent()
    # 进程存活（returncode=None）→ 必须杀进程树
    task_alive = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-alive"] = _make_bash_rec(
        task_alive, pid=111, process=_FakeProcess(returncode=None),
    )
    # 进程已退出（returncode 非 None）→ 不杀（pid 可能被 OS 复用）
    task_dead = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-dead"] = _make_bash_rec(
        task_dead, pid=222, process=_FakeProcess(returncode=0),
    )
    # process 为 None（记录不完整）→ 不杀（pid 复用安全红线：无法确认
    # 进程是否已退出，跳过进程树杀，仅靠 task.cancel 的 CancelledError
    # 分支清理）
    task_noproc = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-noproc"] = _make_bash_rec(
        task_noproc, pid=333, process=None,
    )

    count = await agent._kill_all_background_tasks()
    assert count == 3
    assert killed == [111]  # 仅存活且有 process 对象的进程被杀
    assert task_alive.cancelled() and task_dead.cancelled()
    assert task_noproc.cancelled()
    assert agent._background_tasks == {}
    for t in (task_alive, task_dead, task_noproc):
        try:
            await t
        except asyncio.CancelledError:
            pass


async def test_kill_background_bash_process_tree_failure_ignored(monkeypatch):
    """杀进程树异常仅记日志，不阻断其余任务取消与清表。"""
    def _boom(pid):
        raise RuntimeError("killpg 失败")

    monkeypatch.setattr("src.tools.bash.kill_process_tree", _boom)

    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(
        task, pid=333, process=_FakeProcess(returncode=None),
    )

    count = await agent._kill_all_background_tasks()
    assert count == 1
    assert task.cancelled()
    assert agent._background_tasks == {}
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_kill_background_skips_done_tasks():
    """已完成/无 task 的记录不取消（task 为 None / task.done()），表仍清空。"""
    agent = _FakeMainAgent()
    done_task = asyncio.ensure_future(_long_running())
    await asyncio.sleep(0)  # 让 task 进入 pending
    done_task.cancel()
    try:
        await done_task
    except asyncio.CancelledError:
        pass
    agent._background_tasks["bg-done"] = _make_bash_rec(
        done_task, done=True,
    )
    agent._background_tasks["bg-none"] = _make_bash_rec(None)

    count = await agent._kill_all_background_tasks()
    assert count == 0  # 无运行中任务可取消
    assert agent._background_tasks == {}


async def test_kill_background_empty_tables():
    """空任务表：返回 0 且不报错。"""
    agent = _FakeMainAgent()
    assert await agent._kill_all_background_tasks() == 0


# ── 3. _kill_all_background_tasks：杀 subagent 后台任务 ───

async def test_kill_background_subagent_task_cancelled():
    """运行中的 subagent 后台任务被取消，subagent 表被清空。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks["sa-1"] = _make_sa_rec(task)

    count = await agent._kill_all_background_tasks()
    assert count == 1
    assert task.cancelled()
    assert agent._subagent_tasks == {}
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_kill_background_subagent_without_table():
    """SubAgent 形态（无 _subagent_tasks 属性）：仅杀 bash 表，不报错。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)
    del agent._subagent_tasks  # 模拟 SubAgent 形态（BaseAgent 不初始化该表）

    count = await agent._kill_all_background_tasks()
    assert count == 1
    assert task.cancelled()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_kill_background_invalid_records_skipped():
    """记录非 dict / task 非 Task（异常数据）跳过，不中断整体清理。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)
    agent._background_tasks["bg-bad"] = "not-a-dict"  # 异常记录
    agent._background_tasks["bg-notask"] = {
        "task": "not-an-asyncio-task",  # task 非 Task 对象（无 done()）
        "done": False, "status": "running",
    }

    count = await agent._kill_all_background_tasks()
    assert count == 1
    assert task.cancelled()
    assert agent._background_tasks == {}
    try:
        await task
    except asyncio.CancelledError:
        pass


# ── 4. managed_by_tool 任务同样被杀 ─────────────────────

async def test_kill_background_managed_by_tool_also_killed():
    """managed_by_tool 任务（bash_opt/subagent_opt 已接管）ESC 时同样被杀。"""
    agent = _FakeMainAgent()
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-managed"] = _make_bash_rec(
        bg_task, managed=True,
    )
    sa_task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks["sa-managed"] = _make_sa_rec(sa_task, managed=True)

    count = await agent._kill_all_background_tasks()
    assert count == 2
    assert bg_task.cancelled() and sa_task.cancelled()
    assert agent._background_tasks == {}
    assert agent._subagent_tasks == {}
    for t in (bg_task, sa_task):
        try:
            await t
        except asyncio.CancelledError:
            pass


# ── 5. kill_all_active_background_tasks：跨所有活跃 Agent ─

async def test_kill_all_active_agents(monkeypatch):
    """全局杀任务遍历所有活跃 Agent：主 Agent bash/subagent + SubAgent bash。"""
    agent = _FakeMainAgent()
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-main"] = _make_bash_rec(bg_task)
    sa_task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks["sa-main"] = _make_sa_rec(sa_task)

    # SubAgent 形态（手动加入注册表 + 持有 bash 表）
    sub = _bare_subagent()
    sub._background_tasks = {}
    sub_bg_task = asyncio.ensure_future(_long_running())
    sub._background_tasks["bg-sub"] = _make_bash_rec(sub_bg_task)
    _active_agents.add(sub)

    try:
        count = await kill_all_active_background_tasks()
        assert count == 3
        assert bg_task.cancelled() and sa_task.cancelled()
        assert sub_bg_task.cancelled()
        assert agent._background_tasks == {}
        assert agent._subagent_tasks == {}
        assert sub._background_tasks == {}
    finally:
        # 清理手动注册项（WeakSet 对 sub 无强引用，GC 后自动移除；
        # 显式 discard 保证测试间注册表隔离）
        _active_agents.discard(sub)
        for t in (bg_task, sa_task, sub_bg_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


async def test_kill_all_active_agents_empty():
    """无活跃后台任务时返回 0（注册表可含无任务 Agent，零副作用）。"""
    agent = _FakeMainAgent()  # 无任何后台任务
    assert await kill_all_active_background_tasks() == 0


async def test_kill_all_active_background_tasks_dedup():
    """去重标志：正在执行杀任务时，并发调用直接返回 0（避免重复杀进程树）。"""
    import src.core.base_agent as base_mod

    old = base_mod._kill_in_progress
    base_mod._kill_in_progress = True
    try:
        assert await kill_all_active_background_tasks() == 0
    finally:
        base_mod._kill_in_progress = old


# ── 6. Agent.run() interrupted 分支（kill 标志门控） ─────

async def test_agent_run_interrupted_kills_background():
    """Agent.run() interrupted + kill_background 置位（ESC）时杀所有后台任务。"""
    agent = _FakeMainAgent()  # 注册表中的"主 Agent"
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(bg_task)
    sa_task = asyncio.ensure_future(_long_running())
    agent._subagent_tasks["sa-1"] = _make_sa_rec(sa_task)

    # 运行 Agent.run()：mock pipeline 返回 interrupted=True
    runner = Agent.__new__(Agent)
    runner._interrupt_port = MockInterruptAdapter()

    class _FakePipeline:
        async def run_round_async(self, ctx):
            return True  # 模拟 ESC 中断生成

    runner._pipeline = _FakePipeline()

    request_kill_background()  # 纯 Esc 置位 kill 标志
    try:
        result = await runner.run()
        assert result is True
        # 全局 kill_all 杀掉了注册表中所有活跃 Agent 的后台任务
        assert bg_task.cancelled() and sa_task.cancelled()
        assert agent._background_tasks == {}
        assert agent._subagent_tasks == {}
    finally:
        reset_kill_background()
        for t in (bg_task, sa_task):
            try:
                await t
            except asyncio.CancelledError:
                pass


async def test_agent_run_interrupted_without_kill_flag_keeps_tasks():
    """interrupted 但 kill 标志未置位（Ctrl+C/异常/网络错误）：不杀后台任务。

    P0 修复：Pipeline 返回 interrupted=True 的原因不只有 ESC（模型调用异常、
    中间件/工具异常、外部取消均置位），普通中断只终止当前生成，后台任务
    继续运行（既有语义）。
    """
    agent = _FakeMainAgent()
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(bg_task)

    runner = Agent.__new__(Agent)
    runner._interrupt_port = MockInterruptAdapter()

    class _FakePipeline:
        async def run_round_async(self, ctx):
            return True  # 模拟非 ESC 原因中断（网络错误/异常）

    runner._pipeline = _FakePipeline()
    reset_kill_background()  # 确保 kill 标志未置位

    try:
        result = await runner.run()
        assert result is True
        assert not bg_task.cancelled()  # 后台任务未被误杀
        assert "bg-1" in agent._background_tasks
    finally:
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass


async def test_agent_run_completes_without_killing():
    """Agent.run() 正常完成（未中断）时不杀后台任务。"""
    agent = _FakeMainAgent()
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(bg_task)

    runner = Agent.__new__(Agent)
    runner._interrupt_port = MockInterruptAdapter()

    class _FakePipeline:
        async def run_round_async(self, ctx):
            return False  # 正常完成

    runner._pipeline = _FakePipeline()

    try:
        result = await runner.run()
        assert result is False
        assert not bg_task.cancelled()  # 后台任务未被误杀
        assert "bg-1" in agent._background_tasks
    finally:
        bg_task.cancel()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass


# ── 7. _wait_background_tasks 中断检查（kill 标志门控） ──

async def test_wait_background_tasks_interrupt_kills_all():
    """等待后台任务期间中断 + kill 标志（ESC）：杀掉全部后台任务（含 managed）。"""
    agent = _FakeMainAgent()
    # 非 managed 任务（在 pending 等待集内）
    task1 = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task1)
    # managed_by_tool 任务（不在 pending 等待集，但必须一并被杀）
    task2 = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-managed"] = _make_bash_rec(task2, managed=True)
    agent._interrupt_port = MockInterruptAdapter()
    agent._interrupt_port.set_interrupted(True)  # 模拟已按 ESC
    request_kill_background()

    try:
        unfinished = await agent._wait_background_tasks(
            [task1], timeout=0.05,
        )
        assert unfinished == set()  # 中断：不返回未完成任务
        assert task1.cancelled() and task2.cancelled()
        assert agent._background_tasks == {}  # 两表均被杀清
    finally:
        reset_kill_background()
        for t in (task1, task2):
            try:
                await t
            except asyncio.CancelledError:
                pass


async def test_wait_background_tasks_interrupt_without_kill_flag():
    """中断但 kill 标志未置位（Ctrl+C/双 Esc）：退出等待但不杀后台任务。"""
    agent = _FakeMainAgent()
    task1 = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task1)
    task2 = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-managed"] = _make_bash_rec(task2, managed=True)
    agent._interrupt_port = MockInterruptAdapter()
    agent._interrupt_port.set_interrupted(True)
    reset_kill_background()

    try:
        unfinished = await agent._wait_background_tasks(
            [task1], timeout=0.05,
        )
        assert unfinished == set()  # 退出等待
        assert not task1.cancelled()  # 后台任务未被杀
        assert not task2.cancelled()
        assert "bg-1" in agent._background_tasks
        assert "bg-managed" in agent._background_tasks
    finally:
        for t in (task1, task2):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass


async def test_wait_background_tasks_no_interrupt_waits():
    """未中断时 _wait_background_tasks 正常等待任务完成，不杀任务。"""
    agent = _FakeMainAgent()
    agent._interrupt_port = MockInterruptAdapter()  # 默认未中断

    async def _finish():
        await asyncio.sleep(0.01)

    task = asyncio.ensure_future(_finish())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    unfinished = await agent._wait_background_tasks([task], timeout=5)
    assert unfinished == set()
    assert not task.cancelled()
    assert task.done()
    assert "bg-1" in agent._background_tasks  # 未被清空（未中断路径不杀）


# ── 8. schedule_kill_all_background_tasks（render 线程调度） ─

async def test_schedule_kill_all_background_tasks():
    """schedule_kill_all_background_tasks 跨线程调度杀任务到事件循环并执行。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    loop = asyncio.get_running_loop()  # 模拟 UI 层传入的主事件循环
    schedule_kill_all_background_tasks(loop)
    # 轮询等待跨线程调度执行（正常 <10ms；上限放宽防 CI 慢环境 flaky）
    for _ in range(500):
        if agent._background_tasks == {}:
            break
        await asyncio.sleep(0.01)
    assert agent._background_tasks == {}
    assert task.cancelled()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_schedule_kill_all_background_tasks_loop_not_running():
    """事件循环未运行时跳过调度（run_coroutine_threadsafe 排队永不执行，
    协程泄漏），由事件循环处理点兜底。"""
    agent = _FakeMainAgent()
    task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(task)

    class _NotRunningLoop:
        def is_running(self):
            return False

    schedule_kill_all_background_tasks(_NotRunningLoop())
    # 未调度：任务仍运行、表未清空（等待处理点兜底）
    assert not task.cancelled()
    assert "bg-1" in agent._background_tasks
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


async def test_schedule_kill_all_background_tasks_no_loop(monkeypatch):
    """loop=None 时直接返回（不再 get_event_loop——Python 3.9 隐式创建临时
    循环且永不关闭，悬空泄漏；调用方应总是传入运行中的主事件循环）。"""
    called = []
    monkeypatch.setattr(
        "src.core.base_agent.asyncio.get_event_loop",
        lambda: called.append(1) or object(),
    )
    schedule_kill_all_background_tasks()
    assert called == []  # 未调用 get_event_loop


async def test_schedule_kill_all_background_tasks_schedule_failure(monkeypatch):
    """run_coroutine_threadsafe 失败（loop 不匹配/已关闭）时仅降级不抛异常，
    且已创建的协程被显式 close（无 "coroutine was never awaited" 泄漏）。"""
    def _boom(coro, loop):
        raise RuntimeError("loop is not running")

    monkeypatch.setattr(
        "src.core.base_agent.asyncio.run_coroutine_threadsafe", _boom,
    )
    # 传 is_running()=True 的伪 loop，确保 _boom 真正被触发
    # （loop=None 会直接 return，不覆盖本降级路径）
    fake_loop = SimpleNamespace(is_running=lambda: True)
    schedule_kill_all_background_tasks(fake_loop)  # 不抛异常


# ── 9. _do_interrupt kill_background 门控（render 线程） ──

def test_do_interrupt_escape_calls_kill_background_callback():
    """纯 Esc（_do_interrupt(kill_background=True)）触发 kill_background 回调。"""
    calls = []
    d = _make_dispatcher(
        kill_cb=lambda: calls.append("kill"),
        interrupt_cb=lambda: calls.append("interrupt"),
    )
    d._do_interrupt(kill_background=True)
    assert calls == ["interrupt", "kill"]


def test_do_interrupt_ctrl_c_skips_kill_background_callback():
    """普通中断（Ctrl+C/双 Esc，kill_background=False）不触发 kill 回调。"""
    calls = []
    d = _make_dispatcher(
        kill_cb=lambda: calls.append("kill"),
        interrupt_cb=lambda: calls.append("interrupt"),
    )
    d._do_interrupt()
    assert calls == ["interrupt"]


def test_do_interrupt_no_kill_callback_skipped():
    """未注入 kill_background 回调时 kill_background=True 仅记日志（测试兼容）。"""
    calls = []
    d = _make_dispatcher(
        kill_cb=None,
        interrupt_cb=lambda: calls.append("interrupt"),
    )
    d._do_interrupt(kill_background=True)
    assert calls == ["interrupt"]  # 不抛异常


# ── 10. pid 复用安全红线 / 中断语义 / 集成注入 ────────────

def test_should_kill_process_pid_reuse_safety():
    """pid 复用安全红线：仅 process 对象存在且 returncode None 时才杀进程树。"""
    from src.core.base_agent import _should_kill_process

    assert _should_kill_process(_FakeProcess(returncode=None)) is True
    assert _should_kill_process(_FakeProcess(returncode=0)) is False
    # process 为 None（记录不完整）：无法确认进程状态，跳过进程树杀
    assert _should_kill_process(None) is False


def test_do_interrupt_esc_cancel_input_triggers_kill():
    """esc_cancel_input 模式下纯 Esc 取消输入仍触发 kill_background 回调。"""
    calls = []
    d = _make_dispatcher(kill_cb=lambda: calls.append("kill"))
    d._cancel_input = lambda: calls.append("cancel")
    # 复刻 ESC 路径的 _cancel_input 分支：取消输入 + 触发杀后台任务
    d._cancel_input()
    d._trigger_kill_background()
    assert calls == ["cancel", "kill"]


async def test_agent_run_kill_in_wait_returns_interrupted():
    """等待后台任务期间 ESC 杀任务（kill 标志消费）→ Agent.run 返回 interrupted=True。

    P1-2 修复：_wait_background_tasks 内 kill 后会话层保持中断语义
    （_finalize_round 据此发射中断事件 / 保存 checkpoint / 状态转换），
    不再"无痕消失"。
    """
    agent = _FakeMainAgent()
    bg_task = asyncio.ensure_future(_long_running())
    agent._background_tasks["bg-1"] = _make_bash_rec(bg_task)

    runner = Agent.__new__(Agent)
    runner._interrupt_port = MockInterruptAdapter()

    class _FakePipeline:
        async def run_round_async(self, ctx):
            return False  # 模型正常完成（后台任务等待期间用户按 ESC）

    runner._pipeline = _FakePipeline()

    async def _fake_process_bg():
        # 模拟 _process_background_tasks 内部 _wait_background_tasks 收到
        # ESC（kill 标志置位）后杀掉所有后台任务
        await kill_all_active_background_tasks()
        return False

    async def _fake_process_sa():
        return False

    request_kill_background()
    try:
        runner._process_background_tasks = _fake_process_bg
        runner._process_subagent_tasks = _fake_process_sa
        result = await runner.run()
        assert result is True  # kill 标志已消费 → interrupted=True
        assert bg_task.cancelled()
    finally:
        reset_kill_background()
        try:
            await bg_task
        except asyncio.CancelledError:
            pass


def test_loop_and_runner_inject_kill_background_callback():
    """_loop.py / clawbot runner.py 注入 set_kill_background_callback（集成点）。"""
    import src.app_loop._loop as loop_mod
    import src.clawbot.runner as runner_mod

    loop_src = open(loop_mod.__file__, encoding="utf-8").read()
    assert "set_kill_background_callback" in loop_src
    assert "request_kill_background()" in loop_src
    assert "schedule_kill_all_background_tasks(self._loop)" in loop_src

    runner_src = open(runner_mod.__file__, encoding="utf-8").read()
    assert "set_kill_background_callback" in runner_src
    assert "request_kill_background()" in runner_src
    assert "schedule_kill_all_background_tasks(_main_loop)" in runner_src
