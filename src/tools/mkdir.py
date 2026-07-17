from __future__ import annotations

import asyncio
import os
from .base import tool_metadata
from .file_base import FileSystemToolBase
from .file_ops import validate_path_security, async_file_exists


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="write",
    description="创建目录",
)
class MkdirFunc(FileSystemToolBase):
    name = "mkdir"
    _action_verb = "创建"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "mkdir",
                "description": (
                    "创建目录。支持文件沙盒撤回（创建的目录可通过沙盒机制恢复）。"
                    "默认只创建单层目录，设置 parents=True 可递归创建父目录（类似 mkdir -p）。"
                    "\n\n"
                    "参数行为摘要："
                    "\n- path 参数指定要创建的目录路径"
                    "\n- parents 控制是否递归创建父目录"
                    "\n- 目录已存在时不会报错，返回成功信息"
                    "\n\n"
                    "【边界信息】"
                    "\n- 路径安全校验：拒绝路径穿越攻击（如../../etc/passwd）"
                    "\n- 父目录不存在且 parents=False（默认）时，返回提示信息"
                    "\n- 目录已存在时视为成功（不报错）"
                    "\n- 路径中的文件部分已存在（如 path 指向一个已有文件）时返回错误提示"
                    "\n- 沙盒机制：记录创建的目录路径到沙盒，可通过沙盒撤回"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要创建的目录路径（支持相对路径和绝对路径）。路径安全校验会拒绝路径穿越攻击（如../../etc/passwd）。"
                        },
                        "parents": {
                            "type": "boolean",
                            "description": "是否递归创建父目录（类似 mkdir -p）。为 true 时自动创建所有不存在的父目录；为 false（默认）时父目录不存在则返回提示。",
                            "default": False
                        }
                    },
                    "required": ["path"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path", "")
        display = f"'{cls._sanitize_display(path)}'"
        if arguments.get("parents"):
            display += " -p"
        return display

    def __init__(self, path: str, parents: bool = False):
        super().__init__()
        validate_path_security(path)
        self.path = path
        self.parents = parents

    # ── 执行 ──

    async def execute(self) -> str:
        """异步执行目录创建逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_mkdir():
            validate_path_security(self.path)

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

            # 执行目录创建 + 沙盒记录（含隐式创建的父目录）
            from .file_ops import async_makedirs_and_record
            await async_makedirs_and_record(self.path, self.name)

            return f"创建成功: {self.path}"

        return await self._run_with_error_handling(_do_mkdir)

    def _get_operation_desc(self) -> str:
        return f"mkdir: {self.path}{' -p' if self.parents else ''}"
