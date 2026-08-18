"""
subagent_opt — 按 task_id 操作后台 subagent 任务

配合 subagent 工具（默认后台）使用。subagent 后台启动后返回
{"task_id": "sa-xxx", "status": "running", "description": "...", "type": "..."}，
大模型可据此用 subagent_opt 工具按 task_id 操作：

- op=read  读取后台 subagent 任务当前状态与已产生的结果，立即返回（不等待完成）
- op=wait  等待任务执行完成并获取结果（JSON：task_id/description/status/output）
- op=kill  取消后台 subagent 任务（级联取消其内部 SubAgent）

后台 subagent 仅主 Agent 独有（subagent 工具运行时校验 + SubAgent 工具
白名单排除），因此 subagent_opt 同样仅主 Agent 可用（SubAgent 内无
subagent 后台任务可管理）。
"""

from __future__ import annotations

import asyncio
import json
import logging

from .base import Func, tool_metadata

logger = logging.getLogger(__name__)


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="general",
    priority=50,
    tool_category="general",
    description="操作后台subagent任务",
)
class SubagentOptFunc(Func):
    """按 task_id 操作后台 subagent 任务（subagent 默认后台启动）。"""

    name = "subagent_opt"
    _DEFAULT_WAIT_TIMEOUT: int = 300

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "subagent_opt",
                "description": (
                    "按 task_id 操作后台 subagent 任务（subagent 默认后台启动，"
                    "background 缺省即后台）。"
                    "op：read（读取当前状态与已产生的结果，立即返回不等待完成）、"
                    "wait（等待完成取结果，timeout 秒，默认 300/0 无限）、"
                    "kill（取消后台 subagent 任务）。"
                    "task_id 必须是当前对话 subagent 后台返回的 sa-xxx。"
                    "后台 subagent 仅主 Agent 可派发。返回：操作结果 JSON 或输出；失败以 ( 开头。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "后台 subagent 任务的 task_id（subagent 默认后台返回的 "
                                "'sa-xxx' 格式 ID）。"
                            ),
                        },
                        "op": {
                            "type": "string",
                            "enum": ["read", "wait", "kill"],
                            "description": (
                                "要执行的操作："
                                "\n- read：读取后台 subagent 任务当前状态与已产生的结果，"
                                "立即返回（不等待任务完成）；任务继续运行"
                                "\n- wait：等待任务完成并获取结果"
                                "\n- kill：取消后台 subagent 任务（级联取消其内部 SubAgent）"
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                "仅 wait 操作生效：等待完成的超时秒数（默认 300；"
                                "传 0 表示无限等待）。支持小数（如 0.5）。"
                                "超时后任务继续运行，可再次等待或 kill。"
                            ),
                        },
                    },
                    "required": ["task_id", "op"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        task_id = arguments.get("task_id", "")
        op = arguments.get("op", "")
        return f"'{op} {task_id}'"

    def __init__(self, task_id: str, op: str, timeout=None):
        super().__init__()
        self.task_id = task_id
        self.op = op
        # timeout 仅对 wait 生效：省略/None → 300s；<=0 → 无限等待
        # 使用 float 保留小数（如 0.5 秒短超时），避免 int() 截断
        if timeout is None:
            self.timeout = self._DEFAULT_WAIT_TIMEOUT
        else:
            try:
                timeout = float(timeout)
            except (TypeError, ValueError):
                timeout = self._DEFAULT_WAIT_TIMEOUT
            self.timeout = None if timeout <= 0 else timeout

    # ── execute ──────────────────────────────────────────

    async def execute(self) -> str:
        """按 task_id 和 op 操作后台 subagent 任务，返回结果字符串。"""
        from ..core.subagent import SubAgent  # 延迟导入避免模块加载循环

        agent = self.agent
        if isinstance(agent, SubAgent):
            return ("错误：subagent_opt 仅主 Agent 可用"
                    "（SubAgent 内不可管理后台 subagent）")
        if agent is None or not hasattr(agent, '_subagent_tasks'):
            return "(后台 subagent 操作需要关联 Agent 上下文，当前未关联)"

        # ★ task_id 前缀校验（P2，review 2026-08-18）：subagent 后台任务 id
        #   恒为 "sa-xxx"，且注册在 subagent 专用表 _subagent_tasks 中——
        #   误传 bash 后台任务（bg-xxx）时直接提示（bash 任务在 bash 专用表
        #   _background_tasks，本工具查不到也不该操作，需用 bash_opt 管理）。
        if not self.task_id.startswith("sa-"):
            return (f"(错误：task_id 必须是 subagent 后台启动（默认后台）返回的 "
                    f"'sa-xxx' 格式 ID，当前: {self.task_id}。"
                    f"bash 后台任务请用 bash_opt 操作)")

        rec = agent._subagent_tasks.get(self.task_id)
        if rec is None:
            return (f"(后台 subagent 任务不存在: {self.task_id}。"
                    f"请先用 subagent 启动后台任务（默认后台）获取 task_id)")

        # ★ 仅对有效 op 标记 managed_by_tool（P2，review 2026-08-18）：
        #   未知 op（如 "pause"）不修改任务管理状态——否则任务被标记为
        #   "已由工具管理"但模型不知道如何继续，_process_subagent_tasks
        #   不再自动等待/插入结果，任务进入「失联」状态。
        if self.op in ("read", "wait", "kill"):
            rec["managed_by_tool"] = True

        if self.op == "read":
            return await self._op_read(rec)
        if self.op == "wait":
            return await self._op_wait(agent, rec)
        if self.op == "kill":
            return await self._op_kill(agent, rec)
        return f"(未知操作: {self.op}。支持: read/wait/kill)"

    # ── op=read ──────────────────────────────────────────

    async def _op_read(self, rec: dict) -> str:
        """读取后台 subagent 任务当前状态与已产生的结果，立即返回。

        返回 JSON（task_id/description/agent_type/status/output）：
          - status: 任务当前状态（running / completed）
          - output: 已产生的结果（任务未完成时为空；subagent 无中间输出流，
            最终结果由 wait 完整获取）
        """
        done = bool(rec.get("done"))
        status = rec.get("status") or ("completed" if done else "running")
        payload = {
            "task_id": self.task_id,
            "description": rec.get("description", ""),
            "agent_type": rec.get("agent_type", ""),
            "status": status,
            "output": rec.get("result", ""),
        }
        return json.dumps(payload, ensure_ascii=False)

    # ── op=wait ──────────────────────────────────────────

    async def _op_wait(self, agent, rec: dict) -> str:
        """等待任务完成并返回结果（JSON：task_id/description/status/output）。

        完成（或已完成后）把任务记录从 subagent 表移除——大模型已通过本工具
        拿到结果，避免 _process_subagent_tasks 再以用户消息重复插入。

        ★ 使用 asyncio.wait 而非 wait_for：wait_for 超时会 cancel 后台任务
        本身（任务被误杀），wait 只观察不干预，超时后任务继续运行。
        """
        task = rec.get("task")
        if not rec.get("done") and task is not None:
            try:
                if self.timeout:
                    done, _pending = await asyncio.wait({task}, timeout=self.timeout)
                    if not done:
                        return (f"(等待后台 subagent {self.task_id} 超时（{self.timeout} 秒），"
                                f"任务仍在运行。可再次 wait 或 op=kill 取消)")
                else:
                    await task
            except asyncio.CancelledError:
                return f"(等待后台 subagent {self.task_id} 被取消)"
            except Exception as e:
                logger.debug("后台 subagent wait 异常: %s", e)

        # 读取最终结果（任务完成后由 _run_background_subagent 写入 rec）
        result = rec.get("result", "")
        status = rec.get("status", "completed")
        payload = {
            "task_id": self.task_id,
            "description": rec.get("description", ""),
            "agent_type": rec.get("agent_type", ""),
            "status": status,
            "output": result,
        }
        # 移除任务记录（避免 _process_subagent_tasks 重复插入用户消息）
        if hasattr(agent, "_remove_subagent_task"):
            agent._remove_subagent_task(self.task_id)
        else:
            agent._subagent_tasks.pop(self.task_id, None)
        return json.dumps(payload, ensure_ascii=False)

    # ── op=kill ──────────────────────────────────────────

    async def _op_kill(self, agent, rec: dict) -> str:
        """取消后台 subagent 任务并从 tasklist 移除。

        取消后台 asyncio 任务：_run_background_subagent 的 CancelledError
        分支会把结果写为「已被取消」（_complete_subagent_task），随后
        记录被移除（后台任务已终止，无需再被对话轮次处理）。
        """
        task = rec.get("task")
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=2.0)
            except Exception:
                pass  # 任务取消过程异常忽略

        # 移除任务记录并更新 TUI 计数
        if hasattr(agent, "_remove_subagent_task"):
            agent._remove_subagent_task(self.task_id)
        else:
            agent._subagent_tasks.pop(self.task_id, None)
        return f"(已取消后台 subagent 任务 {self.task_id})"
