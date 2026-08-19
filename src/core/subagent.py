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
# - execute: 计划执行型（默认），保留读写工具 + bash，排除 web_search + subagent + user_select，
#   无路径白名单限制，用于执行计划文件步骤并返回修改文件列表
_TOOL_EXCLUSION_MAP = {
    "map": {
        "bash", "bash_opt", "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "web_search",
        "subagent", "user_select",
    },
    "review": {
        "bash", "bash_opt", "subagent_opt", "write_file", "update_file", "rm", "mv", "cp", "mkdir",
        "subagent", "user_select",
    },
    "plan": {
        "bash", "bash_opt", "subagent_opt",
        "rm",
        "mv",
        "cp",
        "subagent",
        "user_select",
    },
    "execute": {
        "subagent",
        "subagent_opt",
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
        dispatch_label: str = "",
    ):
        super().__init__()

        self.label = label
        self.description = description
        self.prompt = prompt
        self.parent = parent_agent
        self._event_port = getattr(parent_agent, '_event_port', None)
        self.agent_type = agent_type
        # ★ 2026-08-17（用户需求：agent 内容合并到 subagent）：所属
        #   subagent 工具的 label（tool_call_id）——运行时与面板槽位
        #   dispatch_label 同源（spec["tool_label"]）；随 _record_to_parent
        #   写入会话存档，load 恢复后主轨迹仍可把历史 subagent 合并到
        #   subagent 工具记录（不分两条）。
        self.dispatch_label = dispatch_label

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
            # ★ SubAgent 结束时清理未完成的后台 bash 任务（防资源泄漏）：
            #   SubAgent 内部自动转后台 / background=True 的 bash 任务若未完成，
            #   父 Agent 无法访问其任务记录（挂在 SubAgent 的 _background_tasks
            #   ——bash 专用表；SubAgent 无 subagent 后台任务，_subagent_tasks 恒空），
            #   必须在此取消 asyncio task 并终止子进程，防止 task + 子进程长期
            #   残留（fd/进程资源累积 → 后续并行执行卡死）。
            #   清理异常（如再入 CancelledError）不得阻断 _record_to_parent
            #   （/export 导出完整性，P1-4 修复）。
            try:
                await self._cleanup_background_tasks()
            except BaseException:
                _logger.exception(
                    "SubAgent %s 清理后台任务异常（不影响记录导出）", self.label,
                )
            finally:
                # 无论成功/失败/取消，均将完整对话记录到父 Agent（供 /export 导出）
                self._record_to_parent()

    async def _cleanup_background_tasks(self) -> None:
        """清理 SubAgent 内部未完成的后台 bash 任务。

        取消仍在运行的 asyncio task（_run_pty 的 CancelledError 分支会杀进程树），
        对已记录 pid 且进程尚未退出的任务兜底杀进程树，最后清空任务记录。
        """
        tasks_to_cancel: list = []
        bg = getattr(self, "_background_tasks", {})
        if bg is None:
            bg = {}
        elif not isinstance(bg, dict):
            _logger.warning(
                "SubAgent %s 的 _background_tasks 类型异常: %s（回退为空 dict）",
                getattr(self, "label", "?"), type(bg).__name__,
            )
            bg = {}
        for _task_id, rec in bg.items():
            # record 级防御（P2-2）：异常 record 跳过，避免中断整个清理
            if not isinstance(rec, dict):
                _logger.warning(
                    "SubAgent %s 后台任务记录类型异常: %s（跳过）",
                    getattr(self, "label", "?"), type(rec).__name__,
                )
                continue
            task = rec.get("task")
            if task is not None and not task.done():
                tasks_to_cancel.append(task)
            process = rec.get("process")
            # 兜底杀进程树（P1-2）：仅当进程仍可能存活时执行。
            #   process.returncode 非 None 表示进程已退出（进程组已解散），
            #   pid 可能已被 OS 复用，此时 killpg(pid) 会误杀无关进程组——
            #   安全红线（禁止影响未授权进程）。
            pid = rec.get("pid")
            process_alive = (process is None or process.returncode is None)
            if pid is not None and process_alive:
                try:
                    from ..tools.bash import kill_process_tree
                    kill_process_tree(pid)
                except Exception:
                    _logger.debug("SubAgent 清理后台任务进程树失败: %s", pid, exc_info=True)
            # ★ P2-2：不再单独 process.kill()——kill_process_tree 已覆盖
            #   进程组 + /proc 后代补杀；此处若再用同一 pid 调用 process.kill()，
            #   killpg 之后 returncode 更新有延迟，pid 可能已被 OS 复用，
            #   存在误杀无关进程的风险（安全红线）。
        for t in tasks_to_cancel:
            t.cancel()
        if tasks_to_cancel:
            # ★ 取消等待带超时（P1-3）：被取消的后台 bash 任务可能卡在
            #   process.wait()（子进程不可杀），无界等待会让 SubAgent.run
            #   的 finally 永不结束 → 父 Agent dispatch 等待 → 整个并行执行
            #   卡死（与本次修复目标冲突）。进程树已 kill，超时后放弃等待，
            #   残余 task 由事件循环 GC 回收。
            try:
                await asyncio.wait_for(
                    asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                    timeout=5.0,
                )
            except asyncio.TimeoutError:
                _logger.warning(
                    "SubAgent %s 后台任务取消等待超时（%d 个任务），放弃等待",
                    self.label, len(tasks_to_cancel),
                )
            except Exception:
                _logger.debug("SubAgent 清理后台任务等待取消异常", exc_info=True)
        # ★ 时序说明（P3-3）：此处的 task 完成回调（_complete_background_task
        #   → 写 record）在上面的 await gather 等待期间于事件循环中执行，
        #   先更新 record 再 clear，无数据丢失。
        # ★ P3：全 done 场景（tasks_to_cancel 为空，无 await 点）时，让出一次
        #   事件循环，使已排队（call_soon）的 _on_done 完成回调先执行并写入
        #   done/result，再 clear——避免窗口记录（task 已完成但回调未执行）
        #   的结果静默丢失。
        await asyncio.sleep(0)
        if getattr(self, "_background_tasks", None):
            self._background_tasks.clear()
            self._publish_background_task_event()

    async def _run_impl(self) -> str:
        """SubAgent 主循环实现（由 run() 包裹，确保 finally 记录完整对话）。"""
        content = ""

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
            # ★ 2026-08-17（用户需求：load 命令后也要合并）：所属 subagent
            #   tool_call_id 随会话存档持久化——/load/--load 恢复后
            #   restore_trace_archive 凭此把历史 subagent 合并到主轨迹对应
            #   subagent 工具记录（旧会话无该字段 → 空串，独立记录兼容）。
            "dispatch_label": self.dispatch_label,
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
            # 兼容不支持重试参数的自定义端口（旧 Mock 等）：TypeError 回退默认调用。
            # 注：若 ModelPort.call 内部因真实 bug 抛 TypeError，此处会误回退并
            # 掩盖错误——权衡取兼容性优先（自定义端口以旧签名为主，内部 TypeError
            # 罕见且二次调用会再次暴露）；如需精确可改签名探测（review P3）。
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
        # 检测 subagent 调用，创建共享 ParallelExecutor
        # （复用 MainAgent 的 _count_dispatch_subagents：排除后台 subagent，
        #  仅显式 background=false 的前台 subagent 计入 barrier）
        from .internal.agent._tool_callbacks import _count_dispatch_subagents
        dispatch_count = _count_dispatch_subagents(tool_calls)
        if dispatch_count > 0:
            from .parallel_executor import ParallelExecutor
            self._shared_executor = ParallelExecutor(self)
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
            # （经公开 release()，不直接触碰 _all_done 私有字段）
            if self._shared_executor is not None:
                self._shared_executor.release()
            self._shared_executor = None

        # 收集所有工具结果，确保 messages 序列完整
        # ★ 防御性修复：ToolScheduler 调度结果可能因极端时序/异常而缺失
        #   某个 tool_call 的结果。若不补发，下一轮模型调用会携带
        #   「assistant 带 tool_calls 但无对应 tool 消息」的不完整历史，
        #   触发 API 400（An assistant message with 'tool_calls' must be
        #   followed by tool messages responding to each 'tool_call_id'）。
        #   此处对缺失结果的 tool_call 补发失败结果，保证消息序列自洽。
        executed_ids: set = set()
        for tool_call_id, output, _ in results:
            self._append_tool_result(tool_call_id, output)
            executed_ids.add(tool_call_id)
        for tc in tool_calls:
            tc_id = tc.get("id")
            if tc_id and tc_id not in executed_ids:
                _logger.warning(
                    "SubAgent %s 工具调用结果缺失 (tool_call_id=%s)，补发失败结果以保持消息序列完整",
                    self.label, tc_id,
                )
                self._append_tool_result(
                    tc_id,
                    f"工具执行失败: 调度器未返回该工具调用的结果 (tool_call_id={tc_id})",
                )

    def _build_tool_callbacks(
        self,
    ) -> Tuple[Optional[Callable], Optional[Callable], Optional[Callable]]:
        """构建工具执行回调三元组 (on_before, on_after, run_method)"""
        from .internal.agent._tool_callbacks import _run_file_display

        display = self.display

        def on_before(tc, detail):
            tool_name = tc["name"]
            if display:
                # ★ BUG（2026-08-16，显示多一行修复）：透传 tool_call_id——
                #   流式 parsing（api/stream/handlers/tool_calls.py）已带
                #   tool_id（_stream_label）发布 ToolParsingEvent，此处不传
                #   tool_id 会与流式 parsing 记录分裂（start 无 id 无法认领
                #   带 id 的 parsing 记录 → 同一次调用两条记录 → 面板同一
                #   工具显示两行）。SubAgent.display 为 EventBusDisplayProxy
                #   （ParallelExecutor 装配），tool_parsing/tool_start 均支持
                #   tool_id 参数。
                tool_id = tc.get("id", "")
                display.tool_parsing(self.label, tool_name, detail, tool_id=tool_id)
                display.tool_start(self.label, tool_name, detail, tool_id=tool_id)

        def on_after(tc, output, success):
            tool_name = tc["name"]
            if success:
                # ToolScheduler 同步调用 on_after，在线程锁保护下自增计数器
                with self._tool_calls_count_lock:
                    self.tool_calls_count += 1
            if display:
                display.tool_done(self.label, tool_name, success=success,
                                  tool_id=tc.get("id", ""))

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
