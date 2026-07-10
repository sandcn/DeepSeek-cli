#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""文件修改记录

记录大模型对文件的单次修改操作，支持应用、回滚和异步操作。
"""

import os
import time
import threading
import contextlib
import aiofiles
import aiofiles.os
import asyncio
from typing import Optional

from ..core.ports.output import get_default_output_port

_out = get_default_output_port()


class FileChangeRecord:
    """文件修改记录"""

    def __init__(self, file_path: str, content_before: Optional[str],
                 content_after: Optional[str], message_index: int,
                 timestamp: float = None, tool_name: str = "write_file",
                 record_type: str = "file"):
        """
        初始化文件修改记录

        Args:
            file_path: 文件路径
            content_before: 修改前的内容，None表示路径不存在
            content_after: 修改后的内容，None表示路径被删除；对目录用 "" 表示存在
            message_index: 关联的消息索引（在messages列表中的位置）
            timestamp: 时间戳，默认为当前时间
            tool_name: 工具名称
            record_type: 记录类型，"file"（默认）或 "directory"
        """
        self.file_path = file_path
        self.content_before = content_before
        self.content_after = content_after
        self.message_index = message_index
        self.timestamp = timestamp or time.time()
        self.tool_name = tool_name
        self.record_type = record_type  # "file" | "directory"

        # ─── 锁层次（两锁设计）──────────────────────────────────────
        # 本实例使用两个锁，锁获取顺序固定，禁止调换：
        #
        #   同步路径（apply/revert）:  _cross_lock               （唯一锁）
        #   异步路径（apply_async/revert_async）: _async_lock → _cross_lock
        #
        # 1. _cross_lock (threading.Lock) — 跨执行上下文的全局互斥锁
        #    - 同步方法直接 with 获取
        #    - 异步方法通过 _cross_lock_async() 异步 context manager 获取
        #    - 使用普通 Lock（非 RLock），因为应用中不存在递归获取场景
        #    - 注意：若未来 apply/revert 引入相互调用，需升级为 RLock
        # 2. _async_lock (asyncio.Lock) — 协程间互斥锁
        #    - 只用于异步方法，轻量级保护协程间串行
        #    - 始终在 _cross_lock 之前获取（异步路径固定锁顺序）
        #    - 在 __init__ 中直接创建，消除懒初始化的 check-then-act 竞态条件
        #
        # 死锁预防：同步路径只有单个锁（无嵌套），异步路径固定 from outside in
        # 的顺序（inner _async_lock → outer _cross_lock），不会出现循环等待。
        # ──────────────────────────────────────────────────────────
        self._cross_lock = threading.Lock()  # 跨执行上下文互斥锁
        self._async_lock = asyncio.Lock()    # 协程间互斥锁（直接初始化，无竞态）

    def __repr__(self):
        return (f"FileChangeRecord(file_path={self.file_path!r}, "
                f"message_index={self.message_index}, "
                f"tool_name={self.tool_name!r})")

    @contextlib.asynccontextmanager
    async def _cross_lock_async(self):
        """异步上下文管理器：安全获取/释放 _cross_lock（避免手动 acquire/release）"""
        await asyncio.to_thread(self._cross_lock.acquire)
        try:
            yield
        finally:
            self._cross_lock.release()

    def get_change_type(self) -> str:
        """获取修改类型"""
        is_dir = self.record_type == "directory"
        if self.content_before is None and self.content_after is not None:
            return "新建目录" if is_dir else "新建文件"
        elif self.content_before is not None and self.content_after is None:
            return "删除目录" if is_dir else "删除文件"
        elif self.content_before == self.content_after:
            return "无变化"
        else:
            return "修改目录" if is_dir else "修改文件"

    # ── 核心写入逻辑（同步/异步）─────────────────────────

    def _do_apply(self, content: Optional[str]) -> bool:
        """核心写入逻辑：将文件设置为 content 指定的状态。"""
        with self._cross_lock:
            try:
                if content is None:
                    if os.path.exists(self.file_path):
                        if self.record_type == "directory" or os.path.isdir(self.file_path):
                            import shutil
                            shutil.rmtree(self.file_path)
                        else:
                            os.remove(self.file_path)
                    return True
                elif self.record_type == "directory":
                    os.makedirs(self.file_path, exist_ok=True)
                    return True
                else:
                    parent = os.path.dirname(self.file_path)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    with open(self.file_path, 'w', encoding='utf-8', errors='replace') as f:
                        f.write(content)
                    return True
            except Exception as e:
                _out.write(f"沙盒恢复失败: {self.file_path} - {e}", level="error", source="sandbox")
                return False

    async def _do_apply_async(self, content: Optional[str]) -> bool:
        """异步核心写入逻辑：将文件设置为 content 指定的状态。"""
        async with self._async_lock:
            async with self._cross_lock_async():
                try:
                    if content is None:
                        try:
                            if self.record_type == "directory":
                                import shutil
                                await asyncio.to_thread(shutil.rmtree, self.file_path)
                            else:
                                await aiofiles.os.remove(self.file_path)
                        except FileNotFoundError:
                            pass
                        return True
                    elif self.record_type == "directory":
                        await asyncio.to_thread(os.makedirs, self.file_path, exist_ok=True)
                        return True
                    else:
                        parent = os.path.dirname(self.file_path)
                        if parent:
                            await asyncio.to_thread(os.makedirs, parent, exist_ok=True)
                        async with aiofiles.open(self.file_path, 'w', encoding='utf-8', errors='replace') as f:
                            await f.write(content)
                        return True
                except Exception as e:
                    _out.write(f"沙盒恢复失败: {self.file_path} - {e}", level="error", source="sandbox")
                    return False

    # ── 公开接口（薄委托）─────────────────────────────────

    def apply(self) -> bool:
        """应用此修改（将文件设置为content_after状态）"""
        return self._do_apply(self.content_after)

    def revert(self) -> bool:
        """回滚此修改（将文件恢复到content_before状态）"""
        return self._do_apply(self.content_before)

    async def apply_async(self) -> bool:
        """异步应用修改（将文件设置为 content_after 状态）"""
        return await self._do_apply_async(self.content_after)

    async def revert_async(self) -> bool:
        """异步回滚修改（将文件恢复到 content_before 状态）"""
        return await self._do_apply_async(self.content_before)
