"""
bash_opt — 按 task_id 操作后台 bash 任务或 GUI 窗口

配合 bash 工具 background=True 模式使用。bash 后台启动后返回
{"task_id": "bg-xxx", "status": "running"}，大模型可据此用 bash_opt 工具
按 task_id 操作：

- op=read   读取后台命令**当前已产生**的全部输出并清空缓冲，立即返回（不等待完成）
- op=wait   等待任务执行完成并获取结果（JSON：task_id/status/stdout/stderr/
            returncode；超时同样返回 JSON：timeout=true / done=false / waited /
            hint，任务继续运行）
- op=kill   杀死后台命令的所有进程树（进程组 + 全部递归后代），杀完后
            **校验**进程是否真正退出（僵尸视为已终止）；仍有存活则**自动
            补杀**（最多 3 轮尝试），返回信息报告校验结论与残留 PID
- op=stdin  向后台命令的 stdin 发送文本输入（text 参数，newline 可选是否追加
            换行，encoding 指定文本编码；缺省 **utf-8**——与 bash 工具为子进程
            统一设置的 stdio 编码一致）。需要按系统 ANSI 代码页（中文 Windows =
            gbk）写入原生控制台程序时显式传 encoding='gbk'
- op=close_stdin  关闭后台命令的 stdin（发送 EOF）：bash 任务统一在 PTY 中
            运行，**没有真正的 EOF**——只是写入 EOT（ConPTY 为 \x1a /
            POSIX 为 \x04）模拟按键。依赖 stdin EOF 的命令请用 op=kill 结束
            任务。**PTY 的 stdin 通道不会因此关闭**，之后仍可继续
            op=stdin / op=keys
- op=keys   向后台命令发送光标/键盘消息（自动路由：目标进程有 GUI 窗口时
            作为窗口级键盘消息注入该窗口，否则回退写入终端——跨平台
            ANSI/VT100 转义序列；支持 ctrl+c 等修饰键组合、esc/pageup 等
            别名、单个字符与 f1-f24）。无响应时用 op=kill

典型 GUI 流程（先用 bash 后台启动程序，再用本工具操作）：

1. ``bash({"command": "notepad.exe", "background": true})`` → 返回
   ``{"task_id": "bg-xxx", "status": "running"}``。窗口创建有延迟，
   screenshot / 输入 op 会自动等待窗口出现（约 5 秒）；
2. ``bash_opt({"task_id": "bg-xxx", "op": "screenshot", "path": "shot.png"})``
   → 用 ``read_image`` 查看界面，按截图左上角像素坐标定位控件；
3. ``bash_opt({"task_id": "bg-xxx", "op": "click", "x": 120, "y": 80})`` /
   ``bash_opt({"task_id": "bg-xxx", "op": "type", "text": "hello"})``
   逐步操作；每次操作后可用 op=screenshot 截图复核；
4. 结束：``bash_opt({"task_id": "bg-xxx", "op": "wait"})`` 取最终输出，
   或 ``bash_opt({"task_id": "bg-xxx", "op": "kill"})`` 终止程序。

GUI 能力只保留三类：**鼠标 / 光标操作、截图、键盘输入**。

- op=screenshot  把后台命令（及其子进程）的窗口，或整个屏幕，截图保存为 PNG
                 （path 参数指定文件路径；可选 crop 指定只截取的像素区域，
                 window 选择目标窗口；screen=true（含字符串 'true'）/
                 'primary'/序号 做整屏 / 多显示器截取（不需要进程句柄））
- op=move / hover / click / drag / scroll / key / type / release
                 向后台命令的 **GUI 窗口**注入鼠标 / 键盘 / 文本输入
                 （window 选择目标窗口；鼠标按钮、双击、拖动、滚轮、组合键、
                  任意 Unicode 文本；release 释放按下的键与鼠标按钮，清理
                  长按留下的悬空状态）

坐标语义（输入 op 通用）：x / y 除像素整数外，还支持语义值——'center' /
'middle'、'left' / 'right' / 'top' / 'bottom'、百分比 '50%'、偏移写法
'center+20' / 'center-20' / 'left+20' / 'right-10' / 'top+5' / 'bottom-30'
（基准 + 像素偏移，自动夹到窗口内）；语义值在注入时按窗口实际尺寸换算，
窗口被缩放也不失准。坐标原点为**窗口截图左上角**（与 op=screenshot 产物
一致，便于「先截图看清界面，再按像素点操作」）。

read 为**增量读取**：后台任务运行期间的每一行输出都会累积到内部缓冲，
每次 read 取走当前全部累积内容并清空，适合实时观察长时任务（编译/下载/
日志流）的进度；任务最终完整结果仍由 op=wait 获取。

read 属**只读观察**：不接管任务生命周期——任务完成后的结果仍由对话轮次正常
回灌，不会因为「read 看一眼进度」而丢失。缓冲有内存上限（超限丢弃最旧
内容），单次返回默认最多 **1000 行**（``max_lines`` 可调，``0`` 不按行截断），
并且还有字符上限（超限只返回尾部最新内容并标注 truncated），避免超长输出
撑爆上下文。

需要**接管任务**（不再由对话轮次自动等待 / 回灌结果）的 op 只有：
stdin / close_stdin / keys / kill 与窗口输入（click 等）——它们要么与任务
交互、要么显式终止、要么向 GUI 注入输入，此后须由模型主动 wait 取最终输出。

多窗口选择（window 参数，截图 / 输入 op 通用）：一个 GUI 程序往往同时存在
多个顶层窗口（主窗口、弹出菜单、下拉浮层、文件对话框），因此 screenshot /
输入 op 都接受 window 选择器——'main'（缺省主窗口，可见性优先）、
'active'（前台窗口）、'#N'（**可操作窗口**按 Z 序第 N 个，1 = 最靠前）、
'handle:0x…'、'title:子串'、'class:子串'、'pid:1234'、'popup'（无标题弹层）、
'dialog'（对话框）。不可见（visible=false）或已最小化的窗口不会被 'main' /
'#N' / 'popup' / 'dialog' 选中。

截图结果 JSON 附带被截窗口的句柄 / 标题 / 候选窗口总数，便于确认选对了窗口。

窗口输入同样按 task_id 定位该命令进程树的可见窗口：click 支持左/右/中键与
双击，drag 支持按住左/右/中键拖拽（带轨迹插值），scroll 支持上下左右滚动，
key 支持 ctrl+shift+s 之类的组合键，type 逐字符输入任意 Unicode 文本。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time

from .base import Func
from .bash import READ_BUFFER_DROPPED_KEY, _remember_echo_lines, kill_process_tree
from ._bash_opt_schema import build_bash_opt_schema
from ._process_output import CHILD_STDIO_ENCODING
from .file_ops import normalize_user_path, validate_path_security
from ._screenshot import (
    CropError,
    CropRegion,
    MonitorError,
    NoWindowError,
    ScreenshotError,
    SelectorError,
    capture_process_window,
    capture_screen,
    list_monitors,
    resolve_monitor,
)
from ._window_input import (
    INPUT_OPS,
    ActionError,
    InputError,
    NoWindowError as InputNoWindowError,
    build_action,
    probe_window,
    send_window_input,
)
from ._terminal_keys import SUPPORTED_TERMINAL_KEYS, parse_terminal_key
from ..core.base_agent import _parse_bash_result_fields

logger = logging.getLogger(__name__)

def default_stdin_encoding() -> str:
    """``op=stdin`` 的默认文本编码：跟随 bash 工具的 stdio 约定（``utf-8``）。

    为什么不再按系统 ANSI 代码页（``GetACP``，中文 Windows = cp936）取默认：

      - bash 工具启动**每个**后台任务时，包装脚本都会把子进程 stdio 统一为
        无 BOM UTF-8——``[Console]::InputEncoding`` / ``OutputEncoding`` /
        ``$OutputEncoding`` 与 ``PYTHONIOENCODING`` / ``PYTHONUTF8``（见
        ``bash._powershell_encoding_line``）；
      - 于是 Python / Node / PowerShell 等程序都按 UTF-8 读 stdin：按 GBK
        写入的中文会被当成非法字节直接抛错（实测 Python 3.11：
        ``UnicodeDecodeError: 'utf-8' codec can't decode byte 0xd6``）；
      - 原生控制台程序（``cmd /c sort`` / ``findstr``）在包装脚本设置
        ``Console.InputEncoding = UTF-8`` 后同样按 UTF-8 解码，实测中文
        输入 / 输出均正常——旧的 ``gbk`` 默认反而是少数派。

    需要按系统 ANSI 代码页（中文 Windows = GBK）写入旧式程序时显式传
    ``encoding='gbk'``。Cygwin / MSYS2 与原生 Windows 一致返回 ``utf-8``。
    """
    return CHILD_STDIO_ENCODING


#: PTY master 写入的总超时（秒）——子进程长时间不读取输入时，写缓冲区会持续
#: 占满；无上限重试会让工具调用永久挂住，这里给出兜底超时（超时转可读错误）。
_PTY_WRITE_TIMEOUT: float = 5.0
#: PTY 写入遇 EAGAIN 时的重试间隔（秒）
_PTY_WRITE_RETRY_INTERVAL: float = 0.01

# 终端按键名 → ANSI/VT100 序列的解析见 ``_terminal_keys`` 模块：它复用
# ``_window_input.keys`` 的键名别名与组合键语法（与 op=key 完全一致），
# 支持 esc/del/pageup/ctrl+c 等别名、修饰键组合、单个字符与 f1-f20。


async def _write_pty_all(fd: int, data: bytes, *,
                         timeout: float = _PTY_WRITE_TIMEOUT,
                         retry_interval: float = _PTY_WRITE_RETRY_INTERVAL) -> None:
    """向 PTY master 写入全部数据，处理非阻塞 EAGAIN（缓冲区满时短暂重试）。

    PTY master 被包装进 asyncio 读管道后处于非阻塞模式；子进程不读取时
    写缓冲区可能短暂占满，os.write 抛 BlockingIOError，这里等待后重试
    直至写完。写入失败（fd 关闭 / EIO 等）抛 OSError 由调用方处理。

    ★ 重试有**总超时**（``timeout``）：目标进程长时间不读取输入（如挂起 /
    死循环）时，缓冲区会一直占满；没有上限的话本协程会永久挂住工具调用。
    超时抛 :class:`TimeoutError`（``OSError`` 子类），由调用方转成可读错误。

    Args:
        fd: PTY master 文件描述符。
        data: 待写入字节。
        timeout: 写入总超时（秒，<= 0 表示不限制）。
        retry_interval: EAGAIN 之后的重试间隔（秒）。
    """
    view = memoryview(data)
    total = 0
    deadline = time.monotonic() + float(timeout) if timeout and timeout > 0 else None
    while total < len(view):
        try:
            written = os.write(fd, view[total:])
        except BlockingIOError:
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError(
                    f"PTY 写入超时（超过 {timeout:g} 秒）：目标进程未读取输入，"
                    f"写缓冲区持续占满"
                ) from None
            await asyncio.sleep(retry_interval)
            continue
        total += written


def _write_pty_all_blocking(fd: int, data: bytes) -> None:
    """阻塞 fd 上写完 ``data``（Windows ConPTY 输入管道；在工作线程里调用）。

    Windows 匿名管道不支持 CRT 非阻塞模式（``os.set_blocking`` 对管道 fd
    无效），``os.write`` 在缓冲区满时**直接阻塞**而不是抛 BlockingIOError。
    因此不能在事件循环线程里调用——否则目标程序不读输入时整个应用冻结
    （TUI 无响应、其他工具也停摆）。调用方用 ``asyncio.to_thread`` 包一层，
    并用 ``asyncio.wait_for`` 提供超时（见 :func:`_write_pty_bytes`）。

    Args:
        fd: 写端文件描述符。
        data: 待写入字节。
    """
    view = memoryview(data)
    total = 0
    while total < len(view):
        written = os.write(fd, view[total:])
        if written <= 0:  # pragma: no cover - 管道关闭时会抛 OSError
            raise OSError("PTY 写入返回 0（写端已关闭）")
        total += written


async def _write_pty_bytes(fd: int, data: bytes) -> None:
    """向 PTY / ConPTY 写端写完数据（按平台选择非阻塞或线程化写入）。

    - POSIX：master fd 已被 ``connect_read_pipe`` 设为非阻塞，走
      :func:`_write_pty_all`（EAGAIN 重试 + 总超时）；
    - Windows ConPTY：写端是阻塞匿名管道，必须放到工作线程（否则阻塞事件
      循环），用 ``asyncio.wait_for`` 限制等待时间。

    Raises:
        OSError: 写入失败 / 超时（``TimeoutError`` 是 ``OSError`` 子类）。
    """
    if os.name == "nt":
        await asyncio.wait_for(
            asyncio.to_thread(_write_pty_all_blocking, fd, data),
            timeout=_PTY_WRITE_TIMEOUT,
        )
        return
    await _write_pty_all(fd, data)


def _format_position(arguments: dict, x_key: str = "x", y_key: str = "y") -> str:
    """把窗口内坐标显示为 ``@x,y``（缺省为 ``@center``，仅用于工具调用展示）。"""
    x = arguments.get(x_key)
    y = arguments.get(y_key)
    if x is None and y is None:
        return "@center"
    return f"@{x},{y}"


class BashOptFunc(Func):
    """按 task_id 操作后台 bash 任务（bash background=True 启动）。"""

    name = "bash_opt"
    _DEFAULT_WAIT_TIMEOUT: int = 300
    #: 截图「等待窗口出现」的总时长（秒）：GUI 程序启动后窗口创建有延迟，
    #: 首轮未找到窗口时按 _SCREENSHOT_RETRY_INTERVAL 轮询重试。
    _SCREENSHOT_WAIT_SECONDS: float = 5.0
    #: 截图重试轮询间隔（秒）
    _SCREENSHOT_RETRY_INTERVAL: float = 1.0
    #: 单轮截图操作的硬超时（秒）——GDI/外部命令卡死时兜底
    _SCREENSHOT_TIMEOUT: float = 30.0
    #: 输入注入「等待窗口出现」的总时长（秒）：GUI 程序窗口创建有延迟
    _INPUT_WAIT_SECONDS: float = 5.0
    #: 输入注入重试轮询间隔（秒）
    _INPUT_RETRY_INTERVAL: float = 1.0
    #: 单轮输入注入的硬超时（秒）——注入调用阻塞时兜底
    _INPUT_TIMEOUT: float = 30.0
    #: op=read 单次返回的字符上限（保留尾部最新内容）
    #: 长时任务的累积输出可能极大，原样返回会撑爆模型上下文；这里在返回时
    #: 截断并明确标注（内存侧的缓冲上限见 ``bash._READ_BUFFER_MAX_CHARS``）。
    _READ_OUTPUT_MAX_CHARS: int = 100_000
    #: op=read 单次返回的**行数**上限（保留尾部最新行；与 bash 工具的
    #: ``MAX_LINES`` 对齐，避免一次 read 把几十万行日志灌进上下文）。
    #: ``max_lines=0`` 表示不按行截断，只受 ``_READ_OUTPUT_MAX_CHARS`` 约束。
    _READ_MAX_LINES_DEFAULT: int = 1000
    #: op=close_stdin 关闭写端时的等待上限（秒）——管道对端不响应时不无限挂住
    _STDIN_CLOSE_TIMEOUT: float = 3.0
    #: stdin 的默认文本编码：跟随 bash 工具为子进程统一设置的 stdio 编码
    #: （见 :func:`default_stdin_encoding` 与
    #: ``_process_output.CHILD_STDIO_ENCODING``）。
    _DEFAULT_STDIN_ENCODING: str = CHILD_STDIO_ENCODING
    #: 由 bash_opt **接管生命周期**的 op：这些操作要么输入交互（stdin /
    #: close_stdin / keys / 窗口输入）、要么显式终止任务（kill），任务不再由
    #: 对话轮次自动等待与回灌结果。
    #: ★ 只读观察型 op（read / screenshot）**不接管**：否则模型「read 看一眼
    #: 进度」就会让任务完成结果再也回灌不到对话（被
    #: _collect_done_background_messages 静默清理）——见模块行为说明。
    #: ★ release 也从集合中排除：它释放的是**全局**按下的键 / 鼠标按钮，
    #: 与后台任务生命周期无关；若照旧接管，一次 release 就会让任务完成结果
    #: 不再自动回灌（静默丢失）。
    _MANAGED_OPS: tuple[str, ...] = (
        "stdin", "close_stdin", "keys", "kill",
        *(op for op in INPUT_OPS if op != "release"),
    )
    #: 与具体进程 / 窗口无关、**无需 task_id** 即可执行的 op（其余 op 仍需要
    #: 后台任务的进程句柄 / 窗口）。screenshot 的整屏形态见
    #: :meth:`_can_run_without_task`。
    _TASKLESS_OPS: tuple[str, ...] = ("release",)
    #: 已结束后台任务的记忆条数（FIFO）——供再次操作同一 task_id 时给出
    #: 「已被 wait 取走结果 / 已被 kill 终止」这类精确提示，而不是误导性的
    #: 「任务不存在（请先用 bash 启动）」。
    _FINISHED_MEMORY: int = 50
    #: 存放「已结束任务」记忆的 agent 属性名（由本工具在运行时惰性创建）
    _FINISHED_ATTR: str = "_bashopt_finished_tasks"

    @classmethod
    def to_tool_schema(cls):
        # schema 字面量已抽至 ``src/tools/_bash_opt_schema.py``（本方法原为
        # 880 行纯数据函数）；每次调用返回全新 dict（调用方可安全修改）。
        return build_bash_opt_schema()

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        task_id = arguments.get("task_id") or ""  # 防御 None（模型传 null）
        op = arguments.get("op", "")
        extra = ""
        if op == "stdin":
            extra = str(arguments.get("text", ""))
            encoding = arguments.get("encoding")
            if encoding:
                extra = (f"{extra} encoding={encoding}" if extra
                         else f"encoding={encoding}")
        elif op == "close_stdin":
            extra = "关闭 stdin"
        elif op == "keys":
            extra = str(arguments.get("key", ""))
        elif op == "screenshot":
            extra = str(arguments.get("path", ""))
            crop = arguments.get("crop")
            if crop:
                extra = f"{extra} crop={crop}" if extra else f"crop={crop}"
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}" if extra else f"window={window}"
            screen = arguments.get("screen")
            if screen:
                extra = f"{extra} screen={screen}" if extra else f"screen={screen}"
        elif op in INPUT_OPS:
            extra = cls._input_display(op, arguments)
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}" if extra else f"window={window}"
            path = arguments.get("path")
            if path:
                extra = f"{extra} shot={path}" if extra else f"shot={path}"
        display = f"{op} {task_id}"
        if extra:
            display += f" {cls._sanitize_display(extra)}"
        return f"'{display}'"

    @staticmethod
    def _input_display(op: str, arguments: dict) -> str:
        """窗口输入动作的显示摘要（按钮/次数/坐标/按键/文本）。"""
        if op == "type":
            return str(arguments.get("text", ""))
        if op == "key":
            return str(arguments.get("key", ""))
        if op == "release":
            return "全部"
        if op == "click":
            button = str(arguments.get("button") or "left")
            count = arguments.get("count")
            label = f"{button}"
            if count not in (None, 1, "1"):
                label += f"x{count}"
            return f"{label} {_format_position(arguments)}"
        if op == "hover":
            return _format_position(arguments)
        if op == "move":
            return _format_position(arguments)
        if op == "scroll":
            direction = str(arguments.get("direction") or "down")
            amount = arguments.get("amount") or 3
            return f"{direction}*{amount} {_format_position(arguments)}"
        if op == "drag":
            start = _format_position(arguments, "from_x", "from_y")
            end = _format_position(arguments, "to_x", "to_y")
            return f"{arguments.get('button') or 'left'} {start}->{end}"
        return ""

    def __init__(self, task_id: str = "", op: str = "", timeout=None,
                 text: str | None = None, newline: bool | None = None,
                 encoding: str | None = None, max_lines=None,
                 key: str | None = None, path: str | None = None,
                 crop: str | None = None,
                 x=None, y=None, to_x=None, to_y=None,
                 from_x=None, from_y=None,
                 button: str | None = None, count=None,
                 direction: str | None = None, amount=None,
                 window: str | None = None, screen=None):
        super().__init__()
        # task_id 归一化（防御 None/缺失）：模型传 {"task_id": null} 时
        # from_args 把 None 传入（默认值不生效），后续 startswith 崩溃。
        self.task_id = task_id or ""
        self.op = op
        # timeout 仅对 wait 生效：省略/None → 按 op 取默认；<=0 → 无限等待。
        # 使用 float 保留小数（如 0.5 秒短超时）。
        self.timeout_given = timeout is not None
        if timeout is None:
            self.timeout = self._DEFAULT_WAIT_TIMEOUT
        else:
            try:
                timeout = float(timeout)
                # NaN 防御：NaN <= 0 恒为 False，会以 NaN 传入 asyncio.wait
                # 导致行为未定义——按缺省超时处理
                if math.isnan(timeout):
                    timeout = self._DEFAULT_WAIT_TIMEOUT
            except (TypeError, ValueError):
                timeout = self._DEFAULT_WAIT_TIMEOUT
            # inf（+∞）归一化为无限等待（None）：asyncio.wait 对 inf 超时
            # 的 deadline 计算不可预期，显式映射为「无限」语义更安全
            self.timeout = None if timeout <= 0 or math.isinf(timeout) else timeout
        self.text = text
        # newline 三态：None = 未指定（stdin 默认追加换行、type 默认原样）
        self.newline = None if newline is None else bool(newline)
        # stdin 文本编码（缺省 utf-8——与 bash 工具为子进程统一设置的 stdio
        # 编码一致，Python / Node / PowerShell 都按 UTF-8 读 stdin）。
        # 显式 encoding 始终优先（GBK 原生控制台程序传 'gbk'）。
        self.encoding = self._normalize_encoding(encoding)
        # read 的行数上限（默认 1000 行，与 bash 工具的 MAX_LINES 对齐）：
        # None = 默认；0 / 负数 = 不按行截断；非法值回退默认。
        self.max_lines = self._normalize_max_lines(max_lines)
        self.key = key
        self.path = path
        self.crop = crop
        # ── 窗口输入参数（move/click/drag/scroll/key/type） ──
        self.x = x
        self.y = y
        self.to_x = to_x
        self.to_y = to_y
        self.from_x = from_x
        self.from_y = from_y
        self.button = button
        self.count = count
        self.direction = direction
        self.amount = amount
        # ── 目标窗口选择（screenshot / 输入 op 通用）──
        self.window = window
        # ── 整屏 / 多显示器截取 ──
        self.screen = screen      # 全屏 / 多显示器截取（true / 'primary' / 序号）

    # ── 参数归一化 ───────────────────────────────────────

    @classmethod
    def _normalize_encoding(cls, encoding) -> str:
        """归一化 stdin 文本编码名（缺省 / 空串 → 默认编码 ``utf-8``）。

        默认值见 :func:`default_stdin_encoding`：跟随 bash 工具为子进程
        统一设置的 stdio 编码（UTF-8）；需要 GBK 的原生控制台程序显式传
        ``encoding='gbk'``。

        不在构造期校验编码名是否合法：非法编码名在真正写入时由
        :meth:`_op_stdin` 返回可读错误（``encoding='utf8x'`` 这类笔误能直接
        看到提示，而不是被静默改成默认值）。
        """
        if encoding is None:
            return default_stdin_encoding()
        text = str(encoding).strip()
        return text or default_stdin_encoding()

    @classmethod
    def _normalize_max_lines(cls, max_lines) -> int:
        """归一化 ``max_lines``：None/非法 → 默认 1000 行；<=0 → 0（不限）。"""
        if max_lines is None:
            return cls._READ_MAX_LINES_DEFAULT
        if isinstance(max_lines, bool):
            return cls._READ_MAX_LINES_DEFAULT
        try:
            value = int(str(max_lines).strip())
        except (TypeError, ValueError):
            return cls._READ_MAX_LINES_DEFAULT
        return value

    # ── execute ──────────────────────────────────────────

    async def execute(self) -> str:
        """按 task_id 和 op 操作后台 bash 任务，返回结果字符串。

        统一异常兜底：任何未预期的内部错误都转为可读文本（并记日志），
        避免异常穿透到工具框架、导致整轮对话失败。
        """
        try:
            return await self._execute_inner()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 工具层统一兜底
            logger.exception("bash_opt 执行异常 op=%s task=%s", self.op, self.task_id)
            return f"(bash_opt 内部错误（op={self.op}）: {exc})"

    async def _execute_inner(self) -> str:
        """按 task_id 和 op 操作后台 bash 任务，返回结果字符串。

        ★ 并非所有 op 都依赖后台任务：release 与具体进程 / 窗口无关；
        screenshot 在 screen=… 时截整屏 / 多显示器。这些 op 允许省略 task_id
        （或传一个已结束的 task_id），不再被任务生命周期绑死；其余 op 仍要求
        有效的 ``bg-xxx`` 记录，并给出与「任务不存在 / 已结束」相符的提示。
        """
        agent = getattr(self, 'agent', None)
        if agent is None:
            return "(后台任务操作需要关联 Agent 上下文，当前未关联)"
        background_tasks = getattr(agent, "_background_tasks", None)
        if background_tasks is None:
            # 无需任务的 op（release / screen 截图）不依赖该表；
            # 其余 op 仍要求已关联 Agent 的后台任务上下文。
            if not self._can_run_without_task():
                return "(后台任务操作需要关联 Agent 上下文，当前未关联)"
            background_tasks = {}

        supported_ops = ("read", "wait", "kill", "stdin", "close_stdin",
                         "keys", "screenshot", *INPUT_OPS)
        if self.op not in supported_ops:
            return f"(未知操作: {self.op}。支持: {'/'.join(supported_ops)})"

        taskless = self._can_run_without_task()

        # ★ task_id 前缀校验（与 subagent_opt 对称，双保险）：bash 后台任务
        #   id 恒为 "bg-xxx"，且注册在 bash 专用表 _background_tasks——subagent
        #   后台任务（sa-xxx）注册在独立的 _subagent_tasks 表，本工具查不到也
        #   不该操作；误传时直接提示走 subagent_opt。
        #   无需任务的 op 不受此前缀约束（它们本就与任务无关）。
        if self.task_id and not self.task_id.startswith("bg-") and not taskless:
            return (f"(错误：task_id 必须是 bash 后台启动（background=True）返回的 "
                    f"'bg-xxx' 格式 ID，当前: {self.task_id}。"
                    f"subagent 后台任务请用 subagent_opt 操作)")

        rec = (background_tasks.get(self.task_id)
               if self.task_id and self.task_id.startswith("bg-") else None)
        if rec is None and not taskless:
            if not self.task_id:
                return (f"(错误：op={self.op} 需要 task_id（bash background=True "
                        f"返回的 'bg-xxx' 格式 ID）。subagent 后台任务请用 "
                        f"subagent_opt 操作。无需任务的 op 只有 release 与 "
                        f"screen=… 的 screenshot)")
            reason = self._finished_reason(agent, self.task_id)
            if reason:
                return (f"(后台任务 {self.task_id} 已结束：{reason}。"
                        f"该任务已从后台任务表移除，无法再读取输出；"
                        f"需要新的后台任务请用 bash background=True 启动)")
            return (f"(后台任务不存在: {self.task_id}。"
                    f"请先用 bash background=True 启动后台任务获取 task_id)")

        # ★ 只有**接管型** op 才标记 managed_by_tool（见 _MANAGED_OPS 说明）：
        #   该任务的结果由大模型通过本工具主动获取（wait 拿到输出 / kill 终止
        #   / stdin / keys 交互），后续 _process_background_tasks 不再把结果
        #   作为用户消息自动插入，也不自动等待其完成（避免交互任务阻塞对话
        #   轮次）。只读观察型 op（read / screenshot）**不标记**，
        #   否则模型「read 看一眼进度」就会让任务完成结果再也回灌不到对话
        #   （被 _collect_done_background_messages 静默清理而模型毫不知情）。
        if rec is not None and self.op in self._MANAGED_OPS:
            rec["managed_by_tool"] = True

        if self.op == "read":
            return await self._op_read(rec)
        if self.op == "wait":
            return await self._op_wait(agent, rec)
        if self.op == "kill":
            return await self._op_kill(agent, rec)
        if self.op == "stdin":
            return await self._op_stdin(rec)
        if self.op == "close_stdin":
            return await self._op_close_stdin(rec)
        if self.op == "keys":
            return await self._op_keys(rec)
        if self.op == "screenshot":
            return await self._op_screenshot(rec)
        if self.op in INPUT_OPS:
            return await self._op_input(rec)
        return f"(未知操作: {self.op}。支持: {'/'.join(supported_ops)})"

    def _can_run_without_task(self) -> bool:
        """当前 op 是否可在没有后台任务记录时执行。

        与具体进程 / 窗口无关的 op 不依赖 task_id；screenshot 只有走整屏
        形态时才与任务无关。其余 op（read / wait / kill / stdin / keys /
        窗口输入）都需要任务记录里的进程句柄或窗口。
        """
        if self.op in self._TASKLESS_OPS:
            return True
        if self.op == "screenshot":
            return self._screen_requested()
        return False

    # ── 已结束后台任务的记忆（精确提示用） ───────────────

    @classmethod
    def _remember_finished(cls, agent, task_id: str, reason: str) -> None:
        """记下「任务已结束」及其原因（供后续同 task_id 操作给出精确提示）。

        记录挂在 agent 上一个轻量 FIFO 字典上（最多 :data:`_FINISHED_MEMORY`
        条），不改变 agent 的既有接口；写失败时静默忽略（仅影响提示友好度）。
        """
        if agent is None or not task_id:
            return
        try:
            memory = getattr(agent, cls._FINISHED_ATTR, None)
            if not isinstance(memory, dict):
                memory = {}
                setattr(agent, cls._FINISHED_ATTR, memory)
            memory[task_id] = str(reason)
            while len(memory) > cls._FINISHED_MEMORY:
                memory.pop(next(iter(memory)))
        except Exception:  # noqa: BLE001 - 记忆失败不影响主流程
            logger.debug("记录已结束后台任务失败（task=%s）", task_id, exc_info=True)

    @classmethod
    def _finished_reason(cls, agent, task_id: str) -> str | None:
        """查询 task_id 是否在「已结束」记忆中，返回原因（未记录时 None）。"""
        memory = getattr(agent, cls._FINISHED_ATTR, None)
        if not isinstance(memory, dict):
            return None
        reason = memory.get(task_id)
        return str(reason) if reason else None

    # ── op=read ──────────────────────────────────────────

    async def _op_read(self, rec: dict) -> str:
        """读取后台任务当前已产生的全部输出并清空缓冲，立即返回。

        read 为增量读取：任务运行期间的每一行输出累积到 read_buffer，
        本次读走全部内容并清空；任务继续运行，后续 read 只返回新输出，
        最终完整结果由 op=wait 获取。

        返回 JSON（task_id/status/output/total_lines/returned_lines/max_lines）：
          - status: 任务当前状态（running / completed）
          - output: 本次读取到的累积输出（读取后已清空缓冲）
          - 默认最多返回最后 :data:`_READ_MAX_LINES_DEFAULT` 行（``max_lines``
            可调整，``0`` 表示不按行截断）；超出行数时附 ``truncated`` /
            ``dropped_lines``，只保留**尾部最新**行。
          - 输出超过 :data:`_READ_OUTPUT_MAX_CHARS` 时同样只返回尾部最新内容
            并附 ``dropped_chars``；缓冲因内存上限裁剪过时附
            ``buffer_overflow_chars``（提示更早的输出已被丢弃）。

        ★ 本 op 属只读观察，**不接管任务生命周期**（不标记 managed_by_tool）：
        任务完成后其结果仍会由对话轮次正常回灌，不会因为「看了一眼进度」
        而丢失。
        """
        lock = rec.get("io_lock")
        if lock is not None:
            async with lock:
                output = rec.get("read_buffer", "")
                rec["read_buffer"] = ""
                buffer_dropped = int(
                    rec.pop(READ_BUFFER_DROPPED_KEY, 0) or 0)
        else:
            output = rec.get("read_buffer", "")
            rec["read_buffer"] = ""
            buffer_dropped = int(rec.pop(READ_BUFFER_DROPPED_KEY, 0) or 0)
        done = bool(rec.get("done"))
        status = rec.get("status") or ("completed" if done else "running")
        # ★ 行数截断（默认 1000 行，保留尾部最新行）：read 是增量读取，
        #   长时任务一次积压几万行时会整段灌进模型上下文；这里与 bash 工具的
        #   MAX_LINES 对齐，max_lines=0 关闭该限制（只受字符上限约束）。
        max_lines = self.max_lines if self.max_lines > 0 else 0
        total_lines = len(output.splitlines())
        dropped_lines = 0
        if max_lines and total_lines > max_lines:
            lines = output.splitlines(keepends=True)
            dropped_lines = total_lines - max_lines
            output = "".join(lines[-max_lines:])
        payload = {
            "task_id": self.task_id,
            "status": status,
            "output": output,
            "total_lines": total_lines,
            "returned_lines": total_lines - dropped_lines,
            "max_lines": max_lines,
        }
        if dropped_lines:
            payload["truncated"] = True
            payload["dropped_lines"] = dropped_lines
        if len(output) > self._READ_OUTPUT_MAX_CHARS:
            dropped = len(output) - self._READ_OUTPUT_MAX_CHARS
            payload["output"] = output[-self._READ_OUTPUT_MAX_CHARS:]
            payload["truncated"] = True
            payload["dropped_chars"] = dropped
        if buffer_dropped:
            payload["buffer_overflow_chars"] = buffer_dropped
        if payload.get("truncated") or buffer_dropped:
            payload["hint"] = (
                "输出过大，仅返回尾部最新内容；更早的内容已被丢弃"
                f"（默认最多返回最后 {self._READ_MAX_LINES_DEFAULT} 行，"
                "可用 max_lines 调整、max_lines=0 不按行截断；"
                "op=wait 返回任务最终结果，但 stdout/stderr 同样按 bash 工具的"
                " 1000 行上限截断；需要完整输出请让命令把输出重定向到文件后"
                "再按需读取）"
            )
        return json.dumps(payload, ensure_ascii=False)

    # ── op=wait ──────────────────────────────────────────

    async def _op_wait(self, agent, rec: dict) -> str:
        """等待任务完成并返回结果（JSON：task_id/status/stdout/stderr/returncode）。

        命令输出按 bash 三元 JSON 结构展开（stdout/stderr/returncode 分离）：
        优先读取任务记录中 _complete_background_task 写入的独立字段，
        缺失时回退解析 result 原文。

        完成（或已完成后）把任务记录从 tasklist 移除——大模型已通过本工具
        拿到输出，避免 _process_background_tasks 再以用户消息重复插入。

        ★ 使用 asyncio.wait 而非 wait_for：wait_for 超时会 cancel 后台任务
        本身（任务被误杀），wait 只观察不干预，超时后任务继续运行——
        超时返回**结构化 JSON**（task_id / status / timeout=True / done=False /
        waited / hint），不是散文错误：调用方可据此决定「再等 / op=read 看
        实时输出 / op=kill 终止」。
        """
        task = rec.get("task")
        if not rec.get("done") and task is not None:
            try:
                if self.timeout:
                    done, _pending = await asyncio.wait({task}, timeout=self.timeout)
                    if not done:
                        # 超时不是失败：任务仍在运行（wait 只观察不干预）。
                        # 返回结构化 JSON，与完成分支同一契约（task_id/status），
                        # 另带 timeout / done / waited / hint，调用方可直接判断
                        # 「再等 / 去看实时输出 / 杀掉」，不必解析散文。
                        return json.dumps({
                            "task_id": self.task_id,
                            "status": rec.get("status", "running"),
                            "timeout": True,
                            "done": False,
                            "waited": self.timeout,
                            "hint": ("等待超时，任务仍在运行：可再次 op=wait 继续等，"
                                     "或用 op=read 查看实时输出、op=kill 终止"),
                        }, ensure_ascii=False)
                else:
                    await task
            except asyncio.CancelledError:
                return f"(等待后台任务 {self.task_id} 被取消)"
            except Exception as e:
                logger.debug("后台任务 wait 异常: %s", e)

        # 读取最终结果（任务完成后由 _run_background_task 写入 rec），
        # 三元 JSON 展开为 stdout / stderr / returncode 独立字段
        if "stdout" in rec or "returncode" in rec:
            stdout = rec.get("stdout", "")
            stderr = rec.get("stderr", "")
            returncode = rec.get("returncode")
        else:
            stdout, stderr, returncode = _parse_bash_result_fields(
                rec.get("result", ""))
        status = rec.get("status", "completed")
        payload = {
            "task_id": self.task_id,
            "status": status,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
        }
        # 移除任务记录（避免 _process_background_tasks 重复插入用户消息）
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
        self._remember_finished(agent, self.task_id, "输出已被 op=wait 取走")
        return json.dumps(payload, ensure_ascii=False)

    # ── op=kill ──────────────────────────────────────────

    async def _op_kill(self, agent, rec: dict) -> str:
        """杀死后台任务的所有进程树并取消后台任务，从 tasklist 移除。

        ★ 杀死后**校验**：进程树杀（进程组 + 全部递归后代）在线程中执行，
        内部轮询确认进程是否真正退出（僵尸视为已终止），仍有存活则自动
        补杀（最多 3 轮尝试）。返回信息报告校验结论；确有残留（权限不足 /
        不可中断状态）时列出残留 PID，可再次 op=kill 重试补杀。
        """
        pid = rec.get("pid")
        process = rec.get("process")
        task = rec.get("task")

        # 1. 杀死进程树（进程组 + 递归后代；杀后校验、未死补杀）
        result = None
        target_pid = pid
        if target_pid is None and process is not None:
            target_pid = getattr(process, "pid", None)
        if target_pid is not None:
            try:
                # 同步重试 / 校验会短暂阻塞，移出事件循环线程执行。
                # create_time（Windows FILETIME）用于 pid 复用防护：任务早已
                # 结束、PID 被系统复用时拒绝杀，避免误杀无关进程树；
                # job_handle 用于 TerminateJobObject，覆盖 PPID 链断裂后
                # Toolhelp32 收集不到的孤儿后代。
                result = await asyncio.to_thread(
                    kill_process_tree, target_pid,
                    create_time=rec.get("pid_create_time"),
                    job_handle=rec.get("job_handle"),
                    extra_pids=rec.get("descendant_pids"),
                    extra_create_times=rec.get("descendant_create_times"),
                    winpid=rec.get("winpid"),
                    extra_win_pids=rec.get("descendant_win_pids"),
                    extra_win_create_times=rec.get("descendant_win_create_times"),
                    win_hint=rec.get("win_hint"),
                    win_hints=rec.get("win_hints"),
                    win_start_time=rec.get("win_start_time"),
                )
            except Exception as e:
                logger.debug("kill 进程树异常: %s", e)
        if result is None and process is not None:
            try:
                process.kill()
            except ProcessLookupError:
                pass  # 进程已退出
            except Exception as e:
                logger.debug("process.kill 异常: %s", e)

        # 2. 取消 asyncio 后台任务（若仍在运行）
        if task is not None and not task.done():
            task.cancel()
            try:
                await asyncio.wait({task}, timeout=2.0)
            except Exception:
                logger.debug("忽略异常（best-effort 路径）", exc_info=True)  # 任务取消过程异常忽略

        # 3. 校验仍有残留时保留任务记录，便于再次 op=kill 补杀
        residual = False
        if result is not None:
            residual = bool(
                getattr(result, "remaining_pids", ())
                or getattr(result, "remaining_win_pids", ())
                or getattr(result, "job_remaining", 0)
            )
        if residual:
            rec["managed_by_tool"] = True
            rec["status"] = "kill_failed"
            writer = rec.get("stdin_writer")
            if writer is not None:
                try:
                    writer.close()
                except Exception:  # pragma: no cover - 依赖底层句柄
                    logger.debug("kill 残留时关闭 stdin 写端失败", exc_info=True)
            rec["stdin_writer"] = None
            rec["master_fd"] = None
            return self._kill_report(result)

        # 4. 移除任务记录并更新 TUI 计数
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
        self._remember_finished(agent, self.task_id, "已被 op=kill 终止")
        return self._kill_report(result)

    def _kill_report(self, result) -> str:
        """组装 op=kill 的结果说明（含杀后校验结论与残留进程）。

        Args:
            result: ``kill_process_tree`` 返回的 ``KillResult``（缺省 None——
                无进程句柄、或旧实现 / 测试替身未返回校验结果时）。
        """
        if result is None:
            return f"(已杀死后台任务 {self.task_id} 及其所有进程树)"
        remaining = tuple(getattr(result, "remaining_pids", ()) or ())
        remaining_win = tuple(getattr(result, "remaining_win_pids", ()) or ())
        attempts = int(getattr(result, "attempts", 0) or 0)
        if getattr(result, "pid_reused", False):
            return (f"(未杀死后台任务 {self.task_id}：记录的 PID "
                    f"{getattr(result, 'pid', 0)} 已被系统复用"
                    f"（创建时间不匹配），为避免误杀无关进程已跳过进程树杀)")
        if not getattr(result, "verified", False):
            return (f"(已杀死后台任务 {self.task_id} 及其所有进程树"
                    f"（{attempts} 轮尝试，未做杀后校验）)")
        job_remaining = int(getattr(result, "job_remaining", 0) or 0)
        if not remaining and not remaining_win and not job_remaining:
            return (f"(已杀死后台任务 {self.task_id} 及其所有进程树"
                    f"（{attempts} 轮尝试后校验：无残留进程）)")
        parts: list[str] = []
        if remaining:
            parts.append(f"以下进程仍存活: {', '.join(str(p) for p in remaining)}")
        if remaining_win:
            parts.append(
                f"以下 Windows 进程仍存活: {', '.join(str(p) for p in remaining_win)}")
        if job_remaining:
            parts.append(f"Job 内仍有 {job_remaining} 个进程未退出")
        return (f"(已尝试杀死后台任务 {self.task_id} 的进程树（{attempts} 轮），"
                f"但校验时{'；'.join(parts)} —— 可能权限不足或处于"
                f"不可中断状态，可稍后重试 op=kill 补杀)")

    # ── op=stdin ─────────────────────────────────────────

    async def _op_stdin(self, rec: dict) -> str:
        """向后台任务 stdin 发送文本输入（按 :attr:`encoding` 编码）。

        默认编码 ``utf-8``：bash 工具的包装脚本把每个后台任务的子进程 stdio
        统一为 UTF-8（Console Input/OutputEncoding 与 ``PYTHONIOENCODING`` /
        ``PYTHONUTF8``），Python / Node / PowerShell 都按 UTF-8 读 stdin——
        按系统 ANSI 代码页（中文 Windows = GBK）写入会直接被判非法字节
        （实测 Python 3.11 抛 ``UnicodeDecodeError``）。需要 GBK 的旧式原生
        控制台程序显式传 ``encoding='gbk'``。
        ``newline`` 未指定或为 true 时按同一编码追加换行。

        ★ PTY 任务的「回车」是 **CR**
        （``\\r``）而不是 ``\\n``：终端里 ``\\n`` 只是下移一行、不会提交输入
        行（实测 ConPTY / POSIX PTY 均如此，交互式程序会一直等下去）。bash
        任务统一在 PTY 中运行，因此换行自动用 ``\\r``，与真实键盘回车一致。
        """
        if self.text is None:
            return "(stdin 操作需要 text 参数指定要发送的文本)"
        text = self.text if isinstance(self.text, str) else str(self.text)
        try:
            data = text.encode(self.encoding)
            # newline 未指定（None）或 true → 追加换行（保持「输入一行」语义）；
            # 显式 false 时原样发送。换行按同一编码编码（UTF-16 等宽编码不会
            # 退化成单字节 \n）；PTY 任务的「回车」是 CR（\r）。
            if self.newline is not False:
                newline = "\r" if rec.get("mode") == "pty" else "\n"
                data += newline.encode(self.encoding)
        except LookupError:
            return (f"(未知编码: {self.encoding}。请使用 Python 编码名，"
                    f"如 utf-8 / gbk / cp936 / gb18030)")
        except UnicodeEncodeError as exc:
            return (f"(文本无法按编码 {self.encoding} 编码: {exc.reason}；"
                    f"可改用 encoding='utf-8')")
        ok, err = await self._write_to_task(rec, data)
        if not ok:
            return err
        # ★ 登记输入回显（仅 Windows ConPTY）：ConPTY 无法关闭终端驱动的
        #   回显，读取端（bash._read_loop）按行尾匹配丢弃一次。POSIX 分支
        #   已在 openpty 后关闭 ECHO，剩下的都是程序真实输出——再登记会
        #   误删真实输出（实测 cat 的 "hello" 被当成回显丢弃）。
        if rec.get("mode") == "pty" and os.name == "nt":
            _remember_echo_lines(rec.get("echo_filter"), text)
        return (f"(已向后台任务 {self.task_id} 发送 stdin 输入"
                f"（编码 {self.encoding}）: "
                f"{self._sanitize_display(text)})")

    # ── op=close_stdin ───────────────────────────────────

    async def _op_close_stdin(self, rec: dict) -> str:
        """关闭后台任务的 stdin（发送 EOF）。

        依赖 stdin EOF 才会输出 / 退出的命令（``sort`` / ``findstr`` /
        ``cat`` / ``more`` / cmd 的 ``set /p`` 等）用 pipe 模式关闭写端后
        子进程读到 EOF 即可正常走完并输出，不必 kill 丢掉结果。

        - ``mode='pipe'``：关闭 stdin 写端（StreamWriter.close），并等
          ``wait_closed`` 完成（有超时，避免对端不响应时挂住）；
        - ``mode='pty'``：PTY **没有真正的 EOF 语义**，只能写入 EOT
          （``0x04``，Windows ConPTY 为 ``0x1a``）模拟 Ctrl+D / Ctrl+Z 按键。
          程序不一定会因此退出（实测 ``python -i -q`` 收到 EOT 后仍在运行），
          返回文本会如实说明；依赖 EOF 的命令请用 ``op=kill`` 结束任务
          （PTY master 关闭会同时断开输出读取，
          因此这里不能直接关闭 PTY）。**PTY 的 stdin 通道不会因此关闭**：
          EOT 只是一次按键，之后仍可继续 ``op=stdin`` / ``op=keys``。
        """
        mode = rec.get("mode")
        if mode is None:
            return (f"(后台任务 {self.task_id} 尚未就绪（进程句柄未建立），"
                    f"请稍后重试)")
        if mode == "pty":
            if rec.get("eot_sent"):
                return (f"(已向后台任务 {self.task_id} 的 PTY 发送过 EOT；"
                        f"PTY 没有真正的 EOF，通道仍可用——可继续 op=stdin / "
                        f"op=keys 写入，或再次发送 EOT)")
        elif rec.get("stdin_closed"):
            return f"(后台任务 {self.task_id} 的 stdin 已关闭，无需重复操作)"
        lock = rec.get("io_lock")
        if lock is not None:
            async with lock:
                return await self._close_stdin_unlocked(rec, mode)
        return await self._close_stdin_unlocked(rec, mode)

    async def _close_stdin_unlocked(self, rec: dict, mode: str) -> str:
        """在 io_lock 保护下关闭 stdin（pipe 关写端 / pty 写 EOT）。"""
        if mode == "pty":
            master_fd = rec.get("master_fd")
            if master_fd is None:
                return (f"(后台任务 {self.task_id} 无 PTY 句柄，进程可能已结束)")
            # Windows ConPTY 的 EOF 是 Ctrl+Z（\x1a）；POSIX PTY 是 Ctrl+D（\x04）
            eof_byte = b"\x1a" if os.name == "nt" else b"\x04"
            try:
                await asyncio.wait_for(
                    _write_pty_bytes(master_fd, eof_byte),
                    timeout=self._STDIN_CLOSE_TIMEOUT,
                )
            except asyncio.TimeoutError:
                return (f"(向后台任务 {self.task_id} 的 PTY 写入 EOT 超时"
                        f"（{self._STDIN_CLOSE_TIMEOUT} 秒），进程可能已无响应；"
                        f"可用 op=kill 终止)")
            except OSError as exc:
                return (f"(向后台任务 {self.task_id} 的 PTY 写入 EOT 失败: {exc})")
            # ★ 不置 stdin_closed（2026-10-20 修复）：PTY 没有真正的 EOF，
            #   写入 EOT 只是一次按键模拟（ICANON 下还只在行首生效，行内有
            #   内容时只是刷新输入缓冲）。把它当成「stdin 已关闭」会永久
            #   锁死交互通道——写一次 Ctrl+D 之后 op=stdin / op=keys 全部
            #   被拒，与实际语义不符。这里只记录「已发送过 EOT」用于提示。
            rec["eot_sent"] = True
            return (f"(已向后台任务 {self.task_id} 的 PTY 写入 EOT"
                    f"（ConPTY 为 Ctrl+Z、POSIX 为 Ctrl+D）；注意 PTY 没有真正的"
                    f" EOF：这只是模拟按键，程序不一定会退出（实测 python REPL "
                    f"收到 EOT 后仍在运行）。依赖 stdin EOF 才输出 / 退出的命令"
                    f"请用 op=kill 结束该"
                    f"任务。stdin 通道仍可用——需要继续交互可直接"
                    f" op=stdin / op=keys 继续写入)")

        if mode != "pipe":
            return f"(后台任务 {self.task_id} 写入模式异常: {mode})"

        stdin = rec.get("stdin_writer")
        if stdin is None:
            return (f"(后台任务 {self.task_id} 无 stdin 管道（可能已关闭或进程已结束）)")
        try:
            stdin.close()
            wait_closed = getattr(stdin, "wait_closed", None)
            if wait_closed is not None:
                await asyncio.wait_for(
                    wait_closed(), timeout=self._STDIN_CLOSE_TIMEOUT)
        except asyncio.TimeoutError:
            # 写端已 close，只是底层关闭握手没在时限内完成；保留句柄便于排查，
            # 但明确告知结果，避免模型反复重试。
            rec["stdin_closed"] = True
            rec["stdin_writer"] = None
            return (f"(已关闭后台任务 {self.task_id} 的 stdin 写端，但等待关闭"
                    f"完成超时（{self._STDIN_CLOSE_TIMEOUT} 秒）；子进程通常已"
                    f"读到 EOF，可用 op=wait 确认状态)")
        except (OSError, RuntimeError, ValueError) as exc:
            return (f"(关闭后台任务 {self.task_id} 的 stdin 失败: {exc})")
        rec["stdin_closed"] = True
        rec["stdin_writer"] = None
        return (f"(已关闭后台任务 {self.task_id} 的 stdin（写端已关闭，子进程将"
                f"读到 EOF）；依赖输入结束才输出 / 退出的命令现在可以正常完成，"
                f"用 op=wait 取最终结果)")

    # ── op=keys ──────────────────────────────────────────

    async def _op_keys(self, rec: dict) -> str:
        """向后台任务发送键盘消息（按被操作程序形态自动路由）。

        路由规则：

          - 目标进程（含其子进程）有可接收键盘输入的 **GUI 窗口** → 按键
            作为**窗口级键盘消息**注入该窗口（与 op=key 同一套合成事件，
            见 :meth:`_send_keys_to_window`）；
          - 没有 GUI 窗口 → 回退写入**终端**（PTY master / stdin 管道，
            ANSI/VT100 序列，见 :meth:`_send_keys_to_terminal`）；
          - 两者都不可用（无 GUI 窗口且无终端写入句柄）→ 返回错误说明。

        这样同一个 ``op=keys`` 既能操作 GUI 程序（游戏 / GUI 应用），
        也能操作纯命令行程序（编译 / 交互式 shell），无需模型先判断形态。
        """
        if self.key is None:
            return "(keys 操作需要 key 参数指定按键，如 key='up' / key='ctrl_c')"
        key_text = str(self.key)
        pid = rec.get("pid")
        window_pid = (pid if isinstance(pid, int) and not isinstance(pid, bool)
                      and pid > 0 else None)
        window_failure: str | None = None
        if window_pid is not None and await self._probe_window_async(window_pid):
            try:
                return await self._send_keys_to_window(window_pid, key_text)
            except (InputNoWindowError, InputError) as exc:
                # 探测到窗口但注入失败（窗口已关闭 / 无法置前等）：尝试终端回退
                window_failure = str(exc)
        ok, message = await self._send_keys_to_terminal(rec, key_text)
        if ok:
            return message
        if window_failure is not None:
            return (f"(按键发送失败：目标 GUI 窗口注入失败（{window_failure}）；"
                    f"终端备选通道也不可用（{self._plain(message)}）")
        if message.startswith("(按键解析失败"):
            # 键名在终端不可用（错误提示已含「改用 op=key」），且无 GUI 窗口
            return f"{message}（目标进程也没有可接收键盘输入的 GUI 窗口）"
        return (f"(按键发送失败：目标进程既没有可接收键盘输入的 GUI 窗口，"
                f"也没有可写入的终端句柄 —— {self._plain(message)}。"
                f"若为 GUI 程序请等窗口出现后用 op=key 注入（可用 op=screenshot "
                f"确认窗口状态）；纯命令行进程请确认已启动（可用 op=wait 查看状态）)")

    async def _probe_window_async(self, pid: int) -> bool:
        """探测目标进程是否有可接收键盘输入的 GUI 窗口（不阻塞事件循环）。

        ``probe_window`` 内部要枚举窗口 / 调系统 API（阻塞调用），因此放到
        线程池执行并加超时——与截图 / 输入注入的调用方式保持一致。超时或
        探测异常时按「没有 GUI 窗口」处理，交由终端通道兜底。
        """
        try:
            found = await asyncio.wait_for(
                asyncio.to_thread(probe_window, pid),
                timeout=self._INPUT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            logger.debug("窗口探测超时（pid=%s），按无 GUI 窗口处理", pid)
            return False
        except Exception:  # noqa: BLE001 - 探测失败不应中断按键发送
            logger.debug("窗口探测失败（pid=%s）", pid, exc_info=True)
            return False
        return found is not None

    async def _send_keys_to_window(self, pid: int, key_text: str) -> str:
        """把按键作为窗口级键盘消息注入目标 GUI 窗口。

        与 ``op=key`` 走同一条输入注入通道（Windows SendInput/PostMessage、
        X11 xdotool、macOS Quartz），键名规则亦共用；窗口创建有延迟时按
        :meth:`_send_input_with_retry` 的节奏重试。

        Args:
            pid: 目标进程 PID。
            key_text: 按键文本（与 ``op=key`` 同一套键名规则）。

        Raises:
            ActionError: 键名非法。
            InputNoWindowError: 注入时窗口已消失。
            InputError: 平台不支持或注入失败。
        """
        action = build_action("key", {"key": key_text})
        result = await self._send_input_with_retry(pid, action)
        payload = {
            "task_id": self.task_id,
            "op": "keys",
            "channel": "gui",
            "hint": ("检测到目标进程有 GUI 窗口，按键已作为窗口级键盘消息注入该窗口；"
                     "可用 op=screenshot 截图后 read_image 核对界面变化"
                     "（纯 GUI 按键可直接用 op=key）"),
        }
        payload.update(result.to_dict())
        return json.dumps(payload, ensure_ascii=False)

    async def _send_keys_to_terminal(self, rec: dict,
                                     key_text: str) -> tuple[bool, str]:
        """回退通道：按键转 ANSI/VT100 序列写入终端（PTY master / stdin）。

        Args:
            rec: 后台任务记录。
            key_text: 按键文本（与 ``op=key`` 同一套键名规则）。

        Returns:
            ``(成功, 消息)``——成功时为提示文本，失败时为错误文本（解析失败
            或通道不可用）。
        """
        try:
            sequence = parse_terminal_key(key_text)
        except ActionError as exc:
            return False, (
                f"(按键解析失败: {exc}。终端按键支持: "
                f"{', '.join(SUPPORTED_TERMINAL_KEYS)}、ctrl_a..ctrl_z、"
                f"alt+<字符>、shift+tab、单个字符；亦接受 esc/del/pageup/"
                f"return/ins/pgdn 等别名)"
            )
        payload = sequence.encode("utf-8")
        ok, err = await self._write_to_task(rec, payload)
        if not ok:
            return False, err
        note = self._terminal_key_note(rec, payload)
        return True, (f"(已向后台任务 {self.task_id} 发送终端按键: "
                      f"{key_text}{note})")

    @staticmethod
    def _terminal_key_note(rec: dict, payload: bytes) -> str:
        """按键写入「非终端」stdin（管道 / 重定向）时的提醒。

        控制字符与 ANSI 序列要靠终端（PTY）解释才会变成信号（``ctrl+c`` 的
        SIGINT）或按键事件；目标是管道时它们只是普通字节，程序既不中断也不
        会当快捷键处理。此时必须如实提示，避免「看起来发送成功、实际无效果」。
        """
        if not any(byte < 0x20 or byte == 0x7F for byte in payload):
            return ""
        mode = rec.get("mode")
        if mode == "pipe":
            return ("；注意：目标进程的 stdin 不是终端（PTY）而是管道 / 重定向，"
                    "控制字符不会被解释为信号或快捷键（例如 ctrl+c 不会中断进程，"
                    "需要结束任务请用 op=kill）")
        if mode == "pty":
            if os.name == "nt":
                return ("；提示：Windows ConPTY 下控制字符能否变成 Ctrl+C 事件"
                        "取决于目标程序（实测可中断 Python 等进程，但部分程序不"
                        "响应）——若进程没有反应，请用 op=kill 终止任务")
            # POSIX PTY：子进程已建立控制终端（setsid + TIOCSCTTY），正常情况下
            # ctrl+c 会变成 SIGINT；程序可能自行忽略 / 接管该信号，仍给出兜底提示。
            return ("；提示：若目标进程没有响应（例如自行忽略 SIGINT），"
                    "可用 op=kill 终止")
        return ""

    @staticmethod
    def _plain(message: str) -> str:
        """去掉错误文本最外层括号（嵌入更长的说明时避免括号嵌套）。"""
        text = str(message).strip()
        if text.startswith("(") and text.endswith(")"):
            return text[1:-1]
        return text

    # ── op=screenshot ────────────────────────────────────

    # ── 已结束任务的统一提示（screenshot / 输入 op） ──────────

    @staticmethod
    def _task_windows_alive(rec: dict) -> bool:
        """任务记录登记的原生 Windows 进程是否仍存活。

        App Execution Alias（notepad / mspaint 等 Store 应用）或经 explorer
        启动的 GUI：任务根进程（Cygwin bash）启动完就退出，任务因此被标记
        completed，但真实 GUI 进程仍在运行。此时 screenshot / 输入 op 仍
        应可用——先看登记表里的 Windows PID / Job 成员 / exe 名提示。
        """
        try:
            from . import _task_winpid, _win_proc
        except Exception:  # pragma: no cover - 依赖导入链
            return False
        pid = rec.get("pid")
        candidates: set[int] = set()
        for value in (rec.get("winpid"), _task_winpid.winpid_for(pid) if pid else None):
            if value:
                candidates.add(int(value))
        job = rec.get("job_handle")
        if job:
            try:
                candidates.update(_task_winpid.job_processes(job))
            except Exception:  # pragma: no cover - 依赖系统调用
                logger.debug("查询任务 Job 内进程失败: job=%s", job, exc_info=True)
        hint = rec.get("win_hint")
        hints = rec.get("win_hints")
        start_time = rec.get("win_start_time")
        if hint or hints:
            # 与 op=kill 共用同一套「按名 + 启动时间窗」兜底：覆盖 daemon 化的
            # GUI（mintty）与 Store 应用；时间上界避免把用户中途打开的同名
            # 程序算进本任务。
            from ._bash_support import _merge_hints, _win_pids_for_hint
            try:
                for candidate in _win_pids_for_hint(
                        _merge_hints(hint, hints), start_time):
                    candidates.add(int(candidate))
            except Exception:  # pragma: no cover - 依赖系统调用
                logger.debug("按名兜底查找 Windows 进程失败", exc_info=True)
        for candidate in candidates:
            try:
                if _win_proc.is_process_alive(candidate):
                    return True
            except Exception:  # pragma: no cover - 依赖系统调用
                continue
        return False

    @classmethod
    def _ended_task_note(cls, rec: dict) -> str | None:
        """任务已结束时返回状态描述，否则返回 ``None``。

        screenshot / 输入 op 只对**运行中的进程**有意义：任务
        结束后进程（及其窗口）已不存在，继续按 PID 枚举窗口只会得到
        「没有可截图的可见窗口」，把「任务已结束」误报成「窗口不可见」，
        模型据此反复 restore / 重试。这里统一给出可读状态。

        ★ App Execution Alias / detached GUI：根进程退出但真实 GUI 进程
        仍存活（登记表可查），此时不算「任务已结束」。
        """
        if not rec.get("done"):
            return None
        if cls._task_windows_alive(rec):
            return None
        status = str(rec.get("status") or "completed")
        returncode = rec.get("returncode")
        rc = "" if returncode is None else f"，returncode={returncode}"
        return f"已结束（{status}{rc}），进程已退出，窗口不再存在"

    def _ended_task_reply(self, rec: dict, action: str) -> str | None:
        """任务已结束时生成统一错误回复（未结束返回 ``None``）。"""
        note = self._ended_task_note(rec)
        if note is None:
            return None
        return (f"(后台任务 {self.task_id} {note}，无法{action}；"
                f"可用 op=wait 查看最终输出，需要{action}请用 bash "
                f"background=True 重新启动程序并保持运行)")

    async def _op_screenshot(self, rec: dict) -> str:
        """把后台任务进程树（含其启动的 GUI 子进程）的窗口截图存为 PNG。

        适用于后台命令运行图形程序的场景（游戏 / GUI 应用 / 渲染预览）：
        按 task_id 的进程 PID 定位窗口并抓取像素，产物写入 path 指定的
        文件；crop 非空时只写出指定像素区域（「指定大小」能力），省略时
        输出整窗原始像素。截图完成返回 JSON（path/width/height/
        window_pid/window_title，裁剪时附 crop），大模型随后可用
        read_image 查看画面。
        """
        if not self.path or not str(self.path).strip():
            return ("(screenshot 操作需要 path 参数指定截图保存的文件路径，"
                    "如 path='shot.png' 或 path='/tmp/shot.png')")
        # ★ 整屏截图不依赖任务窗口（任务结束后仍可截桌面），窗口截图才需要
        #   进程存活；先给出「任务已结束」而不是让窗口枚举报「没有可见窗口」。
        if not self._screen_requested():
            ended = self._ended_task_reply(rec, "截图")
            if ended:
                return ended
        try:
            crop = self._resolve_crop()
        except CropError as exc:
            return f"(截图裁剪参数非法: {exc})"
        try:
            target_path = self._prepare_screenshot_path(str(self.path))
        except ValueError as exc:
            return f"(截图路径非法: {exc})"
        try:
            info = await self._screenshot_target(
                rec, target_path=target_path, crop=crop,
                window=self.window, screen=self.screen)
        except MonitorError as exc:
            return f"(截图失败: {exc})"
        except (SelectorError, ScreenshotError, CropError, ValueError) as exc:
            scope = f"（window 选择器 {self.window!r}）" if self.window else ""
            return f"(截图失败{scope}: {exc})"
        result = info["result"]
        region = info["region"]
        payload: dict = {"task_id": self.task_id, "op": "screenshot"}
        for key in ("screen", "monitor", "monitor_index", "monitors_total"):
            if info.get(key) is not None:
                payload[key] = info[key]
        payload.update(result.to_dict())
        notes = ["截图已保存"]
        if self._screen_requested() and self.window:
            notes.append("整屏 / 多显示器截取与 window 无关，已忽略该参数")
        if region is not None:
            payload["crop"] = region.to_dict()
            notes.append("已按 crop 裁剪")
        if self.window:
            payload["window"] = str(self.window)
        notes.append(
            f"截图像素左上角对应屏幕坐标 ({result.window_x},{result.window_y})"
            f"（screen = 原点 + 截图像素；输入 op 的坐标即以本产物左上角为原点）"
        )
        if result.window_rect:
            rect = result.window_rect
            box_label = "显示器区域" if self._screen_requested() else "窗口外框"
            notes.append(
                f"{box_label} {rect['width']}x{rect['height']}"
                f"@({rect['x']},{rect['y']})（产物 {result.width}x{result.height} "
                f"与它的差值来自窗口装饰 / DWM 黑边 / crop，二者不必相等）"
            )
            offset_x = result.window_x - rect["x"]
            offset_y = result.window_y - rect["y"]
            if result.width != rect["width"] or result.height != rect["height"]:
                # ★ 坐标换算（真实用例：最大化窗口 2582x1390@(-11,-11) 截图
                #   只有 2560x1368，原点 (0,0)）：截图像素原点 = 屏幕
                #   (window_x, window_y)，与窗口外框左上角相差 (offset_x,
                #   offset_y)。模型若直接拿 window_rect 的坐标 + 像素偏移，
                #   会整体偏掉这几十像素；这里把换算公式写清楚。
                notes.append(
                    f"截图原点相对{box_label}左上角偏移 ({offset_x},{offset_y})"
                    f"（DWM 不可见边框 / 窗口超出屏幕被裁剪 / crop）；"
                    f"窗口内坐标 = 截图像素 + ({offset_x},{offset_y})，"
                    f"输入 op 仍以截图像素左上角为原点"
                )
            if not self._screen_requested() and (
                    rect["x"] < 0 or rect["y"] < 0
                    or result.width < rect["width"]
                    or result.height < rect["height"]):
                notes.append("窗口部分超出屏幕时，截图只包含屏幕内可见部分")
        if info.get("monitors_total", 0) > 1 and self._screen_requested():
            notes.append(
                f"当前共 {info['monitors_total']} 个显示器（本次为第 "
                f"{info.get('monitor_index')} 个）；screen=true/'all' 截取整个"
                f"虚拟桌面，screen=序号 只截单个显示器"
            )
        payload["hint"] = ("，".join(notes) +
                           "，可用 read_image 工具读取该文件查看画面")
        return json.dumps(payload, ensure_ascii=False)

    def _resolve_crop(self) -> CropRegion | None:
        """解析 crop 参数为裁剪区域（省略 / 空白 → None，输出整窗）。

        Raises:
            CropError: 参数格式非法（非 4 个整数 / 数值非法）。
        """
        raw = self.crop
        if raw is None or not str(raw).strip():
            return None
        return CropRegion.parse(str(raw))

    async def _capture_with_retry(self, pid: int, path: str,
                                  crop: CropRegion | None = None,
                                  *, window: str | None = None):
        """截图（GUI 程序窗口创建有延迟时轮询重试）。

        仅在「目标暂无可见窗口」时重试（NoWindowError）；其它错误（含
        裁剪参数越界、窗口选择器无匹配）立即返回。每轮截图在线程中执行
        （GDI 调用阻塞），并受 _SCREENSHOT_TIMEOUT 保护。

        Args:
            pid: 目标进程 PID。
            path: 输出 PNG 路径。
            crop: 可选裁剪区域。
            window: 可选窗口选择器（缺省 = 主窗口）。

        Returns:
            CaptureResult（成功后）。

        Raises:
            ScreenshotError: 所有重试均失败，截图命令超时/异常，或裁剪越界。
            SelectorError: 窗口选择器无匹配窗口。
        """
        kwargs: dict = {}
        if crop is not None:
            kwargs["crop"] = crop
        if window:
            kwargs["window"] = window
        deadline = time.monotonic() + self._SCREENSHOT_WAIT_SECONDS
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(
                        lambda: capture_process_window(pid, path, **kwargs)),
                    timeout=self._SCREENSHOT_TIMEOUT,
                )
            except NoWindowError as exc:
                if time.monotonic() >= deadline:
                    raise
                logger.debug("截图重试（暂无窗口）: %s", exc)
                await asyncio.sleep(self._SCREENSHOT_RETRY_INTERVAL)
            except asyncio.TimeoutError:
                raise ScreenshotError(
                    f"截图超时（超过 {self._SCREENSHOT_TIMEOUT:g} 秒）："
                    f"窗口无响应或截图命令卡死"
                ) from None

    @staticmethod
    def _screen_spec_requested(raw) -> bool:
        """``screen`` 取值是否表示「整屏 / 多显示器」截取。

        ``true`` / ``'all'`` / ``'virtual'`` / ``0`` / ``'primary'`` / 序号都
        表示启用；``None`` / ``false`` / ``off`` / 空串表示关闭（窗口截图）。
        """
        if raw is None:
            return False
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        return text not in ("", "false", "no", "off")

    def _screen_requested(self) -> bool:
        """本工具实例是否要求整屏 / 多显示器截图。"""
        return self._screen_spec_requested(self.screen)

    async def _resolve_monitor(self, spec=None):
        """按 ``spec``（缺省取实例 ``screen``）挑出显示器，返回
        ``(Monitor, 序号, 总数)``。

        ``screen`` 的布尔真值（``True``）与文本真值（``'true'`` / ``'yes'`` /
        ``'on'``，大小写与空白不敏感）都表示整个虚拟桌面——上游常把 JSON
        布尔转成字符串，工具层必须与 ``op=screenshot`` 的文档语义一致。

        Raises:
            MonitorError: 平台枚举不到显示器或选择越界。
        """
        try:
            monitors = await asyncio.wait_for(
                asyncio.to_thread(list_monitors), timeout=self._INPUT_TIMEOUT)
        except asyncio.TimeoutError:
            raise MonitorError(
                f"枚举显示器超时（超过 {self._INPUT_TIMEOUT:g} 秒）：系统无响应"
            ) from None
        value = self.screen if spec is None else spec
        monitor = resolve_monitor(
            monitors, value if not isinstance(value, bool) else None)
        index = 0
        for position, item in enumerate(monitors, start=1):
            if item == monitor:
                index = position
                break
        return monitor, index, len(monitors)

    async def _capture_screen_with_retry(self, monitor, path: str, crop):
        """整屏 / 多显示器截图（在线程中执行并受超时保护）。"""
        kwargs: dict = {}
        if crop is not None:
            kwargs["crop"] = crop
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(
                    lambda: capture_screen(monitor, path, **kwargs)),
                timeout=self._SCREENSHOT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ScreenshotError(
                f"全屏截图超时（超过 {self._SCREENSHOT_TIMEOUT:g} 秒）"
            ) from None

    async def _screenshot_target(self, rec: dict, *, target_path: str, crop,
                                 window, screen):
        """执行一次截图（窗口 / 整屏），返回结果与元信息。

        Returns:
            ``{result, region, screen?, monitor?, ...}``。

        Raises:
            ScreenshotError / SelectorError / CropError: 截图失败。
        """
        if self._screen_spec_requested(screen):
            monitor, index, total = await self._resolve_monitor(screen)
            result = await self._capture_screen_with_retry(
                monitor, target_path, crop)
            return {
                "result": result, "region": crop,
                "screen": screen, "monitor": monitor.to_dict(),
                "monitor_index": index, "monitors_total": total,
            }
        pid = rec.get("pid")
        if pid is None:
            raise ScreenshotError(
                f"后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                f"无法截图；需要整屏截图可传 screen=true"
            )
        result = await self._capture_with_retry(
            pid, target_path, crop, window=window)
        return {"result": result, "region": crop}

    @staticmethod
    def _prepare_screenshot_path(path: str) -> str:
        """规范化截图输出路径：展开 ~、换算 Windows 盘符、补 .png、建父目录、
        安全校验。

        Raises:
            ValueError: 路径为空、指向目录、或未通过安全校验。
        """
        expanded = normalize_user_path(path)
        if not expanded:
            raise ValueError("路径为空")
        absolute = os.path.abspath(expanded)
        if os.path.isdir(absolute):
            raise ValueError(f"目标路径是一个目录: {absolute}")
        if not os.path.splitext(absolute)[1]:
            absolute += ".png"
        validate_path_security(absolute)
        parent = os.path.dirname(absolute)
        try:
            if parent:
                os.makedirs(parent, exist_ok=True)
        except OSError as exc:
            raise ValueError(f"无法创建父目录 {parent}: {exc}") from exc
        return absolute

    # ── op=窗口输入（move/click/drag/scroll/key/type） ────

    async def _op_input(self, rec: dict) -> str:
        """向后台任务的目标 GUI 窗口注入鼠标 / 键盘 / 文本输入。

        坐标以窗口截图左上角为原点（与 op=screenshot 产物一致），便于
        「先截图看清界面，再按像素点操作」。``window`` 可把输入投向指定窗口
        （右键菜单、下拉浮层、对话框等独立顶层窗口）。传 ``path`` 时在输入
        注入完成后立即截取同一目标窗口（文件名由 path 指定），结果并入
        ``screenshot`` 字段；不传 path 不产生截图。

        结果返回 JSON（task_id/op 与动作细节如按钮、坐标、屏幕坐标、按键序列、
        投递方式；带 path 时含 screenshot / screenshot_error）。
        """
        pid = rec.get("pid") if rec else None
        if pid is None and self.op != "release":
            if rec is None:
                return (f"(错误：op={self.op} 需要 task_id（后台任务的进程句柄）；"
                        f"只有 release 可以在没有任务时执行（全局释放按下的键 / "
                        f"鼠标按钮）)")
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法注入输入。可用 op=wait 查看任务状态)")
        try:
            action = self._build_input_action()
        except (ActionError, ValueError) as exc:
            return f"(输入参数非法: {exc})"
        try:
            result = await self._send_input_with_retry(pid, action)
        except (InputNoWindowError, InputError) as exc:
            message = str(exc)
            if "客户区" in message:
                return (f"(输入失败: {message}。该窗口没有可换算的客户区，无法投递"
                        f"窗口消息鼠标坐标；可先让目标窗口取得前台（点击其窗口区域）"
                        f"后用合成输入重试)")
            return f"(输入失败: {message})"
        # ★ GUI 输入 op 的 path 语义：输入注入成功**之后**再截目标窗口
        #   （同一 window 选择器），文件名由 path 指定；不传 path 则只注入。
        #   截图失败只写 screenshot_error 字段，不回滚 / 不掩盖已完成的输入。
        shot_requested = bool(self.path and str(self.path).strip())
        hint = ("输入已注入；已尝试按 path 自动截图，结果见 screenshot / "
                "screenshot_error，可用 read_image 读取该文件核对"
                if shot_requested else
                "输入已注入；需要核对界面变化可传 path 自动截图，或改用 "
                "op=screenshot 截图后用 read_image 查看")
        payload = {
            "task_id": self.task_id,
            "op": self.op,
            "hint": (hint + "（坐标原点为窗口截图左上角，与 op=screenshot "
                     "产物一致；window 选择器可用 'main' / '#N' / 'popup' / "
                     "'title:子串' / 'handle:0x…'）"),
        }
        if self.window:
            payload["window"] = str(self.window)
        payload.update(result.to_dict())
        if shot_requested:
            payload.update(await self._capture_after_input(rec))
        return json.dumps(payload, ensure_ascii=False)

    async def _capture_after_input(self, rec: dict) -> dict:
        """GUI 输入完成后按 ``path`` 截图，返回并入输入结果 JSON 的字段。

        成功时给出 ``{"screenshot": {...}}``（字段与 op=screenshot 的结果
        一致：path/width/height/window_* 等，crop 生效时附 crop）；失败时
        给出 ``{"screenshot_error": "..."}``——输入已经生效，截图失败不得
        让整个操作看起来没做，模型可据提示改用 op=screenshot 单独重截。

        固定截**目标窗口**（与输入时的 ``window`` 选择器一致）；整屏 /
        多显示器形态仍只由 op=screenshot 的 ``screen`` 参数触发，输入 op
        不接受它，避免「点一下却截了整块桌面」的意外产物。
        """
        if not rec or rec.get("pid") is None:
            return {"screenshot_error": (
                "该操作没有可截图的窗口（release 可在无 task_id 时执行），"
                "未自动截图；需要截图请用 op=screenshot 指定 task_id 或 "
                "screen=true")}
        try:
            crop = self._resolve_crop()
        except CropError as exc:
            return {"screenshot_error": f"截图裁剪参数非法: {exc}"}
        try:
            target_path = self._prepare_screenshot_path(str(self.path))
        except ValueError as exc:
            return {"screenshot_error": f"截图路径非法: {exc}"}
        try:
            info = await self._screenshot_target(
                rec, target_path=target_path, crop=crop,
                window=self.window, screen=None)
        except (MonitorError, SelectorError, ScreenshotError,
                CropError, ValueError) as exc:
            scope = f"（window 选择器 {self.window!r}）" if self.window else ""
            return {"screenshot_error": f"截图失败{scope}: {exc}"}
        result = info["result"]
        shot: dict = result.to_dict()
        if info["region"] is not None:
            shot["crop"] = info["region"].to_dict()
        shot["hint"] = (
            "输入已生效并完成截图，可用 read_image 读取该文件查看变化；"
            f"截图像素左上角对应屏幕坐标 ({result.window_x},{result.window_y})，"
            "输入 op 的坐标即以本产物左上角为原点"
        )
        return {"screenshot": shot}

    def _build_input_action(self):
        """把工具参数打包为输入动作（type 的 newline 语义在此落地）。"""
        text = self.text
        if self.op == "type" and self.newline:
            text = (text or "") + "\n"
        params = {
            "x": self.x, "y": self.y,
            "to_x": self.to_x, "to_y": self.to_y,
            "from_x": self.from_x, "from_y": self.from_y,
            "button": self.button, "count": self.count,
            "key": self.key, "text": text,
            "direction": self.direction, "amount": self.amount,
            "window": self.window,
        }
        return build_action(self.op, params)

    async def _send_input_with_retry(self, pid: int, action):
        """注入输入（GUI 程序窗口创建有延迟时轮询重试）。

        仅在「暂无窗口」时重试（InputNoWindowError）；参数类错误立即抛出。
        每轮注入在线程中执行（系统输入合成 / 外部命令调用阻塞），并受
        _INPUT_TIMEOUT 保护。

        Raises:
            ActionError / InputError: 参数非法、平台不支持、注入失败或超时。
        """
        deadline = time.monotonic() + self._INPUT_WAIT_SECONDS
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(send_window_input, pid, action),
                    timeout=self._INPUT_TIMEOUT,
                )
            except InputNoWindowError as exc:
                if time.monotonic() >= deadline:
                    raise
                logger.debug("输入注入重试（暂无窗口）: %s", exc)
                await asyncio.sleep(self._INPUT_RETRY_INTERVAL)
            except asyncio.TimeoutError:
                raise InputError(
                    f"输入注入超时（超过 {self._INPUT_TIMEOUT:g} 秒）：注入调用无响应"
                ) from None

    # ── 写入辅助 ─────────────────────────────────────────

    async def _write_to_task(self, rec: dict, data: bytes) -> tuple[bool, str]:
        """向后台任务写入字节（PTY master 或 PIPE stdin），返回 (成功, 错误消息)。"""
        mode = rec.get("mode")
        if mode is None:
            return (False,
                    f"(后台任务 {self.task_id} 尚未就绪（进程句柄未建立），"
                    f"请稍后重试)")
        if rec.get("stdin_closed"):
            return (False,
                    f"(后台任务 {self.task_id} 的 stdin 已被 op=close_stdin 关闭，"
                    f"无法再写入；需要继续交互请重新启动任务)")
        lock = rec.get("io_lock")
        if lock is not None:
            async with lock:
                return await self._write_unlocked(rec, data, mode)
        return await self._write_unlocked(rec, data, mode)

    async def _write_unlocked(self, rec: dict, data: bytes, mode: str) -> tuple[bool, str]:
        """在 io_lock 保护下实际写入字节。"""
        try:
            if mode == "pty":
                master_fd = rec.get("master_fd")
                if master_fd is None:
                    return (False,
                            f"(后台任务 {self.task_id} 无 PTY 句柄，进程可能已结束)")
                # 平台差异由 _write_pty_bytes 处理：POSIX 非阻塞重试，
                # Windows ConPTY 在线程里写（阻塞管道不能阻塞事件循环）。
                await _write_pty_bytes(master_fd, data)
            elif mode == "pipe":
                stdin = rec.get("stdin_writer")
                if stdin is None:
                    return (False,
                            f"(后台任务 {self.task_id} 无 stdin 管道，进程可能已结束)")
                stdin.write(data)
                try:
                    await stdin.drain()
                except (ConnectionResetError, BrokenPipeError):
                    return (False,
                            f"(后台任务 {self.task_id} 的 stdin 管道已关闭，进程可能已结束)")
            else:
                return (False, f"(后台任务 {self.task_id} 写入模式异常: {mode})")
        except (OSError, ValueError, RuntimeError) as e:
            return (False, f"(写入后台任务 {self.task_id} 失败: {e})")
        return (True, "")
