from __future__ import annotations

import asyncio
import json
import logging
import os
import select
from shutil import get_terminal_size
import sys
import time

from src._compat_termios import HAS_TERMIOS
from .base import Func, tool_metadata
from ..core.constants import GREEN, YELLOW, RED, DIM, RESET
from ..tui.consumer import get_active_chat_ui
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
    tool_category="interactive",
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
                "description": "向用户显示交互式选择界面，用于确认方案、选择选项或澄清需求歧义。支持单选/多选，超时自动选中默认项。非交互环境自动回退默认选项。需要用户确认时优先使用此工具。\n\n参数行为摘要：\n- title（必填）：选择界面的标题，简明扼要即可\n- options（必填）：选项字符串列表，用户从中选择；空列表时返回 {\"selected\":[], \"action\":\"empty\"}\n- option_descriptions（可选）：与 options 等长的说明字符串列表，option_descriptions[i] 为 options[i] 的说明；TUI 终端中移动到选项时说明显示在选项右侧。缺省为空\n- multi_select：是否允许多选，false=单选（默认），true=多选可勾选多项\n- default_options：超时/取消/非交互时回退的默认选项列表，值必须在 options 中\n- timeout：超时秒数（默认120），超时自动回退 default_options，action=\"timeout\"\n\n【边界信息】\n- options为空时返回 {\"selected\":[], \"action\":\"empty\"}，不会崩溃\n- 非交互式终端（非tty）自动回退默认选项，action为\"non_interactive\"\n- 超时（默认120秒）自动选中默认选项，action为\"timeout\"\n- 用户取消操作时返回默认选项，action为\"cancel\"\n- 异常发生时回退默认选项并返回错误信息，action为\"error: ...\"\n- multi_select默认为False（单选模式）\n- default_options参数可选，默认为空列表\n- option_descriptions长度不足时缺省为空字符串；长度超出部分忽略",
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
                        "option_descriptions": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "与 options 等长的说明列表，option_descriptions[i] 为 options[i] 的说明；TUI 中移动到选项时说明显示在右侧。可选，默认空",
                            "default": []
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

    def __init__(self, title, options, multi_select=False, default_options=None, timeout=120, option_descriptions=None):
        super().__init__()  # 调用父类初始化，设置agent为None
        self.title = title
        self.options = options
        self.multi_select = multi_select
        self.default_options = default_options or []
        self.timeout = timeout
        # 与 options 等长的说明列表；长度不足补齐空串，超出截断
        descs = list(option_descriptions or [])
        if len(descs) < len(self.options):
            descs += [""] * (len(self.options) - len(descs))
        self.option_descriptions = descs[: len(self.options)]
        self._input = None

    async def execute(self):
        """异步执行选择并返回结果"""
        if not self.options:
            return json.dumps({"selected": [], "action": "empty"}, ensure_ascii=False)

        # 终端模式：在底部栏补全区显示选项，raw I/O 交互
        return await self._execute_terminal_async()

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

    async def _execute_terminal_async(self) -> str:
        """终端模式：在底部栏补全区显示选项，用 raw I/O 处理 ↑↓/Enter/Esc。

        完全基于标准库实现（termios/tty/os/select），无需 prompt_toolkit。
        """
        monitor = get_active_monitor()
        self._stop_monitor(monitor)

        # Windows 回退：termios 不可用时降级为 non-interactive
        if not HAS_TERMIOS:
            _logger.warning(
                "user_select Windows 回退 non-interactive: title=%s",
                self.title,
            )
            self._start_monitor(monitor)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "non_interactive",
            }, ensure_ascii=False)

        # 非交互环境检测
        if not os.isatty(sys.stdin.fileno()):
            _logger.warning(
                "user_select 非交互回退: fd.isatty()=%s, title=%s",
                os.isatty(sys.stdin.fileno()), self.title,
            )
            self._start_monitor(monitor)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "non_interactive"
            }, ensure_ascii=False)

        # 获取 ChatUIConsumer 用于操作底部栏
        chat_ui = get_active_chat_ui()
        bb = chat_ui.bottom_bar if chat_ui else None
        input_ = chat_ui.get_input_component() if chat_ui else None
        self._input = input_

        if bb is None:
            self._start_monitor(monitor)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "error: ChatUI 未激活",
            }, ensure_ascii=False)

        # 确保底部栏已激活（否则 show_completions 静默跳过）
        if not bb.is_active:
            term_h = get_terminal_size().lines
            if term_h < bb._MIN_HEIGHT:
                self._start_monitor(monitor)
                return json.dumps({
                    "selected": list(self.default_options or []),
                    "action": "error: 终端高度不足",
                }, ensure_ascii=False)
            # 最小激活：仅设置标志和缓存，跳过全量绘制（由 show_completions 完成）
            bb.set_active(True)
            bb._last_text = ""
            bb._last_rendered_text = ""
            bb._last_bottom_lines = bb._bottom_lines
            bb._last_scroll_end = term_h - bb._bottom_lines

        # 多选：初始显示复选框（默认选项前端已勾选），Enter 提交全部选中项，空格切换
        multi_display = []
        for i, opt in enumerate(self.options):
            prefix = "✓ " if opt in (self.default_options or []) else "  "
            multi_display.append(f"{prefix}{i + 1}. {opt}")
        multi_texts = list(self.options) if self.multi_select else self.options

        # 保存当前选中索引的默认值
        initial_idx = 0
        if self.default_options:
            for i, opt in enumerate(self.options):
                if opt in self.default_options:
                    initial_idx = i
                    break

        # 终端 I/O 设置：使用 Input 和 EscapeMonitor 公开 API
        fd = input_.fd if input_ else sys.stdin.fileno()

        try:
            # 清空 stdin 残留
            if self._input:
                self._input.flush_stdin_buffer()

            # 使用 EscapeMonitor 公开方法设置 cbreak 模式
            if monitor:
                monitor.apply_monitor_settings()

            # 仅清空输入文本，使输入区显示干净弹窗选择界面；
            # 但保持 _status_active 不变（不清除），让状态行在弹窗期间
            # 持续刷新 token/耗时/速率，用户能实时看到 AI 仍在生成
            if bb._last_text:
                bb._last_text = ""

            # 在底部栏补全区显示选项
            bb.show_completions(
                multi_display, initial_idx,
                texts=multi_texts,
                title="选择",
                descriptions=self.option_descriptions,
                split_desc=True,
            )

            # 多选状态跟踪（默认选项初始勾选）
            selected_indices: set[int] = set()
            if self.default_options:
                for i, opt in enumerate(self.options):
                    if opt in self.default_options:
                        selected_indices.add(i)
            deadline = None if self.timeout <= 0 else time.monotonic() + self.timeout

            # ★ 动画刷新：render 线程已 suspend（_run_interactive），补全弹窗
            #   呼吸色/spinner/状态栏动画无人驱动而静止（"user_select 显示时
            #   动态不刷新"）。修复：select 至多等待 _REFRESH_INTERVAL，超时后
            #   经 chat_ui.request_bottom_redraw() 触发同步渲染一帧推进时间基
            #   动画（render 线程停止时该调用走同步渲染路径，10Hz 节流）。
            _REFRESH_INTERVAL = 0.1
            last_refresh = 0.0
            while True:
                remaining = None
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break  # 超时退出循环

                wait = _REFRESH_INTERVAL
                if remaining is not None:
                    wait = min(remaining, _REFRESH_INTERVAL)
                try:
                    ready, _, _ = select.select([fd], [], [], wait)
                except (ValueError, OSError):
                    continue

                if not ready:
                    # select 超时：到达 refresh 间隔（或 deadline）
                    if deadline is not None and time.monotonic() >= deadline:
                        break  # 真正超时退出
                    now_t = time.monotonic()
                    if now_t - last_refresh >= _REFRESH_INTERVAL and chat_ui is not None:
                        last_refresh = now_t
                        try:
                            chat_ui.request_bottom_redraw()
                        except Exception:
                            _logger.debug("user_select: 动画刷新异常", exc_info=True)
                    continue

                try:
                    raw = input_.read_byte() if input_ else os.read(fd, 1)
                    if not raw:
                        continue
                except (ValueError, OSError):
                    continue

                b = raw[0]

                # ── ESC / ANSI 序列 ──
                if b == 0x1b:
                    try:
                        has_more, _, _ = select.select([fd], [], [], 0.3)
                        if has_more:
                            nxt = input_.read_byte() if input_ else os.read(fd, 1)
                            if nxt in (b'[', b'O'):
                                # CSI/SS3 序列：\x1b[A/↑, \x1b[B/↓, \x1bOA/↑, \x1bOB/↓
                                has_term, _, _ = select.select([fd], [], [], 0.1)
                                if has_term:
                                    term = input_.read_with_timeout(0.1) if input_ else os.read(fd, 1)
                                    if term is None:
                                        continue
                                    if not term:
                                        continue
                                    if term == b'A':      # ↑
                                        bb.cycle_completion(-1)
                                    elif term == b'B':    # ↓
                                        bb.cycle_completion(1)
                                continue
                    except (ValueError, OSError):
                        pass
                    # 单 ESC → 取消
                    bb.hide_completions()
                    return json.dumps({
                        "selected": list(self.default_options or []),
                        "action": "cancel",
                    }, ensure_ascii=False)

                # ── 空格 → 切换选中（多选） ──
                elif b == 0x20 and self.multi_select:
                    idx = bb._completion_idx
                    if not (0 <= idx < len(self.options)):
                        continue
                    if idx in selected_indices:
                        selected_indices.discard(idx)
                    else:
                        selected_indices.add(idx)
                    # 更新弹窗显示（✓ 标记）
                    new_disp = []
                    for i, opt in enumerate(self.options):
                        prefix = "✓ " if i in selected_indices else "  "
                        new_disp.append(f"{prefix}{i + 1}. {opt}")
                    show_idx = min(bb._completion_idx, len(new_disp) - 1)
                    bb.show_completions(
                        new_disp, show_idx,
                        texts=self.options,
                        title="选择",
                        descriptions=self.option_descriptions,
                        split_desc=True,
                    )
                    continue

                # ── Enter → 确认（单选=当前项，多选=全部选中项） ──
                elif b in (0x0d, 0x0a):
                    if self.multi_select:
                        selected = [self.options[i] for i in sorted(selected_indices)]
                        if not selected:
                            selected = list(self.default_options or [])
                        bb.hide_completions()
                        return json.dumps({
                            "selected": selected,
                            "action": "confirmed",
                        }, ensure_ascii=False)
                    else:
                        idx = bb._completion_idx
                        if not (0 <= idx < len(self.options)):
                            continue
                        chosen = self.options[idx]
                        bb.hide_completions()
                        return json.dumps({
                            "selected": [chosen],
                            "action": "confirmed",
                        }, ensure_ascii=False)

            # 超时
            bb.hide_completions()
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "timeout",
            }, ensure_ascii=False)

        except Exception as e:
            error_msg = str(e)[:100]
            _logger.debug("user_select 异常", exc_info=True)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": f"error: {error_msg}",
            }, ensure_ascii=False)
        finally:
            # 恢复终端设置（通过 EscapeMonitor 公开 API）
            if monitor:
                try:
                    monitor.restore_terminal_settings()
                except Exception:
                    pass

            # 清除弹窗状态 + 主动重绘，确保底部栏立即恢复正常显示
            try:
                bb._completion._visible = False
                bb._completion._popup_height = 0
                bb._completion._items = []
                bb._completion._texts = []
                bb._completion._split_desc = False
                bb.force_redraw()
            except Exception as e:
                _logger.debug("user_select: cleanup failed: %s", e)

            # 清空 stdin 残留
            if self._input:
                self._input.flush_stdin_buffer()

            # 重启 EscapeMonitor
            self._start_monitor(monitor)

    async def display(self):
        """异步显示选择界面并返回结果"""
        Func._publish_tool_text(f"\n{GREEN}> 用户选择: {self.title}{RESET}")

        # 显示选项预览
        def _opt_desc(i: int) -> str:
            desc = self.option_descriptions[i] if i < len(self.option_descriptions) else ""
            if not desc:
                return ""
            desc_short = desc.replace('\r', '').replace('\n', ' ')
            if len(desc_short) > 40:
                desc_short = desc_short[:37] + "..."
            return f" — {desc_short}"

        if len(self.options) <= 10:
            for i, option in enumerate(self.options):
                if option in self.default_options:
                    Func._publish_tool_text(f"  {DIM}{i + 1}. {option}{_opt_desc(i)} (默认){RESET}")
                else:
                    Func._publish_tool_text(f"  {DIM}{i + 1}. {option}{_opt_desc(i)}{RESET}")
        else:
            Func._publish_tool_text(f"  {DIM}共 {len(self.options)} 个选项{RESET}")
            for i, option in enumerate(self.options[:5]):
                if option in self.default_options:
                    Func._publish_tool_text(f"  {DIM}{i + 1}. {option}{_opt_desc(i)} (默认){RESET}")
                else:
                    Func._publish_tool_text(f"  {DIM}{i + 1}. {option}{_opt_desc(i)}{RESET}")
            Func._publish_tool_text(f"  {DIM}... 还有 {len(self.options) - 5} 个选项{RESET}")

        Func._publish_tool_text(f"  {DIM}模式: {'多选' if self.multi_select else '单选'}{RESET}")
        Func._publish_tool_text(f"  {DIM}超时: {self.timeout}秒{RESET}")

        # 执行选择
        result_json = await self.execute()
        result = json.loads(result_json)

        # 显示结果
        action = result.get("action", "")
        selected = result.get("selected", [])

        if action == "confirmed":
            if selected:
                if len(selected) == 1:
                    Func._publish_tool_text(f"  {GREEN}+ 已选择: {selected[0]}{RESET}")
                else:
                    Func._publish_tool_text(f"  {GREEN}+ 已选择 {len(selected)} 项: {', '.join(selected[:3])}{RESET}")
                    if len(selected) > 3:
                        Func._publish_tool_text(f"  {DIM}  ... 还有 {len(selected)-3} 项{RESET}")
            else:
                Func._publish_tool_text(f"  {YELLOW}未选择任何项{RESET}")
        elif action == "cancel":
            Func._publish_tool_text(f"  {YELLOW}x 用户取消{RESET}")
        elif action == "timeout":
            Func._publish_tool_text(f"  {YELLOW}超时{RESET}")
        elif action == "non_interactive":
            Func._publish_tool_text(f"  {YELLOW}非交互式环境{RESET}")
        elif action.startswith("error:"):
            Func._publish_tool_text(f"  {RED}x 错误: {action}{RESET}")
        elif action == "empty":
            Func._publish_tool_text(f"  {DIM}(无可用选项){RESET}")
        else:
            Func._publish_tool_text(f"  {YELLOW}未知操作: {action}{RESET}")

        # 统一处理默认选项显示
        if action in ("cancel", "timeout", "non_interactive") or action.startswith("error:"):
            if self.default_options:
                Func._publish_tool_text(f"  {DIM}使用默认选项: {', '.join(self.default_options)}{RESET}")

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
                          timeout=self.timeout,
                          option_descriptions=tuple(self.option_descriptions))

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
