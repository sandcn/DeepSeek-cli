"""Agent 基类 — 纯消息管理

提供公共的消息追加和沙盒同步方法。
保持核心逻辑简单，工具调用等复杂行为由 Agent 子类实现。
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import weakref
from typing import Any

from .sandbox_manager import get_sandbox_manager, set_current_message_index

_logger = logging.getLogger(__name__)

# ── 全局活跃 Agent 注册表（ESC 中断杀后台任务用） ──────────
# weakref.WeakSet：Agent/SubAgent 实例被 GC 后自动移除，无需显式注销，
# 避免生命周期管理遗漏导致的内存泄漏或悬挂引用。主 Agent 与所有
# SubAgent 实例在 BaseAgent.__init__ 中注册；ESC 中断时
# kill_all_active_background_tasks() 遍历本表统一杀掉全部后台任务
# （bash + subagent，含 managed_by_tool 的）。
_active_agents: "weakref.WeakSet[BaseAgent]" = weakref.WeakSet()
_active_agents_lock = threading.Lock()

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
        # ★ 注册到全局活跃 Agent 注册表（ESC 中断时统一杀掉所有后台任务）
        with _active_agents_lock:
            _active_agents.add(self)

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

        返回仍未完成的任务集合。空集合表示：
          - 全部任务已完成；或
          - 中断退出等待——用户按 ESC（kill_background 标志置位）时已杀掉
            所有后台任务（含 managed_by_tool）；普通中断（Ctrl+C/双 Esc/
            clawbot /stop/网络错误，kill 标志未置位）仅退出等待、任务继续
            运行（由 bash_opt/subagent_opt 或下一轮对话继续管理）。
        超时后未完成的任务由调用方处理（标记 managed_by_tool 交 bash_opt
        工具管理），避免长时/挂起的 bash 后台任务（如自动转后台后命令
        永不退出）让 Agent/SubAgent 无限阻塞。

        Args:
            tasks: asyncio.Task 列表
            timeout: 最长等待秒数。None 使用 _BACKGROUND_WAIT_TIMEOUT。

        Returns:
            仍未完成（超时）的任务集合；全部完成或中断退出等待时返回空集合。
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
            # 等待期间检查中断信号：用户按 ESC（kill_background 标志置位）
            # 时杀掉所有后台任务（含 managed_by_tool 的 bash/subagent，
            # 跨所有活跃 Agent——由 kill_all_active_background_tasks 遍历
            # 全局注册表统一处理）；普通中断（Ctrl+C/双 Esc/网络错误等，
            # kill 标志未置位）仅退出等待、不杀后台任务（新语义，见
            # docstring——区别于旧实现无条件取消 pending 任务）。
            try:
                port = getattr(self, "_interrupt_port", None)
                interrupted = False
                if port is not None:
                    interrupted = await port.is_interrupted()
                if not interrupted:
                    # 全局中断信号兜底：SubAgent 无 _interrupt_port 时仍可
                    # 响应中断（ESC kill 经 kill_all_active_background_tasks
                    # 遍历注册表兜底达成）
                    from ..api.interrupt_async import is_interrupted
                    interrupted = is_interrupted()
                if interrupted:
                    from ..api.interrupt_async import is_kill_background_requested
                    if is_kill_background_requested():
                        await kill_all_active_background_tasks()
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
                # 注意：_wait_background_tasks 中断退出等待时也返回空集
                # （ESC kill 已清表 / 普通中断运行中任务保留）——与"全部
                # 完成"语义区分见其 docstring；运行中任务由下方 stale 清理
                # 分支保留（非 managed），下一轮对话继续处理
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
                # 注意：_wait_background_tasks 中断退出等待时也返回空集
                # （ESC kill 已清表 / 普通中断运行中任务保留）——与"全部
                # 完成"语义区分见其 docstring；运行中任务由下方 stale 清理
                # 分支保留（非 managed），下一轮对话继续处理
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

    # ═══════════════════════════════════════════════════════════
    # ESC 中断：杀掉全部后台任务（bash + subagent）
    # ═══════════════════════════════════════════════════════════

    async def _kill_all_background_tasks(self) -> int:
        """杀掉本 Agent 的全部后台任务（bash + subagent，含 managed_by_tool）。

        用户按 ESC 中断时调用（Agent.run interrupted 分支 /
        _wait_background_tasks 中断检查，均在事件循环线程中执行，
        task.cancel() 线程安全）：
          1. bash 表（_background_tasks）：取消运行中的 asyncio task
             （_run_pty/_run_pipe 的 CancelledError 分支会杀进程树），
             对已记录 pid 且进程仍存活的记录兜底杀进程树（pid 复用
             安全红线：仅当进程仍可能存活时执行）；
          2. subagent 表（_subagent_tasks）：取消运行中的 asyncio task
             （SubAgent.run() 的 finally 会清理其内部 bash 任务）；
          3. 清空两张任务表（不再生成"已取消"结果消息，保持中断语义
             干净——模型不会收到后台任务取消通知）。

        Returns:
            被取消的 asyncio 任务数量。
        """
        tasks_to_cancel: list = []
        pids_to_kill: list = []

        # ── bash 后台任务表 ──
        bg = getattr(self, "_background_tasks", None)
        if isinstance(bg, dict):
            for rec in list(bg.values()):
                if not isinstance(rec, dict):
                    continue
                task = rec.get("task")
                # 非 Task 防御（异常记录）：仅当 task 具备 done() 才调用
                if task is not None and getattr(task, "done", None) is not None and not task.done():
                    tasks_to_cancel.append(task)
                # 收集待杀进程树 pid（pid 复用安全红线见 _should_kill_process）
                pid = rec.get("pid")
                if pid is not None and _should_kill_process(rec.get("process")):
                    pids_to_kill.append(pid)

        # ── subagent 后台任务表（仅主 Agent 持有，SubAgent 无此表） ──
        sa = getattr(self, "_subagent_tasks", None)
        if isinstance(sa, dict):
            for rec in list(sa.values()):
                if not isinstance(rec, dict):
                    continue
                task = rec.get("task")
                if task is not None and getattr(task, "done", None) is not None and not task.done():
                    tasks_to_cancel.append(task)

        # 取消任务 + 移出事件循环线程杀进程树 + 超时等待（公共助手，与
        # SubAgent._cleanup_background_tasks 共用，避免两处漂移）
        await _cancel_tasks_and_kill_pids(tasks_to_cancel, pids_to_kill)
        cancelled = len(tasks_to_cancel)

        # ── 清空任务表并发布计数更新（TUI 行首统计） ──
        cleared = False
        if isinstance(bg, dict) and bg:
            bg.clear()
            cleared = True
        if isinstance(sa, dict) and sa:
            sa.clear()
            cleared = True
        if cleared:
            self._publish_background_task_event()
        return cancelled


# ── 后台任务杀公共助手（ESC kill 与 SubAgent 清理共用） ──

def _should_kill_process(process) -> bool:
    """pid 复用安全红线：仅当持有 process 对象且进程仍可能存活时才杀进程树。

    - process 为 None（记录不完整/异常数据）时无法确认进程是否已退出，pid
      可能已被 OS 复用——此时 killpg(pid) 会误杀无关进程组，跳过进程树杀
      （仅依赖 task.cancel() 的 CancelledError 分支清理）；
    - process.returncode 非 None 表示进程已退出、进程组已解散，同样跳过。

    ⚠ 已知窗口（有意的权衡）：asyncio 的 ``Process.returncode`` 仅在
    ``process.wait()`` 完成后才更新——后台 bash 进程被外部杀死后、读取循环
    尚未 break 到 ``finally: await process.wait()`` 之前，returncode 仍为
    None → 判定「存活」→ 对已解散的进程组执行 killpg。实际窗口极窄
    （pid 复用需大量进程创建，毫秒级内几乎不可能），且进程树杀是尽力而为
    的兜底（正常路径 task.cancel() 的 CancelledError 分支已杀进程树）。
    """
    return process is not None and process.returncode is None


async def _cancel_tasks_and_kill_pids(tasks_to_cancel: list, pids_to_kill: list) -> None:
    """取消 asyncio 任务 + 移出事件循环线程杀进程树 + 取消等待带超时。

    被 ``_kill_all_background_tasks`` 与 ``SubAgent._cleanup_background_tasks``
    共用，统一 pid 存活判定（``_should_kill_process``）与取消等待语义，防止
    两处逻辑漂移（尤其 pid 复用安全红线判定）。

    - kill_process_tree 内部同步遍历 /proc（进程多时可达数百 ms），移出事件
      循环线程执行（asyncio.to_thread），避免中断瞬间阻塞事件循环；
    - 取消等待带超时（5s）：被取消的后台 bash 任务可能卡在 process.wait()
      （子进程不可杀），无界等待会阻塞中断路径；进程树已杀，超时后放弃，
      残余 task 由取消流程最终完成（wait_for 超时不撤销 cancel 请求）。
    """
    if pids_to_kill:
        try:
            from ..tools.bash import kill_process_tree
        except Exception:
            # 导入失败（依赖链异常）：杀进程树整体失效——显式 warning 便于
            # 排查，并清空 pids 避免 _kill_pids 内冗余 TypeError（任务取消
            # 仍执行，子进程由 CancelledError 分支尽力清理）
            _logger.warning(
                "导入 kill_process_tree 失败，杀后台任务进程树不可用",
                exc_info=True,
            )
            pids_to_kill.clear()
            kill_process_tree = None

        def _kill_pids() -> None:
            for pid in pids_to_kill:
                try:
                    kill_process_tree(pid)
                except Exception:
                    _logger.debug(
                        "杀后台任务进程树失败: %s", pid, exc_info=True,
                    )

        try:
            await asyncio.to_thread(_kill_pids)
        except Exception:
            _logger.debug("杀后台任务进程树批量执行异常", exc_info=True)

    for t in tasks_to_cancel:
        t.cancel()
    if tasks_to_cancel:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_cancel, return_exceptions=True),
                timeout=5.0,
            )
        except asyncio.TimeoutError:
            _logger.warning(
                "后台任务取消等待超时（%d 个任务），放弃等待"
                "（进程树已杀，取消请求仍生效）",
                len(tasks_to_cancel),
            )
        except Exception:
            _logger.debug("后台任务取消等待异常", exc_info=True)


# ── 全局杀后台任务去重标志 ─────────────────────────────
# ESC 时 render 线程经 run_coroutine_threadsafe 调度杀任务（实时），同时
# 事件循环处理点（Agent.run interrupted 分支 / _wait_background_tasks 中断
# 检查）也会触发——两次调用间 task.cancel()/dict.clear() 幂等无害，但
# kill_process_tree 会重复执行。用标志去重：正在执行时后续调用直接返回 0。
_kill_in_progress = False
_kill_in_progress_lock = threading.Lock()


async def kill_all_active_background_tasks() -> int:
    """ESC 中断时杀掉所有活跃 Agent 的后台任务（bash + subagent）。

    ★ 全局语义：遍历**进程内所有**活跃 Agent（主 Agent + 各 SubAgent，
    WeakSet 自动清理已 GC 实例）的后台任务，不区分归属——ESC 是全局中断
    语义（假定单主 Agent 产品形态；若未来支持多主 Agent 独立运行，需按
    agent 实例维度去重/隔离）。逐个调用 ``_kill_all_background_tasks``：
      - 主 Agent 的 bash 后台任务（_background_tasks，含 managed_by_tool）
      - 主 Agent 的 subagent 后台任务（_subagent_tasks，含 managed_by_tool）
      - 各 SubAgent 内部的 bash 后台任务（SubAgent 的 _background_tasks）

    由中断路径（Agent.run() interrupted 分支 / _wait_background_tasks
    中断检查 / render 线程跨线程调度）在事件循环线程中调用，task.cancel()
    线程安全。带去重标志（_kill_in_progress）：正在执行时并发调用直接返回
    0（ESC 实时调度与事件循环兜底可能同时触发，避免 kill_process_tree
    重复执行；多 Agent 场景下 A 的 kill 进行中时 B 的调用会被去重吞掉，
    符合全局 ESC 语义）。

    Returns:
        被取消的 asyncio 任务总数（去重命中时为 0）。
    """
    global _kill_in_progress
    with _kill_in_progress_lock:
        if _kill_in_progress:
            return 0
        _kill_in_progress = True
    try:
        with _active_agents_lock:
            agents = list(_active_agents)
        total = 0
        for agent in agents:
            try:
                total += await agent._kill_all_background_tasks()
            except Exception:
                _logger.debug(
                    "ESC 杀后台任务失败（agent=%r）", agent, exc_info=True,
                )
        return total
    finally:
        with _kill_in_progress_lock:
            _kill_in_progress = False


def schedule_kill_all_background_tasks(loop=None) -> None:
    """render 线程 ESC 中断时调度杀掉所有后台任务（事件循环线程执行）。

    ESC 中断发生在 render 线程（``_do_interrupt`` → kill_background 回调），
    而 ``asyncio.Task.cancel()`` 须在事件循环线程执行；本函数经
    ``asyncio.run_coroutine_threadsafe`` 把 ``kill_all_active_background_tasks()``
    调度到主事件循环立即执行（事件循环运行中即杀；排队中的后台任务被杀）。

    Args:
        loop: 主事件循环（UI 层在 async 上下文经 ``asyncio.get_running_loop()``
              传入——render 线程中 ``asyncio.get_event_loop()`` 必然抛
              RuntimeError，不可达实时调度）。None 时直接返回（Python 3.9
              的 ``get_event_loop()`` 可能隐式创建并设置临时循环且永不关闭，
              悬空泄漏——调用方应总是传入运行中的主事件循环）。

    事件循环不可用/未运行时仅记日志降级——中断信号已由调用方置位，
    杀任务由下一个事件循环处理点兜底（Agent.run() interrupted 分支 /
    _wait_background_tasks 中断检查），保证最终一致性。
    """
    if loop is None:
        return
    coro = None
    try:
        if not loop.is_running():
            # 事件循环未运行：run_coroutine_threadsafe 排队后永不执行
            # （协程泄漏），跳过——由事件循环处理点兜底
            return
        coro = kill_all_active_background_tasks()
        fut = asyncio.run_coroutine_threadsafe(coro, loop)
        # 消费 Future 异常：协程内部异常（如未来新增的发布路径）不 retrieve
        # 会在 GC 时打印 "Task exception was never retrieved"——done 回调
        # 取出异常（仅触发 retrieve，异常已被协程内部 try/except 消化）
        fut.add_done_callback(
            lambda f: f.exception() if not f.cancelled() else None,
        )
    except Exception:
        # 调度失败（loop 不可用/不匹配）：关闭已创建的协程避免资源泄漏
        # （真实场景中 run_coroutine_threadsafe 会消费协程；异常路径下
        #   协程对象悬空，显式 close 消除 "coroutine was never awaited"）
        if coro is not None:
            try:
                coro.close()
            except Exception:
                pass
        _logger.debug(
            "ESC 中断调度杀后台任务失败（将由事件循环处理点兜底）",
            exc_info=True,
        )
