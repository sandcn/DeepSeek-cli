"""跨模块集成测试 — TUI 架构改进（步骤 14 集成代码）。

覆盖删除死代码后 app 启动路径完整、param_formatter 迁移后调用链、
cost 降级链路 webui 消费、公开 API 收敛后 tools/plugins 调用链、
幽灵导入修复后的 /theme 补全链路，以及 source 过滤谓词收敛。

原则：不依赖真实终端 I/O，使用 import 冒烟 + 纯函数调用 + mock 组件验证。
"""

from __future__ import annotations

import importlib

import pytest

from unittest.mock import MagicMock

from src.tui._const import is_agent_source
from src.tui._consumer import ChatUIConsumer


# ═══════════════════════════════════════════════════════════
# 场景 1：删除死代码后 app 启动路径完整
# ═══════════════════════════════════════════════════════════

class TestAppStartupImportChain:
    """从主入口到 TUI 装配的导入链完整性。"""

    def test_app_startup_import_chain_regression(self):
        """app 主入口 → application → app_loop → TUI 装配链可完整导入。"""
        import src.app  # noqa: F401  主入口 re-export
        import src.app_init.main  # noqa: F401  异步主入口
        import src.application  # noqa: F401  Application/InteractiveMode/SingleMode
        import src.app_loop  # noqa: F401
        import src.app_loop._single  # noqa: F401  SingleMode → ChatUIConsumer 路径
        import src.app_loop._loop  # noqa: F401  InteractiveMode 主循环
        import src.app_loop._session_setup  # noqa: F401  会话回调 → chat_ui 路径
        import src.tui  # noqa: F401  TUI 包公共导出面
        import src.tui._assembly  # noqa: F401  TuiAssembly.assemble 装配
        import src.tui._consumer  # noqa: F401  ChatUIConsumer

    def test_deleted_modules_no_residual_import_regression(self):
        """已删除死代码模块（步骤 2/6/8）不可再导入，无残留引用。"""
        deleted = (
            "src.tui.framework",
            "src.tui.config",
            "src.tui.render_buffer",
            "src.tui._input_reader",
            "src.tui._locks",
            "src.tui._param_formatter",
            "src.tui._buffer",
            "src.tui._cursor_tracker",
            "src.tui._cost",
            "src.tui._animator",
        )
        for mod_name in deleted:
            with pytest.raises(ImportError):
                importlib.import_module(mod_name)


# ═══════════════════════════════════════════════════════════
# 场景 2：param_formatter 迁移后 tool_executor_async 调用链
# ═══════════════════════════════════════════════════════════

class TestParamFormatterChain:
    """core/param_formatter 迁移后 core 工具执行链路。"""

    def test_param_formatter_tool_executor_chain_regression(self):
        """tool_executor_async 从 core.param_formatter 导入 extract_key_params 且行为正确。"""
        from src.core.param_formatter import extract_key_params

        # 已知工具参数裁剪：纯值（对齐 Claude Code `Read pyproject.toml`，非 JSON/k=v）
        assert extract_key_params("read_file", {"path": "src/main.py"}) == "src/main.py"
        assert extract_key_params("bash", '{"command": "ls -la"}') == "ls -la"
        # 未知工具：紧凑 `k=v` 空格连接（非 JSON 大括号）
        assert extract_key_params("unknown_tool", {"full": "value"}) == "full=value"
        # show_all=True：同样非 JSON（k=v 空格连接）
        assert extract_key_params("read_file", {"path": "a.py"}, show_all=True) == "path=a.py"

        # 消费方模块可正常导入（调用链完整）
        import src.core.tool_executor_async  # noqa: F401

    def test_param_formatter_old_path_removed_regression(self):
        """旧路径 src/tui/_param_formatter 已删除，禁止恢复引用。"""
        with pytest.raises(ImportError):
            importlib.import_module("src.tui._param_formatter")


# ═══════════════════════════════════════════════════════════
# 场景 3：cost 降级链路 webui 消费
# ═══════════════════════════════════════════════════════════

class TestCostDegradeChain:
    """_cost 删除后 webui cost_update 链路（handler 契约保留，费用推送降级跳过）。"""

    def test_webui_connection_import_chain_regression(self):
        """webui ws_handler.connection 可正常导入（cost 消费链路完整）。"""
        import src.webui.ws_handler.connection  # noqa: F401

    def test_webui_connection_no_cost_import_regression(self):
        """webui ws_handler.connection 不再依赖 src.tui._cost（模块已删除）。"""
        import sys

        # 幂等：防止其他测试进程内已引入 src.tui._cost
        sys.modules.pop("src.tui._cost", None)
        import src.webui.ws_handler.connection  # noqa: F401
        assert "src.tui._cost" not in sys.modules


# ═══════════════════════════════════════════════════════════
# 场景 4：公开 API 收敛后 tools/plugins 调用链
# ═══════════════════════════════════════════════════════════

class TestPublicApiChain:
    """ChatUIConsumer 公开访问器与 tools/plugins 调用点签名匹配。"""

    @staticmethod
    def _make_mock_components() -> dict:
        return {
            "engine": MagicMock(),
            "bottom_bar": MagicMock(),
            "rs": MagicMock(),
            "dispatcher": MagicMock(),
            "tui_renderer": MagicMock(),
            "cmpl_handler": MagicMock(),
            "input": MagicMock(),
        }

    def test_public_accessor_chain_regression(self):
        """get_input_component/get_input/request_bottom_redraw 与调用点签名匹配。"""
        components = self._make_mock_components()
        consumer = ChatUIConsumer.for_testing(components)

        # user_select.py L156 使用 get_input_component()
        assert consumer.get_input_component() is components["input"]
        # editmsg_plugin.py 使用 get_input()
        assert consumer.get_input() is components["input"]
        # _subagent_panel.py 使用 request_bottom_redraw()
        consumer.request_bottom_redraw()
        components["engine"].request_bottom_redraw.assert_called_once()

    def test_tools_plugin_import_chain_regression(self):
        """tools/plugins 消费方模块可正常导入（公开 API 收敛后链路完整）。"""
        import src.tools.user_select  # noqa: F401
        import src.tools.file_base  # noqa: F401
        import src.core.commands.plugins.editmsg_plugin  # noqa: F401
        import src.renderer._locks  # noqa: F401  锁真源

    def test_lifecycle_accessors_regression(self):
        """TuiLifecycle 公开访问器（bound_handlers/is_started/handlers_bound）可用。"""
        from src.tui._lifecycle import TuiLifecycle

        lifecycle = TuiLifecycle(
            engine=MagicMock(),
            bus=MagicMock(),
            bb=MagicMock(),
            rs=MagicMock(),
            dispatcher=MagicMock(),
        )
        assert lifecycle.is_started is False
        assert lifecycle.handlers_bound is False
        assert lifecycle.bound_handlers is None
        lifecycle.bound_handlers = {"type": MagicMock()}
        assert lifecycle.bound_handlers is not None


# ═══════════════════════════════════════════════════════════
# 场景 5：幽灵导入修复后的 /theme 补全链路
# ═══════════════════════════════════════════════════════════

class TestThemeCompletionChain:
    """_completion_engine 通过 core CommandUiAdapter 获取主题（幽灵导入已修复）。"""

    def test_theme_completion_chain_regression(self):
        """/theme 补全通过 CommandUiAdapter.get_theme_names_with_desc 返回真实主题名。"""
        from src.tui._completion_engine import CompletionEngine

        engine = CompletionEngine()
        themes = engine._theme_cache.get()
        assert isinstance(themes, list)
        assert len(themes) >= 1
        # 返回结构为 (name, desc) 二元组
        for name, desc in themes:
            assert isinstance(name, str)
            assert isinstance(desc, str)
        # 主题集合含 dark/light/high-contrast（CommandUiAdapter 经 ThemeRegistry）
        names = [name for name, _ in themes]
        assert "dark" in names and "light" in names and "high-contrast" in names

    def test_theme_adapter_concurrent_singleton_regression(self):
        """多线程并发首次访问 _fetch_themes 时 CommandUiAdapter 仅构造一次（双检锁）。

        双检锁（_THEME_ADAPTER_LOCK + 内层二次判空）保证懒加载单例线程安全：
        并发线程全部看到 _THEME_ADAPTER is None 并竞争锁，仅持锁线程构造，
        其余线程获锁后二次判空跳过构造。无锁实现下构造次数可能 > 1。
        """
        import threading
        from unittest.mock import patch

        import src.tui._completion_engine as _completion_engine
        from src.tui._completion_engine import CompletionEngine
        from src.core.commands._ui_adapter import CommandUiAdapter

        # 重置模块级单例为 None，确保并发首次访问路径（双检锁内层判空被触发）
        old_adapter = _completion_engine._THEME_ADAPTER
        _completion_engine._THEME_ADAPTER = None
        try:
            construct_count = 0
            real_init = CommandUiAdapter.__init__

            def _counting_init(self, *args, **kwargs):
                nonlocal construct_count
                construct_count += 1
                real_init(self, *args, **kwargs)

            threads_n = 8
            with patch.object(CommandUiAdapter, "__init__", _counting_init):
                engines = [CompletionEngine() for _ in range(threads_n)]
                barrier = threading.Barrier(threads_n)
                results: list[list] = []
                errors: list[BaseException] = []

                def _worker(idx: int) -> None:
                    try:
                        barrier.wait(timeout=10)
                        themes = engines[idx]._fetch_themes()
                        results.append(themes)
                    except BaseException as exc:  # noqa: BLE001 — 汇总并发线程异常
                        errors.append(exc)

                threads = [
                    threading.Thread(target=_worker, args=(i,)) for i in range(threads_n)
                ]
                for t in threads:
                    t.start()
                for t in threads:
                    t.join(timeout=15)

                assert not errors, f"并发访问 _fetch_themes 异常: {errors}"
                assert len(results) == threads_n
                # 全部线程返回非空主题列表（真实主题名）
                assert all(len(r) >= 1 for r in results)
                # 双检锁保证 CommandUiAdapter 恰好构造一次
                assert construct_count == 1, (
                    f"CommandUiAdapter 应仅构造一次，实际 {construct_count} 次"
                )
        finally:
            _completion_engine._THEME_ADAPTER = old_adapter


# ═══════════════════════════════════════════════════════════
# 场景 6：source 过滤谓词与错误截断收敛（方向②）
# ═══════════════════════════════════════════════════════════

class TestSharedPredicates:
    """_const 公共函数被渲染/装配/消费链路统一引用。"""

    def test_agent_source_filter_predicate_regression(self):
        """is_agent_source 语义：agent 与 agent-* 为真，其他为假。"""
        assert is_agent_source("agent") is True
        assert is_agent_source("agent-1") is True
        assert is_agent_source("agent-sub") is True
        assert is_agent_source("tool-executor") is False
        assert is_agent_source("parallel") is False
        assert is_agent_source(None) is False

    def test_dispatcher_source_filter_uses_predicate_regression(self):
        """EventDispatcher 默认过滤函数收敛至 _const.is_agent_source。"""
        from src.tui._dispatcher import EventDispatcher

        dispatcher = EventDispatcher(push_cmd=lambda cmd: None, filter_fn=None)
        assert dispatcher._default_filter_fn("agent") is True
        assert dispatcher._default_filter_fn("agent-2") is True
        assert dispatcher._default_filter_fn("parallel") is False
        assert dispatcher._default_filter_fn(None) is False

    def test_truncate_error_message_shared_regression(self):
        """错误截断公共函数（_const.truncate_error_message）边界行为。"""
        from src.tui._const import truncate_error_message

        # 未超长原样返回
        assert truncate_error_message("short", 80) == "short"
        # 超长保留前 max_length-3 字符 + "..."
        assert truncate_error_message("x" * 100, 80) == "x" * 77 + "..."
        # 空串/None
        assert truncate_error_message("", 80) == ""
        assert truncate_error_message(None, 80) == ""


# ═══════════════════════════════════════════════════════════
# 场景 7：跨方向③④⑤ 联动集成（步骤 11 集成代码）
# ═══════════════════════════════════════════════════════════

class TestCrossDirectionIntegration:
    """跨方向③（RenderCmd 双格式移除）④（publish_output 收敛）⑤（Input 委托）联动。

    覆盖步骤 11 要求的三个跨模块数据流：
      - RenderCmd 经 ChatUIConsumer 公开 API → engine → renderer handler 全链路
      - publish_output 经 core 适配器 → get_output_publisher → tui EventBus 发布
      - Input → InputParser 委托端到端（feed_byte → KeyEvent → dispatch → 缓冲）
    不依赖真实终端 I/O（mock 组件 + 纯函数调用）。
    """

    def test_render_cmd_full_chain_regression(self):
        """方向③：RenderCmd 经 ChatUIConsumer 公开 API 与 engine→renderer 内部链路。"""
        from unittest.mock import MagicMock

        from src.tui._const import (
            RenderCmd,
            ReasoningCmd,
            ToolCountIncCmd,
            NotificationCmd,
        )
        from src.tui._consumer import ChatUIConsumer
        from src.tui.ink.session import InkSession
        from src.tui.app.model import AppModel
        from src.tui.app.apply import apply_cmd

        # ── 公开 API 链路：ChatUIConsumer.on_* → engine.push_cmd(RenderCmd) ──
        components = {
            "engine": MagicMock(),
            "bottom_bar": MagicMock(),
            "rs": MagicMock(),
            "dispatcher": MagicMock(),
            "tui_renderer": MagicMock(),
            "cmpl_handler": MagicMock(),
            "input": MagicMock(),
        }
        consumer = ChatUIConsumer.for_testing(components)
        consumer.on_user_message("hi")
        cmd = components["engine"].push_cmd.call_args[0][0]
        assert isinstance(cmd, RenderCmd)
        assert cmd.text == "hi"

        # ── 内部链路：InkSession.push_cmd → apply_cmd → AppModel ──
        model = AppModel()
        session = InkSession(model=model, apply_cmd=apply_cmd)
        session.push_cmd(ReasoningCmd(text="reason"))
        session.push_cmd(ToolCountIncCmd())
        session.push_cmd(NotificationCmd(text="notify"))
        drained = []
        while not session._cmd_queue.empty():
            _, _, c = session._cmd_queue.get_nowait()
            drained.append(c)
        session._apply_commands(drained)

        assert any(b.kind == "reasoning" for b in model.blocks)
        assert model.status.tool_count == 1
        assert any(b.kind == "notification" for b in model.blocks)

    def test_output_publisher_core_to_tui_chain_regression(self):
        """方向④：core DefaultOutputAdapter.write() 经工厂 → tui publish_output → EventBus。"""
        from unittest.mock import patch

        from src.core.adapters.output import DefaultOutputAdapter
        from src.tui.events import DisplayEventBus
        from src.tui.events.event_types import OutputEvent

        received = []
        bus = DisplayEventBus.get_default()
        handler = lambda ev: received.append(ev)  # noqa: E731
        bus.subscribe(handler, event_type=OutputEvent)
        try:
            # 模拟 TUI 模式（活跃 ChatUI）→ get_output_publisher 返回 publish_output
            with patch("src.tui.consumer.get_active_chat_ui", return_value=object()):
                DefaultOutputAdapter().write("hello", level="info", source="core")
            assert len(received) == 1
            assert received[0].text == "hello"
            assert received[0].level == "info"
            assert received[0].source == "core"
        finally:
            bus.unsubscribe(handler, event_type=OutputEvent)

    def test_input_parser_delegation_end_to_end_regression(self):
        """方向⑤：Input.feed_byte 委托 InputParser → KeyEvent → dispatch → 缓冲。"""
        from pathlib import Path

        from src.tui._input import Input
        from src.tui._input_parser import InputParser

        input_ = Input(fd=0, history_file=Path("/tmp/nonexistent_input_history"))
        # 委托：可打印字符 → InputParser.feed_byte → char 事件
        ev = input_.feed_byte(ord("a"))
        assert ev is not None and ev.kind == "char" and ev.char == "a"
        # dispatch → 缓冲编辑
        input_._dispatch_key_event(ev)
        assert input_.get_current_text() == "a"
        # 静态转发：控制字符
        enter_ev = input_._decode_control_char(0x0d)
        assert enter_ev.kind == "enter"
        # ESC → None（完整序列需 I/O，仅验证委托返回）
        assert input_.feed_byte(0x1b) is None
        # 策略对象独立可用（InputParser 直接等价）
        parser = InputParser()
        ev2 = parser.feed_byte(ord("b"))
        assert ev2 is not None and ev2.kind == "char" and ev2.char == "b"


# ═══════════════════════════════════════════════════════════
# 横切步骤18 — 端到端工具命令流（工具状态语义）
# ═══════════════════════════════════════════════════════════

class TestToolCardChain:
    """横切步骤18 — TOOL_OPEN → TOOL_OUTPUT → TOOL_CLOSE 端到端命令流。

    验证方向D 步骤15 新工具状态语义在「事件 → 命令 → 模型」链路上的落地：
      - 关闭成功 → tool_status=done、输出完整可见；
      - 关闭失败 → tool_status=fail。
    """

    def _drive(self, model, cmds):
        """批量应用命令（等价 InkSession._apply_commands）。"""
        from src.tui.app.apply import apply_cmd
        for cmd in cmds:
            apply_cmd(model, cmd)

    def test_tool_open_output_close_status_done_regression(self):
        """成功关闭：状态 done、输出完整可见。"""
        from src.tui._const import ToolOpenCmd, ToolOutputCmd, ToolCloseCmd
        from src.tui.app.model import AppModel
        from src.tools.registry import get_tool_display_name

        model = AppModel()
        self._drive(model, [
            ToolOpenCmd(tool_id="t1", tool_name="bash", detail="ls"),
            ToolOutputCmd(tool_id="t1", text="file1\nfile2"),
            ToolCloseCmd(tool_id="t1", success=True),
        ])
        tool_blocks = [b for b in model.blocks if b.kind == "tool"]
        assert len(tool_blocks) == 1
        block = tool_blocks[0]
        assert block.extra["tool_status"] == "done"
        assert block.closed is True
        # 阶段5：工具卡由 ToolCard 从 block.lines 渲染（不冻结 _cached_ink_lines）
        assert block._cached_ink_lines is None
        # 标题含工具显示名（工具名经 registry 显示名映射）
        display = get_tool_display_name("bash") or "bash"
        assert display in block.lines[0].plain
        # 状态底行（✔）
        assert "\u2714" in block.lines[-1].plain

    def test_tool_close_fail_status_fail_regression(self):
        """失败关闭：状态 fail、底行 ✖。"""
        from src.tui._const import ToolOpenCmd, ToolCloseCmd
        from src.tui.app.model import AppModel

        model = AppModel()
        self._drive(model, [
            ToolOpenCmd(tool_id="t2", tool_name="bash"),
            ToolCloseCmd(tool_id="t2", success=False),
        ])
        block = model.blocks[-1]
        assert block.kind == "tool"
        assert block.extra["tool_status"] == "fail"
        assert "\u2716" in block.lines[-1].plain
