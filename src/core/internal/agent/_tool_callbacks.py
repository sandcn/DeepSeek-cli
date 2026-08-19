"""ToolCallbackChain — Agent 工具回调链的独立封装。

从 agent.py 提取，封装工具执行的完整生命周期：
  handle_tool_calls → _run_tool_method → _on_before_tool / _on_after_tool

工具执行通过 ToolScheduler.schedule() 统一调度（DAG 依赖分析 + 拓扑排序 + 分层并发），
ToolScheduler 为全局单例，内聚 ToolDAG 构建 + 调度 + 并发控制。
"""

from __future__ import annotations

import json
import logging
import re
from ...parallel_executor import ParallelExecutor
from ...tool_executor_async import ToolScheduler
from ...telemetry import get_default_collector
from ....api.tokens import estimate_tokens
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


# 敏感键名掩码正则（str 形态参数无法 JSON 解析时的兜底脱敏）
# 匹配 "key": "value" / 'key': 'value'（键名忽略大小写）
_SENSITIVE_KEY_JSON_RE = re.compile(
    r'(["\'](?:api[_-]?key|apikey|token|secret|password|passwd|'
    r'authorization|auth|private[_-]?key|access[_-]?key|key)["\']\s*:\s*["\'])'
    r'[^"\']*["\']',
    re.IGNORECASE,
)


def _mask_sensitive_json_text(text: str) -> str:
    """将 str 形态参数文本中的敏感键值对掩码为 ***。

    与 dict 路径的 SENSITIVE_KEYS 集合对应；仅作 JSON 解析失败时
    的兜底（正常 JSON 走递归脱敏，语义更精确）。
    """
    return _SENSITIVE_KEY_JSON_RE.sub(r'\1***"', text)


# 敏感键名集合（dict 路径递归脱敏用；与 _mask_sensitive_json_text 对应）
_SENSITIVE_ARGS_KEYS = frozenset({
    "api_key", "api-key", "apikey", "token", "secret",
    "password", "passwd", "authorization", "auth",
    "key", "private_key", "access_key",
})


def _sanitize_args_impl(args, _depth: int = 0) -> str:
    """过滤工具参数中的敏感字段，用于审计日志记录（模块级实现）。

    args 可能为 dict（正常）或 str（JSON 字符串形态，见
    ``_call_is_background_subagent`` 的处理场景）。str 形态先尝试
    JSON 解析后递归脱敏——否则 ``{"password": "xxx"}`` 等敏感字段
    会原样写入 audit.log（安全缺陷）；解析失败时按 JSON 键名模式
    掩码值。模块级函数：避免 Python 3.9 下 staticmethod 经类名
    递归引用不可调用的问题。
    """
    _MAX_RECURSION_DEPTH = 20
    if _depth >= _MAX_RECURSION_DEPTH:
        return "{...}"

    if not isinstance(args, dict):
        text = str(args)[:2000]
        # 尝试 JSON 解析 → 递归脱敏
        try:
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                return _sanitize_args_impl(parsed, _depth)
        except (json.JSONDecodeError, TypeError):
            pass
        # 解析失败（截断/非 JSON）：按键名模式掩码 "key": "value"
        return _mask_sensitive_json_text(text)

    sanitized = {}
    for k, v in args.items():
        if k.lower() in _SENSITIVE_ARGS_KEYS:
            sanitized[k] = "***"
        elif isinstance(v, dict):
            sanitized[k] = _sanitize_args_impl(v, _depth + 1)
        elif isinstance(v, str) and len(v) > 100:
            sanitized[k] = v[:100] + "..."
        else:
            sanitized[k] = v
    result = str(sanitized)
    return result[:200]


async def _run_file_display(func, display=None):
    """通过 display 管道执行文件操作工具（write_file/update_file），获取 diff 输出。

    SubAgent 和 Agent 的 _run_with_capture 对文件工具都需要走 display 路径：
    - ParallelDisplay 场景：用 capture_and_print_async 捕获差异并打印到终端
    - 普通 display 场景：回退到 func.display()（自带 stdout 捕获）
    """
    if display is not None and hasattr(display, 'capture_and_print_async'):
        return await display.capture_and_print_async(func.display)
    return await func.display()


def _parse_background_flag(args) -> bool:
    """解析 subagent 调用的 background 标志（缺省视为后台）。

    委托 :func:`src.tools.subagent.parse_background_flag`（单一实现源，
    与 SubagentFunc.from_args 保持一致——保证 barrier 计数判定与
    subagent 工具实际执行模式一致）。"""
    from ....tools.subagent import parse_background_flag as _parse
    return _parse(args)


def _call_is_background_subagent(tc: dict) -> bool:
    """判断 tool_call 是否为后台 subagent 调用（默认后台）。

    subagent 默认后台执行（background 缺省即视为后台）——与
    ``SubagentFunc.from_args`` 的默认值 True 一致，避免 barrier 计数与
    subagent 工具实际执行路径不一致（否则后台 subagent 计入 dispatch_count
    后不注册 barrier，只能靠 _BARRIER_TIMEOUT 兜底）。
    """
    if tc.get("name") != "subagent":
        return False
    return _parse_background_flag(tc.get("arguments"))


def _count_dispatch_subagents(tool_calls: list) -> int:
    """统计需进入共享 barrier 的 subagent 调用数（排除后台 subagent）。

    ★ 后台 subagent（默认：background 缺省或为 true）不进入共享
    ParallelExecutor barrier：其执行体由 subagent 工具内部自启独立 asyncio
    后台任务（_execute_background），立即返回 task_id JSON，不参与同轮普通
    subagent 的注册/等待协调。若把后台 subagent 计入 dispatch_count，barrier
    期望的注册数（含后台 subagent）与实际上会注册的协程数（仅前台 subagent）
    不匹配，只能靠 _BARRIER_TIMEOUT=60s 兜底超时唤醒，白白拖慢整轮工具返回。
    仅显式 background=false 的前台 subagent 计入 barrier。
    """
    return sum(
        1 for tc in tool_calls
        if tc.get("name") == "subagent" and not _call_is_background_subagent(tc)
    )


class ToolCallbackChain:
    """工具回调链 — 封装 Agent 中工具调用的完整生命周期。

    接受 agent 实例作为构造参数，通过 self._agent 访问 agent 属性。
    提取自 agent.py：_handle_tool_calls / _run_tool_method / _on_before_tool /
    _on_after_tool / _sanitize_args_for_log / _show_tool_execution_summary。
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

        # subagent 调用时创建共享 ParallelExecutor
        # 单次调用独立执行，多次调用共享实例实现真正并行
        # ★ 后台 subagent（默认：background 缺省或为 true）不进入共享 barrier：
        #   其执行体在 subagent 工具内部自启独立 asyncio 后台任务
        #   （_execute_background），立即返回 task_id JSON；不计入 dispatch_count
        #   避免 barrier 计数不匹配拖到 _BARRIER_TIMEOUT=60s 兜底
        #   （见 _count_dispatch_subagents）。仅显式 background=false 的前台
        #   subagent 计入 barrier（同轮多个前台 subagent 真正并行）。
        dispatch_count = _count_dispatch_subagents(tool_calls)
        if dispatch_count > 0:
            agent._shared_executor = ParallelExecutor(agent)
            agent._shared_executor.setup_barrier(dispatch_count)
        else:
            agent._shared_executor = None

        # ── 回调工厂（消除 lambda 重复） ────────────────
        def _on_before(tc, detail):
            return self._on_before_tool(tc, detail, parse_elapsed)
        def _on_after(tc, output, success):
            return self._on_after_tool(tc, output, success)

        # ── ToolScheduler 统一调度 ──────────────────────
        # UNIQUE_PATH: MainAgent 工具执行入口，项目唯二 schedule() 调用方之一
        results: list[tuple[str, Any, bool]] = []
        try:
            # _append_assistant_message 也在 try 内：若消息构造抛异常（罕见），
            # finally 仍会释放 barrier（防 SubAgent 等待者死锁）
            agent._append_assistant_message(content, tool_calls, reasoning_content)
            results = await ToolScheduler.default().schedule(
                tool_calls,
                agent_ref=agent,
                on_before=_on_before,
                on_after=_on_after,
                run_method=self._run_tool_method,
            )
        finally:
            # 确保取消/异常时释放 barrier（经公开 release()，不直接触碰私有字段）
            if agent._shared_executor is not None:
                agent._shared_executor.release()
            agent._shared_executor = None

        successful_tools = []
        failed_tools = []
        for tool_call_id, output, success in results:
            agent._append_tool_result(tool_call_id, output)
            tc_name = next((tc["name"] for tc in tool_calls if tc["id"] == tool_call_id), "unknown")
            if success:
                successful_tools.append(tc_name)
            else:
                failed_tools.append((tc_name, to_tool_text(output)))

        # ★ P3-时序修复（2026-08-08）：subagent 提前返回后，剩余 dispatch
        #   由后台任务执行并补发 tool result（_bg_subagents）。等待其完成，
        #   确保下一轮模型调用的消息序列完整（避免 assistant tool_call 无对应
        #   tool 消息 → API 400 / 模型重发）。
        try:
            await ToolScheduler.default().wait_background_dispatch()
        except Exception:
            _logger.debug("等待后台 dispatch 任务异常", exc_info=True)

        # ★ P3-一致性修复：提前返回的 dispatch 结果已由 bg 补发到消息，
        #   补入工具汇总（否则 dispatch 计入 tools.calls 但不显示在 summary）。
        #   P1-1 修复：仅补入「未包含在 schedule() 返回结果中」的 dispatch 节点，
        #   避免非提前返回（正常执行）路径的汇总重复。
        result_ids = {r[0] for r in results}
        _scheduler = ToolScheduler.default()
        for tc in tool_calls:
            if (tc.get("name") == "subagent"
                    and tc.get("id") not in result_ids):
                _r = getattr(_scheduler, "_results_map", {}).get(tc.get("id", ""))
                if _r is not None and len(_r) >= 3:
                    _, _output, _success = _r
                    if _success:
                        successful_tools.append("subagent")
                    else:
                        failed_tools.append(("subagent", _output))

        self._show_tool_execution_summary(successful_tools, failed_tools)

        # ── 可观测性：记录工具执行指标 ───────────────────
        metrics = get_default_collector()
        metrics.counter("tools.calls", len(tool_calls))
        metrics.counter("tools.failed", len(failed_tools))

    # ── 工具回调（从 _handle_tool_calls 内联闭包提取） ──────

    @staticmethod
    def _sanitize_args_for_log(args, _depth: int = 0) -> str:
        """过滤工具参数中的敏感字段，用于审计日志记录。

        委托模块级 :func:`_sanitize_args_impl`（Python 3.9 下 staticmethod
        不可直接调用，模块级函数避免类名引用递归问题）。
        """
        return _sanitize_args_impl(args, _depth)

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

    def _on_after_tool(self, tc: dict, output, success: bool) -> None:
        """工具执行后回调：display 完成进度 + TUI 刷新通知。"""
        agent = self._agent
        tool_label = tc["id"]

        # 多模态结构化结果（ToolResult）归一化为纯文本用于展示/统计
        from ...tools.base import to_tool_text
        output = to_tool_text(output)

        if success:
            metadata: dict = {
                "参数": f"{estimate_tokens(_safe_json_dumps(tc.get('arguments', '')))}t",
                "输出": f"{estimate_tokens(output)}t",
                "行数": output.count('\n') + 1,
                "output_preview": output,
                "tool_name": tc["name"],
            }
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

        - subagent: 跳过 UI 输出，直接 execute
        - user_select: 交互式终端工具，跳过 stdout 捕获
        - 其他工具: 统一走 stdout 捕获 + display

        执行期间设置 contextvar（当前 tool_id），使 print_to_terminal /
        SharedCapture.write 能定向分发输出事件到正确的工具 box。
        """
        agent = self._agent
        tool_label = tc.get("id", "")
        if tool_label:
            func.tool_label = tool_label

        tool_name = tc["name"]

        if tool_name == "subagent":
            coro = func.execute()
        elif tool_name == "user_select":
            coro = self._run_interactive(func)
        else:
            coro = self._run_with_capture(func, tool_label)
        from ._tool_context import run_with_tool_context
        return await run_with_tool_context(tool_label, coro)

    async def _run_interactive(self, func):
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

    async def _run_with_capture(self, func, tool_label: str):
        """执行通用工具，带 stdout 捕获。

        工具执行期间的 stdout 输出会被实时捕获为 ToolOutputChunkEvent
        → EventBus → 前端渲染。
        """
        agent = self._agent
        if tool_label:
            agent._capture_mgr.start_capture(tool_label)

        try:
            return await func.display()
        finally:
            if tool_label:
                agent._capture_mgr.stop_capture(tool_label)

    def _show_tool_execution_summary(self, successful_tools, failed_tools):
        """通过 EventPort 发布工具执行汇总事件。"""
        agent = self._agent
        agent._event_port.publish("tool_summary", data={
            "successful_tools": [get_tool_display_name(name) for name in successful_tools],
            "failed_tools": [(get_tool_display_name(name), output) for name, output in failed_tools],
        }, source="agent")
