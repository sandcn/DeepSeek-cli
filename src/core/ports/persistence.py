"""会话持久化和断点管理的端口定义。

包含两个抽象接口（PersistencePort / CheckpointPort）。
适配器实现已移至 src.core.adapters.persistence。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


# ── 端口定义 ──────────────────────────────────────────────

class PersistencePort(ABC):
    """会话持久化端口 —— 管理对话的保存、加载、列举与删除。"""

    @abstractmethod
    def save_session(
        self,
        messages: list[dict],
        model: str,
        session_id: str | None = None,
    ) -> str:
        """保存对话并返回会话 ID。"""

    @abstractmethod
    def load_session(self, session_id: str) -> dict | None:
        """加载指定 ID 的会话数据，不存在时返回 None。"""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """列出所有已保存的会话摘要（按保存时间降序）。"""

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """删除指定会话，成功返回 True，不存在 / 失败返回 False。"""

    @abstractmethod
    def get_recover_cmd(self, session_id: str) -> str:
        """获取恢复指定会话的命令行提示字符串。"""


class CheckpointPort(ABC):
    """断点管理端口 —— 用于长任务执行中的状态保存与恢复。"""

    @abstractmethod
    def save(
        self,
        messages: list[dict],
        model: str,
        task_description: str = "",
    ) -> None:
        """保存当前任务状态为断点。"""

    @abstractmethod
    def load(self) -> dict | None:
        """加载最新断点数据，不存在时返回 None。"""

    @abstractmethod
    def clear(self) -> None:
        """清除断点（任务成功完成时调用）。"""

    @abstractmethod
    def exists(self) -> bool:
        """检查是否存在有效断点。"""

    @abstractmethod
    def get_info(self) -> dict | None:
        """获取断点摘要信息，不存在时返回 None。"""
