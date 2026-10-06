"""
bash_opt — 按 task_id 操作后台 bash 任务

配合 bash 工具 background=True 模式使用。bash 后台启动后返回
{"task_id": "bg-xxx", "status": "running"}，
大模型可据此用 bash_opt 工具按 task_id 操作：

- op=read   读取后台命令**当前已产生**的全部输出并清空缓冲，立即返回（不等待完成）
- op=wait   等待任务执行完成并获取结果（JSON：task_id/status/stdout/stderr/returncode）
- op=kill   杀死后台命令的所有进程树（killpg + /proc 递归补杀后代）
- op=stdin  向后台命令的 stdin 发送文本输入（text 参数，newline 可选是否追加换行）
- op=keys   向后台命令发送光标/键盘消息（跨平台 ANSI/VT100 转义序列）
- op=screenshot  把后台命令（及其子进程）的窗口截图保存为 PNG
                 （path 参数指定文件路径，可选 crop 参数指定只截取的像素区域）
- op=move / click / drag / scroll / key / type
                 向后台命令的 **GUI 窗口**注入鼠标 / 键盘 / 文本输入
                 （鼠标按钮、双击、拖动、滚轮、组合键、任意 Unicode 文本）

read 为**增量读取**：后台任务运行期间的每一行输出都会累积到内部缓冲，
每次 read 取走当前全部累积内容并清空，适合实时观察长时任务（编译/下载/
日志流）的进度；任务最终完整结果仍由 op=wait 获取。

截图（screenshot）适用于后台任务运行的是**图形界面程序**（游戏、GUI 应用、
渲染预览等）的场景：按 task_id 定位该命令产生的进程树，取其可见窗口像素
写盘（Windows 用 PrintWindow/BitBlt；Linux 用 ImageMagick import/xwd；
macOS 用 screencapture）。默认输出整窗原始像素；需要「指定大小」时可传
crop='x,y,width,height' 只截取窗口内的像素区域（以整窗截图左上角为原点，
区域越界报错并提示窗口实际尺寸）。产物为 PNG，可用 read_image 查看画面。
纯命令行进程没有窗口，此时返回可读的错误说明。

窗口输入（move/click/drag/scroll/key/type）同样按 task_id 定位该命令进程树
的可见窗口，坐标以**窗口截图左上角**为原点（与 op=screenshot 产物一致，
便于「先截图看清界面，再按像素点操作」）：click 支持左/右/中键与双击，
drag 支持按住左/右/中键拖拽（带轨迹插值），scroll 支持上下左右滚动，
key 支持 ctrl+shift+s 之类的组合键，type 逐字符输入任意 Unicode 文本。
Windows 用 SendInput（必要时回退 PostMessage 投递）、Linux 用 xdotool、
macOS 用 Quartz/cliclick + osascript；平台工具缺失时返回带安装提示的错误。

键盘消息跨平台说明：VT100/ANSI 转义序列是终端输入的标准语义，被 Linux/
macOS/Android(Termux) 的 PTY 与 Windows 的 ConPTY/Windows Terminal 统一
接受。按键名（如 up/down/ctrl_c）映射为对应字节序列，经 PTY master 或
stdin 管道写入后台进程，不依赖平台特定 API。op=keys 面向**终端程序**，
op=key 面向**GUI 窗口**（合成窗口级按键事件），二者按被操作程序的形态选用。
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
import time

from .base import Func
from .bash import kill_process_tree
from .file_ops import validate_path_security
from ._screenshot import (
    CropError,
    CropRegion,
    NoWindowError,
    ScreenshotError,
    capture_process_window,
)
from ._window_input import (
    INPUT_OPS,
    ActionError,
    InputError,
    NoWindowError as InputNoWindowError,
    build_action,
    send_window_input,
)
from ..core.base_agent import _parse_bash_result_fields

logger = logging.getLogger(__name__)

# ── 键盘消息映射表（跨平台 ANSI/VT100） ───────────────────
# VT100/ANSI 转义序列是终端输入的标准语义（ECMA-48 / xterm），
# 在 Linux/macOS/Android(Termux) 的 PTY 和 Windows 的 ConPTY/
# Windows Terminal 中都被统一接受，不依赖平台特定 API。
_KEY_SEQUENCES: dict[str, str] = {
    # 光标键
    "up": "\x1b[A",
    "down": "\x1b[B",
    "right": "\x1b[C",
    "left": "\x1b[D",
    # 编辑键
    "home": "\x1b[H",
    "end": "\x1b[F",
    "page_up": "\x1b[5~",
    "page_down": "\x1b[6~",
    "insert": "\x1b[2~",
    "delete": "\x1b[3~",
    "backspace": "\x7f",   # DEL（多数终端 Backspace 发送 DEL）
    "tab": "\t",
    "enter": "\r",
    "escape": "\x1b",
    "space": " ",
    # 功能键（F1-F4 用 SS3 前缀，F5-F12 用 CSI 前缀）
    "f1": "\x1bOP",
    "f2": "\x1bOQ",
    "f3": "\x1bOR",
    "f4": "\x1bOS",
    "f5": "\x1b[15~",
    "f6": "\x1b[17~",
    "f7": "\x1b[18~",
    "f8": "\x1b[19~",
    "f9": "\x1b[20~",
    "f10": "\x1b[21~",
    "f11": "\x1b[23~",
    "f12": "\x1b[24~",
}

# 常用控制组合（ctrl_a..ctrl_z = 0x01..0x1A，其余程序化生成）
_CTRL_KEYS: dict[str, str] = {
    "ctrl_c": "\x03",   # 中断（SIGINT）
    "ctrl_d": "\x04",   # EOF（退出输入）
    "ctrl_z": "\x1a",   # 挂起（SIGTSTP）
    "ctrl_l": "\x0c",   # 清屏（clear）
    "ctrl_r": "\x12",   # 反向搜索历史
    "ctrl_u": "\x15",   # 删除光标到行首
    "ctrl_w": "\x17",   # 删除前一个词
}


def _resolve_key(key: str) -> str | None:
    """将按键名解析为终端输入字节序列（ANSI/VT100，跨平台）。

    支持：
      - 光标键：up / down / left / right
      - 编辑键：home / end / page_up / page_down / insert / delete /
        backspace / tab / enter / escape / space
      - 功能键：f1 - f12
      - 控制组合：ctrl_a .. ctrl_z、ctrl_c / ctrl_d / ctrl_z 等

    按键名不区分大小写，下划线与连字符等价（ctrl_c == ctrl-c）。
    未知按键返回 None。
    """
    normalized = key.strip().lower().replace("-", "_")
    if normalized in _KEY_SEQUENCES:
        return _KEY_SEQUENCES[normalized]
    if normalized in _CTRL_KEYS:
        return _CTRL_KEYS[normalized]
    # 程序化生成 ctrl_<letter>（0x01..0x1A）
    if normalized.startswith("ctrl_"):
        letter = normalized[len("ctrl_"):]
        if len(letter) == 1 and "a" <= letter <= "z":
            return chr(ord(letter) - ord("a") + 1)
    return None


async def _write_pty_all(fd: int, data: bytes) -> None:
    """向 PTY master 写入全部数据，处理非阻塞 EAGAIN（缓冲区满时短暂重试）。

    PTY master 被包装进 asyncio 读管道后处于非阻塞模式；子进程不读取时
    写缓冲区可能短暂占满，os.write 抛 BlockingIOError，这里等待后重试
    直至写完。写入失败（fd 关闭 / EIO 等）抛 OSError 由调用方处理。
    """
    view = memoryview(data)
    total = 0
    while total < len(view):
        try:
            written = os.write(fd, view[total:])
        except BlockingIOError:
            await asyncio.sleep(0.01)
            continue
        total += written


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

    @classmethod
    def to_tool_schema(cls):
        return {
            "type": "function",
            "function": {
                "name": "bash_opt",
                "description": (
                    "按 task_id 操作后台 bash 任务（由 bash background=true 启动）。"
                    "op：read（读取当前已产生的全部输出并清空缓冲，立即返回不等待完成）、"
                    "wait（等待完成取结果 JSON：task_id/status/stdout/stderr/returncode，"
                    "timeout 秒，默认 300/0 无限）、"
                    "kill（杀进程树）、stdin（发文本到 stdin，需 text）、"
                    "keys（向终端发按键，需 key，跨平台 ANSI/VT100）、"
                    "screenshot（把该命令进程树的窗口截图存为 PNG，需 path，"
                    "可选 crop 指定只截取的像素区域，格式 'x,y,width,height'）、"
                    "move/click/drag/scroll/key/type（向该命令进程树的 GUI 窗口注入"
                    "鼠标移动/点击（左中右键、可双击）/拖动/滚轮/按键/文本，"
                    "坐标以窗口截图左上角为原点且可用 screenshot 对照）。"
                    "task_id 必须是当前对话 bash 后台返回的 bg-xxx。返回：操作结果 JSON 或输出；失败以 ( 开头。"
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "task_id": {
                            "type": "string",
                            "description": (
                                "后台 bash 任务的 task_id（bash background=True 返回的 "
                                "'bg-xxx' 格式 ID）。"
                            ),
                        },
                        "op": {
                            "type": "string",
                            "enum": ["read", "wait", "kill", "stdin", "keys",
                                     "screenshot", *INPUT_OPS],
                            "description": (
                                "要执行的操作："
                                "\n- read：读取后台命令当前已产生的全部输出并清空缓冲，"
                                "立即返回（不等待任务完成）；后续 read 只返回新产生的输出，"
                                "最终完整结果由 wait 获取"
                                "\n- wait：等待任务完成并获取命令输出"
                                "\n- kill：杀死任务所有进程树"
                                "\n- stdin：向任务 stdin 发送文本输入（需 text）"
                                "\n- keys：向任务（终端程序）发送光标/键盘消息（需 key）"
                                "\n- screenshot：把任务进程树（含其启动的 GUI 子进程）的窗口"
                                "截图保存为 PNG 文件（需 path；可选 crop 指定只截取的像素区域），"
                                "用于查看图形程序运行画面；"
                                "纯命令行进程没有窗口，会返回错误说明"
                                "\n- move/click/drag/scroll/key/type：向任务进程树的 GUI 窗口"
                                "注入输入（鼠标移动/点击（左中右键、双击即 count=2）/拖动/滚轮、"
                                "键盘按键、文本）；"
                                "坐标以窗口截图左上角为原点（与 screenshot 产物一致），"
                                "click/scroll 省略坐标时作用于窗口中心；"
                                "键输入需 key，文本输入需 text；纯命令行进程没有窗口，会报错"
                            ),
                        },
                        "timeout": {
                            "type": "number",
                            "description": (
                                "仅 wait 操作生效：等待完成的超时秒数（默认 300；"
                                "传 0 表示无限等待）。支持小数（如 0.5）。"
                                "超时后任务继续运行，可再次等待或 kill。"
                            ),
                        },
                        "text": {
                            "type": "string",
                            "description": (
                                "stdin / type 操作的文本内容："
                                "stdin 为发送到后台命令 stdin 的内容（按 UTF-8 编码，"
                                "不经过 shell 解释）；"
                                "type 为注入到 GUI 窗口的文本（逐字符输入，支持任意 "
                                "Unicode，'\\n' 与 '\\t' 转为回车/制表键）。"
                            ),
                        },
                        "newline": {
                            "type": "boolean",
                            "description": (
                                "是否在 text 末尾追加换行：stdin 默认 true"
                                "（按「输入一行」语义发送），type 默认 false"
                                "（原样输入，需要回车时传 true 或在 text 中写 '\\n'）。"
                            ),
                        },
                        "key": {
                            "type": "string",
                            "description": (
                                "keys / key 操作的按键名："
                                "keys（终端程序）用跨平台 ANSI/VT100 按键名，支持 "
                                "up/down/left/right、home/end/page_up/page_down/"
                                "insert/delete/backspace/tab/enter/escape/space、"
                                "f1-f12、ctrl_a-ctrl_z（含 ctrl_c/ctrl_d/ctrl_z/ctrl_l 等）；"
                                "key（GUI 窗口）用组合键文本，如 'ctrl+shift+s'、'alt+f4'、"
                                "'enter'、'a'（支持 ctrl/alt/shift/meta 修饰键、编辑与"
                                "导航键、f1-f24、单个字符）。"
                            ),
                        },
                        "path": {
                            "type": "string",
                            "description": (
                                "仅 screenshot 操作必填：截图保存的文件路径（PNG）。"
                                "无扩展名时自动补 .png；父目录不存在会自动创建。"
                                "截图后可用 read_image 读取该文件查看画面。"
                            ),
                        },
                        "crop": {
                            "type": "string",
                            "description": (
                                "仅 screenshot 操作可选：只截取窗口内的像素区域，"
                                "格式 'x,y,width,height'（如 '100,50,800,600'），"
                                "以整窗截图左上角为原点（(0,0) 即窗口左上角）。"
                                "省略时输出整窗原始像素（不做任何缩放）。"
                                "区域须完全落在窗口截图内，越界会报错并提示窗口实际尺寸。"
                            ),
                        },
                        "x": {
                            "type": "number",
                            "description": (
                                "窗口内坐标 X（像素，原点为窗口截图左上角，与 screenshot "
                                "产物一致）。move 必填；click / scroll 可选，省略则作用于"
                                "窗口中心；drag 用 from_x/from_y 指定起点。"
                            ),
                        },
                        "y": {
                            "type": "number",
                            "description": (
                                "窗口内坐标 Y（像素，原点为窗口截图左上角）。"
                                "与 x 同时提供或同时省略。"
                            ),
                        },
                        "to_x": {
                            "type": "number",
                            "description": "仅 drag：拖动终点的窗口内坐标 X（必填）。",
                        },
                        "to_y": {
                            "type": "number",
                            "description": "仅 drag：拖动终点的窗口内坐标 Y（必填）。",
                        },
                        "from_x": {
                            "type": "number",
                            "description": (
                                "仅 drag 可选：拖动起点的窗口内坐标 X；与 from_y 同时"
                                "省略时从窗口中心按下。"
                            ),
                        },
                        "from_y": {
                            "type": "number",
                            "description": "仅 drag 可选：拖动起点的窗口内坐标 Y。",
                        },
                        "button": {
                            "type": "string",
                            "enum": ["left", "right", "middle"],
                            "description": (
                                "仅 click / drag：鼠标按钮（默认 left）。"
                                "right 即右键（右击），middle 为中键。"
                            ),
                        },
                        "count": {
                            "type": "number",
                            "description": (
                                "仅 click：点击次数（默认 1；2 表示双击，最大 10）。"
                            ),
                        },
                        "modifiers": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "注入期间按住的修饰键，取值 ctrl / alt / shift / meta"
                                "（可多个）：move/click/drag/scroll 表示按住这些键执行动作"
                                "（如 ['ctrl'] 表示 Ctrl+点击）；key 动作会与 key 参数"
                                "里的组合键合并（两种写法等价）。"
                            ),
                        },
                        "direction": {
                            "type": "string",
                            "enum": ["up", "down", "left", "right"],
                            "description": (
                                "仅 scroll：滚动方向（默认 down，向下滚动查看后续内容）。"
                            ),
                        },
                        "amount": {
                            "type": "number",
                            "description": (
                                "仅 scroll：滚动量（默认 3，即约 3 行/格；最大 100）。"
                            ),
                        },
                        "duration": {
                            "type": "number",
                            "description": (
                                "仅 drag：拖动耗时秒数（默认 0.3，0 表示瞬时；"
                                "需要目标程序识别连续移动时调大，最大 10）。"
                            ),
                        },
                        "steps": {
                            "type": "number",
                            "description": (
                                "仅 drag：轨迹插值步数（默认 20，范围 2-200）。"
                            ),
                        },
                        "method": {
                            "type": "string",
                            "enum": ["auto", "sendinput", "message"],
                            "description": (
                                "仅 Windows 输入注入可选（其它平台忽略）："
                                "auto（默认，优先合成真实输入事件，无法取得前台时回退消息投递）、"
                                "sendinput（强制合成真实输入事件，需要目标窗口可被置前）、"
                                "message（直接投递 WM_* 窗口消息，不移动真实光标、不需要焦点，"
                                "但目标程序必须处理这些消息）。"
                            ),
                        },
                    },
                    "required": ["task_id", "op"],
                },
            },
        }

    @classmethod
    def display_params(cls, arguments: dict, max_len: int = 80) -> str:
        task_id = arguments.get("task_id") or ""  # 防御 None（模型传 null）
        op = arguments.get("op", "")
        extra = ""
        if op == "stdin":
            extra = str(arguments.get("text", ""))
        elif op == "keys":
            extra = str(arguments.get("key", ""))
        elif op == "screenshot":
            extra = str(arguments.get("path", ""))
            crop = arguments.get("crop")
            if crop:
                extra = f"{extra} crop={crop}" if extra else f"crop={crop}"
        elif op in INPUT_OPS:
            extra = cls._input_display(op, arguments)
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
        if op == "click":
            button = str(arguments.get("button") or "left")
            count = arguments.get("count")
            label = f"{button}"
            if count not in (None, 1, "1"):
                label += f"x{count}"
            return f"{label} {_format_position(arguments)}"
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

    def __init__(self, task_id: str, op: str, timeout=None,
                 text: str | None = None, newline: bool | None = None,
                 key: str | None = None, path: str | None = None,
                 crop: str | None = None,
                 x=None, y=None, to_x=None, to_y=None,
                 from_x=None, from_y=None,
                 button: str | None = None, count=None, modifiers=None,
                 direction: str | None = None, amount=None,
                 duration=None, steps=None, method: str | None = None):
        super().__init__()
        # task_id 归一化（防御 None/缺失）：模型传 {"task_id": null} 时
        # from_args 把 None 传入（默认值不生效），后续 startswith 崩溃。
        self.task_id = task_id or ""
        self.op = op
        # timeout 仅对 wait 生效：省略/None → 300s；<=0 → 无限等待
        # 使用 float 保留小数（如 0.5 秒短超时），避免 int() 截断
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
        self.modifiers = modifiers
        self.direction = direction
        self.amount = amount
        self.duration = duration
        self.steps = steps
        self.method = method

    # ── execute ──────────────────────────────────────────

    async def execute(self) -> str:
        """按 task_id 和 op 操作后台 bash 任务，返回结果字符串。"""
        agent = getattr(self, 'agent', None)
        if agent is None or not hasattr(agent, '_background_tasks'):
            return "(后台任务操作需要关联 Agent 上下文，当前未关联)"

        # ★ task_id 前缀校验（与 subagent_opt 对称，双保险）：bash 后台任务
        #   id 恒为 "bg-xxx"，且注册在 bash 专用表 _background_tasks——subagent
        #   后台任务（sa-xxx）注册在独立的 _subagent_tasks 表，本工具查不到也
        #   不该操作；误传时直接提示走 subagent_opt。
        if not self.task_id.startswith("bg-"):
            return (f"(错误：task_id 必须是 bash 后台启动（background=True）返回的 "
                    f"'bg-xxx' 格式 ID，当前: {self.task_id}。"
                    f"subagent 后台任务请用 subagent_opt 操作)")

        rec = agent._background_tasks.get(self.task_id)
        if rec is None:
            return (f"(后台任务不存在: {self.task_id}。"
                    f"请先用 bash background=True 启动后台任务获取 task_id)")

        # ★ 标记为 bash_opt 管理：该任务的结果由大模型通过本工具主动获取
        #   （wait 拿到输出 / kill 终止 / stdin / keys 交互），后续
        #   _process_background_tasks 不再把结果作为用户消息自动插入，
        #   也不自动等待其完成（避免交互任务阻塞对话轮次）。
        rec["managed_by_tool"] = True

        if self.op == "read":
            return await self._op_read(rec)
        if self.op == "wait":
            return await self._op_wait(agent, rec)
        if self.op == "kill":
            return await self._op_kill(agent, rec)
        if self.op == "stdin":
            return await self._op_stdin(rec)
        if self.op == "keys":
            return await self._op_keys(rec)
        if self.op == "screenshot":
            return await self._op_screenshot(rec)
        if self.op in INPUT_OPS:
            return await self._op_input(rec)
        supported = "/".join(("read", "wait", "kill", "stdin", "keys",
                              "screenshot", *INPUT_OPS))
        return f"(未知操作: {self.op}。支持: {supported})"

    # ── op=read ──────────────────────────────────────────

    async def _op_read(self, rec: dict) -> str:
        """读取后台任务当前已产生的全部输出并清空缓冲，立即返回。

        read 为增量读取：任务运行期间的每一行输出累积到 read_buffer，
        本次读走全部内容并清空；任务继续运行，后续 read 只返回新输出，
        最终完整结果由 op=wait 获取。

        返回 JSON（task_id/status/output）：
          - status: 任务当前状态（running / completed）
          - output: 本次读取到的累积输出（读取后已清空缓冲）
        """
        lock = rec.get("io_lock")
        if lock is not None:
            async with lock:
                output = rec.get("read_buffer", "")
                rec["read_buffer"] = ""
        else:
            output = rec.get("read_buffer", "")
            rec["read_buffer"] = ""
        done = bool(rec.get("done"))
        status = rec.get("status") or ("completed" if done else "running")
        payload = {
            "task_id": self.task_id,
            "status": status,
            "output": output,
        }
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
        本身（任务被误杀），wait 只观察不干预，超时后任务继续运行。
        """
        task = rec.get("task")
        if not rec.get("done") and task is not None:
            try:
                if self.timeout:
                    done, _pending = await asyncio.wait({task}, timeout=self.timeout)
                    if not done:
                        return (f"(等待后台任务 {self.task_id} 超时（{self.timeout} 秒），"
                                f"任务仍在运行。可再次 wait 或 op=kill 终止)")
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
        return json.dumps(payload, ensure_ascii=False)

    # ── op=kill ──────────────────────────────────────────

    async def _op_kill(self, agent, rec: dict) -> str:
        """杀死后台任务的所有进程树并取消后台任务，从 tasklist 移除。"""
        pid = rec.get("pid")
        process = rec.get("process")
        task = rec.get("task")

        # 1. 杀死进程树（killpg 进程组 + /proc 递归补杀后代）
        if pid is not None:
            try:
                kill_process_tree(pid)
            except Exception as e:
                logger.debug("kill 进程树异常: %s", e)
        elif process is not None:
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
                pass  # 任务取消过程异常忽略

        # 3. 移除任务记录并更新 TUI 计数
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
        return f"(已杀死后台任务 {self.task_id} 及其所有进程树)"

    # ── op=stdin ─────────────────────────────────────────

    async def _op_stdin(self, rec: dict) -> str:
        """向后台任务 stdin 发送文本输入。"""
        if self.text is None:
            return "(stdin 操作需要 text 参数指定要发送的文本)"
        data = self.text
        # newline 未指定（None）或 true → 追加换行（保持「输入一行」语义）；
        # 显式 false 时原样发送
        if self.newline is not False:
            data += "\n"
        ok, err = await self._write_to_task(rec, data.encode("utf-8"))
        if not ok:
            return err
        return f"(已向后台任务 {self.task_id} 发送 stdin 输入: {self._sanitize_display(self.text)})"

    # ── op=keys ──────────────────────────────────────────

    async def _op_keys(self, rec: dict) -> str:
        """向后台任务发送光标/键盘消息（跨平台 ANSI/VT100 转义序列）。"""
        if self.key is None:
            return "(keys 操作需要 key 参数指定按键，如 key='up' / key='ctrl_c')"
        seq = _resolve_key(self.key)
        if seq is None:
            supported = sorted(_KEY_SEQUENCES.keys()) + ["ctrl_a..ctrl_z"]
            return (f"(未知按键: {self.key}。支持: {', '.join(supported)})")
        ok, err = await self._write_to_task(rec, seq.encode("utf-8"))
        if not ok:
            return err
        return f"(已向后台任务 {self.task_id} 发送按键: {self.key})"

    # ── op=screenshot ────────────────────────────────────

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
        try:
            crop = self._resolve_crop()
        except CropError as exc:
            return f"(截图裁剪参数非法: {exc})"
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法截图。可用 op=wait 查看任务状态)")
        try:
            target_path = self._prepare_screenshot_path(str(self.path))
        except ValueError as exc:
            return f"(截图路径非法: {exc})"
        try:
            result = await self._capture_with_retry(pid, target_path, crop)
        except ScreenshotError as exc:
            return f"(截图失败: {exc})"
        payload: dict = {"task_id": self.task_id, "op": "screenshot"}
        payload.update(result.to_dict())
        if crop is not None:
            payload["crop"] = crop.to_dict()
            payload["hint"] = ("截图已按 crop 裁剪并保存，"
                               "可用 read_image 工具读取该文件查看画面")
        else:
            payload["hint"] = "截图已保存，可用 read_image 工具读取该文件查看画面"
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
                                  crop: CropRegion | None = None):
        """截图（GUI 程序窗口创建有延迟时轮询重试）。

        仅在「目标暂无可见窗口」时重试（NoWindowError）；其它错误（含
        裁剪参数越界的 CropError）立即返回。每轮截图在线程中执行
        （GDI 调用阻塞），并受 _SCREENSHOT_TIMEOUT 保护。

        Returns:
            CaptureResult（成功后）。

        Raises:
            ScreenshotError: 所有重试均失败，截图命令超时/异常，或裁剪越界。
        """
        deadline = time.monotonic() + self._SCREENSHOT_WAIT_SECONDS
        while True:
            try:
                return await asyncio.wait_for(
                    asyncio.to_thread(capture_process_window, pid, path, crop),
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
    def _prepare_screenshot_path(path: str) -> str:
        """规范化截图输出路径：展开 ~、补 .png、建父目录、安全校验。

        Raises:
            ValueError: 路径为空、指向目录、或未通过安全校验。
        """
        expanded = os.path.expanduser(path.strip())
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
        """向后台任务的 GUI 窗口注入鼠标 / 键盘 / 文本输入。

        坐标以窗口截图左上角为原点（与 op=screenshot 产物一致），便于
        「先截图看清界面，再按像素点操作」。结果返回 JSON（task_id/op 与
        动作细节如按钮、坐标、屏幕坐标、按键序列、投递方式），可用
        op=screenshot + read_image 核对界面变化。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法注入输入。可用 op=wait 查看任务状态)")
        try:
            action = self._build_input_action()
        except ActionError as exc:
            return f"(输入参数非法: {exc})"
        try:
            result = await self._send_input_with_retry(pid, action)
        except (InputNoWindowError, InputError) as exc:
            return f"(输入失败: {exc})"
        payload = {
            "task_id": self.task_id,
            "op": self.op,
            "hint": ("输入已注入；可用 op=screenshot 截图后用 read_image 核对界面变化"
                     "（坐标原点为窗口截图左上角）"),
        }
        payload.update(result.to_dict())
        return json.dumps(payload, ensure_ascii=False)

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
            "modifiers": self.modifiers, "key": self.key, "text": text,
            "direction": self.direction, "amount": self.amount,
            "duration": self.duration, "steps": self.steps,
            "method": self.method,
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
                await _write_pty_all(master_fd, data)
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
