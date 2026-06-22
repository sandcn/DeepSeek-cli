from __future__ import annotations

import asyncio
import os
import shutil
from .base import tool_metadata
from .file_base import FileSystemToolBase
from .file_ops import (
    validate_path_security, async_file_exists, async_read_file_content,
    async_collect_files, async_is_link, async_makedirs,
    async_record_directory_files,
    async_record_sandbox,
)


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    description="移动文件或目录",
)
class MvFunc(FileSystemToolBase):
    name = "mv"
    _action_verb = "移动"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "mv",
                "description": (
                    "移动文件或目录（source → destination）。支持文件沙盒撤回（可通过沙盒机制恢复）。"
                    "使用 shutil.move 执行移动操作，支持跨文件系统移动。"
                    "\n\n"
                    "【边界信息】"
                    "\n- 路径安全校验：拒绝路径穿越攻击（如../../etc/passwd）"
                    "\n- 源路径不存在时返回明确信息「源路径不存在: xxx」"
                    "\n- 目标路径已存在时将被覆盖（shutil.move 行为），请谨慎操作"
                    "\n- 目录移动：递归收集并记录所有文件内容到沙盒后执行移动"
                    "\n- 符号链接：移动链接本身而非其指向的目标"
                    "\n- 权限不足时返回 PermissionError"
                    "\n- 沙盒机制：移动前保存源文件内容和目标位置的原有内容，可通过沙盒撤回"
                    "\n- 支持跨文件系统移动（会自动使用 copy+delete 策略）"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "源文件或目录路径（必填）。源路径必须存在，支持文件或目录；符号链接移动链接本身而非其指向的目标。"
                        },
                        "destination": {
                            "type": "string",
                            "description": "目标文件或目录路径（必填）。如果目标已存在，将被 shutil.move 覆盖；支持跨文件系统移动（自动使用 copy+delete 策略）。"
                        }
                    },
                    "required": ["source", "destination"]
                }
            }
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        source = arguments.get("source", "")
        destination = arguments.get("destination", "")
        display = f"'{cls._sanitize_display(source)}' -> '{cls._sanitize_display(destination)}'"
        return display

    def __init__(self, source: str, destination: str):
        super().__init__()
        validate_path_security(source)
        validate_path_security(destination)
        self.source = source
        self.destination = destination

    # --- 执行 ---

    async def execute(self) -> str:
        """异步执行移动逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_move():
            validate_path_security(self.source)
            validate_path_security(self.destination)

            if not await async_file_exists(self.source):
                return f"(源路径不存在: {self.source})"

            dst_exists = await async_file_exists(self.destination)
            if dst_exists:
                same = await asyncio.to_thread(os.path.samefile, self.source, self.destination)
                if same:
                    return f"(源和目标路径相同: {self.source})"

            src_is_file = await async_is_link(self.source) or await asyncio.to_thread(os.path.isfile, self.source)
            src_is_dir = await asyncio.to_thread(os.path.isdir, self.source)

            if src_is_file:
                source_content = await async_read_file_content(self.source)
                dst_content = await async_read_file_content(self.destination) if dst_exists else None
                await asyncio.to_thread(shutil.move, self.source, self.destination)
                await async_record_sandbox(self.source, source_content, None, self.name)
                await async_record_sandbox(self.destination, dst_content, source_content, self.name)
                action = "覆盖" if dst_exists else "移动"
                return f"{action}成功: {self.source} -> {self.destination}"

            elif src_is_dir:
                src_files = await async_collect_files(self.source)
                # ★ 先读取所有源文件内容（move 前必须读，move 后源路径已不存在）
                src_contents = {}
                for fp in src_files:
                    src_contents[fp] = await async_read_file_content(fp)

                dst_existing = {}
                if dst_exists:
                    dst_dir = await asyncio.to_thread(os.path.isdir, self.destination)
                    if dst_dir:
                        dst_files = await async_collect_files(self.destination)
                        for fp in dst_files:
                            dst_existing[fp] = await async_read_file_content(fp)

                await asyncio.to_thread(shutil.move, self.source, self.destination)

                await async_record_directory_files(
                    self.source, self.destination, src_files, self.name,
                    dst_existing or None, source_contents=src_contents,
                )
                # ★ 源目录自身：content_before="" 表示目录存在，content_after=None 表示被移动后删除
                await async_record_sandbox(self.source, "", None, self.name, record_type="directory")
                return f"移动成功: {self.source} -> {self.destination} ({len(src_files)}个文件)"

            else:
                return f"(不支持的路径类型: {self.source})"

        return await self._run_with_error_handling(_do_move)

    def _get_operation_desc(self) -> str:
        return f"mv: '{self.source}' -> '{self.destination}'"

    # --- 显示 ---