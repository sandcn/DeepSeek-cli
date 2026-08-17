"""webui 删除验证测试。

覆盖链路：
1. 模块级删除（src.webui 不可导入，src 不再含 webui 目录）
2. 命令行删除（webui 子命令 / --webui 不再被 _parse_args 接受；
   run/session/version 子命令保留）
3. 工具类删除（Func 及子类不再有 web_display /
   _web_display_result_template / _web_print）
4. 事件删除（UserSelectNeededEvent 不再导出）
5. 运行模式删除（ParallelExecutor / SubAgentSpawner 不再有 is_web；
   _NullDisplayPort 无 is_web；ToolCallbackChain._run_interactive 无 is_web 参数）
6. 回归：display 方法保留、SubagentPromptEvent 等核心事件保留
"""
from __future__ import annotations

import importlib
import inspect
import sys

import pytest

from src.app_init._args import _parse_args
from src.core.adapters.null import _NullDisplayPort
from src.core.internal.agent._subagent_spawner import SubAgentSpawner
from src.core.internal.agent._tool_callbacks import ToolCallbackChain
from src.core.parallel_executor import ParallelExecutor
from src.tools.base import Func


# ═══════════════════════════════════════════════════════════════
# 1. 模块级删除
# ═══════════════════════════════════════════════════════════════

class TestModuleRemoved:
    def test_src_webui_not_importable(self):
        with pytest.raises(ImportError):
            importlib.import_module("src.webui")

    def test_src_webui_server_not_importable(self):
        with pytest.raises(ImportError):
            importlib.import_module("src.webui.server")


# ═══════════════════════════════════════════════════════════════
# 2. 命令行删除
# ═══════════════════════════════════════════════════════════════

class TestArgsRemoved:
    def test_webui_subcommand_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["chat.py", "webui"])
        with pytest.raises(SystemExit):
            _parse_args()

    def test_webui_flag_rejected(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["chat.py", "--webui"])
        with pytest.raises(SystemExit):
            _parse_args()

    def test_run_subcommand_kept(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["chat.py", "run"])
        args = _parse_args()
        assert args.command == "run"

    def test_session_subcommand_kept(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["chat.py", "session", "list"])
        args = _parse_args()
        assert args.command == "session"
        assert args.session_cmd == "list"

    def test_version_subcommand_kept(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["chat.py", "version"])
        args = _parse_args()
        assert args.command == "version"


# ═══════════════════════════════════════════════════════════════
# 3. 工具类删除
# ═══════════════════════════════════════════════════════════════

class TestToolMethodRemoved:
    def test_func_base_no_web_display(self):
        assert not hasattr(Func, "web_display")
        assert not hasattr(Func, "_web_display_result_template")
        assert not hasattr(Func, "_web_print")

    @pytest.mark.parametrize("mod_name,cls_name", [
        ("src.tools.ls", "LsFunc"),
        ("src.tools.find", "FindFunc"),
        ("src.tools.search", "SearchFunc"),
        ("src.tools.read_file", "ReadFileFunc"),
        ("src.tools.bash", "BashFunc"),
        ("src.tools.file_base", "FileToolBase"),
        ("src.tools.user_select", "UserSelectFunc"),
    ])
    def test_tool_classes_no_web_display(self, mod_name, cls_name):
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        assert not hasattr(cls, "web_display")

    @pytest.mark.parametrize("mod_name,cls_name", [
        ("src.tools.ls", "LsFunc"),
        ("src.tools.find", "FindFunc"),
        ("src.tools.search", "SearchFunc"),
        ("src.tools.read_file", "ReadFileFunc"),
        ("src.tools.bash", "BashFunc"),
        ("src.tools.user_select", "UserSelectFunc"),
    ])
    def test_tool_classes_display_kept(self, mod_name, cls_name):
        mod = importlib.import_module(mod_name)
        cls = getattr(mod, cls_name)
        assert hasattr(cls, "display")


# ═══════════════════════════════════════════════════════════════
# 4. 事件删除
# ═══════════════════════════════════════════════════════════════

class TestEventRemoved:
    def test_user_select_needed_event_not_exported(self):
        from src.tui import events as ev
        assert not hasattr(ev, "UserSelectNeededEvent")

    def test_user_select_needed_event_not_in_event_types(self):
        from src.tui.events import event_types as et
        assert not hasattr(et, "UserSelectNeededEvent")
        assert all(
            getattr(t, "__name__", "") != "UserSelectNeededEvent"
            for t in et.ALL_EVENT_TYPES
        )

    def test_core_events_kept(self):
        from src.tui import events as ev
        for name in ("SubagentPromptEvent", "AgentResultEvent",
                     "ToolOutputChunkEvent", "OutputEvent"):
            assert hasattr(ev, name), f"{name} 不应被误删"


# ═══════════════════════════════════════════════════════════════
# 5. 运行模式删除
# ═══════════════════════════════════════════════════════════════

class TestRunModeRemoved:
    def test_parallel_executor_no_is_web(self):
        params = inspect.signature(ParallelExecutor.__init__).parameters
        assert "is_web" not in params

    def test_subagent_spawner_no_is_web(self):
        params = inspect.signature(SubAgentSpawner.__init__).parameters
        assert "is_web" not in params

    def test_null_display_port_no_is_web(self):
        assert not hasattr(_NullDisplayPort, "is_web")
        assert not hasattr(_NullDisplayPort(), "is_web")

    def test_tool_callbacks_no_is_web_params(self):
        params = inspect.signature(ToolCallbackChain._run_interactive).parameters
        assert "is_web" not in params
        params = inspect.signature(ToolCallbackChain._run_with_capture).parameters
        assert "is_web" not in params

    def test_app_loop_modes_no_is_web_params(self):
        from src.app_loop._loop import run_interactive_mode_async
        from src.app_loop._single import run_single_mode_async
        params = inspect.signature(run_interactive_mode_async).parameters
        assert "is_web" not in params
        params = inspect.signature(run_single_mode_async).parameters
        assert "is_web" not in params


# ═══════════════════════════════════════════════════════════════
# 6. 回归：user_select 不引用 webui
# ═══════════════════════════════════════════════════════════════

class TestUserSelectNoWebUi:
    def test_user_select_module_has_no_webui_import(self):
        import src.tools.user_select as us
        assert not hasattr(us, "web_display")
        assert not hasattr(us.UserSelectFunc, "web_display")

    def test_user_select_source_no_pending_selects(self):
        import inspect as _inspect
        import src.tools.user_select as us
        source = _inspect.getsource(us)
        assert "webui" not in source
        assert "pending_selects" not in source
