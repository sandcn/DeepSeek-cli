"""文件操作工具的公共基类，提取 write_file 和 update_file 的共享逻辑。"""

from __future__ import annotations

import os
import time
import logging
import asyncio

_logger = logging.getLogger(__name__)
from .base import Func, tool_metadata
from .file_ops import (
    validate_path_security,
    check_file_size,
    async_file_exists,
    get_plan_allowed_dir,
    is_path_within_dir,
)
from ..core.constants import GREEN, RED, DIM, RESET
from ..tui._diff_renderer import render_diff_to_ansi
from ..renderer._locks import (
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
    # display_params 模板：'路径' + 可选标志。子类设置后无需再覆盖 display_params
    _display_flag: str | None = None       # 附加标志文本，如 "-p"/"-r"
    _display_flag_arg: str | None = None   # 标志对应的参数名，如 "parents"/"recursive"

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        """标准 display_params 模板：'路径' + 可选标志。"""
        path = arguments.get("path", "")
        display = f"'{cls._sanitize_display(path)}'"
        flag = cls._display_flag
        if flag and arguments.get(cls._display_flag_arg):
            display += f" {flag}"
        return display

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
        result = await self.execute()
        from ..core.constants import GREEN, RED, DIM, RESET
        if result.startswith("("):
            Func._publish_tool_text(
                f"\n  {DIM}{self._get_operation_desc()}{RESET}\n  {RED}x {result}{RESET}"
            )
        else:
            Func._publish_tool_text(
                f"\n  {DIM}{self._get_operation_desc()}{RESET}\n  {GREEN}+ {result}{RESET}"
            )
        return result

class PathSecurityError(FileToolError):
    pass

class FileSizeError(FileToolError):
    pass

# 注：plan 白名单目录不再做模块级缓存——get_plan_allowed_dir() 基于
# os.getcwd()，os.chdir（含测试 chdir）后缓存会陈旧；与 mkdir / base.can_use
# 一致，每次 _validate_path_and_size 动态解析（realpath，开销可忽略）。

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

        包含同步 I/O 操作（os.path.realpath），
        在异步上下文中执行以避免构造时阻塞。

        优化：提前执行一次 realpath（原 3 次 → 1 次有效调用），
        消除 validate_path_security 内部的重复 realpath 解析。
        """
        # 一次 realpath 解析所有符号链接和相对路径，后续校验均基于此路径
        real = os.path.realpath(self.path)
        try:
            validate_path_security(real)
        except ValueError as e:
            raise PathSecurityError(str(e))

        # plan agent 路径白名单校验
        # 设计说明：仅当 agent_type 显式为 "plan" 时触发，默认值 None 不触发。
        # agent_type 由 SubAgent._handle_tool_calls 注入，保证子代理的所有
        # 工具调用都会被正确标记。若未注入（直接 ToolRegistry.dispatch），无 agent
        # 上下文即无语义，不限制是正确行为。
        agent_type_val = getattr(self, 'agent_type', None)
        if agent_type_val == 'plan':
            allowed_dir = get_plan_allowed_dir()
            agent_label = "plan agent"
            # realpath 解析符号链接 + commonpath 防穿越（与 mkdir / base.can_use
            # 共用 is_path_within_dir，统一语义防符号链接绕过）
            if not is_path_within_dir(real, allowed_dir):
                raise PathSecurityError(
                    f"{agent_label} 只能在 {allowed_dir} 目录下写入文件。"
                    f"当前路径: {self.path}（解析后: {real}），"
                    f"不在允许的目录: {allowed_dir}"
                )

        if os.path.exists(real):
            try:
                check_file_size(real, MAX_FILE_SIZE_MB)
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

    def _show_diff(self, old: str, new: str) -> str:
        diff_text = render_diff_to_ansi(self.path, old, new)
        if diff_text:
            return diff_text + "\n"
        return ""

    # ── 输出发布（subagent 上下文感知） ─────────────────

    def _is_subagent_context(self) -> bool:
        """判断当前工具执行是否处于 subagent 上下文。

        subagent 经 ``run_with_tool_context(self.label, ...)`` 执行工具，
        contextvar ``current_tool_id`` 为 ``agent-N`` 前缀（与
        ``EventDispatcher._is_subagent_label`` 的 ``agent-`` 前缀约定一致）；
        主 agent 的 tool_id 为 API 生成的 tool_call_id（如 ``call_xxx``），
        无 ``agent-`` 前缀。
        """
        from ..core.internal.agent._tool_context import get_current_tool_id
        tool_id = get_current_tool_id()
        return bool(tool_id and tool_id.startswith("agent-"))

    def _publish_file_output(self, text: str) -> None:
        """发布文件操作输出（diff 预览 + 执行结果）。

        主 agent 上下文：走 ``_publish_tool_text`` → ToolOutputChunkEvent
        → 工具卡片（既有行为不变）。
        subagent 上下文：走 ``publish_output`` → OutputEvent →
        ``EventDispatcher._on_output`` → WriteLineCmd → 主消息区 committed
        文本行，使 subagent 调用 write_file/update_file 的 diff 在消息区
        可见（不创建工具 box，避免 BUG-63 的永不关闭 box 问题）。
        """
        if self._is_subagent_context():
            from ..tui.events.consumers import publish_output
            try:
                publish_output(text, level="raw")
            except Exception:
                _logger.debug("publish_output 失败", exc_info=True)
        else:
            Func._publish_tool_text(text)

    # ── diff 预览 ──

    async def _show_diff_preview(
        self,
        old_content: str | None,
        new_content: str,
        exists: bool,
        mode_desc: str,
    ) -> str:
        """返回累积的 diff 预览文本

        ★ diff_active 互斥：通过 diff_active Event 标记 diff 渲染中，
        阻止 _refresh_loop 在此期间渲染帧，避免 diff 输出与面板刷新交叠。

        diff 通过 render_diff_to_ansi 渲染为 ANSI 字符串后，
        经本方法累积并返回，由调用方统一上屏。
        """
        diff_was_active = diff_active.is_set()
        if not diff_was_active:
            diff_active.set()
        try:
            parts = []
            if exists:
                parts.append(f"\n  {DIM}{self.path} {mode_desc}{RESET}")
                if old_content != new_content:
                    diff_text = self._show_diff(old_content, new_content)
                    if diff_text:
                        parts.append(diff_text)
                else:
                    parts.append(f"  {DIM}no changes{RESET}")
            else:
                parts.append(f"\n  {DIM}{self.path} new {mode_desc}{RESET}")
                diff_text = self._show_diff("", new_content)
                if diff_text:
                    parts.append(diff_text)
            return "".join(parts)
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

    def _log_execution_result(self, output: str, elapsed: float) -> str:
        """记录并返回执行结果文本"""
        ok = self._success_verb() in output
        if ok and elapsed > 0.5:
            stats_str = f" (耗时: {elapsed:.2f}s"
            if self.stats["lines_processed"] > 0:
                stats_str += f", {self.stats['lines_processed']}行"
            stats_str += ")"
            output += stats_str
        if ok:
            return f"  {GREEN}+ {output}{RESET}"
        else:
            return f"  {RED}x {output}{RESET}"

    async def execute(self, precomputed_content=None) -> str:
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
            # ★ 记录失败不视为写入失败：文件已落盘，报失败会让模型重试/重复
            #   写入，且 undo 回滚失效。与 cp/mv/rm 的 async_record_sandbox
            #   （捕获 + warning）策略一致——捕获 + warning，工具仍报成功。
            try:
                await async_record_file_change_from_context(
                    self.path, old_content, new_content, self.name
                )
            except Exception:
                _logger.warning("沙盒记录失败（文件已写入，忽略）: %s", self.path,
                                exc_info=True)
            return result

        except FileToolError as e:
            return f"({self._success_verb().replace('成功','失败')}: {e})"
        except asyncio.CancelledError:
            raise
        except Exception as e:
            _logger.exception("文件操作异常: %s", e)
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
        except Exception as e:
            if not isinstance(e, FileToolError):
                _logger.exception("display() 内容生成异常: %s", e)
                err_msg = f"({self._success_verb().replace('成功','失败')}: 内容生成失败)"
            else:
                err_msg = f"({self._success_verb().replace('成功','失败')}: {e})"
            self._publish_file_output(f"  {RED}x {err_msg}{RESET}")
            return err_msg

        # 获取 diff 预览文本
        preview_text = await self._show_diff_preview(old_content, new_content, exists, mode_desc)

        # 执行写入并测量耗时
        start_time = time.time()
        output = await self.execute(precomputed_content=new_content)
        elapsed = time.time() - start_time

        # 获取执行结果文本并合并发布
        result_text = self._log_execution_result(output, elapsed)
        self._publish_file_output(preview_text + result_text)
        return output
