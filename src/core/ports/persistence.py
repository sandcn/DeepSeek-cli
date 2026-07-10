"""会话持久化和断点管理的端口定义及默认适配器实现。

包含两个抽象接口（PersistencePort / CheckpointPort）
以及基于 JSON 文件系统的默认实现（JsonFilePersistence / JsonFileCheckpoint）。
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


# ── 默认适配器：JSON 文件持久化 ───────────────────────────

class JsonFilePersistence(PersistencePort):
    """基于 .chat/msg_list/*.json 文件的会话持久化实现。

    包装 src/chat_msgs 模块中的函数，通过延迟导入避免模块加载时触发文件 IO。
    """

    def save_session(
        self,
        messages: list[dict],
        model: str,
        session_id: str | None = None,
    ) -> str:
        from ...chat_msgs import save_session as _save
        return _save(messages, model, session_id)

    def load_session(self, session_id: str) -> dict | None:
        from ...chat_msgs import load_session as _load
        return _load(session_id)

    def list_sessions(self) -> list[dict]:
        from ...chat_msgs import list_sessions as _list
        return _list()

    def delete_session(self, session_id: str) -> bool:
        from ...chat_msgs import delete_session as _delete
        return _delete(session_id)

    def get_recover_cmd(self, session_id: str) -> str:
        from ...chat_msgs import get_recover_cmd as _cmd
        return _cmd(session_id)

    # ── 辅助方法 ────────────────────────────────────────

    @staticmethod
    def generate_id() -> str:
        """生成唯一的会话 ID。"""
        from ...chat_msgs import generate_id as _gen
        return _gen()




class JsonFileCheckpoint(CheckpointPort):
    """基于 .chat/msg_list/_checkpoint.json 文件的断点实现。

    包装 src/checkpoint 模块中的函数，通过延迟导入避免模块加载时触发文件 IO。
    """

    def save(
        self,
        messages: list[dict],
        model: str,
        task_description: str = "",
    ) -> None:
        from ...checkpoint import save_checkpoint
        save_checkpoint(messages, model, task_description)

    def load(self) -> dict | None:
        from ...checkpoint import load_checkpoint
        return load_checkpoint()

    def clear(self) -> None:
        from ...checkpoint import clear_checkpoint
        clear_checkpoint()

    def exists(self) -> bool:
        from ...checkpoint import has_checkpoint
        return has_checkpoint()

    def get_info(self) -> dict | None:
        from ...checkpoint import get_checkpoint_info
        return get_checkpoint_info()

    # ── 异步包装方法 ─────────────────────────────────

    async def async_save(
        self,
        messages: list[dict],
        model: str,
        task_description: str = "",
    ) -> None:
        """异步保存断点，使用 to_thread 避免阻塞事件循环。"""
        import asyncio
        await asyncio.to_thread(self.save, messages, model, task_description)

    async def async_load(self) -> dict | None:
        """异步加载断点数据。"""
        import asyncio
        return await asyncio.to_thread(self.load)

    async def async_clear(self) -> None:
        """异步清除断点。"""
        import asyncio
        await asyncio.to_thread(self.clear)

    async def async_exists(self) -> bool:
        """异步检查断点是否存在。"""
        import asyncio
        return await asyncio.to_thread(self.exists)
