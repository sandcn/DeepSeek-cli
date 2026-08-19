"""Agent 基类 — 纯消息管理

提供公共的消息追加和沙盒同步方法。
保持核心逻辑简单，工具调用等复杂行为由 Agent 子类实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from .sandbox_manager import get_sandbox_manager, set_current_message_index

_logger = logging.getLogger(__name__)

# ── 后台 bash 任务自动等待超时（防无限卡死） ──────────────
# 前台 bash 超过 _AUTO_BG_TIMEOUT 秒会自动转后台（命令不终止），
# 转后台后任务可能长期运行（长时/交互式命令）。_process_background_tasks
# 在等待这类任务时必须有一个有界上限，否则任一长时 bash 任务会让
# Agent/SubAgent 无限阻塞——用户侧现象：多个 SubAgent 并发执行时，
# 只要一个 SubAgent 的 bash 命令长时间不退出，整个并行执行永久卡死。
# 超时后未完成任务被标记为 bash_opt 管理（模型已拿到 task_id），
# 由模型经 bash_opt 工具主动 wait/kill 管理，不再自动阻塞对话。
_BACKGROUND_WAIT_TIMEOUT: float = 120.0


def _parse_bash_result_fields(result: str) -> tuple[str, str, "int | None"]:
    """解析 bash 工具的三元 JSON 结果为 (stdout, stderr, returncode) 三元组。

    bash 工具返回给大模型的结构为 ``{"stdout", "stderr", "returncode"}``；
    后台任务完成时该 JSON 字符串存入任务记录 ``result`` 字段。本函数供
    ``_complete_background_task``（写入时展开为独立字段）与消费侧
    （bash_opt wait / _collect_done_background_messages）解析复用。

    回退语义：result 不是合法三元 JSON（旧格式纯文本、空串、异常路径
    文本）时返回 ``(result, "", None)``——原文进 stdout、无退出码。

    Args:
        result: bash 工具结果字符串（三元 JSON 或任意文本）。

    Returns:
        (stdout, stderr, returncode) 三元组；returncode 解析失败时为 None。
    """
    if not result:
        return ("", "", None)
    try:
        data = json.loads(result)
    except (json.JSONDecodeError, TypeError):
        return (result, "", None)
    if isinstance(data, dict) and "returncode" in data:
        rc = data.get("returncode")
        # 校验 returncode 为 int（bash 返回字符串 "0" 等异常形态时置 None，
        # 保持下游 json.dumps 输出的 returncode 类型与 int 约定一致）
        if not isinstance(rc, int):
            rc = None
        return (
            str(data.get("stdout", "") or ""),
            str(data.get("stderr", "") or ""),
            rc,
        )
    return (result, "", None)


def _serialize_tool_arguments(arguments: Any) -> str:
    """安全序列化 tool_call arguments 为 JSON 字符串。

    处理 None/str/dict 三种输入类型，异常时回退为错误描述 JSON。
    """
    if arguments is None:
        return ""
    if isinstance(arguments, str):
        return arguments
    try:
        return json.dumps(arguments, ensure_ascii=False)
    except (TypeError, ValueError) as e:
        _logger.warning("工具参数序列化失败: %s", e)
        return json.dumps({"error": "序列化失败", "raw": str(arguments)[:500]}, ensure_ascii=False)


def _build_tool_calls_payload(tool_calls: list[dict]) -> list[dict]:
    """将原始 tool_calls 转换为 API 兼容格式。

    为每个 tool_call 添加 type="function" 和嵌套的 function 结构。
    """
    processed = []
    for tc in tool_calls:
        processed.append({
            "id": tc.get("id", ""),
            "type": "function",
            "function": {
                "name": tc.get("name", ""),
                "arguments": _serialize_tool_arguments(tc.get("arguments")),
            },
        })
    return processed


# 非ABC：为 Agent/SubAgent 提供共享消息操作
class BaseAgent:
    """消息管理基类（非ABC）——为 Agent/SubAgent 提供 add_user_message/_append_tool_result 等共享的消息操作。注意：这不是抽象基类，不含抽象方法。"""

    def __init__(self):
        self.messages: list[dict] = []
        self.model: str | None = None
        self.tools: list[dict] = []
        # ── 后台任务列表（task_id → 任务记录） ──
        # ★ 两类后台任务使用**独立**列表（2026-08-18）：
        #   - _background_tasks：bash 后台任务（bash background=True /
        #     自动转后台），task_id 恒为 "bg-xxx"，主 Agent 与 SubAgent
        #     都持有（SubAgent 内可跑 bash 后台任务）
        #   - _subagent_tasks：subagent 后台任务（subagent 直接后台执行），
        #     task_id 恒为 "sa-xxx"，**仅主 Agent（Agent 类）独有**——
        #     后台 subagent 仅主 Agent 可派发（白名单排除 + 运行时校验），
        #     SubAgent 不持有该表，在 Agent.__init__ 中显式初始化
        # 两列表互不共享：bash_opt 只操作 _background_tasks，subagent_opt
        # 只操作 _subagent_tasks——误传对方 task_id 时天然查不到记录，从
        # 结构上杜绝跨类型误操作（工具内部另有 task_id 前缀校验双保险）。
        self._background_tasks: dict[str, dict] = {}

    # ── 沙盒索引同步 ──────────────────────────────────

    def _sync_sandbox_index(self, msg_index: int | None = None) -> None:
        """同步沙盒管理器的消息索引到指定消息位置。

        SubAgent 设置 _skip_sandbox_update=True 阻止此更新，
        避免多个并发 SubAgent 的 thread local 互相覆盖。

        Args:
            msg_index: 目标消息索引。为 None 时取当前消息列表末尾。
        """
        if getattr(self, '_skip_sandbox_update', False):
            return
        sandbox_manager = get_sandbox_manager()
        if not sandbox_manager:
            return
        idx = msg_index if msg_index is not None else (len(self.messages) - 1 if self.messages else 0)
        sandbox_manager.update_message_index(idx)
        set_current_message_index(idx)

    # ── 消息管理 ──────────────────────────────────────

    def _refresh_context_usage(self) -> None:
        """消息变更后刷新上下文使用率全局快照（TUI 模式行行首动态刷新）。

        ContextManager.refresh_usage() 为 O(len(messages)) 懒同步（长度变化
        才全量 resync，否则复用缓存）——消息追加属低频路径，TUI 渲染线程
        读取侧仍每帧 O(1)。SubAgent 无 context_manager（getattr 防御跳过）。
        """
        cm = getattr(self, "context_manager", None)
        if cm is None:
            return
        try:
            cm.refresh_usage()
        except Exception:
            _logger.debug("消息变更后刷新上下文使用率失败", exc_info=True)

    def add_user_message(self, content: str | None) -> None:
        """添加用户消息到消息列表。"""
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        self.messages.append({"role": "user", "content": content})
        self._refresh_context_usage()

    def _append_assistant_message(
        self,
        content: str | None,
        tool_calls: list[dict] | None = None,
        reasoning_content: str | None = None,
    ) -> None:
        """追加 assistant 消息。

        处理 DeepSeek thinking mode 的强制约束：
        - tool_calls 存在时 content 必须为 None
        - reasoning_content key 始终存在（即使为空字符串）
        """
        msg: dict[str, Any] = {"role": "assistant"}

        # content: tool_calls 时置 None，否则保底空字符串
        msg["content"] = None if tool_calls else (content or "")

        # reasoning_content: DeepSeek 要求 key 始终存在
        if isinstance(reasoning_content, str):
            msg["reasoning_content"] = reasoning_content
        else:
            # 非 str（None/异常类型）：记录日志并回退空字符串（保持 key 存在）
            if reasoning_content is not None:
                _logger.debug(
                    "reasoning_content 类型异常 (%s)，回退为空",
                    type(reasoning_content).__name__,
                )
            msg["reasoning_content"] = ""

        if tool_calls:
            msg["tool_calls"] = _build_tool_calls_payload(tool_calls)

        self.messages.append(msg)
        self._sync_sandbox_index(len(self.messages) - 1)
        self._refresh_context_usage()

    def _append_tool_result(self, tool_call_id: str, content) -> None:
        """追加 tool 角色消息。

        content 支持三种类型：
        - str：纯文本结果（原有行为）。
        - ToolResult：结构化结果（工具设置了 result_blocks 时由执行链路
          包装）——有 blocks 时 content 为 OpenAI 兼容 content blocks list
          （多模态模型可见图片），无 blocks 时退化为 text。
        - list[dict]：直接作为 content blocks（多模态，OpenAI 兼容格式，
          如 [{"type": "image_url", "image_url": {...}}]）。
        """
        from ..tools.base import ToolResult
        if isinstance(content, ToolResult):
            content = content.to_content()
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._sync_sandbox_index(len(self.messages) - 1)
        self._refresh_context_usage()

    # ═══════════════════════════════════════════════════════════
    # 后台任务管理（bash / subagent 各自独立）
    # ═══════════════════════════════════════════════════════════
    # 设计：
    # - bash 后台任务注册到 self._background_tasks（bash_opt 专用表）
    # - subagent 后台任务注册到 self._subagent_tasks（subagent_opt 专用表）
    # - 两表完全独立，各配一套 *_background_task / *_subagent_task 方法；
    #   一轮对话完成后由 _process_background_tasks()（bash 表）与
    #   _process_subagent_tasks()（subagent 表）分别检查处理：
    #   ① 有已完成的后台任务 → 收集结果（JSON：task_id + 命令输出按
    #      stdout/stderr/returncode 三元展开）作为用户消息插入对话，
    #      返回 True（调用方应继续一轮对话让模型处理）
    #   ② 无已完成但仍有运行中 → 等待全部完成 → 插入结果消息 → 返回 True
    #   ③ 无任何后台任务 → 返回 False（对话可正常结束）

    def _register_background_task(self, task_id: str, record: dict) -> None:
        """注册 bash 后台任务记录到 bash 任务表（_background_tasks）。

        Args:
            task_id: 后台 bash 任务 ID（如 bg-xxxx）
            record: 任务记录 dict，至少包含 task/command/done 等键
        """
        if not hasattr(self, "_background_tasks"):
            self._background_tasks = {}
        self._background_tasks[task_id] = record
        # TUI 模式行行首统计（bash · N · subagent · N；bash/subagent 分列）
        self._publish_background_task_event()

    def _register_subagent_task(self, task_id: str, record: dict) -> None:
        """注册 subagent 后台任务记录到 subagent 任务表（_subagent_tasks）。

        subagent 后台任务与 bash 后台任务分表独立管理：subagent_opt 只操作
        本表，与 bash_opt 的 _background_tasks 互不干扰。

        Args:
            task_id: 后台 subagent 任务 ID（如 sa-xxxx）
            record: 任务记录 dict，至少包含 task/description/done 等键
        """
        if not hasattr(self, "_subagent_tasks"):
            self._subagent_tasks = {}
        self._subagent_tasks[task_id] = record
        # TUI 模式行行首统计（bash · N · subagent · N；bash/subagent 分列）
        self._publish_background_task_event()

    def _complete_background_task(self, task_id: str, result: str,
                                  status: str = "completed") -> None:
        """标记 bash 后台任务完成并写入命令输出（三元 JSON 结果）。

        result 为 bash 工具的三元 JSON 字符串（{"stdout", "stderr",
        "returncode"}）；写入时同步解析展开为任务记录的独立字段
        stdout / stderr / returncode，供 bash_opt wait 与
        _collect_done_background_messages 直接读取（无需二次解析）。
        解析失败（旧格式纯文本）时 stdout 取原文、returncode 为 None。

        Args:
            task_id: 后台 bash 任务 ID
            result: 命令结果（三元 JSON 字符串）
            status: 完成状态（默认 completed）
        """
        if not hasattr(self, "_background_tasks"):
            return
        record = self._background_tasks.get(task_id)
        if record is not None:
            record["result"] = result
            record["stdout"], record["stderr"], record["returncode"] = (
                _parse_bash_result_fields(result)
            )
            record["status"] = status
            record["done"] = True
        # TUI 模式行行首统计（bash · N · subagent · N；bash/subagent 分列）
        self._publish_background_task_event()

    def _complete_subagent_task(self, task_id: str, result: str,
                                status: str = "completed") -> None:
        """标记 subagent 后台任务完成并写入结果。

        Args:
            task_id: 后台 subagent 任务 ID
            result: 任务结果文本
            status: 完成状态（默认 completed）
        """
        if not hasattr(self, "_subagent_tasks"):
            return
        record = self._subagent_tasks.get(task_id)
        if record is not None:
            record["result"] = result
            record["status"] = status
            record["done"] = True
        # TUI 模式行行首统计（bash · N · subagent · N；bash/subagent 分列）
        self._publish_background_task_event()

    def _pending_background_tasks(self) -> list[dict]:
        """返回所有未完成的 bash 后台任务记录列表。

        ★ 被 bash_opt 工具管理的任务（managed_by_tool=True）不在此列：
        其生命周期由大模型通过 bash_opt 工具主动控制（read/wait/kill/stdin/keys），
        不需要对话轮次自动等待其完成（交互式任务可能长期运行）。
        """
        if not hasattr(self, "_background_tasks"):
            return []
        return [
            r for r in self._background_tasks.values()
            if not r.get("done") and not r.get("managed_by_tool")
        ]

    def _pending_subagent_tasks(self) -> list[dict]:
        """返回所有未完成的 subagent 后台任务记录列表。

        ★ 被 subagent_opt 工具管理的任务（managed_by_tool=True）不在此列：
        其生命周期由大模型通过 subagent_opt 工具主动控制（read/wait/kill），
        不需要对话轮次自动等待其完成。
        """
        if not hasattr(self, "_subagent_tasks"):
            return []
        return [
            r for r in self._subagent_tasks.values()
            if not r.get("done") and not r.get("managed_by_tool")
        ]

    def _get_background_task(self, task_id: str) -> dict | None:
        """按 task_id 获取 bash 后台任务记录（bash_opt 工具使用）。"""
        if not hasattr(self, "_background_tasks"):
            return None
        return self._background_tasks.get(task_id)

    def _get_subagent_task(self, task_id: str) -> dict | None:
        """按 task_id 获取 subagent 后台任务记录（subagent_opt 工具使用）。"""
        if not hasattr(self, "_subagent_tasks"):
            return None
        return self._subagent_tasks.get(task_id)

    def _remove_background_task(self, task_id: str) -> dict | None:
        """移除并返回指定 bash 后台任务记录（bash_opt 工具使用）。

        任务被 bash_opt 工具主动消费（wait 拿到输出 / kill 终止）时，
        从 bash 任务表移除，避免 _process_background_tasks 再次把结果
        作为用户消息重复插入对话。
        """
        if not hasattr(self, "_background_tasks"):
            return None
        rec = self._background_tasks.pop(task_id, None)
        if rec is not None:
            self._publish_background_task_event()
        return rec

    def _remove_subagent_task(self, task_id: str) -> dict | None:
        """移除并返回指定 subagent 后台任务记录（subagent_opt 工具使用）。

        任务被 subagent_opt 工具主动消费（wait 拿到结果 / kill 取消）时，
        从 subagent 任务表移除，避免 _process_subagent_tasks 再次把结果
        作为用户消息重复插入对话。
        """
        if not hasattr(self, "_subagent_tasks"):
            return None
        rec = self._subagent_tasks.pop(task_id, None)
        if rec is not None:
            self._publish_background_task_event()
        return rec

    def _count_running_bash_tasks(self) -> int:
        """返回当前运行中（未完成）的后台 bash 任务总数。

        TUI 模式行行首显示用（bash · N）：仅统计 bash 任务表
        （_background_tasks，task_id 恒为 "bg-xxx"，主 Agent 与 SubAgent
        各自独立持有）。
        """
        table = getattr(self, "_background_tasks", None)
        if not table:
            return 0
        return sum(1 for r in table.values() if not r.get("done"))

    def _count_running_subagent_tasks(self) -> int:
        """返回当前运行中（未完成）的后台 subagent 任务总数。

        TUI 模式行行首显示用（subagent · N）：仅统计 subagent 任务表
        （_subagent_tasks，task_id 恒为 "sa-xxx"，仅主 Agent 独有）。
        """
        table = getattr(self, "_subagent_tasks", None)
        if not table:
            return 0
        return sum(1 for r in table.values() if not r.get("done"))

    def _publish_background_task_event(self) -> None:
        """发布后台任务数量变更事件（TUI 模式行行首统计用）。

        主 Agent 发布 label="main"，SubAgent 发布自身 label（agent-N）；
        事件携带该 agent 当前运行中的后台 bash 与 subagent 任务数，TUI
        分别聚合所有 label 显示（bash · N · subagent · N）。
        """
        try:
            from ..tui.events.event_types import BackgroundTaskChangedEvent
            port = getattr(self, "_event_port", None)
            if port is None or not hasattr(port, "publish_event"):
                return
            label = getattr(self, "label", None) or "main"
            bash_count = self._count_running_bash_tasks()
            subagent_count = self._count_running_subagent_tasks()
            port.publish_event(BackgroundTaskChangedEvent(
                label=label, count=bash_count,
                subagent_count=subagent_count, source="agent",
            ))
        except Exception:
            _logger.debug("发布后台任务数量事件失败", exc_info=True)

    def _collect_done_background_messages(self) -> list[str]:
        """收集所有已完成 **bash** 后台任务的结果为 JSON 用户消息，并从 bash 表移除。

        每条消息格式：{"task_id": "...", "command": "...", "status": "...",
        "stdout": "...", "stderr": "...", "returncode": N}——命令输出按
        bash 三元 JSON 结构展开（stdout/stderr/returncode 分离）。
        _complete_background_task 已把三字段写入任务记录，这里直接读取；
        记录缺失字段（异常路径）时回退解析 result 原文。

        ★ 被 bash_opt 工具管理的任务（managed_by_tool=True）只清理、不生成消息：
        其结果已由大模型通过 bash_opt wait 主动获取（或由 stdin/keys 交互管理），
        不再重复插入用户消息。

        仅操作 self._background_tasks（bash 表）；subagent 表由
        _collect_done_subagent_messages 独立处理。
        """
        if not hasattr(self, "_background_tasks"):
            return []
        messages: list[str] = []
        done_ids: list[str] = []
        for task_id, record in self._background_tasks.items():
            if record.get("done"):
                if not record.get("managed_by_tool"):
                    if "stdout" in record or "returncode" in record:
                        stdout = record.get("stdout", "")
                        stderr = record.get("stderr", "")
                        returncode = record.get("returncode")
                    else:
                        stdout, stderr, returncode = _parse_bash_result_fields(
                            record.get("result", ""))
                    payload = {
                        "task_id": task_id,
                        "command": record.get("command", ""),
                        "status": record.get("status", "completed"),
                        "stdout": stdout,
                        "stderr": stderr,
                        "returncode": returncode,
                    }
                    messages.append(json.dumps(payload, ensure_ascii=False))
                done_ids.append(task_id)
        for task_id in done_ids:
            self._background_tasks.pop(task_id, None)
        if done_ids:
            # 任务移除后发布最新计数（含 subagent 聚合）
            self._publish_background_task_event()
        return messages

    def _collect_done_subagent_messages(self) -> list[str]:
        """收集所有已完成 **subagent** 后台任务的结果为 JSON 用户消息，并从 subagent 表移除。

        每条消息格式：{"task_id": "...", "command": "...", "status": "...", "output": "..."}
        subagent 记录与 bash 记录共用 command 字段（"subagent(描述)"），
        插入的用户消息同样为 JSON 格式（含 taskid 与结果）。

        ★ 被 subagent_opt 工具管理的任务（managed_by_tool=True）只清理、不生成消息：
        其结果已由大模型通过 subagent_opt wait 主动获取，不再重复插入用户消息。

        仅操作 self._subagent_tasks（subagent 表）；bash 表由
        _collect_done_background_messages 独立处理。
        """
        if not hasattr(self, "_subagent_tasks"):
            return []
        messages: list[str] = []
        done_ids: list[str] = []
        for task_id, record in self._subagent_tasks.items():
            if record.get("done"):
                if not record.get("managed_by_tool"):
                    payload = {
                        "task_id": task_id,
                        "command": record.get("command", ""),
                        "status": record.get("status", "completed"),
                        "output": record.get("result", ""),
                    }
                    messages.append(json.dumps(payload, ensure_ascii=False))
                done_ids.append(task_id)
        for task_id in done_ids:
            self._subagent_tasks.pop(task_id, None)
        if done_ids:
            # 任务移除后发布最新计数（含 bash 聚合）
            self._publish_background_task_event()
        return messages

    def _append_background_result_messages(self, messages: list[str]) -> None:
        """将后台任务结果消息插入对话（user 角色），并同步沙盒/上下文缓存。

        Args:
            messages: 后台任务结果 JSON 消息列表
        """
        for content in messages:
            self.add_user_message(content)
        self._sync_sandbox_index()
        # 主 Agent 有 context_manager，插入消息后使缓存失效保持一致
        cm = getattr(self, "context_manager", None)
        if cm is not None:
            try:
                cm.invalidate_cache()
            except Exception:
                _logger.debug("后台任务消息插入后 invalidate_cache 失败", exc_info=True)

    async def _wait_background_tasks(self, tasks: list, timeout: float | None = None) -> set:
        """等待所有后台任务完成，带超时上限（防无限卡死）。

        返回仍未完成的任务集合（空集合表示全部完成，或被中断取消）。
        超时后未完成的任务由调用方处理（标记 managed_by_tool 交 bash_opt
        工具管理），避免长时/挂起的 bash 后台任务（如自动转后台后命令
        永不退出）让 Agent/SubAgent 无限阻塞。

        Args:
            tasks: asyncio.Task 列表
            timeout: 最长等待秒数。None 使用 _BACKGROUND_WAIT_TIMEOUT。

        Returns:
            仍未完成（超时）的任务集合；全部完成或中断取消时返回空集合。
        """
        if not tasks:
            return set()
        if timeout is None:
            timeout = _BACKGROUND_WAIT_TIMEOUT
        pending = set(tasks)
        deadline = time.monotonic() + timeout
        while pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break  # 超时：返回仍未完成的任务集合
            _, pending = await asyncio.wait(pending, timeout=min(0.2, remaining))
            if not pending:
                break
            # 等待期间检查中断信号：用户按 ESC 时取消剩余后台任务
            try:
                port = getattr(self, "_interrupt_port", None)
                if port is not None and await port.is_interrupted():
                    for t in pending:
                        t.cancel()
                    # ★ 取消等待带超时（P1）：被取消的后台 bash 任务可能卡在
                    #   _run_pty/_run_pipe 的 process.wait()（子进程不可杀），
                    #   无界等待会让 Agent/SubAgent 永久阻塞（卡死）。进程树
                    #   已由 _run_pty 的 CancelledError 分支 kill，超时后放弃。
                    try:
                        await asyncio.wait_for(
                            asyncio.gather(*pending, return_exceptions=True),
                            timeout=5.0,
                        )
                    except asyncio.TimeoutError:
                        _logger.warning(
                            "后台任务取消等待超时（%d 个任务），放弃等待",
                            len(pending),
                        )
                    return set()
            except Exception:
                _logger.debug("后台任务等待中断检查失败", exc_info=True)
        return pending

    async def _process_background_tasks(self) -> bool:
        """一轮对话完成后处理 **bash** 后台任务（主 Agent 与 SubAgent 共用）。

        仅处理 self._background_tasks（bash 表）；返回 True 表示已把 bash
        后台任务结果插入用户消息，需要继续一轮对话；返回 False 表示 bash
        表无后台任务可处理。

        防卡死：等待运行中后台任务完成带 _BACKGROUND_WAIT_TIMEOUT 超时。
        超时后仍在运行的任务被标记为 ``managed_by_tool=True``（不再自动等待），
        并插入「仍在运行」用户消息——模型已在前台 bash 工具返回中拿到
        task_id，可经 bash_opt 工具继续 wait/kill 管理。

        注（review P3）：与 _process_subagent_tasks 高度同构（同一处理模板，
        仅表名/字段不同）——刻意保留两份以独立演进两表语义（bash 三元结果
        展开 vs subagent 输出），重构提取公共模板收益低且引入回归风险。
        """
        if not hasattr(self, "_background_tasks") or not self._background_tasks:
            return False

        # ① 有已完成的后台任务 → 收集结果插入用户消息
        done_msgs = self._collect_done_background_messages()
        if done_msgs:
            self._append_background_result_messages(done_msgs)
            return True

        # ② 无已完成，但有运行中的后台任务 → 等待全部完成后处理（带超时）
        pending = self._pending_background_tasks()
        if pending:
            tasks = [
                r.get("task") for r in pending
                if r.get("task") is not None and not r["task"].done()
            ]
            unfinished: set = set()
            if tasks:
                unfinished = await self._wait_background_tasks(tasks)
            done_msgs = self._collect_done_background_messages()
            if done_msgs:
                self._append_background_result_messages(done_msgs)
                return True
            if unfinished:
                # ★ 超时未完成（长时/挂起后台任务）：
                #   1. 标记 managed_by_tool——后续不再自动等待其完成
                #      （模型已拿到 task_id，可经 bash_opt 继续管理）；
                #   2. 插入「仍在运行」用户消息，让模型知道任务未结束，
                #      可选择继续管理或结束对话（不再无限阻塞）。
                running_msgs: list[str] = []
                for task in unfinished:
                    # 快照遍历（P3 防御）：当前循环内无 await 不会结构性修改，
                    # 但快照可防未来加入 await 时 bash_opt/完成回调并发 pop。
                    # 注（review P3）：unfinished × 任务表双重遍历为 O(N²)——
                    # 未完成任务数量极小（≤ 少数后台 bash），开销可忽略，
                    # 换取与任务表强一致的精确匹配。
                    for task_id, rec in list(self._background_tasks.items()):
                        if rec.get("task") is task:
                            if not rec.get("managed_by_tool"):
                                rec["managed_by_tool"] = True
                            # 任务仍在运行：result 尚无最终输出（空串），
                            # 三元字段占位，完整结果由 bash_opt wait 获取
                            running_msgs.append(json.dumps({
                                "task_id": task_id,
                                "command": rec.get("command", ""),
                                "status": "running",
                                "stdout": rec.get("stdout", ""),
                                "stderr": rec.get("stderr", ""),
                                "returncode": rec.get("returncode"),
                            }, ensure_ascii=False))
                            break
                if running_msgs:
                    self._append_background_result_messages(running_msgs)
                    self._publish_background_task_event()
                    return True
                # 防御：running_msgs 为空（极端时序下 unfinished 任务已被
                # bash_opt 移除或刚完成）→ 仅移除这些任务的残留记录，
                # 不清空全表（避免误删其他仍在管理的任务记录）。
                removed_any = False
                for task in unfinished:
                    for task_id, rec in list(self._background_tasks.items()):
                        if rec.get("task") is task:
                            del self._background_tasks[task_id]
                            removed_any = True
                            break
                if removed_any:
                    self._publish_background_task_event()
            else:
                # unfinished 为空（tasks 全为 None/done、或中断取消后已收集）：
                # 仅清理「非 managed 且 task 缺失或已结束」的残留记录，
                # 保留 managed_by_tool 任务（模型可经 bash_opt 继续管理，
                # 全表 clear 会使其失联——P2 修复）。
                #
                # ★ P1 修复（2026-08-08）：stale 判定必须同时满足
                #   ``task.done()`` 与 ``rec.get("done")``——自动转后台任务
                #   （_promote_to_background）的 done 标志由 exec_task 的
                #   _on_done 完成回调写入（call_soon 排队），存在「task 已完成
                #   但回调未执行」的极窄窗口；若此时仅凭 task.done() 判 stale
                #   删除记录，回调随后 _complete_background_task 时 record 为
                #   None → 结果静默丢弃。保留该记录一个循环，待回调写入后由
                #   分支 ① _collect_done_background_messages 正常消费。
                stale_ids: list[str] = []
                for tid, rec in self._background_tasks.items():
                    if rec.get("managed_by_tool"):
                        continue
                    task = rec.get("task")
                    if task is None or (task.done() and rec.get("done")):
                        stale_ids.append(tid)
                for tid in stale_ids:
                    del self._background_tasks[tid]
                if stale_ids:
                    self._publish_background_task_event()

        return False

    async def _process_subagent_tasks(self) -> bool:
        """一轮对话完成后处理 **subagent** 后台任务（仅主 Agent 需要）。

        仅处理 self._subagent_tasks（subagent 表）；返回 True 表示已把
        subagent 后台任务结果插入用户消息，需要继续一轮对话；返回 False
        表示 subagent 表无后台任务可处理。

        与 _process_background_tasks（bash 表）完全独立：本方法不触碰
        _background_tasks，只消费 subagent 后台任务（task_id 恒为 "sa-xxx"，
        由 subagent 工具直接后台派发注册）。

        防卡死：等待运行中后台任务完成带 _BACKGROUND_WAIT_TIMEOUT 超时。
        超时后仍在运行的任务被标记为 ``managed_by_tool=True``（不再自动等待），
        并插入「仍在运行」用户消息——模型已在 subagent 工具返回中拿到
        task_id，可经 subagent_opt 工具继续 read/wait/kill 管理。
        """
        if not hasattr(self, "_subagent_tasks") or not self._subagent_tasks:
            return False

        # ① 有已完成的后台任务 → 收集结果插入用户消息
        done_msgs = self._collect_done_subagent_messages()
        if done_msgs:
            self._append_background_result_messages(done_msgs)
            return True

        # ② 无已完成，但有运行中的后台任务 → 等待全部完成后处理（带超时）
        pending = self._pending_subagent_tasks()
        if pending:
            tasks = [
                r.get("task") for r in pending
                if r.get("task") is not None and not r["task"].done()
            ]
            unfinished: set = set()
            if tasks:
                unfinished = await self._wait_background_tasks(tasks)
            done_msgs = self._collect_done_subagent_messages()
            if done_msgs:
                self._append_background_result_messages(done_msgs)
                return True
            if unfinished:
                # ★ 超时未完成（长时/挂起后台 subagent）：
                #   1. 标记 managed_by_tool——后续不再自动等待其完成
                #      （模型已拿到 task_id，可经 subagent_opt 继续管理）；
                #   2. 插入「仍在运行」用户消息，让模型知道任务未结束，
                #      可选择继续管理或结束对话（不再无限阻塞）。
                running_msgs: list[str] = []
                for task in unfinished:
                    # 快照遍历（P3 防御）：当前循环内无 await 不会结构性修改，
                    # 但快照可防未来加入 await 时 subagent_opt/完成回调并发 pop。
                    for task_id, rec in list(self._subagent_tasks.items()):
                        if rec.get("task") is task:
                            if not rec.get("managed_by_tool"):
                                rec["managed_by_tool"] = True
                            running_msgs.append(json.dumps({
                                "task_id": task_id,
                                "command": rec.get("command", ""),
                                "status": "running",
                                "output": rec.get("result", ""),
                            }, ensure_ascii=False))
                            break
                if running_msgs:
                    self._append_background_result_messages(running_msgs)
                    self._publish_background_task_event()
                    return True
                # 防御：running_msgs 为空（极端时序下 unfinished 任务已被
                # subagent_opt 移除或刚完成）→ 仅移除这些任务的残留记录，
                # 不清空全表（避免误删其他仍在管理的任务记录）。
                removed_any = False
                for task in unfinished:
                    for task_id, rec in list(self._subagent_tasks.items()):
                        if rec.get("task") is task:
                            del self._subagent_tasks[task_id]
                            removed_any = True
                            break
                if removed_any:
                    self._publish_background_task_event()
            else:
                # unfinished 为空（tasks 全为 None/done、或中断取消后已收集）：
                # 仅清理「非 managed 且 task 缺失或已结束」的残留记录，
                # 保留 managed_by_tool 任务（模型可经 subagent_opt 继续管理，
                # 全表 clear 会使其失联——P2 修复）。
                stale_ids: list[str] = []
                for tid, rec in self._subagent_tasks.items():
                    if rec.get("managed_by_tool"):
                        continue
                    task = rec.get("task")
                    if task is None or (task.done() and rec.get("done")):
                        stale_ids.append(tid)
                for tid in stale_ids:
                    del self._subagent_tasks[tid]
                if stale_ids:
                    self._publish_background_task_event()

        return False
