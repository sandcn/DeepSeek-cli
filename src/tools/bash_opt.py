"""
bash_opt — 按 task_id 操作后台 bash 任务

配合 bash 工具 background=True 模式使用。bash 后台启动后返回
{"task_id": "bg-xxx", "status": "running"}，
大模型可据此用 bash_opt 工具按 task_id 操作：

- op=read   读取后台命令**当前已产生**的全部输出并清空缓冲，立即返回（不等待完成）
- op=wait   等待任务执行完成并获取结果（JSON：task_id/status/stdout/stderr/returncode）
- op=kill   杀死后台命令的所有进程树（killpg + /proc 递归补杀后代）
- op=stdin  向后台命令的 stdin 发送文本输入（text 参数，newline 可选是否追加换行）
- op=keys   向后台命令发送光标/键盘消息（自动路由：目标进程有 GUI 窗口时
            作为窗口级键盘消息注入该窗口，否则回退写入终端——跨平台
            ANSI/VT100 转义序列；支持 ctrl+c 等修饰键组合、esc/pageup 等
            别名、单个字符与 f1-f24，repeat 可一次连按多次）
- op=screenshot  把后台命令（及其子进程）的窗口截图保存为 PNG
                 （path 参数指定文件路径；可选 crop 指定只截取的像素区域，
                 window 选择目标窗口，grid 叠加等距坐标参考线）
- op=windows     列出该进程树的全部窗口（句柄 / 标题 / 类名 / 位置尺寸 /
                 Z 序 / 是否前台 / 是否主窗口），用于挑选目标窗口
- op=window      控制被选窗口的状态与几何（window_action=activate / maximize /
                 minimize / restore / close / move / resize / fit），
                 配合 window 选择器与 x/y/width/height
- op=move / click / drag / scroll / key / type
                 向后台命令的 **GUI 窗口**注入鼠标 / 键盘 / 文本输入
                 （window 选择目标窗口、settle 注入后等待、shot 注入后自动截图；
                  鼠标按钮、双击、拖动、滚轮、组合键、任意 Unicode 文本；
                  按键可分「按下 / 弹起 / 完整」阶段，见 phase 参数，
                  并用 repeat 一次连按多次）

read 为**增量读取**：后台任务运行期间的每一行输出都会累积到内部缓冲，
每次 read 取走当前全部累积内容并清空，适合实时观察长时任务（编译/下载/
日志流）的进度；任务最终完整结果仍由 op=wait 获取。

截图（screenshot）适用于后台任务运行的是**图形界面程序**（游戏、GUI 应用、
渲染预览等）的场景：按 task_id 定位该命令产生的进程树，取其可见窗口像素
写盘（Windows 用 PrintWindow/BitBlt；Linux 用 ImageMagick import/xwd；
macOS 用 screencapture）。默认输出整窗原始像素；需要「指定大小」时可传
crop='x,y,width,height' 只截取窗口内的像素区域（以整窗截图左上角为原点，
区域越界报错并提示窗口实际尺寸）。产物为 PNG，可用 read_image 查看画面。
结果同时给出 ``window_summary``（命中的窗口一行摘要，``#N`` 编号与选择器
一致）、``window_x`` / ``window_y``（产物左上角对应的屏幕坐标）与
``window_rect``（窗口外框屏幕矩形），便于把截图像素换算成屏幕坐标 / 输入
坐标，或直接喂给 ``op=window`` 的 move / fit。
纯命令行进程没有窗口，此时返回可读的错误说明。

多窗口选择（window 参数，截图 / 输入 op / 窗口控制通用）：一个 GUI 程序往往
同时存在多个顶层窗口（主窗口、弹出菜单、下拉浮层、文件对话框），因此
screenshot / 输入 op 都接受 window 选择器——'main'（缺省主窗口，可见性优先）、
'active'（前台窗口）、'#N'（**可操作窗口**按 Z 序第 N 个，1 = 最靠前）、
'handle:0x…'、'title:子串'、'class:子串'、'pid:1234'、'popup'（无标题弹层）、
'dialog'（对话框）。op=windows 先给出窗口清单（含 selectable / z_index），再把
同一个选择器用于截图或输入，即可精确操作这些独立顶层窗口（旧版只能操作面积
最大的主窗口）。不可见（visible=false）或已最小化的窗口不会被 'main' / '#N' /
'popup' / 'dialog' 选中：它们既截不到有效像素（产物全黑），也收不到鼠标键盘
输入（Chrome 的 Chrome_WidgetWin_0 这类隐藏辅助窗口尤其容易与真实弹层混淆）；
确实需要操作这类窗口时用 'handle:0x…' 显式指定。

弹层（右键菜单 / 下拉浮层 / ``WS_EX_TOOLWINDOW`` 弹出窗口）**不参与前台
切换**，``SetForegroundWindow`` 对它们无效；输入 op 因此按「该窗口**所属
应用**是否在前台」判定可用通道——目标窗口自身、其属主窗口、或同进程树内的
任一窗口是前台，就用合成输入投递：鼠标事件按屏幕坐标命中光标下的真实窗口
（弹层浮在最上层，这正是它被操作的方式），键盘事件交给同一应用的前台窗口
（浏览器的渲染进程会正常处理，如关闭下拉浮层），结果里的 ``foreground_window``
标注实际接收窗口。``op=windows`` 清单里的 ``tool_window`` / ``client_area``
两个字段可直接预判：``client_area=false`` 的窗口没有可换算的客户区，不能用
``method='message'`` 投递鼠标坐标，去掉 method（默认 auto）走合成输入即可。

截图增强：grid 参数（如 grid=50）在产物上叠加等距参考线，读图后可精确换算
像素坐标；结果 JSON 附带被截窗口的句柄 / 标题 / 候选窗口总数，便于确认选对
了窗口。

窗口输入（move/click/drag/scroll/key/type）同样按 task_id 定位该命令进程树
的可见窗口，坐标以**窗口截图左上角**为原点（与 op=screenshot 产物一致，
便于「先截图看清界面，再按像素点操作」）：click 支持左/右/中键与双击，
drag 支持按住左/右/中键拖拽（带轨迹插值），scroll 支持上下左右滚动，
key 支持 ctrl+shift+s 之类的组合键与「按下 / 弹起 / 完整」阶段（phase：
press / down / up，各平台分别独立发送 down 与 up 消息），repeat 参数可
一次连按 N 次（如 repeat=8 连按 F12），type 逐字符输入任意 Unicode 文本
（每个字符发送配对的按下与弹起）。
Windows 用 SendInput（必要时回退 PostMessage 投递）、Linux 用 xdotool、
macOS 用 Quartz/cliclick + osascript；平台工具缺失时返回带安装提示的错误。
Windows 的键盘 / 文本注入会**多次重试取得前台**、注入后复核焦点并在被
抢走时重新激活重发，SendInput 完全未投递时也会短期重试；若最终仍走
PostMessage 回退通道（对 Chrome / Electron / 游戏等自绘界面常无效），
结果里会附带 warning 说明。

键盘消息跨平台说明：``op=keys`` 自动按被操作程序的形态选通道——目标进程
（含其子进程）**有 GUI 窗口**时，按键作为窗口级键盘消息注入该窗口（与
``op=key`` 同一套合成事件）；**没有 GUI 窗口**时回退写入终端（ANSI/VT100
序列）。VT100/ANSI 转义序列是终端输入的标准语义，被 Linux/macOS/
Android(Termux) 的 PTY 与 Windows 的 ConPTY/Windows Terminal 统一接受；
按键名（如 up/down/ctrl_c/ctrl+c/esc/pageup）映射为对应字节序列，经 PTY
master 或 stdin 管道写入后台进程，不依赖平台特定 API。键名规则在两种通道
下共用同一套（别名与组合键语法一致）；两者都不可用（无 GUI 窗口且无终端
写入句柄）时返回错误说明。需要**精确控制**通道时：``op=key`` 面向
**GUI 窗口**（强制要求窗口存在，无窗口报错），终端序列亦可直接用
``op=stdin`` 写入。
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
    SelectorError,
    capture_process_window,
    control_process_window,
    describe_windows,
    list_process_windows,
    parse_control_request,
    window_hint,
)
from ._window_input import (
    DEFAULT_KEY_REPEAT,
    INPUT_OPS,
    MAX_KEY_REPEAT,
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

# 终端按键名 → ANSI/VT100 序列的解析见 ``_terminal_keys`` 模块：它复用
# ``_window_input.keys`` 的键名别名与组合键语法（与 op=key 完全一致），
# 支持 esc/del/pageup/ctrl+c 等别名、修饰键组合、单个字符与 f1-f20。


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
    #: 窗口目录 / 窗口控制 op（非输入注入，单独分派）
    _WINDOW_OPS: tuple[str, ...] = ("windows", "window")
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
    #: 注入后自动截图（shot）时的默认等待（秒）——给界面留出刷新时间
    _DEFAULT_SHOT_SETTLE: float = 0.2
    #: 注入后等待（settle）的上限（秒）——避免误传超大值长期挂住
    _MAX_SETTLE_SECONDS: float = 30.0
    #: shot=true 时自动截图的输出目录（相对当前工作目录）
    _SHOT_AUTO_DIR: str = "bash_opt_shots"
    #: 连按（repeat）时终端序列的写入间隔（秒）——避免被程序合并成一次
    _TERMINAL_KEY_REPEAT_INTERVAL: float = 0.05

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
                    "keys（发送按键，需 key：目标进程有 GUI 窗口时自动作为窗口级"
                    "键盘消息注入该窗口，否则写入终端；支持 ctrl+c 等组合键、"
                    "esc/pageup 等别名与单个字符，repeat 可一次连按多次）、"
                    "screenshot（把该命令进程树的窗口截图存为 PNG，需 path，"
                    "可选 crop 指定只截取的像素区域，格式 'x,y,width,height'，"
                    "可选 window 选择目标窗口（'#1'/'title:子串'/'popup' 等）、"
                    "grid 叠加坐标参考线）、"
                    "windows（列出该命令进程树的全部窗口及其句柄/标题/几何/Z 序）、"
                    "window（控制窗口，window_action=activate/maximize/minimize/"
                    "restore/close/move/resize/fit，用 window 选择目标窗口）、"
                    "move/click/drag/scroll/key/type（向该命令进程树的 GUI 窗口注入"
                    "鼠标移动/点击（左中右键、可双击）/拖动/滚轮/按键/文本，"
                    "key 支持 phase=press/down/up 的按下与弹起分离发送，"
                    "window 可选目标窗口，shot 可注入后自动截图，"
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
                                     "screenshot", "windows", "window",
                                     *INPUT_OPS],
                            "description": (
                                "要执行的操作："
                                "\n- read：读取后台命令当前已产生的全部输出并清空缓冲，"
                                "立即返回（不等待任务完成）；后续 read 只返回新产生的输出，"
                                "最终完整结果由 wait 获取"
                                "\n- wait：等待任务完成并获取命令输出"
                                "\n- kill：杀死任务所有进程树"
                                "\n- stdin：向任务 stdin 发送文本输入（需 text）"
                                "\n- keys：向任务发送光标/键盘消息（需 key；"
                                "目标进程有 GUI 窗口时自动作为窗口级键盘消息注入该窗口，"
                                "没有 GUI 窗口则写入终端 PTY/stdin；"
                                "支持 ctrl+c 等组合键、esc/pageup 等别名与单个字符）"
                                "\n- screenshot：把任务进程树（含其启动的 GUI 子进程）的窗口"
                                "截图保存为 PNG 文件（需 path；可选 crop 指定只截取的像素区域），"
                                "用于查看图形程序运行画面；"
                                "结果附带 window_x/window_y（截图像素左上角对应的屏幕坐标）"
                                "与 window_rect（窗口外框），便于截图像素与屏幕坐标换算；"
                                "纯命令行进程没有窗口，会返回错误说明"
                                "\n- move/click/drag/scroll/key/type：向任务进程树的 GUI 窗口"
                                "注入输入（鼠标移动/点击（左中右键、双击即 count=2）/拖动/滚轮、"
                                "键盘按键（phase=press/down/up 分按下与弹起）、文本）；"
                                "坐标以窗口截图左上角为原点（与 screenshot 产物一致），"
                                "click/scroll 省略坐标时作用于窗口中心；"
                                "键输入需 key，文本输入需 text；纯命令行进程没有窗口，会报错"
                                "\n- windows：列出该后台任务进程树的全部窗口"
                                "（句柄/标题/类名/位置尺寸/Z 序/是否前台/是否主窗口/"
                                "tool_window/client_area），"
                                "z_index 与 '#N' 选择器编号一致、selectable_total 为可操作"
                                "窗口数，"
                                "用于挑选目标窗口（右键菜单、下拉浮层、对话框等独立顶层"
                                "窗口都在其中）；"
                                "tool_window=true 的弹层不参与前台切换，输入 op 会按"
                                "「同应用是否在前台」选通道，鼠标按屏幕坐标命中光标下的"
                                "窗口、键盘交给同一应用的前台窗口；"
                                "client_area=false 表示该窗口没有可换算的客户区"
                                "（不能用 method='message' 投递鼠标坐标）"
                                "\n- window：控制窗口状态与几何（需 window_action="
                                "activate/maximize/minimize/restore/close/move/resize/fit，"
                                "配合 window 选择器指定目标窗口）"
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
                                "keys / key 操作的按键名（二者共用同一套键名规则）："
                                "keys（自动路由：有 GUI 窗口注入窗口、否则发终端）"
                                "支持修饰键组合（ctrl+c / alt+f4 / "
                                "shift+tab）、紧凑写法（ctrl_c / ctrl-c）、常用别名"
                                "（esc / return / del / ins / pageup / pgup / pgdn / "
                                "next / prior）、光标与编辑键（up/down/left/right/"
                                "home/end/page_up/page_down/insert/delete/backspace/"
                                "tab/enter/escape/space）、f1-f20 与单个字符；"
                                "key（GUI 窗口）用同样的组合键文本，如 'ctrl+shift+s'、"
                                "'alt+f4'、'enter'、'a'（支持 ctrl/alt/shift/meta "
                                "修饰键、编辑与导航键、f1-f24、单个字符）。"
                            ),
                        },
                        "window": {
                            "type": "string",
                            "description": (
                                "目标窗口选择器（screenshot / 输入 op / window 通用；"
                                "省略即主窗口）："
                                "'main' 主窗口（可见性优先）、'active' 当前前台窗口、"
                                "'#N' 可操作窗口（可见且未最小化）按 Z 序第 N 个"
                                "（1 = 最靠前，如弹出的右键菜单、下拉浮层、对话框；"
                                "N 与 op=windows 清单里的 z_index 一致）、"
                                "'handle:0x1a2b' 按平台窗口句柄、"
                                "'title:子串' / 'class:子串' / 'pid:1234' 按属性匹配、"
                                "'popup' 无标题弹层、'dialog' 对话框。"
                                "不可见（visible=false）或已最小化的窗口不会被 "
                                "'main' / '#N' / 'popup' / 'dialog' 选中——它们截出来"
                                "是全黑图、输入也打不进去；需要这类窗口时用 "
                                "'handle:0x…' 显式指定。"
                                "先用 op=windows 查看窗口清单（selectable / z_index "
                                "字段），再用同一选择器把截图 / 输入投向任意窗口。"
                                "弹层（清单里 tool_window=true 的右键菜单 / 下拉"
                                "浮层）不参与前台切换，直接把 window 指向它即可用"
                                "合成输入操作（鼠标按屏幕坐标命中光标下的窗口、"
                                "键盘交给同一应用的前台窗口）；"
                                "client_area=false 的窗口不能配合 "
                                "method='message' 使用。"
                            ),
                        },
                        "grid": {
                            "type": "number",
                            "description": (
                                "仅 screenshot 可选：在截图上叠加等距坐标参考线，值为"
                                "线间距像素（0 或缺省 = 按画面尺寸自动选约 10 格，"
                                "如 grid=50 每 50 像素一条主线、每 25 像素一条次线），"
                                "便于读图后精确给出 x/y 坐标。"
                            ),
                        },
                        "shot": {
                            "type": "string",
                            "description": (
                                "仅输入 op（click/move/drag/scroll/key/type）可选："
                                "注入完成后自动截图，省去额外一次 screenshot 调用。"
                                "取值为截图路径（无扩展名自动补 .png），或 true 表示"
                                "自动命名到 'bash_opt_shots/' 目录。结果 JSON 的 "
                                "screenshot 字段给出 path/width/height，以及 "
                                "window_x/window_y（截图像素左上角的屏幕坐标）与 "
                                "window_rect（窗口外框）。"
                            ),
                        },
                        "settle": {
                            "type": "number",
                            "description": (
                                "仅输入 op 可选：注入后等待的秒数再返回（默认 0；"
                                "指定 shot 时默认 0.2 秒），用于等界面完成刷新，"
                                "避免截图拍到旧画面。"
                            ),
                        },
                        "window_action": {
                            "type": "string",
                            "enum": ["activate", "maximize", "minimize", "restore",
                                     "close", "move", "resize", "fit"],
                            "description": (
                                "仅 window 操作必填：窗口控制动作。activate 置前激活、"
                                "maximize/minimize/restore 最大化/最小化/还原、"
                                "close 请求关闭（等价点关闭按钮）、move 按屏幕坐标移动"
                                "（需 x/y）、resize 调整尺寸（需 width/height）、"
                                "fit 同时移动并调整尺寸（x/y/width/height 都要）。"
                                "配合 window 选择器指定目标窗口。"
                            ),
                        },
                        "width": {
                            "type": "number",
                            "description": (
                                "仅 window 操作的 resize / fit：目标窗口宽度（像素）。"
                            ),
                        },
                        "height": {
                            "type": "number",
                            "description": (
                                "仅 window 操作的 resize / fit：目标窗口高度（像素）。"
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
                        "phase": {
                            "type": "string",
                            "enum": ["press", "down", "up"],
                            "description": (
                                "仅 key 操作可选：按键阶段。"
                                "press（默认，按下并弹起，一次完整按键）、"
                                "down（只发送「按下」消息，可用于长按）、"
                                "up（只发送「弹起」消息）。"
                                "按下与弹起在各平台分别独立发送（Linux 用 xdotool "
                                "keydown/keyup、Windows 用 WM_KEYDOWN/WM_KEYUP 或 "
                                "WM_SYSKEY*/SendInput、macOS 用 Quartz 按键事件）；"
                                "key='ctrl' 可单独按下/弹起修饰键本身。"
                            ),
                        },
                        "repeat": {
                            "type": "number",
                            "description": (
                                "key / keys 操作可选：连按次数（默认 1，最大 100）。"
                                "一次调用连续发送 N 次完整按键，每次之间留固定间隔；"
                                "仅在 phase='press'（默认）时生效，down/up 的长按"
                                "阶段忽略该参数。用于「连按多次」场景（如连按 F12 "
                                "开关调试工具），减少多次调用与中途失焦导致的漏按。"
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
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}" if extra else f"window={window}"
            grid = arguments.get("grid")
            if grid:
                extra = f"{extra} grid={grid}" if extra else f"grid={grid}"
        elif op in INPUT_OPS:
            extra = cls._input_display(op, arguments)
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}" if extra else f"window={window}"
            if arguments.get("shot"):
                extra = f"{extra} shot" if extra else "shot"
        elif op == "window":
            action = str(arguments.get("window_action") or "")
            window = arguments.get("window")
            extra = f"{action} window={window}" if window else action
        elif op == "windows":
            extra = "窗口清单"
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
            label = str(arguments.get("key", ""))
            phase = str(arguments.get("phase") or "press").strip().lower()
            if phase in ("down", "up"):
                label = f"{label} {phase}"
            return label
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
                 duration=None, steps=None, method: str | None = None,
                 phase: str | None = None, repeat=None,
                 window: str | None = None, grid=None,
                 shot=None, settle=None,
                 window_action: str | None = None,
                 width=None, height=None):
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
        # 按键阶段（仅 key 生效）：press=按下并弹起（默认）/ down=只按下 / up=只弹起
        self.phase = phase
        # 按键连按次数（仅 key / keys 生效，phase=press 时）：一次调用连按 N 次
        self.repeat = repeat
        # ── 目标窗口选择（screenshot / 输入 op / window 通用）──
        self.window = window
        # ── 截图增强 ──
        self.grid = grid          # 坐标网格步长（0 = 自动；None/0 = 不画）
        self.shot = shot          # 输入 op 注入后自动截图（路径或 True）
        self.settle = settle      # 注入后等待秒数（None = 按 op 取默认）
        # ── 窗口控制（op=window）──
        self.window_action = window_action
        self.width = width
        self.height = height

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
        if self.op == "windows":
            return await self._op_windows(rec)
        if self.op == "window":
            return await self._op_window(rec)
        if self.op in INPUT_OPS:
            return await self._op_input(rec)
        supported = "/".join(("read", "wait", "kill", "stdin", "keys",
                              "screenshot", "windows", "window", *INPUT_OPS))
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
        """向后台任务发送光标/键盘消息（按被操作程序形态自动路由）。

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
        try:
            repeat = self._resolve_key_repeat()
        except ActionError as exc:
            return f"(按键参数非法: {exc})"
        pid = rec.get("pid")
        window_pid = (pid if isinstance(pid, int) and not isinstance(pid, bool)
                      and pid > 0 else None)
        window_failure: str | None = None
        if window_pid is not None and probe_window(window_pid) is not None:
            try:
                return await self._send_keys_to_window(window_pid, key_text, repeat)
            except (InputNoWindowError, InputError) as exc:
                # 探测到窗口但注入失败（窗口已关闭 / 无法置前等）：尝试终端回退
                window_failure = str(exc)
        ok, message = await self._send_keys_to_terminal(rec, key_text, repeat)
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

    def _resolve_key_repeat(self) -> int:
        """解析 key / keys 的连按次数（缺省 1）。

        Raises:
            ActionError: 不是 1..MAX_KEY_REPEAT 之间的整数。
        """
        raw = self.repeat
        if raw is None:
            return DEFAULT_KEY_REPEAT
        if isinstance(raw, bool):
            raise ActionError(f"repeat 必须是整数，当前: {raw!r}")
        if isinstance(raw, int):
            value = raw
        else:
            text = str(raw).strip()
            if not text:
                return DEFAULT_KEY_REPEAT
            try:
                value = int(text)
            except ValueError:
                raise ActionError(f"repeat 必须是整数，当前: {raw!r}") from None
        if not 1 <= value <= MAX_KEY_REPEAT:
            raise ActionError(f"repeat 需在 1..{MAX_KEY_REPEAT} 之间，当前: {value}")
        return value

    async def _send_keys_to_window(self, pid: int, key_text: str,
                                   repeat: int = DEFAULT_KEY_REPEAT) -> str:
        """把按键作为窗口级键盘消息注入目标 GUI 窗口。

        与 ``op=key`` 走同一条输入注入通道（Windows SendInput/PostMessage、
        X11 xdotool、macOS Quartz），键名规则亦共用；窗口创建有延迟时按
        :meth:`_send_input_with_retry` 的节奏重试。

        Args:
            pid: 目标进程 PID。
            key_text: 按键文本（与 ``op=key`` 同一套键名规则）。
            repeat: 连按次数（仅 ``phase=press`` 生效）。

        Raises:
            ActionError: 键名非法。
            InputNoWindowError: 注入时窗口已消失。
            InputError: 平台不支持或注入失败。
        """
        action = build_action("key", {"key": key_text, "repeat": repeat})
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

    async def _send_keys_to_terminal(self, rec: dict, key_text: str,
                                     repeat: int = DEFAULT_KEY_REPEAT
                                     ) -> tuple[bool, str]:
        """回退通道：按键转 ANSI/VT100 序列写入终端（PTY master / stdin）。

        Args:
            rec: 后台任务记录。
            key_text: 按键文本（与 ``op=key`` 同一套键名规则）。
            repeat: 连按次数（序列重复写入，每次之间留固定间隔）。

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
        for index in range(max(int(repeat), 1)):
            if index:
                await asyncio.sleep(self._TERMINAL_KEY_REPEAT_INTERVAL)
            ok, err = await self._write_to_task(rec, payload)
            if not ok:
                return False, err
        suffix = "" if repeat == 1 else f" x{repeat}"
        return True, (f"(已向后台任务 {self.task_id} 发送终端按键: "
                      f"{key_text}{suffix})")

    @staticmethod
    def _plain(message: str) -> str:
        """去掉错误文本最外层括号（嵌入更长的说明时避免括号嵌套）。"""
        text = str(message).strip()
        if text.startswith("(") and text.endswith(")"):
            return text[1:-1]
        return text

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
        try:
            grid = self._resolve_grid()
        except CropError as exc:
            return f"(截图网格参数非法: {exc})"
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法截图。可用 op=wait 查看任务状态)")
        try:
            target_path = self._prepare_screenshot_path(str(self.path))
        except ValueError as exc:
            return f"(截图路径非法: {exc})"
        try:
            result = await self._capture_with_retry(
                pid, target_path, crop, window=self.window, grid=grid)
        except (SelectorError, ScreenshotError) as exc:
            scope = f"（window 选择器 {self.window!r}）" if self.window else ""
            return f"(截图失败{scope}: {exc})"
        payload: dict = {"task_id": self.task_id, "op": "screenshot"}
        payload.update(result.to_dict())
        notes = ["截图已保存"]
        if crop is not None:
            payload["crop"] = crop.to_dict()
            notes.append("已按 crop 裁剪")
        if grid is not None:
            payload["grid"] = grid
            notes.append("已叠加坐标参考线")
        if self.window:
            payload["window"] = str(self.window)
        notes.append(
            f"截图像素左上角对应屏幕坐标 ({result.window_x},{result.window_y})"
            f"（screen = 原点 + 截图像素；输入 op 的坐标即以本产物左上角为原点）"
        )
        if result.window_rect:
            rect = result.window_rect
            notes.append(
                f"窗口外框 {rect['width']}x{rect['height']}"
                f"@({rect['x']},{rect['y']})（产物 {result.width}x{result.height} "
                f"与它的差值来自窗口装饰 / DWM 黑边 / crop，二者不必相等）"
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

    def _resolve_grid(self) -> int | None:
        """解析 grid 参数为网格步长（省略 → None 不画；``0`` / true → 自动）。

        Raises:
            CropError: 取值不是数值。
        """
        raw = self.grid
        if raw is None:
            return None
        if isinstance(raw, bool):
            return 0 if raw else None
        text = str(raw).strip()
        if not text:
            return None
        try:
            value = int(float(text))
        except (TypeError, ValueError):
            raise CropError(f"grid 需要数值（0 = 自动选择步长），当前: {raw!r}") from None
        return max(value, 0)

    async def _capture_with_retry(self, pid: int, path: str,
                                  crop: CropRegion | None = None,
                                  *, window: str | None = None,
                                  grid: int | None = None):
        """截图（GUI 程序窗口创建有延迟时轮询重试）。

        仅在「目标暂无可见窗口」时重试（NoWindowError）；其它错误（含
        裁剪参数越界、窗口选择器无匹配）立即返回。每轮截图在线程中执行
        （GDI 调用阻塞），并受 _SCREENSHOT_TIMEOUT 保护。

        Args:
            pid: 目标进程 PID。
            path: 输出 PNG 路径。
            crop: 可选裁剪区域。
            window: 可选窗口选择器（缺省 = 主窗口）。
            grid: 可选网格步长（``None`` = 不画）。

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
        if grid is not None:
            kwargs["grid"] = grid
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
        """向后台任务的目标 GUI 窗口注入鼠标 / 键盘 / 文本输入。

        坐标以窗口截图左上角为原点（与 op=screenshot 产物一致），便于
        「先截图看清界面，再按像素点操作」。``window`` 可把输入投向指定窗口
        （右键菜单、下拉浮层、对话框等独立顶层窗口）；``settle`` 在注入后等待
        指定秒数再返回；``shot`` 在注入后自动截图并把结果放进返回 JSON，省去
        额外一次 screenshot 调用。结果返回 JSON（task_id/op 与动作细节如按钮、
        坐标、屏幕坐标、按键序列、投递方式）。
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
            settle = self._resolve_settle()
        except ValueError as exc:
            return f"(输入参数非法: {exc})"
        try:
            result = await self._send_input_with_retry(pid, action)
        except (InputNoWindowError, InputError) as exc:
            message = str(exc)
            if "窗口选择器" in message:
                return (f"(输入失败: {message}。弹出菜单 / 下拉浮层这类窗口在失焦"
                        f"或截图 / 窗口提权后可能已关闭，可先用 op=windows 复核"
                        f"当前窗口，再用同一选择器重试)")
            if "客户区" in message:
                return (f"(输入失败: {message}。该窗口没有可换算的客户区，无法用 "
                        f"method='message' 投递鼠标坐标——默认的 method='auto' "
                        f"会改用合成输入（真实光标命中光标下的窗口）；若显式传了 "
                        f"method='message'，去掉它后重试)")
            return f"(输入失败: {message})"
        payload = {
            "task_id": self.task_id,
            "op": self.op,
            "hint": ("输入已注入；可用 op=screenshot 截图后用 read_image 核对界面变化"
                     "（坐标原点为窗口截图左上角，与 op=screenshot 产物一致；"
                     "window 选择器可用 'main' / "
                     "'#N'（当前可操作窗口的 Z 序，见 op=windows 的 z_index）/ "
                     "'popup' / 'title:子串' / 'handle:0x…'；"
                     "结果里的 window_frame 给出命中窗口的截图坐标系 "
                     "screen_x/screen_y/width/height，可据此把输入坐标与截图坐标对齐）"),
        }
        if self.window:
            payload["window"] = str(self.window)
        payload.update(result.to_dict())
        if settle:
            await asyncio.sleep(settle)
        shot_error = await self._attach_shot(payload, rec)
        if shot_error:
            payload["screenshot_error"] = shot_error
        return json.dumps(payload, ensure_ascii=False)

    def _resolve_settle(self) -> float:
        """解析注入后等待时长（秒）：未指定时「带 shot」默认等一小会儿。

        Raises:
            ValueError: 取值非法（非数值或为负）。
        """
        raw = self.settle
        if raw is None:
            return self._DEFAULT_SHOT_SETTLE if self.shot else 0.0
        if isinstance(raw, bool):
            raise ValueError("settle 需要数值（秒）")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"settle 需要数值（秒），当前: {raw!r}") from None
        if math.isnan(value) or value < 0:
            raise ValueError(f"settle 不能为负数，当前: {raw!r}")
        return min(value, self._MAX_SETTLE_SECONDS)

    def _shot_path(self) -> str:
        """解析 shot 参数为截图路径（``true`` 等占位值 → 自动命名）。"""
        raw = self.shot
        auto = raw is True
        if not auto and isinstance(raw, str) and raw.strip().lower() in (
                "true", "auto", "yes", "on"):
            auto = True
        if auto:
            stamp = time.strftime("%H%M%S")
            return os.path.join(self._SHOT_AUTO_DIR, f"{self.task_id}-{stamp}.png")
        return str(raw or "").strip()

    async def _attach_shot(self, payload: dict, rec: dict) -> str | None:
        """输入 op 的 shot 参数：注入后自动截图，结果写入 ``payload["screenshot"]``。

        Returns:
            失败原因（成功或未启用时返回 None）；截图失败不影响注入结果。
        """
        if not self.shot:
            return None
        path = self._shot_path()
        if not path:
            return "shot 需要截图路径（或 true 自动命名到 bash_opt_shots/）"
        try:
            target_path = self._prepare_screenshot_path(path)
        except ValueError as exc:
            return f"截图路径非法: {exc}"
        pid = rec.get("pid")
        if pid is None:
            return "任务尚无进程句柄，无法截图"
        try:
            grid = self._resolve_grid()
        except CropError as exc:
            return f"截图网格参数非法: {exc}"
        try:
            result = await self._capture_with_retry(
                pid, target_path, None, window=self.window, grid=grid)
        except (SelectorError, ScreenshotError) as exc:
            scope = f"（window 选择器 {self.window!r}）" if self.window else ""
            return f"截图失败{scope}: {exc}"
        payload["screenshot"] = result.to_dict()
        payload["hint"] = (f"{payload.get('hint', '')}；已自动截图，"
                           f"可用 read_image 读取 screenshot.path")
        return None

    # ── op=windows / op=window ───────────────────────────

    async def _op_windows(self, rec: dict) -> str:
        """列出后台任务进程树的全部窗口（挑选目标窗口 / 排查窗口问题用）。

        返回 JSON（total + windows 列表）：每个窗口含句柄、标题、类名、位置
        尺寸、Z 序（``order``，0 = 最靠前）、是否前台 / 最小化 / 主窗口。模型
        据此把 ``window`` 选择器用于 screenshot / 输入 op / 窗口控制，即可精确
        操作弹出菜单、下拉浮层、对话框等独立顶层窗口。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法枚举窗口。可用 op=wait 查看任务状态)")
        try:
            infos = await asyncio.wait_for(
                asyncio.to_thread(list_process_windows, pid),
                timeout=self._INPUT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            return (f"(枚举窗口超时（超过 {self._INPUT_TIMEOUT:g} 秒）："
                    f"系统窗口枚举无响应")
        described = describe_windows(infos)
        payload = {
            "task_id": self.task_id,
            "op": "windows",
            "pid": pid,
            "total": len(infos),
            "windows_total": len(infos),
            "selectable_total": sum(1 for item in described if item.get("selectable")),
            "windows": described,
        }
        if infos:
            payload["hint"] = ("用 window 参数把 screenshot / 输入 op 投向指定窗口："
                               "'main'（缺省主窗口）、'#N'（可操作窗口的 Z 序第 N 个，"
                               "取 windows[].z_index；如弹出的右键菜单 / 下拉浮层）、"
                               "'active'（前台窗口）、'title:子串'、'class:子串'、"
                               "'handle:0x…'、'popup'、'dialog'。"
                               "selectable=false 的窗口（visible=false 或已最小化）"
                               "不会被 'main' / '#N' / 'popup' / 'dialog' 选中，"
                               "需要时用 handle:0x… 显式指定；"
                               "计数看 selectable_total（可操作窗口数）与 "
                               "windows_total（含隐藏 / 最小化的全部窗口数）。"
                               "'#N' 的编号与截图结果里的 window_summary 编号同源"
                               "（都按可操作窗口的 Z 序），可直接互相参照。"
                               "tool_window=true 的弹层（右键菜单 / 下拉浮层 / 弹出"
                               "窗口）不参与前台切换，直接点击即可：输入 op 会按"
                               "「该窗口所属应用是否在前台」判定，鼠标事件按屏幕"
                               "坐标命中光标下的真实窗口、键盘事件交给同一应用的"
                               "前台窗口（结果里的 foreground_window 标注了实际"
                               "接收窗口）。client_area=false 表示该窗口没有可换算"
                               "的客户区，此时不能用 method='message' 投递鼠标坐标"
                               "（去掉 method 用默认的合成输入即可）")
            payload["summary"] = window_hint(infos)
        else:
            payload["hint"] = ("未找到可见窗口（纯命令行进程没有 GUI 窗口；"
                               "窗口已最小化或被隐藏时也找不到）")
        return json.dumps(payload, ensure_ascii=False)

    async def _op_window(self, rec: dict) -> str:
        """控制被选窗口的状态与几何（激活 / 最大化 / 最小化 / 还原 / 关闭 / 移动 / 缩放）。

        配合 ``window`` 选择器指定目标窗口，``window_action`` 指定动作；
        ``move`` / ``fit`` 用 ``x`` / ``y``（屏幕坐标），``resize`` / ``fit`` 用
        ``width`` / ``height``。返回动作前后的窗口状态，便于确认结果。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法控制窗口。可用 op=wait 查看任务状态)")
        try:
            request = parse_control_request(
                self.window_action, window=self.window, x=self.x, y=self.y,
                width=self.width, height=self.height,
            )
        except SelectorError as exc:
            return f"(窗口控制参数非法: {exc})"
        try:
            detail = await asyncio.wait_for(
                asyncio.to_thread(control_process_window, pid, request),
                timeout=self._INPUT_TIMEOUT,
            )
        except (SelectorError, ScreenshotError) as exc:
            return f"(窗口控制失败: {exc})"
        except asyncio.TimeoutError:
            return (f"(窗口控制超时（超过 {self._INPUT_TIMEOUT:g} 秒）：窗口无响应")
        payload = {
            "task_id": self.task_id,
            "op": "window",
            "hint": ("窗口状态已变更；可用 op=screenshot（可带 window 选择器）"
                     "截图核对，或继续用输入 op 操作"),
        }
        payload.update(detail)
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
            "method": self.method, "phase": self.phase,
            "repeat": self.repeat,
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
