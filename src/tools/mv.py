from __future__ import annotations

import asyncio
import os
import shutil
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
                    "移动文件或目录（source → destination），支持跨文件系统。"
                    "目标已存在将被覆盖。返回：移动结果；失败以 ( 开头。路径穿越被拒绝；沙盒可撤回。"
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
            if not await async_file_exists(self.source):
                return f"(源路径不存在: {self.source})"

            dst_exists = await async_file_exists(self.destination)
            if dst_exists:
                same = await asyncio.to_thread(os.path.samefile, self.source, self.destination)
                if same:
                    return f"(源和目标路径相同: {self.source})"

            src_is_file = await async_is_link(self.source) or await asyncio.to_thread(os.path.isfile, self.source)
            src_is_dir = await asyncio.to_thread(os.path.isdir, self.source)

            # ★ 修复（review 方向）：目标为已存在目录时 shutil.move 会把源
            #   **移入该目录内**（实际结果为 dst/basename(source)），修复前
            #   沙盒仍按 destination 扁平记录（undo 回滚路径错误、覆盖提示
            #   与实际落点不符）。effective_dst 为真实落点。
            dst_is_dir = dst_exists and await asyncio.to_thread(os.path.isdir, self.destination)
            if dst_is_dir:
                effective_dst = os.path.join(
                    self.destination, os.path.basename(os.path.normpath(self.source)),
                )
            else:
                effective_dst = self.destination
            effective_exists = await async_file_exists(effective_dst)
            if effective_exists:
                same_eff = await asyncio.to_thread(os.path.samefile, self.source, effective_dst)
                if same_eff:
                    return f"(源和目标路径相同: {self.source})"

            if src_is_file:
                source_content = await async_read_file_content(self.source)
                dst_content = await async_read_file_content(effective_dst) if effective_exists else None
                await asyncio.to_thread(shutil.move, self.source, self.destination)
                # ★ 跨文件系统一致性检查：shutil.move 在跨文件系统时使用 copy+delete 策略，
                #    若 copy 成功但 delete 失败，源文件仍存在。此时沙盒不应记录源删除。
                source_still_exists = await async_file_exists(self.source)
                if source_still_exists:
                    # 跨文件系统：复制成功但源删除失败，仅记录目标写入
                    await async_record_sandbox(effective_dst, dst_content, source_content, self.name)
                    return f"(移动部分成功: 跨文件系统复制完成但源文件删除失败: {self.source})"
                await async_record_sandbox(self.source, source_content, None, self.name)
                await async_record_sandbox(effective_dst, dst_content, source_content, self.name)
                action = "覆盖" if effective_exists else "移动"
                return f"{action}成功: {self.source} -> {effective_dst}"

            elif src_is_dir:
                src_files = await async_collect_files(self.source)
                # ★ 先读取所有源文件内容（move 前必须读，move 后源路径已不存在）
                src_contents = {}
                for fp in src_files:
                    src_contents[fp] = await async_read_file_content(fp)

                dst_existing = {}
                if effective_exists:
                    eff_is_dir = await asyncio.to_thread(os.path.isdir, effective_dst)
                    if eff_is_dir:
                        dst_files = await async_collect_files(effective_dst)
                        for fp in dst_files:
                            dst_existing[fp] = await async_read_file_content(fp)

                await asyncio.to_thread(shutil.move, self.source, self.destination)

                # ★ 跨文件系统一致性检查：shutil.move 在跨文件系统时使用 copy+delete 策略，
                #    若 copy 成功但 delete 失败，源目录仍存在。此时沙盒不应记录源删除。
                source_still_exists = await async_file_exists(self.source)
                if source_still_exists:
                    # 跨文件系统：复制成功但源删除失败，仅记录目标写入
                    await async_record_directory_files(
                        self.source, effective_dst, src_files, self.name,
                        dst_existing or None, source_contents=src_contents,
                        source_deleted=False,
                    )
                    return f"(移动部分成功: 跨文件系统复制完成但源目录删除失败: {self.source})"

                await async_record_directory_files(
                    self.source, effective_dst, src_files, self.name,
                    dst_existing or None, source_contents=src_contents,
                )
                # ★ 源目录自身：content_before="" 表示目录存在，content_after=None 表示被移动后删除
                await async_record_sandbox(self.source, "", None, self.name, record_type="directory")
                return f"移动成功: {self.source} -> {effective_dst} ({len(src_files)}个文件)"

            else:
                return f"(不支持的路径类型: {self.source})"

        return await self._run_with_error_handling(_do_move)

    def _get_operation_desc(self) -> str:
        return f"mv: '{self.source}' -> '{self.destination}'"

    # --- 显示 ---