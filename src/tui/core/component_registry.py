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

from .registry_base import RegistryBase
from .singleton import SingletonMeta

# RenderCommand 枚举值 — 使用 .value 获取整数值
from ..engine.const import RenderCommand


__all__: list[str] = [
    "ComponentRegistry",
]


class ComponentRegistry(RegistryBase, metaclass=SingletonMeta):
    """组件注册表 — 管理 RenderCommand → 组件映射。

    映射结构：command_id (int) → (method_name: str, arg_indices: tuple[int, ...])

    单例模式，由 ``SingletonMeta`` 自动提供 get_default / reset_default。
    """

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
        return {
            # ── 框架通用命令（5 个）── renderer_base.py
            RenderCommand.NOTIFICATION.value:   ("_do_notification",   (1,)),
            RenderCommand.WRITE_LINE.value:     ("_do_write_line",     (1,)),
            RenderCommand.ERROR.value:          ("_do_error",          (1,)),
            RenderCommand.SPLASH.value:         ("_do_splash",         ()),
            RenderCommand.SUBAGENT_FRAME.value: ("_do_subagent_frame", (1,)),
            # ── 聊天域命令（12 个）── renderer.py
            RenderCommand.REASONING.value:      ("_do_reasoning",      (1,)),
            RenderCommand.CONTENT.value:        ("_do_content",        (1,)),
            RenderCommand.PHASE_DONE.value:     ("_do_phase_done",     (1,)),
            RenderCommand.TOOL_COUNT_INC.value: ("_do_tool_count_inc", ()),
            RenderCommand.TOOL_COUNT_DEC.value: ("_do_tool_count_dec", ()),
            RenderCommand.TOOL_FAIL_INC.value:  ("_do_tool_fail_inc",  ()),
            RenderCommand.MAIN_PHASE.value:     ("_do_main_phase",     (1,)),
            RenderCommand.TOOL_OUTPUT.value:    ("_do_tool_output",    (1,)),
            RenderCommand.TOOL_SUMMARY.value:   ("_do_tool_summary",   (1, 2)),
            RenderCommand.PARSE_INFO.value:     ("_do_parse_info",     (1, 2, 3)),
            RenderCommand.USER_MSG.value:       ("_do_user_message",   (1,)),
            RenderCommand.DISPLAY_MSGS.value:   ("_do_display_messages", (1, 2)),
        }

    def _populate_defaults(self) -> None:
        """将 _build_default_commands() 返回的全部命令注册到当前实例。

        在 __init__ 中自动调用，确保每次构造（含 reset_default() 后重建）
        都自动恢复完整的命令映射。
        """
        for cid, (method_name, arg_indices) in self._build_default_commands().items():
            self._mapping[cid] = (method_name, arg_indices)

    # 单例访问由 SingletonMeta 提供：
    #   ComponentRegistry.get_default() → 线程安全单例获取（DCL）
    #   ComponentRegistry.reset_default() → 线程安全单例重置（供测试使用）

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

    def list_registered(self) -> dict:
        """返回所有已注册映射的副本（线程安全）。

        Returns:
            命令 ID → (method_name, arg_indices) 映射字典副本。
        """
        with self._lock:
            return dict(self._mapping)
