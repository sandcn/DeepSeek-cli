"""
bash_opt — 按 task_id 操作后台 bash 任务

配合 bash 工具 background=True 模式使用。bash 后台启动后返回
{"task_id": "bg-xxx", "status": "running"}，
大模型可据此用 bash_opt 工具按 task_id 操作：

- op=read   读取后台命令**当前已产生**的全部输出并清空缓冲，立即返回（不等待完成）
- op=wait   等待任务执行完成并获取结果（JSON：task_id/status/stdout/stderr/returncode）
- op=kill   杀死后台命令的所有进程树（进程组 + 全部递归后代），杀完后
            **校验**进程是否真正退出（僵尸视为已终止）；仍有存活则**自动
            补杀**（最多 3 轮尝试），返回信息报告校验结论与残留 PID
- op=stdin  向后台命令的 stdin 发送文本输入（text 参数，newline 可选是否追加换行）
- op=keys   向后台命令发送光标/键盘消息（自动路由：目标进程有 GUI 窗口时
            作为窗口级键盘消息注入该窗口，否则回退写入终端——跨平台
            ANSI/VT100 转义序列；支持 ctrl+c 等修饰键组合、esc/pageup 等
            别名、单个字符与 f1-f24，repeat 可一次连按多次）
- op=screenshot  把后台命令（及其子进程）的窗口，或整个屏幕，截图保存为 PNG
                 （path 参数指定文件路径；可选 crop 指定只截取的像素区域，
                 window 选择目标窗口，grid 叠加等距坐标参考线；
                 screen=true/'primary'/序号 做整屏 / 多显示器截取（不需要进程
                 句柄），element+margin 只截某个控件的区域）
- op=windows     列出该进程树的全部窗口（句柄 / 标题 / 类名 / 位置尺寸 /
                 Z 序 / 是否前台 / 是否主窗口），用于挑选目标窗口
- op=window      控制被选窗口的状态与几何（window_action=activate / maximize /
                 minimize / restore / close / move / resize / fit），
                 配合 window 选择器与 x/y/width/height
- op=move / hover / click / drag / scroll / key / type
                 向后台命令的 **GUI 窗口**注入鼠标 / 键盘 / 文本输入
                 （window 选择目标窗口、settle 注入后等待、shot 注入后自动截图；
                  鼠标按钮、双击、拖动、滚轮、组合键、任意 Unicode 文本；
                  按键可分「按下 / 弹起 / 完整」阶段，见 phase 参数，
                  并用 repeat 一次连按多次；hover 移动到目标点停留触发挥发性
                  界面，move 另支持 dx/dy 相对当前光标偏移，click 另支持 hold
                  长按与 interval 多击间隔，move 另支持 duration/steps 平滑移动）
- op=sequence    一次调用按顺序执行多个动作（click / move / drag / scroll /
                 key / type 与 wait / screenshot / window 步骤混排），
                 减少往返、避免两次调用之间被抢焦点；on_error 决定遇错
                 停止还是继续，可在整段前后自动截图比较变化
- op=clipboard   读写系统剪贴板（clipboard_action=set / get / clear / append）；
                 配合 type 的 via='clipboard'，把长文本 / 特殊字符（中文、
                 emoji、多行）以「粘贴」方式可靠输入（游戏、Electron、
                 远程桌面都接受）；粘贴后恢复原剪贴板内容前会留出片刻，
                 避免目标程序（浏览器 / Electron）异步读剪贴板时拿到旧内容
- op=wait_window 等待窗口出现（按 window 选择器，timeout 秒）；GUI 程序
                 启动慢时先等窗口就绪再操作，避免「窗口还没出现」空转
- op=elements    列出窗口内的控件（名称 / 类型 / 类名 / 矩形 / 可用状态）；
                 可用 element 参数（如 element='确定'）直接按控件名点击或
                 输入，不必读图算像素（Windows 优先走 UI Automation，
                 Chrome / Electron / Qt / WPF 等自绘界面也能枚举到无障碍
                 节点；极少暴露元素时再回退截图 + 像素坐标，见 op=locate）
- op=locate      在窗口截图里定位「局部图标」（template 模板匹配）或
                 「文字」（query OCR），返回与输入 op 同源的坐标，可直接点击；
                 适合没有可枚举控件的界面（游戏 / canvas / 图片按钮）
- op=pixel       读取窗口截图像素颜色：mode=point 点取色、region 区域统计
                 （均值 / 极值 / 主色）、find 查找目标颜色并按连通块返回候选
                 位置；用于状态灯 / 进度条等无可枚举控件的判定
- op=annotate    在截图上绘制矩形 / 十字 / 编号标签（可用 locate 结果复核），
                 产出带标注的图；boxes / points / labels 描述标记
- op=record      把一段操作序列（actions，与 sequence 同构）保存为命名宏
- op=replay      回放命名宏（macro / path），times 可重复多次；重复性 GUI
                 任务一次固化、随时重跑
- op=release     释放按下的键与鼠标按钮（keys / buttons 可只释放指定目标）；
                 清理 key/click 的 phase='down' 留下的悬空状态

★ 游戏操作增强（本工具对游戏场景的重点优化）：

  - move 的 relative_event：发送**纯相对位移事件**做视角控制——第一人称 /
    第三人称游戏读取的是相对位移，且不受游戏 ClipCursor（锁定光标到窗口
    中心）影响；steps 拆分位移、interval 控制步间间隔；
  - click 的 phase=down/up：把鼠标按下与弹起分离，实现「按住左键射击 /
    拖框 / 按住右键瞄准」这类跨调用保持按键的操作；hold 一步完成长按；
    count 上限放宽到 100（连点），interval 控制连点节奏；
  - key 的 hold / interval：一步完成「按住某键 hold 秒」（持续移动 / 蓄力），
    连发（repeat）可用 interval 自定义速率；
  - hold_keys（任意鼠标 / 键盘动作通用）：动作期间按住任意键（如按住 W
    同时点击、按住 Shift 跑动），比 modifiers 更通用（modifiers 仅限
    修饰键）；必要时用 op=release 兜底释放，避免卡键；
  - sequence / replay 期间自动开启「输入会话」：批量动作复用窗口定位与前台
    确认，连续输入更跟手；
  - screenshot 对 DirectX 独占全屏游戏：窗口自身 DC 拿不到画面时会自动从
    桌面屏幕 DC 拷贝窗口区域，尽量截到真实游戏画面。

★ 「一次调用把一组操作做完」的推荐组合：

  - sequence + actions：点输入框 → 输入 → 回车 → 截图，一条调用完成；
  - element + type：按控件名输入（先聚焦再输入，内部自动换算坐标）；
  - type 的 via='clipboard'：长文本粘贴（比逐字符输入快且不掉字；目标程序
    读取剪贴板较慢时配 restore_clipboard=false 更稳）；
  - wait_for='change' / diff：操作后等界面真的变了再返回，确认生效。

坐标语义（输入 op 与 sequence 通用）：x / y 除像素整数外，还支持语义值
——'center' / 'middle'、'left' / 'right' / 'top' / 'bottom'、百分比 '50%'、
偏移写法 'center+20' / 'center-20' / 'left+20' / 'right-10' / 'top+5' /
'bottom-30'（基准 + 像素偏移，自动夹到窗口内）；语义值在注入时按窗口实际
尺寸换算，窗口被缩放也不失准。

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
确实需要操作这类窗口时用 'handle:0x…' 显式指定——截图会对「已最小化 / 不可见」
的目标直接报错并提示先 restore，不会产出与界面无关的占位小图。

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
像素坐标（省略 = 不叠加；网格步长传 0 表示按画面尺寸自动选择）；结果 JSON
附带被截窗口的句柄 / 标题 / 候选窗口总数，便于确认选对了窗口。命中的窗口若
已最小化或不可见，截图会直接报错并提示先 restore（最小化窗口没有可渲染的
客户区，硬截只会得到与界面无关的占位小图）。

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
import tempfile
import time
from contextlib import asynccontextmanager

from .base import Func
from .bash import kill_process_tree
from .file_ops import validate_path_security
from ._screenshot import (
    CropError,
    CropRegion,
    MonitorError,
    NoWindowError,
    ScreenshotError,
    SelectorError,
    capture_process_window,
    capture_screen,
    control_process_window,
    describe_elements,
    describe_windows,
    filter_elements,
    indexed_summary,
    list_monitors,
    list_process_elements,
    list_process_windows,
    parse_control_request,
    pick_window,
    resolve_monitor,
    window_geometry,
    window_hint,
)
from ._screenshot.diff import (
    DEFAULT_TOLERANCE,
    compare_png_files,
)
from ._screenshot.imagematch import (
    DEFAULT_MATCH_TOLERANCE,
    DEFAULT_MAX_RESULTS,
    ImageMatchError,
    match_template,
)
from ._screenshot.ocr import (
    OcrError,
    find_text as find_ocr_text,
    recognize as recognize_text,
)
from ._screenshot.png_decode import DecodedImage, decode_png_file
from ._screenshot.color import (
    DEFAULT_COLOR_TOLERANCE,
    DEFAULT_MAX_REGIONS,
    MAX_REGIONS_LIMIT,
    ColorError,
    RGB,
    find_color_regions,
    parse_color,
    pixel_at,
    region_stats,
    summarize as summarize_color,
)
from ._screenshot.annotate import (
    DEFAULT_ANNOTATE_COLOR,
    DEFAULT_TEXT_SCALE,
    DEFAULT_THICKNESS as DEFAULT_ANNOTATE_THICKNESS,
    MAX_MARKS,
    AnnotateError,
    annotate_png_file,
)
from ._screenshot.transform import crop_rgb
from ._screenshot.elements import (
    DEFAULT_ELEMENT_LIMIT,
    ElementError,
    ElementInfo,
    match_element as match_window_element,
)
from ._screenshot.windows import (
    WINDOW_CONTROL_ACTIONS,
    WINDOW_MEMORY_ACTIONS,
    normalize_control_action,
)
from ._clipboard import (
    ClipboardError,
    read_clipboard_text,
    write_clipboard_text,
)
from ._window_input import (
    DEFAULT_KEY_REPEAT,
    INPUT_OPS,
    MAX_KEY_REPEAT,
    ActionError,
    InputError,
    NoWindowError as InputNoWindowError,
    SequenceError,
    SequenceStep,
    begin_input_session,
    build_action,
    end_input_session,
    parse_sequence,
    probe_window,
    resolve_backend as resolve_input_backend,
    send_window_input,
    wait_seconds,
)
from ._terminal_keys import SUPPORTED_TERMINAL_KEYS, parse_terminal_key
from ._window_input.action import parse_coordinate
from ._window_input.macro import (
    DEFAULT_MACRO_DIR,
    Macro,
    MacroError,
    list_macros as list_saved_macros,
    load_macro,
    save_macro,
)
from ..core.base_agent import _parse_bash_result_fields

logger = logging.getLogger(__name__)

#: 参数「未显式传入」的哨兵（与显式 None / 空值区分，供内部方法复用参数）
_UNSET: object = object()

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


def _box_in_crop(box, crop) -> bool:
    """文本框是否与裁剪区域相交（OCR 结果按 crop 过滤时用）。"""
    return not (box.left + box.width <= crop.x or box.left >= crop.x + crop.width
                or box.top + box.height <= crop.y or box.top >= crop.y + crop.height)


def _attach_screen_coords(payload: dict, frame) -> None:
    """给 ``op=pixel`` 结果补上屏幕坐标（就地修改）。

    点取色结果的 ``x`` / ``y``、颜色查找各块的 ``x`` / ``y`` / ``center_x`` /
    ``center_y`` 都是窗口截图坐标；这里按 frame 原点换算并写入 ``screen_*``
    字段，便于把坐标直接用给其它工具或系统级操作。
    """
    if isinstance(payload.get("x"), int) and isinstance(payload.get("y"), int):
        payload["screen_x"] = payload["x"] + frame.screen_x
        payload["screen_y"] = payload["y"] + frame.screen_y
    for region in payload.get("regions", []) or []:
        if not isinstance(region, dict):
            continue
        for x_key, y_key in (("x", "y"), ("center_x", "center_y")):
            if isinstance(region.get(x_key), int) and isinstance(region.get(y_key), int):
                region["screen_" + x_key] = region[x_key] + frame.screen_x
                region["screen_" + y_key] = region[y_key] + frame.screen_y


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
    #: 序列（sequence）默认的「遇错」策略：stop 停止 / continue 继续
    _DEFAULT_ON_ERROR: str = "stop"
    #: on_error 取值
    _ON_ERROR_MODES: tuple[str, ...] = ("stop", "continue")
    #: type 的输入方式：typing 逐字符注入 / clipboard 走系统剪贴板粘贴
    _TYPE_VIAS: tuple[str, ...] = ("typing", "clipboard")
    #: 剪贴板粘贴组合键（默认 ctrl+v；macOS 为 command+v）
    _PASTE_KEYS: dict[str, str] = {
        "windows": "ctrl+v", "macos": "meta+v", "x11": "ctrl+v",
    }
    #: 剪贴板粘贴组合键的兜底（平台无法判定时）
    _DEFAULT_PASTE_KEY: str = "ctrl+v"
    #: 剪贴板粘贴后、恢复原剪贴板内容前的等待（秒）。
    #: 浏览器 / Electron / 远程桌面等目标的「粘贴」是异步消息处理（收到
    #: 粘贴键后才去读剪贴板），恢复太快会让它们读到**旧内容**（表现为
    #: 「粘出来的还是上一次的剪贴板文本」）；这里留出读取窗口再恢复。
    _CLIPBOARD_RESTORE_DELAY: float = 0.35
    #: clipboard_action 取值（缺省按是否提供 text 推断）
    _CLIPBOARD_ACTIONS: tuple[str, ...] = ("set", "get", "clear", "append")
    #: clipboard 读取返回的字符数上限（防御超大剪贴板内容撑爆上下文）
    _CLIPBOARD_MAX_CHARS: int = 100_000
    #: wait_window 默认超时（秒）——未显式传 timeout 时使用
    _WAIT_WINDOW_TIMEOUT: float = 15.0
    #: wait_window 轮询间隔（秒）
    _WAIT_WINDOW_INTERVAL: float = 0.5
    #: wait_for（等待界面变化 / 稳定）默认超时（秒）
    _WAIT_FOR_TIMEOUT: float = 5.0
    #: wait_for 轮询间隔（秒）
    _WAIT_FOR_INTERVAL: float = 0.25
    #: wait_for 轮询间隔的放大系数（每轮乘以此值）
    _WAIT_FOR_BACKOFF: float = 1.5
    #: wait_for 轮询间隔上限（秒）——自适应退避，减少长时间等待时的截图次数
    _WAIT_FOR_MAX_INTERVAL: float = 1.0
    #: wait_for='stable' 允许忽略的微小变化比例（变化像素占比不超过该值时
    #: 仍判为「画面已稳定」）：带输入光标的界面会因光标闪烁永远等不到
    #: 「逐像素一致」，这类噪声（光标 / 时钟秒数）不应让稳定判定失败。
    _STABLE_CHANGE_RATIO: float = 0.001
    #: 界面比较的默认颜色容差（每通道）
    _DIFF_TOLERANCE: int = DEFAULT_TOLERANCE
    #: 输入动作后等待界面变化的判定模式
    _WAIT_FOR_MODES: tuple[str, ...] = ("change", "stable")
    #: elements 默认返回的控件条数上限
    _DEFAULT_ELEMENT_LIMIT: int = DEFAULT_ELEMENT_LIMIT
    #: elements 单次枚举的最大条数（防止极端界面输出过长）
    _MAX_ELEMENT_LIMIT: int = 2000
    #: op=locate 图像匹配的默认容差（每像素平均通道差）
    _LOCATE_TOLERANCE: int = DEFAULT_MATCH_TOLERANCE
    #: op=locate 返回结果条数上限
    _MAX_LOCATE_RESULTS: int = 50
    #: op=locate 模板多尺度搜索的步数上限
    _MAX_LOCATE_SCALE_STEPS: int = 21
    #: op=pixel 的取色模式（point 点取色 / region 区域统计 / find 颜色查找）
    _PIXEL_MODES: tuple[str, ...] = ("point", "region", "find")
    #: op=pixel 缺省模式
    _DEFAULT_PIXEL_MODE: str = "point"
    #: op=pixel 颜色查找返回的连通块数上限
    _MAX_PIXEL_REGIONS: int = MAX_REGIONS_LIMIT
    #: op=pixel 颜色查找默认连通块数
    _DEFAULT_PIXEL_REGIONS: int = DEFAULT_MAX_REGIONS
    #: op=annotate 默认标注颜色（RGB）
    _ANNOTATE_COLOR: tuple[int, int, int] = DEFAULT_ANNOTATE_COLOR
    #: op=annotate 默认字号放大倍率
    _ANNOTATE_TEXT_SCALE: int = DEFAULT_TEXT_SCALE
    #: op=annotate 默认边框线宽
    _ANNOTATE_THICKNESS: int = DEFAULT_ANNOTATE_THICKNESS
    #: 单次标注的最大标记数
    _MAX_ANNOTATE_MARKS: int = MAX_MARKS
    #: 宏（record / replay）默认存放目录
    _MACRO_DIR: str = DEFAULT_MACRO_DIR
    #: op=replay 默认重复次数
    _DEFAULT_REPLAY_TIMES: int = 1
    #: op=replay 重复次数上限
    _MAX_REPLAY_TIMES: int = 20

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
                    "kill（杀整个进程树：进程组 + 全部递归后代，杀后校验是否"
                    "真正退出、未死自动补杀，最多 3 轮尝试，残留进程在结果中"
                    "报告）、stdin（发文本到 stdin，需 text）、"
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
                    "move/hover/click/drag/scroll/key/type（向该命令进程树的 GUI 窗口注入"
                    "鼠标移动（绝对 x/y 或相对 dx/dy）/悬停/点击（左中右键、"
                    "可双击、可 hold 长按）/拖动/滚轮/按键/文本，"
                    "key 支持 phase=press/down/up 的按下与弹起分离发送，"
                    "window 可选目标窗口，shot 可注入后自动截图，"
                    "坐标以窗口截图左上角为原点且可用 screenshot 对照；"
                    "element 可按控件名定位（click/move/scroll/drag 用控件中心，"
                    "type/key 先点击该控件聚焦）；type 可用 via='clipboard' 走"
                    "剪贴板粘贴输入长文本/中文/emoji；wait_for='change'/'stable' "
                    "可在注入后等界面变化或稳定；diff=true 会回传注入前后截图差异；"
                    "x/y 支持 'center'、'50%'、'center+20' 等语义坐标）、"
                    "sequence（一次调用按序执行多个动作：actions 数组，步骤可为"
                    "输入动作或 wait/screenshot/window，on_error 决定遇错停止/继续）、"
                    "clipboard（读写系统剪贴板：clipboard_action=set/get/clear/append，"
                    "set/append 需 text）、"
                    "wait_window（等待窗口出现，按 window 选择器，timeout 秒）、"
                    "elements（列出窗口内控件清单：名称/类型/矩形/可用状态，"
                    "含窗口内坐标可直接用于 click；max_elements 限制条数，"
                    "element 作为过滤子串）。"
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
                                     "elements", "wait_window", "clipboard",
                                     "locate", "pixel", "annotate",
                                     "record", "replay", "sequence",
                                     *INPUT_OPS],
                            "description": (
                                "要执行的操作："
                                "\n- read：读取后台命令当前已产生的全部输出并清空缓冲，"
                                "立即返回（不等待任务完成）；后续 read 只返回新产生的输出，"
                                "最终完整结果由 wait 获取"
                                "\n- wait：等待任务完成并获取命令输出"
                                "\n- kill：杀死任务所有进程树（进程组 + 全部递归"
                                "后代）；杀完后校验进程是否真正退出（僵尸视为"
                                "已终止），仍有存活则自动补杀（最多 3 轮尝试）；"
                                "校验时仍存活的残留 PID 会在结果中列出"
                                "\n- stdin：向任务 stdin 发送文本输入（需 text）"
                                "\n- keys：向任务发送光标/键盘消息（需 key；"
                                "目标进程有 GUI 窗口时自动作为窗口级键盘消息注入该窗口，"
                                "没有 GUI 窗口则写入终端 PTY/stdin；"
                                "支持 ctrl+c 等组合键、esc/pageup 等别名与单个字符）"
                                "\n- screenshot：把任务进程树（含其启动的 GUI 子进程）的窗口"
                                "或整个屏幕截图保存为 PNG 文件（需 path；可选 crop 指定只截取的像素区域，"
                                "screen=true/'primary'/序号 整屏 / 多显示器截取（不再需要进程句柄），"
                                "element+margin 只截某控件区域），"
                                "用于查看图形程序运行画面；"
                                "结果附带 window_x/window_y（截图像素左上角对应的屏幕坐标）"
                                "与 window_rect（窗口外框），便于截图像素与屏幕坐标换算；"
                                "纯命令行进程没有窗口，会返回错误说明"
                                "\n- move/hover/click/drag/scroll/key/type：向任务进程树的 GUI 窗口"
                                "注入输入（鼠标移动（x/y 绝对或 dx/dy 相对；"
                                "duration+steps 可做平滑移动）/悬停/点击（左中右键、"
                                "双击即 count=2、hold 长按）/拖动/滚轮、"
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
                                "activate/maximize/minimize/restore/close/move/resize/"
                                "fit/always_on_top/not_on_top/get_geometry/"
                                "save_geometry/restore_geometry，配合 window 选择器"
                                "指定目标窗口；always_on_top 置顶避免被遮挡，"
                                "save_geometry/restore_geometry 记住并恢复几何）"
                                "\n- elements：列出窗口内控件（名称/类型/类名/矩形/"
                                "可用状态），含屏幕坐标与窗口内坐标（window_center_x/"
                                "window_center_y，可直接用于 click）；"
                                "element 可作为过滤子串，max_elements 限制条数。"
                                "Windows 优先走 UI Automation：Chrome/Electron/"
                                "Qt/WPF/UWP 等自绘界面也能枚举到无障碍节点；"
                                "UIA 不可用时回退经典 Win32 子窗口枚举"
                                "\n- wait_window：等待窗口出现（按 window 选择器，"
                                "timeout 秒，默认 15 秒）；GUI 程序启动慢时先等"
                                "窗口就绪再操作"
                                "\n- clipboard：读写系统剪贴板"
                                "（clipboard_action=set/get/clear/append，"
                                "set/append 需 text；缺省按是否提供 text 推断）；"
                                "配合 key='ctrl+v' 或 type 的 via='clipboard' 粘贴"
                                "长文本/中文/emoji"
                                "\n- locate：在窗口截图里定位「局部图标」或"
                                "「文字」，返回坐标（与输入 op 同源，可直接点击）。"
                                "给 template=模板图片路径 做模板匹配；给 query="
                                "要查找的文字 做 OCR 文字识别（Windows 用系统自带 "
                                "OCR，其它平台用 tesseract）。适合没有可枚举控件的"
                                "界面（游戏 / canvas / 图片按钮）；crop 可限定"
                                "搜索区域"
                                "\n- sequence：一次调用按序执行多个动作"
                                "（actions 数组，每项形如 {\"op\": \"click\", \"x\": 10}；"
                                "步骤可为输入动作或 wait（seconds）/screenshot（path）/"
                                "window（window_action）；on_error=stop/continue 决定"
                                "遇错停止还是继续）。适合「点输入框→输入→回车→截图」"
                                "这类连续操作，减少往返与中途失焦"
                                "\n- pixel：读取窗口截图像素颜色（mode=point 点取色 / "
                                "region 区域均值与主色 / find 查找目标颜色并按连通块"
                                "返回可点击的候选位置），适合识别状态灯、进度条等"
                                "没有可枚举控件的画面"
                                "\n- annotate：在截图上绘制矩形 / 十字 / 编号标签"
                                "（常配合 locate 的匹配结果复核），产出带标注的图；"
                                "boxes / points / labels 可用 window 截图或 path 指定"
                                "\n- record / replay：把一段操作序列（actions）保存为"
                                "命名宏（macro），之后一条 replay 调用重复回放"
                                "（times 可重复多次），适合重复性的 GUI 任务"
                                "\n- release：释放按下的键与鼠标按钮（keys / buttons "
                                "可只释放指定目标，留空释放全部）——清理 key/click 的 "
                                "phase='down' 留下的悬空状态，是游戏操作中断后的兜底"
                                "\n- 游戏操作增强：move 的 relative_event（发送纯相对"
                                "位移事件做视角控制，不受 ClipCursor / 锁定光标影响）、"
                                "click 的 phase=down/up（按住左键射击 / 拖框）与 hold "
                                "长按、key 的 hold 长按与 interval 连发间隔、hold_keys "
                                "（动作期间按住任意键，如按住 W 同时点击）"
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
                                "字母 / 数字键走系统虚拟键码，会经过系统输入法（IME）："
                                "中文输入法激活时，连按（repeat）字母键可能被输入法"
                                "吞并或变成候选上屏；需要稳定连按请先切到英文输入法，"
                                "或改用 type（Unicode 注入，不受输入法影响）。"
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
                                "'title~:正则' / 'class~:正则' / 're:正则' 按正则匹配"
                                "标题 / 类名、'process:进程名'（如 process:chrome）"
                                "按进程（exe）名匹配、'fuzzy:关键词' 模糊匹配、"
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
                                "线间距像素（省略 = 不叠加参考线；0 或 true = 按画面"
                                "尺寸自动选步长约 10 格；如 grid=50 每 50 像素一条主线、"
                                "每 25 像素一条次线），便于读图后精确给出 x/y 坐标。"
                            ),
                        },
                        "screen": {
                            "type": ["boolean", "string", "number"],
                            "description": (
                                "仅 screenshot / sequence 的 screenshot 步骤可选："
                                "整屏 / 多显示器截取（不限于目标进程的窗口）。"
                                "true / 'all' / '0' = 整个虚拟桌面（所有显示器）；"
                                "'primary' = 主显示器；序号（1 / '2'）= 第 N 个"
                                "显示器。省略 / false = 截目标窗口。"
                            ),
                        },
                        "margin": {
                            "type": "number",
                            "description": (
                                "仅 screenshot / sequence 的 screenshot 步骤可选："
                                "与 element 配合，把控件区域向四周外扩的像素数"
                                "（默认 0，可为负表示收缩），用于截控件及其周边一点"
                                "范围。"
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
                                "仅输入 op 可选（sequence 的步骤也可带）：注入后等待的"
                                "秒数再返回（默认 0；指定 shot 时默认 0.2 秒），用于等"
                                "界面完成刷新，避免截图拍到旧画面。需要「等界面真的变了」"
                                "而不是固定等待时，改用 wait_for='change'。"
                            ),
                        },
                        "window_action": {
                            "type": "string",
                            "enum": ["activate", "maximize", "minimize", "restore",
                                     "close", "move", "resize", "fit",
                                     "always_on_top", "not_on_top",
                                     "get_geometry", "save_geometry",
                                     "restore_geometry"],
                            "description": (
                                "仅 window 操作必填：窗口控制动作。activate 置前激活、"
                                "maximize/minimize/restore 最大化/最小化/还原、"
                                "close 请求关闭（等价点关闭按钮）、move 按屏幕坐标移动"
                                "（需 x/y）、resize 调整尺寸（需 width/height）、"
                                "fit 同时移动并调整尺寸（x/y/width/height 都要）、"
                                "always_on_top/not_on_top 置顶/取消置顶（操作期间防止"
                                "被其它窗口遮挡）、get_geometry 读取当前几何、"
                                "save_geometry 记住当前几何、restore_geometry 恢复"
                                "上次记住的几何（布局固定后再按像素操作更稳）。"
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
                            "type": ["number", "string"],
                            "description": (
                                "窗口内坐标 X（原点为窗口截图左上角，与 screenshot "
                                "产物一致）。支持像素整数或语义值：'center'/'middle'、"
                                "'left'/'right'、百分比 '50%'、偏移写法"
                                "'center+20'/'center-20'/'left+20'/'right-10'"
                                "（语义值按窗口实际尺寸在注入时换算，窗口缩放也不失准）。"
                                "move 必填；click / scroll 可选，省略则作用于窗口中心；"
                                "drag 用 from_x/from_y 指定起点；也可用 element 按控件名定位。"
                            ),
                        },
                        "y": {
                            "type": ["number", "string"],
                            "description": (
                                "窗口内坐标 Y（语义值同 x：'center'、'bottom'、'50%'、"
                                "'center-20'、'bottom-30' 等）。与 x 同时提供或同时省略。"
                            ),
                        },
                        "to_x": {
                            "type": ["number", "string"],
                            "description": (
                                "仅 drag：拖动终点的窗口内坐标 X（必填；支持与 x 相同的"
                                "语义值）。"
                            ),
                        },
                        "to_y": {
                            "type": ["number", "string"],
                            "description": "仅 drag：拖动终点的窗口内坐标 Y（必填）。",
                        },
                        "from_x": {
                            "type": ["number", "string"],
                            "description": (
                                "仅 drag 可选：拖动起点的窗口内坐标 X；与 from_y 同时"
                                "省略时从窗口中心按下。给 element 时用控件中心作为起点。"
                            ),
                        },
                        "from_y": {
                            "type": ["number", "string"],
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
                                "仅 click：点击次数（默认 1；2 表示双击，最大 100）。"
                                "游戏连点（射击 / 快速选择）可配 interval 控制节奏；"
                                "phase='down'/'up' 分离阶段下恒按一次处理。"
                            ),
                        },
                        "hold": {
                            "type": "number",
                            "description": (
                                "click / key 的长按时长（秒，默认 0 = 立即弹起）。>0 时"
                                "按下后保持指定时间再弹起：click 为「点住不放」（长按"
                                "拖动 / 蓄力），key 为「按住某键一段时间」（游戏持续"
                                "移动 / 蓄力 / 长按功能键）；上限 30 秒。"
                            ),
                        },
                        "interval": {
                            "type": "number",
                            "description": (
                                "click / key / move 的时间间隔（秒，上限 10）："
                                "click = 多次点击（双击 / 三击）之间的间隔（默认 "
                                "0.05，需小于系统双击时间才会识别为双击）；"
                                "key = 连发（repeat）时相邻两次按键的间隔（默认用"
                                "平台默认约 0.05；游戏连点可设 0.02 加快）；"
                                "move（relative_event） = 相对位移拆步发送时的步间间隔。"
                            ),
                        },
                        "dwell": {
                            "type": "number",
                            "description": (
                                "仅 hover：移动到目标点后的停留时长（秒，默认 0.6，"
                                "上限 30）。用于触发鼠标悬停才出现的 tooltip / 悬浮"
                                "菜单 / 延迟加载。"
                            ),
                        },
                        "dx": {
                            "type": "number",
                            "description": (
                                "仅 move：相对当前光标屏幕位置的水平像素偏移"
                                "（可为负）。与 x/y 互斥——相对移动给 dx/dy，绝对"
                                "移动给 x/y。加 relative_event=true 可改为发送纯"
                                "相对位移事件（游戏视角）。"
                            ),
                        },
                        "dy": {
                            "type": "number",
                            "description": (
                                "仅 move：相对当前光标屏幕位置的垂直像素偏移"
                                "（可为负）。需与 dx 同时提供（不移动的轴给 0）；"
                                "加 relative_event=true 可改为发送纯相对位移事件。"
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
                                "drag / move 可选：动作耗时秒数。"
                                "drag 默认 0.3（0 表示瞬时；需要目标程序识别"
                                "连续移动时调大，最大 10）；move 默认 0（一步直达），"
                                ">0 时在起点与终点之间按 duration 分步平滑移动。"
                            ),
                        },
                        "steps": {
                            "type": "number",
                            "description": (
                                "drag / move 可选：轨迹插值步数。"
                                "drag 默认 20（范围 2-200）；move 默认 1（不插值），"
                                ">1 时按该步数插值分步移动（配合 duration 控制节奏），"
                                "范围 2-200。某些程序（游戏 / 拖选 / 悬停菜单）只"
                                "响应连续移动事件，此时用平滑移动。"
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
                                "key / click 操作的阶段（默认 press）。"
                                "press（按下并弹起，一次完整按键 / 点击）、"
                                "down（只发送「按下」消息，按住不放）、"
                                "up（只发送「弹起」消息）。"
                                "click 用 down/up 分离可实现「按住左键射击 / 拖框 / "
                                "按住右键瞄准」等跨调用保持按键的游戏操作——配合 "
                                "op=release（或 key/click 的 up 阶段）释放，避免卡键。"
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
                        "relative_event": {
                            "type": "boolean",
                            "description": (
                                "仅 move 的相对移动（给了 dx/dy）可选：true 时发送"
                                "**纯相对鼠标位移事件**（不把光标定位到绝对位置）。"
                                "第一人称 / 第三人称游戏读取的正是这种相对位移，且"
                                "不受游戏 ClipCursor（把光标锁到窗口中心）影响——"
                                "旋转视角 / 转镜头请用它。steps 把总位移拆成多个"
                                "等分事件（引擎常忽略单次超大位移），interval 控制"
                                "步间间隔。"
                            ),
                        },
                        "hold_keys": {
                            "type": ["string", "array"],
                            "items": {"type": "string"},
                            "description": (
                                "动作期间额外按住的任意键（点击类 / 移动 / 拖动 / 滚动 / "
                                "hover / key / type 都可用）：字符串（如 'w' 或 "
                                "'w+shift'）或数组（如 ['w', 'shift']），每项是单个"
                                "键（修饰键或普通键）。用于游戏组合键——按住 W 前进的"
                                "同时点击 / 转视角，按住 Shift 跑动等。与 modifiers "
                                "的区别：modifiers 只接受 ctrl/alt/shift/meta，"
                                "hold_keys 接受任意键。"
                            ),
                        },
                        "keys": {
                            "type": ["string", "array"],
                            "items": {"type": "string"},
                            "description": (
                                "仅 release 可选：要释放的键（字符串或数组）；留空 = "
                                "释放本次会话记录的全部已按下键。release 用于清理 "
                                "key/click 的 phase='down' 留下的悬空按下状态。"
                            ),
                        },
                        "buttons": {
                            "type": ["string", "array"],
                            "items": {"type": "string"},
                            "description": (
                                "仅 release 可选：要释放的鼠标按钮（left/right/"
                                "middle，字符串或数组）；留空 = 释放全部已按下的"
                                "鼠标按钮。"
                            ),
                        },
                        "actions": {
                            "type": "array",
                            "items": {
                                "type": "object",
                                "properties": {
                                    "op": {
                                        "type": "string",
                                        "description": (
                                            "步骤类型：click / move / drag / scroll / "
                                            "key / type / wait / screenshot / window"
                                        ),
                                    },
                                },
                            },
                            "description": (
                                "仅 sequence 必填：步骤数组（最多 50 步），每项是一个"
                                "对象，用 op 指定类型："
                                "\n- 输入动作：click / move / drag / scroll / key / type"
                                "（参数与单独调用时相同，另可带 element / via / paste_key /"
                                " restore_clipboard / newline / settle / shot / window /"
                                " wait_for / diff / tolerance）；"
                                "\n- wait：{\"op\": \"wait\", \"seconds\": 0.5} 等待指定秒数；"
                                "\n- screenshot：{\"op\": \"screenshot\", \"path\": \"a.png\"}"
                                " 截图存盘（可选 crop / grid）；"
                                "\n- window：{\"op\": \"window\", \"window_action\": "
                                "\"always_on_top\"} 窗口控制（可选 x/y/width/height）。"
                                "未指定 window 的步骤继承本次调用的 window 选择器；"
                                "适合把「点击输入框 → 输入文本 → 回车 → 截图」一条调用完成。"
                            ),
                        },
                        "on_error": {
                            "type": "string",
                            "enum": ["stop", "continue"],
                            "description": (
                                "仅 sequence 可选：步骤失败时的策略（默认 stop）。"
                                "stop = 立即停止并把已完成步骤与错误一起返回；"
                                "continue = 跳过失败步骤继续执行后续步骤"
                                "（每步的 ok / error 都会如实回报）。"
                            ),
                        },
                        "clipboard_action": {
                            "type": "string",
                            "enum": ["set", "get", "clear", "append"],
                            "description": (
                                "仅 clipboard 可选：set 写入 text、get 读取当前文本、"
                                "clear 清空、append 在原内容后追加 text。"
                                "省略时按是否提供 text 推断（给了 text 即 set，"
                                "否则 get）。"
                            ),
                        },
                        "element": {
                            "type": "string",
                            "description": (
                                "按控件名定位（推荐先用 op=elements 看清单），两种用法："
                                "\n- 输入 op（click / move / scroll / drag / type / key）："
                                "click/move/scroll 用控件中心作为坐标、drag 用控件中心"
                                "作为起点；type/key 先点击该控件聚焦再输入。"
                                "与显式 x/y（drag 的 from_x/from_y）互斥；"
                                "\n- op=elements：作为过滤子串，只返回匹配的控件。"
                                "取值形式：'确定'（按控件文本子串，找不到再按自动化 "
                                "ID、类名、控件类型）、'text:子串'、'class:子串'、"
                                "'id:子串'（自动化 ID / 控件名，如 WinForms 的 "
                                "TextBox.Name）、'type:edit'、'#3'（清单第 3 个控件；"
                                "op=elements 的过滤同样接受 '#N' 写法，与输入 op "
                                "的定位一致）；"
                                "类型匹配同时接受英文类型名与中文标签"
                                "（'edit' / '编辑框'、'button' / '按钮'、'list' / '列表'）。"
                            ),
                        },
                        "via": {
                            "type": "string",
                            "enum": ["typing", "clipboard"],
                            "description": (
                                "仅 type 可选：输入方式（默认 typing）。"
                                "typing = 逐字符合成按键（走 Unicode 注入，不受"
                                "输入法影响）；"
                                "clipboard = 把文本写入系统剪贴板后发送粘贴键"
                                "（默认 ctrl+v，macOS 为 command+v），"
                                "长文本 / 中文 / emoji / 多行文本不会掉字，"
                                "也不会被目标程序误当成快捷键；"
                                "粘贴后默认恢复原剪贴板内容（会先留出片刻让目标程序"
                                "读取剪贴板，避免浏览器 / Electron 粘出旧内容）。"
                            ),
                        },
                        "paste_key": {
                            "type": "string",
                            "description": (
                                "仅 type via='clipboard' 可选：粘贴组合键"
                                "（默认按平台取 ctrl+v / command+v）。"
                                "个别程序需要 shift+insert 等其它粘贴键时可显式指定。"
                            ),
                        },
                        "restore_clipboard": {
                            "type": "boolean",
                            "description": (
                                "仅 type via='clipboard' 可选：粘贴后是否恢复原剪贴板"
                                "内容（默认 true，避免破坏用户剪贴板）。恢复前会等待"
                                "片刻，给目标程序读取剪贴板的时间——浏览器 / Electron / "
                                "远程桌面的粘贴是异步处理，恢复太快会把**旧内容**粘进去"
                                "（表现为「粘出来的还是上一次的剪贴板文本」）；不需要"
                                "保留原剪贴板时传 false 更稳，也少一次剪贴板读写。"
                            ),
                        },
                        "wait_for": {
                            "type": "string",
                            "description": (
                                "仅输入 op 可选：注入后等待界面满足条件再返回。"
                                "'change' 等待画面发生变化（点击后等界面刷新，"
                                "变化区域会一并回报）；"
                                "'stable' 等待画面稳定（动画 / 加载结束；连续两次"
                                "采样一致即算稳定，光标闪烁 / 时钟这类微小噪声会被"
                                "忽略，并在结果的 ignored_change 里说明）；"
                                "也可传秒数（如 '0.5'，等价于增强版 settle）。"
                                "超时由 wait_timeout 控制（默认 5 秒）；"
                                "无法判定时 satisfied 为 null 并附 reason。"
                            ),
                        },
                        "wait_timeout": {
                            "type": "number",
                            "description": (
                                "仅输入 op 可选：wait_for 的超时秒数（默认 5，"
                                "上限 120）。超时返回 satisfied=false，"
                                "不会阻塞后续操作。"
                            ),
                        },
                        "diff": {
                            "type": "boolean",
                            "description": (
                                "仅输入 op 可选：true 时注入前后各截一张图并比较像素"
                                "差异，结果放在返回 JSON 的 diff 字段"
                                "（changed / changed_ratio / region / summary），"
                                "用于确认操作是否真的让界面发生了变化。"
                                "与 wait_for='change' 同时使用时会复用同一张基准图。"
                            ),
                        },
                        "tolerance": {
                            "type": "number",
                            "description": (
                                "输入 op 的 diff / op=locate 可选：像素比较的"
                                "颜色容差（每通道 0..255）。diff 默认 8（0 表示"
                                "要求像素完全一致，界面有抗锯齿 / 淡入淡出动画时"
                                "可调大）；locate 模板匹配默认 25（模板与截图"
                                "比例一致时可用较小值）。"
                            ),
                        },
                        "max_elements": {
                            "type": "number",
                            "description": (
                                "仅 elements 可选：控件清单最多返回多少条"
                                "（默认 200，上限 2000）。控件很多时按需缩小以"
                                "保持输出简洁。"
                            ),
                        },
                        "template": {
                            "type": "string",
                            "description": (
                                "仅 locate：模板图片路径（PNG）。在窗口截图里"
                                "搜索该小图（图标 / 按钮局部），返回其位置与"
                                "置信度；与 query 二选一。"
                            ),
                        },
                        "query": {
                            "type": "string",
                            "description": (
                                "仅 locate：要在画面里查找的文字（OCR）。"
                                "返回匹配文字的包围盒坐标（支持跨词短语）；"
                                "与 template 二选一。也可用 text 传该查询。"
                            ),
                        },
                        "max_results": {
                            "type": "number",
                            "description": (
                                "仅 locate：最多返回多少条匹配（默认 10，上限 50）。"
                            ),
                        },
                        "min_scale": {
                            "type": "number",
                            "description": (
                                "仅 locate 模板匹配：模板最小缩放比例（默认 1.0）。"
                                "模板与截图像素比例不一致时可设 0.5~2.0 搜索。"
                            ),
                        },
                        "max_scale": {
                            "type": "number",
                            "description": (
                                "仅 locate 模板匹配：模板最大缩放比例（默认 1.0，"
                                "需 >= min_scale）。"
                            ),
                        },
                        "scale_steps": {
                            "type": "number",
                            "description": (
                                "仅 locate 模板匹配：在 min_scale..max_scale 之间"
                                "取多少个缩放档（默认 1，上限 21）。"
                            ),
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["point", "region", "find"],
                            "description": (
                                "仅 pixel 可选：取色模式。"
                                "point（默认，需 x/y）读该像素颜色；"
                                "region（需 region）统计区域均值 / 极值 / 主色；"
                                "find（需 color）在窗口（或 region）内查找目标颜色，"
                                "按连通块返回多个候选位置（可点击）。"
                            ),
                        },
                        "color": {
                            "type": "string",
                            "description": (
                                "仅 pixel / annotate 使用：颜色。"
                                "支持 '#RRGGBB'、'rgb(r,g,b)'、'r,g,b' 或颜色名"
                                "（如 'red'）。pixel 里作为「期望色 / 查找目标」"
                                "（给 color 时点 / 区域取色会附带是否匹配的结论）；"
                                "annotate 里作为标记颜色（缺省红色）。"
                            ),
                        },
                        "region": {
                            "type": "string",
                            "description": (
                                "仅 pixel 使用：取色 / 查找区域（'x,y,width,height'，"
                                "窗口截图坐标，原点为窗口截图左上角）；"
                                "省略时：point 模式下用 x/y，find 模式下扫描整窗。"
                            ),
                        },
                        "min_pixels": {
                            "type": "number",
                            "description": (
                                "仅 pixel 的 find 模式：连通块的最小像素数"
                                "（默认 1），用于过滤噪点。"
                            ),
                        },
                        "max_regions": {
                            "type": "number",
                            "description": (
                                "仅 pixel 的 find 模式：最多返回多少块候选区域"
                                "（默认 10，上限 100）。"
                            ),
                        },
                        "output": {
                            "type": "string",
                            "description": (
                                "仅 annotate 可选：标注结果输出路径（PNG）。"
                                "省略时覆盖 path 指定的原图；无扩展名自动补 .png。"
                            ),
                        },
                        "boxes": {
                            "type": "array",
                            "items": {"type": ["string", "object", "array"]},
                            "description": (
                                "仅 annotate：矩形标注列表，每项为 "
                                "'x,y,width,height'、[x,y,w,h] 或 "
                                "{x,y,width,height}；坐标以图像左上角为原点。"
                            ),
                        },
                        "points": {
                            "type": "array",
                            "items": {"type": ["string", "object", "array"]},
                            "description": (
                                "仅 annotate：点标注列表，每项为 'x,y'、[x,y] 或 "
                                "{x,y}；绘制为十字 + 中心点。"
                            ),
                        },
                        "labels": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": (
                                "仅 annotate：与 boxes + points 顺序对应的标签文本"
                                "（5x7 点阵，常用数字 / 字母）；省略时自动编号。"
                            ),
                        },
                        "macro": {
                            "type": "string",
                            "description": (
                                "record / replay 的宏名（保存到 "
                                "'bash_opt_macros/<宏名>.json'）。replay 时按名加载；"
                                "record 时保存/追加到该名字。"
                            ),
                        },
                        "times": {
                            "type": "number",
                            "description": (
                                "仅 replay 可选：整段宏重复执行次数（默认 1，"
                                "上限 20）。"
                            ),
                        },
                        "append": {
                            "type": "boolean",
                            "description": (
                                "仅 record 可选：同名宏已存在时，把新步骤追加到"
                                "末尾（默认 false = 覆盖）。"
                            ),
                        },
                        "seconds": {
                            "type": "number",
                            "description": (
                                "仅 sequence 中 {\"op\": \"wait\"} 步骤使用："
                                "等待秒数（支持小数，最大 60）。"
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
            screen = arguments.get("screen")
            if screen:
                extra = f"{extra} screen={screen}" if extra else f"screen={screen}"
            element = arguments.get("element")
            if element:
                extra = f"{extra} element={element}" if extra else f"element={element}"
        elif op in INPUT_OPS:
            extra = cls._input_display(op, arguments)
            element = arguments.get("element")
            if element:
                extra = f"element={element}" if not extra else f"element={element} {extra}"
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
        elif op == "elements":
            selector = arguments.get("element")
            extra = f"筛选={selector}" if selector else "控件清单"
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}"
        elif op == "wait_window":
            window = arguments.get("window")
            extra = f"等待窗口 {window}" if window else "等待窗口 main"
        elif op == "locate":
            if arguments.get("template"):
                extra = f"图像 {arguments.get('template')}"
            else:
                extra = f"文字 {arguments.get('query') or arguments.get('text') or ''}"
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}"
        elif op == "clipboard":
            action = str(arguments.get("clipboard_action") or "")
            if not action:
                action = "set" if arguments.get("text") is not None else "get"
            extra = action
        elif op == "sequence":
            actions = arguments.get("actions")
            count = len(actions) if isinstance(actions, (list, tuple)) else 0
            extra = f"{count} 步"
            window = arguments.get("window")
            if window:
                extra = f"{extra} window={window}"
        elif op == "pixel":
            parts = [str(arguments.get("mode") or "point")]
            if arguments.get("x") is not None or arguments.get("y") is not None:
                parts.append(_format_position(arguments))
            region = arguments.get("region")
            if region:
                parts.append(f"region={region}")
            color = arguments.get("color")
            if color:
                parts.append(f"color={color}")
            extra = " ".join(parts)
        elif op == "annotate":
            boxes = arguments.get("boxes")
            points = arguments.get("points")
            box_count = len(boxes) if isinstance(boxes, (list, tuple)) else 0
            point_count = len(points) if isinstance(points, (list, tuple)) else 0
            extra = f"{box_count}框 {point_count}点"
            color = arguments.get("color")
            if color:
                extra = f"{extra} color={color}"
        elif op in ("record", "replay"):
            macro = arguments.get("macro") or arguments.get("path") or ""
            extra = str(macro)
            if op == "record":
                actions = arguments.get("actions")
                count = len(actions) if isinstance(actions, (list, tuple)) else 0
                extra = f"{extra} {count} 步" if extra else f"{count} 步"
            else:
                times = arguments.get("times")
                if times not in (None, 1, "1"):
                    extra = f"{extra} x{times}" if extra else f"x{times}"
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
            hold = arguments.get("hold")
            if hold not in (None, 0, "0", 0.0, ""):
                label += f" hold={hold}s"
            repeat = arguments.get("repeat")
            if repeat not in (None, 1, "1"):
                label += f" x{repeat}"
            return label
        if op == "release":
            keys = arguments.get("keys")
            buttons = arguments.get("buttons")
            parts = []
            if keys:
                parts.append("keys=" + ("/".join(keys) if isinstance(
                    keys, (list, tuple)) else str(keys)))
            if buttons:
                parts.append("buttons=" + ("/".join(buttons) if isinstance(
                    buttons, (list, tuple)) else str(buttons)))
            return " ".join(parts) or "全部"
        if op == "click":
            button = str(arguments.get("button") or "left")
            count = arguments.get("count")
            label = f"{button}"
            if count not in (None, 1, "1"):
                label += f"x{count}"
            phase = str(arguments.get("phase") or "press").strip().lower()
            if phase in ("down", "up"):
                label += f" {phase}"
            hold = arguments.get("hold")
            if hold not in (None, 0, "0", 0.0, ""):
                label += f" hold={hold}s"
            return f"{label} {_format_position(arguments)}"
        if op == "hover":
            dwell = arguments.get("dwell")
            suffix = f" dwell={dwell}s" if dwell not in (None, "") else ""
            return f"{_format_position(arguments)}{suffix}"
        if op == "move":
            if arguments.get("dx") is not None or arguments.get("dy") is not None:
                label = f"rel dx={arguments.get('dx') or 0} dy={arguments.get('dy') or 0}"
                if arguments.get("relative_event") in (True, "true", "1", 1):
                    label += " event"
            else:
                label = _format_position(arguments)
            steps = arguments.get("steps")
            duration = arguments.get("duration")
            if steps not in (None, 1, "1"):
                label += f" smooth={steps}"
            elif duration not in (None, 0, "0", 0.0, ""):
                label += f" smooth={duration}s"
            return label
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
                 dx=None, dy=None, dwell=None, hold=None, interval=None,
                 button: str | None = None, count=None, modifiers=None,
                 direction: str | None = None, amount=None,
                 duration=None, steps=None, method: str | None = None,
                 phase: str | None = None, repeat=None,
                 relative_event=None, hold_keys=None,
                 keys=None, buttons=None,
                 window: str | None = None, grid=None,
                 shot=None, settle=None,
                 window_action: str | None = None,
                 width=None, height=None,
                 element: str | None = None,
                 diff=None, tolerance=None,
                 wait_for=None, wait_timeout=None,
                 actions=None, on_error: str | None = None,
                 clipboard_action: str | None = None,
                 via: str | None = None, paste_key: str | None = None,
                 restore_clipboard=None, max_elements=None,
                 template: str | None = None, query: str | None = None,
                 max_results=None, min_scale=None, max_scale=None,
                 scale_steps=None,
                 screen=None, margin=None,
                 color=None, region=None, mode=None, output=None,
                 boxes=None, points=None, labels=None,
                 macro=None, times=None, append=None,
                 min_pixels=None, max_regions=None):
        super().__init__()
        # task_id 归一化（防御 None/缺失）：模型传 {"task_id": null} 时
        # from_args 把 None 传入（默认值不生效），后续 startswith 崩溃。
        self.task_id = task_id or ""
        self.op = op
        # timeout 仅对 wait / wait_window 生效：省略/None → 按 op 取默认；
        # <=0 → 无限等待。使用 float 保留小数（如 0.5 秒短超时）。
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
        # 鼠标相对移动偏移（仅 move）：相对当前光标屏幕位置平移
        self.dx = dx
        self.dy = dy
        # 悬停停留时长（仅 hover）
        self.dwell = dwell
        # 长按时长 / 多次点击间隔（仅 click）
        self.hold = hold
        self.interval = interval
        self.button = button
        self.count = count
        self.modifiers = modifiers
        self.direction = direction
        self.amount = amount
        self.duration = duration
        self.steps = steps
        self.method = method
        # 按键阶段（key / click 生效）：press=按下并弹起（默认）/ down=只按下 / up=只弹起
        self.phase = phase
        # 按键连按次数（仅 key / keys 生效，phase=press 时）：一次调用连按 N 次
        self.repeat = repeat
        # 鼠标相对位移事件（仅 move 的 dx/dy 生效）：不发绝对定位，只发相对位移，
        # 适合游戏视角控制（不受光标锁定影响）
        self.relative_event = relative_event
        # 动作期间额外按住的任意键（游戏组合键，如 ['w', 'shift']）
        self.hold_keys = hold_keys
        # op=release 的目标（留空 = 释放全部已按下的键 / 鼠标按钮）
        self.keys = keys
        self.buttons = buttons
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
        # ── 控件定位（输入 op 的 element 参数）──
        self.element = element
        # ── 结果确认（输入动作后比较截图 / 等待界面变化）──
        self.diff = diff                # true = 注入前后自动比较截图差异
        self.tolerance = tolerance      # 比较容差（每通道，缺省 _DIFF_TOLERANCE）
        self.wait_for = wait_for        # 'change' / 'stable' / 秒数
        self.wait_timeout = wait_timeout  # wait_for 超时（秒）
        # ── 动作序列（op=sequence）──
        self.actions = actions
        self.on_error = on_error        # stop / continue（缺省 stop）
        # ── 剪贴板（op=clipboard / type via='clipboard'）──
        self.clipboard_action = clipboard_action
        self.via = via
        self.paste_key = paste_key
        self.restore_clipboard = (None if restore_clipboard is None
                                  else bool(restore_clipboard))
        # ── 控件枚举（op=elements）──
        self.max_elements = max_elements
        # ── 图像 / 文字定位（op=locate）──
        self.template = template      # 模板图片路径（图像匹配）
        self.query = query            # OCR 查找文本
        self.max_results = max_results
        self.min_scale = min_scale    # 模板缩放搜索范围
        self.max_scale = max_scale
        self.scale_steps = scale_steps
        # ── 截图增强（op=screenshot / sequence 步骤）──
        self.screen = screen          # 全屏 / 多显示器截取（true / 'primary' / 序号）
        self.margin = margin          # 按控件区域截图时的外扩像素
        # ── 像素取色（op=pixel）──
        self.color = color            # 期望色 / 查找目标色（文本）
        self.region = region          # 取色 / 查找区域（'x,y,w,h'）
        self.mode = mode              # point / region / find
        self.min_pixels = min_pixels  # find 模式连通块最小像素数
        self.max_regions = max_regions  # find 模式返回块数上限
        # ── 截图标注（op=annotate）──
        self.output = output          # 标注结果输出路径
        self.boxes = boxes            # 矩形标记列表
        self.points = points          # 点标记列表
        self.labels = labels          # 标记标签
        # ── 操作宏（op=record / op=replay）──
        self.macro = macro            # 宏名（缺省存到 _MACRO_DIR）
        self.times = times            # replay 重复次数
        self.append = (None if append is None else bool(append))  # record 追加

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
        if self.op == "elements":
            return await self._op_elements(rec)
        if self.op == "wait_window":
            return await self._op_wait_window(rec)
        if self.op == "clipboard":
            return await self._op_clipboard(rec)
        if self.op == "locate":
            return await self._op_locate(rec)
        if self.op == "pixel":
            return await self._op_pixel(rec)
        if self.op == "annotate":
            return await self._op_annotate(rec)
        if self.op == "record":
            return await self._op_record(rec)
        if self.op == "replay":
            return await self._op_replay(rec)
        if self.op == "sequence":
            return await self._op_sequence(rec)
        if self.op in INPUT_OPS:
            return await self._op_input(rec)
        supported = "/".join(("read", "wait", "kill", "stdin", "keys",
                              "screenshot", "windows", "window",
                              "elements", "wait_window", "clipboard",
                              "locate", "pixel", "annotate", "record",
                              "replay", "sequence", *INPUT_OPS))
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
                # 同步重试 / 校验会短暂阻塞，移出事件循环线程执行
                result = await asyncio.to_thread(kill_process_tree, target_pid)
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
                pass  # 任务取消过程异常忽略

        # 3. 移除任务记录并更新 TUI 计数
        if hasattr(agent, "_remove_background_task"):
            agent._remove_background_task(self.task_id)
        else:
            agent._background_tasks.pop(self.task_id, None)
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
        attempts = int(getattr(result, "attempts", 0) or 0)
        if not getattr(result, "verified", False):
            return (f"(已杀死后台任务 {self.task_id} 及其所有进程树"
                    f"（{attempts} 轮尝试，未做杀后校验）)")
        if not remaining:
            return (f"(已杀死后台任务 {self.task_id} 及其所有进程树"
                    f"（{attempts} 轮尝试后校验：无残留进程）)")
        residual = ", ".join(str(p) for p in remaining)
        return (f"(已尝试杀死后台任务 {self.task_id} 的进程树（{attempts} 轮），"
                f"但校验时以下进程仍存活: {residual} —— 可能权限不足或处于"
                f"不可中断状态，可稍后重试 op=kill 补杀)")

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
        try:
            target_path = self._prepare_screenshot_path(str(self.path))
        except ValueError as exc:
            return f"(截图路径非法: {exc})"
        try:
            info = await self._screenshot_target(
                rec, target_path=target_path, crop=crop, grid=grid,
                window=self.window, screen=self.screen, element=self.element,
                margin=self.margin)
        except MonitorError as exc:
            return f"(截图失败: {exc})"
        except ElementError as exc:
            return f"(截图失败: 控件区域不可用: {exc})"
        except (SelectorError, ScreenshotError, CropError, ValueError) as exc:
            scope = f"（window 选择器 {self.window!r}）" if self.window else ""
            return f"(截图失败{scope}: {exc})"
        result = info["result"]
        region = info["region"]
        element_desc = info["element"]
        payload: dict = {"task_id": self.task_id, "op": "screenshot"}
        for key in ("screen", "monitor", "monitor_index", "monitors_total"):
            if info.get(key) is not None:
                payload[key] = info[key]
        payload.update(result.to_dict())
        notes = ["截图已保存"]
        if region is not None:
            payload["crop"] = region.to_dict()
            notes.append("已按 crop 裁剪")
        if element_desc is not None:
            payload["element"] = element_desc
            notes.append("截取范围为该控件区域")
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
            box_label = "显示器区域" if self._screen_requested() else "窗口外框"
            notes.append(
                f"{box_label} {rect['width']}x{rect['height']}"
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

    def _resolve_grid(self, raw=None) -> int | None:
        """解析 grid 参数为网格步长（省略 → None 不画；``0`` / true → 自动）。

        Args:
            raw: 显式取值（供 op=sequence 的步骤复用）；``None`` 时取本工具
                实例的 ``grid`` 参数。

        Raises:
            CropError: 取值不是数值。
        """
        if raw is None:
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

        Raises:
            MonitorError: 平台枚举不到显示器或选择越界。
        """
        monitors = await asyncio.wait_for(
            asyncio.to_thread(list_monitors), timeout=self._INPUT_TIMEOUT)
        value = self.screen if spec is None else spec
        monitor = resolve_monitor(
            monitors, value if not isinstance(value, bool) else None)
        index = 0
        for position, item in enumerate(monitors, start=1):
            if item == monitor:
                index = position
                break
        return monitor, index, len(monitors)

    async def _capture_screen_with_retry(self, monitor, path: str, crop,
                                         grid: int | None):
        """整屏 / 多显示器截图（在线程中执行并受超时保护）。"""
        kwargs: dict = {}
        if crop is not None:
            kwargs["crop"] = crop
        if grid is not None:
            kwargs["grid"] = grid
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

    async def _element_capture_region(self, pid: int, element: str, window,
                                      margin_raw, crop):
        """按控件名算出截图区域（可选 margin 外扩、crop 相对该控件再裁剪）。

        Returns:
            ``(CropRegion, 控件摘要)``。

        Raises:
            ElementError: 控件不可用或无法确定坐标系。
            CropError: 最终区域越界。
        """
        elements = await asyncio.wait_for(
            asyncio.to_thread(list_process_elements, pid, window),
            timeout=self._INPUT_TIMEOUT,
        )
        if not elements:
            raise ElementError("窗口内没有可枚举的控件（无法按控件区域截图）")
        matched = match_window_element(elements, element)
        frame = await self._window_frame_for(pid, window)
        if frame is None:
            raise ElementError("无法确定窗口坐标系（窗口可能已关闭）")
        margin = self._resolve_margin(margin_raw)
        x = matched.left - frame.screen_x - margin
        y = matched.top - frame.screen_y - margin
        width = matched.width + margin * 2
        height = matched.height + margin * 2
        x = max(0, x)
        y = max(0, y)
        width = min(width, frame.width - x)
        height = min(height, frame.height - y)
        if width <= 0 or height <= 0:
            raise ElementError("控件区域落在窗口外或尺寸非法，无法截图")
        if crop is not None:
            x += crop.x
            y += crop.y
            width, height = crop.width, crop.height
        region = CropRegion(x, y, width, height)
        region.validate_against(frame.width, frame.height)
        return region, self._element_summary(matched)

    def _resolve_margin(self, raw=_UNSET) -> int:
        """解析控件区域截图的外扩像素（可为负表示收缩，缺省 0）。"""
        value = self.margin if raw is _UNSET else raw
        if value is None:
            return 0
        if isinstance(value, bool):
            raise ValueError("margin 需要整数（像素）")
        try:
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"margin 需要整数（像素），当前: {value!r}") from None

    async def _screenshot_target(self, rec: dict, *, target_path: str, crop,
                                 grid, window, screen, element, margin):
        """执行一次截图（窗口 / 控件区域 / 整屏），返回结果与元信息。

        Returns:
            ``{result, region, element, screen?, monitor?, ...}``。

        Raises:
            ScreenshotError / SelectorError / ElementError / CropError: 截图失败。
        """
        if self._screen_spec_requested(screen):
            monitor, index, total = await self._resolve_monitor(screen)
            result = await self._capture_screen_with_retry(
                monitor, target_path, crop, grid)
            return {
                "result": result, "region": crop, "element": None,
                "screen": screen, "monitor": monitor.to_dict(),
                "monitor_index": index, "monitors_total": total,
            }
        pid = rec.get("pid")
        if pid is None:
            raise ScreenshotError(
                f"后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                f"无法截图；需要整屏截图可传 screen=true"
            )
        region = crop
        element_desc = None
        if element is not None and str(element).strip():
            region, element_desc = await self._element_capture_region(
                pid, element, window, margin, crop)
        result = await self._capture_with_retry(
            pid, target_path, region, window=window, grid=grid)
        return {"result": result, "region": region, "element": element_desc}

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
        指定秒数再返回；``shot`` 在注入后自动截图并把结果放进返回 JSON。

        增强能力（让「操作应用」更省事）：

          - ``element``：按控件名定位（先 op=elements 看清单）。click / move /
            scroll / drag 直接换成控件中心坐标；type / key 先点击该控件聚焦
            再输入，不必读图算像素；
          - ``via='clipboard'``（仅 type）：文本经系统剪贴板粘贴输入，
            长文本 / 中文 / emoji 不会掉字，也避免被当成快捷键误触发；
          - ``wait_for='change'|'stable'``：注入后轮询界面，等「画面变化」或
            「画面稳定」再返回（点击后界面还没刷新时不必猜等待时间）；
          - ``diff=true``：注入前后自动比较截图差异，回传是否变化、变化比例
            与变化区域，用于确认操作是否真的生效。

        结果返回 JSON（task_id/op 与动作细节如按钮、坐标、屏幕坐标、按键序列、
        投递方式；启用上述增强时附 ``element`` / ``wait_for`` / ``diff`` 字段）。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法注入输入。可用 op=wait 查看任务状态)")
        try:
            wait_mode, wait_timeout, extra_wait = self._resolve_wait_target()
            settle = self._resolve_settle()
            if extra_wait:
                settle = max(settle, extra_wait)
            diff_enabled = self._resolve_diff_flag()
            tolerance = self._resolve_tolerance()
            via = self._resolve_type_via()
            # 提前校验剪贴板恢复开关（非法取值在此给出可读提示，而不是把
            # 异常留到注入阶段变成内部错误）
            restore_clipboard = self._resolve_restore_clipboard()
        except ValueError as exc:
            return f"(输入参数非法: {exc})"
        # ── 控件定位（element）：换算中心坐标 / 作为聚焦点击 ──
        element_desc = None
        element_point = None
        if self.element is not None and str(self.element).strip():
            if self._has_explicit_point():
                return ("(输入参数非法: element 与坐标参数不能同时提供——"
                        "element 会自动使用控件中心坐标；"
                        "要精确点某处请去掉 element 直接给 x/y)")
            try:
                element_point, element_info = await self._element_target(
                    pid, self.element, self.window)
            except (ElementError, SelectorError) as exc:
                return f"(控件定位失败: {exc})"
            element_desc = self._element_summary(element_info)
        try:
            action = self._build_input_action(
                overrides=self._element_overrides(element_point))
        except ActionError as exc:
            return f"(输入参数非法: {exc})"
        focus_click = self._element_focus_click(action, element_point)
        # ── 变化判定 / 差异比较需要基准图：注入前先截一张 ──
        temporaries: list[str] = []
        before_path = None
        if diff_enabled or wait_mode is not None:
            before_path = await self._temp_screenshot(pid, self.window)
            if before_path is not None:
                temporaries.append(before_path)
        try:
            if focus_click is not None:
                await self._send_input_with_retry(pid, focus_click)
            detail = await self._inject_input_action(
                pid, action, via, restore_clipboard=restore_clipboard)
        except (InputNoWindowError, InputError) as exc:
            self._cleanup_temps(temporaries)
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
        if element_desc is not None:
            payload["element"] = element_desc
        payload.update(detail)
        try:
            if settle:
                await asyncio.sleep(settle)
            if wait_mode is not None:
                payload["wait_for"] = await self._await_screen(
                    pid, mode=wait_mode, before_path=before_path,
                    window=self.window, timeout=wait_timeout)
            if diff_enabled:
                payload["diff"] = await self._diff_result(
                    pid, before_path, window=self.window, tolerance=tolerance)
            shot_error = await self._attach_shot(payload, rec)
            if shot_error:
                payload["screenshot_error"] = shot_error
        finally:
            self._cleanup_temps(temporaries)
        return json.dumps(payload, ensure_ascii=False)

    # ── 输入 op 的增强辅助（控件 / 剪贴板 / 变化判定） ────

    def _has_explicit_point(self) -> bool:
        """输入动作是否显式给了坐标（与 element 互斥）。"""
        if self.op in ("click", "move", "hover", "scroll"):
            return (self.x is not None or self.y is not None
                    or self.dx is not None or self.dy is not None)
        if self.op == "drag":
            return self.from_x is not None or self.from_y is not None
        return False

    def _element_overrides(self, point: tuple[int, int] | None) -> dict:
        """把控件中心坐标转成动作坐标覆盖（click/move/scroll/drag 用）。"""
        if point is None:
            return {}
        if self.op == "drag":
            return {"from_x": point[0], "from_y": point[1]}
        if self.op in ("click", "move", "hover", "scroll"):
            return {"x": point[0], "y": point[1]}
        return {}

    def _element_focus_click(self, action, point: tuple[int, int] | None):
        """type / key 配合 element 时，先生成的「点击控件聚焦」动作。"""
        if point is None or action.name not in ("key", "type"):
            return None
        return build_action("click", {
            "x": point[0], "y": point[1], "window": self.window or "",
        })

    async def _element_target(self, pid: int, element: str,
                              window: str | None) -> tuple[tuple[int, int], ElementInfo]:
        """按控件名定位控件，返回 ``((窗口内 x, y), 控件描述)``。

        Raises:
            ElementError: 没有可枚举控件 / 没有匹配控件 / 无法确定坐标系。
            SelectorError: 窗口选择器非法或没有匹配窗口。
        """
        elements = await asyncio.wait_for(
            asyncio.to_thread(list_process_elements, pid, window),
            timeout=self._INPUT_TIMEOUT,
        )
        if not elements:
            raise ElementError(
                "窗口内没有可枚举的控件（Windows 优先用 UI Automation 枚举，"
                "Chrome / Electron / Qt / WPF / UWP 等自绘界面通常也能枚举到；"
                "游戏 / 纯 OpenGL / 自绘 canvas 等仍可能不暴露内部元素）——"
                "请改用 op=screenshot 截图 + read_image 读图后用像素坐标操作，"
                "或用 op=locate 做图像 / 文字定位"
            )
        matched = match_window_element(elements, element)
        frame = await self._window_frame_for(pid, window)
        if frame is None:
            raise ElementError(
                "无法确定窗口坐标系（窗口可能已关闭），不能把控件坐标换算成输入坐标"
            )
        return ((matched.center_x - frame.screen_x,
                 matched.center_y - frame.screen_y), matched)

    @staticmethod
    def _element_summary(item: ElementInfo) -> dict:
        """控件定位结果的一行摘要（结果回显用）。"""
        return {
            "handle_hex": item.handle_hex,
            "class": item.class_name,
            "text": item.text,
            "label": item.label,
            "automation_id": item.automation_id,
            "type": item.control_type,
            "enabled": item.enabled,
            "visible": item.visible,
        }

    async def _window_frame_for(self, pid: int, window: str | None):
        """返回被选窗口的截图坐标系（与 op=screenshot 产物一致）。

        取不到时返回 ``None``（不抛错）：调用方据此给出可读提示，而不是让
        整个操作失败。
        """
        backend = resolve_input_backend()
        locate = getattr(backend, "locate", None)
        if locate is None:
            return None
        try:
            target = await asyncio.wait_for(
                asyncio.to_thread(locate, pid, window),
                timeout=self._INPUT_TIMEOUT,
            )
        except (SelectorError, ScreenshotError, OSError, ValueError):
            return None
        return getattr(target, "frame", None)

    async def _inject_input_action(self, pid: int, action, via: str, *,
                                   paste_key=_UNSET,
                                   restore_clipboard=_UNSET) -> dict:
        """执行一次输入注入（type 且 via='clipboard' 时改走剪贴板粘贴）。

        Args:
            pid: 目标进程 PID。
            action: 输入动作。
            via: ``typing`` 逐字符注入 / ``clipboard`` 剪贴板粘贴。
            paste_key: 仅剪贴板粘贴用（``_UNSET`` = 取实例参数）；供
                ``op=sequence`` 的步骤级参数透传。
            restore_clipboard: 仅剪贴板粘贴用（``_UNSET`` = 取实例参数）。
        """
        if via == "clipboard" and action.name == "type":
            return await self._paste_text(pid, action, paste_key=paste_key,
                                          restore_clipboard=restore_clipboard)
        result = await self._send_input_with_retry(pid, action)
        return result.to_dict()

    async def _paste_text(self, pid: int, action, *, paste_key=_UNSET,
                          restore_clipboard=_UNSET) -> dict:
        """把文本放入系统剪贴板后发送粘贴键（type via='clipboard'）。

        逐字符合成按键对长文本 / 中文 / emoji 既慢又容易被目标程序丢字或
        误判为快捷键；剪贴板粘贴是 GUI 应用最可靠的文本输入方式。

        恢复原剪贴板之前会等待 :data:`_CLIPBOARD_RESTORE_DELAY` 秒：浏览器 /
        Electron / 远程桌面等目标的「粘贴」是异步消息处理（收到粘贴键后才去
        读剪贴板），恢复太快会让它们读到**旧内容**（表现为「粘出来的还是
        上一次的剪贴板文本」）。确实不需要恢复时传 ``restore_clipboard=False``
        （既避免时序问题，也少一次剪贴板读写）。

        Args:
            pid: 目标进程 PID。
            action: ``type`` 动作（``action.text`` 为待粘贴文本）。
            paste_key: 粘贴组合键（``_UNSET`` = 取本工具实例的 ``paste_key``，
                再缺省按平台取 ctrl+v / command+v）。
            restore_clipboard: 粘贴后是否恢复原剪贴板（``_UNSET`` = 取实例参数，
                缺省 True）。

        Raises:
            InputError: 剪贴板不可用或粘贴键注入失败。
        """
        key_text = self._resolve_paste_key(paste_key)
        restore = self._resolve_restore_clipboard(restore_clipboard)
        original = None
        if restore:
            try:
                original = await asyncio.to_thread(read_clipboard_text)
            except ClipboardError:
                logger.debug("读取原剪贴板失败，跳过恢复", exc_info=True)
        try:
            await asyncio.to_thread(write_clipboard_text, action.text)
        except ClipboardError as exc:
            raise InputError(f"剪贴板写入失败（无法粘贴输入）: {exc}") from exc
        try:
            key_action = build_action("key", {
                "key": key_text, "window": getattr(action, "window", "") or "",
            })
            result = await self._send_input_with_retry(pid, key_action)
            if restore and original is not None:
                # 目标程序此刻才去读剪贴板：留出读取窗口再恢复原内容
                await asyncio.sleep(self._CLIPBOARD_RESTORE_DELAY)
        finally:
            if restore and original is not None:
                try:
                    await asyncio.to_thread(write_clipboard_text, original)
                except ClipboardError:
                    logger.debug("恢复剪贴板内容失败", exc_info=True)
        detail = dict(result.to_dict())
        restored = bool(restore and original is not None)
        detail.update({
            "via": "clipboard",
            "paste_key": key_text,
            "pasted_characters": len(action.text),
            "clipboard_restored": restored,
        })
        if restored:
            detail["clipboard_restore_delay"] = self._CLIPBOARD_RESTORE_DELAY
        return detail

    def _resolve_paste_key(self, raw=_UNSET) -> str:
        """粘贴组合键：显式参数优先，其次 paste_key 参数，缺省按平台取。

        Args:
            raw: 显式取值（``op=sequence`` 的步骤复用）；``_UNSET`` 时取本工具
                实例的 ``paste_key`` 参数。
        """
        value = self.paste_key if raw is _UNSET else raw
        if value is not None and str(value).strip():
            return str(value).strip()
        backend = resolve_input_backend()
        name = str(getattr(backend, "name", "") or "")
        return self._PASTE_KEYS.get(name, self._DEFAULT_PASTE_KEY)

    def _resolve_restore_clipboard(self, raw=_UNSET) -> bool:
        """是否在粘贴后恢复原剪贴板内容（缺省 True；``_UNSET`` = 取实例参数）。

        Raises:
            ValueError: 取值无法识别为布尔。
        """
        value = self.restore_clipboard if raw is _UNSET else raw
        return self._parse_bool(value, default=True, label="restore_clipboard")

    async def _temp_screenshot(self, pid: int, window: str | None) -> str | None:
        """截一张临时图（变化判定 / 差异比较用），失败返回 None。"""
        path = os.path.join(
            tempfile.gettempdir(),
            f"bash_opt-{self.task_id or 'task'}-{time.monotonic_ns()}.png",
        )
        try:
            await self._capture_with_retry(pid, path, None, window=window)
        except (ScreenshotError, SelectorError, OSError):
            self._remove_temp(path)
            return None
        return path

    @staticmethod
    def _remove_temp(path: str | None) -> None:
        """删除临时文件（不存在 / 删除失败都忽略）。"""
        if not path:
            return
        try:
            os.remove(path)
        except OSError:
            pass

    def _cleanup_temps(self, paths) -> None:
        for path in list(paths):
            self._remove_temp(path)

    async def _await_screen(self, pid: int, *, mode: str,
                            before_path: str | None, window: str | None,
                            timeout: float) -> dict:
        """等待界面变化（``change``）或稳定（``stable``）。

        ``change``：把每一轮采样与注入前的基准图比较，出现差异即满足；
        ``stable``：连续两次采样一致即满足（动画 / 加载结束）；变化像素占比
        不超过 :data:`_STABLE_CHANGE_RATIO` 的**微小噪声**（输入光标闪烁、
        时钟秒数）同样视为稳定，并在结果的 ``ignored_change`` 里说明忽略了
        多少像素——否则带光标的界面永远等不到「逐像素一致」。

        Returns:
            ``{mode, satisfied, waited, samples}``；无法判定时 ``satisfied``
            为 ``None`` 并附 ``reason``（如截图失败、缺少基准图）。
        """
        started = time.monotonic()
        deadline = started + max(float(timeout), 0.0)
        samples = 0
        previous = before_path
        interval = self._WAIT_FOR_INTERVAL
        while True:
            samples += 1
            current = await self._temp_screenshot(pid, window)
            if current is None:
                if time.monotonic() >= deadline:
                    return self._wait_result(
                        mode, None, started, samples,
                        "无法截图（窗口可能已关闭或无响应）")
                await asyncio.sleep(interval)
                interval = min(interval * self._WAIT_FOR_BACKOFF,
                               self._WAIT_FOR_MAX_INTERVAL)
                continue
            keep = False
            try:
                reference = before_path if mode == "change" else previous
                if reference is None:
                    if mode == "change":
                        if before_path is None:
                            return self._wait_result(
                                mode, None, started, samples,
                                "没有注入前的基准截图，无法判定界面是否变化")
                        return self._wait_result(
                            mode, None, started, samples, "缺少比较基准")
                    previous = current
                    keep = True
                else:
                    diff = await asyncio.to_thread(
                        compare_png_files, reference, current)
                    if mode == "change":
                        if diff.changed:
                            return self._wait_result(
                                mode, True, started, samples, None,
                                diff.to_dict())
                    elif self._is_stable(diff):
                        # 连续两次采样一致（或只剩光标闪烁级别的噪声）→ 稳定
                        return self._wait_result(
                            mode, True, started, samples,
                            ignored=self._stability_note(diff))
                    else:
                        previous = current
                        keep = True
            except (ScreenshotError, OSError, ValueError) as exc:
                logger.debug("等待界面变化时比较失败: %s", exc)
            finally:
                if not keep:
                    self._remove_temp(current)
            if time.monotonic() >= deadline:
                verb = "变化" if mode == "change" else "稳定"
                return self._wait_result(
                    mode, False, started, samples,
                    f"超时：界面在 {timeout:g} 秒内没有{verb}")
            await asyncio.sleep(interval)
            interval = min(interval * self._WAIT_FOR_BACKOFF,
                           self._WAIT_FOR_MAX_INTERVAL)

    def _is_stable(self, diff) -> bool:
        """画面是否可判为「已稳定」（允许光标闪烁 / 时钟之类的微小噪声）。

        Args:
            diff: 本轮与上一轮的差异（:class:`~._screenshot.diff.DiffResult`）。
        """
        if not diff.changed:
            return True
        if diff.size_changed:
            return False
        return diff.changed_ratio <= self._STABLE_CHANGE_RATIO

    @classmethod
    def _stability_note(cls, diff) -> str | None:
        """稳定判定的补充说明（忽略微小变化时给出，便于解释 satisfied=True）。"""
        if not diff.changed:
            return None
        return (f"忽略了 {diff.changed_pixels} 像素（{diff.changed_ratio * 100:.3f}%）"
                f"的微小变化（光标闪烁 / 时钟等噪声），区域 {diff.region}")

    @staticmethod
    def _wait_result(mode: str, satisfied, started: float, samples: int,
                     reason: str | None = None, change: dict | None = None,
                     ignored: str | None = None) -> dict:
        """组装 ``wait_for`` 的结果字典。"""
        payload = {
            "mode": mode,
            "satisfied": satisfied,
            "waited": round(time.monotonic() - started, 3),
            "samples": samples,
        }
        if change is not None:
            payload["change"] = change
        if ignored:
            payload["ignored_change"] = ignored
        if reason:
            payload["reason"] = reason
        return payload

    async def _diff_result(self, pid: int, before_path: str | None, *,
                           window: str | None, tolerance: int) -> dict:
        """注入前后截图比较（``diff=true``）：回传是否变化与变化区域。"""
        if before_path is None:
            return {
                "changed": None,
                "reason": "无法截取注入前的基准图（窗口可能尚未就绪）",
            }
        after_path = await self._temp_screenshot(pid, window)
        if after_path is None:
            return {
                "changed": None,
                "reason": "无法截取注入后的截图（窗口可能已关闭）",
            }
        try:
            diff = await asyncio.to_thread(
                compare_png_files, before_path, after_path, tolerance=tolerance)
        except (ScreenshotError, OSError, ValueError) as exc:
            return {"changed": None, "reason": f"截图比较失败: {exc}"}
        finally:
            self._remove_temp(after_path)
        payload = diff.to_dict()
        payload["summary"] = diff.summary()
        return payload

    def _resolve_wait_target(self, raw=_UNSET, timeout_raw=_UNSET
                             ) -> tuple[str | None, float, float]:
        """解析 ``wait_for`` / ``wait_timeout``。

        Args:
            raw: 显式 ``wait_for`` 取值（op=sequence 的步骤复用）；``_UNSET``
                时取本工具实例的 ``wait_for``。
            timeout_raw: 显式 ``wait_timeout`` 取值；``_UNSET`` 时取实例值。

        Returns:
            ``(模式或 None, 超时秒数, 额外等待秒数)``：数值形式（如 ``0.5``）
            视为「额外等待秒数」（等价增强版 settle），此时模式为 ``None``。

        Raises:
            ValueError: 取值非法。
        """
        wait_raw = self.wait_for if raw is _UNSET else raw
        timeout = self._parse_wait_timeout(
            self.wait_timeout if timeout_raw is _UNSET else timeout_raw)
        if wait_raw is None:
            return None, timeout, 0.0
        if isinstance(wait_raw, bool):
            if not wait_raw:
                return None, timeout, 0.0
            return "change", timeout, 0.0
        text = str(wait_raw).strip().lower()
        if not text:
            return None, timeout, 0.0
        if text in ("change", "changed", "diff", "update", "updated"):
            return "change", timeout, 0.0
        if text in ("stable", "settle", "static", "idle"):
            return "stable", timeout, 0.0
        try:
            seconds = float(text)
        except ValueError:
            raise ValueError(
                f"wait_for 取值非法: {wait_raw!r}。支持 'change'（等待界面变化）、"
                f"'stable'（等待界面稳定）或秒数（额外等待，如 0.5）"
            ) from None
        if seconds < 0:
            raise ValueError(f"wait_for 秒数不能为负，当前: {seconds}")
        return None, timeout, min(seconds, self._MAX_SETTLE_SECONDS)

    def _resolve_wait_timeout(self) -> float:
        """解析 ``wait_timeout``（等待界面变化 / 稳定的超时秒数）。"""
        return self._parse_wait_timeout(self.wait_timeout)

    @staticmethod
    def _parse_wait_timeout(raw) -> float:
        """解析等待界面变化的超时（秒，上限为 settle 上限的 4 倍）。

        Raises:
            ValueError: 取值非法或为负。
        """
        if raw is None:
            return BashOptFunc._WAIT_FOR_TIMEOUT
        if isinstance(raw, bool):
            raise ValueError("wait_timeout 需要数值（秒）")
        try:
            value = float(raw)
        except (TypeError, ValueError):
            raise ValueError(f"wait_timeout 需要数值（秒），当前: {raw!r}") from None
        if math.isnan(value) or value < 0:
            raise ValueError(f"wait_timeout 不能为负，当前: {raw!r}")
        return min(value, BashOptFunc._MAX_SETTLE_SECONDS * 4)

    def _resolve_diff_flag(self) -> bool:
        """解析 ``diff``（是否比较注入前后截图差异）。"""
        return self._parse_diff(self.diff)

    @staticmethod
    def _parse_diff(raw) -> bool:
        """解析布尔型开关（``diff``），接受 true/false 与 1/0 等写法。

        Raises:
            ValueError: 取值无法识别。
        """
        if raw is None or raw is False:
            return False
        if raw is True:
            return True
        text = str(raw).strip().lower()
        if text in ("", "false", "0", "no", "off"):
            return False
        if text in ("true", "1", "yes", "on", "auto"):
            return True
        raise ValueError(f"diff 需要布尔值（true/false），当前: {raw!r}")

    @staticmethod
    def _parse_bool(raw, *, default: bool, label: str) -> bool:
        """解析通用布尔参数（缺省 / 空值 → ``default``）。

        与 ``_parse_diff`` 同源但错误文案按参数名生成，供 ``restore_clipboard``
        / ``newline`` 这类需要「显式区分未传与 false」的开关使用。

        Raises:
            ValueError: 取值无法识别为布尔。
        """
        if raw is None:
            return default
        if isinstance(raw, bool):
            return raw
        text = str(raw).strip().lower()
        if text in ("", "default", "auto"):
            return default
        if text in ("true", "1", "yes", "on"):
            return True
        if text in ("false", "0", "no", "off"):
            return False
        raise ValueError(f"{label} 需要布尔值（true/false），当前: {raw!r}")

    def _resolve_tolerance(self) -> int:
        """解析 ``tolerance``（截图比较的颜色容差，每通道 0..255）。"""
        return self._parse_tolerance(self.tolerance)

    @staticmethod
    def _parse_tolerance(raw) -> int:
        """解析截图比较容差（0..255，缺省 ``_DIFF_TOLERANCE``）。

        Raises:
            ValueError: 取值非法或越界。
        """
        if raw is None:
            return BashOptFunc._DIFF_TOLERANCE
        if isinstance(raw, bool):
            raise ValueError("tolerance 需要整数（0..255）")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"tolerance 需要整数（0..255），当前: {raw!r}") from None
        if not 0 <= value <= 255:
            raise ValueError(f"tolerance 需在 0..255 之间，当前: {value}")
        return value

    def _resolve_type_via(self, raw=_UNSET) -> str:
        """解析 type 的 ``via``（``typing`` 逐字符 / ``clipboard`` 粘贴）。

        Args:
            raw: 显式取值（op=sequence 的步骤复用）；``_UNSET`` 时取实例值。
        """
        value = self.via if raw is _UNSET else raw
        if value is None:
            return "typing"
        text = str(value).strip().lower()
        if not text:
            return "typing"
        aliases = {
            "typing": "typing", "type": "typing", "keys": "typing",
            "clipboard": "clipboard", "paste": "clipboard", "clip": "clipboard",
        }
        resolved = aliases.get(text)
        if resolved is None:
            raise ValueError(
                f"via 取值非法: {value!r}。支持 typing（逐字符注入）或 "
                f"clipboard（经系统剪贴板粘贴）"
            )
        return resolved


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

    def _shot_path(self, shot=None) -> str:
        """解析 shot 参数为截图路径（``true`` 等占位值 → 自动命名）。

        Args:
            shot: 显式取值（供 op=sequence 的步骤复用）；``None`` 时取本工具
                实例的 ``shot`` 参数。
        """
        raw = self.shot if shot is None else shot
        auto = raw is True
        if not auto and isinstance(raw, str) and raw.strip().lower() in (
                "true", "auto", "yes", "on"):
            auto = True
        if auto:
            stamp = time.strftime("%H%M%S")
            return os.path.join(self._SHOT_AUTO_DIR, f"{self.task_id}-{stamp}.png")
        return str(raw or "").strip()

    async def _attach_shot(self, payload: dict, rec: dict, *,
                           shot=_UNSET, window=_UNSET,
                           grid=_UNSET) -> str | None:
        """输入 op 的 shot 参数：注入后自动截图，结果写入 ``payload["screenshot"]``。

        ``shot`` / ``window`` / ``grid`` 可显式传入（供 op=sequence 的步骤复用），
        省略时取本工具实例的对应参数。

        Returns:
            失败原因（成功或未启用时返回 None）；截图失败不影响注入结果。
        """
        shot_value = self.shot if shot is _UNSET else shot
        if not shot_value:
            return None
        window_value = self.window if window is _UNSET else window
        grid_value = self.grid if grid is _UNSET else grid
        path = self._shot_path(shot_value)
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
            grid_step = self._resolve_grid(grid_value)
        except CropError as exc:
            return f"截图网格参数非法: {exc}"
        try:
            result = await self._capture_with_retry(
                pid, target_path, None, window=window_value, grid=grid_step)
        except (SelectorError, ScreenshotError) as exc:
            scope = f"（window 选择器 {window_value!r}）" if window_value else ""
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
                               "'title~:正则'、'class~:正则'、're:正则'、"
                               "'process:进程名'、'fuzzy:关键词'、"
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
        """控制被选窗口的状态与几何。

        除 ``activate`` / ``maximize`` / ``minimize`` / ``restore`` / ``close`` /
        ``move`` / ``resize`` / ``fit`` 外，还支持：

          - ``always_on_top`` / ``not_on_top``：置顶 / 取消置顶（操作 GUI 应用
            时避免被别的窗口遮挡，尤其防止「截图 → 定位 → 点击」之间被抢前台）；
          - ``get_geometry`` / ``save_geometry`` / ``restore_geometry``：读取 /
            记住 / 恢复窗口几何（把布局固定下来后再按像素操作，减少坐标漂移）。

        配合 ``window`` 选择器指定目标窗口；``move`` / ``fit`` 用 ``x`` / ``y``
        （屏幕坐标），``resize`` / ``fit`` 用 ``width`` / ``height``。返回动作
        前后的窗口状态，便于确认结果。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法控制窗口。可用 op=wait 查看任务状态)")
        try:
            self._validate_window_request(
                self.window_action, window=self.window, x=self.x, y=self.y,
                width=self.width, height=self.height,
            )
        except SelectorError as exc:
            return f"(窗口控制参数非法: {exc})"
        try:
            detail = await self._apply_window_action(
                rec, self.window_action, window=self.window,
                x=self.x, y=self.y, width=self.width, height=self.height,
            )
        except SelectorError as exc:
            return f"(窗口控制失败: {exc})"
        except ScreenshotError as exc:
            return f"(窗口控制失败: {exc})"
        payload = {
            "task_id": self.task_id,
            "op": "window",
            "hint": ("窗口状态已变更；可用 op=screenshot（可带 window 选择器）"
                     "截图核对，或继续用输入 op 操作"),
        }
        payload.update(detail)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _validate_window_request(window_action, *, window=None, x=None, y=None,
                                 width=None, height=None) -> str:
        """只校验窗口动作与几何参数（不执行）。

        Returns:
            规范化后的动作名。

        Raises:
            SelectorError: 动作未知或几何参数非法。
        """
        action = normalize_control_action(window_action)
        if action is None:
            supported = "、".join((*WINDOW_CONTROL_ACTIONS, *WINDOW_MEMORY_ACTIONS))
            raise SelectorError(
                f"窗口控制动作非法: {window_action!r}。支持: {supported}"
            )
        if action in WINDOW_MEMORY_ACTIONS:
            return action
        parse_control_request(action, window=window, x=x, y=y,
                              width=width, height=height)
        return action

    async def _apply_window_action(self, rec: dict, window_action, *,
                                   window=None, x=None, y=None,
                                   width=None, height=None) -> dict:
        """执行一次窗口控制（含工具层扩展的几何记忆动作）。

        Raises:
            SelectorError: 动作或几何参数非法。
            ScreenshotError: 无进程句柄、平台不支持、无匹配窗口或调用失败。
        """
        pid = rec.get("pid")
        if pid is None:
            raise ScreenshotError(
                f"后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                f"无法控制窗口"
            )
        action = normalize_control_action(window_action)
        if action is None:
            supported = "、".join((*WINDOW_CONTROL_ACTIONS, *WINDOW_MEMORY_ACTIONS))
            raise SelectorError(
                f"窗口控制动作非法: {window_action!r}。支持: {supported}"
            )
        if action in WINDOW_MEMORY_ACTIONS:
            return await self._apply_geometry_action(rec, action, window=window)
        request = parse_control_request(
            action, window=window, x=x, y=y, width=width, height=height)
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(control_process_window, pid, request),
                timeout=self._INPUT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ScreenshotError(
                f"窗口控制超时（超过 {self._INPUT_TIMEOUT:g} 秒）：窗口无响应"
            ) from None

    async def _apply_geometry_action(self, rec: dict, action: str, *,
                                     window=None) -> dict:
        """窗口几何记忆动作：读取 / 记住 / 恢复（``get/save/restore_geometry``）。

        几何保存在任务记录里（``rec["saved_geometry"]``），因此跨多次工具调用
        依然有效：先 ``save_geometry`` 固定布局，做完整套操作后再
        ``restore_geometry`` 还原。
        """
        if action == "get_geometry":
            rect = await self._current_window_rect(rec, window)
            return {"window_action": action, "geometry": self._geometry_payload(rect)}
        if action == "save_geometry":
            rect = await self._current_window_rect(rec, window)
            saved = {"window": window or "main", **self._geometry_payload(rect)}
            rec["saved_geometry"] = saved
            return {"window_action": action, "saved": saved,
                    "geometry": self._geometry_payload(rect)}
        saved = rec.get("saved_geometry")
        if not saved:
            raise ScreenshotError(
                "尚未保存窗口几何：请先执行 op=window, "
                "window_action='save_geometry' 记住当前布局"
            )
        rect = saved.get("rect") or {}
        try:
            request = parse_control_request(
                "fit", window=window or saved.get("window") or "main",
                x=rect.get("x"), y=rect.get("y"),
                width=rect.get("width"), height=rect.get("height"),
            )
        except SelectorError as exc:
            raise ScreenshotError(f"保存的窗口几何非法，无法恢复: {exc}") from exc
        pid = rec.get("pid")
        try:
            detail = await asyncio.wait_for(
                asyncio.to_thread(control_process_window, pid, request),
                timeout=self._INPUT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ScreenshotError(
                f"恢复窗口几何超时（超过 {self._INPUT_TIMEOUT:g} 秒）"
            ) from None
        detail["window_action"] = action
        detail["restored"] = saved
        return detail

    async def _current_window_rect(self, rec: dict, window) -> dict:
        """读取被选窗口的屏幕矩形（含句柄 / 标题，供几何记忆使用）。"""
        pid = rec.get("pid")
        try:
            infos = await asyncio.wait_for(
                asyncio.to_thread(list_process_windows, pid),
                timeout=self._INPUT_TIMEOUT,
            )
        except asyncio.TimeoutError:
            raise ScreenshotError(
                f"枚举窗口超时（超过 {self._INPUT_TIMEOUT:g} 秒）：系统无响应"
            ) from None
        if not infos:
            raise ScreenshotError(
                f"进程 {pid} 及其子进程没有可操作窗口（纯命令行进程没有 GUI 窗口）"
            )
        target = pick_window(infos, window)
        geometry = window_geometry(infos, target)
        rect = dict(geometry["rect"])
        rect["handle"] = target.handle
        rect["handle_hex"] = target.handle_hex
        rect["title"] = target.title
        rect["window_summary"] = indexed_summary(infos, target)
        return rect

    @staticmethod
    def _geometry_payload(rect: dict) -> dict:
        """把窗口几何整理为统一结构（截图坐标系矩形 + 句柄 / 标题）。"""
        return {
            "rect": {key: rect.get(key) for key in ("x", "y", "width", "height")},
            "handle_hex": rect.get("handle_hex"),
            "title": rect.get("title", ""),
            "window_summary": rect.get("window_summary", ""),
        }

    def _build_input_action(self, overrides: dict | None = None):
        """把工具参数打包为输入动作（type 的 newline 语义在此落地）。

        Args:
            overrides: 覆盖参数字典（如按控件中心坐标覆盖 x/y 或
                from_x/from_y——见 :meth:`_element_overrides`）。
        """
        text = self.text
        if self.op == "type" and self.newline:
            text = (text or "") + "\n"
        params = {
            "x": self.x, "y": self.y,
            "to_x": self.to_x, "to_y": self.to_y,
            "from_x": self.from_x, "from_y": self.from_y,
            "dx": self.dx, "dy": self.dy,
            "dwell": self.dwell, "hold": self.hold, "interval": self.interval,
            "button": self.button, "count": self.count,
            "modifiers": self.modifiers, "key": self.key, "text": text,
            "direction": self.direction, "amount": self.amount,
            "duration": self.duration, "steps": self.steps,
            "method": self.method, "phase": self.phase,
            "repeat": self.repeat,
            "relative_event": self.relative_event,
            "hold_keys": self.hold_keys,
            "keys": self.keys, "buttons": self.buttons,
            "window": self.window,
        }
        if overrides:
            params.update(overrides)
        return build_action(self.op, params)

    # ── op=elements（控件清单） ──────────────────────────

    async def _op_elements(self, rec: dict) -> str:
        """列出被选窗口内的控件（名称 / 类型 / 类名 / 矩形 / 可用状态）。

        返回 JSON：``total``（枚举到的控件总数）、``matched``（按 ``element``
        过滤后的条数）与 ``elements`` 清单。清单里每个控件都带**屏幕坐标**
        （``x`` / ``y`` / ``center_x`` / ``center_y``）与**窗口内坐标**
        （``window_center_x`` / ``window_center_y``，与 ``op=screenshot`` 产物同源）；
        后者可直接交给 ``click`` 的 ``x`` / ``y``，也可直接给输入 op 传
        ``element='控件名'`` 由工具内部换算。``element`` 过滤与输入 op 的定位
        共用同一套写法：``'#N'``（清单第 N 个）、``'text:子串'``、
        ``'class:子串'``、``'type:edit'`` 与中文标签（``'编辑框'`` / ``'按钮'``）。

        Windows 优先走 **UI Automation**：Chrome / Electron / Qt / WPF / UWP
        等自绘界面通常也能枚举到无障碍节点；UIA 不可用时回退经典 Win32 子窗口
        枚举。仍枚举不到时（游戏 / 纯 OpenGL / 自绘 canvas）可改用 ``op=locate``
        做图像 / 文字定位。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法枚举控件。可用 op=wait 查看任务状态)")
        try:
            limit = self._resolve_element_limit()
        except ValueError as exc:
            return f"(elements 参数非法: {exc})"
        try:
            elements = await asyncio.wait_for(
                asyncio.to_thread(list_process_elements, pid, self.window),
                timeout=self._INPUT_TIMEOUT,
            )
        except SelectorError as exc:
            return (f"(枚举控件失败: {exc}。可先用 op=windows 查看窗口清单，"
                    f"再用 window 选择器指定目标窗口)")
        except ElementError as exc:
            return f"(枚举控件失败: {exc})"
        except asyncio.TimeoutError:
            return (f"(枚举控件超时（超过 {self._INPUT_TIMEOUT:g} 秒）：系统无响应")
        if not elements:
            payload = {
                "task_id": self.task_id,
                "op": "elements",
                "total": 0,
                "matched": 0,
                "elements": [],
                "hint": ("未枚举到控件：该窗口可能不暴露无障碍 / 子控件节点"
                         "（游戏 / 纯 OpenGL / 自绘界面 canvas），或窗口尚未就绪。"
                         "此时可用 op=locate 做图像 / 文字定位，或回到"
                         "「op=screenshot 截图 + read_image 读图 + 像素坐标操作」"
                         "的方式；也可用 op=windows 确认窗口是否存在"),
            }
            return json.dumps(payload, ensure_ascii=False)
        filtered = (filter_elements(elements, self.element)
                    if self.element is not None and str(self.element).strip()
                    else list(elements))
        descriptors = describe_elements(filtered, limit)
        frame = await self._window_frame_for(pid, self.window)
        if frame is not None:
            for item in descriptors:
                item.update(self._to_window_coords(item, frame))
        payload = {
            "task_id": self.task_id,
            "op": "elements",
            "total": len(elements),
            "matched": len(filtered),
            "returned": len(descriptors),
            "elements": descriptors,
            "hint": ("操作方式二选一：①把 window_center_x / window_center_y 交给 "
                     "click 的 x / y（坐标与 op=screenshot 产物同源）；②直接给输入 "
                     "op 传 element='控件名'（或 'text:子串' / 'class:子串' / "
                     "'type:edit' / '#N' / 中文类型 '编辑框'、'按钮'），"
                     "click / move / scroll / drag 会用控件中心，type / key 会先点击"
                     "该控件聚焦再输入。enabled=false 的控件（灰置）通常点不动；"
                     "visible=false 表示当前被隐藏"),
        }
        if self.element:
            payload["element"] = str(self.element)
            if not filtered:
                payload["filter_empty"] = True
                payload["hint"] = (
                    f"element={self.element!r} 没有匹配的控件（本次共枚举到 "
                    f"{len(elements)} 个）。过滤支持 '#N'（清单第 N 个，从 1 开始）、"
                    f"'text:子串'、'id:子串'（自动化 ID）、'class:子串'、"
                    f"'type:edit' 与中文标签"
                    f"（'编辑框' / '按钮' / '列表'）；也可不传 element 先看完整清单"
                )
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _to_window_coords(item: dict, frame) -> dict:
        """把控件描述的屏幕坐标换算为窗口内坐标（截图坐标系）。"""
        return {
            "window_x": item["x"] - frame.screen_x,
            "window_y": item["y"] - frame.screen_y,
            "window_center_x": item["center_x"] - frame.screen_x,
            "window_center_y": item["center_y"] - frame.screen_y,
        }

    def _resolve_element_limit(self) -> int:
        """解析 ``max_elements``（控件清单返回条数上限，1.._MAX_ELEMENT_LIMIT）。"""
        raw = self.max_elements
        if raw is None:
            return self._DEFAULT_ELEMENT_LIMIT
        if isinstance(raw, bool):
            raise ValueError("max_elements 需要正整数")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"max_elements 需要正整数，当前: {raw!r}") from None
        if value < 1:
            raise ValueError(f"max_elements 必须为正整数，当前: {value}")
        return min(value, self._MAX_ELEMENT_LIMIT)

    # ── op=locate（图像 / 文字定位） ─────────────────────

    async def _op_locate(self, rec: dict) -> str:
        """在窗口截图里定位「局部图标」（模板匹配）或「文字」（OCR）。

        适合没有可枚举控件的界面（游戏、canvas、图片按钮）：截一张当前窗口
        画面，在其上查找：
          - ``template``：模板图片路径 → 模板匹配，返回图标位置与置信度；
          - ``query``（或 ``text``）：文字 → OCR 识别后查找，返回文字位置；
        返回的 ``x`` / ``y`` / ``center_x`` / ``center_y`` 与输入 op 的坐标
        同源（窗口截图坐标系），可直接交给 ``click`` / ``move``。

        ``crop`` 可把搜索范围限制在窗口内某区域（减少误匹配 / 提速）；
        ``tolerance``（模板匹配容差）、``max_results``、``min_scale`` /
        ``max_scale`` / ``scale_steps``（模板多尺度搜索）可微调。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法做图像 / 文字定位。可用 op=wait 查看任务状态)")
        template_raw = str(self.template).strip() if self.template is not None else ""
        query = self.query if self.query is not None else self.text
        query_provided = query is not None
        query_raw = str(query).strip() if query_provided else ""
        if not template_raw and not query_provided:
            return ("(locate 需要 template（模板图片路径）或 query（要查找的文字）："
                    "template='icon.png' 做图像匹配；query='保存' 做 OCR 文字定位。"
                    "可先用 op=screenshot 截图确认画面)")
        try:
            tolerance = self._resolve_locate_tolerance()
            max_results = self._resolve_max_results()
            min_scale, max_scale, scale_steps = self._resolve_scale()
            crop = self._resolve_crop()
        except (ValueError, ImageMatchError, CropError) as exc:
            return f"(locate 参数非法: {exc})"
        path = await self._temp_screenshot(pid, self.window)
        if path is None:
            return ("(locate 失败: 无法截取窗口画面（窗口可能尚未就绪或已关闭）——"
                    "可用 op=windows 确认窗口状态，或先 op=wait_window 等窗口出现)")
        try:
            if template_raw:
                payload = await self._locate_template(
                    template_raw, path, crop, tolerance, max_results,
                    min_scale, max_scale, scale_steps)
            else:
                payload = await self._locate_text(
                    query_raw, path, crop, max_results)
        except (ImageMatchError, OcrError, ScreenshotError, OSError,
                ValueError) as exc:
            return f"(locate 失败: {exc})"
        finally:
            self._remove_temp(path)
        frame = await self._window_frame_for(pid, self.window)
        if frame is not None:
            for item in payload["matches"]:
                item["screen_x"] = item["x"] + frame.screen_x
                item["screen_y"] = item["y"] + frame.screen_y
                item["screen_center_x"] = item["center_x"] + frame.screen_x
                item["screen_center_y"] = item["center_y"] + frame.screen_y
            payload["frame"] = frame.to_dict()
        payload.update({
            "task_id": self.task_id,
            "op": "locate",
            "hint": ("matches 里的 x/y/center_x/center_y 与输入 op 坐标同源"
                     "（窗口截图左上角为原点），可直接交给 click / move 的 x / y；"
                     "screen_* 是换算后的屏幕坐标。找到多个时可用 score 或 text "
                     "挑选；不确认时可 first 用 op=screenshot 复核"),
        })
        if self.window:
            payload["window"] = str(self.window)
        if crop is not None:
            payload["crop"] = crop.to_dict()
        return json.dumps(payload, ensure_ascii=False)

    async def _locate_template(self, template_path: str, shot_path: str, crop,
                               tolerance: int, max_results: int,
                               min_scale: float, max_scale: float,
                               scale_steps: int) -> dict:
        """模板匹配：解码截图与模板，返回窗口坐标下的匹配列表。"""
        expanded = os.path.expanduser(template_path)
        if not os.path.isfile(expanded):
            raise ImageMatchError(f"模板图片不存在: {template_path}")
        image = await asyncio.to_thread(decode_png_file, shot_path)
        template = await asyncio.to_thread(decode_png_file, expanded)
        offset_x = offset_y = 0
        if crop is not None:
            image = self._crop_decoded(image, crop)
            offset_x, offset_y = crop.x, crop.y
        matches = await asyncio.to_thread(
            lambda: match_template(
                image, template, tolerance=tolerance, max_results=max_results,
                min_scale=min_scale, max_scale=max_scale, scale_steps=scale_steps))
        items = [self._locate_item(match.to_dict(), offset_x, offset_y)
                 for match in matches]
        return {
            "mode": "template",
            "template": template_path,
            "template_size": {"width": template.width, "height": template.height},
            "total": len(items),
            "returned": len(items),
            "matches": items,
        }

    async def _locate_text(self, query: str, shot_path: str, crop,
                           max_results: int) -> dict:
        """OCR 文字定位：识别截图文字并按查询过滤（支持跨词短语）。"""
        boxes = await asyncio.to_thread(recognize_text, shot_path)
        if crop is not None:
            boxes = [box for box in boxes if _box_in_crop(box, crop)]
        if query:
            matched = await asyncio.to_thread(find_ocr_text, boxes, query)
        else:
            matched = list(boxes)
        offset_x = crop.x if crop is not None else 0
        offset_y = crop.y if crop is not None else 0
        items = []
        for box in matched[:max_results]:
            payload = box.to_dict()
            payload["x"] += offset_x
            payload["y"] += offset_y
            payload["center_x"] += offset_x
            payload["center_y"] += offset_y
            items.append(payload)
        return {
            "mode": "text",
            "query": query,
            "recognized": len(boxes),
            "total": len(matched),
            "returned": len(items),
            "matches": items,
        }

    @staticmethod
    def _locate_item(raw: dict, offset_x: int, offset_y: int) -> dict:
        """把匹配结果平移到窗口坐标（叠加 crop 偏移）。"""
        item = dict(raw)
        for key in ("x", "center_x"):
            item[key] = item[key] + offset_x
        for key in ("y", "center_y"):
            item[key] = item[key] + offset_y
        return item

    @staticmethod
    def _crop_decoded(image: DecodedImage, crop) -> DecodedImage:
        """按 crop 区域裁剪解码图像（越界抛 CropError）。"""
        cropped = crop_rgb(image.rgb, image.width, image.height, crop)
        return DecodedImage(crop.width, crop.height, cropped)

    def _resolve_locate_tolerance(self) -> int:
        """解析 locate 的模板匹配容差（缺省用图像匹配默认容差）。"""
        if self.tolerance is None:
            return self._LOCATE_TOLERANCE
        return self._parse_tolerance(self.tolerance)

    def _resolve_max_results(self) -> int:
        """解析 locate 的返回条数上限。

        Raises:
            ValueError: 取值非法。
        """
        raw = self.max_results
        if raw is None:
            return DEFAULT_MAX_RESULTS
        if isinstance(raw, bool):
            raise ValueError("max_results 需要正整数")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"max_results 需要正整数，当前: {raw!r}") from None
        if value < 1:
            raise ValueError(f"max_results 必须为正整数，当前: {value}")
        return min(value, self._MAX_LOCATE_RESULTS)

    def _resolve_scale(self) -> tuple[float, float, int]:
        """解析模板多尺度搜索参数（min_scale / max_scale / scale_steps）。"""
        minimum = self._number_or(self.min_scale, 1.0, "min_scale")
        maximum = self._number_or(self.max_scale, 1.0, "max_scale")
        steps = self._int_or(self.scale_steps, 1, "scale_steps")
        if minimum <= 0 or maximum <= 0:
            raise ImageMatchError("min_scale / max_scale 必须为正数")
        if minimum > maximum:
            raise ImageMatchError(
                f"min_scale（{minimum}）不能大于 max_scale（{maximum}）"
            )
        if steps < 1:
            raise ImageMatchError("scale_steps 必须 >= 1")
        return minimum, maximum, min(steps, self._MAX_LOCATE_SCALE_STEPS)

    @staticmethod
    def _number_or(raw, default: float, label: str) -> float:
        if raw is None:
            return default
        if isinstance(raw, bool):
            raise ImageMatchError(f"{label} 需要数值")
        try:
            return float(raw)
        except (TypeError, ValueError):
            raise ImageMatchError(f"{label} 需要数值，当前: {raw!r}") from None

    @classmethod
    def _int_or(cls, raw, default: int, label: str) -> int:
        if raw is None:
            return default
        if isinstance(raw, bool):
            raise ImageMatchError(f"{label} 需要整数")
        try:
            return int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ImageMatchError(f"{label} 需要整数，当前: {raw!r}") from None

    # ── op=pixel（像素取色 / 颜色检测） ──────────────────

    async def _op_pixel(self, rec: dict) -> str:
        """读取窗口截图的像素颜色（``op=pixel``）。

        三种模式（``mode``）：

          - ``point``（默认，需 ``x`` / ``y``）：读取该点颜色；给了 ``color``
            时附带「是否与目标色在容差内匹配」的结论；
          - ``region``（可选 ``region`` = ``'x,y,w,h'``，缺省整窗）：统计该区域
            的均值 / 极值 / 主色；给了 ``color`` 时比较均值色；
          - ``find``（需 ``color``）：在 ``region``（缺省整窗）内查找目标颜色，
            按 4 连通块聚合成若干候选位置（包围盒 / 中心点），可直接交给
            ``click`` 点击——适合定位状态灯、地图标记等多个同色元素。

        坐标以窗口截图左上角为原点（与 ``op=screenshot`` / 输入 op 同源），
        ``x`` / ``y`` 支持 ``'center'`` / ``'50%'`` / ``'center+20'`` 等语义值。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法取色。可用 op=wait 查看任务状态)")
        try:
            mode = self._resolve_pixel_mode()
        except ValueError as exc:
            return f"(pixel 参数非法: {exc})"
        try:
            target = (parse_color(self.color)
                      if self.color is not None and str(self.color).strip()
                      else None)
        except ColorError as exc:
            return f"(pixel 参数非法: {exc})"
        if mode == "find" and target is None:
            return "(pixel 的 find 模式需要 color 参数（要查找的目标颜色）)"
        if mode == "point" and (self.x is None or self.y is None):
            return "(pixel 的 point 模式需要 x 与 y（窗口内坐标，支持语义值）)"
        try:
            tolerance = (DEFAULT_COLOR_TOLERANCE if self.tolerance is None
                         else self._parse_tolerance(self.tolerance))
            region = self._parse_pixel_region()
            max_regions = self._resolve_pixel_regions()
            min_pixels = self._resolve_min_pixels()
        except (ValueError, CropError) as exc:
            return f"(pixel 参数非法: {exc})"
        path = await self._temp_screenshot(pid, self.window)
        if path is None:
            return ("(pixel 失败: 无法截取窗口画面（窗口可能尚未就绪或已关闭）——"
                    "可用 op=windows 确认窗口状态，或先 op=wait_window 等窗口出现)")
        try:
            image = await asyncio.to_thread(decode_png_file, path)
            payload = self._pixel_payload(image, mode, target, tolerance,
                                          region, max_regions, min_pixels)
        except (ColorError, CropError, ValueError, ActionError, OSError) as exc:
            return f"(pixel 失败: {exc})"
        finally:
            self._remove_temp(path)
        payload.update({
            "task_id": self.task_id,
            "op": "pixel",
            "mode": mode,
            "hint": ("颜色坐标为窗口截图坐标（与 op=screenshot 产物同源）；"
                     "find 模式返回的 center_x / center_y 可直接交给 click 的 x / y；"
                     "screen_* 是换算后的屏幕坐标"),
        })
        if self.window:
            payload["window"] = str(self.window)
        frame = await self._window_frame_for(pid, self.window)
        if frame is not None:
            payload["frame"] = frame.to_dict()
            _attach_screen_coords(payload, frame)
        return json.dumps(payload, ensure_ascii=False)

    def _resolve_pixel_mode(self) -> str:
        """解析 ``op=pixel`` 的 ``mode``（point / region / find，接受常见别名）。"""
        raw = self.mode
        if raw is None or not str(raw).strip():
            return self._DEFAULT_PIXEL_MODE
        text = str(raw).strip().lower()
        aliases = {
            "point": "point", "pixel": "point", "color": "point", "at": "point",
            "region": "region", "area": "region", "stats": "region",
            "average": "region", "avg": "region",
            "find": "find", "search": "find", "match": "find", "locate": "find",
        }
        resolved = aliases.get(text)
        if resolved is None:
            raise ValueError(
                f"mode 取值非法: {raw!r}。支持 point（点取色）/ region（区域统计）"
                f"/ find（颜色查找）"
            )
        return resolved

    def _parse_pixel_region(self) -> "CropRegion | None":
        """解析 ``op=pixel`` 的 ``region``（``'x,y,w,h'``；空 = None）。"""
        raw = self.region
        if raw is None or not str(raw).strip():
            return None
        return CropRegion.parse(str(raw))

    def _resolve_pixel_regions(self) -> int:
        """解析 ``op=pixel`` 的 ``max_regions``（1.._MAX_PIXEL_REGIONS）。"""
        raw = self.max_regions
        if raw is None:
            return self._DEFAULT_PIXEL_REGIONS
        if isinstance(raw, bool):
            raise ValueError("max_regions 需要正整数")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"max_regions 需要正整数，当前: {raw!r}") from None
        if value < 1:
            raise ValueError(f"max_regions 必须为正整数，当前: {value}")
        return min(value, self._MAX_PIXEL_REGIONS)

    def _resolve_min_pixels(self) -> int:
        """解析 ``op=pixel`` 的 ``min_pixels``（连通块最小像素数）。"""
        raw = self.min_pixels
        if raw is None:
            return 1
        if isinstance(raw, bool):
            raise ValueError("min_pixels 需要正整数")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"min_pixels 需要正整数，当前: {raw!r}") from None
        if value < 1:
            raise ValueError(f"min_pixels 必须为正整数，当前: {value}")
        return value

    def _pixel_payload(self, image, mode: str, target, tolerance: int,
                       region, max_regions: int, min_pixels: int) -> dict:
        """按模式组装取色结果（不含 task_id / frame 等公共字段）。"""
        if mode == "point":
            x = parse_coordinate(self.x, image.width, label="取色 x")
            y = parse_coordinate(self.y, image.height, label="取色 y")
            value = pixel_at(image, x, y)
            return {
                "x": x,
                "y": y,
                "color": summarize_color(value, target, tolerance=tolerance),
                "image": {"width": image.width, "height": image.height},
            }
        if mode == "region":
            stats = region_stats(image, region)
            payload = {
                "region": region.to_dict() if region is not None
                else {"x": 0, "y": 0, "width": image.width, "height": image.height},
                "stats": stats,
                "image": {"width": image.width, "height": image.height},
            }
            if target is not None:
                average = stats["average"]
                payload["color"] = summarize_color(
                    RGB(average["r"], average["g"], average["b"]), target,
                    tolerance=tolerance)
            return payload
        regions = find_color_regions(
            image, target, tolerance=tolerance, region=region,
            max_regions=max_regions, min_pixels=min_pixels)
        return {
            "color": target.to_dict(),
            "tolerance": tolerance,
            "region": region.to_dict() if region is not None
            else {"x": 0, "y": 0, "width": image.width, "height": image.height},
            "found": len(regions),
            "regions": [item.to_dict() for item in regions],
        }

    # ── op=annotate（截图标注） ──────────────────────────

    async def _op_annotate(self, rec: dict) -> str:
        """在截图上绘制矩形 / 十字 / 编号标签（``op=annotate``）。

        输入图二选一：``path`` 指定已有 PNG，或用 ``window`` 从目标窗口现截；
        ``boxes`` / ``points`` / ``labels`` 描述要画的标记（顺序一一对应，
        省略 ``labels`` 时自动编号）。``output`` 指定输出路径（省略时覆盖
        ``path``；从窗口现截时输出到 ``bash_opt_shots/``）。
        """
        boxes = self._as_list(self.boxes)
        points = self._as_list(self.points)
        try:
            grid = self._resolve_grid()
        except CropError as exc:
            return f"(annotate 参数非法: {exc})"
        if not boxes and not points and grid is None:
            return "(annotate 至少需要 boxes / points 之一，或给 grid 叠加网格)"
        if len(boxes) > self._MAX_ANNOTATE_MARKS or len(points) > self._MAX_ANNOTATE_MARKS:
            return (f"(annotate 元素过多：boxes / points 各上限 "
                    f"{self._MAX_ANNOTATE_MARKS} 个)")
        source: str | None = None
        temporary = False
        try:
            if self.path and str(self.path).strip():
                source = self._prepare_image_path(str(self.path))
            else:
                pid = rec.get("pid")
                if pid is None:
                    return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或"
                            f"已退出），无法截图标注；也可用 path 指定已有 PNG)")
                source = await self._temp_screenshot(pid, self.window)
                if source is None:
                    return ("(annotate 失败: 无法截取窗口画面（窗口可能尚未就绪"
                            "或已关闭）——可先用 op=screenshot 确认窗口)")
                temporary = True
            try:
                target = self._annotate_output_path(source)
            except ValueError as exc:
                return f"(annotate 输出路径非法: {exc})"
            result = await asyncio.to_thread(
                annotate_png_file, source,
                boxes=boxes, points=points, labels=self.labels,
                color=self.color, output=target,
                thickness=self._ANNOTATE_THICKNESS,
                text_scale=self._ANNOTATE_TEXT_SCALE, grid=grid)
        except (AnnotateError, ColorError, ScreenshotError, ValueError,
                OSError) as exc:
            return f"(annotate 失败: {exc})"
        finally:
            if temporary and source is not None:
                self._remove_temp(source)
        payload = {
            "task_id": self.task_id,
            "op": "annotate",
            "hint": ("标注图已写出，可用 read_image 读取核对；坐标以图像左上角"
                     "为原点（与 op=screenshot 产物同源）"),
        }
        payload.update(result.to_dict())
        if self.window:
            payload["window"] = str(self.window)
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _as_list(value) -> list:
        """把可选参数归一化为列表（``None`` → 空；单值 → 单元素列表）。"""
        if value is None:
            return []
        if isinstance(value, (list, tuple)):
            return list(value)
        return [value]

    @staticmethod
    def _prepare_image_path(path: str) -> str:
        """规范化输入图片路径（展开 ~、补 .png、安全校验、要求存在）。

        Raises:
            ValueError: 路径为空、文件不存在或未通过安全校验。
        """
        expanded = os.path.expanduser(str(path).strip())
        if not expanded:
            raise ValueError("路径为空")
        absolute = os.path.abspath(expanded)
        if not os.path.splitext(absolute)[1]:
            absolute += ".png"
        validate_path_security(absolute)
        if not os.path.isfile(absolute):
            raise ValueError(f"输入图片不存在: {absolute}")
        return absolute

    def _annotate_output_path(self, source: str) -> str:
        """解析标注输出路径：显式 output 优先，否则覆盖 path / 自动命名。"""
        if self.output is not None and str(self.output).strip():
            return self._prepare_screenshot_path(str(self.output))
        if self.path is not None and str(self.path).strip():
            return source
        stamp = time.strftime("%H%M%S")
        return self._prepare_screenshot_path(
            os.path.join(self._SHOT_AUTO_DIR,
                         f"{self.task_id}-annotated-{stamp}.png"))

    # ── op=record / op=replay（操作宏） ──────────────────

    async def _op_record(self, rec: dict) -> str:
        """把一段操作序列保存为命名宏（``op=record``）。

        步骤格式与 ``op=sequence`` 的 ``actions`` 完全一致；用 ``macro`` 指定
        宏名（存到 ``bash_opt_macros/<宏名>.json``）或用 ``path`` 指定文件路径。
        ``append=true`` 时把新步骤追加到同名宏末尾。
        """
        raw = self.actions
        if raw is None:
            return "(record 需要 actions 参数（要保存的步骤数组，与 sequence 相同）)"
        steps = raw if isinstance(raw, (list, tuple)) else [raw]
        name = str(self.macro).strip() if self.macro is not None else ""
        path = str(self.path).strip() if self.path is not None else ""
        if not name and not path:
            return "(record 需要 macro（宏名）或 path（文件路径）)"
        window = str(self.window).strip() if self.window else ""
        if name:
            macro = Macro(name=name, steps=tuple(dict(step) for step in steps),
                          window=window)
        else:
            base = os.path.splitext(os.path.basename(path))[0] or "macro"
            macro = Macro(name=base, steps=tuple(dict(step) for step in steps),
                          window=window)
        try:
            saved = await asyncio.to_thread(
                save_macro, macro, path=(path or None),
                directory=self._MACRO_DIR, append=bool(self.append))
        except MacroError as exc:
            return f"(record 失败: {exc})"
        payload = {
            "task_id": self.task_id,
            "op": "record",
            "macro": macro.name,
            "path": saved,
            "steps": len(macro.steps),
            "appended": bool(self.append),
            "hint": (f"宏已保存；可用 op=replay, macro='{macro.name}' 回放"
                     f"（或 path='{saved}'）"),
        }
        return json.dumps(payload, ensure_ascii=False)

    async def _op_replay(self, rec: dict) -> str:
        """回放命名宏（``op=replay``）：按 ``macro``（宏名）或 ``path`` 加载并执行。

        步骤在保存时已校验；回放复用 ``op=sequence`` 的步骤执行器，因此
        element / via / wait_for / diff / shot 等增强参数同样生效。``times``
        可整段重复执行多次（默认 1，上限 20）；``on_error`` 决定遇错停止 / 继续。
        """
        name = str(self.macro).strip() if self.macro is not None else ""
        path = str(self.path).strip() if self.path is not None else ""
        if not name and not path:
            return "(replay 需要 macro（宏名）或 path（文件路径）)"
        try:
            macro = await asyncio.to_thread(
                load_macro, name=(name or None), path=(path or None),
                directory=self._MACRO_DIR)
        except MacroError as exc:
            available = await asyncio.to_thread(list_saved_macros, self._MACRO_DIR)
            names = ", ".join(item["name"] for item in available) or "（暂无已保存宏）"
            return f"(replay 失败: {exc}。当前可用宏: {names})"
        try:
            times = self._resolve_times()
            on_error = self._resolve_on_error()
        except ValueError as exc:
            return f"(replay 参数非法: {exc})"
        try:
            steps = parse_sequence([dict(step) for step in macro.steps])
        except SequenceError as exc:
            return f"(replay 失败: 宏步骤非法: {exc})"
        runs: list[dict] = []
        completed = failed = 0
        stopped_early = False
        async with self._input_session(rec):
            for iteration in range(times):
                results, run_completed, run_failed, stopped = (
                    await self._run_step_sequence(rec, steps, on_error))
                completed += run_completed
                failed += run_failed
                runs.append({
                    "iteration": iteration + 1,
                    "completed": run_completed,
                    "failed": run_failed,
                    "steps": results,
                })
                if stopped:
                    stopped_early = True
                    break
        payload = {
            "task_id": self.task_id,
            "op": "replay",
            "macro": macro.name,
            "path": path or os.path.join(self._MACRO_DIR, macro.name + ".json"),
            "times": times,
            "executed": len(runs),
            "steps_per_run": len(steps),
            "completed": completed,
            "failed": failed,
            "stopped_early": stopped_early or len(runs) < times,
            "on_error": on_error,
            "runs": runs,
            "hint": ("宏已回放；每步 result 是该动作的完整结果。需要确认界面变化时"
                     "可在宏里放 screenshot 步骤，或对关键步骤设 shot / settle / "
                     "wait_for='change'"),
        }
        return json.dumps(payload, ensure_ascii=False)

    def _resolve_times(self) -> int:
        """解析 ``op=replay`` 的 ``times``（1.._MAX_REPLAY_TIMES）。"""
        raw = self.times
        if raw is None:
            return self._DEFAULT_REPLAY_TIMES
        if isinstance(raw, bool):
            raise ValueError("times 需要正整数")
        try:
            value = int(float(str(raw).strip()))
        except (TypeError, ValueError):
            raise ValueError(f"times 需要正整数，当前: {raw!r}") from None
        if value < 1:
            raise ValueError(f"times 必须为正整数，当前: {value}")
        return min(value, self._MAX_REPLAY_TIMES)

    # ── op=wait_window（等待窗口出现） ───────────────────

    async def _op_wait_window(self, rec: dict) -> str:
        """等待目标窗口出现（按 ``window`` 选择器）。

        GUI 程序启动后窗口创建有延迟（尤其是 Electron / 游戏 / 需要登录的
        IDE），先等窗口就绪再操作可以避免一连串「没有匹配窗口」的失败。
        ``timeout`` 秒内每 ``_WAIT_WINDOW_INTERVAL`` 轮询一次窗口清单，用
        ``window`` 选择器匹配；命中即返回该窗口的清单条目与几何。

        超时返回可读提示（附当前窗口清单），便于改用正确的选择器。
        """
        pid = rec.get("pid")
        if pid is None:
            return (f"(后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                    f"无法等待窗口。可用 op=wait 查看任务状态)")
        timeout = self.timeout if self.timeout_given else self._WAIT_WINDOW_TIMEOUT
        started = time.monotonic()
        deadline = None if not timeout else started + timeout
        selector = self.window or "main"
        attempts = 0
        last_error = ""
        while True:
            attempts += 1
            infos = await self._list_windows_quietly(pid)
            if infos:
                try:
                    target = pick_window(infos, self.window)
                except SelectorError as exc:
                    last_error = str(exc)
                else:
                    described = describe_windows(infos)
                    match = next((item for item in described
                                  if item["handle"] == target.handle), None)
                    payload = {
                        "task_id": self.task_id,
                        "op": "wait_window",
                        "found": True,
                        "selector": selector,
                        "waited": round(time.monotonic() - started, 3),
                        "attempts": attempts,
                        "window": match,
                        "windows": described,
                        "hint": ("窗口已就绪：用同一个 window 选择器继续 "
                                 "screenshot / 输入 op；需要固定布局时可先用 "
                                 "op=window 的 always_on_top 或 save_geometry"),
                    }
                    return json.dumps(payload, ensure_ascii=False)
            if deadline is not None and time.monotonic() >= deadline:
                break
            await asyncio.sleep(self._WAIT_WINDOW_INTERVAL)
        infos = await self._list_windows_quietly(pid)
        candidates = window_hint(infos) if infos else "（无可见窗口）"
        timeout_desc = f"{timeout:g} 秒" if timeout else "无限"
        detail = f"；匹配提示: {last_error}" if last_error else ""
        return (f"(等待窗口超时（{timeout_desc}）：window 选择器 {selector!r} 没有等到"
                f"匹配窗口{detail}。当前窗口: {candidates}。"
                f"可用 op=windows 查看完整清单、op=wait 确认进程状态；"
                f"若进程是纯命令行程序，它不会有 GUI 窗口)")

    async def _list_windows_quietly(self, pid: int) -> list:
        """枚举窗口（失败 / 超时返回空列表，供轮询使用）。"""
        try:
            return await asyncio.wait_for(
                asyncio.to_thread(list_process_windows, pid),
                timeout=self._INPUT_TIMEOUT,
            )
        except (asyncio.TimeoutError, OSError, ValueError, RuntimeError):
            return []

    # ── op=clipboard（系统剪贴板） ───────────────────────

    async def _op_clipboard(self, rec: dict) -> str:
        """读写系统剪贴板（``clipboard_action=set / get / clear / append``）。

        典型用法：先把长文本 / 中文 / emoji 放进剪贴板，再用 ``op=key`` 发
        ``ctrl+v`` 粘贴（或用 ``op=type`` 的 ``via='clipboard'`` 一步完成）。
        文本走剪贴板比逐字符合成按键更可靠，也不会被目标程序当成快捷键。
        """
        try:
            action, text = self._resolve_clipboard_action()
        except ValueError as exc:
            return f"(clipboard 参数非法: {exc})"
        try:
            if action == "get":
                content = await asyncio.to_thread(read_clipboard_text)
                truncated = len(content) > self._CLIPBOARD_MAX_CHARS
                payload = {
                    "task_id": self.task_id,
                    "op": "clipboard",
                    "clipboard_action": "get",
                    "text": content[:self._CLIPBOARD_MAX_CHARS],
                    "length": len(content),
                }
                if truncated:
                    payload["truncated"] = True
                    payload["hint"] = (
                        f"剪贴板文本超过 {self._CLIPBOARD_MAX_CHARS} 字符，已截断返回"
                        f"（完整长度 {len(content)}）"
                    )
                return json.dumps(payload, ensure_ascii=False)
            if action == "clear":
                await asyncio.to_thread(write_clipboard_text, "")
                return json.dumps({
                    "task_id": self.task_id,
                    "op": "clipboard",
                    "clipboard_action": "clear",
                    "length": 0,
                    "hint": "剪贴板文本已清空",
                }, ensure_ascii=False)
            if action == "append":
                existing = await asyncio.to_thread(read_clipboard_text)
                merged = existing + text
                await asyncio.to_thread(write_clipboard_text, merged)
                return json.dumps({
                    "task_id": self.task_id,
                    "op": "clipboard",
                    "clipboard_action": "append",
                    "length": len(merged),
                    "appended": len(text),
                    "hint": ("已在剪贴板原有内容后追加文本；可用 op=key "
                             "key='ctrl+v' 粘贴到当前焦点窗口"),
                }, ensure_ascii=False)
            await asyncio.to_thread(write_clipboard_text, text)
            return json.dumps({
                "task_id": self.task_id,
                "op": "clipboard",
                "clipboard_action": "set",
                "length": len(text),
                "hint": ("文本已写入剪贴板；可用 op=key key='ctrl+v' 粘贴，"
                         "或直接用 op=type 的 via='clipboard' 一步完成输入"),
            }, ensure_ascii=False)
        except ClipboardError as exc:
            return f"(剪贴板操作失败: {exc})"

    def _resolve_clipboard_action(self) -> tuple[str, str]:
        """解析 ``clipboard_action`` 与文本（缺省按是否提供 text 推断）。

        Raises:
            ValueError: 动作非法，或 set / append 缺少 text。
        """
        raw = self.clipboard_action
        text = self.text
        if raw is None or not str(raw).strip():
            action = "set" if text is not None else "get"
        else:
            action = str(raw).strip().lower()
        aliases = {
            "set": "set", "write": "set", "copy": "set", "put": "set",
            "get": "get", "read": "get", "show": "get",
            "clear": "clear", "empty": "clear", "reset": "clear",
            "append": "append", "add": "append", "push": "append",
        }
        resolved = aliases.get(action)
        if resolved is None:
            raise ValueError(
                f"clipboard_action 取值非法: {raw!r}。支持: "
                f"{', '.join(self._CLIPBOARD_ACTIONS)}（缺省按是否提供 text 推断）"
            )
        if resolved in ("set", "append"):
            if text is None:
                raise ValueError(
                    f"clipboard {resolved} 需要 text 参数（要写入剪贴板的文本）"
                )
            return resolved, str(text)
        return resolved, ""

    # ── op=sequence（一次调用执行一串动作） ──────────────

    async def _op_sequence(self, rec: dict) -> str:
        """按顺序执行 ``actions`` 里的多个动作（见 ``_window_input.sequence``）。

        一个应用往往需要「点击输入框 → 输入文本 → 回车 → 截图」这样的连续操作；
        逐条调用工具不仅往返多，两次调用之间还可能被别的窗口抢走焦点。本 op 在
        同一次调用里按序执行全部步骤，``on_error`` 决定遇错停止（默认）还是继续。

        步骤类型：输入动作（click / move / drag / scroll / key / type）与
        ``wait``（等待秒数）、``screenshot``（截图存盘）、``window``（窗口控制，
        含 always_on_top / save_geometry 等）。每个步骤都可带 ``window`` /
        ``settle`` / ``shot`` / ``element`` 参数，语义与单独调用时一致；
        未指定 ``window`` 时继承本次调用的 ``window``。
        """
        try:
            steps = parse_sequence(self.actions)
        except SequenceError as exc:
            return f"(sequence 参数非法: {exc})"
        try:
            on_error = self._resolve_on_error()
        except ValueError as exc:
            return f"(sequence 参数非法: {exc})"
        async with self._input_session(rec):
            results, completed, failed, stopped = await self._run_step_sequence(
                rec, steps, on_error)
        payload = {
            "task_id": self.task_id,
            "op": "sequence",
            "total": len(steps),
            "completed": completed,
            "failed": failed,
            "stopped_early": stopped,
            "on_error": on_error,
            "steps": results,
            "hint": ("序列已执行；每步的 result 字段是该动作的完整结果（含坐标 / "
                     "投递方式）。需要确认界面变化时可在整段后跟一步 screenshot，"
                     "或对关键步骤设 shot=true / settle / wait_for='change'；"
                     "遇错停止时可用 on_error='continue' 让后续步骤继续执行"),
        }
        return json.dumps(payload, ensure_ascii=False)

    @asynccontextmanager
    async def _input_session(self, rec: dict):
        """在批量动作（sequence / replay）前后包裹「输入会话」以降低每步开销。

        会话期间，输入后端会缓存窗口定位结果并复用前台确认，游戏这类需要
        连续快速输入的场景更跟手；开启失败（后端不支持）时静默降级为逐条
        调用，行为完全一致。
        """
        pid = rec.get("pid")
        started = False
        if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0:
            try:
                started = await asyncio.to_thread(begin_input_session, pid)
            except Exception:  # noqa: BLE001 - 会话优化失败不应影响执行
                logger.debug("开启输入会话失败", exc_info=True)
                started = False
        try:
            yield
        finally:
            if started:
                try:
                    await asyncio.to_thread(end_input_session)
                except Exception:  # noqa: BLE001
                    logger.debug("结束输入会话失败", exc_info=True)

    async def _run_step_sequence(self, rec: dict, steps: list,
                                 on_error: str) -> tuple[list[dict], int, int, bool]:
        """按序执行步骤列表，返回 ``(步骤结果, 成功数, 失败数, 是否提前停止)``。

        供 ``op=sequence`` 与 ``op=replay`` 共用：逐步执行并记录每步的
        ``ok`` / ``elapsed`` / 结果（或错误）；``on_error='stop'`` 时首个失败的
        步骤会中止后续步骤。
        """
        results: list[dict] = []
        completed = 0
        failed = 0
        stopped = False
        for step in steps:
            started = time.monotonic()
            try:
                detail = await self._run_sequence_step(rec, step)
            except (ActionError, InputError, ScreenshotError, SelectorError,
                    ElementError, ClipboardError, ColorError, AnnotateError,
                    ValueError) as exc:
                failed += 1
                entry = {
                    "index": step.index,
                    "op": step.kind,
                    "ok": False,
                    "error": str(exc),
                    "elapsed": round(time.monotonic() - started, 3),
                }
                if on_error == "stop":
                    entry["stopped"] = True
                results.append(entry)
                if on_error == "stop":
                    stopped = True
                    break
                continue
            completed += 1
            entry = {
                "index": step.index,
                "op": step.kind,
                "ok": True,
                "elapsed": round(time.monotonic() - started, 3),
            }
            entry.update(detail)
            results.append(entry)
        return results, completed, failed, stopped

    async def _run_sequence_step(self, rec: dict, step: SequenceStep) -> dict:
        """执行序列中的一步（wait / screenshot / window / 输入动作）。

        Raises:
            ActionError / InputError / ScreenshotError / SelectorError /
            ElementError: 该步失败（由调用方按 on_error 处理）。
        """
        if step.kind == "wait":
            seconds = wait_seconds(step)
            await asyncio.sleep(seconds)
            return {"waited": seconds}
        if step.kind == "screenshot":
            return await self._sequence_screenshot(rec, step)
        if step.kind == "window":
            detail = await self._apply_window_action(
                rec, step.params.get("window_action"),
                window=step.window or self.window,
                x=step.params.get("x"), y=step.params.get("y"),
                width=step.params.get("width"), height=step.params.get("height"),
            )
            return {"window": detail}
        return await self._sequence_action(rec, step)

    async def _sequence_action(self, rec: dict, step: SequenceStep) -> dict:
        """执行序列中的输入动作步骤。

        支持与本工具实例同名的全部增强参数（写在步骤对象里即可）：
        ``element``（按控件名定位）、``via``（type 走剪贴板）、
        ``paste_key`` / ``restore_clipboard`` / ``newline``（剪贴板粘贴与
        末尾换行，与单独调用 ``type`` 同义）、``settle``、``shot``、
        ``wait_for`` / ``wait_timeout``、``diff`` / ``tolerance``。
        """
        pid = rec.get("pid")
        if pid is None:
            raise ScreenshotError(
                f"后台任务 {self.task_id} 尚无进程句柄（命令未就绪或已退出），"
                f"无法注入输入"
            )
        params = dict(step.params)
        if not str(params.get("window") or "").strip() and self.window:
            params["window"] = self.window
        try:
            wait_mode, wait_timeout, extra_wait = self._resolve_wait_target(
                params.pop("wait_for", _UNSET),
                params.pop("wait_timeout", _UNSET))
            diff_enabled = self._parse_diff(params.pop("diff", None))
            tolerance = self._parse_tolerance(params.pop("tolerance", None))
            via = (self._resolve_type_via(params.pop("via", _UNSET))
                   if step.kind == "type" else "typing")
            # 步骤级「剪贴板输入」参数：与单独调用 type 时同名同义
            paste_key = (params.pop("paste_key", _UNSET)
                         if step.kind == "type" else _UNSET)
            restore_clipboard = (params.pop("restore_clipboard", _UNSET)
                                 if step.kind == "type" else _UNSET)
            newline = self._parse_bool(
                params.pop("newline", None) if step.kind == "type" else None,
                default=False, label="newline")
        except ValueError as exc:
            raise ActionError(str(exc)) from exc
        if step.kind == "type" and newline:
            # 与单独调用 type 的 newline 语义一致：文本末尾追加换行（Enter）
            params["text"] = str(params.get("text") or "") + "\n"
        element = params.pop("element", None)
        point = None
        element_desc = None
        if element is not None and str(element).strip():
            point, info = await self._element_target(
                pid, element, params.get("window"))
            element_desc = self._element_summary(info)
            if step.kind == "drag":
                params["from_x"], params["from_y"] = point
            elif step.kind in ("click", "move", "scroll"):
                params["x"], params["y"] = point
        action = build_action(step.kind, params)
        focus_click = self._element_focus_click(action, point)
        temporaries: list[str] = []
        payload: dict = {}
        try:
            before_path = None
            if diff_enabled or wait_mode is not None:
                before_path = await self._temp_screenshot(
                    pid, params.get("window"))
                if before_path is not None:
                    temporaries.append(before_path)
            if focus_click is not None:
                await self._send_input_with_retry(pid, focus_click)
            payload["result"] = await self._inject_input_action(
                pid, action, via, paste_key=paste_key,
                restore_clipboard=restore_clipboard)
            if element_desc is not None:
                payload["element"] = element_desc
            settle = step.settle or extra_wait
            if settle:
                await asyncio.sleep(settle)
            if wait_mode is not None:
                payload["wait_for"] = await self._await_screen(
                    pid, mode=wait_mode, before_path=before_path,
                    window=params.get("window"), timeout=wait_timeout)
            if diff_enabled:
                payload["diff"] = await self._diff_result(
                    pid, before_path, window=params.get("window"),
                    tolerance=tolerance)
            if step.shot:
                holder: dict = {}
                error = await self._attach_shot(
                    holder, rec, shot=step.shot,
                    window=step.window or self.window,
                    grid=step.params.get("grid"))
                if error:
                    payload["screenshot_error"] = error
                elif "screenshot" in holder:
                    payload["screenshot"] = holder["screenshot"]
        finally:
            self._cleanup_temps(temporaries)
        return payload

    async def _sequence_screenshot(self, rec: dict, step: SequenceStep) -> dict:
        """执行序列中的 screenshot 步骤（截图存盘）。

        支持与 ``op=screenshot`` 同名的增强参数：``screen``（整屏 / 多显示器）、
        ``element`` / ``margin``（按控件区域截图）、``crop`` / ``grid``。
        """
        path = str(step.params.get("path") or "").strip()
        try:
            target_path = self._prepare_screenshot_path(path)
        except ValueError as exc:
            raise ScreenshotError(f"截图路径非法: {exc}") from exc
        crop = None
        crop_raw = step.params.get("crop")
        if crop_raw is not None and str(crop_raw).strip():
            try:
                crop = CropRegion.parse(str(crop_raw))
            except CropError as exc:
                raise ScreenshotError(f"截图裁剪参数非法: {exc}") from exc
        try:
            grid = self._resolve_grid(step.params.get("grid"))
        except CropError as exc:
            raise ScreenshotError(f"截图网格参数非法: {exc}") from exc
        info = await self._screenshot_target(
            rec, target_path=target_path, crop=crop, grid=grid,
            window=step.window or self.window,
            screen=step.params.get("screen"),
            element=step.params.get("element"),
            margin=step.params.get("margin"))
        payload = {"screenshot": info["result"].to_dict()}
        if info.get("element") is not None:
            payload["element"] = info["element"]
        if info.get("monitor") is not None:
            payload["monitor"] = info["monitor"]
        return payload

    def _resolve_on_error(self) -> str:
        """解析 ``on_error``（``stop`` 遇错停止 / ``continue`` 继续后续步骤）。"""
        raw = self.on_error
        if raw is None:
            return self._DEFAULT_ON_ERROR
        text = str(raw).strip().lower()
        if not text:
            return self._DEFAULT_ON_ERROR
        aliases = {
            "stop": "stop", "abort": "stop", "halt": "stop", "break": "stop",
            "continue": "continue", "skip": "continue", "keep": "continue",
            "next": "continue",
        }
        resolved = aliases.get(text)
        if resolved is None:
            raise ValueError(
                f"on_error 取值非法: {raw!r}。支持 stop（遇错停止，默认）或 "
                f"continue（跳过失败步骤继续执行）"
            )
        return resolved

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
