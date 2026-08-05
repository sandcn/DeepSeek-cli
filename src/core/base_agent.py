"""Agent 基类 — 纯消息管理

提供公共的消息追加和沙盒同步方法。
保持核心逻辑简单，工具调用等复杂行为由 Agent 子类实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from .sandbox_manager import get_sandbox_manager, set_current_message_index

_logger = logging.getLogger(__name__)


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
        # bash 工具后台模式（background=True）把任务记录注册到这里，
        # 一轮对话完成后由 _process_background_tasks() 检查并处理。
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

    def add_user_message(self, content: str | None) -> None:
        """添加用户消息到消息列表。"""
        if content is None:
            content = ""
        elif not isinstance(content, str):
            content = str(content)
        self.messages.append({"role": "user", "content": content})

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
        msg["reasoning_content"] = (
            reasoning_content if isinstance(reasoning_content, str)
            else _logger.debug("reasoning_content 类型异常 (%s)，回退为空", type(reasoning_content).__name__) or ""
        )

        if tool_calls:
            msg["tool_calls"] = _build_tool_calls_payload(tool_calls)

        self.messages.append(msg)
        self._sync_sandbox_index(len(self.messages) - 1)

    def _append_tool_result(self, tool_call_id: str, content: str) -> None:
        """追加 tool 角色消息。"""
        self.messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": content,
        })
        self._sync_sandbox_index(len(self.messages) - 1)

    # ═══════════════════════════════════════════════════════════
    # 后台任务（bash background=True）管理
    # ═══════════════════════════════════════════════════════════
    # 设计：
    # - bash 工具后台模式把任务记录注册到 self._background_tasks（tasklist）
    # - 一轮对话完成后由 _process_background_tasks() 检查：
    #   ① 有已完成的后台任务 → 收集结果（JSON：task_id + 命令输出）作为
    #      用户消息插入对话，返回 True（调用方应继续一轮对话让模型处理）
    #   ② 无已完成但仍有运行中 → 等待全部完成 → 插入结果消息 → 返回 True
    #   ③ 无任何后台任务 → 返回 False（对话可正常结束）

    def _register_background_task(self, task_id: str, record: dict) -> None:
        """注册后台任务记录到 tasklist。

        Args:
            task_id: 后台任务 ID（如 bg-xxxx）
            record: 任务记录 dict，至少包含 task/command/done 等键
        """
        if not hasattr(self, "_background_tasks"):
            self._background_tasks = {}
        self._background_tasks[task_id] = record
        # TUI 状态栏右下角统计（含 subagent 聚合）
        self._publish_background_task_event()

    def _complete_background_task(self, task_id: str, result: str,
                                  status: str = "completed") -> None:
        """标记后台任务完成并写入命令输出。

        Args:
            task_id: 后台任务 ID
            result: 命令输出（stdout+stderr 合并，已截断）
            status: 完成状态（默认 completed）
        """
        if not hasattr(self, "_background_tasks"):
            return
        record = self._background_tasks.get(task_id)
        if record is not None:
            record["result"] = result
            record["status"] = status
            record["done"] = True
        # TUI 状态栏右下角统计（含 subagent 聚合）
        self._publish_background_task_event()

    def _pending_background_tasks(self) -> list[dict]:
        """返回所有未完成的后台任务记录列表。

        ★ 被 bash_task 工具管理的任务（managed_by_tool=True）不在此列：
        其生命周期由大模型通过 bash_task 工具主动控制（wait/kill/stdin/keys），
        不需要对话轮次自动等待其完成（交互式任务可能长期运行）。
        """
        if not hasattr(self, "_background_tasks"):
            return []
        return [
            r for r in self._background_tasks.values()
            if not r.get("done") and not r.get("managed_by_tool")
        ]

    def _get_background_task(self, task_id: str) -> dict | None:
        """按 task_id 获取后台任务记录（bash_task 工具使用）。"""
        if not hasattr(self, "_background_tasks"):
            return None
        return self._background_tasks.get(task_id)

    def _remove_background_task(self, task_id: str) -> dict | None:
        """移除并返回指定后台任务记录（bash_task 工具使用）。

        任务被 bash_task 工具主动消费（wait 拿到输出 / kill 终止）时，
        从 tasklist 移除，避免 _process_background_tasks 再次把结果
        作为用户消息重复插入对话。
        """
        if not hasattr(self, "_background_tasks"):
            return None
        rec = self._background_tasks.pop(task_id, None)
        if rec is not None:
            self._publish_background_task_event()
        return rec

    def _count_running_background_tasks(self) -> int:
        """返回当前运行中（未完成）的后台 bash 任务数量。"""
        if not hasattr(self, "_background_tasks"):
            return 0
        return sum(1 for r in self._background_tasks.values() if not r.get("done"))

    def _publish_background_task_event(self) -> None:
        """发布后台任务数量变更事件（TUI 状态栏统计用）。

        主 Agent 发布 label="main"，SubAgent 发布自身 label（agent-N）；
        事件携带该 agent 当前运行中的后台任务数，TUI 聚合所有 label 显示总数。
        """
        try:
            from ..tui.events.event_types import BackgroundTaskChangedEvent
            port = getattr(self, "_event_port", None)
            if port is None or not hasattr(port, "publish_event"):
                return
            label = getattr(self, "label", None) or "main"
            count = self._count_running_background_tasks()
            port.publish_event(BackgroundTaskChangedEvent(
                label=label, count=count, source="agent",
            ))
        except Exception:
            _logger.debug("发布后台任务数量事件失败", exc_info=True)

    def _collect_done_background_messages(self) -> list[str]:
        """收集所有已完成后台任务的结果为 JSON 用户消息，并从 tasklist 移除。

        每条消息格式：{"task_id": "...", "command": "...", "status": "...", "output": "..."}
        满足需求：插入的用户消息为 JSON 格式，含 taskid 和命令输出。

        ★ 被 bash_task 工具管理的任务（managed_by_tool=True）只清理、不生成消息：
        其结果已由大模型通过 bash_task wait 主动获取（或由 stdin/keys 交互管理），
        不再重复插入用户消息。
        """
        if not hasattr(self, "_background_tasks"):
            return []
        messages: list[str] = []
        done_ids: list[str] = []
        for task_id, record in self._background_tasks.items():
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
            self._background_tasks.pop(task_id, None)
        if done_ids:
            # 任务移除后发布最新计数（含 subagent 聚合）
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

    async def _wait_background_tasks(self, tasks: list) -> None:
        """等待所有后台任务完成，期间响应中断信号（中断时取消剩余任务）。

        Args:
            tasks: asyncio.Task 列表
        """
        if not tasks:
            return
        pending = set(tasks)
        while pending:
            _, pending = await asyncio.wait(pending, timeout=0.2)
            if not pending:
                break
            # 等待期间检查中断信号：用户按 ESC 时取消剩余后台任务
            try:
                port = getattr(self, "_interrupt_port", None)
                if port is not None and await port.is_interrupted():
                    for t in pending:
                        t.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    break
            except Exception:
                _logger.debug("后台任务等待中断检查失败", exc_info=True)

    async def _process_background_tasks(self) -> bool:
        """一轮对话完成后处理后台任务（主 Agent 与 SubAgent 共用）。

        返回 True 表示已把后台任务结果插入用户消息，需要继续一轮对话；
        返回 False 表示无后台任务可处理，对话可以结束。
        """
        if not hasattr(self, "_background_tasks") or not self._background_tasks:
            return False

        # ① 有已完成的后台任务 → 收集结果插入用户消息
        done_msgs = self._collect_done_background_messages()
        if done_msgs:
            self._append_background_result_messages(done_msgs)
            return True

        # ② 无已完成，但有运行中的后台任务 → 等待全部完成后再处理
        pending = self._pending_background_tasks()
        if pending:
            tasks = [
                r.get("task") for r in pending
                if r.get("task") is not None and not r["task"].done()
            ]
            if tasks:
                await self._wait_background_tasks(tasks)
            done_msgs = self._collect_done_background_messages()
            if done_msgs:
                self._append_background_result_messages(done_msgs)
                return True
            # 等待后仍无完成（任务被取消等）→ 清理残留记录
            self._background_tasks.clear()

        return False
