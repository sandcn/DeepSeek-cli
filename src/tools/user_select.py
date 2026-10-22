"""user_select — 用户交互选择工具（React Ink 化，支持并发多问题 tab）。

React Ink 化（2026-08-05）：终端交互从「命令补全弹窗（show_completions +
CompletionState）+ 手动 raw I/O（select/read_byte/cbreak）」迁移为独立的
React Ink 组件 ``UserSelectPopup``（src/tui/app/user_select.py）：

  - ``execute()`` 仅设置 ``model.user_select`` 弹窗状态并轮询等待组件完成
    （不再直接读 stdin / 操作补全弹窗私有字段）；
  - 弹窗渲染与交互由组件负责（use_input + use_state，render 线程驱动
    InputDispatcher 路由按键）；
  - 结果经 ``model.user_select.done/action/result`` 回传，工具协程读取后
    清理状态。
  - 终态写入统一经 ``UserSelectState.try_set_final``（first-write-wins，
    锁内原子写）——工具超时分支与组件确认并发时不会互相覆盖
    （2026-08-17 修复：修复前工具侧超时分支无条件覆盖，用户已确认却
    可能返回 timeout，UserSelect timeout 精确性验证（1s）有机率复现）。

★ 2026-08-19（用户需求：user_select 并发 + tab 切换，参考 Claude Code
AskUserQuestion）：``parallel_safe=True``——多个 user_select 工具调用可
**并发执行**（ToolDAG 不再强制 user_select 串行化，见 tool_dag.py
``_add_user_select_constraints``）。每个并发调用**追加**一个
``UserSelectState`` 到 ``model.user_selects`` 并发队列，``UserSelectPopup``
以 tab 形式全部一起显示、Tab/←/→ 切换焦点。交互协议：

  - 打开：append ``model.user_selects`` + 同步 ``model.user_select`` +
    ``bottom_view="user_select"`` + request_bottom_redraw；
  - 组件：问题 Enter 确认仅 ``mark_answered`` 标记 answered（**提交前可
    重新回答**）；最后一个 tab = Submit 页——Enter 统一 ``try_set_final``
    置 done（已答按各自 action/result、未答取 default_options）；
  - 工具：轮询自己的 state.done（Submit 提交或超时置位）→ 读 result 返回；
  - 清理：已完成（done）的 tab **保留显示**（[×] 标记，参考 Claude）；
    当**全部**问题都 done 时（最后一个完成的协程）统一清空列表 + 关闭
    bottom_view——单问题（非并发）行为与旧版一致（Enter 直接提交）。

★ 2026-08-19（用户需求：user_select.py 增加可以并发向用户提问的提词）：
``to_tool_schema()`` 的 description（模型可见的工具提词）显式声明「支持
并发向用户提问」——引导模型在需要多个决策/确认/澄清的问题时，同一轮
**同时**发起多个 user_select 调用（并行提问、一次问完），弹窗以 tab 形式
全部显示、用户一次答完，避免逐个串行提问打断用户。

★ 2026-10-21（用户需求：支持用户输入不在选项中的内容并返回，用户输入时
停止自动超时）：工具构造的 ``UserSelectState`` 带 ``allow_custom=True``
——弹窗列表末尾渲染「✎ 其他（自定义输入…）」入口，用户可输入任意文本。
结果 ``selected`` 含自定义文本，并在 JSON 中额外返回
``"custom": "..."`` 标记自由输入。

★ 2026-10-22（用户需求：删除 ``timeout`` 参数，改成「不操作界面一分钟后
才算超时」）：工具 schema 与 ``__init__`` 均不再接受 ``timeout``；超时改为
**空闲计时**——构造 state 时写入 ``idle_timeout=IDLE_TIMEOUT_SECONDS``
（60s）与初始 ``deadline=now+60``，弹窗每收到一次按键/滚动就把
``deadline`` 重置为 ``now+60``（见 ``core.user_select_state.touch_activity``
与 ``tui/app/user_select.py`` 的 ``_touch_idle_states``）。因此：

  - 用户持续操作（导航/勾选/切 tab/输入自定义答案）**永不超时**；
  - 界面停顿 1 分钟无任何操作 → 工具轮询判定超时，原子回退
    ``default_options``（``action="timeout"``）；
  - 外部编辑器（Ctrl+G vim）独占终端期间组件置 ``state.typing=True``，
    轮询暂停计时并在返回后补回，避免长文编辑被空闲超时打断。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time

from src._compat_termios import HAS_TERMIOS
from .base import Func
from ..core.constants import GREEN, YELLOW, RED, DIM, RESET
from ..core.display_target import get_display_target
from ..core.user_select_state import IDLE_TIMEOUT_SECONDS


_logger = logging.getLogger(__name__)


def _interactive_stdin() -> bool:
    """当前进程的 stdin 能否接收交互式按键（决定弹窗是否可显示）。

    POSIX/Cygwin（``HAS_TERMIOS=True``）：TUI 的输入层经 select/os.read
    读取 stdin，要求 stdin 是真实 TTY。

    原生 Windows（``HAS_TERMIOS=False``）：TUI 输入改由
    ``src._win_console``（``ReadConsoleInputW`` + ``SetConsoleMode``）
    承载，**不依赖 termios**——stdin 是控制台句柄即可交互
    （``_win_console.supported()``）。

    ★ 实现位于 L0 的 ``src/_win_console.py``（不是 ``src.tui``）：基础设施层
    （tools）不得依赖表现层，实现下沉后 tui 与 tools 共用同一后端。

    ★ 修复（2026-10-20，用户需求：Windows 下 user_select 弹窗必须显示）：
    修复前 ``_execute_terminal_async`` 只要 ``HAS_TERMIOS`` 为 False 就
    直接回退 ``non_interactive``——原生 Windows 上 termios 永远不可用，
    即使 TUI 正在运行、可以正常读取按键（Windows 控制台后端），弹窗也
    永远不显示（工具静默返回默认选项）。

    Returns:
        True — stdin 可交互（可以弹出选择界面）；False — 非交互环境。
    """
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError, TypeError):
        return False
    try:
        if os.isatty(fd):
            return True
    except (OSError, ValueError):
        pass
    if not HAS_TERMIOS:
        # 原生 Windows：isatty 之外的兜底探测（控制台句柄可读按键）
        try:
            from .. import _win_console
            return bool(_win_console.supported())
        except Exception:
            _logger.debug("user_select: Windows 控制台输入探测失败", exc_info=True)
    return False


def _poll_deadline(state, paused_at: float, now: float) -> "tuple[float, bool]":
    """超时轮询一拍：外部编辑器运行期间暂停空闲计时。

    ★ 2026-10-22（用户需求：删除 timeout 参数，改成「不操作界面一分钟后
    才算超时」）：``deadline`` 由空闲计时维护——弹窗每次按键/滚动经
    ``UserSelectState.touch_activity`` 重置为 ``now + idle_timeout``（60s），
    因此本函数只负责判定「是否已连续 1 分钟没有操作」。组件在调用外部
    编辑器（Ctrl+G，默认 vim）期间把 ``state.typing`` 置 True（跨线程普通
    字段，GIL 原子）——编辑器独占终端、界面收不到按键，本函数在
    ``typing`` 为 True 期间不判定超时，并把进入编辑器的时刻记入
    ``paused_at``；返回后把暂停时长补回 ``state.deadline``（剩余时间继续，
    不会因编辑耗时被立刻判超时，也不会永久挂起——未提交退出后仍由空闲
    计时兜底）。

    Args:
        state: UserSelectState（读 ``typing`` / ``deadline``）。
        paused_at: 本轮暂停开始时刻（0.0 = 当前未暂停）。
        now: 当前 ``time.monotonic()``。

    Returns:
        ``(新的 paused_at, 是否已超时)``。
    """
    typing = bool(getattr(state, "typing", False))
    if typing:
        if not paused_at:
            paused_at = now
        return paused_at, False
    if paused_at:
        try:
            deadline = float(getattr(state, "deadline", 0.0) or 0.0)
        except (TypeError, ValueError):
            deadline = 0.0
        if deadline > 0:
            state.deadline = deadline + max(0.0, now - paused_at)
        paused_at = 0.0
    try:
        deadline = float(getattr(state, "deadline", 0.0) or 0.0)
    except (TypeError, ValueError):
        deadline = 0.0
    return paused_at, bool(deadline > 0 and now >= deadline)


def _idle_timeout_text() -> str:
    """空闲超时的展示文案（``IDLE_TIMEOUT_SECONDS`` 60s → ``1 分钟``）。

    非整分钟时回退为秒数（保持展示与常量一致，避免硬编码「1 分钟」）。
    """
    if IDLE_TIMEOUT_SECONDS >= 60 and IDLE_TIMEOUT_SECONDS % 60 == 0:
        return f"{int(IDLE_TIMEOUT_SECONDS // 60)} 分钟"
    return f"{IDLE_TIMEOUT_SECONDS:g} 秒"


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
                    "★ 支持并发向用户提问：当有多个需要用户决策/确认/澄清的问题时，"
                    "应在同一轮同时发起多个 user_select 调用（并行提问、一次问完所有问题），"
                    "所有问题会在一个弹窗界面里以 tab 形式一起显示给用户"
                    "（Tab/←/→ 切换焦点、逐题 Enter 确认、最后统一提交）——"
                    "用户一次答完所有问题，避免逐个串行提问打断用户。"
                    "支持单选/多选（multi_select）；"
                    "1 分钟没有任何操作（按键/滚动）才自动超时并回退 default_options，"
                    "用户持续操作期间不会超时；非交互自动回退 default_options。"
                    "★ 支持用户输入选项外的自定义答案：选项列表末尾自动追加"
                    "『✎ 其他（自定义输入…）』入口，用户可输入任意文本；"
                    "输入期间同样按空闲计时（每次按键重置 1 分钟倒计时）。"
                    "返回 JSON：{\"selected\":[...], \"action\":...}，"
                    "action 为 confirmed/cancel/timeout/non_interactive/empty/error；"
                    "用户使用自由输入时 selected 中含该文本，并额外返回 "
                    "\"custom\": \"用户输入文本\"。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {
                            "type": "string",
                            "description": "选择界面的标题，必填，建议简明扼要"
                        },
                        "header": {
                            "type": "string",
                            "description": "极短标签（≤12 字符），显示为弹窗标题 chip 与多问题 tab 标签，如「认证方式」「优先级」；缺省时用 title 截断"
                        },
                        "options": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "选项字符串列表，用户从中选择；列表末尾自动追加『✎ 其他（自定义输入…）』入口，用户可输入不在选项中的答案；空列表时返回 {\"selected\":[], \"action\":\"empty\"}"
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
                        }
                    },
                    "required": ["title", "options"]
                }
            }
        }

    def __init__(self, title, options, multi_select=False, default_options=None,
                 option_descriptions=None, header=""):
        """构造 user_select 工具实例。

        ★ 2026-10-22（用户需求）：**已删除** ``timeout`` 参数——超时不再由
        调用方指定，统一为「界面 1 分钟无操作」（``IDLE_TIMEOUT_SECONDS``）。
        模型侧历史调用若仍携带 ``timeout``，``Func.from_args`` 会作为多余
        参数忽略（debug 日志），不影响构造与执行。
        ★ 2026-10-22（用户需求：TUI 与 Claude 对齐）：新增可选 ``header``
        ——极短标签（≤12 字符），渲染为标题 chip 与多问题 tab 标签；空串
        时渲染层回退 title 截断（旧调用零回归）。
        """
        super().__init__()  # 调用父类初始化，设置agent为None
        self.title = title
        self.header = str(header or "")
        self.options = options
        self.multi_select = multi_select
        self.default_options = default_options or []
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
        # 非交互环境检测（POSIX：stdin 是 TTY；原生 Windows：控制台句柄
        # 可经 ``src.tui._win_console`` 读取按键——termios 不可用不代表
        # 无法交互，TUI 在 Windows 上由 Win32 控制台后端承载输入）。
        if not _interactive_stdin():
            _logger.warning(
                "user_select 非交互回退: stdin 不可交互, title=%s",
                self.title,
            )
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": "non_interactive"
            }, ensure_ascii=False)

        # 获取 ChatUIConsumer 与 AppModel
        chat_ui = get_display_target()
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

        from ..core.user_select_state import UserSelectState
        state = None  # 并发队列元素（finally 引用；构造异常时兜底 None）
        try:
            # ★ 并发弹窗（2026-08-19 用户需求：user_select 并发 + tab 切换，
            #   参考 Claude AskUserQuestion）：多个并发 user_select 工具调用
            #   各自**追加**一个 ``UserSelectState`` 到 ``model.user_selects``
            #   并发队列（真源，UserSelectPopup 以 tab 形式全部显示）；
            #   ``model.user_select`` 兼容字段同步指向本 state（旧代码/测试
            #   读取）。打开前清理已完成（done）的残留 state（旧协程已返回、
            #   无人清理的场景）——同一轮并发问题不互相清理。
            prev_seq = getattr(model.user_select, "seq", 0)
            state = UserSelectState(
                visible=True,
                seq=prev_seq + 1,
                title=self.title or "选择",
                header=self.header,
                options=list(self.options),
                option_descriptions=list(self.option_descriptions),
                multi_select=self.multi_select,
                default_options=default_opts,
                selected=initial_idx,
                checked=checked,
                # ★ 2026-10-21（用户需求）：工具路径默认允许用户输入选项外
                #   的自定义答案；命令系统选择（CommandUiAdapter）不传该字段
                #   → 默认 False，index 契约保持不变。
                allow_custom=True,
                # ★ 2026-10-22（用户需求）：超时改为「界面 1 分钟无操作」——
                #   初始 deadline = now + 60s；此后弹窗每收到一次按键/滚动
                #   由组件经 ``touch_activity`` 重置 deadline（``idle_timeout``
                #   为 0 的固定 deadline 调用方不受影响）。
                idle_timeout=IDLE_TIMEOUT_SECONDS,
                deadline=time.monotonic() + IDLE_TIMEOUT_SECONDS,
            )
            try:
                if not hasattr(model, "user_selects"):
                    model.user_selects = []
                model.user_selects[:] = [
                    s for s in model.user_selects if not getattr(s, "done", False)
                ]
                model.user_selects.append(state)
            except Exception:
                _logger.debug("user_select: 追加并发队列失败，回退单例", exc_info=True)
            model.user_select = state
            # ★ 模态底部视图（2026-08-17 通用机制）：激活底部视图——App
            #   底部区只渲染 UserSelectPopup（状态栏/输入区不显示，弹窗在
            #   原来底部框位置独立显示）。user_select 工具协议：打开设置
            #   bottom_view，清理恢复空（与 UserSelectState 同生命周期）。
            if hasattr(model, "bottom_view"):
                model.bottom_view = "user_select"
            chat_ui.request_bottom_redraw()

            # 轮询等待组件交互完成（render 线程运行中；组件写 done）
            # ★ 2026-08-19：轮询**本 state**（并发队列中的元素），不再读
            #   ``model.user_select``——并发场景下 model.user_select 可能被
            #   其他协程覆盖。
            # ★ 2026-10-22（用户需求）：超时 = 界面连续 1 分钟没有操作。
            #   deadline 由组件在每次操作时经 ``state.touch_activity()`` 重置；
            #   外部编辑器（Ctrl+G vim）独占终端期间 ``state.typing`` 为 True
            #   ——轮询暂停计时，编辑器返回后把暂停时长补回 deadline。
            paused_at = 0.0
            while not state.done:
                paused_at, expired = _poll_deadline(
                    state, paused_at, time.monotonic(),
                )
                if expired:
                    # 超时：原子终态写入（first-write-wins）——若组件恰在
                    # 临界窗口已确认（done 已置位），try_set_final 返回 False
                    # 且不覆盖，保留组件结果（2026-08-17 修复：修复前无条件
                    # 写 done/action/result，用户已确认却可能返回 timeout）。
                    state.try_set_final("timeout", default_opts)
                    break
                await asyncio.sleep(0.05)

            st = state
            action = st.action or "timeout"
            result = list(st.result) if st.result else default_opts
            custom = str(getattr(st, "custom", "") or "")
            payload = {
                "selected": result,
                "action": action,
            }
            # ★ 2026-10-21（用户需求）：自由输入时额外返回 custom 字段标记——
            #   不复用 selected 中的同名文本做推断（选项文本可能恰好相同）。
            if custom:
                payload["custom"] = custom
            return json.dumps(payload, ensure_ascii=False)
        except Exception as e:
            error_msg = str(e)[:100]
            _logger.debug("user_select 异常", exc_info=True)
            return json.dumps({
                "selected": list(self.default_options or []),
                "action": f"error: {error_msg}",
            }, ensure_ascii=False)
        finally:
            # 清理弹窗状态 + 主动重绘（底部栏立即恢复正常显示）。
            # ★ 并发清理（2026-08-19）：已完成（done）的 tab **保留显示**
            #   （[×] 标记，参考 Claude AskUserQuestion）——自己的协程不
            #   立即移除；当**全部**问题都 done 时（最后一个完成的协程）
            #   统一清空列表 + 关闭 bottom_view。异常路径（state 未 done）
            #   先置终态避免弹窗残留卡死。
            try:
                if state is not None and not state.done:
                    state.try_set_final("timeout", default_opts)
                if state is None or not getattr(model, "user_selects", None) or all(
                    getattr(s, "done", False) for s in model.user_selects
                ):
                    # 全部完成（或队列已空）：整体关闭弹窗
                    try:
                        model.user_selects.clear()
                    except Exception:
                        _logger.debug("user_select: clear 队列失败", exc_info=True)
                    # ★ 模态底部视图：**先关闭底部视图**（App 恢复状态栏 +
                    #   输入区，弹窗关闭后正常底部框重新显示）再清理弹窗状态
                    #   ——避免「user_select 已重置但 bottom_view 仍指向弹窗」
                    #   的空白帧窗口（渲染线程可能在此窗口取帧，弹窗消失但
                    #   底部区未恢复）。
                    if hasattr(model, "bottom_view"):
                        model.bottom_view = ""
                    # ★ 2026-08-18（连续弹出显示错乱修复）：清理**保留 seq**——
                    #   seq 单调递增保证 App key（us-{seq}）永不重复 → 调和器
                    #   每次强制重挂载 UserSelectPopup（重置内部 selected/
                    #   checked state）。修复前归零：连续两次弹出之间若关闭帧
                    #   被渲染节流合并跳过，第二次 seq 与第一次相同 → key 复用
                    #   → fiber 复用 → 旧选中/勾选残留。
                    prev_seq = getattr(model.user_select, "seq", 0)
                    model.user_select = UserSelectState(seq=prev_seq)
                    chat_ui.request_bottom_redraw()
                    # ★ 2026-08-19 根因修复（同 editmsg）：弹窗清理后、渲染
                    #   线程发布新 input router 前，旧 router 仍含已卸载弹窗
                    #   控件的 handler——用户紧接着的按键（Enter 等）被旧
                    #   router 消费吞掉。同步等待新 router 发布（两帧）再清理
                    #   stdin，之后的用户按键不再被吞。超时（2s）降级继续。
                    try:
                        chat_ui.flush_input_router(2.0)
                    except Exception:
                        _logger.debug("user_select: flush_input_router 异常", exc_info=True)
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
        _label = f"{self.header} · {self.title}" if self.header and self.header != self.title else self.title
        Func._publish_tool_text(f"\n{GREEN}> 用户选择: {_label}{RESET}")

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

        Func._publish_tool_text(
            f"  {DIM}模式: {'多选' if self.multi_select else '单选'} · 可输入自定义答案{RESET}"
        )
        # ★ 2026-10-22（用户需求）：timeout 参数已删除——超时固定为
        #   「界面 1 分钟没有操作」（每次按键/滚动重置倒计时）。
        Func._publish_tool_text(
            f"  {DIM}超时: {_idle_timeout_text()}无操作（按键/滚动重置计时）{RESET}"
        )

        # 执行选择
        result_json = await self.execute()
        result = json.loads(result_json)

        # 显示结果
        action = result.get("action", "")
        selected = result.get("selected", [])
        custom = str(result.get("custom", "") or "")

        if action == "confirmed":
            # ★ 2026-10-21（用户需求）：自由输入优先展示（selected 中同名
            #   文本不重复显示；多选时其余勾选项单独汇总）。多行内容在
            #   单行输出中折叠为 ⏎（返回 JSON 仍保留原始换行）。
            def _oneline(value) -> str:
                return (
                    str(value)
                    .replace("\r\n", "\n")
                    .replace("\r", "\n")
                    .replace("\n", "\u23ce")
                )

            others = [x for x in selected if str(x) != custom] if custom else list(selected)
            if custom:
                Func._publish_tool_text(
                    f"  {GREEN}+ 自定义输入: {_oneline(custom)}{RESET}"
                )
                if others:
                    Func._publish_tool_text(
                        f"  {GREEN}+ 同时选择 {len(others)} 项: "
                        f"{', '.join(_oneline(x) for x in others[:3])}{RESET}"
                    )
                    if len(others) > 3:
                        Func._publish_tool_text(f"  {DIM}  ... 还有 {len(others)-3} 项{RESET}")
            elif selected:
                if len(selected) == 1:
                    Func._publish_tool_text(
                        f"  {GREEN}+ 已选择: {_oneline(selected[0])}{RESET}"
                    )
                else:
                    Func._publish_tool_text(
                        f"  {GREEN}+ 已选择 {len(selected)} 项: "
                        f"{', '.join(_oneline(x) for x in selected[:3])}{RESET}"
                    )
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
