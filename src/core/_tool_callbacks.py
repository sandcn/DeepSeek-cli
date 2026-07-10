"""ToolCallbackChain — Agent 工具回调链的独立封装。

从 agent.py 提取，封装工具执行的完整生命周期：
  handle_tool_calls → _run_tool_method → _on_before_tool / _on_after_tool

工具执行使用 DAG 引擎：
  1. ToolDAG 构建依赖图（显式引用 + 隐式路径重叠 + user_select 独占约束）
  2. Kahn 拓扑排序 → 分层
  3. 逐层并发执行（同层无依赖工具并行，层间串行等待）
"""

from __future__ import annotations

import asyncio
import json
import logging
from .parallel_executor import ParallelExecutor
from .telemetry import get_default_collector
from ..core.ports.tokens import DefaultTokensAdapter

_tokens_port = DefaultTokensAdapter()
from ..tools.base import Func
from ..tools.registry import get_tool_display_name
from ..config import audit_logger

_logger = logging.getLogger(__name__)

def _is_parallel_safe(registry, tool_name: str) -> bool:
    """通过 metadata 动态查询工具是否并行安全。

    metadata 查询失败时默认返回 False（安全优先）。
    """
    try:
        meta = registry.get_metadata(tool_name)
        return meta is not None and meta.parallel_safe
    except Exception:
        _logger.debug("_is_parallel_safe 查询失败，工具 '%s' 默认返回 False", tool_name, exc_info=True)
        return False


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
        """处理工具调用，按四波排序执行：
        Wave 0: user_select 串行（独占终端）
        Wave 1: 并行安全工具并发（metadata 驱动，parallel_safe=True）
        Wave 2: 非并行安全工具串行（metadata 驱动，parallel_safe=False）
        Wave 3: dispatch_agent 并发（SubAgent，前三波完成后执行）
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

        # ── 回调工厂（消除 lambda 重复） ────────────────
        def _on_before(tc, detail):
            return self._on_before_tool(tc, detail, parse_elapsed)
        def _on_after(tc, output, success):
            return self._on_after_tool(tc, output, success)

        # ── DAG 调度执行 ───────────────────────────────
        try:
            from .tool_dag import ToolDAG

            dag = ToolDAG(tool_calls, agent._async_tool_executor.registry)
            results_map: dict[str, tuple] = {}  # tool_call_id → (id, output, success)

            if dag.size == 0:
                pass  # 无工具调用
            elif dag.size == 1:
                # 单工具：直接串行执行
                node = next(iter(dag.nodes.values()))
                single_call = [{"id": node.tc_id, "name": node.name,
                                "arguments": node.arguments}]
                single_result = await agent._async_tool_executor.execute_async(
                    single_call,
                    agent_ref=agent,
                    on_before=_on_before,
                    on_after=_on_after,
                    run_method=self._run_tool_method,
                    parallel=False,
                )
                for r in single_result:
                    results_map[r[0]] = r
            else:
                # 多工具：使用 DAG 引擎调度
                dag_results = await agent._async_tool_executor.execute_dag_async(
                    dag,
                    agent_ref=agent,
                    on_before=_on_before,
                    on_after=_on_after,
                    run_method=self._run_tool_method,
                )
                for r in dag_results:
                    results_map[r[0]] = r

        finally:
            # 确保取消/异常时释放 barrier
            if agent._shared_executor is not None:
                agent._shared_executor.signal_all_done()
            agent._shared_executor = None

        # 按原始 tool_calls 顺序重建结果列表
        results = []
        for tc in tool_calls:
            tc_id = tc["id"]
            if tc_id in results_map:
                results.append(results_map[tc_id])
            else:
                # 极端异常路径：execute_async 未返回某工具的结果
                _logger.warning("工具结果丢失: %s (id=%s)", tc.get("name", "?"), tc_id)
                results.append((tc_id, f"错误：工具 '{tc.get('name', '?')}' 结果丢失", False))

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
        audit_logger.info(f"{tc['name']} | {self._sanitize_args_for_log(tc.get('arguments', {}))}")

        tool_label, tool_name = tc["id"], tc["name"]
        arg_str = _safe_json_dumps(tc.get("arguments", ""))

        metadata = {"参数": f"{_tokens_port.estimate_tokens(arg_str)}t"}
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
                "参数": f"{_tokens_port.estimate_tokens(_safe_json_dumps(tc.get('arguments', '')))}t",
                "输出": f"{_tokens_port.estimate_tokens(output)}t",
                "行数": output.count('\n') + 1,
                "output_preview": preview,
                "tool_name": tc["name"],
            }
            if diff_data:
                metadata["diff_data"] = diff_data
            agent.display.tool_done(tool_label, tc["name"], success=True, metadata=metadata)
        else:
            err_preview = (output[:300] + '…') if len(output) > 300 else output
            agent.display.tool_done(tool_label, tc.get("name", ""), success=False,
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
        """
        agent = self._agent
        tool_label = tc.get("id", "")
        if tool_label:
            func.tool_label = tool_label

        tool_name = tc["name"]
        is_web = getattr(agent._display_port, 'is_web', False)

        if tool_name == "dispatch_agent":
            return await func.execute()
        if tool_name == "user_select":
            return await self._run_interactive(func, is_web)
        return await self._run_with_capture(func, tool_label, is_web)

    async def _run_interactive(self, func, is_web: bool):
        """执行交互式终端工具（user_select），跳过 stdout 捕获。

        stdout 捕获会劫持 sys.stdout 为 _SharedCapture，导致 prompt_toolkit
        无法创建正确的终端输出(Vt100_Output)，Picker UI 静默回退为 PlainTextOutput。
        user_select 的 display() 自带终端输出，无需额外捕获。

        执行前暂停 ChatUIConsumer（停止 render 线程、拆除底部栏），
        执行后恢复，确保 Picker 独占终端不被后台渲染干扰。
        """
        if is_web and func.__class__.web_display is not Func.web_display:
            return await func.web_display()

        from ..chat_ui import get_active_chat_ui
        chat_ui = get_active_chat_ui()
        if chat_ui is not None:
            chat_ui.suspend()
        try:
            return await func.execute()
        finally:
            if chat_ui is not None:
                chat_ui.resume()

    async def _run_with_capture(self, func, tool_label: str, is_web: bool):
        """执行通用工具，带 stdout 捕获。

        工具执行期间的 stdout 输出会被实时捕获为 ToolOutputChunkEvent
        → EventBus → WebToolBridge → SSE → 前端。
        """
        agent = self._agent
        if tool_label:
            await agent._capture_mgr.start_capture(tool_label)

        try:
            if is_web and func.__class__.web_display is not Func.web_display:
                return await func.web_display()
            return await func.display()
        finally:
            if tool_label:
                await agent._capture_mgr.stop_capture(tool_label)

    def _show_tool_execution_summary(self, successful_tools, failed_tools):
        """通过 EventPort 发布工具执行汇总事件。"""
        agent = self._agent
        agent._event_port.publish("tool_summary", data={
            "successful_tools": [get_tool_display_name(name) for name in successful_tools],
            "failed_tools": [(get_tool_display_name(name), output) for name, output in failed_tools],
        }, source="agent")
