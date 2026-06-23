"""Tests for src/core/parallel_executor.py — ParallelExecutor"""

import asyncio
from unittest.mock import MagicMock, AsyncMock, patch, call, PropertyMock

import pytest

from src.core.parallel_executor import ParallelExecutor


# ═══════════════════════════════════════════════════════════════
# 夹具
# ═══════════════════════════════════════════════════════════════

@pytest.fixture
def parent():
    """返回一个 mock parent agent"""
    return MagicMock()


@pytest.fixture
def executor(parent):
    """返回一个 ParallelExecutor 实例"""
    return ParallelExecutor(parent_agent=parent, max_history=3)


@pytest.fixture
def mock_display():
    """返回一个 mock ParallelDisplay 实例"""
    disp = MagicMock()
    disp.await_stop = AsyncMock()
    return disp


@pytest.fixture
def mock_subagent_instance():
    """返回一个 mock SubAgent 实例，含 async run() 方法"""
    sa = MagicMock()
    sa.label = "agent-1"
    sa.description = "测试子代理"
    sa.run = AsyncMock(return_value="执行成功")
    return sa


# ═══════════════════════════════════════════════════════════════
# 构造与属性
# ═══════════════════════════════════════════════════════════════

class TestInit:
    """ParallelExecutor 构造函数"""

    def test_parent_stored(self, executor, parent):
        """parent 参数正确存储"""
        assert executor.parent is parent

    def test_max_history_default(self):
        """max_history 默认值为 3"""
        executor = ParallelExecutor(parent_agent=MagicMock())
        assert executor.max_history == 3

    def test_max_history_custom(self):
        """自定义 max_history"""
        executor = ParallelExecutor(parent_agent=MagicMock(), max_history=5)
        assert executor.max_history == 5

    def test_is_batch_mode_initial_false(self, executor):
        """初始状态下 is_batch_mode 为 False"""
        assert executor.is_batch_mode is False

    def test_agent_factory_default(self):
        """agent_factory 默认为 SubAgent 类"""
        from src.core.parallel_executor import SubAgent
        executor = ParallelExecutor(parent_agent=MagicMock())
        assert executor._agent_factory is SubAgent

    def test_agent_factory_custom(self):
        """可传入自定义 agent_factory"""
        custom_factory = MagicMock()
        executor = ParallelExecutor(parent_agent=MagicMock(), agent_factory=custom_factory)
        assert executor._agent_factory is custom_factory

    def test_is_web_default(self):
        """is_web 默认值为 False"""
        executor = ParallelExecutor(parent_agent=MagicMock())
        assert executor._is_web is False

    def test_is_web_custom(self):
        """可设置 is_web=True"""
        executor = ParallelExecutor(parent_agent=MagicMock(), is_web=True)
        assert executor._is_web is True

    def test_initial_pending_specs_empty(self, executor):
        """初始 _pending_specs 为空列表"""
        assert executor._pending_specs == []

    def test_initial_results_empty(self, executor):
        """初始 _results 为空列表"""
        assert executor._results == []

    def test_initial_expected_count_zero(self, executor):
        """初始 _expected_count 为 0"""
        assert executor._expected_count == 0

    def test_initial_registered_count_zero(self, executor):
        """初始 _registered_count 为 0"""
        assert executor._registered_count == 0

    def test_all_done_event_not_set(self, executor):
        """初始 _all_done 是 asyncio.Event 且未设置"""
        assert isinstance(executor._all_done, asyncio.Event)
        assert executor._all_done.is_set() is False

    def test_agents_lock_is_lock(self, executor):
        """_agents_lock 是 asyncio.Lock"""
        assert isinstance(executor._agents_lock, asyncio.Lock)


# ═══════════════════════════════════════════════════════════════
# is_batch_mode
# ═══════════════════════════════════════════════════════════════

class TestIsBatchMode:
    """is_batch_mode 属性"""

    def test_default_false(self, executor):
        """未调用 setup_barrier 时返回 False"""
        assert executor.is_batch_mode is False

    def test_true_after_setup_barrier(self, executor):
        """setup_barrier(3) 后返回 True"""
        executor.setup_barrier(3)
        assert executor.is_batch_mode is True

    def test_true_after_setup_barrier_one(self, executor):
        """setup_barrier(1) 正常设置，返回 True"""
        executor.setup_barrier(1)
        assert executor.is_batch_mode is True

    def test_false_after_setup_barrier_zero(self, executor):
        """setup_barrier(0) 被跳过，仍然返回 False"""
        executor.setup_barrier(0)
        assert executor.is_batch_mode is False


# ═══════════════════════════════════════════════════════════════
# setup_barrier
# ═══════════════════════════════════════════════════════════════

class TestSetupBarrier:
    """setup_barrier 方法"""

    def test_skip_when_count_zero(self, executor):
        """count=0 时跳过，状态不变"""
        executor._pending_specs.append({"dummy": True})
        executor._results.append({"dummy": True})
        executor._expected_count = 5
        executor._registered_count = 3
        executor._all_done.set()

        executor.setup_barrier(0)

        assert executor._expected_count == 5
        assert executor._registered_count == 3

    def test_setup_when_count_one(self, executor):
        """count=1 时正常设置，_expected_count 为 1"""
        executor.setup_barrier(1)
        assert executor._expected_count == 1

    def test_normal_setup(self, executor):
        """count=3 时正确设置 barrier 并清空旧数据"""
        executor._pending_specs.append({"old": True})
        executor._results.append({"old": True})
        executor._all_done.set()

        executor.setup_barrier(3)

        assert executor._expected_count == 3
        assert executor._registered_count == 0
        assert executor._pending_specs == []
        assert executor._results == []
        assert executor._all_done.is_set() is False

    def test_large_count(self, executor):
        """count 较大时也能正常工作"""
        executor.setup_barrier(10)
        assert executor._expected_count == 10
        assert executor._registered_count == 0


# ═══════════════════════════════════════════════════════════════
# add_agent
# ═══════════════════════════════════════════════════════════════

class TestAddAgent:
    """add_agent 方法"""

    def test_returns_index_zero_first(self, executor):
        """首次添加返回索引 0"""
        idx = executor.add_agent("测试", "prompt")
        assert idx == 0

    def test_returns_incremented_index(self, executor):
        """多次添加返回递增索引"""
        assert executor.add_agent("任务1", "prompt1") == 0
        assert executor.add_agent("任务2", "prompt2") == 1
        assert executor.add_agent("任务3", "prompt3") == 2

    def test_stores_spec(self, executor):
        """添加后 spec 存储在 _pending_specs 中"""
        executor.add_agent("测试", "prompt内容", model="gpt-4", tool_label="my_tool")
        assert len(executor._pending_specs) == 1
        spec = executor._pending_specs[0]
        assert spec["description"] == "测试"
        assert spec["prompt"] == "prompt内容"
        assert spec["agent_type"] == "ordinary"
        assert spec["model"] == "gpt-4"
        assert spec["tool_label"] == "my_tool"

    def test_default_model_none(self, executor):
        """不传 model 时 model 为 None"""
        executor.add_agent("测试", "prompt")
        assert executor._pending_specs[0]["model"] is None

    def test_default_tool_label_none(self, executor):
        """不传 tool_label 时 tool_label 为 None"""
        executor.add_agent("测试", "prompt")
        assert executor._pending_specs[0]["tool_label"] is None

    def test_default_agent_type_ordinary(self, executor):
        """不传 agent_type 时默认 ordinary"""
        executor.add_agent("测试", "prompt")
        assert executor._pending_specs[0]["agent_type"] == "ordinary"

    def test_custom_agent_type(self, executor):
        """可传入自定义 agent_type"""
        executor.add_agent("测试", "prompt", agent_type="ordinary")
        assert executor._pending_specs[0]["agent_type"] == "ordinary"

    def test_map_agent_type(self, executor):
        """可传入 map agent_type"""
        executor.add_agent("分析项目", "生成地图", agent_type="map")
        assert executor._pending_specs[0]["agent_type"] == "map"

    def test_plan_execute_agent_type(self, executor):
        """可传入 plan_execute agent_type"""
        executor.add_agent("执行计划", "执行步骤", agent_type="plan_execute")
        assert executor._pending_specs[0]["agent_type"] == "plan_execute"


# ═══════════════════════════════════════════════════════════════
# get_result
# ═══════════════════════════════════════════════════════════════

class TestGetResult:
    """get_result 方法"""

    def test_valid_index(self, executor):
        """有效索引返回对应结果"""
        executor._results = [
            {"label": "agent-1", "description": "任务1", "result": "成功", "error": ""},
            {"label": "agent-2", "description": "任务2", "result": "完成", "error": ""},
        ]
        result = executor.get_result(1)
        assert result["label"] == "agent-2"
        assert result["result"] == "完成"

    def test_first_index(self, executor):
        """索引 0 返回第一个结果"""
        executor._results = [
            {"label": "agent-1", "description": "任务1", "result": "成功", "error": ""},
        ]
        result = executor.get_result(0)
        assert result["result"] == "成功"

    def test_out_of_bounds_returns_error(self, executor):
        """索引超出范围返回错误字典"""
        executor._results = [
            {"label": "agent-1", "description": "任务1", "result": "成功", "error": ""},
        ]
        result = executor.get_result(5)
        assert "error" in result
        assert "尚未就绪" in result["error"]

    def test_negative_index_with_pending_content(self, executor):
        """负数索引且 _pending_specs 有内容时引用最后一个 pending spec"""
        executor.add_agent("待执行任务", "prompt内容")
        result = executor.get_result(-1)
        assert "error" in result
        assert "尚未就绪" in result["error"]
        assert result["description"] == "待执行任务"

    def test_pending_spec_fallback(self, executor):
        """索引在 _pending_specs 范围内但不在 _results 中时返回错误"""
        executor.add_agent("待执行任务", "prompt内容")
        result = executor.get_result(0)
        assert result["label"] == "agent-1"
        assert result["description"] == "待执行任务"
        assert "尚未就绪" in result["error"]

    def test_empty_state(self, executor):
        """_results 和 _pending_specs 均为空时返回缺省错误"""
        result = executor.get_result(0)
        assert result["label"] == "agent-1"
        assert result["description"] == "?"
        assert "尚未就绪" in result["error"]


# ═══════════════════════════════════════════════════════════════
# _run_one
# ═══════════════════════════════════════════════════════════════

class TestRunOne:
    """_run_one 异步方法"""

    @pytest.mark.asyncio
    async def test_run_one_success(self, executor, mock_subagent_instance, mock_display):
        """成功执行 → 返回包含结果的字典，更新 display"""
        sa = mock_subagent_instance
        display = mock_display

        with patch("src.core.parallel_executor.DisplayEventBus") as MockBus:
            mock_bus = MagicMock()
            MockBus.get_default.return_value = mock_bus

            result = await executor._run_one(sa, display, stagger=0)

        assert result["label"] == "agent-1"
        assert result["description"] == "测试子代理"
        assert result["result"] == "执行成功"
        assert result["error"] == ""

        sa.run.assert_awaited_once()
        display.update_agent_status.assert_called_with("agent-1", "done")
        display.set_result.assert_called_with("agent-1", result_text="执行成功")
        mock_bus.publish.assert_called()

    @pytest.mark.asyncio
    async def test_run_one_cancelled(self, executor, mock_subagent_instance, mock_display):
        """被取消 → 返回结果 dict，不抛出 CancelledError，保证 agent 身份不丢失"""
        sa = mock_subagent_instance
        sa.run = AsyncMock(side_effect=asyncio.CancelledError())
        display = mock_display

        with patch("src.core.parallel_executor.DisplayEventBus"):
            result = await executor._run_one(sa, display, stagger=0)

        assert result["label"] == "agent-1"
        assert result["description"] == "测试子代理"
        assert result["result"] == ""
        assert result["error"] == "cancelled"
        display.update_model_phase.assert_called_with("agent-1", "error", "cancelled")
        display.update_agent_status.assert_called_with("agent-1", "fail")
        display.set_result.assert_called_with("agent-1", error="cancelled")

    @pytest.mark.asyncio
    async def test_run_one_exception(self, executor, mock_subagent_instance, mock_display):
        """执行异常 → 返回包含异常信息的字典"""
        sa = mock_subagent_instance
        sa.run = AsyncMock(side_effect=ValueError("测试错误"))
        display = mock_display

        with patch("src.core.parallel_executor.DisplayEventBus"):
            result = await executor._run_one(sa, display, stagger=0)

        assert result["label"] == "agent-1"
        assert result["description"] == "测试子代理"
        assert result["result"] == ""
        assert "测试错误" in result["error"]

        display.update_model_phase.assert_called_with("agent-1", "error", "测试错误")
        display.update_agent_status.assert_called_with("agent-1", "fail")
        display.set_result.assert_called_with("agent-1", error="测试错误")

    @pytest.mark.asyncio
    async def test_run_one_stagger_triggers_sleep(self, executor, mock_subagent_instance, mock_display):
        """stagger>0 时触发 asyncio.sleep，延迟时间为 min(stagger * base, STAGGER_MAX_DELAY * 3)"""
        sa = mock_subagent_instance
        display = mock_display

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep, \
                patch("random.uniform", return_value=0.2), \
                patch("src.core.parallel_executor.DisplayEventBus"):
            result = await executor._run_one(sa, display, stagger=2)

        assert result["result"] == "执行成功"
        mock_sleep.assert_awaited_once()
        # delay = min(2 * 0.2, 0.5 * 3) = min(0.4, 1.5) = 0.4
        args, _ = mock_sleep.await_args
        assert abs(args[0] - 0.4) < 0.001

    @pytest.mark.asyncio
    async def test_run_one_no_stagger_no_sleep(self, executor, mock_subagent_instance, mock_display):
        """stagger=0 时不触发 asyncio.sleep"""
        sa = mock_subagent_instance
        display = mock_display

        with patch("asyncio.sleep", AsyncMock()) as mock_sleep, \
                patch("src.core.parallel_executor.DisplayEventBus"):
            await executor._run_one(sa, display, stagger=0)

        mock_sleep.assert_not_awaited()


# ═══════════════════════════════════════════════════════════════
# _run_agents
# ═══════════════════════════════════════════════════════════════

class TestRunAgents:
    """_run_agents 异步方法"""

    @pytest.mark.asyncio
    async def test_basic_flow(self, executor, mock_display):
        """创建 agent → gather 执行 → 收集结果"""
        mock_sa1 = MagicMock()
        mock_sa1.label = "agent-1"
        mock_sa1.description = "任务1"
        mock_sa1.run = AsyncMock(return_value="结果1")

        mock_sa2 = MagicMock()
        mock_sa2.label = "agent-2"
        mock_sa2.description = "任务2"
        mock_sa2.run = AsyncMock(return_value="结果2")

        executor._spawner._agent_factory = MagicMock(side_effect=[mock_sa1, mock_sa2])
        display = mock_display

        specs = [
            {"description": "任务1", "prompt": "prompt1", "model": "gpt-4"},
            {"description": "任务2", "prompt": "prompt2"},
        ]

        with patch("asyncio.sleep", AsyncMock()), \
                patch("src.core.parallel_executor.DisplayEventBus"):
            results = await executor._run_agents(specs, display)

        assert len(results) == 2
        assert results[0]["label"] == "agent-1"
        assert results[0]["description"] == "任务1"
        assert results[0]["result"] == "结果1"
        assert results[0]["error"] == ""
        assert results[1]["label"] == "agent-2"
        assert results[1]["description"] == "任务2"
        assert results[1]["result"] == "结果2"
        assert results[1]["error"] == ""

        # 验证 agent factory 调用
        assert executor._spawner._agent_factory.call_count == 2
        executor._spawner._agent_factory.assert_any_call(
            label="agent-1", description="任务1", prompt="prompt1",
            parent_agent=executor.parent, model="gpt-4", agent_type="ordinary",
        )
        executor._spawner._agent_factory.assert_any_call(
            label="agent-2", description="任务2", prompt="prompt2",
            parent_agent=executor.parent, model=None, agent_type="ordinary",
        )

        # 验证 display 调用
        display.add_agent.assert_has_calls([
            call("agent-1", "任务1", status="running", agent_type="ordinary"),
            call("agent-2", "任务2", status="running", agent_type="ordinary"),
        ])
        display.start.assert_called_once()

        # 验证 run 调用
        mock_sa1.run.assert_awaited_once()
        mock_sa2.run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_single_agent(self, executor, mock_display):
        """单个 agent 也能正常执行"""
        mock_sa = MagicMock()
        mock_sa.label = "agent-1"
        mock_sa.description = "单任务"
        mock_sa.run = AsyncMock(return_value="结果")

        executor._spawner._agent_factory = MagicMock(return_value=mock_sa)
        display = mock_display

        specs = [{"description": "单任务", "prompt": "prompt"}]

        with patch("asyncio.sleep", AsyncMock()), \
                patch("src.core.parallel_executor.DisplayEventBus"):
            results = await executor._run_agents(specs, display)

        assert len(results) == 1
        assert results[0]["result"] == "结果"
        executor._spawner._agent_factory.assert_called_once()
        display.add_agent.assert_called_once_with("agent-1", "单任务", status="running", agent_type="ordinary")

    @pytest.mark.asyncio
    async def test_empty_specs(self, executor, mock_display):
        """空 specs 返回空结果列表"""
        with patch("src.core.parallel_executor.DisplayEventBus"):
            results = await executor._run_agents([], mock_display)

        assert results == []
        mock_display.start.assert_called_once()

    @pytest.mark.asyncio
    async def test_one_agent_fails(self, executor, mock_display):
        """其中一个 agent 异常时，结果列表中包含错误条目"""
        mock_sa_good = MagicMock()
        mock_sa_good.label = "agent-1"
        mock_sa_good.description = "正常任务"
        mock_sa_good.run = AsyncMock(return_value="正常结果")

        mock_sa_bad = MagicMock()
        mock_sa_bad.label = "agent-2"
        mock_sa_bad.description = "异常任务"
        mock_sa_bad.run = AsyncMock(side_effect=RuntimeError("执行失败"))

        executor._spawner._agent_factory = MagicMock(side_effect=[mock_sa_good, mock_sa_bad])
        display = mock_display

        specs = [
            {"description": "正常任务", "prompt": "prompt1"},
            {"description": "异常任务", "prompt": "prompt2"},
        ]

        with patch("asyncio.sleep", AsyncMock()), \
                patch("src.core.parallel_executor.DisplayEventBus"):
            results = await executor._run_agents(specs, display)

        assert len(results) == 2
        assert results[0]["result"] == "正常结果"
        assert results[0]["error"] == ""
        assert results[1]["result"] == ""
        assert "执行失败" in results[1]["error"]


# ═══════════════════════════════════════════════════════════════
# run
# ═══════════════════════════════════════════════════════════════

class TestRun:
    """run 独立模式异步方法"""

    @pytest.mark.asyncio
    async def test_run_basic(self, executor):
        """独立模式基本流程：创建 display → _run_agents → 返回结果"""
        specs = [{"description": "任务1", "prompt": "prompt1"}]

        with patch("src.core.parallel_executor.ParallelDisplay") as MockDisplay, \
                patch("src.core.parallel_executor.DisplayEventBus"), \
                patch.object(executor, "_run_agents") as mock_run_agents, \
                patch.object(executor, "_stream_results_markdown"), \
                patch.object(executor._spawner, "publish_summary"), \
                patch("asyncio.sleep", AsyncMock()):

            mock_display_instance = MagicMock()
            mock_display_instance.await_stop = AsyncMock()
            MockDisplay.return_value = mock_display_instance

            mock_run_agents.return_value = [
                {"label": "agent-1", "description": "任务1", "result": "结果", "error": ""},
            ]

            results = await executor.run(specs)

        assert len(results) == 1
        assert results[0]["result"] == "结果"
        mock_run_agents.assert_awaited_once_with(specs, mock_display_instance)
        mock_display_instance.await_stop.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_run_cancelled(self, executor):
        """独立模式被取消时构造降级结果并重新 raise"""
        specs = [{"description": "任务1", "prompt": "prompt1"}]

        with patch("src.core.parallel_executor.ParallelDisplay") as MockDisplay, \
                patch("src.core.parallel_executor.DisplayEventBus"), \
                patch.object(executor, "_stream_results_markdown"), \
                patch.object(executor._spawner, "publish_summary"):

            mock_display_instance = MagicMock()
            mock_display_instance.await_stop = AsyncMock()
            MockDisplay.return_value = mock_display_instance

            # 让 _run_agents 抛出 CancelledError
            with patch.object(executor, "_run_agents",
                              AsyncMock(side_effect=asyncio.CancelledError())):
                with pytest.raises(asyncio.CancelledError):
                    await executor.run(specs)

    @pytest.mark.asyncio
    async def test_run_exception(self, executor):
        """独立模式异常时降级为错误结果"""
        specs = [{"description": "任务1", "prompt": "prompt1"}]

        with patch("src.core.parallel_executor.ParallelDisplay") as MockDisplay, \
                patch("src.core.parallel_executor.DisplayEventBus"), \
                patch.object(executor, "_stream_results_markdown"), \
                patch.object(executor._spawner, "publish_summary"):

            mock_display_instance = MagicMock()
            mock_display_instance.await_stop = AsyncMock()
            MockDisplay.return_value = mock_display_instance

            with patch.object(executor, "_run_agents",
                              AsyncMock(side_effect=RuntimeError("意外错误"))):
                results = await executor.run(specs)

        assert len(results) == 1
        assert results[0]["result"] == ""
        assert "意外错误" in results[0]["error"]

    @pytest.mark.asyncio
    async def test_run_empty_specs(self, executor):
        """空 specs 返回空结果"""
        with patch("src.core.parallel_executor.ParallelDisplay"), \
                patch("src.core.parallel_executor.DisplayEventBus"), \
                patch.object(executor, "_run_agents", AsyncMock(return_value=[])), \
                patch.object(executor, "_stream_results_markdown"), \
                patch.object(executor._spawner, "publish_summary"):
            results = await executor.run([])
            assert results == []


# ═══════════════════════════════════════════════════════════════
# register_and_wait
# ═══════════════════════════════════════════════════════════════

class TestRegisterAndWait:
    """register_and_wait 异步方法"""

    @pytest.mark.asyncio
    async def test_barrier_zero_returns_early(self, executor):
        """_expected_count <= 0 时直接返回"""
        executor._expected_count = 0
        result = await executor.register_and_wait()
        assert result is None

    @pytest.mark.asyncio
    async def test_not_all_registered_waits(self, executor):
        """未达到预期数量时等待 _all_done"""
        executor._expected_count = 3
        executor._registered_count = 1
        executor._all_done = asyncio.Event()

        async def delayed_set():
            await asyncio.sleep(0.01)
            executor._all_done.set()

        # 第二次调用后 registered_count=2（仍未达到 3），等待 _all_done
        async def run():
            await asyncio.gather(
                executor.register_and_wait(),
                delayed_set(),
            )

        executor._registered_count = 2
        await run()
        assert executor._all_done.is_set()

    @pytest.mark.asyncio
    async def test_last_one_triggers_execute_all(self, executor):
        """最后一个注册者触发 _execute_all"""
        executor._expected_count = 2
        executor._registered_count = 1  # 已有1个注册

        with patch.object(executor, "_execute_all", AsyncMock()) as mock_exec:
            await executor.register_and_wait()

        mock_exec.assert_awaited_once()
        assert executor._registered_count == 2
