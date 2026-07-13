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

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "rm",
                "description": (
                    "删除文件或目录。支持文件沙盒撤回（已删除的文件可通过沙盒机制恢复）。"
                    "删除目录时必须设置 recursive=True（类似 rm -r）。"
                    "删除前会先读取文件内容保存到沙盒，以便后续撤回。"
                    "\n\n"
                    "【删除前必查引用（强制）】删除任何文件/目录前，"
                    "必须先 search 全量搜索所有引用并逐处确认，"
                    "确保不会破坏其他模块的依赖关系。"
                    "\n\n"
                    "参数行为摘要："
                    "\n- path 与 recursive 配合使用：path 指定要删除的文件或目录，recursive 控制目录删除行为"
                    "\n- 删除文件时 recursive 参数无效，始终直接删除单文件"
                    "\n- 删除目录时：recursive=False（默认）→ 非空目录拒绝删除并返回提示；recursive=True → 递归删除整个目录树"
                    "\n- 符号链接：删除链接本身，不删除其指向的目标文件"
                    "\n\n"
                    "【边界信息】"
                    "\n- 路径安全校验：拒绝路径穿越攻击（如../../etc/passwd）"
                    "\n- 路径不存在时返回明确信息「路径不存在: xxx」，非错误"
                    "\n- 删除目录时recursive默认为False：非空目录未设recursive=True时返回提示并拒绝"
                    "\n- 删除目录时递归收集所有文件内容，逐个记录沙盒后执行shutil.rmtree"
                    "\n- 符号链接：删除链接本身而非其指向的目标"
                    "\n- 权限不足时返回PermissionError"
                    "\n- 沙盒机制：删除前保存文件内容，可通过沙盒恢复撤回"
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

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        path = arguments.get("path", "")
        display = f"'{cls._sanitize_display(path)}'"
        if arguments.get("recursive"):
            display += " -r"
        return display

    def __init__(self, path: str, recursive: bool = False):
        super().__init__()
        validate_path_security(path)
        self.path = path
        self.recursive = recursive

    # ── 执行 ──

    async def execute(self) -> str:
        """异步执行删除逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_remove():
            validate_path_security(self.path)

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
