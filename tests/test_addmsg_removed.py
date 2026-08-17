"""/addmsg 功能删除验证测试。

覆盖链路：
1. 模块级删除（src.core.middleware.addmsg / addmsg_plugin 不可导入）
2. 插件注册表（get_plugin_registry 不含 addmsg）
3. BaseAgent 符号删除（add_addmsg / drain_addmsg / has_pending_addmsg /
   insert_addmsg_messages / set_addmsg_input_provider /
   set_addmsg_chat_ui_provider / _addmsg_queue 等不再存在）
4. Pipeline 删除（PipelineContext 无 addmsg_inserted 字段；
   run_round_async 不再引用 addmsg）
5. Input 删除（_input.Input / InputBufferEditor 无 peek_queued_input）
6. 回归：其余交互命令插件仍正常注册、中间件管道不受影响
"""
from __future__ import annotations

import importlib

import pytest

from src.core.base_agent import BaseAgent
from src.core.commands import get_plugin_registry
from src.core.pipeline import Pipeline, PipelineContext


# ═══════════════════════════════════════════════════════════════
# 1. 模块级删除
# ═══════════════════════════════════════════════════════════════

class TestModuleRemoved:
    def test_middleware_addmsg_not_importable(self):
        with pytest.raises(ImportError):
            importlib.import_module("src.core.middleware.addmsg")

    def test_addmsg_plugin_not_importable(self):
        with pytest.raises(ImportError):
            importlib.import_module("src.core.commands.plugins.addmsg_plugin")


# ═══════════════════════════════════════════════════════════════
# 2. 插件注册表
# ═══════════════════════════════════════════════════════════════

class TestPluginRegistry:
    def test_addmsg_not_registered(self):
        import src.core.commands.plugins  # noqa: F401  # 触发插件模块注册
        registry = get_plugin_registry()
        assert registry.exists("addmsg") is False
        assert registry.get("addmsg") is None

    def test_other_interactive_plugins_still_registered(self):
        import src.core.commands.plugins  # noqa: F401
        registry = get_plugin_registry()
        for name in ("model", "editmsg", "deitmsg", "loop", "skill"):
            assert registry.exists(name) is True, f"命令 /{name} 不应被误删"


# ═══════════════════════════════════════════════════════════════
# 3. BaseAgent 符号删除
# ═══════════════════════════════════════════════════════════════

class TestBaseAgentRemoved:
    def test_addmsg_methods_removed(self):
        for name in (
            "add_addmsg",
            "drain_addmsg",
            "has_pending_addmsg",
            "insert_addmsg_messages",
            "set_addmsg_input_provider",
            "set_addmsg_chat_ui_provider",
        ):
            assert not hasattr(BaseAgent, name), f"BaseAgent.{name} 应已删除"

    def test_addmsg_fields_removed(self):
        agent = BaseAgent()
        for name in (
            "_addmsg_queue",
            "_addmsg_input_provider",
            "_addmsg_chat_ui_provider",
        ):
            assert not hasattr(agent, name), f"BaseAgent 实例 {name} 应已删除"

    def test_background_task_fields_kept(self):
        """回归：后台任务（bash background=True）管理不受影响。"""
        agent = BaseAgent()
        assert hasattr(agent, "_background_tasks")
        assert hasattr(agent, "_process_background_tasks")


# ═══════════════════════════════════════════════════════════════
# 4. Pipeline 删除
# ═══════════════════════════════════════════════════════════════

class TestPipelineRemoved:
    def test_pipeline_context_no_addmsg_inserted(self):
        assert "addmsg_inserted" not in PipelineContext.__dataclass_fields__

    def test_pipeline_run_no_addmsg_reference(self):
        import inspect
        source = inspect.getsource(Pipeline.run_round_async)
        assert "addmsg" not in source

    def test_pipeline_middleware_registration_unaffected(self):
        """回归：Pipeline 中间件注册机制不受影响。"""
        p = Pipeline()
        assert p.async_middlewares == []


# ═══════════════════════════════════════════════════════════════
# 5. Input 删除
# ═══════════════════════════════════════════════════════════════

class TestInputRemoved:
    def test_input_no_peek_queued_input(self):
        from src.tui._input import Input
        assert not hasattr(Input, "peek_queued_input")

    def test_input_buffer_editor_no_peek_queued_input(self):
        from src.tui._input_buffer import InputBufferEditor
        assert not hasattr(InputBufferEditor, "peek_queued_input")

    def test_queued_input_api_kept(self):
        """回归：排队输入核心 API 保留。"""
        from src.tui._input import Input
        from src.tui._input_buffer import InputBufferEditor
        for cls in (Input, InputBufferEditor):
            assert hasattr(cls, "get_queued_input")
            assert hasattr(cls, "has_queued_input")
            assert hasattr(cls, "drain_all")
