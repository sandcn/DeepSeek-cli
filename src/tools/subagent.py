"""
subagent — 并行子 Agent 调度工具

模型通过此工具同时派发多个独立子任务，并行执行后汇总结果。
当同一轮有多个 subagent 调用（前台模式）时，共享一个 ParallelExecutor
实现真正的并行。

后台模式（默认，background=True）：立即返回 {"task_id": "sa-xxx", ...} JSON，
后台 subagent 在独立 asyncio 后台任务中执行（不阻塞当前工具调用），
完成后结果由对话轮次自动插入用户消息（_process_background_tasks），
或由 subagent_opt 工具按 task_id 主动管理（read/wait/kill）。

前台模式（background=False）：阻塞等待子 Agent 执行完成，直接返回结果；
同轮多个前台 subagent 共享 ParallelExecutor 真正并行。

★ 后台 subagent 仅主 Agent（MainAgent）可派发：SubAgent 的工具白名单
  本就不含 subagent（_TOOL_EXCLUSION_MAP 全类型排除），此处运行时再强制
  校验 isinstance(agent, SubAgent)，双保险确保 SubAgent 内无法派发后台
  subagent（任务记录也只注册在主 Agent 的 _background_tasks）。
"""

import asyncio
import json
import logging
import time
import uuid

from .base import Func, tool_metadata, print_to_terminal
from ..core.constants import DIM, RESET

logger = logging.getLogger(__name__)

# 低优先级模型适用的 subagent 类型（与前台 execute() 一致）
_LOW_MODEL_TYPES = {"map", "execute"}


def parse_background_flag(args) -> bool:
    """解析 subagent 调用的 background 标志（缺省视为后台）。

    兼容 dict（原始 JSON 对象）与 str（JSON 字符串）；字符串布尔
    （"true"/"false"）正确解析——修复前 ``bool("false")`` 误判为后台。
    解析失败/缺省回退 True（默认后台语义，安全降级）。

    调度层 barrier 计数（core/internal/agent/_tool_callbacks 与
    core/subagent._handle_tool_calls）与本工具 from_args 共用本函数，
    保证「后台判定」与「实际执行模式」一致。
    """
    raw = None
    if isinstance(args, dict):
        raw = args.get("background", True)
    elif isinstance(args, str):
        try:
            data = json.loads(args)
            if isinstance(data, dict):
                raw = data.get("background", True)
        except (json.JSONDecodeError, TypeError):
            return True
    else:
        return True
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes", "on")
    return bool(raw)


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=50,
    tool_category="general",
    description="并行子Agent调度",
)
class SubagentFunc(Func):
    name = "subagent"

    def __init__(self, description: str, prompt: str, target_agent_type: str = "execute",
                 background: bool = True):
        super().__init__()
        self.description = description
        self.prompt = prompt
        self.target_agent_type = target_agent_type  # 目标子Agent类型，与 Func.agent_type 独立
        self.background = bool(background)          # 后台执行模式（默认 True，仅主 Agent 可派发）

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        desc = arguments.get("description", "?")
        # 默认后台：仅显式 background=false（前台）时不带 bg 前缀
        prefix = "" if arguments.get("background") is False else "bg "
        return f"{prefix}agent: {desc}"[:max_len]

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": cls.name,
                "description": (
                    "派发子 Agent 执行任务（独立上下文+文件沙盒），同轮多次调用自动并行。"
                    "type：execute（读写+bash，默认）/ map（只读分析）/ review（代码审查 P0-P3）/ plan（生成计划，仅写 .chat/plan/）。"
                    "同一文件的所有修改必须在单次调用内完成。"
                    "默认后台执行：立即返回 {\"task_id\": ...} JSON，"
                    "后台 subagent 继续运行，完成后结果自动插入对话（或由 subagent_opt "
                    "工具按 task_id 主动管理）。background=false 时前台阻塞执行并直接返回"
                    "子 Agent 执行结果。后台 subagent 仅主 Agent 可派发。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "description": {
                            "type": "string",
                            "description": "简短的任务摘要，显示在 UI 中作为子任务的标题标签。建议不超过50字，提炼任务核心，如「解析 user.py 模块」「测试 auth.py 登录流程」。",
                        },
                        "prompt": {
                            "type": "string",
                            "description": "子Agent的完整任务指令。必须包含：目标、具体文件路径、输出格式要求、约束条件等全部信息。长度不限，但建议结构清晰、分点列出，避免模糊表述，确保子Agent可独立执行无需额外上下文。",
                        },
                        "type": {
                            "type": "string",
                            "enum": ["map", "review", "plan", "execute"],
                            "description": "子Agent类型。execute（默认）：排除 subagent、user_select 和 web_search，保留所有读写工具+bash，无路径限制，用于执行计划文件步骤并返回修改文件列表。map：只读分析型，仅保留 read_file/search/find/ls 等读取工具，专用于项目代码分析和地图生成。review：代码审查型，只读工具集（含 read_file/search/find/ls/web_search），专用于文件列表的 Code Review（P0-P3 分级输出）。plan：计划型，只读分析工具 + write_file/update_file/mkdir（仅限写入 .chat/plan/ 目录），根据指令生成计划。",
                        },
                        "background": {
                            "type": "boolean",
                            "description": (
                                "是否后台执行（默认 true）。true 立即返回 {\"task_id\": \"sa-xxx\", ...} JSON，"
                                "后台 subagent 继续执行（不阻塞当前工具调用）；"
                                "完成后结果由对话轮次自动插入用户消息，"
                                "或用 subagent_opt 工具按 task_id 主动管理（read/wait/kill）。"
                                "false 时前台阻塞执行并直接返回子 Agent 执行结果。"
                                "后台 subagent 仅主 Agent 可派发。"
                            ),
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
            background=parse_background_flag(args),
        )

    @classmethod
    def _resolve_model(cls, agent, agent_type: str):
        """解析子 Agent 使用的模型：指定类型优先使用低优先级模型（与前台路径一致）。

        返回 None 时由 SubAgent 构造回退到父 Agent 模型（model or parent_agent.model）。
        """
        model = getattr(agent, 'model', None)
        if agent_type in _LOW_MODEL_TYPES:
            try:
                config_port = agent.get_config_port()
                low_model = config_port.get_low_model()
                if low_model:  # 非空字符串表示已设置
                    model = low_model
            except Exception:
                pass  # 安全降级：继续使用父模型
        return model

    async def execute(self) -> str:
        if not self.description or not self.prompt:
            return "错误：未提供子 Agent 任务描述或指令"
        if not self.agent:
            return "错误：未关联父 Agent"

        # ── 后台模式（默认）：立即返回 task_id JSON，不阻塞当前工具调用 ──
        if self.background:
            return await self._execute_background()

        # 检查是否有共享的 ParallelExecutor（同一轮多个 subagent）
        shared = getattr(self.agent, '_shared_executor', None)
        if shared is not None and shared.is_batch_mode:
            # 传递 tool_label，用于前端将 subagent 路由到对应的 dispatch 工具容器
            tool_label = getattr(self, 'tool_label', '')

            # 模型选择：对指定类型的 subagent 检查是否应使用低优先级模型
            model = self._resolve_model(self.agent, self.target_agent_type)

            idx = shared.add_agent(self.description, self.prompt, agent_type=self.target_agent_type, model=model, tool_label=tool_label)
            try:
                await shared.register_and_wait()
            except asyncio.CancelledError:
                # 被级联取消时（如同层工具异常触发 FIRST_EXCEPTION），
                # 若自己是最后一个已注册 agent，则唤醒其他等待者防止死锁
                async with shared._agents_lock:
                    if shared._registered_count >= shared._expected_count:
                        shared._all_done.set()
                raise
            r = shared.get_result(idx)
            return self._format_single(r)

        # 独立模式（无 shared_executor）→ 无法执行
        # 正常流程下由 _tool_callbacks.py 创建 shared executor，
        # 此处为异常回退路径（外部非正常调用）
        return ("错误：subagent 未处于有效的并行执行上下文中。"
                "请通过 tools 系统正常调用 subagent。")

    # ── 后台执行模式（默认） ──────────────────────────────
    # background 缺省/true 时：SubAgent 在独立 asyncio 后台任务中执行，
    # 任务记录注册到当前 Agent 的 _background_tasks（与 bash 后台同表），
    # 工具立即返回 {"task_id": "sa-xxx", "status": "running"} JSON。
    # 一轮对话完成后 _process_background_tasks 检查：已完成 → 结果作为
    # 用户消息插入对话；未完成 → 带超时等待后同样插入（模型可经
    # subagent_opt 工具继续 read/wait/kill 管理）。

    async def _execute_background(self) -> str:
        """后台执行 subagent：生成 task_id、注册到主 Agent 后台任务列表，立即返回 JSON。

        仅主 Agent 可派发（运行时强制校验，与工具白名单双保险）。
        任务记录注册到 ``agent._background_tasks``，一轮对话完成后由
        ``_process_background_tasks`` 自动处理（与 bash 后台任务同机制）。
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
        与前台路径一致的 UI 事件（SubagentPromptEvent/AgentResultEvent）、
        面板管理、stdout 泄漏检测与结果格式化。完成后经
        ``agent._complete_subagent_task`` 写入 subagent 任务记录（subagent
        专用表 _subagent_tasks），供 _process_subagent_tasks / subagent_opt
        读取——与 bash 后台任务（_background_tasks）完全独立。

        文件沙盒（SandboxManager）处理：
        - 后台任务由 asyncio.ensure_future 创建，复制派发时 contextvars
          （含 message_index）——SubAgent 文件操作经 record_file_change_
          from_context 关联到**派发轮次**的消息索引（与前台 subagent 语义
          一致，沙盒可精确回滚到派发轮次）；
        - 若 MainAgent 在后台 subagent 运行期间发生上下文压缩
          （remap_indices 删除派发轮次消息），派发索引可能已失效：完成后
          检测 ``spawn_index > current_index`` 并把本任务新增的沙盒记录
          重挂到当前有效索引，避免悬空记录（回滚丢失）。
        """
        agent = self.agent

        # 模型选择：与前台 execute() 一致（map/execute 类型优先低优先级模型）
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
            from ..core.parallel_executor import ParallelExecutor
            executor = ParallelExecutor(agent)
            results = await executor.run([spec])
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
