"""文件操作工具的公共基类，提取 write_file 和 update_file 的共享逻辑。"""

from __future__ import annotations

import os
import sys
import time
import logging
import asyncio

_logger = logging.getLogger(__name__)
from .base import Func, tool_metadata
from .file_ops import (
    validate_path_security,
    check_file_size,
    async_file_exists,
)
from ..core.constants import GREEN, RED, DIM, RESET
from ..tui.consumer.diff_renderer import show_file_diff
from ..tui.widgets.lock import (
    diff_active,
)
from ..core.sandbox_manager import async_record_file_change_from_context

from ._constants import DEFAULT_ENCODING, DEFAULT_ERRORS as DEFAULT_ERRORS_HANDLING, MAX_FILE_SIZE_MB


class FileToolError(Exception):
    pass


# ═══════════════════════════════════════════════════════════════
# FileSystemToolBase — cp/mv/rm/mk 的公共基类
# ═══════════════════════════════════════════════════════════════

class FileSystemToolBase(Func):
    """cp/mv/rm/mk 的公共基类，提供异常处理模板和 display() 模式。

    子类覆盖：
      - _action_verb: str — 操作动词，用于错误消息格式
      - _get_operation_desc() -> str — 返回 display() 使用的操作描述
      - execute() — 业务逻辑，委托给 _run_with_error_handling()
    """

    _action_verb: str = ""  # 子类覆盖："复制"/"移动"/"删除"/"创建"

    def _get_operation_desc(self) -> str:
        """返回 display() 中 _print_operation 使用的操作描述。子类必须覆盖。"""
        raise NotImplementedError

    async def _run_with_error_handling(self, operation):
        """封装统一的异常处理模板，包装 operation() 中的业务逻辑。

        ValueError / PermissionError / OSError 按照 _action_verb 构造
        统一格式的错误消息；asyncio.CancelledError 向上传播；
        其他未知异常记录日志并返回统一错误格式。
        """
        try:
            return await operation()
        except ValueError as e:
            return f"({self._action_verb}失败: {e})"
        except PermissionError as e:
            return f"(权限不足: {e})"
        except OSError as e:
            return f"({self._action_verb}失败: {e})"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logging.getLogger(__name__).exception(
                "%s异常: %s", self._action_verb, e
            )
            return f"({self._action_verb}失败: {e})"

    async def display(self) -> str:
        """标准 display() 模式：打印操作描述 → 执行 → 打印结果。"""
        self._print_operation(self._get_operation_desc())
        result = await self.execute()
        self._print_result(result)
        return result

class PathSecurityError(FileToolError):
    pass

class FileSizeError(FileToolError):
    pass


@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=False,
    timeout_estimate=0,
    category="io",
    priority=10,
    tool_category="write",
    description="文件操作工具基类",
)
class FileToolBase(Func):
    """write_file 和 update_file 的公共基类。"""

    def __init__(self, path: str, content_for_size_check: str = ""):
        # 内容大小校验（纯字符串计算，无 I/O）保留在构造时
        self._check_content_size(content_for_size_check)
        super().__init__()
        self.path = path
        self.encoding = DEFAULT_ENCODING
        self.errors = DEFAULT_ERRORS_HANDLING
        self.stats = {"total_time": 0.0, "lines_processed": 0}

    # ── 校验 ──

    @staticmethod
    def _check_content_size(content: str):
        size = len(content.encode('utf-8', errors='ignore'))
        limit = MAX_FILE_SIZE_MB * 1024 * 1024
        if size > limit:
            raise FileSizeError(f"内容大小({size // 1024}KB)超过最大限制({MAX_FILE_SIZE_MB}MB)")

    def _validate_path_and_size(self):
        """在 execute() 中执行的路径安全校验和文件大小检查。

        包含同步 I/O 操作（os.path.islink, os.path.exists, os.path.realpath），
        在异步上下文中执行以避免构造时阻塞。
        """
        try:
            validate_path_security(self.path)
            # 额外检查：解析符号链接后再次校验
            if os.path.islink(self.path) or os.path.exists(self.path):
                real = os.path.realpath(self.path)
                try:
                    validate_path_security(real)
                except ValueError as e:
                    raise PathSecurityError(f"符号链接指向越界路径: {self.path} -> {real}")
        except ValueError as e:
            raise PathSecurityError(str(e))

        # plan / write_memory agent 路径白名单校验
        # 设计说明：仅当 agent_type 显式为 "plan" 或 "write_memory" 时触发，默认值 None 不触发。
        # agent_type 由 SubAgent._handle_tool_calls 注入，保证子代理的所有
        # 工具调用都会被正确标记。若未注入（直接 ToolRegistry.dispatch），无 agent
        # 上下文即无语义，不限制是正确行为。
        # execute agent 无路径白名单限制，
        # 因其需要修改项目源码文件来执行计划步骤。
        agent_type_val = getattr(self, 'agent_type', None)
        if agent_type_val in ('plan', 'write_memory'):
            if agent_type_val == 'plan':
                allowed_dir = os.path.realpath(os.path.abspath(os.path.join(os.getcwd(), '.chat', 'plan')))
                agent_label = "plan agent"
            else:  # write_memory
                allowed_dir = os.path.realpath(os.path.abspath(os.path.join(os.getcwd(), '.chat', 'memory')))
                agent_label = "write_memory agent"
            # 使用 os.path.realpath 解析目标目录的所有符号链接中间目录，
            # 防止 allowed_dir 本身是符号链接指向外部目录时被绕过
            abs_path = os.path.abspath(self.path)
            # os.path.commonpath 判断子路径关系，防 ../ 穿越
            try:
                common = os.path.commonpath([allowed_dir, abs_path])
                if common != allowed_dir:
                    raise PathSecurityError(
                        f"{agent_label} 只能在 {allowed_dir} 目录下写入文件。"
                        f"当前路径: {self.path}（解析后: {abs_path}），"
                        f"不在允许的目录: {allowed_dir}"
                    )
            except ValueError:
                # 不同驱动器（Windows）等无法比较的情况
                raise PathSecurityError(
                    f"{agent_label} 只能在 {allowed_dir} 目录下写入文件。"
                    f"当前路径: {self.path} 无法与 {allowed_dir} 比较"
                )

        if os.path.exists(self.path):
            try:
                check_file_size(self.path, MAX_FILE_SIZE_MB)
            except ValueError as e:
                raise FileSizeError(str(e))

    # ── 读写 ──

    async def _read_original(self) -> str | None:
        """读取文件原始内容。文件不存在时返回 None（沙盒还原依赖此语义）。

        使用 asyncio.to_thread + 标准 open 替代 aiofiles.open，
        避免 aiofiles 旧版本在 Python 3.9 子线程中调用
        asyncio.get_event_loop() 时抛出 RuntimeError。
        """
        if not await async_file_exists(self.path):
            return None
        try:
            return await asyncio.to_thread(self._sync_read_file)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.warning("读取原始文件失败: %s", self.path)
            return None

    def _sync_read_file(self) -> str | None:
        """同步读取文件内容（在 asyncio.to_thread 中执行）。

        委托 file_ops._sync_read_file 以减少逻辑重复。
        """
        from .file_ops import _sync_read_file
        return _sync_read_file(self.path, self.encoding, self.errors)

    async def _atomic_write(self, content: str) -> tuple[int, int]:
        """原子写入文件，返回 (lines_count, size_bytes)

        委托给 utils.async_atomic_write 实现，避免重复的原子写入逻辑。
        """
        from .utils import async_atomic_write
        lines_count, size_bytes = await async_atomic_write(
            self.path, content, self.encoding, self.errors,
        )
        self.stats["lines_processed"] = lines_count
        return lines_count, size_bytes

    def _show_diff(self, old: str, new: str):
        show_file_diff(self.path, old, new)

    # ── diff 预览 ──

    async def _show_diff_preview(
        self,
        old_content: str | None,
        new_content: str,
        exists: bool,
        mode_desc: str,
    ) -> None:
        """显示 diff 预览到终端

        ★ 锁策略：print() 使用 timeout 超时保护的 output_lock，
        防止 PTY 缓冲区满时锁被永久持有导致输出管线冻结。
        超时（0.1s）后降级为直写 sys.__stdout__，保证工具执行不阻塞。

        与 show_file_diff() 内部使用相同锁策略，确保输出顺序一致。
        使用 threading.RLock（可重入）而非 asyncio.Lock，
        避免异步锁在事件循环中的冗余串行化等待。
        """
        diff_was_active = diff_active.is_set()
        if not diff_was_active:
            diff_active.set()
        try:
            if exists:
                Func._publish_tool_text(f"\n  {DIM}{self.path} {mode_desc}{RESET}")
                if old_content != new_content:
                    self._show_diff(old_content, new_content)
                else:
                    Func._publish_tool_text(f"  {DIM}no changes{RESET}")
            else:
                Func._publish_tool_text(f"\n  {DIM}{self.path} new {mode_desc}{RESET}")
                self._show_diff("", new_content)
        finally:
            if not diff_was_active:
                diff_active.clear()

    # ── 执行框架 ──

    async def _get_new_content(self) -> str:
        """子类实现：返回最终要写入的完整内容。"""
        raise NotImplementedError

    def _success_verb(self) -> str:
        """子类实现：返回成功动词，如 '写入成功' 或 '更新成功'。"""
        raise NotImplementedError

    def _mode_desc(self) -> str:
        """子类实现：返回操作描述。"""
        raise NotImplementedError

    # ── 执行结果记录 ──

    def _log_execution_result(self, output: str, elapsed: float) -> None:
        """记录并显示执行结果"""
        ok = self._success_verb() in output
        if ok and elapsed > 0.5:
            stats_str = f" (耗时: {elapsed:.2f}s"
            if self.stats["lines_processed"] > 0:
                stats_str += f", {self.stats['lines_processed']}行"
            stats_str += ")"
            output += stats_str
        if ok:
            Func._publish_tool_text(f"  {GREEN}+ {output}{RESET}")
        else:
            Func._publish_tool_text(f"  {RED}x {output}{RESET}")

    async def _prepare_diff_content(self) -> tuple[str | None, str | None, bool]:
        """准备 diff 所需的新旧内容，返回 (old_content, new_content, exists)
        若 _get_new_content 出错，返回 (None, None, exists)
        """
        exists = await async_file_exists(self.path)
        old_content = await self._read_original() if exists else None
        try:
            new_content = await self._get_new_content()
        except FileToolError as e:
            return None, None, exists
        return old_content, new_content, exists

    async def execute(self, precomputed_content=None) -> str:
        """异步执行文件写入，直接 await async 文件操作（不再通过 run_in_executor 包装同步方法）"""
        total_start = time.time()
        try:
            # 路径安全校验 + 文件大小检查（在异步上下文中执行，避免构造时阻塞）
            self._validate_path_and_size()

            parent_dir = os.path.dirname(self.path)
            if parent_dir:
                from .file_ops import async_makedirs_and_record
                await async_makedirs_and_record(parent_dir, self.name)

            old_content = await self._read_original()
            new_content = precomputed_content if precomputed_content is not None else await self._get_new_content()
            lines_count, size = await self._atomic_write(new_content)
            result = f"{self._success_verb()} {lines_count}L {size}B"

            # 沙盒记录（异步版本，通过 to_thread 安全记录）
            await async_record_file_change_from_context(
                self.path, old_content, new_content, self.name
            )
            return result

        except FileToolError as e:
            return f"({self._success_verb().replace('成功','失败')}: {e})"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            import logging
            logging.getLogger(__name__).exception("文件操作异常: %s", e)
            return f"({self._success_verb().replace('成功','失败')}: {e})"
        finally:
            self.stats["total_time"] = time.time() - total_start

    async def display(self) -> str:
        """异步显示文件操作过程并返回结果"""
        mode_desc = self._mode_desc()

        # 准备内容
        exists = await async_file_exists(self.path)
        old_content = None
        new_content = None
        if exists:
            old_content = await self._read_original()
        try:
            new_content = await self._get_new_content()
        except FileToolError as e:
            err_msg = f"({self._success_verb().replace('成功','失败')}: {e})"
            Func._publish_tool_text(f"  {RED}x {err_msg}{RESET}")
            return err_msg

        # 显示 diff 预览
        await self._show_diff_preview(old_content, new_content, exists, mode_desc)

        # 执行写入并测量耗时
        start_time = time.time()
        output = await self.execute(precomputed_content=new_content)
        elapsed = time.time() - start_time

        # 记录执行结果
        self._log_execution_result(output, elapsed)
        return output

    async def web_display(self) -> str:
        """Web 模式：返回 JSON 格式的 diff 数据"""
        import json
        from ..tui.consumer.diff_renderer import render_diff_to_ansi

        mode_desc = self._mode_desc()
        old_content, new_content, exists = await self._prepare_diff_content()

        if new_content is None and old_content is None:
            # ★ P0 修复: _get_new_content 出错（如 StringNotFoundError）时，
            #   返回错误 JSON 而非用空内容覆写文件。
            err_msg = f"({self._success_verb().replace('成功','失败')}: 内容生成失败)"
            Func._publish_tool_text(f"  {RED}x {err_msg}{RESET}")
            return json.dumps({"type": "webdiff", "path": self.path, "result": err_msg}, ensure_ascii=False)

        if new_content is None:
            # _get_new_content 出错，execute 会处理
            new_content = old_content or ""

        # 打印 diff 预览到终端（通过 EventBus 统一渲染）
        if exists and old_content == new_content:
            Func._publish_tool_text(f"📄 {self.path} {mode_desc}\n(无变化)\n")
        else:
            diff_text = render_diff_to_ansi(self.path, old_content or "", new_content)
            Func._publish_tool_text(f"📄 {self.path} {mode_desc}\n")
            if diff_text:
                Func._publish_tool_text(diff_text + "\n")

        output = await self.execute(precomputed_content=new_content)

        ok = self._success_verb() in output
        Func._publish_tool_text(f"{'+' if ok else 'x'} {output}\n")

        result_data = {
            "type": "webdiff",
            "path": self.path,
            "mode": mode_desc,
            "old_content": old_content or "",
            "new_content": new_content,
            "result": output,
        }
        return json.dumps(result_data, ensure_ascii=False)
