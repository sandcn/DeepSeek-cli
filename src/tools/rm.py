from __future__ import annotations

import asyncio
import os
import shutil

from .base import tool_metadata
from .file_base import FileSystemToolBase
from .file_ops import (
    validate_path_security, async_file_exists, async_collect_files,
    async_read_file_content, async_remove_file, async_is_link,
    async_record_sandbox,
)


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="write",
    description="删除文件或目录",
)
class RmFunc(FileSystemToolBase):
    name = "rm"
    _action_verb = "删除"
    _display_flag = "-r"
    _display_flag_arg = "recursive"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "rm",
                "description": (
                    "删除文件或目录。删除非空目录必须设 recursive=true。"
                    "删除前必须先 search 全量搜索引用，确认无依赖关系。"
                    "返回：删除结果；失败以 ( 开头。路径穿越被拒绝；沙盒可撤回。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要删除的文件或目录路径（支持相对路径和绝对路径）。路径不存在时返回明确信息「路径不存在: xxx」，非错误。路径安全校验会拒绝路径穿越攻击（如../../etc/passwd）。"
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "是否递归删除目录。如果 path 是目录且非空，必须传 true（类似 rm -r），否则拒绝删除并返回提示。默认为 false 防止误删非空目录。删除时递归收集所有文件内容逐一记录沙盒后执行 shutil.rmtree。",
                            "default": False
                        }
                    },
                    "required": ["path"]
                }
            }
        }

    def __init__(self, path: str, recursive: bool = False):
        super().__init__()
        validate_path_security(path)
        self.path = path
        self.recursive = recursive

    # ── 执行 ──

    async def execute(self) -> str:
        """异步执行删除逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_remove():
            if not await async_file_exists(self.path):
                return f"(路径不存在: {self.path})"

            if await async_is_link(self.path) or await asyncio.to_thread(os.path.isfile, self.path):
                # ── 删除文件 ──
                content = await async_read_file_content(self.path)
                await async_record_sandbox(self.path, content, None, self.name)
                await async_remove_file(self.path)
                return f"删除成功: {self.path}"

            elif await asyncio.to_thread(os.path.isdir, self.path):
                # ── 删除目录 ──
                if not self.recursive:
                    entries = await asyncio.to_thread(os.listdir, self.path)
                    if not entries:
                        # 空目录：直接删除
                        await async_record_sandbox(self.path, "", None, self.name, record_type="directory")
                        await asyncio.to_thread(os.rmdir, self.path)
                        return f"删除成功: {self.path}"
                    else:
                        return f"(目录非空，如需删除目录请设置 recursive=True: {self.path})"

                files = await async_collect_files(self.path)
                contents = {}
                for fp in files:
                    contents[fp] = await async_read_file_content(fp)

                for fp in files:
                    await async_record_sandbox(fp, contents[fp], None, self.name)
                # 目录自身：content_before="" 表示目录存在，content_after=None 表示被删除
                await async_record_sandbox(self.path, "", None, self.name, record_type="directory")

                await asyncio.to_thread(shutil.rmtree, self.path)
                return f"删除成功: {self.path} ({len(files)}个文件)"

            else:
                return f"(不支持的路径类型: {self.path})"

        return await self._run_with_error_handling(_do_remove)

    def _get_operation_desc(self) -> str:
        return f"rm: {self.path}{' -r' if self.recursive else ''}"
