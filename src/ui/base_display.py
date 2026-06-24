"""
显示抽象基类 (Base Display)

设计意图：
---------
BaseDisplay 是所有显示层的抽象基类，继承 core/ports/display.py 的 DisplayPort，
统一核心层端口接口与显示层实现接口。所有具体的显示实现（如终端 TUI、Web UI、
日志输出等）都必须继承该类并实现全部抽象方法。

方法分类：
1. 生命周期控制：start() / stop()        — 显示器的启动与关闭
2. 工具调用展示：tool_parsing / tool_start / tool_done  — 工具从解析到执行完成的全链路展示
3. 状态与阶段：update_status / update_model_phase       — 更新工具状态和模型推理阶段
4. 资源消耗：update_usage                               — 展示 Token 用量信息
5. 实时指标：update_speed / update_live_input / update_live_output — 实时速率与 Token 流
6. 代理管理：add_agent / update_agent_status             — 多 Agent 生命周期管理
"""

from abc import abstractmethod
from typing import Optional, TYPE_CHECKING

from ..core.ports.display import DisplayPort

if TYPE_CHECKING:
    from .output_target import IOutputTarget


class BaseDisplay(DisplayPort):
    """显示抽象基类 — 同时满足核心层 DisplayPort 契约。

    所有显示终端（TUI/Web/日志等）必须继承此类并实现全部抽象方法。
    继承自 DisplayPort，因此所有子类自动满足核心层端口接口。
    """

    def __init__(self, output_target: Optional["IOutputTarget"] = None):
        """初始化显示基类。

        Args:
            output_target: 输出目标实例。默认为 None，子类可自行创建默认输出。
        """
        self._output_target = output_target

    @property
    def output_target(self) -> Optional["IOutputTarget"]:
        """获取当前输出目标。"""
        return self._output_target

    # ═══════════════════════════════════════════════════════════
    # 以下方法继承自 DisplayPort，由子类实现
    # ═══════════════════════════════════════════════════════════
    #
    # start() / stop()
    # tool_parsing() / tool_start() / tool_done()
    # update_model_phase() / update_usage()
    # update_speed() / update_live_input() / update_live_output()
    # tool_batch_start() / update_parse_info()
    # update_agent_status() / add_agent()
    # capture_and_print()
    #
    # ═══════════════════════════════════════════════════════════

    # ── BaseDisplay 特有方法（DisplayPort 之外） ─────────────

    @abstractmethod
    def update_status(self, label: str, status: str) -> None:
        """更新状态。

        更新指定 label 对应显示单元的当前状态文本。

        Args:
            label:  工具标识或 Agent 标识
            status: 状态描述文本（如"等待中"、"运行中"、"已完成"）
        """
        ...

    # ── Web/CLI 双端收敛方法 ─────────────────────────────

    def capture_and_print_async(self, display_func) -> str:
        """异步捕获显示函数的输出并打印。

        默认回退到同步的 capture_and_print，子类可覆盖以提供异步实现。
        """
        return self.capture_and_print(display_func)

    def tool_batch_start(self, label: str, names: list[str]) -> None:
        """批量工具开始。默认空实现，子类可按需覆盖。"""
        pass

    def tool_parsing(self, label: str, tool_name: str, arguments: str = "") -> None:
        """工具解析中。默认空实现，子类可按需覆盖。"""
        pass

    def update_parse_info(self, label: str, tool_name: str, tokens: int, elapsed: float) -> None:
        """更新解析信息。默认空实现，子类可按需覆盖。"""
        pass

    def parse_info_done(self, label: str) -> None:
        """解析信息完成。默认空实现，子类可按需覆盖。"""
        pass

    def add_agent(self, agent_id: str, agent_type: str, description: str) -> None:
        """添加代理。默认空实现，子类可按需覆盖。"""
        pass

    def update_agent_status(self, agent_id: str, status: str, detail: str) -> None:
        """更新代理状态。默认空实现，子类可按需覆盖。"""
        pass

    def set_panel_context(self, context) -> None:
        """注入 PanelContext。默认空实现，子类可按需覆盖。"""
        pass

    def create_sub_display(self, max_history: int) -> "DisplayPort":
        """创建子 DisplayPort。默认返回自身（降级）。"""
        return self

    def set_result(self, agent_id: str, result: str | None = None, error: str | None = None) -> None:
        """设置代理执行结果。默认空实现，子类可按需覆盖。"""
        pass

    def remove_agent(self, agent_id: str) -> None:
        """移除代理显示。默认空实现，子类可按需覆盖。"""
        pass
