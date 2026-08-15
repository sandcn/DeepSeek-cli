from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path
from .base import tool_metadata
from .file_base import FileSystemToolBase
from .file_ops import (
    validate_path_security, async_file_exists, async_read_file_content,
    async_collect_files, async_is_link,
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
    tool_category="write",
    description="复制文件或目录",
)
class CpFunc(FileSystemToolBase):
    name = "cp"
    _action_verb = "复制"

    @classmethod
    def to_tool_schema(cls) -> dict:
        return {
            "type": "function",
            "function": {
                "name": "cp",
                "description": (
                    "复制文件或目录（source → destination）。"
                    "复制目录必须设 recursive=true；目标已存在则覆盖（文件保留元数据）。"
                    "返回：复制结果；失败以 ( 开头。路径穿越被拒绝；沙盒可撤回。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "source": {
                            "type": "string",
                            "description": "源路径（文件或目录）。源路径必须存在，支持文件或目录类型；不支持符号链接（返回提示信息）。"
                        },
                        "destination": {
                            "type": "string",
                            "description": "目标路径。目标已存在时直接覆盖（文件用 shutil.copy2，目录用 shutil.copytree）。目录复制时需 recursive=True。"
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "是否递归复制目录。复制目录时必传 true，否则返回拒绝提示信息。如果 source 是目录，必须为 true。",
                            "default": False
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
        display = f"'{cls._sanitize_display(source)}' \u2192 '{cls._sanitize_display(destination)}'"
        if arguments.get("recursive"):
            display += " -r"
        return display

    def __init__(self, source: str, destination: str, recursive: bool = False):
        super().__init__()
        validate_path_security(source)
        validate_path_security(destination)
        self.source = source
        self.destination = destination
        self.recursive = recursive

    def _build_dest_path(self, source_root: str, file_path: str) -> str:
        try:
            rel_path = Path(file_path).relative_to(Path(source_root))
        except ValueError:
            # source_root 不是 file_path 的前缀（如跨驱动器、符号链接等）
            try:
                rel_path = Path(os.path.relpath(file_path, source_root))
            except ValueError:
                rel_path = Path(os.path.basename(file_path))
        return str(Path(self.destination) / rel_path)

    def _get_operation_desc(self) -> str:
        return f"cp: '{self.source}' -> '{self.destination}'{' -r' if self.recursive else ''}"

    async def execute(self) -> str:
        """异步执行复制逻辑，所有阻塞操作使用 asyncio.to_thread 包装"""

        async def _do_copy():
            if not await async_file_exists(self.source):
                return f"(源路径不存在: {self.source})"

            if await async_is_link(self.source):
                return f"(不支持复制符号链接: {self.source})"

            src_is_file = await asyncio.to_thread(os.path.isfile, self.source)
            src_is_dir = await asyncio.to_thread(os.path.isdir, self.source)

            # 复制文件
            if src_is_file:
                # ★ 修复（review 方向）：目标为已存在目录时 shutil.copy2 会把
                #   文件复制**进该目录内**（实际落点 dst/basename(source)），
                #   修复前沙盒按 destination（目录本身）记录文件内容，undo
                #   回滚路径错误。effective_dst 为真实落点。
                if await asyncio.to_thread(os.path.isdir, self.destination):
                    effective_dst = os.path.join(
                        self.destination, os.path.basename(os.path.normpath(self.source)),
                    )
                else:
                    effective_dst = self.destination
                dest_content_before = await async_read_file_content(effective_dst) if await async_file_exists(effective_dst) else None
                source_content = await async_read_file_content(self.source)

                dest_dir = os.path.dirname(effective_dst)
                if dest_dir:
                    from .file_ops import async_makedirs_and_record
                    await async_makedirs_and_record(dest_dir, self.name)

                await asyncio.to_thread(shutil.copy2, self.source, self.destination)
                await async_record_sandbox(effective_dst, dest_content_before, source_content, self.name)
                return f"复制成功: {self.source} \u2192 {effective_dst}"

            # 复制目录
            elif src_is_dir:
                if not self.recursive:
                    return f"(源路径是目录，如需复制目录请设置 recursive=True: {self.source})"

                source_files = await async_collect_files(self.source)

                # 构建目标位置原有内容映射
                dst_existing = {}
                for sf in source_files:
                    df = self._build_dest_path(self.source, sf)
                    if await async_file_exists(df):
                        dst_existing[df] = await async_read_file_content(df)
                    else:
                        dst_existing[df] = None

                await async_record_directory_files(
                    self.source, self.destination, source_files, self.name, dst_existing,
                    source_deleted=False,
                )

                if await async_file_exists(self.destination):
                    await asyncio.to_thread(
                        shutil.copytree, self.source, self.destination,
                        dirs_exist_ok=True, copy_function=shutil.copy2,
                    )
                else:
                    await asyncio.to_thread(
                        shutil.copytree, self.source, self.destination,
                        copy_function=shutil.copy2,
                    )

                return f"复制成功: {self.source} \u2192 {self.destination} ({len(source_files)}个文件)"

            else:
                return f"(不支持的源路径类型: {self.source})"

        return await self._run_with_error_handling(_do_copy)

    async def display(self) -> str:
        return await super().display()
