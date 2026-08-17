"""user_select — 用户交互选择工具（React Ink 化）。

React Ink 化（2026-08-05）：终端交互从「命令补全弹窗（show_completions +
CompletionState）+ 手动 raw I/O（select/read_byte/cbreak）」迁移为独立的
React Ink 组件 ``UserSelectPopup``（src/tui/app/user_select.py）：

  - ``execute()`` 仅设置 ``model.user_select`` 弹窗状态并轮询等待组件完成
    （不再直接读 stdin / 操作补全弹窗私有字段）；
  - 弹窗渲染与交互由组件负责（use_input + use_state，render 线程驱动
    InputDispatcher 路由按键）；
  - 结果经 ``model.user_select.done/action/result`` 回传，工具协程读取后
    清理状态。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from src._compat_termios import HAS_TERMIOS
from .base import Func, tool_metadata
from ..core.constants import GREEN, YELLOW, RED, DIM, RESET
from ..tui.consumer import get_active_chat_ui


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
                "description": (
                    "向用户显示交互式选择界面，用于确认方案、选择选项或澄清歧义；需用户确认时优先使用。"
                    "支持单选/多选（multi_select），超时/非交互自动回退 default_options。"
                    "返回 JSON：{\"selected\":[...], \"action\":...}，"
                    "action 为 confirmed/cancel/timeout/non_interactive/empty/error。"
                ),
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
                            "description": "与 options 等长的说明列表，option_descriptions[i] 为 options[i] 的说明；TUI 中移动到选项时说明显示在选项右侧。可选，默认空",
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

    async def execute(self):
        """异步执行选择并返回结果"""
        if not self.options:
            return json.dumps({"selected": [], "action": "empty"}, ensure_ascii=False)

        # 终端模式：React Ink 弹窗（UserSelectPopup 组件渲染 + use_input 交互）
        return await self._execute_terminal_async()

    async def _execute_terminal_async(self) -> str:
        """终端模式：设置 UserSelectPopup 弹窗状态，轮询等待组件交互完成。

        React Ink 化（2026-08-05）：不再手动读取终端输入（不再依赖 cbreak/
        select 原始字节循环）、不再 stop/start EscapeMonitor、不再操作补全
        弹窗私有字段。弹窗由
        ``src/tui/app/user_select.py::UserSelectPopup`` 组件渲染与交互
        （render 线程运行中，InputDispatcher 路由 use_input 事件），
        本方法仅：
          1. 检测终端可用性（非交互回退）；
          2. 写入 ``model.user_select``（visible=True, seq+1）；
          3. 轮询 ``us.done``（带 deadline 超时回退）；
          4. 读取结果并清理弹窗状态。
        """
        # Windows 回退：termios 不可用时降级为 non-interactive
        if not HAS_TERMIOS:
            _logger.warning(
                "user_select Windows 回退 non-interactive: title=%s",
                self.title,
            )
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "non_interactive",
            }, ensure_ascii=False)

        # 非交互环境检测
        try:
            tty_ok = os.isatty(sys.stdin.fileno())
        except (ValueError, OSError):
            tty_ok = False
        if not tty_ok:
            _logger.warning(
                "user_select 非交互回退: fd.isatty()=False, title=%s",
                self.title,
            )
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "non_interactive"
            }, ensure_ascii=False)

        # 获取 ChatUIConsumer 与 AppModel
        chat_ui = get_active_chat_ui()
        if chat_ui is None:
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "error: ChatUI 未激活",
            }, ensure_ascii=False)
        model = chat_ui.get_model() if hasattr(chat_ui, "get_model") else None
        if model is None or not hasattr(model, "user_select"):
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "error: ChatUI 模型不可用",
            }, ensure_ascii=False)

        default_opts = list(self.default_options or [])

        # 清理可能残留的命令补全弹窗（避免与选择弹窗叠显）
        try:
            bb = chat_ui.bottom_bar
            if bb is not None and getattr(bb, "is_completion_visible", False):
                bb.hide_completions()
            # ★ 模态底部视图（2026-08-17）：不再清空输入文本——输入区在底部
            #   视图激活期间不渲染（App 只渲染 UserSelectPopup），保留用户
            #   输入（关闭弹窗后原输入恢复显示，不丢失）。修复前清空
            #   ``bb._last_text`` 是为「输入区显示干净弹窗」——输入区已不
            #   渲染，清空反而不必要地丢失用户输入。
        except Exception:
            _logger.debug("user_select: 清理补全弹窗失败", exc_info=True)

        # 清空 stdin 残留（避免弹窗打开时旧按键进入组件）
        try:
            input_ = chat_ui.get_input_component()
            if input_ is not None and hasattr(input_, "flush_stdin_buffer"):
                input_.flush_stdin_buffer()
        except Exception:
            _logger.debug("user_select: flush stdin 失败", exc_info=True)

        # 初始选中索引（默认选项首项）；多选默认预勾选
        initial_idx = 0
        checked = []
        for i, opt in enumerate(self.options):
            if opt in default_opts:
                if not checked:
                    initial_idx = i
                checked.append(i)

        from ..tui.app.model import UserSelectState
        try:
            # 设置弹窗状态（seq+1 强制 UserSelectPopup 重挂载）
            prev_seq = getattr(model.user_select, "seq", 0)
            model.user_select = UserSelectState(
                visible=True,
                seq=prev_seq + 1,
                title=self.title or "选择",
                options=list(self.options),
                option_descriptions=list(self.option_descriptions),
                multi_select=self.multi_select,
                default_options=default_opts,
                selected=initial_idx,
                checked=checked,
                deadline=0.0 if self.timeout <= 0 else time.monotonic() + self.timeout,
            )
            # ★ 模态底部视图（2026-08-17 通用机制）：激活底部视图——App
            #   底部区只渲染 UserSelectPopup（状态栏/输入区不显示，弹窗在
            #   原来底部框位置独立显示）。user_select 工具协议：打开设置
            #   bottom_view，清理恢复空（与 UserSelectState 同生命周期）。
            if hasattr(model, "bottom_view"):
                model.bottom_view = "user_select"
            chat_ui.request_bottom_redraw()

            # 轮询等待组件交互完成（render 线程运行中；组件写 done）
            deadline = model.user_select.deadline
            while not model.user_select.done:
                if deadline > 0 and time.monotonic() >= deadline:
                    # 超时：写回默认结果（组件下一帧读到 done 停止渲染）
                    model.user_select.done = True
                    model.user_select.action = "timeout"
                    model.user_select.result = default_opts
                    break
                await asyncio.sleep(0.05)

            st = model.user_select
            action = st.action or "timeout"
            result = list(st.result) if st.result else default_opts
            return json.dumps({
                "selected": result,
                "action": action,
            }, ensure_ascii=False)
        except Exception as e:
            error_msg = str(e)[:100]
            _logger.debug("user_select 异常", exc_info=True)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": f"error: {error_msg}",
            }, ensure_ascii=False)
        finally:
            # 清理弹窗状态 + 主动重绘（底部栏立即恢复正常显示）
            try:
                model.user_select = UserSelectState()
                # ★ 模态底部视图：关闭底部视图 → App 恢复状态栏 + 输入区
                #   （弹窗关闭后正常底部框重新显示）。
                if hasattr(model, "bottom_view"):
                    model.bottom_view = ""
                chat_ui.request_bottom_redraw()
            except Exception:
                _logger.debug("user_select: cleanup 失败", exc_info=True)
            # 清空 stdin 残留
            try:
                input_ = chat_ui.get_input_component()
                if input_ is not None and hasattr(input_, "flush_stdin_buffer"):
                    input_.flush_stdin_buffer()
            except Exception:
                _logger.debug("user_select: finally flush 失败", exc_info=True)

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
