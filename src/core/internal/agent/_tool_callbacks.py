"""ToolCallbackChain — Agent 工具回调链的独立封装。

从 agent.py 提取，封装工具执行的完整生命周期：
  handle_tool_calls → _run_tool_method → _on_before_tool / _on_after_tool

工具执行通过 ToolScheduler.schedule() 统一调度（DAG 依赖分析 + 拓扑排序 + 分层并发），
ToolScheduler 为全局单例，内聚 ToolDAG 构建 + 调度 + 并发控制。
"""

from __future__ import annotations

import asyncio
import json
import logging
from ...parallel_executor import ParallelExecutor
from ...tool_executor_async import ToolScheduler
from ...telemetry import get_default_collector
from ....api.tokens import estimate_tokens
from ....tools.base import Func
from ....tools.registry import get_tool_display_name

_logger = logging.getLogger(__name__)


def _safe_json_dumps(obj) -> str:
    """安全序列化任意对象为 JSON 字符串，失败时回退为 str(obj)。"""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(obj)


async def _run_file_display(func, display=None):
    """通过 display 管道执行文件操作工具（write_file/update_file），获取 diff 输出。

    SubAgent 和 Agent 的 _run_with_capture 对文件工具都需要走 display 路径：
    - ParallelDisplay 场景：用 capture_and_print_async 捕获差异并打印到终端
    - 普通 display 场景：回退到 func.display()（自带 stdout 捕获）
    """
    if display is not None and hasattr(display, 'capture_and_print_async'):
        return await display.capture_and_print_async(func.display)
    return await func.display()


def _spinner_refresher(display, tool_label: str):
    """（废弃）Spinner 刷新已迁移至 ChatUIConsumer（chat_ui.py）。"""
    return None


class ToolCallbackChain:
    """工具回调链 — 封装 Agent 中工具调用的完整生命周期。

    接受 agent 实例作为构造参数，通过 self._agent 访问 agent 属性。
    提取自 agent.py：_handle_tool_calls / _run_tool_method / _on_before_tool /
    _on_after_tool / _sanitize_args_for_log / _detect_webdiff / _show_tool_execution_summary。
    """

    def __init__(self, agent):
        self._agent = agent

    # ── 工具调用主入口 ──────────────────────────────────

    async def handle_tool_calls(self, content, tool_calls, reasoning_content=None, usage=None):
        """处理工具调用，通过 ToolScheduler.default().schedule() 统一调度。

        ToolScheduler 根据工具数量和依赖关系自动选择执行策略：
        - 空列表 → 直接返回
        - 全部工具 → 通过全局 DAG 拓扑分层调度（单工具/多工具统一）
        """
        agent = self._agent
        parse_elapsed = (usage or {}).get("tool_parse_elapsed", 0.0)

        # dispatch_agent 调用时创建共享 ParallelExecutor
        # 单次调用独立执行，多次调用共享实例实现真正并行
        dispatch_count = sum(1 for tc in tool_calls if tc.get("name") == "dispatch_agent")
        if dispatch_count > 0:
            is_web = getattr(agent._display_port, 'is_web', False)
            agent._shared_executor = ParallelExecutor(agent, is_web=is_web)
            agent._shared_executor.setup_barrier(dispatch_count)
        else:
            agent._shared_executor = None

        agent._append_assistant_message(content, tool_calls, reasoning_content)

        # ── Phase B：分组工具卡计划（对齐 CC grouped tool use） ──
        # schedule 前对**有序** tool_calls 做 run 划分（唯一有序源——并发
        # on_before 无法保证到达顺序）：连续同类分组工具（_GROUPABLE_TOOLS /
        # _TASK_GROUPABLE_TOOLS）且 run 长度 ≥2 → 合并为一张卡（成员输出
        # 丢弃，摘要卡）；单次调用（长度 1）不分组仍走单卡；bash/write/edit
        # 不分组（对齐 CC）。
        # 惰性 import（core 层避免模块级依赖 TUI app 常量）。
        from src.tui.app._model_helpers import (
            _GROUPABLE_TOOLS as _GROUP_TOOLS,
            _TASK_GROUPABLE_TOOLS as _TASK_TOOLS,
        )
        from ...param_formatter import extract_key_params as _extract_kp
        _GROUPABLE = _GROUP_TOOLS | _TASK_TOOLS
        _groups: list[tuple[str, list]] = []
        _i, _n = 0, len(tool_calls)
        while _i < _n:
            _tc = tool_calls[_i]
            _name = _tc.get("name", "")
            if _name in _GROUPABLE:
                _j = _i
                while _j < _n and tool_calls[_j].get("name") == _name:
                    _j += 1
                _run = tool_calls[_i:_j]
                if len(_run) >= 2:
                    if _name == "dispatch_agent":
                        # Task 成员 detail = 任务描述（CC ``@name`` 行语义）
                        _members = [
                            (
                                m.get("id", ""),
                                (m.get("arguments") or {}).get("description", "")
                                if isinstance(m.get("arguments"), dict)
                                else _extract_kp(_name, m.get("arguments", "")),
                            )
                            for m in _run
                        ]
                    else:
                        _members = [
                            (m.get("id", ""), _extract_kp(_name, m.get("arguments", "")))
                            for m in _run
                        ]
                    _groups.append((_name, _members))
                    _i = _j
                    continue
            _i += 1
        if tool_calls:
            # 每批开始重置批状态（端口方法现启用）——Dispatcher
            # ``_on_tool_batch_start`` 清分组成员 id 集合。**即使本批无分组也
            # 调用（names 为空）**：防上一批分组成员 id 残留导致后续同名 id
            # 的单工具被误抑制开卡。随后 ``tool_group_planned`` 逐个登记成员。
            agent.display.tool_batch_start("main", [g[0] for g in _groups])
            for _gname, _gmembers in _groups:
                agent.display.tool_group_planned("main", _gname, _gmembers)

        # ── 回调工厂（消除 lambda 重复） ────────────────
        def _on_before(tc, detail):
            return self._on_before_tool(tc, detail, parse_elapsed)
        def _on_after(tc, output, success):
            return self._on_after_tool(tc, output, success)

        # ── ToolScheduler 统一调度 ──────────────────────
        # UNIQUE_PATH: MainAgent 工具执行入口，项目唯二 schedule() 调用方之一
        results: list[tuple[str, str, bool]] = []
        try:
            results = await ToolScheduler.default().schedule(
                tool_calls,
                agent_ref=agent,
                on_before=_on_before,
                on_after=_on_after,
                run_method=self._run_tool_method,
            )
        finally:
            # 确保取消/异常时释放 barrier
            if agent._shared_executor is not None:
                agent._shared_executor._all_done.set()
            agent._shared_executor = None

        successful_tools = []
        failed_tools = []
        for tool_call_id, output, success in results:
            agent._append_tool_result(tool_call_id, output)
            tc_name = next((tc["name"] for tc in tool_calls if tc["id"] == tool_call_id), "unknown")
            if success:
                successful_tools.append(tc_name)
            else:
                failed_tools.append((tc_name, output))

        self._show_tool_execution_summary(successful_tools, failed_tools)

        # ── 可观测性：记录工具执行指标 ───────────────────
        metrics = get_default_collector()
        metrics.counter("tools.calls", len(tool_calls))
        metrics.counter("tools.failed", len(failed_tools))

    # ── 工具回调（从 _handle_tool_calls 内联闭包提取） ──────

    @staticmethod
    def _sanitize_args_for_log(args: dict, _depth: int = 0) -> str:
        """过滤工具参数中的敏感字段，用于审计日志记录。"""
        _MAX_RECURSION_DEPTH = 20
        if _depth >= _MAX_RECURSION_DEPTH:
            return "{...}"

        SENSITIVE_KEYS = {"api_key", "api-key", "apikey", "token", "secret",
                          "password", "passwd", "authorization", "auth",
                          "key", "private_key", "access_key"}
        sanitized = {}
        for k, v in args.items():
            if k.lower() in SENSITIVE_KEYS:
                sanitized[k] = "***"
            elif isinstance(v, dict):
                sanitized[k] = ToolCallbackChain._sanitize_args_for_log(v, _depth + 1)
            elif isinstance(v, str) and len(v) > 100:
                sanitized[k] = v[:100] + "..."
            else:
                sanitized[k] = v
        result = str(sanitized)
        return result[:200]

    @staticmethod
    def _detect_webdiff(output: str) -> tuple[dict | None, str]:
        """检测 webdiff JSON 格式的输出，提取 diff_data 和预览文本。"""
        diff_data = None
        preview = output
        if output and output.strip().startswith("{"):
            try:
                parsed = json.loads(output)
                if isinstance(parsed, dict) and parsed.get("type") == "webdiff":
                    diff_data = {
                        "path": parsed.get("path", ""),
                        "mode": parsed.get("mode", ""),
                        "old_content": parsed.get("old_content", ""),
                        "new_content": parsed.get("new_content", ""),
                        "result": parsed.get("result", output),
                    }
                    preview = parsed.get("result", output)
            except (json.JSONDecodeError, TypeError):
                pass
        return diff_data, preview

    def _on_before_tool(self, tc: dict, detail: str, parse_elapsed: float) -> None:
        """工具执行前回调：审计日志 + display 进度展示。"""
        agent = self._agent
        from ....config import audit_logger  # audit_logger — 函数体内延迟导入
        audit_logger.info(f"{tc['name']} | {self._sanitize_args_for_log(tc.get('arguments', {}))}")

        tool_label, tool_name = tc["id"], tc["name"]
        arg_str = _safe_json_dumps(tc.get("arguments", ""))

        metadata = {"参数": f"{estimate_tokens(arg_str)}t"}
        if parse_elapsed > 0:
            metadata["解析"] = f"{parse_elapsed:.1f}s"

        agent.display.tool_parsing(tool_label, tool_name, arg_str)
        agent.display.tool_start(tool_label, tool_name, detail, metadata)

    def _on_after_tool(self, tc: dict, output: str, success: bool) -> None:
        """工具执行后回调：display 完成进度 + TUI 刷新通知。"""
        agent = self._agent
        tool_label = tc["id"]

        if success:
            diff_data, preview = self._detect_webdiff(output)
            metadata: dict = {
                "参数": f"{estimate_tokens(_safe_json_dumps(tc.get('arguments', '')))}t",
                "输出": f"{estimate_tokens(output)}t",
                "行数": output.count('\n') + 1,
                "output_preview": preview,
                "tool_name": tc["name"],
            }
            if diff_data:
                metadata["diff_data"] = diff_data
            agent.display.tool_done(tool_label, tc["name"], success=True, metadata=metadata)
        else:
            err_preview = (output[:300] + '…') if len(output) > 300 else output
            agent.display.tool_done(tool_label, tc["name"], success=False,
                                    metadata={"output_preview": err_preview})

        for _cb in agent._on_tool_completed_callbacks:
            try:
                _cb(tc, output, success)
            except Exception:
                _logger.warning("on_tool_completed callback 异常", exc_info=True)

    async def _run_tool_method(self, func, tc):
        """分发工具执行到对应策略。

        - dispatch_agent: 跳过 UI 输出，直接 execute
        - user_select: 交互式终端工具，跳过 stdout 捕获
        - 其他工具: 统一走 stdout 捕获 + display/web_display

        执行期间设置 contextvar（当前 tool_id），使 print_to_terminal /
        SharedCapture.write 能定向分发输出事件到正确的工具 box。
        """
        agent = self._agent
        tool_label = tc.get("id", "")
        if tool_label:
            func.tool_label = tool_label

        tool_name = tc["name"]
        is_web = getattr(agent._display_port, 'is_web', False)

        if tool_name == "dispatch_agent":
            coro = func.execute()
        elif tool_name == "user_select":
            coro = self._run_interactive(func, is_web)
        else:
            coro = self._run_with_capture(func, tool_label, is_web)
        from ._tool_context import run_with_tool_context
        return await run_with_tool_context(tool_label, coro)

    async def _run_interactive(self, func, is_web: bool):
        """执行交互式终端工具（user_select），跳过 stdout 捕获。

        stdout 捕获会劫持 sys.stdout 为 _SharedCapture，导致 prompt_toolkit
        无法创建正确的终端输出(Vt100_Output)，Picker UI 静默回退为 PlainTextOutput。
        user_select 的 display() 自带终端输出，无需额外捕获。

        React Ink 化（2026-08-05）：user_select 的弹窗由 React Ink 组件
        ``UserSelectPopup`` 在渲染树中显示与交互（use_input 经 render 线程
        驱动的 InputDispatcher 路由）。因此**不再 suspend render 线程**——
        suspend 会停止渲染循环（InputDispatcher 随之停止读 stdin），组件
        无法渲染/接收按键。弹窗期间 AI 状态栏/动画继续正常刷新。
        """
        if is_web and func.__class__.web_display is not Func.web_display:
            return await func.web_display()

        result = await func.execute()

        # user_select 结果通过 ToolOutputChunkEvent 上屏显示
        self._emit_user_select_output(result, getattr(func, 'tool_label', ''))

        return result

    def _emit_user_select_output(self, result: str, tool_label: str) -> None:
        """解析 user_select 结果并发布 ToolOutputChunkEvent 上屏显示。"""
        try:
            data = json.loads(result)
        except (json.JSONDecodeError, TypeError):
            return

        action = data.get("action", "")
        selected = data.get("selected", [])

        if action == "confirmed":
            if selected:
                if len(selected) == 1:
                    text = f"已选择: {selected[0]}"
                else:
                    items = ", ".join(selected[:3])
                    if len(selected) > 3:
                        items += f" ... 还有 {len(selected) - 3} 项"
                    text = f"已选择 {len(selected)} 项: {items}"
            else:
                text = "未选择任何项"
        elif action in ("cancel", "timeout", "non_interactive"):
            return  # 取消/超时/非交互不输出（非用户主动选择）
        elif action == "empty":
            return  # 空选项列表无输出
        elif action and action.startswith("error:"):
            return  # 错误不输出（通过 tool_done 的 success=False 标记）
        else:
            return  # 未知 action 不输出

        if not tool_label:
            return

        try:
            from ....tui.events.event_types import ToolOutputChunkEvent
            self._agent._event_port.publish_event(ToolOutputChunkEvent(
                label=tool_label, tool_id=tool_label, text=text, source="agent",
            ))
        except Exception:
            _logger.debug("user_select 结果上屏失败", exc_info=True)

    async def _run_with_capture(self, func, tool_label: str, is_web: bool):
        """执行通用工具，带 stdout 捕获和 spinner 刷新。

        工具执行期间的 stdout 输出会被实时捕获为 ToolOutputChunkEvent
        → EventBus → WebToolBridge → SSE → 前端。
        """
        agent = self._agent
        if tool_label:
            agent._capture_mgr.start_capture(tool_label)

        refresh_task = _spinner_refresher(agent.display, tool_label) if tool_label else None

        try:
            if is_web and func.__class__.web_display is not Func.web_display:
                return await func.web_display()
            return await func.display()
        finally:
            if refresh_task:
                refresh_task.cancel()
                try:
                    await refresh_task
                except asyncio.CancelledError:
                    pass
            if tool_label:
                agent._capture_mgr.stop_capture(tool_label)

    def _show_tool_execution_summary(self, successful_tools, failed_tools):
        """通过 EventPort 发布工具执行汇总事件。"""
        agent = self._agent
        agent._event_port.publish("tool_summary", data={
            "successful_tools": [get_tool_display_name(name) for name in successful_tools],
            "failed_tools": [(get_tool_display_name(name), output) for name, output in failed_tools],
        }, source="agent")
