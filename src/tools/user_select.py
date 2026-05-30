from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import select
import sys

from .base import Func, tool_metadata
from ..core.constants import GREEN, YELLOW, RED, DIM, RESET
from ..ui._lock import locked_print
from ..ui.select_picker import run_picker_async
from ..api.escape_monitor import get_active_monitor
from ..api.events import publish_event


_logger = logging.getLogger(__name__)

@tool_metadata(
    parallel_safe=False,
    requires_network=False,
    requires_terminal=True,
    timeout_estimate=120,
    category="interactive",
    priority=5,
    description="用户交互选择",
)
class UserSelectFunc(Func):
    name = "user_select"

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        title = arguments.get("title", "")
        if title:
            s = title.replace('\r', '').replace('\n', ' ')
            return f"'{s[:max_len-2]}'" if len(s) <= max_len - 2 else f"'{s[:max_len-5]}...'"
        return ""

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "user_select",
                "description": "向用户显示交互式选择界面，用于确认方案、选择选项或澄清需求歧义。支持单选/多选，超时自动选中默认项。非交互环境自动回退默认选项。需要用户确认时优先使用此工具。\n\n参数行为摘要：\n- title（必填）：选择界面的标题，简明扼要即可\n- options（必填）：选项字符串列表，用户从中选择；空列表时返回 {\"selected\":[], \"action\":\"empty\"}\n- multi_select：是否允许多选，false=单选（默认），true=多选可勾选多项\n- default_options：超时/取消/非交互时回退的默认选项列表，值必须在 options 中\n- timeout：超时秒数（默认120），超时自动回退 default_options，action=\"timeout\"\n\n【边界信息】\n- options为空时返回 {\"selected\":[], \"action\":\"empty\"}，不会崩溃\n- 非交互式终端（非tty）自动回退默认选项，action为\"non_interactive\"\n- 超时（默认120秒）自动选中默认选项，action为\"timeout\"\n- 用户取消操作时返回默认选项，action为\"cancel\"\n- 异常发生时回退默认选项并返回错误信息，action为\"error: ...\"\n- multi_select默认为False（单选模式）\n- default_options参数可选，默认为空列表",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "选择界面的标题，必填，建议简明扼要"
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "选项字符串列表，用户从中选择；空列表时返回 {\"selected\":[], \"action\":\"empty\"}"
                        },
                        "multi_select": {
                            "type": "boolean",
                            "description": "是否允许多选：false=单选（默认），true=多选可勾选多项",
                            "default": False
                        },
                        "default_options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "超时/取消/非交互时回退的默认选项列表，值必须在 options 中",
                            "default": []
                        },
                        "timeout": {
                            "type": "number",
                            "description": "超时秒数（默认120），超时自动回退 default_options，action=\"timeout\"",
                            "default": 120
                        }
                    },
                    "required": ["title", "options"]
                }
            }
        }

    def __init__(self, title, options, multi_select=False, default_options=None, timeout=120):
        super().__init__()  # 调用父类初始化，设置agent为None
        self.title = title
        self.options = options
        self.multi_select = multi_select
        self.default_options = default_options or []
        self.timeout = timeout

    async def execute(self):
        """异步执行选择并返回结果"""
        if not self.options:
            return json.dumps({"selected": [], "action": "empty"}, ensure_ascii=False)

        # 终端模式：使用 run_picker_async 在当前事件循环中运行
        return await self._execute_terminal_async()

    async def _ensure_tty(self):
        """尝试恢复 TTY 终端，返回 (using_tty, tty_stdout, tty_stdin)"""
        import io as _io
        import os as _os

        _tty_stdout = None
        _tty_stdin = None
        _using_tty = False

        if not sys.stdout.isatty() or not sys.stdin.isatty():
            try:
                _tty_fd = _os.open("/dev/tty", _os.O_RDWR)
                if _os.isatty(_tty_fd):
                    _tty_stdout = _io.TextIOWrapper(
                        _os.fdopen(_tty_fd, "wb", buffering=0),
                        encoding="utf-8", write_through=True,
                    )
                    _tty_stdin_fd = _os.open("/dev/tty", _os.O_RDONLY)
                    _tty_stdin = _io.TextIOWrapper(
                        _os.fdopen(_tty_stdin_fd, "rb", buffering=0),
                        encoding="utf-8",
                    )
                    _using_tty = True
                else:
                    _os.close(_tty_fd)
            except (OSError, IOError, AttributeError):
                pass

        return _using_tty, _tty_stdout, _tty_stdin

    async def _flush_stdin(self):
        """清空 stdin 残留字节（如 ESC 中断后遗留的 \\x1b）"""
        loop = asyncio.get_running_loop()
        while select.select([sys.stdin], [], [], 0)[0]:
            await loop.run_in_executor(None, sys.stdin.read, 1)
        try:
            import termios
            termios.tcflush(sys.stdin, termios.TCIFLUSH)
        except (ImportError, OSError, AttributeError):
            pass

    def _stop_monitor(self, monitor):
        """完全停止 EscapeMonitor（替代 pause，更彻底地清理终端状态）。"""
        if monitor is None:
            return
        try:
            monitor.stop()
            _logger.debug("user_select: EscapeMonitor stopped")
        except Exception as e:
            _logger.warning("user_select: EscapeMonitor stop failed: %s", e)

    def _start_monitor(self, monitor):
        """重新启动 EscapeMonitor（替代 resume，从干净状态开始）。"""
        if monitor is None:
            return
        try:
            monitor.start()
            _logger.debug("user_select: EscapeMonitor started")
        except Exception as e:
            _logger.warning("user_select: EscapeMonitor start failed: %s", e)

    def _save_termios(self) -> dict | None:
        """保存当前终端设置，用于后续强制恢复。"""
        try:
            import termios as _tio
            import os as _os
            fd = sys.stdin.fileno()
            if _os.isatty(fd):
                return {"fd": fd, "old": _tio.tcgetattr(fd)}
        except Exception as e:
            _logger.debug("user_select: save_termios failed: %s", e)
        return None

    def _restore_termios(self, guard: dict | None) -> None:
        """强制恢复终端设置（兜底清理）。"""
        if guard is None:
            return
        try:
            import termios as _tio
            _tio.tcsetattr(guard["fd"], _tio.TCSADRAIN, guard["old"])
            _logger.debug("user_select: termios restored (fd=%d)", guard["fd"])
        except Exception as e:
            _logger.warning("user_select: termios restore failed: %s", e)
            try:
                locked_print(f"\n  警告: 终端设置恢复失败，可能需要手动执行 'reset' 命令")
            except Exception:
                _logger.debug("打印恢复警告失败")

    async def _execute_terminal_async(self) -> str:
        """终端模式：使用 run_picker_async 在当前事件循环中交互选择。"""
        monitor = get_active_monitor()

        # 完全停止 EscapeMonitor，避免任何竞态
        self._stop_monitor(monitor)

        # 保存当前终端设置，作为兜底恢复点
        _termios_guard = self._save_termios()

        _tty_stdout = None
        _tty_stdin = None

        try:
            # TTY 检测与恢复
            _, _tty_stdout, _tty_stdin = await self._ensure_tty()

            # 非交互环境检测
            import os as _os2
            if not _os2.isatty(sys.stdin.fileno()):
                _logger.warning(
                    "user_select 非交互回退: fd.isatty()=%s, title=%s",
                    _os2.isatty(sys.stdin.fileno()), self.title,
                )
                return json.dumps({
                    "selected": list(self.default_options or []),
                    "action": "non_interactive"
                }, ensure_ascii=False)

            # 清空 stdin 残留
            await self._flush_stdin()

            # 运行 Picker
            try:
                if _tty_stdout is not None and _tty_stdin is not None:
                    with contextlib.redirect_stdout(_tty_stdout), contextlib.redirect_stdin(_tty_stdin):
                        result = await run_picker_async(
                            title=self.title,
                            options=self.options,
                            multi_select=self.multi_select,
                            default_options=self.default_options,
                            timeout=self.timeout,
                        )
                else:
                    result = await run_picker_async(
                        title=self.title,
                        options=self.options,
                        multi_select=self.multi_select,
                        default_options=self.default_options,
                        timeout=self.timeout,
                    )
            finally:
                # 关闭 TTY 流（如果被打开过）
                if _tty_stdout is not None:
                    try:
                        _tty_stdout.detach().close()
                    except Exception:
                        _logger.debug("关闭 TTY stdout 失败")
                    _tty_stdout = None
                if _tty_stdin is not None:
                    try:
                        _tty_stdin.detach().close()
                    except Exception:
                        _logger.debug("关闭 TTY stdin 失败")
                    _tty_stdin = None

                # 强制恢复终端设置，确保干净退出
                self._restore_termios(_termios_guard)

            return json.dumps(result, ensure_ascii=False)

        except Exception as e:
            # 兜底恢复
            self._restore_termios(_termios_guard)
            error_msg = str(e)[:100]
            return json.dumps({
                "selected": self.default_options,
                "action": f"error: {error_msg}"
            }, ensure_ascii=False)
        finally:
            # 兜底关闭 TTY 流
            if _tty_stdout is not None:
                try:
                    _tty_stdout.detach().close()
                except Exception:
                    _logger.debug("兜底关闭 TTY stdout 失败")
            if _tty_stdin is not None:
                try:
                    _tty_stdin.detach().close()
                except Exception:
                    _logger.debug("兜底关闭 TTY stdin 失败")

            # 清空 stdin 残留，再重启 EscapeMonitor
            await self._flush_stdin()

            # 重启 EscapeMonitor
            self._start_monitor(monitor)

    async def display(self):
        """异步显示选择界面并返回结果"""
        locked_print(f"\n{GREEN}> 用户选择: {self.title}{RESET}")

        # 显示选项预览
        if len(self.options) <= 10:
            for i, option in enumerate(self.options):
                if option in self.default_options:
                    locked_print(f"  {DIM}{i}. {option} (默认){RESET}")
                else:
                    locked_print(f"  {DIM}{i}. {option}{RESET}")
        else:
            locked_print(f"  {DIM}共 {len(self.options)} 个选项{RESET}")
            for i, option in enumerate(self.options[:5]):
                if option in self.default_options:
                    locked_print(f"  {DIM}{i}. {option} (默认){RESET}")
            locked_print(f"  {DIM}... 还有 {len(self.options) - 5} 个选项{RESET}")

        locked_print(f"  {DIM}模式: {'多选' if self.multi_select else '单选'}{RESET}")
        locked_print(f"  {DIM}超时: {self.timeout}秒{RESET}")

        # 执行选择
        result_json = await self.execute()
        result = json.loads(result_json)

        # 显示结果
        action = result.get("action", "")
        selected = result.get("selected", [])

        if action == "confirmed":
            if selected:
                if len(selected) == 1:
                    locked_print(f"  {GREEN}+ 已选择: {selected[0]}{RESET}")
                else:
                    locked_print(f"  {GREEN}+ 已选择 {len(selected)} 项: {', '.join(selected[:3])}{RESET}")
                    if len(selected) > 3:
                        locked_print(f"  {DIM}  ... 还有 {len(selected)-3} 项{RESET}")
            else:
                locked_print(f"  {YELLOW}未选择任何项{RESET}")
        elif action == "cancel":
            locked_print(f"  {YELLOW}x 用户取消{RESET}")
            if self.default_options:
                locked_print(f"  {DIM}使用默认选项: {', '.join(self.default_options)}{RESET}")
        elif action == "timeout":
            locked_print(f"  {YELLOW}超时{RESET}")
            if self.default_options:
                locked_print(f"  {DIM}使用默认选项: {', '.join(self.default_options)}{RESET}")
        elif action == "non_interactive":
            locked_print(f"  {YELLOW}非交互式环境{RESET}")
            if self.default_options:
                locked_print(f"  {DIM}使用默认选项: {', '.join(self.default_options)}{RESET}")
        elif action == "error" or action.startswith("error:"):
            locked_print(f"  {RED}x 错误: {action}{RESET}")
            if self.default_options:
                locked_print(f"  {DIM}使用默认选项: {', '.join(self.default_options)}{RESET}")
        else:
            locked_print(f"  {YELLOW}未知操作: {action}{RESET}")

        return result_json

    async def web_display(self) -> str:
        """Web 模式：发布 UserSelectNeededEvent，等待前端响应。"""
        if not self.options:
            return json.dumps({"selected": [], "action": "empty"}, ensure_ascii=False)

        from ..webui._pending_selects import pending_selects

        select_id = f"select_{id(self)}_{asyncio.get_running_loop().time()}"
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        pending_selects._pending[select_id] = future

        try:
            publish_event("UserSelectNeededEvent",
                          select_id=select_id,
                          title=self.title,
                          options=tuple(self.options),
                          multi_select=self.multi_select,
                          default_options=tuple(self.default_options or []),
                          timeout=self.timeout)

            try:
                result = await asyncio.wait_for(future, timeout=self.timeout + 5)
                return result
            except asyncio.TimeoutError:
                return json.dumps({
                    "selected": list(self.default_options or []),
                    "action": "timeout",
                }, ensure_ascii=False)
        finally:
            pending_selects._pending.pop(select_id, None)
