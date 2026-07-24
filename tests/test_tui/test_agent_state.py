"""测试 AgentStateStore — SubAgent 状态管理存储。"""

from __future__ import annotations

import threading
import time
from src.tui.state.agent_state import AgentStateStore, AgentSlot, ToolRecord


class TestAgentStateStore:
    """AgentStateStore 核心功能测试。"""

    def test_add_agent(self):
        """add_agent 创建新 Agent 并记录顺序。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "第一个 agent", status="running")
        store.add_agent("agent-2", "第二个 agent", status="running")
        assert store.agent_count == 2
        assert store.get_order() == ["agent-1", "agent-2"]

    def test_add_agent_with_type(self):
        """add_agent 支持 agent_type 参数。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "executor", agent_type="execute")
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.agent_type == "execute"

    def test_get_slot_returns_deep_copy(self):
        """get_slot 返回深拷贝，修改不影响原数据。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "深拷贝测试", status="running")
        slot = store.get_slot("agent-1")
        assert slot is not None
        slot.status = "done"
        original = store.get_slot("agent-1")
        assert original is not None
        assert original.status == "running"

    def test_get_slot_nonexistent(self):
        """get_slot 不存在时返回 None。"""
        store = AgentStateStore()
        assert store.get_slot("nonexistent") is None

    def test_remove_agent(self):
        """remove_agent 移除 Agent 并更新顺序。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "第一个")
        store.add_agent("agent-2", "第二个")
        store.remove_agent("agent-1")
        assert store.agent_count == 1
        assert store.get_order() == ["agent-2"]
        assert store.get_slot("agent-1") is None

    def test_remove_nonexistent_agent(self):
        """remove_agent 不存在时安全无报错。"""
        store = AgentStateStore()
        store.remove_agent("nonexistent")  # 不应抛异常

    def test_version_increments_on_add(self):
        """add_agent 增加版本号。"""
        store = AgentStateStore()
        v0 = store.version
        store.add_agent("agent-1", "增版本")
        assert store.version > v0

    def test_version_increments_on_remove(self):
        """remove_agent 增加版本号。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试")
        v1 = store.version
        store.remove_agent("agent-1")
        assert store.version > v1


class TestAgentStateStoreStatus:
    """状态更新测试。"""

    def test_update_agent_status_done(self):
        """update_agent_status 更新为 done 后记录 end_time。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_agent_status("agent-1", "done")
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.status == "done"
        assert slot.end_time > 0

    def test_update_agent_status_fail(self):
        """update_agent_status 更新为 fail 时自动将活跃 tool 标记为 fail。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{"cmd":"ls"}')
        store.tool_start("agent-1", "bash", '{"cmd":"ls"}')
        store.update_agent_status("agent-1", "fail")
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.status == "fail"
        assert len(slot.tool_history) == 1
        assert slot.tool_history[0].phase == "fail"

    def test_update_model_phase(self):
        """update_model_phase 更新模型阶段。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_model_phase("agent-1", "reasoning")
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.model_phase == "reasoning"

    def test_update_model_phase_resets_timer_on_change(self):
        """update_model_phase 在阶段切换时重置计时器。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_model_phase("agent-1", "reasoning")
        t1 = store.get_slot("agent-1").model_phase_start
        time.sleep(0.01)
        store.update_model_phase("agent-1", "content")
        t2 = store.get_slot("agent-1").model_phase_start
        assert t2 > t1

    def test_update_model_phase_nonexistent(self):
        """update_model_phase 对不存在的 label 安全无报错。"""
        store = AgentStateStore()
        store.update_model_phase("nonexistent", "thinking")  # 不应抛异常

    def test_update_nonexistent_agent(self):
        """对不存在的 Agent 调用更新方法时安全无报错。"""
        store = AgentStateStore()
        store.update_agent_status("ghost", "done")  # 不应抛异常
        store.update_model_phase("ghost", "thinking")  # 不应抛异常
        store.tool_parsing("ghost", "bash", "{}")  # 不应抛异常


class TestAgentStateStoreToolHistory:
    """工具调用记录测试。"""

    def test_tool_parsing_creates_new_record(self):
        """tool_parsing 创建新的 ToolRecord。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{"cmd":"ls"}')
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert len(slot.tool_history) == 1
        assert slot.tool_history[0].tool_name == "bash"
        assert slot.tool_history[0].phase == "parsing"
        assert slot.total_calls == 1

    def test_tool_parsing_updates_existing_parsing_record(self):
        """同一工具的后续 parsing chunk 更新已有记录的 detail。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{"cmd":"l')
        store.tool_parsing("agent-1", "bash", '{"cmd":"ls"}')
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert len(slot.tool_history) == 1  # 不追加新记录
        assert slot.tool_history[0].detail == '{"cmd":"ls"}'
        # total_calls 只应在首次创建时增加
        assert slot.total_calls == 1

    def test_tool_parsing_multiple_tools(self):
        """多个工具的 parsing 创建独立记录。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{"cmd":"ls"}')
        store.tool_parsing("agent-1", "python", '{"code":"print(1)"}')
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert len(slot.tool_history) == 2
        assert slot.total_calls == 2

    def test_tool_start_transitions_from_parsing(self):
        """tool_start 将 parsing 记录转为 running。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{"cmd":"ls"}')
        store.tool_start("agent-1", "bash", '{"cmd":"ls"}')
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.tool_history[0].phase == "running"

    def test_tool_start_creates_new_record_if_no_parsing(self):
        """无 parsing 记录时 tool_start 创建新 running 记录。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_start("agent-1", "bash", '{"cmd":"ls"}')
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert len(slot.tool_history) == 1
        assert slot.tool_history[0].phase == "running"

    def test_tool_done_with_name(self):
        """tool_done 使用 tool_name 匹配并更新为 done。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_parsing("agent-1", "bash", '{}')
        store.tool_start("agent-1", "bash", '{}')
        store.tool_done("agent-1", "bash", success=True)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.tool_history[0].phase == "done"
        assert slot.tool_history[0].end_time > 0

    def test_tool_done_without_name(self):
        """tool_done 无 tool_name 时自动匹配最后一个活跃记录。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_start("agent-1", "bash", '{}')
        store.tool_done("agent-1", success=True)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.tool_history[0].phase == "done"

    def test_tool_done_fail(self):
        """tool_done success=False 标记为 fail。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_start("agent-1", "bash", '{}')
        store.tool_done("agent-1", "bash", success=False)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.tool_history[0].phase == "fail"

    def test_tool_done_nonexistent_agent(self):
        """tool_done 对不存在的 Agent 安全无报错。"""
        store = AgentStateStore()
        store.tool_done("ghost", "bash")  # 不应抛异常

    def test_empty_tool_history(self):
        """空 tool_history 时 tool_done 安全无报错。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_done("agent-1", "bash")  # tool_history 为空，安全通过

    def test_tool_batch_start(self):
        """tool_batch_start 设置并行批处理阶段。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.tool_batch_start("agent-1", ["bash", "python"])
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.model_phase == "batch"
        assert "bash, python" in slot.model_info
        assert "2x parallel" in slot.model_info


class TestAgentStateStoreUsage:
    """用量统计测试。"""

    def test_update_usage_accumulate(self):
        """update_usage 累加模式。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_usage("agent-1", {"input": 10, "output": 20})
        store.update_usage("agent-1", {"input": 5, "output": 3})
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.input_tokens == 15
        assert slot.output_tokens == 23

    def test_update_usage_replace(self):
        """update_usage replace 模式覆盖值并清零 live 计数。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_usage("agent-1", {"input": 100, "output": 200})
        store.update_live_output("agent-1", 50)
        slot_before = store.get_slot("agent-1")
        assert slot_before is not None
        assert slot_before.live_output_tokens == 50

        store.update_usage("agent-1", {"input": 300, "output": 400}, replace=True)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.input_tokens == 300
        assert slot.output_tokens == 400
        assert slot.live_output_tokens == 0

    def test_update_usage_with_speed(self):
        """update_usage 更新速度值。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_usage("agent-1", {"speed": 15.5})
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.last_speed == 15.5

    def test_update_usage_zero_speed_ignored(self):
        """update_usage 速度为 0 时忽略（不覆盖）。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_usage("agent-1", {"speed": 10.0})
        store.update_usage("agent-1", {"speed": 0.0})
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.last_speed == 10.0  # 不应被 0 覆盖

    def test_update_tokens(self):
        """update_tokens 快捷输出 token 更新。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_tokens("agent-1", 50)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.output_tokens == 50

    def test_update_live_output(self):
        """update_live_output 累加实时输出 token。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_live_output("agent-1", 10)
        store.update_live_output("agent-1", 20)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.live_output_tokens == 30

    def test_update_live_input(self):
        """update_live_input 仅在 input_tokens==0 时设置。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_live_input("agent-1", 99)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.live_input_tokens == 99

    def test_update_live_input_after_usage_ignored(self):
        """input_tokens>0 后 update_live_input 被忽略。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_usage("agent-1", {"input": 100})
        store.update_live_input("agent-1", 99)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.live_input_tokens == 0  # input_tokens>0，忽略

    def test_update_speed(self):
        """update_speed 更新输出速度。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_speed("agent-1", 25.0)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.last_speed == 25.0

    def test_update_speed_zero_ignored(self):
        """update_speed 速度 0 时忽略。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_speed("agent-1", 30.0)
        store.update_speed("agent-1", 0.0)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.last_speed == 30.0

    def test_update_parse_info(self):
        """update_parse_info 更新解析信息。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.update_parse_info("agent-1", "bash grep", 150, 2.5)
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.model_phase == "parsing"
        assert "bash grep" in slot.model_info
        assert "150t" in slot.model_info
        assert "2.5s" in slot.model_info

    def test_set_result(self):
        """set_result 存储执行结果。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "测试", status="running")
        store.set_result("agent-1", result_text="成功", error="")
        slot = store.get_slot("agent-1")
        assert slot is not None
        assert slot.result_text == "成功"
        assert slot.result_error == ""


class TestAgentStateStoreSnapshot:
    """快照与脏标记测试。"""

    def test_snapshot_all(self):
        """snapshot_all 返回所有 Agent 快照。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        store.add_agent("agent-2", "B", status="running")
        snap = store.snapshot_all()
        assert len(snap) == 2
        assert "agent-1" in snap
        assert "agent-2" in snap

    def test_snapshot_all_deep_copy(self):
        """snapshot_all 返回深拷贝。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        snap = store.snapshot_all()
        snap["agent-1"].status = "done"
        original = store.get_slot("agent-1")
        assert original is not None
        assert original.status == "running"

    def test_dirty_after_add(self):
        """add_agent 后 label 在 dirty 集合中。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        _, dirty, _ = store.snapshot_dirty()
        assert "agent-1" in dirty

    def test_dirty_after_update(self):
        """update_agent_status 后 label 在 dirty 集合中。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        store.update_agent_status("agent-1", "done")
        _, dirty, _ = store.snapshot_dirty()
        assert "agent-1" in dirty

    def test_mark_clean(self):
        """mark_clean 清除脏标记。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        _, _, ver = store.snapshot_dirty()
        assert store.mark_clean(ver) is True
        _, dirty, _ = store.snapshot_dirty()
        assert len(dirty) == 0

    def test_mark_clean_version_mismatch(self):
        """版本不匹配时 mark_clean 返回 False。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        result = store.mark_clean(0)  # 传入老版本号
        assert result is False

    def test_dirty_snapshots_content(self):
        """snapshot_dirty 返回的脏快照包含正确数据。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        store.tool_parsing("agent-1", "bash", '{}')
        snaps, dirty, ver = store.snapshot_dirty()
        assert ver > 0
        assert "agent-1" in dirty
        assert "agent-1" in snaps
        slot = snaps["agent-1"]
        assert len(slot.tool_history) == 1

    def test_snapshot_dirty_does_not_include_removed_agents(self):
        """snapshot_dirty 不包含已移除的 Agent。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "A", status="running")
        store.remove_agent("agent-1")
        snaps, dirty, _ = store.snapshot_dirty()
        assert "agent-1" not in snaps


class TestAgentStateStoreProperties:
    """属性测试。"""

    def test_agent_count(self):
        """agent_count 返回正确计数。"""
        store = AgentStateStore()
        assert store.agent_count == 0
        store.add_agent("a1", "A", status="running")
        assert store.agent_count == 1
        store.add_agent("a2", "B", status="running")
        assert store.agent_count == 2
        store.remove_agent("a1")
        assert store.agent_count == 1

    def test_has_running_agents(self):
        """has_running_agents 正确检测运行中的 Agent。"""
        store = AgentStateStore()
        assert store.has_running_agents is False
        store.add_agent("a1", "A", status="running")
        assert store.has_running_agents is True
        store.update_agent_status("a1", "done")
        assert store.has_running_agents is False

    def test_has_running_agents_multiple(self):
        """多个 Agent 时 has_running_agents 正确判断。"""
        store = AgentStateStore()
        store.add_agent("a1", "A", status="done")
        store.add_agent("a2", "B", status="running")
        assert store.has_running_agents is True

    def test_version_property(self):
        """version 属性返回当前版本号。"""
        store = AgentStateStore()
        v = store.version
        assert isinstance(v, int)
        store.add_agent("a1", "A", status="running")
        assert store.version > v


class TestAgentStateStoreConcurrency:
    """并发安全性测试。"""

    def test_concurrent_add(self):
        """多线程并发添加 Agent 不丢数据。"""
        store = AgentStateStore()
        n = 50

        def add_agents(start: int):
            for i in range(n):
                store.add_agent(f"agent-{start + i}", f"并发测试 #{start + i}", status="running")

        t1 = threading.Thread(target=add_agents, args=(0,))
        t2 = threading.Thread(target=add_agents, args=(n,))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert store.agent_count == 2 * n

    def test_concurrent_update(self):
        """多线程并发更新 Agent 状态不崩溃。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "并发更新", status="running")

        def update():
            for _ in range(100):
                store.tool_parsing("agent-1", "bash", "{}")
                store.tool_start("agent-1", "bash", "{}")
                store.tool_done("agent-1", "bash", success=True)
                store.update_usage("agent-1", {"input": 1, "output": 2})

        t1 = threading.Thread(target=update)
        t2 = threading.Thread(target=update)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        slot = store.get_slot("agent-1")
        assert slot is not None
        # 至少有一些工具调用记录
        assert slot.total_calls >= 1

    def test_concurrent_snapshot_and_update(self):
        """快照与更新并发不崩溃。"""
        store = AgentStateStore()
        store.add_agent("agent-1", "并发快照", status="running")

        stop = threading.Event()

        def updater():
            while not stop.is_set():
                store.tool_parsing("agent-1", "bash", "{}")
                store.tool_start("agent-1", "bash", "{}")
                store.tool_done("agent-1", "bash", success=True)

        def snapshot():
            while not stop.is_set():
                store.snapshot_all()
                store.snapshot_dirty()
                store.get_slot("agent-1")

        t1 = threading.Thread(target=updater)
        t2 = threading.Thread(target=snapshot)
        t1.start()
        t2.start()
        time.sleep(0.1)
        stop.set()
        t1.join(timeout=1)
        t2.join(timeout=1)


class TestAgentSlotDeepCopy:
    """AgentSlot.deep_copy 测试。"""

    def test_deep_copy_basic(self):
        """deep_copy 创建独立副本。"""
        slot = AgentSlot(label="test", description="测试")
        copy = slot.deep_copy()
        assert copy.label == slot.label
        assert copy.description == slot.description
        copy.status = "done"
        assert slot.status == "running"

    def test_deep_copy_tool_history(self):
        """deep_copy 中的 tool_history 是独立对象。"""
        slot = AgentSlot(label="test", description="测试")
        slot.tool_history.append(ToolRecord(tool_name="bash", detail="{}"))
        copy = slot.deep_copy()
        assert len(copy.tool_history) == 1
        copy.tool_history[0].tool_name = "python"
        assert slot.tool_history[0].tool_name == "bash"


class TestToolRecord:
    """ToolRecord 基础测试。"""

    def test_default_phase(self):
        """ToolRecord 默认 phase 为 parsing。"""
        rec = ToolRecord(tool_name="bash", detail="{}")
        assert rec.phase == "parsing"

    def test_default_times(self):
        """ToolRecord 默认 start_time 为 0.0（由调用方设置）。"""
        rec = ToolRecord(tool_name="bash", detail="{}")
        assert rec.start_time == 0.0
        assert rec.end_time == 0.0
