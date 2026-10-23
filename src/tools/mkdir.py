from __future__ import annotations

import asyncio
import os

from .file_base import FileSystemToolBase
from .file_ops import (
    validate_path_security, async_file_exists,
    acquire_sandbox_locks, tool_agent_id,
)


class MkdirFunc(FileSystemToolBase):
    name = "mkdir"
    _action_verb = "创建"
    _display_flag = "-p"
    _display_flag_arg = "parents"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "mkdir",
                "description": (
                    "创建目录；父目录不存在时需 parents=true 才会递归创建。"
                    "目录已存在时返回提示而不报错，路径已被同名文件占用则失败；"
                    "创建记录到沙盒可撤销。"
                    "例：mkdir({\"path\": \"src/new_mod\", \"parents\": true})"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要创建的目录路径（如 src/new_mod）"
                        },
                        "parents": {
                            "type": "boolean",
                            "description": "是否递归创建缺失的父目录（默认 false）",
                            "default": False
                        }
                    },
                    "required": ["path"]
                }
            }
        }

    def __init__(self, path: str, parents: bool = False):
        super().__init__()
        # 展开 ~（plan agent 在 ~/.chat/plan/ 下创建目录）
        path = os.path.expanduser(path)
        validate_path_security(path)
        self.path = path
        self.parents = parents

    # ── 执行 ──

    async def execute(self) -> str:
        """异步执行目录创建逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_mkdir():
            # plan agent 路径白名单校验：仅允许创建 ~/.chat/plan/ 下的目录
            # （与 file_base / base.can_use 共用 get_plan_allowed_dir +
            #  is_path_within_dir，realpath 解析符号链接防绕过）
            agent_type_val = getattr(self, 'agent_type', None)
            if agent_type_val == 'plan':
                from .file_ops import get_plan_allowed_dir, is_path_within_dir
                allowed_dir = get_plan_allowed_dir()
                if not is_path_within_dir(self.path, allowed_dir):
                    return f"(创建失败: plan agent 只能在 {allowed_dir} 下创建目录。当前路径: {self.path}（解析后: {os.path.realpath(self.path)}）)"

            # 检查路径是否已存在
            if await async_file_exists(self.path):
                # 检查是否是一个已存在的文件（非目录）
                is_dir = await asyncio.to_thread(os.path.isdir, self.path)
                if not is_dir:
                    return f"(创建失败: {self.path} 已存在且不是目录)"
                return f"目录已存在: {self.path}"

            parents = self.parents

            # 非递归模式下，检查父目录是否存在
            if not parents:
                parent_dir = os.path.dirname(self.path)
                if parent_dir and not await async_file_exists(parent_dir):
                    return f"(父目录不存在，如需递归创建请设置 parents=True: {self.path})"

            # ── 沙盒 Agent 写锁：待创建目录（目录锁覆盖后续子孙路径）
            # 放在存在性 / 父目录校验之后，避免为「目录已存在」等无写入
            # 场景留下无意义的锁。
            lock_error = acquire_sandbox_locks(self.agent, [self.path], self.name)
            if lock_error:
                return lock_error

            # 执行目录创建 + 沙盒记录（含隐式创建的父目录）
            from .file_ops import async_makedirs_and_record
            await async_makedirs_and_record(
                self.path, self.name, tool_agent_id(self.agent),
            )

            return f"创建成功: {self.path}"

        return await self._run_with_error_handling(_do_mkdir)

    def _get_operation_desc(self) -> str:
        return f"mkdir: {self.path}{' -p' if self.parents else ''}"
