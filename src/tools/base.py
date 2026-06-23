from __future__ import annotations

import abc
import inspect
import logging

from src._compat import dataclass
from typing import Optional



async def print_to_terminal(text: str) -> None:
    """所有工具输出的唯一终端写入路径。

    通过 EventBus 发布 ToolOutputChunkEvent，由 ChatUIConsumer
    render 线程统一排队渲染，不与底部栏刷新竞态。
    """
    from ..ui.events.event_types import ToolOutputChunkEvent
    from ..ui.events.event_bus import DisplayEventBus
    DisplayEventBus.get_default().publish(ToolOutputChunkEvent(
        label="assistant", text=text, source="agent",
    ))


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

    def set_agent(self, agent):
        self.agent = agent

    @classmethod
    def can_use(cls, tool_name: str, agent_type: str = "ordinary") -> "tuple[bool, str | None]":
        """检查指定类型的 agent 能否使用某工具。

        Args:
            tool_name: 工具名称
            agent_type: Agent 类型（ordinary/map/review/plan/read_memory/write_memory/plan_execute），默认 ordinary

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
        sig = inspect.signature(cls.__init__)
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
    def _publish_tool_text(text: str) -> None:
        """将工具显示文本发布到 EventBus，统一走 ChatUIConsumer cmd 队列渲染。"""
        from ..ui.events.event_types import ToolOutputChunkEvent
        from ..ui.events.event_bus import DisplayEventBus
        try:
            DisplayEventBus.get_default().publish(ToolOutputChunkEvent(
                label="assistant", text=text, source="agent",
            ))
        except Exception:
            _logger = logging.getLogger(__name__)
            _logger.debug("_publish_tool_text 失败")

    @staticmethod
    def _print_operation(description: str) -> None:
        """打印操作描述到终端（通用格式）"""
        from ..core.constants import DIM, RESET
        Func._publish_tool_text(f"\n  {DIM}{description}{RESET}")

    @staticmethod
    def _print_result(result: str, success_prefix: str = "+", fail_prefix: str = "x") -> None:
        """根据结果前缀打印成功/失败信息到终端。以 `(` 开头的错误结果视为失败。"""
        from ..core.constants import GREEN, RED, YELLOW, RESET
        if result.startswith("("):
            Func._publish_tool_text(f"  {RED}{fail_prefix} {result}{RESET}")
        else:
            Func._publish_tool_text(f"  {GREEN}{success_prefix} {result}{RESET}")

    @staticmethod
    def _web_print(line: str) -> None:
        """Web 模式下打印到终端（通过 EventBus 统一渲染）"""
        Func._publish_tool_text(line)

    # ── 显示模板方法 ──

    async def _display_result_template(
        self, header: str, extra_info: str = "",
        error_prefixes: tuple[str, ...] = ("(",),
    ) -> str:
        """display() 通用模板 — ls/find/search 统一使用。

        1. 用 locked_print 输出 header（及可选的 extra_info）
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

    async def _web_display_result_template(
        self, header: str, print_result: bool = True,
    ) -> str:
        """web_display() 通用模板 — ls/find/search 统一使用。

        1. 用 _web_print 输出 header
        2. await self.execute()
        3. 可选：打印结果首行（成功绿色/错误黄色）
        4. 返回 result
        """
        from ..core.constants import GREEN, YELLOW, DIM, RESET

        line = f"\n  {DIM}{header}{RESET}\n"
        self._web_print(line)

        result = await self.execute()

        if print_result:
            if result.startswith("("):
                self._web_print(f"  {YELLOW}{result}{RESET}\n")
            else:
                self._web_print(f"  {GREEN}{result.split(chr(10))[0]}{RESET}\n")

        return result

    # ── 抽象方法 ──

    @abc.abstractmethod
    async def execute(self) -> str:
        """异步执行工具逻辑，返回结果字符串（无 UI 副作用）。"""
        ...

    async def display(self) -> str:
        """显示工具执行过程并返回结果。默认直接 await execute()，子类可覆盖以添加 UI 输出。"""
        return await self.execute()

    async def web_display(self) -> str:
        """Web 模式下的工具执行。默认回退到 display()，子类可覆盖以提供 Web 专用 UI。"""
        return await self.display()

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
        description: 工具描述
    """
    parallel_safe: bool = False
    requires_network: bool = False
    requires_terminal: bool = False
    timeout_estimate: float = 0
    category: str = "general"
    priority: int = 100
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
            description=description,
        )
        setattr(cls, _METADATA_ATTR, meta)
        return cls
    return decorator


def get_tool_metadata(tool_class) -> Optional[ToolMetadata]:
    """获取工具类的元数据，未设置时返回 None"""
    return getattr(tool_class, _METADATA_ATTR, None)
