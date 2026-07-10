"""
SubAgent — 独立上下文的子代理

每个 SubAgent 拥有独立的消息历史，使用非流式 API 调用，
通过 ParallelDisplay 上报状态，结果汇总后返回给父 Agent。

SubAgent 共享 MainAgent 的文件沙盒（SandboxManager），
文件变更记录关联到 MainAgent 的消息索引，确保沙盒一致性。
"""

import asyncio
import logging
import threading
from typing import List, Dict, Any, Optional, Tuple, Callable

from .base_agent import BaseAgent

from ..api.stats import accumulate_usage

_logger = logging.getLogger(__name__)

# ── 类型策略：agent_type → 排除的工具集合 ─────────────
# 每种类型映射一个不可用工具集，便于未来扩展。
#
# 策略差异说明：
# - map: 只读分析，排除所有写入类工具 + web_search
# - review: 代码审查，排除所有写入类工具，保留 web_search（可查文档）
# - plan: 计划生成，保留 write_file/update_file，但在 FileToolBase
#   ._validate_path_and_size() 中有额外的路径白名单校验（仅限 .chat/plan/）
# - read_memory: 只读记忆，排除所有写入类工具 + web_search + dispatch_agent + user_select
#   （当前与 map 策略一致，独立维护以备未来分化）
# - write_memory: 读写记忆，保留 write_file/update_file/mk，但在 FileToolBase
#   ._validate_path_and_size() 中有额外的路径白名单校验（仅限 .chat/memory/）
#   （保留 mk 以便在 .chat/memory/ 目录不存在时自行创建，与 plan 不含 mk 的策略不同）
# - execute: 计划执行型（默认），保留读写工具 + bash，排除 web_search + dispatch_agent + user_select，
#   无路径白名单限制，用于执行计划文件步骤并返回修改文件列表
_TOOL_EXCLUSION_MAP = {
    "map": {
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mk",
        "web_search",
        "dispatch_agent", "user_select",
    },
    "think": {
        # 只读+无web_search，当前与 map 一致但语义独立，未来可能分化
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mk",
        "web_search",
        "dispatch_agent", "user_select",
    },
    "review": {
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mk",
        "dispatch_agent", "user_select",
    },
    "plan": {
        "bash",
        "rm",
        "mv",
        "cp",
        "mk",
        "dispatch_agent",
        "user_select",
    },
    "read_memory": {
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mk",
        "web_search",
        "dispatch_agent", "user_select",
    },
    "write_memory": {
        "bash",
        "rm",
        "mv",
        "cp",
        "web_search",
        "dispatch_agent",
        "user_select",
    },
    "execute": {
        "dispatch_agent",
        "user_select",
        "web_search",
    },
}


def _get_excluded_tools(agent_type: str) -> set:
    """根据 agent_type 返回应排除的工具名集合。未知类型回退 execute 策略。"""
    return _TOOL_EXCLUSION_MAP.get(agent_type, _TOOL_EXCLUSION_MAP["execute"])


class SubAgent(BaseAgent):
    """独立子代理，在独立线程中运行"""

    def __init__(
        self,
        label: str,
        description: str,
        prompt: str,
        parent_agent,
        model: str = None,
        model_port=None,
        agent_type: str = "execute",
    ):
        super().__init__()

        self.label = label
        self.description = description
        self.prompt = prompt
        self.parent = parent_agent
        self._event_port = getattr(parent_agent, '_event_port', None)
        self.agent_type = agent_type

        self._registry = parent_agent.get_tool_registry()
        self._model_port = model_port or getattr(parent_agent, '_async_model_port', None)

        self.model = model or parent_agent.model
        excluded = _get_excluded_tools(agent_type)
        self.tools = [t for t in self._registry.get_schemas()
                      if t.get("function", {}).get("name") not in excluded]

        # 通过 parent_agent 的 PromptBuilderPort 构建 system prompt，
        # 确保与 MainAgent 使用一致的 cwd 和环境信息（修复 Bug#4）
        prompt_port = parent_agent.get_prompt_builder_port()
        if agent_type == "map":
            system_parts = prompt_port.build_map_agent_prompt()
        elif agent_type == "think":
            system_parts = prompt_port.build_think_agent_system_prompt()
        elif agent_type == "review":
            system_parts = prompt_port.build_review_agent_prompt()
        elif agent_type == "plan":
            system_parts = prompt_port.build_plan_agent_prompt()
        elif agent_type == "read_memory":
            system_parts = prompt_port.build_read_memory_agent_system_prompt()
        elif agent_type == "write_memory":
            system_parts = prompt_port.build_write_memory_agent_system_prompt()
        elif agent_type == "execute":
            system_parts = prompt_port.build_execute_agent_system_prompt()
        else:
            system_parts = prompt_port.build_subagent_prompt()
        self.messages: List[Dict[str, Any]] = [
            *[{"role": "system", "content": part} for part in system_parts],
            {"role": "user", "content": prompt},
        ]

        # SubAgent 不更新全局沙盒索引，避免多个并发 SubAgent
        # 在 asyncio 单线程中通过 thread local 互相覆盖 parent_idx，
        # 同时防止 SubAgent 自身消息索引污染 SandboxManager 全局索引。
        self._skip_sandbox_update = True

        self.display = None
        self.result: str = ""
        self.error: str = ""
        self.tool_calls_count = 0
        self._tool_calls_count_lock = threading.Lock()

    # =================== 主循环 ===================

    async def run(self) -> str:
        """执行子代理循环，返回最终文本结果

        SubAgent 不管理 thread local/全局沙盒索引，原因：
        1. 多个 SubAgent 在 asyncio 单线程中并发执行，thread local 互相覆盖不可行
        2. SubAgent 设置了 _skip_sandbox_update=True，不会通过
           _append_assistant_message 污染 SandboxManager 全局索引
        3. SubAgent 内的文件操作通过 record_file_change_from_context fallback
           到 sandbox_manager.get_current_message_index_safe()，该值保持为
           MainAgent 最后一次设置的正确索引，不受 SubAgent 影响
        """
        content = ""
        while True:
            try:
                reasoning, content, usage, tool_calls = await self._call_model_impl(
                    self.messages,
                    model=self.model,
                    tools=self.tools,
                    display=self.display,
                    label=self.label,
                    silent=True,
                )
            except asyncio.CancelledError:
                raise  # 透传取消信号到外层统一处理
            except Exception as e:
                return self._handle_model_error(e)

            self._update_display(usage)

            if not tool_calls:
                self.result = content
                return content

            try:
                await self._handle_tool_calls(content, tool_calls, reasoning)
            except asyncio.CancelledError:
                raise  # 透传取消信号到外层统一处理
            except Exception as e:
                _logger.error("SubAgent %s tool call handling failed: %s", self.label, e)
                self.error = str(e)
                return f"工具调用处理失败: {e}"

    # =================== 内部方法 ===================

    def _handle_model_error(self, error: BaseException) -> str:
        """模型调用异常处理"""
        self.error = str(error)
        _logger.error("SubAgent %s model call failed: %s", self.label, error)
        if self.display:
            self.display.update_model_phase(self.label, "error", str(error))
        return f"错误: {error}"

    async def _call_model_impl(self, messages, model=None, tools=None, display=None, label=None, silent=False):
        """调用模型，包装 ModelResult 为 (reasoning, content, usage, tool_calls) 元组

        无超时限制，等待到底（与工具执行策略一致）。
        CancelledError 透传不消化，由上层
        asyncio.gather(return_exceptions=True) 统一兜底收集。

        优先使用异步 ModelPort（async_model_port.call），
        降级到同步 call_model（兼容旧路径）。
        """
        if self._model_port is not None:
            result = await self._model_port.call(messages, model, tools, display, label, silent)
            return result.reasoning, result.content, result.usage, result.tool_calls
        from ..api.model_async import call_model as _sync_call_model
        result = await asyncio.to_thread(_sync_call_model, messages, model, tools, display, label, silent)
        return result

    def _update_display(self, usage):
        """更新显示状态（累加每次模型调用的 token 到显示层）"""
        if self.display:
            from ..ui.events.event_types import UsageUpdatedEvent, ModelPhaseEvent

            if usage is not None:
                self.display.update_usage(self.label, usage, replace=False)
                # 累加到全局统计
                accumulate_usage(usage)
                # 同步发布到 EventPort（Web 前端通过此事件更新用量显示）
                self._event_port.publish_event(UsageUpdatedEvent(
                    label=self.label, usage=usage, replace=False, source=self.label,
                ))
            self.display.update_model_phase(self.label, "")
            # 同步发布到 EventPort（Web 前端通过此事件更新阶段显示）
            self._event_port.publish_event(ModelPhaseEvent(
                label=self.label, phase="", info="", source=self.label,
            ))

    async def _handle_tool_calls(self, content: str, tool_calls: list, reasoning_content: str = None):
        """处理工具调用（内联执行，使用 asyncio.gather 并发执行独立工具调用）

        SubAgent 运行在独立线程中，使用线程局部存储记录自己的消息索引，
        与父 Agent 的 current_message_index 互不干扰。
        """
        self._append_assistant_message(content, tool_calls, reasoning_content)

        on_before, on_after, run_method = self._build_tool_callbacks(tool_calls)

        async def _execute_one(tc: dict) -> tuple:
            from ..ui.formatters.param_formatter import extract_key_params as _extract_key_params  # 纯工具函数 — 适配器层延迟导入
            detail = _extract_key_params(tc["name"], tc["arguments"], show_all=True)
            try:
                if on_before:
                    # on_before 是同步回调，用 run_in_executor 避免阻塞事件循环
                    await asyncio.get_running_loop().run_in_executor(None, on_before, tc, detail)
                func = self._registry.dispatch(tc["name"], tc["arguments"], agent=self.parent)
                func.agent_type = self.agent_type  # 注入 agent_type，供工具运行时判断权限
                if run_method:
                    output = await run_method(func, tc)
                else:
                    output = await func.execute()
                success = True
            except asyncio.CancelledError:
                output = f"工具执行被取消: {tc.get('name', '?')}"
                _logger.warning("SubAgent tool %s cancelled", tc["name"])
                success = False
            except Exception as e:
                output = f"工具执行失败: {e}"
                _logger.error("SubAgent tool %s failed: %s", tc["name"], e)
                success = False
            # ★ P0 修复: on_after 移入 try 块，确保异常不会传播到 asyncio.gather
            #   导致 results 列表混入 Exception 对象、后续解包崩溃。
            if on_after:
                try:
                    # on_after 是同步回调，用 run_in_executor 避免阻塞事件循环
                    await asyncio.get_running_loop().run_in_executor(None, on_after, tc, output, success)
                except Exception:
                    _logger.exception("SubAgent on_after 回调异常")
            return (tc["id"], output, success)

        results = await asyncio.gather(
            *[_execute_one(tc) for tc in tool_calls],
            return_exceptions=True,
        )

        # 收集所有工具结果（含被取消/失败的），确保 messages 序列完整，
        # 与 Agent._handle_tool_calls 行为一致：所有工具的 result 始终追加。
        for item in results:
            # ★ return_exceptions=True 时，CancelledError/KeyboardInterrupt
            #   等 BaseException 也会出现在 results 中，跳过而非解包。
            if isinstance(item, BaseException):
                _logger.error("SubAgent 工具执行异常: %s", item)
                continue
            tool_call_id, output, success = item
            self.messages.append({
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": output,
            })

    def _build_tool_callbacks(
        self, tool_calls: list,
    ) -> Tuple[Optional[Callable], Optional[Callable], Optional[Callable]]:
        """构建工具执行回调三元组 (on_before, on_after, run_method)"""
        from ..ui.events.event_types import ToolStartedEvent, ToolDoneEvent
        from .internal._tool_callbacks import _run_file_display

        display = self.display

        def on_before(tc, detail):
            tool_name = tc["name"]
            if display:
                display.tool_parsing(self.label, tool_name, detail)
                display.tool_start(self.label, tool_name, detail)
            self._event_port.publish_event(ToolStartedEvent(
                label=self.label, tool_name=tool_name, detail=detail, source=self.label,
                tool_id=tc["id"],
            ))

        def on_after(tc, output, success):
            tool_name = tc["name"]
            if success:
                # on_after 通过 run_in_executor 在线程池中执行，
                # 使用 threading.Lock 保护自增操作。
                with self._tool_calls_count_lock:
                    self.tool_calls_count += 1
            if display:
                display.tool_done(self.label, tool_name, success=success)
            self._event_port.publish_event(ToolDoneEvent(
                label=self.label, tool_name=tool_name, success=success, source=self.label,
                tool_id=tc["id"],
            ))

        async def run_method(func, tc):
            if tc["name"] in ("write_file", "update_file"):
                return await _run_file_display(func, display)
            return await func.execute()

        return on_before, on_after, run_method


# ── 端口依赖边界说明 ─────────────────────────────────
# SubAgent 通过 self._event_port（EventPort）发送事件到前端：
# 1. _event_port 从父 Agent 继承，父 Agent 在 __init__ 中注入 EventPort 实现
# 2. 事件发布路径：SubAgent → self._event_port.publish_event() → EventPort 适配器 → DisplayEventBus
# 3. extract_key_params 是工具参数格式化函数，无 UI 副作用，作为纯工具函数延迟导入
