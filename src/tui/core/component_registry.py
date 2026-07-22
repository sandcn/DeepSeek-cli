"""组件注册表 — 管理 RenderCommand → 组件映射。

提供 ComponentRegistry 单例，替代 renderer.py 中的硬编码 _RENDER_DISPATCH 字典。
支持运行时注册，与 @register_render_command 装饰器兼容。

设计原则：
  - 单例模式：全局唯一，通过 get_default() 获取
  - 线程安全：读写操作使用 threading.Lock
  - 向后兼容：与现有 @register_render_command 装饰器兼容
  - 可扩展：外部模块可通过 register() 添加新命令映射

用法：
    from src.tui.core.component_registry import ComponentRegistry

    # 注册
    ComponentRegistry.get_default().register(1, "do_sth", (0,))

    # 查找
    method_name, arg_indices = ComponentRegistry.get_default().resolve(1)
"""

from __future__ import annotations

import threading
from typing import ClassVar, Tuple


__all__: list[str] = [
    "ComponentRegistry",
]


class ComponentRegistry:
    """组件注册表 — 管理 RenderCommand → 组件映射。

    映射结构：command_id (int) → (method_name: str, arg_indices: tuple[int, ...])

    单例模式，通过 get_default() 获取全局实例。
    """

    _instance: ComponentRegistry | None = None
    _instance_lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        """初始化注册表（私有构造器，通过 get_default() 获取）。"""
        self._mapping: dict[int, tuple[str, tuple[int, ...]]] = {}
        self._lock = threading.Lock()
        self._populate_defaults()

    # ── 默认命令集 ────────────────────────────────────

    @staticmethod
    def _build_default_commands() -> dict[int, tuple[str, tuple[int, ...]]]:
        """构建全部 17 个默认命令映射（5 框架 + 12 聊天域）。

        命令 ID 对应 RenderCommand 枚举值，方法名对应 FrameworkRenderer
        或 TuiRenderer 上的 _do_* 方法。

        Returns:
            command_id → (method_name, arg_indices) 映射字典。
        """
        # 延迟导入避免模块级循环依赖
        from ..engine.const import RenderCommand  # noqa: PLC0415

        return {
            # ── 框架通用命令（5 个）── renderer_base.py
            RenderCommand.NOTIFICATION:  ("_do_notification",   (1,)),
            RenderCommand.WRITE_LINE:    ("_do_write_line",     (1,)),
            RenderCommand.ERROR:         ("_do_error",          (1,)),
            RenderCommand.SPLASH:        ("_do_splash",         ()),
            RenderCommand.SUBAGENT_FRAME: ("_do_subagent_frame", (1,)),
            # ── 聊天域命令（12 个）── renderer.py
            RenderCommand.REASONING:     ("_do_reasoning",      (1,)),
            RenderCommand.CONTENT:       ("_do_content",        (1,)),
            RenderCommand.PHASE_DONE:    ("_do_phase_done",     (1,)),
            RenderCommand.TOOL_COUNT_INC: ("_do_tool_count_inc", ()),
            RenderCommand.TOOL_COUNT_DEC: ("_do_tool_count_dec", ()),
            RenderCommand.TOOL_FAIL_INC: ("_do_tool_fail_inc",  ()),
            RenderCommand.MAIN_PHASE:    ("_do_main_phase",     (1,)),
            RenderCommand.TOOL_OUTPUT:   ("_do_tool_output",    (1,)),
            RenderCommand.TOOL_SUMMARY:  ("_do_tool_summary",   (1, 2)),
            RenderCommand.PARSE_INFO:    ("_do_parse_info",     (1, 2, 3)),
            RenderCommand.USER_MSG:      ("_do_user_message",   (1,)),
            RenderCommand.DISPLAY_MSGS:  ("_do_display_messages", (1, 2)),
        }

    def _populate_defaults(self) -> None:
        """将 _build_default_commands() 返回的全部命令注册到当前实例。

        在 __init__ 中自动调用，确保每次构造（含 reset_default() 后重建）
        都自动恢复完整的命令映射。
        """
        for cid, (method_name, arg_indices) in self._build_default_commands().items():
            self._mapping[cid] = (method_name, arg_indices)

    # ── 单例访问 ──────────────────────────────────────

    @classmethod
    def get_default(cls) -> ComponentRegistry:
        """获取全局默认注册表实例（线程安全单例）。"""
        if cls._instance is None:
            with cls._instance_lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset_default(cls) -> None:
        """重置默认实例（供测试使用）。"""
        with cls._instance_lock:
            cls._instance = None

    # ── 注册表操作 ────────────────────────────────────

    def register(
        self,
        command_id: int,
        method_name: str,
        arg_indices: tuple[int, ...] = (),
    ) -> None:
        """注册命令 ID 到方法名的映射。

        Args:
            command_id: RenderCommand 枚举值。
            method_name: TuiRenderer 上对应的方法名（如 "_do_content"）。
            arg_indices: 从命令元组中提取参数的索引元组。
        """
        with self._lock:
            self._mapping[command_id] = (method_name, arg_indices)

    def resolve(self, command_id: int) -> tuple[str, tuple[int, ...]] | None:
        """解析命令 ID 对应的方法名和参数索引。

        Args:
            command_id: RenderCommand 枚举值。

        Returns:
            (method_name, arg_indices) 元组，未注册时返回 None。
        """
        with self._lock:
            return self._mapping.get(command_id)

    def has(self, command_id: int) -> bool:
        """检查命令 ID 是否已注册。

        Args:
            command_id: RenderCommand 枚举值。

        Returns:
            是否已注册。
        """
        with self._lock:
            return command_id in self._mapping

    def clear(self) -> None:
        """清空所有注册（供测试使用）。"""
        with self._lock:
            self._mapping.clear()

    def all_commands(self) -> list[int]:
        """获取所有已注册的命令 ID 列表。

        Returns:
            命令 ID 列表。
        """
        with self._lock:
            return list(self._mapping.keys())

    def count(self) -> int:
        """获取已注册的命令数量。

        Returns:
            注册数量。
        """
        with self._lock:
            return len(self._mapping)
