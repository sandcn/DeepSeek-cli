"""
subagent — 子 Agent 调度工具

模型通过此工具派发独立子任务，**直接后台执行**（无 background 参数、
无前台阻塞模式）：工具立即返回 {"task_id": "sa-xxx", ...} JSON，
后台 subagent 在独立 asyncio 后台任务中执行（不阻塞当前工具调用），
完成后结果由对话轮次自动插入用户消息（_process_subagent_tasks），
或由 subagent_opt 工具按 task_id 主动管理（read/wait/kill）。

同轮多次调用自动并行——每次调用各自独立后台执行，互不阻塞。

★ 后台 subagent 仅主 Agent（MainAgent）可派发：SubAgent 的工具白名单
  本就不含 subagent（_TOOL_EXCLUSION_MAP 全类型排除），此处运行时再强制
  校验 isinstance(agent, SubAgent)，双保险确保 SubAgent 内无法派发后台
  subagent（任务记录也只注册在主 Agent 的 _subagent_tasks）。
"""

import asyncio
import json
import logging
import time
import uuid

from .base import Func, print_to_terminal
from ..core.constants import DIM, RESET
from ..core.agent_types import uses_low_model

logger = logging.getLogger(__name__)


class SubagentFunc(Func):
    name = "subagent"

    def __init__(self, description: str, prompt: str, target_agent_type: str = "execute"):
        super().__init__()
        self.description = description
        self.prompt = prompt
        self.target_agent_type = target_agent_type  # 目标子Agent类型，与 Func.agent_type 独立

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        desc = arguments.get("description", "?")
        return f"agent: {desc}"[:max_len]

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": (
                    "派发子 Agent 执行任务，立即返回 task_id（sa-xxx）后台执行；"
                    "type：execute（默认）/ map / review / plan。仅主 Agent 可用，"
                    "结果在轮次结束后自动插入对话，也可用 subagent_opt 管理。"
                    "例：subagent({\"type\": \"map\", \"description\": \"分析: src/tools\", "
                    "\"prompt\": \"输出调用链与关联文件清单\"})"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "任务摘要（用于显示与识别，如 分析: src/tools）",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "完整任务指令（自包含）",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["map", "review", "plan", "execute"],
                            "description": "子 Agent 类型（默认 execute）",
                        },
                    },
                    "required": ["description", "prompt"],
                },
            },
        }

    @classmethod
    def from_args(cls, args: dict):
        return cls(
            description=args.get("description", ""),
            prompt=args.get("prompt", ""),
            target_agent_type=args.get("type", "execute"),
        )

    @staticmethod
    def _low_priority_model() -> str:
        """低优先级模型：模型档案中**当前模型之外的首个档案**模型名（无 → 空串）。

        环境变量 ``CHAT_LOW_MODEL`` 与 RC 旧键 ``low_model`` 均已移除——低
        优先级模型同样只能来自模型档案（``model_profiles`` + 当前生效档案）。
        """
        try:
            from ..config.model_profiles import configured_models, current_model

            cur = current_model()
            for name in configured_models():
                if name and name != cur:
                    return name
        except Exception:
            return ""
        return ""

    @classmethod
    def _resolve_model(cls, agent, agent_type: str):
        """解析子 Agent 使用的模型：指定类型优先使用低优先级模型（后台执行路径同样生效）。

        是否使用低优先级模型来自 Agent 类型注册表
        （``src.core.agent_types.uses_low_model``，类型是清单中的独立插件条目）；
        低优先级模型取自模型档案（当前模型之外的首个档案）。返回 None 时由
        SubAgent 构造回退到父 Agent 模型（model or parent_agent.model）。
        """
        model = getattr(agent, 'model', None)
        if uses_low_model(agent_type):
            low_model = cls._low_priority_model()
            if low_model:
                model = low_model
        return model

    async def execute(self) -> str:
        if not self.description or not self.prompt:
            return "错误：未提供子 Agent 任务描述或指令"
        if not self.agent:
            return "错误：未关联父 Agent"

        # ── 直接后台执行：立即返回 task_id JSON，不阻塞当前工具调用 ──
        return await self._execute_background()

    # ── 后台执行模式 ────────────────────────────────────
    # subagent 工具**直接后台执行**（无 background 参数、无前台模式）：
    # SubAgent 在独立 asyncio 后台任务中执行，任务记录注册到当前 Agent
    # 的 _subagent_tasks（subagent 专用表），工具立即返回
    # {"task_id": "sa-xxx", "status": "running"} JSON。
    # 一轮对话完成后 _process_subagent_tasks 检查：已完成 → 结果作为
    # 用户消息插入对话；未完成 → 带超时等待后同样插入（模型可经
    # subagent_opt 工具继续 read/wait/kill 管理）。

    async def _execute_background(self) -> str:
        """后台执行 subagent：生成 task_id、注册到主 Agent 后台任务列表，立即返回 JSON。

        仅主 Agent 可派发（运行时强制校验，与工具白名单双保险）。
        任务记录注册到 ``agent._subagent_tasks``，一轮对话完成后由
        ``_process_subagent_tasks`` 自动处理（与 bash 后台任务同机制）。
        """
        from ..core.subagent import SubAgent  # 延迟导入避免模块加载循环

        agent = self.agent
        if isinstance(agent, SubAgent):
            return ("错误：后台 subagent 仅主 Agent 可派发"
                    "（SubAgent 内不可后台派发 subagent）")
        if agent is None or not hasattr(agent, '_register_subagent_task'):
            return "(后台 subagent 需要关联 Agent 上下文，当前未关联)"

        task_id = f"sa-{uuid.uuid4().hex[:12]}"
        # ensure_future 先于注册：若注册（_register_subagent_task）抛异常，
        # 后台任务仍在运行但记录缺失（结果不会被对话轮次收集）——
        # 风险极低（注册为内存 dict 写入），且 task 由事件循环持有，
        # 不会泄漏；保持顺序以让任务尽快启动（review P3 注释说明）
        task = asyncio.ensure_future(self._run_background_subagent(task_id))

        # ── 任务记录注册到 agent._subagent_tasks（subagent 专用表） ──
        # ★ 与 bash 后台任务（_background_tasks）分表独立：bash_opt 无法
        #   触达本表，subagent_opt 仅操作本表。_process_subagent_tasks
        #   据此在对话轮次间隙自动处理：
        #     已完成 → 结果（JSON：task_id + 输出）作为用户消息插入；
        #     未完成 → 带超时等待（_BACKGROUND_WAIT_TIMEOUT），超时后标记
        #     managed_by_tool 交 subagent_opt 工具管理。
        agent._register_subagent_task(task_id, {
            "task": task,
            "command": f"subagent({self.description})",
            "description": self.description,
            "agent_type": self.target_agent_type,
            "created_at": time.time(),
            "done": False,
            "result": "",
            "status": "running",
            "read_buffer": "",
        })

        await print_to_terminal(
            f"{DIM}[后台 subagent 任务 {task_id} 已启动: "
            f"{self.description[:60]}{'...' if len(self.description) > 60 else ''}]"
            f"{RESET}\n"
        )

        return json.dumps({
            "task_id": task_id,
            "status": "running",
            "description": self.description,
            "type": self.target_agent_type,
        }, ensure_ascii=False)

    async def _run_background_subagent(self, task_id: str) -> None:
        """后台 subagent 执行体：运行 SubAgent 并把结果写入后台任务记录。

        复用 ParallelExecutor.run()（独立模式）执行单个 SubAgent——
        与执行路径一致的 UI 事件（SubagentPromptEvent/AgentResultEvent）、
        面板管理、stdout 泄漏检测与结果格式化。完成后经
        ``agent._complete_subagent_task`` 写入 subagent 任务记录（subagent
        专用表 _subagent_tasks），供 _process_subagent_tasks / subagent_opt
        读取——与 bash 后台任务（_background_tasks）完全独立。

        文件沙盒（SandboxManager）处理：
        - 后台任务由 asyncio.ensure_future 创建，复制派发时 contextvars
          （含 message_index）——SubAgent 文件操作经 record_file_change_
          from_context 关联到**派发轮次**的消息索引（与 subagent 执行语义
          一致，沙盒可精确回滚到派发轮次）；
        - 若 MainAgent 在后台 subagent 运行期间发生上下文压缩
          （remap_indices 删除派发轮次消息），派发索引可能已失效：完成后
          检测 ``spawn_index > current_index`` 并把本任务新增的沙盒记录
          重挂到当前有效索引，避免悬空记录（回滚丢失）。
        """
        agent = self.agent

        # 模型选择：与 execute() 一致（map/execute 类型优先低优先级模型）
        model = self._resolve_model(agent, self.target_agent_type)

        # ── 文件沙盒快照（执行前） ──
        # 1. spawn_index：派发时捕获的消息索引（contextvar 已由
        #    ensure_future 复制；None 时回退沙盒当前索引）。
        # 2. before_record_ids：执行前沙盒已有记录身份集合——完成后对比
        #    识别本后台 subagent 新增的记录（重挂时不动其他记录）。
        from ..core.sandbox_manager import get_sandbox_manager, get_current_message_index
        sandbox = get_sandbox_manager()
        spawn_index = get_current_message_index()
        if spawn_index is None and sandbox is not None:
            spawn_index = sandbox.get_current_message_index_safe()
        before_record_ids: set = set()
        if sandbox is not None:
            try:
                # id(r) 身份快照：记录对象在本函数存活期间不被 GC，id 复用
                # 风险仅存在于极端对象生命周期交错场景，当前安全（review P3）
                before_record_ids = {
                    id(r) for r in sandbox.get_all_file_changes()
                }
            except Exception:
                logger.debug("后台 subagent 沙盒快照失败（跳过索引修复）", exc_info=True)
                before_record_ids = set()

        spec = {
            "label": task_id,  # 唯一 label：并发后台 subagent 不互相覆盖（P1）
            "description": self.description,
            "prompt": self.prompt,
            "agent_type": self.target_agent_type,
            "model": model,
            # 传递 tool_label（tool_call_id）→ dispatch_label：随会话存档写入，
            # load 恢复后主轨迹仍可把该 subagent 合并到所属工具记录。
            "tool_label": getattr(self, 'tool_label', ''),
        }
        try:
            # 「一切皆插件」：SubAgent 运行经内核 ``ctx.subagents`` 服务
            # （可替换的执行器装配）；内核缺失时回退既有 ParallelExecutor。
            from ..core.adapters import kernel_runtime as _kr

            service = _kr.active_subagents_service()
            if service is not None:
                results = await service.run(agent, [spec])
            else:
                from ..core.parallel_executor import ParallelExecutor

                results = await ParallelExecutor(agent).run([spec])
            if results:
                result = self._format_single(results[0])
            else:
                result = f"(后台 subagent {task_id} 未返回结果)"
        except asyncio.CancelledError:
            result = f"(后台 subagent {task_id} 已被取消)"
            if agent is not None and hasattr(agent, '_complete_subagent_task'):
                try:
                    agent._complete_subagent_task(task_id, result, status="cancelled")
                except Exception:
                    logger.exception("后台 subagent 取消结果写入失败")
            return
        except Exception as e:
            logger.exception("后台 subagent 执行异常: %s", self.description[:200])
            result = f"(后台 subagent 执行出错: {e})"

        # ── 文件沙盒索引修复（完成后） ──
        # 上下文压缩（remap_indices）删除派发轮次消息后，``spawn_index >
        # current`` 表明派发索引已失效——把本任务新增的沙盒记录重挂到当前
        # 有效索引（仅重挂 id 不在 before 快照中的记录，不影响其他来源）。
        if sandbox is not None and spawn_index is not None:
            try:
                current = sandbox.get_current_message_index_safe()
                if spawn_index > current:
                    new_record_ids = {
                        id(r) for r in sandbox.get_all_file_changes()
                    } - before_record_ids
                    if new_record_ids:
                        moved = sandbox.reindex_records(
                            lambda r, si=spawn_index: (
                                id(r) in new_record_ids and r.message_index == si
                            ),
                            current,
                        )
                        if moved:
                            logger.info(
                                "后台 subagent %s 沙盒记录重挂 %d 条"
                                "（派发索引 %s 已被压缩，重挂到 %s）",
                                task_id, moved, spawn_index, current,
                            )
            except Exception:
                logger.debug("后台 subagent 沙盒索引修复失败", exc_info=True)

        if agent is not None and hasattr(agent, '_complete_subagent_task'):
            try:
                agent._complete_subagent_task(task_id, result)
            except Exception:
                logger.exception("后台 subagent 结果写入失败")
        else:
            logger.warning("后台 subagent %s 完成但 agent 已不可用，结果丢弃", task_id)
            return

        # ★ 完成提示发布为普通输出（OutputEvent），而非工具输出
        #   （ToolOutputChunkEvent）：此时工具上下文已退出（contextvar 中
        #   tool_id 已清除），走工具输出路径会以 label="assistant" 触发
        #   append_tool_output 兜底创建永不闭合的空「工具」卡。
        try:
            from ..core.display_target import get_output_publisher
            publisher = get_output_publisher()
            if publisher is not None:
                publisher(f"[后台 subagent 任务 {task_id} 已完成]",
                          level="info", source="agent")
        except Exception:
            logger.debug("后台 subagent 完成提示发布失败", exc_info=True)

    @staticmethod
    def _format_single(r: dict) -> str:
        """格式化单个 agent 结果。"""
        header = f"## {r['description']}"
        if r.get("error"):
            return f"{header}\n错误: {r['error']}"
        result = r.get('result', '') or ''
        return f"{header}\n{result}"

    async def display(self) -> str:
        return await self.execute()
