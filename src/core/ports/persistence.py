"""会话持久化和断点管理的端口定义。

包含两个抽象接口（PersistencePort / CheckpointPort）。
适配器实现已移至 src.core.adapters.persistence。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

# ── 端口定义 ──────────────────────────────────────────────

class PersistencePort(ABC):
    """会话持久化端口 —— 管理对话的保存、加载、列举与删除。"""

    @abstractmethod
    def save_session(
        self,
        messages: list[dict],
        model: str,
        session_id: str | None = None,
        subagents: list | None = None,
        session_log: list | None = None,
        title_snapshot: dict | None = None,
    ) -> str:
        """保存对话并返回会话 ID。

        Args:
            messages: 消息列表（内部自动过滤 system）
            model: 模型名称
            session_id: 指定 ID，None 时自动生成
            subagents: SubAgent 任务记录列表（含完整聊天信息），可选
            session_log: 会话事件日志快照（仅追加事实源），可选
            title_snapshot: 当前标题快照（标题服务当前接受值），可选——
                传入时直接采用并连同来源落盘；None 时按「已有文件标题优先 →
                确定性回退」决定（旧调用方行为）。
        """

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
        """只更新会话文件的标题字段（可选能力，默认不支持返回 False）。

        标题生成完成后即时落盘使用：会话可能还没到 30 秒周期保存，标题却已经
        生成完毕——写入 ``title`` / ``title_source`` / ``title_model`` 等字段，
        并保持消息内容不变。实现方须与 ``save_session`` 串行化同一会话文件的
        写入（避免 read-modify-write 互相覆盖）。
        """
        return False

    @abstractmethod
    def load_session(self, session_id: str) -> dict | None:
        """加载指定 ID 的会话数据，不存在时返回 None。"""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """列出所有已保存的会话摘要（按保存时间降序）。"""

    def iter_sessions(self):
        """**逐条**产出会话摘要（流式，可选能力；默认基于 ``list_sessions``）。

        实现方可覆盖为真流式实现（每解析出一条会话即产出，调用方/界面可
        增量显示「加载成功一条即增加一条」）；默认实现等价于遍历
        ``list_sessions()`` 结果。
        """
        yield from (self.list_sessions() or ())

    @abstractmethod
    def delete_session(self, session_id: str) -> bool:
        """删除指定会话，成功返回 True，不存在 / 失败返回 False。"""

    def rename_session(self, session_id: str, new_title: str) -> bool:
        """重命名指定会话的标题（可选能力，默认不支持返回 False）。

        实现方可覆盖；调用方（如会话浏览器视图）在返回 False 时提示不支持。
        """
        return False

    def load_session_head_messages(self, session_id: str, max_messages: int) -> list | None:
        """读取会话**前若干条消息**（可选能力，默认不支持返回 None）。

        供会话浏览器预览行使用：只需前若干条消息，无需整读会话文件
        （大文件场景显著降低首屏耗时）。返回 None 表示不支持/无法轻量读取，
        调用方回退 ``load_session`` 全量读取。
        """
        return None

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