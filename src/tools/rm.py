from __future__ import annotations

import asyncio
import os
import shutil

from .file_base import FileSystemToolBase
from .file_ops import (
    validate_path_security, async_file_exists, async_collect_files,
    async_read_file_content, async_remove_file, async_is_link,
    async_record_sandbox, async_collect_dirs, async_record_sandbox_batch,
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
                    "删除文件或目录；文件与空目录可直接删除，非空目录必须 recursive=true"
                    "（递归删除全部内容）。删除后记录到沙盒，可撤销。"
                    "例：rm({\"path\": \"build\", \"recursive\": true})"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {
                            "type": "string",
                            "description": "要删除的路径（如 build）"
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "是否递归删除非空目录（默认 false）",
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
                dirs = await async_collect_dirs(self.path)
                contents = {}
                for fp in files:
                    contents[fp] = await async_read_file_content(fp)

                changes = [
                    (fp, contents[fp], None, self.name, "file") for fp in files
                ]
                # 子目录（含空的、无文件的子目录）+ 目录自身：
                # content_before="" 表示目录存在，content_after=None 表示被删除
                changes.extend(
                    (d, "", None, self.name, "directory") for d in dirs
                )
                changes.append((self.path, "", None, self.name, "directory"))
                await async_record_sandbox_batch(changes)

                await asyncio.to_thread(shutil.rmtree, self.path)
                return f"删除成功: {self.path} ({len(files)}个文件)"

            else:
                return f"(不支持的路径类型: {self.path})"

        return await self._run_with_error_handling(_do_remove)

    def _get_operation_desc(self) -> str:
        return f"rm: {self.path}{' -r' if self.recursive else ''}"
