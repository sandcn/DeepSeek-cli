"""Windows 窗口输入后端（原生 Windows / Cygwin / MSYS2 通用）。

窗口定位与截图后端完全一致（同一套进程树展开 + 顶层窗口筛选规则），因此
输入坐标与 ``op=screenshot`` 产物像素一一对应。

两条投递路径：

  - **SendInput**（默认）：合成系统级输入事件，作用于**前台窗口**。注入前
    先把目标窗口置前；若始终无法取得前台（例如系统前台锁定），自动回退
    PostMessage 投递。兼容性最好，鼠标移动、点击、拖动、滚轮、键盘、Unicode
    文本（IME 无关）都可用。
  - **PostMessage**（``method='message'``）：把 ``WM_MOUSEMOVE`` /
    ``WM_*BUTTON*`` / ``WM_MOUSEWHEEL`` / ``WM_KEY*`` / ``WM_CHAR`` 直接投递
    给窗口，不移动真实光标、不需要焦点；但目标程序若不处理这些消息则不生效。

弹层类窗口（右键菜单 / 下拉浮层 / ``WS_EX_TOOLWINDOW`` 弹出窗口）**不参与
前台切换**，``SetForegroundWindow`` 对它们无效。这类窗口按「**应用**是否在
前台」判定可用性：目标窗口自身、其属主窗口、或同进程树内的任一窗口是前台，
即认为该应用持有输入焦点，可用 SendInput 投递——鼠标事件按屏幕坐标命中
光标下的真实窗口（弹层浮在最上层，正是它的常规操作方式），键盘事件发给
前台窗口（同一应用的渲染进程）。只有整个应用都不在前台时，才退回
PostMessage；若该窗口恰好没有可换算的客户区（``op=windows`` 里的
``client_area=false``），会给出可操作的错误提示，而不是晦涩的坐标换算失败。

键盘按键的**按下与弹起分别独立发送**（``phase='down'`` 只按下、``'up'``
只弹起、``'press'`` 按下后弹起）；Alt 组合键走系统按键消息
（``WM_SYSKEYDOWN``/``WM_SYSKEYUP``，SendInput 路径由系统派生），
``op=type`` 的每个字符发送配对的按下 → 字符 → 弹起。

坐标：后端按「窗口截图坐标系」接收坐标（原点为截图左上角），SendInput 前
换算为屏幕物理像素（进程已 DPI 感知），PostMessage 前换算为客户区坐标
（``WM_MOUSEWHEEL`` 例外，Windows 规定其坐标是屏幕坐标）。
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Callable, Iterator

from .._screenshot import winapi
from .._screenshot.win import (
    WindowCandidate,
    enumerate_window_infos,
    resolve_window_pids,
    visible_region,
)
from .._screenshot.windows import DEFAULT_SELECTOR, pick_window
from .action import (
    ClickAction,
    DragAction,
    InputAction,
    KeyAction,
    MoveAction,
    Point,
    ScrollAction,
    TextAction,
    interpolate,
    resolve_point,
    validate_point,
)
from .geometry import WindowFrame
from .keys import (
    MODIFIER_ORDER,
    WINDOWS_VK,
    Shortcut,
    shift_character,
    utf16_units,
)
from .result import ActionError, InputError, InputResult, NoWindowError

logger = logging.getLogger(__name__)

# ── 时序常量（秒） ──────────────────────────────────────

#: 前台激活的尝试轮数：Windows 前台锁定策略会拒绝后台进程的首次
#: SetForegroundWindow（尤其是刚启动、被最小化或系统正忙时），多试几轮
#: 可显著提高成功率。
_FOREGROUND_ATTEMPTS = 3
#: 每轮激活后等待系统切换焦点的时间（按轮次递增：越靠后等得越久）。
#: 兜底：轮次超出元组长度时取最后一项。
_FOREGROUND_SETTLE_STEPS = (0.05, 0.10, 0.20)
#: SendInput 完全未投递时（返回 0）的重试次数与间隔——合成输入偶发被
#: UIPI / 输入桌面忙碌拒绝，短期重试即可成功。
_SENDINPUT_ATTEMPTS = 3
_SENDINPUT_RETRY_INTERVAL = 0.02
#: 键盘 / 文本注入后前台被抢走时的「重新激活并重发」次数
_KEYBOARD_FOCUS_ATTEMPTS = 2
#: 连按（repeat）每次之间的间隔，避免目标程序把连续事件合并
_KEY_REPEAT_INTERVAL = 0.05
#: 双击两次点击之间的间隔（需小于系统双击时间）
_DOUBLE_CLICK_INTERVAL = 0.05
#: 拖动轨迹每步的最小间隔（duration 为 0 时仍留出重绘时间）
_DRAG_MIN_INTERVAL = 0.005

#: 需要 KEYEVENTF_EXTENDEDKEY 的虚拟键（右侧/小键盘区外的那一批）
_EXTENDED_VKS = frozenset({
    winapi.VK_LEFT, winapi.VK_UP, winapi.VK_RIGHT, winapi.VK_DOWN,
    winapi.VK_INSERT, winapi.VK_DELETE, winapi.VK_HOME, winapi.VK_END,
    winapi.VK_PRIOR, winapi.VK_NEXT, winapi.VK_LWIN, winapi.VK_RWIN,
    winapi.VK_APPS, winapi.VK_NUMLOCK, winapi.VK_SNAPSHOT,
})

#: 鼠标按钮 → SendInput 按下/抬起标志
_BUTTON_FLAGS: dict[str, tuple[int, int]] = {
    "left": (winapi.MOUSEEVENTF_LEFTDOWN, winapi.MOUSEEVENTF_LEFTUP),
    "right": (winapi.MOUSEEVENTF_RIGHTDOWN, winapi.MOUSEEVENTF_RIGHTUP),
    "middle": (winapi.MOUSEEVENTF_MIDDLEDOWN, winapi.MOUSEEVENTF_MIDDLEUP),
}

#: 鼠标按钮 → 窗口消息（按下, 抬起, 双击, 按下时的 MK_* 状态位）
_BUTTON_MESSAGES: dict[str, tuple[int, int, int, int]] = {
    "left": (winapi.WM_LBUTTONDOWN, winapi.WM_LBUTTONUP,
             winapi.WM_LBUTTONDBLCLK, winapi.MK_LBUTTON),
    "right": (winapi.WM_RBUTTONDOWN, winapi.WM_RBUTTONUP,
              winapi.WM_RBUTTONDBLCLK, winapi.MK_RBUTTON),
    "middle": (winapi.WM_MBUTTONDOWN, winapi.WM_MBUTTONUP,
               winapi.WM_MBUTTONDBLCLK, winapi.MK_MBUTTON),
}

#: 滚动方向 → (滚轮标志, 方向系数)
_SCROLL_FLAGS: dict[str, tuple[int, int]] = {
    "up": (winapi.MOUSEEVENTF_WHEEL, 1),
    "down": (winapi.MOUSEEVENTF_WHEEL, -1),
    "right": (winapi.MOUSEEVENTF_HWHEEL, 1),
    "left": (winapi.MOUSEEVENTF_HWHEEL, -1),
}

#: 修饰键 → Windows 虚拟键码
_MODIFIER_VKS: dict[str, int] = {
    "ctrl": winapi.VK_CONTROL,
    "alt": winapi.VK_MENU,
    "shift": winapi.VK_SHIFT,
    "meta": winapi.VK_LWIN,
}

#: 修饰键 → PostMessage 的 MK_* 状态位
_MODIFIER_MK: dict[str, int] = {
    "ctrl": winapi.MK_CONTROL,
    "shift": winapi.MK_SHIFT,
}

#: 消息投递路径下需补发 WM_CHAR 的非字符键 → 其产生的字符。
#: 编辑框等控件靠 WM_CHAR 插入文本：Space 只发 WM_KEYDOWN 不会插入空格；
#: Enter / Backspace 由控件在 WM_KEY* 中处理，补发 WM_CHAR 反而会重复生效。
_MESSAGE_CHAR_KEYS: dict[str, str] = {
    "space": " ",
    "tab": "\t",
}

#: WM_KEYUP / WM_SYSKEYUP 的 lParam：重复次数 1 + previous / transition 位置位
_KEYUP_LPARAM = 1 | (1 << 30) | (1 << 31)


@dataclass
class _TargetWindow:
    """定位到的目标窗口（句柄 + 截图坐标系）。"""

    handle: int
    pid: int
    title: str
    frame: WindowFrame
    #: 属主窗口句柄（弹层通常是它所属的主窗口；无属主 = 0）。工具窗口不会
    #: 成为前台窗口，判断「所属应用是否持有焦点」需要沿属主上溯。
    owner_handle: int = 0
    #: 是否为工具窗口（右键菜单 / 下拉浮层：不参与前台切换）
    tool_window: bool = False


@dataclass
class _MessagePoint:
    """PostMessage 投递点：实际目标窗口 + 客户区坐标 + 屏幕坐标。"""

    handle: int
    client_x: int
    client_y: int
    screen_x: int
    screen_y: int


# ── Win32 输入驱动（后端唯一直接接触系统 API 之处） ──────

class Win32Driver:
    """SendInput / PostMessage 原语封装（可被测试替身替换）。"""

    name = "sendinput"

    def is_foreground(self, handle) -> bool:
        return winapi.is_foreground(handle)

    def activate(self, handle) -> bool:
        """把窗口激活为前台（AttachThreadInput 技巧 + 常规提升）。"""
        return winapi.set_foreground(handle)

    def child_at(self, handle, screen_x: int, screen_y: int) -> int:
        """屏幕点下属于该窗口树的最深窗口（子控件优先，用于消息投递）。"""
        return winapi.hit_test_window(handle, screen_x, screen_y)

    def move_to(self, screen_x: int, screen_y: int) -> None:
        """把系统光标移动到屏幕坐标（绝对定位，多显示器安全）。"""
        nx, ny = winapi.normalize_absolute(screen_x, screen_y)
        flags = (winapi.MOUSEEVENTF_MOVE | winapi.MOUSEEVENTF_ABSOLUTE
                 | winapi.MOUSEEVENTF_VIRTUALDESK)
        self._send([winapi.mouse_input(flags, nx, ny)])

    def mouse_event(self, flags: int, data: int = 0) -> None:
        """发送鼠标按键/滚轮事件（坐标沿用当前光标位置）。"""
        self._send([winapi.mouse_input(flags, 0, 0, data)])

    def key_event(self, vk: int, *, key_up: bool) -> None:
        """发送键盘按下/抬起事件（虚拟键码 + 扫描码）。

        一并带上 ``MapVirtualKeyW`` 解析出的扫描码：浏览器、游戏与
        DirectInput 程序依赖扫描码还原物理键位（如 Web 页面的
        ``KeyboardEvent.code``），只发虚拟键码时这些程序拿不到键位信息。
        """
        flags = winapi.KEYEVENTF_KEYUP if key_up else 0
        scan = winapi.map_virtual_key(vk)
        if scan >= 0xE000:
            scan &= 0xFF
            flags |= winapi.KEYEVENTF_EXTENDEDKEY
        elif vk in _EXTENDED_VKS:
            flags |= winapi.KEYEVENTF_EXTENDEDKEY
        self._send([winapi.key_input(vk=vk, scan=scan, flags=flags)])

    def unicode_event(self, code_unit: int, *, key_up: bool) -> None:
        """发送 Unicode 文本输入事件（不依赖键盘布局）。"""
        self._send([winapi.unicode_key_input(code_unit, key_up=key_up)])

    def post(self, handle, msg: int, wparam: int = 0, lparam: int = 0) -> bool:
        return winapi.post_message(handle, msg, wparam, lparam)

    def client_origin(self, handle) -> tuple[int, int] | None:
        return winapi.client_origin(handle)

    def topmost_at(self, screen_x: int, screen_y: int) -> int:
        """屏幕点下最顶层的窗口句柄（判断真实光标会落到哪个窗口）。

        用于「目标窗口不是前台、但光标位置最上层就是它」的场景：此时合成
        鼠标事件仍能命中它，无需退回消息投递。
        """
        return winapi.window_from_point(screen_x, screen_y)

    def foreground_in_tree(self, pid: int) -> int:
        """同进程树内当前的前台窗口句柄（无则 0）。

        弹层窗口（tool window）自身不会成为前台，但它的应用往往有别的窗口
        （主窗口）正持有焦点——据此仍可用合成输入投递。探测失败按「没有」
        处理（调用方会退回消息投递，不会因此报错）。
        """
        try:
            window_pids = resolve_window_pids(pid)
            if not window_pids:
                return 0
            for info in enumerate_window_infos(window_pids):
                if info.foreground:
                    return winapi.hwnd_value(info.handle)
        except (OSError, ValueError, RuntimeError) as exc:  # pragma: no cover - 依赖系统调用
            logger.debug("同进程树前台窗口探测失败: %s", exc)
        return 0

    def sleep(self, seconds: float) -> None:
        if seconds > 0:
            time.sleep(seconds)

    @staticmethod
    def _send(items: list) -> None:
        """投递一批 SendInput 事件（完全未投递时短期重试）。

        ``SendInput`` 返回「已插入输入流的事件数」：等于总数即成功；返回 0
        表示整批都没进队列（UIPI 拦截 / 输入桌面忙碌等瞬时原因），短期重试
        通常即可成功；返回部分值意味着已有事件生效，重复投递会产生重复按键
        或悬空按下，因此不做重试、直接如实报错。
        """
        total = len(items)
        for attempt in range(_SENDINPUT_ATTEMPTS):
            sent = winapi.send_inputs(items)
            if sent == total:
                return
            if sent > 0:
                raise InputError(
                    f"SendInput 仅投递 {sent}/{total} 个事件（部分已生效，为免"
                    f"重复按键不做重试）；系统可能因权限（UIPI）或输入桌面"
                    f"限制拒绝注入"
                )
            if attempt + 1 < _SENDINPUT_ATTEMPTS:
                time.sleep(_SENDINPUT_RETRY_INTERVAL)
        raise InputError(
            f"SendInput 未能投递任何事件（0/{total}，已重试 "
            f"{_SENDINPUT_ATTEMPTS} 次）；系统可能因权限（UIPI）或输入桌面"
            f"限制拒绝注入"
        )


class WindowsInputBackend:
    """Windows 平台窗口输入后端。"""

    name = "windows"

    def __init__(self, locator: Callable[[int], _TargetWindow | None] | None = None,
                 driver: Win32Driver | None = None):
        self._locate = locator or locate_window
        self._driver = driver or Win32Driver()
        #: 消息投递路径下「最近一次交互控件」缓存（root 句柄 → 子控件句柄）：
        #: Edit/Button 等控件只接收发给自身的 WM_CHAR/WM_KEY*，键盘类动作须
        #: 复用鼠标命中过的控件；跨调用保留由后端实例（注册表单例）承载。
        self._message_key_targets: dict[int, int] = {}

    def supports(self) -> bool:
        return winapi.is_windows_platform()

    def locate(self, pid: int, window: str | None = None) -> _TargetWindow | None:
        """定位 ``pid``（含子进程）的目标窗口（无副作用；供路由决策与注入复用）。

        ``window`` 为窗口选择器（``main`` / ``active`` / ``#1`` / ``popup`` 等，
        见 ``_screenshot.windows``）；缺省取主窗口。选择器无匹配时抛
        ``SelectorError``；进程树内没有窗口时返回 ``None``。
        """
        winapi.ensure_process_dpi_aware()
        if window:
            return self._locate(pid, window)
        return self._locate(pid)

    def send(self, pid: int, action: InputAction) -> InputResult:
        """向 ``pid`` 的目标窗口注入 ``action``，返回注入结果。

        目标窗口取 ``action.window`` 指定的选择器；为空时用主窗口。
        """
        target = self.locate(pid, getattr(action, "window", "") or None)
        if target is None:
            raise NoWindowError(
                f"进程 {pid} 及其子进程没有可接收输入的可见窗口"
                f"（纯命令行进程无 GUI 窗口；窗口已最小化/被隐藏时也找不到）"
            )
        delivery = self._delivery_for(target, action)
        detail, delivery = self._inject(target, action, delivery)
        detail["delivery"] = delivery
        self._annotate_delivery_target(detail, target, delivery)
        warning = self._delivery_warning(action, delivery)
        if warning:
            detail.setdefault("warning", warning)
        return InputResult(
            action=action.name,
            backend=self.name,
            window_pid=target.pid,
            window_title=target.title,
            detail=detail,
            window_selector=getattr(action, "window", "") or DEFAULT_SELECTOR,
            window_handle=_window_handle_text(target.handle),
            window_frame=target.frame.to_dict(),
        )

    def _annotate_delivery_target(self, detail: dict, target: _TargetWindow,
                                  delivery: str) -> None:
        """补充「实际接收输入的窗口」信息。

        弹层 / 工具窗口不会被激活成前台，合成输入实际由同一应用的前台窗口
        接收；把该窗口一并回报，调用方才能解释「为什么目标不是前台却仍成功」。
        """
        if delivery != "sendinput":
            return
        foreground = self._application_foreground_handle(target)
        if foreground and foreground != winapi.hwnd_value(target.handle):
            detail["foreground_window"] = f"0x{foreground:X}"

    @staticmethod
    def _delivery_warning(action: InputAction, delivery: str) -> str | None:
        """PostMessage 回退通道对键盘 / 文本动作的可用性提示。

        键盘消息只有真正处理 ``WM_KEY*`` / ``WM_CHAR`` 的程序（经典 Win32
        控件、对话框）才会响应；Chrome、Electron、游戏等自绘界面通常忽略
        该通道。这里给出提示，避免调用方误以为按键一定生效。
        """
        if delivery != "message" or not isinstance(action, (KeyAction, TextAction)):
            return None
        return (
            "键盘 / 文本走 PostMessage 回退通道：只有处理 WM_KEY*/WM_CHAR 的"
            "程序（经典 Win32 控件、对话框）会响应；Chrome、Electron、游戏等"
            "自绘界面通常忽略该通道。若目标无响应，请让目标窗口取得前台后"
            "用 method='sendinput' 重试"
        )

    def _inject(self, target: _TargetWindow, action: InputAction,
                delivery: str) -> tuple[dict, str]:
        """按投递方式注入，返回 ``(细节, 实际使用的投递方式)``。

        auto 模式下 SendInput 失败（如目标进程权限更高被 UIPI 拒绝）时自动
        回退 PostMessage 投递，并在结果中如实反映实际投递方式。
        """
        if delivery == "message":
            return self._inject_message(target, action), "message"
        try:
            return self._inject_sendinput(target, action), "sendinput"
        except InputError as exc:
            if action.method != "auto":
                raise
            logger.debug("SendInput 注入失败，回退 PostMessage 投递: %s", exc)
            return self._inject_message(target, action), "message"

    # ── 投递方式决策 ─────────────────────────────────────

    #: 可用「真实光标」投递的动作：鼠标事件按屏幕坐标命中光标下的窗口，
    #: 不要求目标窗口是前台（光标处最顶层的窗口就会收到事件）。
    _POINTER_ACTIONS = (ClickAction, MoveAction, DragAction, ScrollAction)

    def _delivery_for(self, target: _TargetWindow, action: InputAction) -> str:
        if action.method == "message":
            return "message"
        if (self._application_foreground_handle(target)
                or self._ensure_foreground(target)):
            return "sendinput"
        if action.method == "sendinput":
            raise InputError(self._foreground_error(
                target, "SendInput 合成的是系统级输入事件，只作用于前台窗口"))
        # auto：整个应用都不在前台 —— 若真实光标在该坐标命中的就是目标窗口
        # （弹层浮在最上层），合成鼠标事件仍能投递；否则退回消息投递。
        if self._pointer_reachable(target, action):
            logger.debug("应用不在前台，但光标位置命中目标窗口，仍用 SendInput 投递")
            return "sendinput"
        logger.debug("窗口未取得前台且光标不可达，回退 PostMessage 投递")
        return "message"

    def _foreground_error(self, target: _TargetWindow, purpose: str) -> str:
        """「拿不到前台」的统一错误文案（含抢占者与下一步建议）。

        ``purpose`` 说明当前动作为什么需要前台（鼠标类 = 系统级事件只作用于
        前台窗口；键盘类 = 键盘事件只被前台窗口接收）。
        """
        return (
            f"无法把窗口 {target.title or target.handle} 置于前台"
            f"（当前前台窗口: {_foreground_description()}）。"
            f"{purpose}；可改用 method='message' 直接投递窗口消息"
            f"（Chrome / Electron / 游戏等自绘界面通常忽略该通道），"
            f"或先让目标窗口获得焦点（点击其窗口区域、或关闭 / 最小化持续"
            f"抢占前台的其它程序）后重试"
        )

    def _application_foreground_handle(self, target: _TargetWindow) -> int:
        """目标窗口**所属应用**当前的前台窗口句柄（0 = 整个应用都不在前台）。

        弹层（tool window / 右键菜单 / 下拉浮层）不会成为前台窗口，直接判断
        ``IsForegroundWindow(target)`` 永远为假；这里按应用维度判定：目标窗口
        自身 → 其属主窗口 → 同进程树内任一前台窗口，依次尝试。任一命中即说明
        该应用持有输入焦点，合成输入可以投递。
        """
        if self._driver.is_foreground(target.handle):
            return winapi.hwnd_value(target.handle)
        owner = winapi.hwnd_value(target.owner_handle)
        if owner and self._driver.is_foreground(owner):
            return owner
        finder = getattr(self._driver, "foreground_in_tree", None)
        if finder is not None:
            found = finder(target.pid)
            if found:
                return winapi.hwnd_value(found)
        return 0

    def _ensure_foreground(self, target: _TargetWindow) -> bool:
        """确保目标窗口（或其所属应用）持有前台（多轮激活 + 递增等待）。

        Windows 前台锁定策略会拒绝后台进程的首次 ``SetForegroundWindow``
        （刚启动、被最小化或系统正忙时尤其明显），因此按
        ``_FOREGROUND_ATTEMPTS`` 轮重复激活，每轮后等待递增的时间再复核，
        尽量在有限时间内拿到前台。

        工具窗口无法成为前台，激活时按其属主（主窗口）尝试；只要应用取得
        焦点（属主 / 同进程树窗口是前台）即视为成功。
        """
        if self._application_foreground_handle(target):
            return True
        for attempt in range(_FOREGROUND_ATTEMPTS):
            self._activate_target(target)
            self._driver.sleep(_foreground_settle(attempt))
            if self._application_foreground_handle(target):
                return True
        return False

    def _activate_target(self, target: _TargetWindow) -> None:
        """激活目标窗口；工具窗口（弹层）优先激活其属主（不参与前台切换）。

        弹层调用 ``SetForegroundWindow`` 必定失败，先激活其属主（该应用的主
        窗口）才有意义；随后仍尝试激活目标窗口自身，作为非工具窗口与属主
        缺失场景的兜底（重复激活同一窗口是幂等的）。
        """
        owner = winapi.hwnd_value(target.owner_handle)
        if target.tool_window and owner:
            self._driver.activate(owner)
        self._driver.activate(target.handle)

    def _pointer_reachable(self, target: _TargetWindow, action: InputAction) -> bool:
        """真实光标是否正好落在目标窗口上（是则鼠标事件无需前台也能命中）。

        仅当屏幕点下最顶层的窗口就是目标窗口（或其子窗口）时才成立；否则
        点击会被遮挡窗口吃掉，必须老实用消息投递。
        """
        if not isinstance(action, self._POINTER_ACTIONS):
            return False
        topmost = getattr(self._driver, "topmost_at", None)
        if topmost is None:
            return False
        point = self._pointer_point(target.frame, action)
        if point is None:
            return False
        screen_x, screen_y = target.frame.to_screen(point)
        hit = winapi.hwnd_value(topmost(screen_x, screen_y))
        if not hit:
            return False
        expected = winapi.hwnd_value(target.handle)
        return hit == expected or winapi.window_root(hit) == expected

    def _pointer_point(self, frame: WindowFrame,
                       action: InputAction) -> Point | None:
        """取动作的「落点」窗口内坐标（拖动取起点，其余取点击 / 移动点）。"""
        if isinstance(action, DragAction):
            return resolve_point(action.from_x, action.from_y,
                                 frame.width, frame.height, label="拖动起点")
        if isinstance(action, (ClickAction, MoveAction, ScrollAction)):
            return resolve_point(action.x, action.y,
                                 frame.width, frame.height, label="鼠标坐标")
        return None

    # ── SendInput 路径 ───────────────────────────────────

    def _inject_sendinput(self, target: _TargetWindow, action: InputAction) -> dict:
        frame = target.frame
        if isinstance(action, ClickAction):
            return self._click_sendinput(frame, action)
        if isinstance(action, MoveAction):
            return self._move_sendinput(frame, action)
        if isinstance(action, DragAction):
            return self._drag_sendinput(frame, action)
        if isinstance(action, ScrollAction):
            return self._scroll_sendinput(frame, action)
        if isinstance(action, KeyAction):
            return self._inject_keyboard(
                target, lambda: self._key_sendinput(action))
        if isinstance(action, TextAction):
            return self._inject_keyboard(
                target, lambda: self._type_sendinput(action))
        raise ActionError(f"Windows 后端不支持的动作: {action.name}")  # pragma: no cover

    def _move_sendinput(self, frame: WindowFrame, action: MoveAction) -> dict:
        point = resolve_point(action.x, action.y, frame.width, frame.height,
                              label="移动坐标")
        screen = frame.to_screen(point)
        self._driver.move_to(*screen)
        return self._point_detail(point, screen)

    def _click_sendinput(self, frame: WindowFrame, action: ClickAction) -> dict:
        point = resolve_point(action.x, action.y, frame.width, frame.height,
                              label="点击坐标")
        screen = frame.to_screen(point)
        down, up = _BUTTON_FLAGS[action.button]
        self._driver.move_to(*screen)
        with self._hold_modifiers(action.modifiers):
            for index in range(action.count):
                if index:
                    self._driver.sleep(_DOUBLE_CLICK_INTERVAL)
                self._driver.mouse_event(down)
                self._driver.mouse_event(up)
        detail = self._point_detail(point, screen)
        detail.update({"button": action.button, "count": action.count})
        return detail

    def _drag_sendinput(self, frame: WindowFrame, action: DragAction) -> dict:
        start = resolve_point(action.from_x, action.from_y, frame.width, frame.height,
                              label="拖动起点")
        end = validate_point(Point(action.to_x, action.to_y), frame.width, frame.height,
                             label="拖动终点")
        start_screen = frame.to_screen(start)
        end_screen = frame.to_screen(end)
        down, up = _BUTTON_FLAGS[action.button]
        self._driver.move_to(*start_screen)
        interval = self._drag_interval(action)
        with self._hold_modifiers(action.modifiers):
            self._driver.mouse_event(down)
            for point in interpolate(start, end, action.steps):
                self._driver.move_to(*frame.to_screen(point))
                if interval:
                    self._driver.sleep(interval)
            self._driver.move_to(*end_screen)
            self._driver.mouse_event(up)
        detail = self._point_detail(start, start_screen)
        detail.update({
            "button": action.button,
            "duration": action.duration,
            "steps": action.steps,
            "to_x": end.x,
            "to_y": end.y,
            "to_screen_x": end_screen[0],
            "to_screen_y": end_screen[1],
        })
        return detail

    def _drag_interval(self, action: DragAction) -> float:
        """拖动轨迹每步间隔：duration 为 0 时退化为最小间隔。"""
        if action.duration <= 0:
            return _DRAG_MIN_INTERVAL
        return max(action.duration / max(action.steps, 1), _DRAG_MIN_INTERVAL)

    def _scroll_sendinput(self, frame: WindowFrame, action: ScrollAction) -> dict:
        point = resolve_point(action.x, action.y, frame.width, frame.height,
                              label="滚动坐标")
        screen = frame.to_screen(point)
        flags, sign = _SCROLL_FLAGS[action.direction]
        data = sign * action.amount * winapi.WHEEL_DELTA
        self._driver.move_to(*screen)
        with self._hold_modifiers(action.modifiers):
            self._driver.mouse_event(flags, data)
        detail = self._point_detail(point, screen)
        detail.update({"direction": action.direction, "amount": action.amount})
        return detail

    def _inject_keyboard(self, target: _TargetWindow,
                         inject: Callable[[], dict]) -> dict:
        """键盘 / 文本注入的前后台确保与焦点复核。

        SendInput 的键盘事件只会被**前台**窗口接收：注入前先把目标窗口（或
        其所属应用）置前；注入后再复核前台，若焦点已被别的窗口抢走（注入
        瞬间被切换），说明按键大概率落到了别处，于是重新激活并重发（最多
        ``_KEYBOARD_FOCUS_ATTEMPTS`` 次），仍失败则附带告警而不静默。

        弹层（右键菜单 / 下拉浮层）自身不会成为前台，此时按键由**同一应用的
        前台窗口**接收（浏览器的渲染进程会正常处理），结果里用
        ``keyboard_window`` 如实标注实际接收窗口。
        """
        if not (self._application_foreground_handle(target)
                or self._ensure_foreground(target)):
            raise InputError(self._foreground_error(
                target, "SendInput 的键盘事件只被前台窗口接收，无法定向注入"))
        detail = inject()
        for _attempt in range(_KEYBOARD_FOCUS_ATTEMPTS):
            handle = self._application_foreground_handle(target)
            if handle:
                return self._annotate_keyboard_window(detail, target, handle)
            logger.debug("键盘注入后目标窗口失去前台，重新激活并重发按键")
            if not self._ensure_foreground(target):
                break
            detail = inject()
        handle = self._application_foreground_handle(target)
        if handle:
            return self._annotate_keyboard_window(detail, target, handle)
        detail["focus_warning"] = (
            "按键注入后目标窗口未保持前台，按键可能被其它窗口接收；"
            "可先让目标窗口取得焦点（例如点击其窗口区域）后重试"
        )
        return detail

    @staticmethod
    def _annotate_keyboard_window(detail: dict, target: _TargetWindow,
                                  handle: int) -> dict:
        """记录键盘事件实际投递到的前台窗口（弹层场景下与目标窗口不同）。"""
        if handle and handle != winapi.hwnd_value(target.handle):
            detail["keyboard_window"] = f"0x{handle:X}"
            detail["keyboard_via"] = (
                "目标窗口是弹层 / 工具窗口，不参与前台切换；按键发送给同一应用的"
                "前台窗口（该应用的渲染进程通常会处理，如浏览器关闭下拉浮层）"
            )
        return detail

    def _key_sendinput(self, action: KeyAction) -> dict:
        vk, implicit = resolve_windows_vk(action.shortcut.key)
        modifiers = self._merged_modifiers(action.shortcut.modifiers, implicit)
        vks = [_MODIFIER_VKS[name] for name in modifiers]
        repeats = action.effective_repeat
        for index in range(repeats):
            if index:
                self._driver.sleep(_KEY_REPEAT_INTERVAL)
            self._press_shortcut(vks, vk, action.phase)
        return {
            "key": action.shortcut.display(),
            "vk": vk,
            "modifiers": list(modifiers),
            "phase": action.phase,
            "repeat": repeats,
        }

    def _press_shortcut(self, vks: list[int], vk: int, phase: str) -> None:
        """发送一次完整按键（含修饰键的按下与逆序释放）。"""
        if phase == "down":
            for name_vk in vks:
                self._driver.key_event(name_vk, key_up=False)
            self._driver.key_event(vk, key_up=False)
        elif phase == "up":
            self._driver.key_event(vk, key_up=True)
            for name_vk in reversed(vks):
                self._driver.key_event(name_vk, key_up=True)
        else:  # press：按住修饰键 → 主键按下/弹起 → 逆序释放修饰键
            for name_vk in vks:
                self._driver.key_event(name_vk, key_up=False)
            try:
                self._driver.key_event(vk, key_up=False)
                self._driver.key_event(vk, key_up=True)
            finally:
                for name_vk in reversed(vks):
                    self._driver.key_event(name_vk, key_up=True)

    def _type_sendinput(self, action: TextAction) -> dict:
        characters = 0
        for char in action.text:
            if char == "\r":
                continue
            if char == "\n":
                self._press_vk(winapi.VK_RETURN)
            elif char == "\t":
                self._press_vk(winapi.VK_TAB)
            else:
                for unit in utf16_units(char):
                    self._driver.unicode_event(unit, key_up=False)
                    self._driver.unicode_event(unit, key_up=True)
            characters += 1
        return {"text": action.text, "characters": characters}

    def _press_vk(self, vk: int) -> None:
        self._driver.key_event(vk, key_up=False)
        self._driver.key_event(vk, key_up=True)

    @contextmanager
    def _hold_modifiers(self, modifiers: tuple[str, ...]) -> Iterator[None]:
        """注入期间按住修饰键（结束按逆序释放）。"""
        vks = [_MODIFIER_VKS[name] for name in modifiers if name in _MODIFIER_VKS]
        for vk in vks:
            self._driver.key_event(vk, key_up=False)
        try:
            yield
        finally:
            for vk in reversed(vks):
                self._driver.key_event(vk, key_up=True)

    @staticmethod
    def _merged_modifiers(explicit: tuple[str, ...],
                          implicit: set[str]) -> tuple[str, ...]:
        """显式修饰键与字符键隐含的修饰键合并（去重、固定顺序）。"""
        names = list(explicit) + [name for name in implicit if name not in explicit]
        return tuple(name for name in MODIFIER_ORDER if name in names)

    @staticmethod
    def _point_detail(point: Point, screen: tuple[int, int]) -> dict:
        return {
            "x": point.x,
            "y": point.y,
            "screen_x": screen[0],
            "screen_y": screen[1],
        }

    # ── PostMessage 路径 ─────────────────────────────────

    def _inject_message(self, target: _TargetWindow, action: InputAction) -> dict:
        if isinstance(action, ClickAction):
            return self._click_message(target, action)
        if isinstance(action, MoveAction):
            return self._move_message(target, action)
        if isinstance(action, DragAction):
            return self._drag_message(target, action)
        if isinstance(action, ScrollAction):
            return self._scroll_message(target, action)
        if isinstance(action, KeyAction):
            return self._key_message(target, action)
        if isinstance(action, TextAction):
            return self._type_message(target, action)
        raise ActionError(f"Windows 后端不支持的动作: {action.name}")  # pragma: no cover

    def _message_point(self, target: _TargetWindow, point: Point) -> _MessagePoint:
        """窗口内坐标 → 命中控件 + 客户区坐标 + 屏幕坐标。

        鼠标消息必须投递给**真正位于该点下的子控件**（Edit / Button 等），
        否则对话框类程序不会响应，因此这里先做命中测试再换算坐标。
        """
        screen = target.frame.to_screen(point)
        handle = self._driver.child_at(target.handle, screen[0], screen[1])
        origin = self._driver.client_origin(handle)
        if origin is None:
            raise _no_client_area_error(handle)
        self._remember_key_target(target, handle)
        return _MessagePoint(
            handle=handle,
            client_x=screen[0] - origin[0],
            client_y=screen[1] - origin[1],
            screen_x=screen[0],
            screen_y=screen[1],
        )

    def _remember_key_target(self, target: _TargetWindow, handle: int) -> None:
        """记录消息投递路径下最近交互的控件（键盘类动作用它作目标）。"""
        self._message_key_targets[winapi.hwnd_value(target.handle)] = handle

    def _keyboard_target(self, target: _TargetWindow) -> int:
        """消息投递路径下键盘动作的目标窗口：最近交互控件优先，否则顶层窗口。"""
        return self._message_key_targets.get(
            winapi.hwnd_value(target.handle), target.handle)

    def _move_message(self, target: _TargetWindow, action: MoveAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="移动坐标")
        hit = self._message_point(target, point)
        self._post_move(hit, 0)
        detail = self._point_detail(point, (hit.screen_x, hit.screen_y))
        detail.update({"client_x": hit.client_x, "client_y": hit.client_y,
                       "target_handle": hit.handle})
        return detail

    def _client_coords(self, handle: int, screen_x: int,
                       screen_y: int) -> tuple[int, int]:
        """屏幕坐标 → 指定窗口的客户区坐标。"""
        origin = self._driver.client_origin(handle)
        if origin is None:
            raise _no_client_area_error(handle)
        return screen_x - origin[0], screen_y - origin[1]

    def _click_message(self, target: _TargetWindow, action: ClickAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="点击坐标")
        hit = self._message_point(target, point)
        down_msg, up_msg, dbl_msg, button_mask = _BUTTON_MESSAGES[action.button]
        lparam = _make_lparam(hit.client_x, hit.client_y)
        self._post_move(hit, 0)
        self._post_modifier_keys(hit.handle, action.modifiers, key_up=False)
        try:
            for index in range(action.count):
                if index:
                    self._driver.sleep(_DOUBLE_CLICK_INTERVAL)
                self._driver.post(hit.handle,
                                  dbl_msg if index == 1 else down_msg,
                                  button_mask, lparam)
                self._driver.post(hit.handle, up_msg, 0, lparam)
        finally:
            self._post_modifier_keys(hit.handle, action.modifiers, key_up=True)
        detail = self._point_detail(point, (hit.screen_x, hit.screen_y))
        detail.update({"button": action.button, "count": action.count,
                       "client_x": hit.client_x, "client_y": hit.client_y,
                       "target_handle": hit.handle})
        return detail

    def _drag_message(self, target: _TargetWindow, action: DragAction) -> dict:
        frame = target.frame
        start = resolve_point(action.from_x, action.from_y, frame.width, frame.height,
                              label="拖动起点")
        end = validate_point(Point(action.to_x, action.to_y), frame.width, frame.height,
                             label="拖动终点")
        start_hit = self._message_point(target, start)
        handle = start_hit.handle  # 按下后按钮消息保持同一控件
        end_x, end_y = self._client_coords(handle, *frame.to_screen(end))
        down_msg, up_msg, _dbl, button_mask = _BUTTON_MESSAGES[action.button]
        self._post_move(start_hit, 0)
        self._post_modifier_keys(handle, action.modifiers, key_up=False)
        try:
            self._driver.post(handle, down_msg, button_mask,
                              _make_lparam(start_hit.client_x, start_hit.client_y))
            interval = self._drag_interval(action)
            for point in interpolate(start, end, action.steps):
                client_x, client_y = self._client_coords(handle, *frame.to_screen(point))
                self._driver.post(handle, winapi.WM_MOUSEMOVE, button_mask,
                                  _make_lparam(client_x, client_y))
                if interval:
                    self._driver.sleep(interval)
            self._driver.post(handle, up_msg, 0, _make_lparam(end_x, end_y))
        finally:
            self._post_modifier_keys(handle, action.modifiers, key_up=True)
        detail = self._point_detail(start, (start_hit.screen_x, start_hit.screen_y))
        detail.update({
            "button": action.button,
            "duration": action.duration,
            "steps": action.steps,
            "to_x": end.x,
            "to_y": end.y,
            "to_client_x": end_x,
            "to_client_y": end_y,
            "target_handle": handle,
        })
        return detail

    def _scroll_message(self, target: _TargetWindow, action: ScrollAction) -> dict:
        point = resolve_point(action.x, action.y, target.frame.width,
                              target.frame.height, label="滚动坐标")
        hit = self._message_point(target, point)
        _flags, sign = _SCROLL_FLAGS[action.direction]
        delta = sign * action.amount * winapi.WHEEL_DELTA
        # WM_MOUSEWHEEL 的 lParam 按 Windows 规定是**屏幕坐标**
        message = (winapi.WM_MOUSEWHEEL if action.direction in ("up", "down")
                   else winapi.WM_MOUSEHWHEEL)
        wparam = ((delta & 0xFFFF) << 16) | self._modifier_state(action.modifiers)
        self._post_move(hit, 0)
        self._driver.post(hit.handle, message, wparam,
                          _make_lparam(hit.screen_x, hit.screen_y))
        detail = self._point_detail(point, (hit.screen_x, hit.screen_y))
        detail.update({"direction": action.direction, "amount": action.amount,
                       "target_handle": hit.handle})
        return detail

    def _key_message(self, target: _TargetWindow, action: KeyAction) -> dict:
        handle = self._keyboard_target(target)
        vk, implicit = resolve_windows_vk(action.shortcut.key)
        modifiers = self._merged_modifiers(action.shortcut.modifiers, implicit)
        # Alt 组合键走系统按键消息（WM_SYSKEYDOWN/WM_SYSKEYUP），否则菜单
        # 加速键等只处理系统消息的程序收不到按下/弹起。
        down_msg, up_msg = _key_message_types(modifiers)
        vks = [_MODIFIER_VKS[name] for name in modifiers]
        repeats = action.effective_repeat
        for index in range(repeats):
            if index:
                self._driver.sleep(_KEY_REPEAT_INTERVAL)
            if action.phase in ("press", "down"):
                for modifier_vk in vks:
                    self._driver.post(handle, down_msg, modifier_vk, 1)
                self._driver.post(handle, down_msg, vk, 1)
                for unit in _message_char_units(action.shortcut, modifiers):
                    self._driver.post(handle, winapi.WM_CHAR, unit, 1)
            if action.phase in ("press", "up"):
                self._driver.post(handle, up_msg, vk, _KEYUP_LPARAM)
                for modifier_vk in reversed(vks):
                    self._driver.post(handle, up_msg, modifier_vk, _KEYUP_LPARAM)
        return {
            "key": action.shortcut.display(),
            "vk": vk,
            "modifiers": list(modifiers),
            "phase": action.phase,
            "target_handle": handle,
            "repeat": repeats,
        }

    def _type_message(self, target: _TargetWindow, action: TextAction) -> dict:
        handle = self._keyboard_target(target)
        characters = 0
        for char in action.text:
            if char == "\r":
                continue
            if char == "\n":
                self._press_key_message(handle, winapi.VK_RETURN, 0x0D)
            elif char == "\t":
                self._press_key_message(handle, winapi.VK_TAB, 0x09)
            else:
                self._type_char_message(handle, char)
            characters += 1
        return {"text": action.text, "characters": characters,
                "target_handle": handle}

    def _type_char_message(self, handle: int, char: str) -> None:
        """消息投递路径输入一个字符：按下 → 字符（可多个码元）→ 弹起。

        能映射到虚拟键的字符同时发送配对的按下/弹起按键消息（部分程序只
        监听按键消息、不处理 WM_CHAR）；无法映射（如中文）时只发 WM_CHAR，
        与 SendInput 路径的 Unicode 码元事件等价。
        """
        units = utf16_units(char)
        scanned = winapi.key_scan_code(char) if len(char) == 1 else None
        if scanned is None:
            for unit in units:
                self._driver.post(handle, winapi.WM_CHAR, unit, 1)
            return
        vk = scanned[0]
        self._driver.post(handle, winapi.WM_KEYDOWN, vk, 1)
        for unit in units:
            self._driver.post(handle, winapi.WM_CHAR, unit, 1)
        self._driver.post(handle, winapi.WM_KEYUP, vk, _KEYUP_LPARAM)

    def _press_key_message(self, handle: int, vk: int, char: int) -> None:
        """消息投递路径按一次功能键：按下 → 字符 → 弹起（成对通知程序）。"""
        self._driver.post(handle, winapi.WM_KEYDOWN, vk, 1)
        self._driver.post(handle, winapi.WM_CHAR, char, 1)
        self._driver.post(handle, winapi.WM_KEYUP, vk, _KEYUP_LPARAM)

    def _post_move(self, hit: _MessagePoint, state: int) -> None:
        self._driver.post(hit.handle, winapi.WM_MOUSEMOVE, state,
                          _make_lparam(hit.client_x, hit.client_y))

    def _post_modifier_keys(self, handle: int, modifiers: tuple[str, ...],
                            *, key_up: bool) -> None:
        message = winapi.WM_KEYUP if key_up else winapi.WM_KEYDOWN
        for name in modifiers:
            vk = _MODIFIER_VKS.get(name)
            if vk is None:
                continue
            lparam = 1 if not key_up else _KEYUP_LPARAM
            self._driver.post(handle, message, vk, lparam)

    @staticmethod
    def _modifier_state(modifiers: tuple[str, ...]) -> int:
        state = 0
        for name in modifiers:
            state |= _MODIFIER_MK.get(name, 0)
        return state


# ── 窗口定位与坐标换算（模块级，便于复用与单测） ──────────

def locate_window(pid: int, window: str | None = None) -> _TargetWindow | None:
    """定位 ``pid``（含子进程）的目标窗口，返回句柄与截图坐标系。

    Args:
        pid: 目标进程 PID（含子进程一起参与窗口匹配）。
        window: 窗口选择器（``main`` / ``active`` / ``#1`` / ``handle:0x…`` /
            ``title:子串`` / ``popup`` / ``dialog``）；``None`` = 主窗口。

    Raises:
        SelectorError: 选择器非法或没有匹配窗口（进程树内有窗口但选不中）。
    """
    winapi.ensure_process_dpi_aware()
    window_pids = resolve_window_pids(pid)
    if not window_pids:
        return None
    infos = enumerate_window_infos(window_pids)
    if not infos:
        return None
    candidate = pick_window(infos, window)
    return _TargetWindow(
        handle=candidate.handle,
        pid=candidate.pid,
        title=candidate.title,
        frame=frame_of(candidate),
        owner_handle=winapi.window_owner(candidate.handle),
        tool_window=bool(candidate.tool_window),
    )


def frame_of(candidate: WindowCandidate) -> WindowFrame:
    """候选窗口 → 截图坐标系（与 ``op=screenshot`` 去掉 DWM 黑边后的区域一致）。"""
    trim = visible_region(candidate)
    if trim is None:
        return WindowFrame(candidate.left, candidate.top,
                           candidate.width, candidate.height)
    return WindowFrame(candidate.left + trim.x, candidate.top + trim.y,
                       trim.width, trim.height)


def resolve_windows_vk(key: str) -> tuple[int, set[str]]:
    """把规范键名（或单字符）解析为 ``(虚拟键码, 隐含修饰键)``。

    Raises:
        InputError: 键名在当前键盘布局下无对应虚拟键码。
    """
    if len(key) == 1:
        scanned = winapi.key_scan_code(key)
        if scanned is None:
            raise InputError(
                f"字符 {key!r} 在当前键盘布局下无对应按键；输入文本请用 op=type"
            )
        vk, state = scanned
        implicit: set[str] = set()
        if state & 1:
            implicit.add("shift")
        if state & 2:
            implicit.add("ctrl")
        if state & 4:
            implicit.add("alt")
        return vk, implicit
    char_vk = WINDOWS_VK.get(key)
    if char_vk is None:
        raise InputError(f"Windows 后端不支持按键: {key!r}")
    return char_vk, set()


def _window_handle_text(handle) -> str:
    """窗口句柄的十六进制文本（无法取值时返回空串）。"""
    value = winapi.hwnd_value(handle)
    return f"0x{value:X}" if value else ""


def _foreground_description() -> str:
    """当前前台窗口的一行描述（读取失败时返回「未知」）。

    用于「无法把目标窗口置于前台」的错误提示：直接告诉调用方是哪个程序
    占着前台，避免在「窗口永远拿不到焦点」时无从下手。
    """
    return winapi.foreground_description()


def _foreground_settle(attempt: int) -> float:
    """第 ``attempt`` 轮前台激活后的等待时长（轮次越靠后等得越久）。"""
    steps = _FOREGROUND_SETTLE_STEPS
    return steps[min(max(attempt, 0), len(steps) - 1)]


def _no_client_area_error(handle) -> InputError:
    """窗口没有可换算客户区时的错误（面向大模型的下一步指引）。

    PostMessage 投递鼠标消息必须把屏幕坐标换算成客户区坐标；Chrome /
    Electron 的弹出层与部分自绘窗口没有标准客户区（``op=windows`` 里
    ``client_area=false``），此时消息投递不可行，应改走合成输入——真实光标
    按屏幕坐标命中窗口，不需要客户区坐标。
    """
    value = winapi.hwnd_value(handle)
    return InputError(
        f"窗口 0x{value:X} 没有可换算的客户区（ClientToScreen / GetClientRect "
        f"都不可用），无法用 PostMessage 换算鼠标坐标。这类窗口（Chrome / "
        f"Electron 弹出层、tool window、部分自绘界面）请改用合成输入：去掉 "
        f"method='message'（默认 method='auto' 会走 SendInput，按屏幕坐标命中"
        f"光标下的真实窗口），或直接用 op=windows 里的 client_area=true 的窗口"
        f"作为操作目标"
    )


def _make_lparam(x: int, y: int) -> int:
    """把客户区坐标打包为消息 lParam（低 16 位 x、高 16 位 y）。"""
    return ((int(y) & 0xFFFF) << 16) | (int(x) & 0xFFFF)


def _key_message_types(modifiers: tuple[str, ...]) -> tuple[int, int]:
    """键盘消息类型：Alt 组合走系统消息（WM_SYSKEYDOWN/WM_SYSKEYUP）。

    Windows 规定 Alt 参与的按键以系统按键消息投递；只处理系统消息的
    程序（菜单加速键等）在普通 WM_KEY* 下收不到按键。
    """
    if "alt" in modifiers:
        return winapi.WM_SYSKEYDOWN, winapi.WM_SYSKEYUP
    return winapi.WM_KEYDOWN, winapi.WM_KEYUP


def _message_char_units(shortcut: Shortcut,
                        modifiers: tuple[str, ...]) -> list[int]:
    """PostMessage 路径下主键应补发的 WM_CHAR 码元。

    Ctrl / Alt / Meta 组合键由程序自行解释，不产生字符；Shift 会改变字符键
    的输出（``a`` → ``A``、``1`` → ``!``），须按 Shift 后的字符发送；
    Space / Tab 等键名本身不携带字符，但编辑框依赖 WM_CHAR 插入，需要补发。
    """
    mod_set = set(modifiers)
    if mod_set & {"ctrl", "alt", "meta"}:
        return []
    if shortcut.is_character:
        char = shift_character(shortcut.key) if "shift" in mod_set else shortcut.key
        return utf16_units(char)
    char = _MESSAGE_CHAR_KEYS.get(shortcut.key)
    if char is None:
        return []
    if "shift" in mod_set:
        char = shift_character(char)
    return utf16_units(char)


__all__ = [
    "Win32Driver",
    "WindowsInputBackend",
    "frame_of",
    "locate_window",
    "resolve_windows_vk",
]
