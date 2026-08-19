from __future__ import annotations

import abc
import functools
import inspect
import logging
import os
from dataclasses import dataclass as _std_dataclass
from typing import Any, List, Optional, Union

from src._compat import dataclass

# ── inspect.signature 缓存 ─────────────────────────────────
# from_args() 每次调用 inspect.signature(cls.__init__) 约 0.1-0.3ms，
# 对频繁调用的工具而言是可避免的开销。使用 lru_cache 消除重复计算，
# 线程安全（GIL 下 dict 操作虽原子，但 lru_cache 语义更清晰）。

@functools.lru_cache(maxsize=256)
def _get_init_sig(cls: type) -> inspect.Signature:
    """获取类的 __init__ 签名，结果有界缓存（工具类数量远小于 256）。"""
    return inspect.signature(cls.__init__)


async def print_to_terminal(text: str, tool_id: str = "") -> None:
    """所有工具输出的唯一终端写入路径。

    通过 EventBus 发布 ToolOutputChunkEvent，由 ChatUIConsumer
    render 线程统一排队渲染，不与底部栏刷新竞态。

    实现委托给 ``Func._publish_tool_text``（二者解析逻辑完全一致：
    tool_id 为空时从 contextvar 解析归属，仍为空回退 "assistant"）。

    Args:
        text: 输出文本。
        tool_id: 可选工具调用 ID。为空时从 contextvar（当前工具上下文）
            解析归属；仍为空回退 "assistant"（兼容旧行为）。
    """
    Func._publish_tool_text(text, tool_id)


# 基类
class Func(abc.ABC):
    name: str | None = None  # 工具名称（类属性，子类覆盖）

    def __init__(self):
        self.agent = None  # 调用工具的Agent实例
        self.agent_type: str | None = None  # 调用方Agent类型，None表示未知
        self.execution_time: float = 0.0
        self.execution_count: int = 0
        self.execution_success: int = 0
        self.execution_failed: int = 0
        # 多模态结构化结果（OpenAI 兼容 content blocks，如 image_url）。
        # 工具在 execute() 中设置后，执行链路自动将返回包装为 ToolResult，
        # tool 消息 content 变为 blocks list（多模态模型可见图片）。
        self.result_blocks: list[dict] | None = None

    def set_agent(self, agent):
        self.agent = agent

    @classmethod
    def can_use(cls, tool_name: str, agent_type: str = "execute", path: str | None = None) -> "tuple[bool, str | None]":
        """检查指定类型的 agent 能否使用某工具。

        Args:
            tool_name: 工具名称
            agent_type: Agent 类型（map/review/plan/execute），默认 execute
            path: 目标文件路径（可选），用于 plan agent 写入文件时的路径白名单校验

        Returns:
            (is_allowed: bool, error_message: str | None)
            - True + None：可以使用
            - False + 错误信息：不可使用，附带原因
        """
        # 延迟导入：避免 tools.base ↔ core.subagent 的循环依赖
        from ..core.subagent import _get_excluded_tools
        excluded = _get_excluded_tools(agent_type)
        if tool_name in excluded:
            return (False, f"工具 '{tool_name}' 不可用于 '{agent_type}' 类型 agent，"
                    f"该 agent 类型的工具白名单已排除此工具")
        # 路径白名单校验：plan agent 使用 write_file / update_file / mkdir 时限制写入目录
        # （与 file_base._validate_path_and_size / mkdir.execute 共用
        #  get_plan_allowed_dir + is_path_within_dir，realpath 解析符号链接防绕过）
        if path is not None and agent_type == 'plan' and tool_name in ('write_file', 'update_file', 'mkdir'):
            from .file_ops import get_plan_allowed_dir, is_path_within_dir
            allowed_dir = get_plan_allowed_dir()
            agent_label = "plan agent"
            if not is_path_within_dir(path, allowed_dir):
                return (False, f"{agent_label} 只能在 {allowed_dir} 目录下写入文件。"
                        f"当前路径: {path}（解析后: {os.path.realpath(path)}），"
                        f"不在允许的目录: {allowed_dir}")
        return (True, None)

    @classmethod
    def get_metadata(cls) -> Optional["ToolMetadata"]:
        """获取此工具的元数据（ToolMetadata），未设置时返回 None"""
        return get_tool_metadata(cls)

    @classmethod
    @abc.abstractmethod
    def to_tool_schema(cls):
        """返回 OpenAI function schema。"""
        ...

    @classmethod
    def from_args(cls, args: dict):
        """从 JSON 参数创建实例。默认按 __init__ 参数名从 args 取值，子类可覆盖以做特殊处理。"""
        sig = _get_init_sig(cls)
        _params = list(sig.parameters.values())
        # 跳过 self
        _init_params = [p for p in _params if p.name != 'self']
        # 检查必需参数是否缺失（无默认值且不在 args 中）
        _missing = [
            p.name for p in _init_params
            if p.default is inspect.Parameter.empty and p.name not in args
        ]
        if _missing:
            names = ', '.join(_missing)
            raise ValueError(
                f"工具 '{cls.name}' 缺少必需参数: {names}。"
                f"调用时必须提供所有必需参数。"
            )
        # 多余参数（非 __init__ 参数名）：记录 debug 日志，不阻塞执行——
        # 模型传错参数名时下游 execute 会给出错误，debug 日志便于排查
        _extra = set(args) - {p.name for p in _init_params}
        if _extra:
            logging.getLogger(__name__).debug(
                "工具 '%s' 收到多余参数: %s（已忽略）",
                cls.name, sorted(_extra),
            )
        kwargs = {p.name: args[p.name] for p in _init_params if p.name in args}
        return cls(**kwargs)

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        """返回用于终端显示的关键参数摘要。子类可覆盖以提供更有意义的展示。"""
        return ""

    # ── 公共辅助方法 ──

    @staticmethod
    def _sanitize_display(text: str) -> str:
        """转义 \\r \\n 等不可见字符，适合终端显示"""
        return text.replace('\r', '/r').replace('\n', '/n')

    # ── 通用显示辅助 ──

    @staticmethod
    def _publish_tool_text(text: str, tool_id: str = "") -> None:
        """将工具显示文本发布到 EventBus，统一走 ChatUIConsumer cmd 队列渲染。

        Args:
            text: 显示文本。
            tool_id: 可选工具调用 ID。为空时从 contextvar 解析归属；
                仍为空回退 "assistant"（兼容旧行为）。
        """
        from ..tui.events.event_types import ToolOutputChunkEvent
        from ..tui.events.publish import emit
        from ..core.internal.agent._tool_context import get_current_tool_id
        try:
            resolved = tool_id or get_current_tool_id() or "assistant"
            emit(ToolOutputChunkEvent(
                label=resolved, tool_id=resolved, text=text, source="agent",
            ))
        except Exception:
            # 事件发布失败 → warning（用户侧工具输出静默丢失需可感知）
            _logger = logging.getLogger(__name__)
            _logger.warning("_publish_tool_text 失败", exc_info=True)

    @staticmethod
    def _publish_tool_notice(text: str, tool_id: str = "") -> None:
        """将工具通知（警告/提示）以「▎通知」块发布（所有工具通用）。

        与 ``_publish_tool_text``（工具卡内输出）互补——通知类文本经
        ``ToolNoticeEvent`` → ``NotificationCmd`` 上屏为通知块：
        ``▎通知`` 角色头 + ``  │ + 文本`` 行，与 Ctrl+B 空模式切换通知
        （``+ 主 Agent 已进入空模式``）同款显示。

        Args:
            text: 通知文本（纯文本；空文本跳过。``+ `` 前缀由本方法
                统一添加，调用方无需拼接）。
            tool_id: 可选工具调用 ID。为空时从 contextvar（当前工具上下文）
                解析归属；仍为空回退 "assistant"（无归属，dispatcher 过滤）。
        """
        text = str(text or "").rstrip("\n")
        if not text:
            return
        if not text.startswith("+ "):
            text = f"+ {text}"
        from ..tui.events.event_types import ToolNoticeEvent
        from ..tui.events.publish import emit
        from ..core.internal.agent._tool_context import get_current_tool_id
        try:
            resolved = tool_id or get_current_tool_id() or "assistant"
            emit(ToolNoticeEvent(
                label=resolved, tool_id=resolved, text=text, source="agent",
            ))
        except Exception:
            _logger = logging.getLogger(__name__)
            _logger.warning("_publish_tool_notice 失败", exc_info=True)

    @staticmethod
    def _print_operation(description: str) -> None:
        """打印操作描述到终端（通用格式）"""
        from ..core.constants import DIM, RESET
        Func._publish_tool_text(f"\n  {DIM}{description}{RESET}")

    @staticmethod
    def _print_result(result: str, success_prefix: str = "+", fail_prefix: str = "x") -> None:
        """根据结果前缀打印成功/失败信息到终端。以 `(` 开头的错误结果视为失败。"""
        from ..core.constants import GREEN, RED, RESET
        if result.startswith("("):
            Func._publish_tool_text(f"  {RED}{fail_prefix} {result}{RESET}")
        else:
            Func._publish_tool_text(f"  {GREEN}{success_prefix} {result}{RESET}")

    # ── 显示模板方法 ──

    async def _display_result_template(
        self, header: str, extra_info: str = "",
        error_prefixes: tuple[str, ...] = ("(",),
    ) -> str:
        """display() 通用模板 — ls/find/search 统一使用。

        1. 用 publish_output 输出 header（及可选的 extra_info）
        2. await self.execute()
        3. 检查结果首行是否为错误（匹配 error_prefixes）→ 分色打印
        4. 返回 result
        """
        from ..core.constants import GREEN, YELLOW, DIM, RESET

        lines = [f"\n  {DIM}{header}{RESET}"]
        if extra_info:
            lines.append(f"  {DIM}  {extra_info}{RESET}")
        for line in lines:
            self._publish_tool_text(line)

        result = await self.execute()
        lines_result = result.split("\n", 1)
        first = lines_result[0]

        is_error = any(first.startswith(p) for p in error_prefixes)
        color = YELLOW if is_error else GREEN
        self._publish_tool_text(f"  {color}{first}{RESET}")
        if len(lines_result) > 1 and lines_result[1]:
            self._publish_tool_text(f"  {DIM}{lines_result[1]}{RESET}")
        return result

    # ── 抽象方法 ──

    @abc.abstractmethod
    async def execute(self) -> str:
        """异步执行工具逻辑，返回结果字符串（默认无 UI 副作用，子类覆盖时须在 docstring 中注明）。"""
        ...

    async def display(self) -> str:
        """显示工具执行过程并返回结果。默认直接 await execute()，子类可覆盖以添加 UI 输出。"""
        return await self.execute()

    # ── 执行指标 ──

    @classmethod
    def get_execution_stats(cls) -> dict:
        """获取所有实例的汇总执行统计（类方法，供展示用）"""
        return {
            "name": cls.name,
            "category": getattr(get_tool_metadata(cls), "category", "general"),
            "parallel_safe": getattr(get_tool_metadata(cls), "parallel_safe", False),
        }

    def record_execution(self, success: bool, elapsed: float) -> None:
        """记录一次工具执行的结果"""
        self.execution_time += elapsed
        self.execution_count += 1
        if success:
            self.execution_success += 1
        else:
            self.execution_failed += 1


# ═══════════════════════════════════════════════════════════════
# 工具元数据系统 (P2-2)
# ═══════════════════════════════════════════════════════════════

@dataclass(slots=True)
class ToolMetadata:
    """工具元数据 — 声明工具的运行时属性

    附加到 Func 子类的 _tool_metadata 属性上，
    供 ToolRegistry、Agent 和 UI 层读取以优化调度和显示。

    Attributes:
        parallel_safe: 是否可与其他工具并行执行
        requires_network: 是否需要网络访问
        requires_terminal: 是否需要用户交互终端
        timeout_estimate: 预计最大执行时间（秒），0 表示不确定
        category: 工具分类标签（"io", "code", "search", "interactive", "general"）
        priority: 执行优先级（越小越优先），默认 100
        tool_category: 调度约束分类（"read"/"write"/"bash"/"interactive"/"general"），默认 "general"
        description: 工具描述
    """
    parallel_safe: bool = False
    requires_network: bool = False
    requires_terminal: bool = False
    timeout_estimate: float = 0
    category: str = "general"
    priority: int = 100
    tool_category: str = "general"
    description: str = ""


# ── 元数据注册辅助 ──────────────────────────────────────

_METADATA_ATTR = "_tool_metadata"


def tool_metadata(
    parallel_safe: bool = False,
    requires_network: bool = False,
    requires_terminal: bool = False,
    timeout_estimate: float = 0,
    category: str = "general",
    priority: int = 100,
    tool_category: str = "general",
    description: str = "",
):
    """装饰器：为工具类附加元数据

    使用方式:
        @tool_metadata(parallel_safe=True, requires_network=True, category="io")
        class ReadFile(Func):
            name = "read_file"
            ...
    """
    def decorator(cls):
        meta = ToolMetadata(
            parallel_safe=parallel_safe,
            requires_network=requires_network,
            requires_terminal=requires_terminal,
            timeout_estimate=timeout_estimate,
            category=category,
            priority=priority,
            tool_category=tool_category,
            description=description,
        )
        setattr(cls, _METADATA_ATTR, meta)
        return cls
    return decorator


def get_tool_metadata(tool_class) -> Optional[ToolMetadata]:
    """获取工具类的元数据，未设置时返回 None"""
    return getattr(tool_class, _METADATA_ATTR, None)


# ═══════════════════════════════════════════════════════════════
# 工具结构化结果（多模态支持）
# ═══════════════════════════════════════════════════════════════
# 使用标准库 dataclass（非 slots）定义：_compat.dataclass 的 slots 兼容
# 包装在 mypy 下无法识别为 dataclass（无自动生成的 __init__），
# 而 ToolResult 对 slots 无性能诉求，标准 dataclass 可同时满足
# 运行（Python 3.9）与类型检查。

@_std_dataclass
class ToolResult:
    """工具结构化结果 — 文本摘要 + 多模态 content blocks

    工具 execute() 返回人类可读文本的同时，可通过 ``func.result_blocks``
    携带 OpenAI 兼容的多模态 content blocks（如 image_url data URI）。
    执行链路（ToolScheduler._run_tool_func）检测到 result_blocks 后，
    将返回包装为本对象；``BaseAgent._append_tool_result`` 据此把 tool
    消息 content 设为 blocks list（多模态模型可直接看到图片），
    Anthropic 适配器再转换为 image block。

    Attributes:
        text: 给模型的文本摘要（execute() 返回值语义，展示/统计用）。
        blocks: OpenAI 兼容 content blocks 列表（如
            ``[{"type": "text", ...}, {"type": "image_url", ...}]``），
            可空（空时退化为纯文本 content）。
    """
    text: str
    blocks: list[dict] | None = None

    def to_content(self) -> Union[str, List[dict]]:
        """转换为 tool 消息 content（str 或 list[dict]）。

        有 blocks 时返回 blocks（多模态），否则返回 text（纯文本）。
        """
        if self.blocks:
            return self.blocks
        return self.text

    @property
    def display_text(self) -> str:
        """展示/统计用文本（TUI 工具卡、token 估算等）。"""
        return self.text


def to_tool_text(value: Any) -> str:
    """将工具输出值（str 或 ToolResult）归一化为纯文本。

    供展示/统计链路（on_after 回调、token 估算等）复用，
    避免各消费方重复 ``isinstance(value, ToolResult)`` 判断。
    """
    if isinstance(value, ToolResult):
        return value.text
    if value is None:
        return ""
    return str(value)
