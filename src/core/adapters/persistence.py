"""持久化端口适配器 — JsonFilePersistence、JsonFileCheckpoint"""

from __future__ import annotations

from ..ports.persistence import PersistencePort, CheckpointPort

class JsonFilePersistence(PersistencePort):
    """基于 ~/.chat/msg_list/*.json 文件的会话持久化实现。

    包装 src/chat_msgs 模块中的函数，通过延迟导入避免模块加载时触发文件 IO。
    """

    def save_session(
        self,
        messages: list[dict],
        model: str,
        session_id: str | None = None,
        subagents: list | None = None,
        session_log: list | None = None,
        title_snapshot: dict | None = None,
    ) -> str:
        from ...chat_msgs import save_session as _save
        return _save(
            messages, model, session_id, subagents, session_log, title_snapshot,
        )

    def update_session_title(
        self,
        session_id: str,
        *,
        title: str,
        source: str = "",
        model: str = "",
        message_seqs: list[int] | None = None,
        updated_at: float | None = None,
    ) -> bool:
        """只更新会话标题字段（标题生成完成后的即时落盘）。"""
        from ...chat_msgs import update_session_title as _update
        return _update(
            session_id,
            title=title,
            source=source,
            model=model,
            message_seqs=message_seqs,
            updated_at=updated_at,
        )

    def load_session(self, session_id: str) -> dict | None:
        from ...chat_msgs import load_session as _load
        return _load(session_id)

    def load_session_head_messages(self, session_id: str, max_messages: int) -> list | None:
        """读取会话前若干条消息（会话浏览器预览的轻量路径）。"""
        from ...chat_msgs import load_session_head_messages as _head
        return _head(session_id, max_messages)

    def list_sessions(self) -> list[dict]:
        from ...chat_msgs import list_sessions as _list
        return _list()

    def iter_sessions(self):
        """逐条产出会话摘要（流式：每解析出一条即刻产出，界面可增量显示）。"""
        from ...chat_msgs import iter_sessions as _iter
        return _iter()

    def delete_session(self, session_id: str) -> bool:
        from ...chat_msgs import delete_session as _delete
        return _delete(session_id)

    def rename_session(self, session_id: str, new_title: str) -> bool:
        from ...chat_msgs import rename_session as _rename
        return _rename(session_id, new_title)

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
    """基于 ~/.chat/_checkpoint.json 文件的断点实现。

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