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
from .tool_executor_async import ToolScheduler
from .exceptions import is_network_error

_logger = logging.getLogger(__name__)

# ── 网络错误重试上限 ────────────────────────────────────
# SubAgent.run() 中每次独立模型调用最多重试 3 次（含首次）
_NETWORK_RETRY_MAX = 3

# ── 类型策略：agent_type → 排除的工具集合 ─────────────
# 每种类型映射一个不可用工具集，便于未来扩展。
#
# 策略差异说明：
# - map: 只读分析，排除所有写入类工具 + web_search
# - review: 代码审查，排除所有写入类工具，保留 web_search（可查文档）
# - plan: 计划生成，保留 write_file/update_file，但在 FileToolBase
#   ._validate_path_and_size() 中有额外的路径白名单校验（仅限 .chat/plan/）
# - execute: 计划执行型（默认），保留读写工具 + bash，排除 web_search + dispatch_agent + user_select，
#   无路径白名单限制，用于执行计划文件步骤并返回修改文件列表
_TOOL_EXCLUSION_MAP = {
    "map": {
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "web_search",
        "dispatch_agent", "user_select",
    },
    "review": {
        "bash", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "dispatch_agent", "user_select",
    },
    "plan": {
        "bash",
        "rm",
        "mv",
        "cp",
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

        # 通过 parent_agent 的 DefaultPromptBuilderAdapter 构建 system prompt，
        # 确保与 MainAgent 使用一致的 cwd 和环境信息（修复 Bug#4）
        prompt_port = parent_agent.get_prompt_builder_port()
        if agent_type == "map":
            system_parts = prompt_port.build_map_agent_prompt()
        elif agent_type == "review":
            system_parts = prompt_port.build_review_agent_prompt()
        elif agent_type == "plan":
            system_parts = prompt_port.build_plan_agent_prompt()
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
        self._shared_executor = None
        self._display_port = None
        self.result: str = ""
        self.error: str = ""
        self.tool_calls_count = 0
        self._tool_calls_count_lock = threading.Lock()

    def get_config_port(self):
        """返回 ConfigPort 实例（委托给 parent Agent）"""
        return self.parent.get_config_port()

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

        网络错误重试：
        - 模型调用异常或返回内容包含网络错误关键词时，最多重试3次
        - 每次重试前向 messages 追加"【继续】"消息通知模型
        - 非网络错误直接返回，不重试
        """
        try:
            return await self._run_impl()
        finally:
            # 无论成功/失败/取消，均将完整对话记录到父 Agent（供 /export 导出）
            self._record_to_parent()

    async def _run_impl(self) -> str:
        """SubAgent 主循环实现（由 run() 包裹，确保 finally 记录完整对话）。"""
        content = ""
        # 日志截断长度
        _LOG_TRUNCATE_LEN = 100

        while True:
            # ── 模型调用（含网络错误重试） ──────────────
            retry_count = 0
            while retry_count < _NETWORK_RETRY_MAX:
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
                    if retry_count < _NETWORK_RETRY_MAX - 1 and is_network_error("", e):
                        retry_count += 1
                        _logger.warning(
                            "SubAgent %s 模型调用网络错误 (第%d次重试): %s",
                            self.label, retry_count, e,
                        )
                        self.messages.append({
                            "role": "user",
                            "content": "【继续】网络错误已恢复，请重试",
                        })
                        continue  # 重新调用模型
                    return self._handle_model_error(e)

                # ── 模型调用成功，检查返回内容是否含网络错误 ──
                # API 层重试用尽后返回错误字符串（不抛异常），需在此检测
                if not tool_calls and is_network_error(content, None):
                    if retry_count < _NETWORK_RETRY_MAX - 1:
                        retry_count += 1
                        _logger.warning(
                            "SubAgent %s 返回内容含网络错误 (第%d次重试): %s",
                            self.label, retry_count, content[:100],
                        )
                        self.messages.append({
                            "role": "user",
                            "content": "【继续】网络错误已恢复，请重试",
                        })
                        continue  # 重新调用模型
                    # 重试用尽，返回错误
                    err_msg = content or "网络错误重试失败"
                    _logger.error(
                        "SubAgent %s 网络错误重试 %d 次仍失败: %s",
                        self.label, _NETWORK_RETRY_MAX, err_msg[:200],
                    )
                    self.error = err_msg
                    return f"错误: {err_msg}"

                # 模型调用正常，跳出重试循环
                break

            self._update_display(usage)

            if not tool_calls:
                # ── 后台任务处理：模型完成对话但后台任务可能有结果 ──
                # bash 后台任务（background=True）注册在本 SubAgent 的
                # _background_tasks 成员中；有完成/等待完成的后台任务时，
                # 把结果 JSON 作为用户消息插入，继续让模型处理一轮。
                if await self._process_background_tasks():
                    continue
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

    def _record_to_parent(self) -> None:
        """将 SubAgent 完整对话记录到父 Agent（供 /export 导出到 markdown）。

        子代理在 run() 结束时（无论成功/失败/取消）把自身完整消息列表
        （system/user/assistant/tool 全部往返）挂到 parent_agent 的
        ``_subagent_records`` 列表上，/export 命令据此渲染 subagent 聊天信息。

        去重策略：以 label 为键，同 label（重试场景重新派发）覆盖旧记录。
        """
        parent = getattr(self, "parent", None)
        if parent is None:
            return
        records = getattr(parent, "_subagent_records", None)
        if records is None:
            records = []
            setattr(parent, "_subagent_records", records)
        record = {
            "label": self.label,
            "description": self.description,
            "agent_type": self.agent_type,
            "prompt": self.prompt,
            "status": "error" if self.error else "done",
            "result": self.result,
            "error": self.error,
            "tool_calls_count": self.tool_calls_count,
            "messages": [dict(m) for m in self.messages],
        }
        for i, r in enumerate(records):
            if r.get("label") == self.label:
                records[i] = record
                return
        records.append(record)

    async def _call_model_impl(self, messages, model=None, tools=None, display=None, label=None, silent=False):
        """调用模型，包装 ModelResult 为 (reasoning, content, usage, tool_calls) 元组

        无超时限制，等待到底（与工具执行策略一致）。
        CancelledError 透传不消化，由上层
        asyncio.gather(return_exceptions=True) 统一兜底收集。

        优先使用异步 ModelPort（async_model_port.call），
        降级到同步 call_model（兼容旧路径）。

        重试策略：SubAgent 传 ``override_max_retries=1`` + ``fixed_delay_sec=0``，
        禁用 API 层全局长重试（默认 MAX_RETRIES=10 × RETRY_BASE_SEC=30 ≈ 5 分钟）。
        原因：
        - SubAgent 是并行临时任务，API 报错时应快速失败返回结果，而非长时间
          重试拖住父 Agent（用户侧现象：子代理"调用一两个工具后卡住 5 分钟"）。
        - API 层不再长重试后，由 SubAgent 主循环的 ``_NETWORK_RETRY_MAX=3``
          提供有限次快速重试（每次请求秒级失败，不叠加长等待）。
        """
        if self._model_port is not None:
            # 兼容不支持重试参数的自定义端口（旧 Mock 等）：TypeError 回退默认调用
            try:
                result = await self._model_port.call(
                    messages, model, tools, display, label, silent,
                    override_max_retries=1, fixed_delay_sec=0,
                )
            except TypeError:
                result = await self._model_port.call(
                    messages, model, tools, display, label, silent,
                )
            return result.reasoning, result.content, result.usage, result.tool_calls
        from ..api.model_async import call_model as _sync_call_model
        result = await asyncio.to_thread(_sync_call_model, messages, model, tools, display, label, silent)
        return result

    def _update_display(self, usage):
        """更新显示状态（仅显示层）。

        注意：token 统计由模型调用层统一累计（_call_model_impl → api 层
        stream_call_async/_call_sync_async 内部已 accumulate_usage），
        此处不再重复累计，否则 SubAgent 每次模型调用的 input/output/calls
        会被统计两次，导致 /cost 输入 tok 翻倍（Bug 修复）。
        """
        from ..tui.events.event_types import UsageUpdatedEvent, ModelPhaseEvent

        if self.display:
            if usage is not None:
                self.display.update_usage(self.label, usage, replace=False)
            self.display.update_model_phase(self.label, "")
        else:
            # display 为 None 时回退到 EventPort 路径
            if usage is not None:
                self._event_port.publish_event(UsageUpdatedEvent(
                    label=self.label, usage=usage, replace=False, source=self.label,
                ))
            self._event_port.publish_event(ModelPhaseEvent(
                label=self.label, phase="", info="", source=self.label,
            ))

    async def _handle_tool_calls(self, content: str, tool_calls: list, reasoning_content: str = None):
        """处理工具调用（委托给全局 ToolScheduler 单例统一调度）

        ToolScheduler 自动根据工具数量和依赖关系选择执行策略：
        - 空列表 → 直接返回
        - 全部工具 → 通过全局 DAG 拓扑分层调度（单工具/多工具统一）
        """
        # 检测 dispatch_agent 调用，创建共享 ParallelExecutor
        dispatch_count = sum(1 for tc in tool_calls if tc.get("name") == "dispatch_agent")
        if dispatch_count > 0:
            from .parallel_executor import ParallelExecutor
            self._shared_executor = ParallelExecutor(self, is_web=False)
            self._shared_executor.setup_barrier(dispatch_count)
        else:
            self._shared_executor = None

        self._append_assistant_message(content, tool_calls, reasoning_content)

        on_before, on_after, run_method = self._build_tool_callbacks()

        # UNIQUE_PATH: SubAgent 工具执行入口，项目唯二 schedule() 调用方之一
        try:
            results = await ToolScheduler.default().schedule(
                tool_calls,
                agent_ref=self,
                on_before=on_before,
                on_after=on_after,
                run_method=run_method,
            )
        finally:
            # 确保取消/异常时释放 barrier，防止死锁
            if self._shared_executor is not None:
                self._shared_executor._all_done.set()
            self._shared_executor = None

        # 收集所有工具结果，确保 messages 序列完整
        for tool_call_id, output, _ in results:
            self._append_tool_result(tool_call_id, output)

    def _build_tool_callbacks(
        self,
    ) -> Tuple[Optional[Callable], Optional[Callable], Optional[Callable]]:
        """构建工具执行回调三元组 (on_before, on_after, run_method)"""
        from .internal.agent._tool_callbacks import _run_file_display

        display = self.display

        def on_before(tc, detail):
            tool_name = tc["name"]
            if display:
                display.tool_parsing(self.label, tool_name, detail)
                display.tool_start(self.label, tool_name, detail)

        def on_after(tc, output, success):
            tool_name = tc["name"]
            if success:
                # ToolScheduler 同步调用 on_after，在线程锁保护下自增计数器
                with self._tool_calls_count_lock:
                    self.tool_calls_count += 1
            if display:
                display.tool_done(self.label, tool_name, success=success)

        async def run_method(func, tc):
            from .internal.agent._tool_context import run_with_tool_context
            # SubAgent 事件 label 为 agent-1 等（与 start_capture 语义一致），
            # 工具输出经 contextvar 定向路由到对应 label。
            if tc["name"] in ("write_file", "update_file"):
                coro = _run_file_display(func, display)
            else:
                coro = func.execute()
            return await run_with_tool_context(self.label, coro)

        return on_before, on_after, run_method


# ── 端口依赖边界说明 ─────────────────────────────────
# SubAgent 通过 self._event_port（EventPort）发送事件到前端：
# 1. _event_port 从父 Agent 继承，父 Agent 在 __init__ 中注入 EventPort 实现
# 2. 事件发布路径：SubAgent → self._event_port.publish_event() → EventPort 适配器 → DisplayEventBus
# 3. extract_key_params 是工具参数格式化函数，无 UI 副作用，作为纯工具函数延迟导入
