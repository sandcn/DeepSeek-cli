"""Windows 窗口截图后端（原生 Windows / Cygwin / MSYS2 通用）。

窗口定位：目标进程 + 全部子进程（Cygwin 下先经 ``/proc/<pid>/winpid`` 把
POSIX PID 映射为 WINPID，再用 Toolhelp32 进程表补全 Windows 后代），
在其可见顶层窗口中选择「有标题、非工具窗口、面积最大」者。

像素获取：优先 ``PrintWindow(PW_RENDERFULLCONTENT)``——它渲染窗口自身
内容，即使窗口被遮挡或在后台也能拿到；硬件加速窗口（D3D/OpenGL 游戏）
常返回全黑占位图，此时回退 ``BitBlt`` 从窗口 DC 拷贝屏幕像素。

DPI：截图前把进程标记为 DPI 感知（``winapi.ensure_process_dpi_aware``），
使窗口几何与物理像素 1:1；否则高 DPI 显示器上窗口坐标被系统虚拟化缩小，
产物右下角会被裁掉。

黑边：``GetWindowRect`` 含 Win10 DWM 的不可见边框（阴影/缩放预留），直接
截图四边会出现黑边；按 ``DWMWA_EXTENDED_FRAME_BOUNDS``（
:func:`visible_region`）裁掉该边框后再输出。

编码：DIB 为 32 位 BGRA（自上而下），取 B/G/R 三通道直接写 PNG
（不依赖 Pillow）。

裁剪：``visible_region`` 去黑边后，``crop`` 在剩余像素上裁剪再编码
（不经过 PNG 解码）；``crop`` 坐标以去黑边后的整窗截图为原点。

可截图性校验：选定目标窗口后先经 :func:`~._screenshot.windows.require_capturable`
检查（最小化 / 隐藏窗口没有可渲染的客户区，截出来是与界面无关的占位小图），
命中时给出「先 restore 再截图」的可执行提示，而不是产出误导性的图片。
"""

from __future__ import annotations

import ctypes
import dataclasses
import logging
import time

from . import grid as grid_module
from . import png, proctree, transform, winapi
from .elements import ElementInfo
from .monitors import Monitor
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropError, CropRegion
from .windows import (
    DEFAULT_SELECTOR,
    WindowControlRequest,
    WindowInfo,
    indexed_summary,
    main_window,
    mark_main,
    pick_window,
    require_capturable,
    window_geometry,
)

logger = logging.getLogger(__name__)

#: 兼容别名：窗口候选就是通用窗口描述（选择规则在 ``windows`` 模块统一实现）
WindowCandidate = WindowInfo

#: ``WindowInfo`` 当前支持的字段名（运行时探测一次）。
#: 枚举窗口时据此只填充「当前定义确实存在」的扩展字段，避免与旧版窗口描述
#: （热更新 / 混部场景）组合使用时因未知关键字参数失败。
_WINDOW_INFO_FIELDS: frozenset[str] = frozenset(
    field.name for field in dataclasses.fields(WindowInfo))

#: 系统外壳窗口类（桌面 / 任务栏等）——即使属于目标进程也不作为截图目标
SHELL_WINDOW_CLASSES = frozenset({
    "Progman",
    "WorkerW",
    "Shell_TrayWnd",
    "Shell_SecondaryTrayWnd",
    "ForegroundStaging",
    "MultitaskingViewFrame",
    "XamlExplorerHostIslandWindow",
})

#: WINDOW 后代展开深度上限
_MAX_TREE_DEPTH = 32

#: 前置窗口后等待重绘的时间（秒）——BitBlt 回退前让画面刷新
_RAISE_SETTLE_SECONDS = 0.25

#: 窗口控制（移动 / 缩放 / 最大化等）后等待状态生效的时间（秒）
_CONTROL_SETTLE_SECONDS = 0.15


def select_window(candidates: list[WindowInfo]) -> WindowInfo | None:
    """从候选窗口中选择最可能的「主窗口」（保留自早期接口）。

    排序优先级：非工具窗口 > 未最小化 > 有标题 > 面积大 > 进程号小
    （后两项保证结果稳定）。选择规则现由 ``windows.main_window`` 统一实现，
    截图与输入注入共用同一套语义。
    """
    return main_window(candidates)


class WindowsBackend:
    """Windows 平台截图后端。"""

    name = "windows"

    def supports(self) -> bool:
        return winapi.is_windows_platform()

    def capture(self, pid: int, path: str,
                crop: CropRegion | None = None,
                window: str | None = None,
                grid: int | None = None) -> CaptureResult:
        """截取 ``pid``（及其子进程）的窗口到 ``path``（PNG）。

        Args:
            pid: 目标进程 PID（含子进程一起参与窗口匹配）。
            path: 输出 PNG 路径。
            crop: 可选裁剪区域（在内存 BGRA 上裁剪后再编码，无需解码 PNG）；
                区域越界抛 ``CropError``。
            window: 可选窗口选择器（见 ``windows`` 模块；缺省 = 主窗口）。
                选择器无匹配时抛 ``SelectorError``。
            grid: 可选坐标网格步长（像素，``0`` = 自动）；非空时在产物上
                叠加参考线，便于读图定位坐标。

        Raises:
            NoWindowError: 进程树内没有可截图的可见窗口。
            SelectorError: 窗口选择器非法或没有匹配窗口。
            CropError: 裁剪区域越界。
            ScreenshotError: 抓取或落盘失败。
        """
        # 高 DPI 显示器上必须先让进程感知 DPI：否则 GetWindowRect 返回被
        # 虚拟化缩小的坐标，而 PrintWindow/BitBlt 输出物理像素，按该尺寸
        # 建的内存 DC 装不下整窗，产物右下角会被裁掉。
        winapi.ensure_process_dpi_aware()
        window_pids = resolve_window_pids(pid)
        if not window_pids:
            raise NoWindowError(
                f"未能解析进程 {pid} 的 Windows 进程号（进程可能已退出或尚未就绪），无法截图"
            )
        candidates = enumerate_candidates(window_pids)
        if not candidates:
            raise NoWindowError(
                f"进程 {pid} 及其子进程没有可截图的可见窗口（纯命令行进程无 GUI 窗口；"
                f"窗口已最小化/被隐藏时也找不到）"
            )
        target = pick_window(candidates, window)
        require_capturable(target, window)
        bgra, width, height = capture_window_pixels(target)
        # 产物图左上角对应的屏幕坐标：先取窗口外框，再依次叠加 DWM 黑边裁剪
        # 与 crop 的偏移。模型据此把截图像素换算成屏幕 / 窗口坐标
        # （screen = window_x + px），解决「截图尺寸与窗口外框尺寸不一致」的困惑。
        origin_x, origin_y = target.left, target.top
        # 去掉 Win10 DWM 为阴影/缩放预留的不可见边框（截图后呈黑边）
        trim = visible_region(target)
        if trim is not None:
            bgra = transform.crop_bgra(bgra, width, height, trim)
            width, height = trim.width, trim.height
            origin_x += trim.x
            origin_y += trim.y
        if crop is not None:
            bgra = transform.crop_bgra(bgra, width, height, crop)
            width, height = crop.width, crop.height
            origin_x += crop.x
            origin_y += crop.y
        if grid is not None:
            bgra = grid_module.draw_grid_bgra(bgra, width, height, int(grid))
        data = png.encode_png_bgra(width, height, bgra)
        with open(path, "wb") as handle:
            handle.write(data)
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=target.pid,
            window_title=target.title,
            backend=self.name,
            window_handle=target.handle,
            windows_total=len(candidates),
            window_selector=window or DEFAULT_SELECTOR,
            window_summary=indexed_summary(candidates, target),
            window_x=origin_x,
            window_y=origin_y,
            window_rect=window_geometry(candidates, target)["rect"],
        )

    def list_windows(self, pid: int) -> list[WindowInfo]:
        """枚举该进程树的全部可操作窗口（``op=windows`` 数据源）。"""
        return list_windows(pid)

    def list_monitors(self) -> list[Monitor]:
        """枚举显示器（多显示器 / 全屏截取用）。"""
        return [Monitor(**item) for item in winapi.list_monitors()]

    def capture_screen(self, monitor: Monitor, path: str,
                       crop: CropRegion | None = None,
                       grid: int | None = None) -> CaptureResult:
        """截取整个显示器区域（多显示器 / 全屏）到 ``path``。"""
        winapi.ensure_process_dpi_aware()
        bgra, width, height = capture_screen_pixels(monitor)
        origin_x, origin_y = monitor.left, monitor.top
        if crop is not None:
            bgra = transform.crop_bgra(bgra, width, height, crop)
            width, height = crop.width, crop.height
            origin_x += crop.x
            origin_y += crop.y
        if grid is not None:
            bgra = grid_module.draw_grid_bgra(bgra, width, height, int(grid))
        data = png.encode_png_bgra(width, height, bgra)
        with open(path, "wb") as handle:
            handle.write(data)
        return CaptureResult(
            path=path,
            width=width,
            height=height,
            window_pid=0,
            window_title="",
            backend=self.name,
            window_handle=0,
            windows_total=0,
            window_selector="screen",
            window_summary=(f"显示器 {monitor.width}x{monitor.height}"
                            f"@({monitor.left},{monitor.top})"
                            + ("[primary]" if monitor.primary else "")),
            window_x=origin_x,
            window_y=origin_y,
            window_rect={"x": monitor.left, "y": monitor.top,
                         "width": monitor.width, "height": monitor.height},
        )

    def list_elements(self, pid: int, window: str | None = None) -> list[ElementInfo]:
        """枚举被选窗口内的控件（``op=elements`` 数据源）。"""
        return list_elements(pid, window)

    def control(self, pid: int, request: WindowControlRequest) -> dict:
        """对被选窗口执行激活 / 最大化 / 最小化 / 还原 / 关闭 / 移动 / 缩放。"""
        return control_window(pid, request)


def resolve_window_pids(pid: int) -> set[int]:
    """把目标 PID 解析为「窗口归属的 Windows PID 集合」（含全部子进程）。

    Cygwin/MSYS2 下 pid 是 POSIX PID，需经 ``/proc/<pid>/winpid`` 映射到
    Windows PID；映射不到（进程已退出，或调用方直接传入 Windows PID）时
    把原值作为候选，避免因 pid 空间差异漏掉目标窗口。
    """
    if winapi.is_cygwin_like():
        base: set[int] = set()
        for posix_pid in proctree.collect_process_tree(pid):
            win_pid = proctree.read_winpid(posix_pid)
            if win_pid:
                base.add(win_pid)
        if not base:
            base = {pid}
    else:
        base = {pid}
    if not base:
        return set()
    return _expand_windows_descendants(base)


def _expand_windows_descendants(root_pids: set[int]) -> set[int]:
    """用 Toolhelp32 进程表把根集合扩展为「根 + 全部 Windows 后代」。"""
    try:
        processes = winapi.list_processes()
    except OSError as exc:  # pragma: no cover - 依赖系统调用
        logger.debug("Windows 进程表读取失败: %s", exc)
        return set(root_pids)
    children: dict[int, list[int]] = {}
    for proc_pid, ppid, _name in processes:
        children.setdefault(ppid, []).append(proc_pid)
    result = set(root_pids)
    stack = [(root, 0) for root in root_pids]
    while stack:
        current, depth = stack.pop()
        if depth >= _MAX_TREE_DEPTH:
            continue
        for child in children.get(current, ()):
            if child in result:
                continue
            result.add(child)
            stack.append((child, depth + 1))
    return result


def enumerate_window_infos(window_pids: set[int]) -> list[WindowInfo]:
    """枚举属于 ``window_pids`` 的可操作顶层窗口（按 Z 序，含前台标记）。

    ``EnumWindows`` 的枚举顺序即 Z 序（越靠前 = 越靠上），因此列表下标可直接
    作为 ``order``；这样 ``#1`` 这类选择器能命中刚弹出的菜单 / 下拉浮层。
    """
    user = winapi.user32()
    process_names = _process_name_map()
    infos: list[WindowInfo] = []
    for order, hwnd in enumerate(winapi.enum_children_windows()):
        window_pid = winapi.window_pid(hwnd)
        if window_pid not in window_pids:
            continue
        visible = bool(user.IsWindowVisible(hwnd))
        left, top, right, bottom = winapi.window_rect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            continue
        class_name = winapi.window_class(hwnd)
        if class_name in SHELL_WINDOW_CLASSES:
            continue
        if winapi.is_window_cloaked(hwnd):
            continue
        if winapi.window_is_hung(hwnd):
            logger.debug("跳过无响应窗口 hwnd=%s", hwnd)
            continue
        extras = _window_extension_fields(hwnd)
        infos.append(WindowInfo(
            handle=hwnd,
            pid=window_pid,
            title=winapi.window_text(hwnd),
            class_name=class_name,
            width=width,
            height=height,
            left=left,
            top=top,
            tool_window=winapi.window_is_toolwindow(hwnd),
            minimized=bool(user.IsIconic(hwnd)),
            visible=visible,
            foreground=winapi.is_foreground(hwnd),
            order=order,
            client_area=winapi.window_client_area(hwnd),
            process_name=process_names.get(window_pid, ""),
            **extras,
        ))
    return mark_main(infos)


def _window_extension_fields(hwnd) -> dict:
    """读取窗口的扩展属性（置顶 / 属主），仅在 ``WindowInfo`` 定义支持时返回。"""
    extras: dict = {}
    if "topmost" in _WINDOW_INFO_FIELDS:
        extras["topmost"] = winapi.window_is_topmost(hwnd)
    if "owner" in _WINDOW_INFO_FIELDS:
        extras["owner"] = winapi.window_owner(hwnd)
    return extras


def _process_name_map() -> dict[int, str]:
    """读取 ``{pid: exe 名}`` 映射（供 ``process:`` 选择器与展示；失败返回空表）。"""
    try:
        processes = winapi.list_processes()
    except OSError as exc:  # pragma: no cover - 依赖系统调用
        logger.debug("读取进程名失败: %s", exc)
        return {}
    return {pid: name for pid, _ppid, name in processes}


def enumerate_candidates(window_pids: set[int]) -> list[WindowInfo]:
    """枚举属于 ``window_pids`` 的可截图顶层窗口（兼容旧接口）。"""
    return enumerate_window_infos(window_pids)


def list_windows(pid: int) -> list[WindowInfo]:
    """返回 ``pid`` 及其子进程的全部可操作窗口（``op=windows`` 的数据源）。"""
    winapi.ensure_process_dpi_aware()
    window_pids = resolve_window_pids(pid)
    if not window_pids:
        return []
    return enumerate_window_infos(window_pids)


def list_elements(pid: int, window: str | None = None) -> list[ElementInfo]:
    """返回被选窗口内的全部控件（``op=elements`` 的数据源）。

    控件来自 ``EnumChildWindows``（递归枚举整棵子窗口树），按「从上到下、
    从左到右」排序，便于与截图 / 清单顺序对照。经典 Win32 控件（按钮、
    编辑框、列表）都能枚举到；Chrome / Electron / 游戏等自绘界面内部没有
    标准子窗口，结果会很少或为空（此时改用截图 + 像素坐标操作）。

    Raises:
        SelectorError: 窗口选择器非法或没有匹配窗口。
    """
    winapi.ensure_process_dpi_aware()
    window_pids = resolve_window_pids(pid)
    if not window_pids:
        return []
    infos = enumerate_window_infos(window_pids)
    if not infos:
        return []
    target = pick_window(infos, window)
    return list_child_elements(target)


def list_child_elements(target: WindowInfo) -> list[ElementInfo]:
    """枚举 ``target`` 窗口内的控件并转为统一描述（按位置排序）。

    优先走 **UI Automation**（``_screenshot/uia.py``）：UIA 能看到 Chrome /
    Electron / Qt / WPF / UWP 等自绘界面暴露的无障碍节点，远胜「只认标准
    子窗口」的 ``EnumChildWindows``。UIA 不可用（非 Windows、COM 创建失败）
    或未枚举到任何元素时，回退 ``EnumChildWindows``（经典 Win32 控件稳）。

    坐标为**屏幕像素**；工具层再用输入后端的窗口 frame 换算成窗口内坐标，
    保证与 ``op=screenshot`` 产物、输入坐标一一对应。
    """
    elements = _list_elements_via_uia(target.handle)
    if elements:
        elements.sort(key=lambda item: (item.top, item.left))
        return elements
    return _list_elements_via_win32(target)


def _list_elements_via_uia(hwnd: int) -> list[ElementInfo]:
    """用 UI Automation 枚举控件（不可用 / 失败返回空列表）。"""
    try:
        from . import uia
    except ImportError:  # pragma: no cover - 模块随包发布
        return []
    if not uia.available():
        return []
    try:
        return list(uia.enumerate_elements(winapi.hwnd_value(hwnd)))
    except Exception as exc:  # noqa: BLE001 - 任何失败都回退经典枚举
        logger.debug("UIA 枚举失败，回退 EnumChildWindows: %s", exc)
        return []


def _list_elements_via_win32(target: WindowInfo) -> list[ElementInfo]:
    """经典 Win32 子窗口枚举（UIA 不可用时的回退）。"""
    elements: list[ElementInfo] = []
    for hwnd in winapi.enum_child_windows(target.handle):
        left, top, right, bottom = winapi.window_rect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            continue
        elements.append(ElementInfo(
            handle=winapi.hwnd_value(hwnd),
            pid=winapi.window_pid(hwnd),
            class_name=winapi.window_class(hwnd),
            text=winapi.window_text(hwnd),
            left=left,
            top=top,
            width=width,
            height=height,
            enabled=winapi.is_window_enabled(hwnd),
            visible=winapi.is_window_visible(hwnd),
            depth=winapi.window_depth(hwnd, target.handle),
            source="win32",
        ))
    elements.sort(key=lambda item: (item.top, item.left))
    return elements


def control_window(pid: int, request: WindowControlRequest) -> dict:
    """对 ``pid`` 进程树中被选中的窗口执行状态 / 几何控制。

    支持激活（置前）、最大化、最小化、还原、关闭请求，以及按屏幕坐标移动 /
    缩放窗口；返回动作前后的窗口状态，便于确认结果（例如调整窗口大小后，
    输入坐标与截图尺寸都更可预测）。

    Raises:
        NoWindowError: 进程树内没有可操作窗口。
        SelectorError: 窗口选择器非法或没有匹配窗口。
    """
    winapi.ensure_process_dpi_aware()
    window_pids = resolve_window_pids(pid)
    infos = enumerate_window_infos(window_pids) if window_pids else []
    if not infos:
        raise NoWindowError(
            f"进程 {pid} 及其子进程没有可操作窗口（纯命令行进程无 GUI 窗口；"
            f"窗口已最小化/被隐藏时也找不到）"
        )
    target = pick_window(infos, request.selector)
    action = request.action
    before = window_state(target.handle)
    if action == "activate":
        _activate_window(target)
    elif action == "maximize":
        winapi.show_window(target.handle, winapi.SW_MAXIMIZE)
    elif action == "minimize":
        winapi.show_window(target.handle, winapi.SW_MINIMIZE)
    elif action == "restore":
        winapi.show_window(target.handle, winapi.SW_RESTORE)
    elif action == "close":
        winapi.close_window(target.handle)
    elif action == "move":
        winapi.set_window_pos(
            target.handle, int(request.x), int(request.y), 0, 0,
            winapi.SWP_NOSIZE | winapi.SWP_NOZORDER | winapi.SWP_NOACTIVATE,
        )
    elif action == "resize":
        winapi.set_window_pos(
            target.handle, 0, 0, int(request.width), int(request.height),
            winapi.SWP_NOMOVE | winapi.SWP_NOZORDER | winapi.SWP_NOACTIVATE,
        )
    elif action == "fit":  # pragma: no branch - 动作集合由上层校验
        winapi.set_window_pos(
            target.handle, int(request.x), int(request.y),
            int(request.width), int(request.height),
            winapi.SWP_NOZORDER | winapi.SWP_NOACTIVATE,
        )
    elif action == "always_on_top":
        winapi.set_window_topmost(target.handle, topmost=True)
    elif action == "not_on_top":
        winapi.set_window_topmost(target.handle, topmost=False)
    time.sleep(_CONTROL_SETTLE_SECONDS)
    after = window_state(target.handle)
    detail = {
        "window_action": action,
        "handle": target.handle,
        "handle_hex": target.handle_hex,
        "window_title": target.title,
        "before": before,
        "after": after,
    }
    if action == "activate":
        activated = _application_foreground(target)
        detail["foreground"] = activated
        owner = winapi.window_owner(target.handle)
        if owner and owner != target.handle:
            detail["owner_handle"] = f"0x{owner:X}"
        if not activated:
            detail["warning"] = (
                f"窗口未能取得前台（当前前台: {winapi.foreground_description()}）。"
                f"Windows 前台锁定策略会拒绝后台进程的置前请求：若目标窗口已最小化，"
                f"先试 window_action=restore 再 activate；若其它程序（如全屏游戏）"
                f"持续抢占前台，需先处理该程序，或改用 SendInput 之外的 "
                f"method='message' 投递通道"
            )
    return detail


def _activate_window(target: WindowInfo) -> bool:
    """把窗口置于前台；工具窗口（弹层 / 菜单）优先激活其属主窗口。

    ``SetForegroundWindow`` 对 ``WS_EX_TOOLWINDOW`` 窗口无效——这类窗口
    （右键菜单、下拉浮层、Chrome/Electron 弹出层）不参与前台切换，直接激活
    只会一直失败；它们的属主才是该应用的主窗口，激活属主即可让整个应用
    取得焦点（浮层也随之可交互）。属主激活失败时再退回尝试窗口自身。
    """
    owner = winapi.window_owner(target.handle) if target.tool_window else 0
    if owner and winapi.set_foreground(owner):
        return True
    return winapi.set_foreground(target.handle)


def _application_foreground(target: WindowInfo) -> bool:
    """该窗口（或其属主）当前是否持有前台（工具窗口自身几乎不会是前台）。"""
    if winapi.is_foreground(target.handle):
        return True
    owner = winapi.window_owner(target.handle)
    return bool(owner) and winapi.is_foreground(owner)


def window_state(handle) -> dict:
    """读取窗口当前状态（几何 + 最小化 / 可见 / 前台 / 置顶 / 存在性）。"""
    if not winapi.is_window(handle):
        return {"exists": False}
    left, top, right, bottom = winapi.window_rect(handle)
    return {
        "exists": True,
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
        "minimized": winapi.is_window_minimized(handle),
        "visible": winapi.is_window_visible(handle),
        "foreground": winapi.is_foreground(handle),
        "topmost": winapi.window_is_topmost(handle),
    }


def visible_region(candidate: WindowCandidate) -> CropRegion | None:
    """计算窗口截图中「去掉 DWM 不可见边框」后的可见区域。

    Win10 的 ``GetWindowRect`` 包含系统为阴影/调整大小预留的不可见边框，
    ``PrintWindow`` / ``BitBlt`` 在该区域无内容，截图四边呈黑边；
    ``DWMWA_EXTENDED_FRAME_BOUNDS`` 给出真实可见边界，与窗口矩形之差即为
    需要裁掉的偏移。

    Returns:
        需要裁剪的区域（以整窗截图左上角为原点）；无黑边（区域即整窗）、
        窗口非 DWM 合成或边界异常时返回 ``None``（保持整窗像素）。
    """
    bounds = winapi.extended_frame_bounds(candidate.handle)
    if bounds is None:
        return None
    left, top, right, bottom = bounds
    width, height = right - left, bottom - top
    if width <= 0 or height <= 0:
        return None
    try:
        region = CropRegion(left - candidate.left, top - candidate.top, width, height)
        region.validate_against(candidate.width, candidate.height)
    except CropError:
        logger.debug(
            "DWM 可见边界异常，按整窗截图: bounds=%s window=%sx%s",
            bounds, candidate.width, candidate.height,
        )
        return None
    if region.is_full(candidate.width, candidate.height):
        return None
    return region


def capture_window_pixels(candidate: WindowCandidate) -> tuple[bytes, int, int]:
    """抓取窗口像素，返回 ``(BGRA 字节, 宽, 高)``。

    依次尝试三条路径，任一拿到非空白图像即返回：

      1. ``PrintWindow(PW_RENDERFULLCONTENT)``——能截后台 / 被遮挡窗口；
      2. 窗口 DC 的 ``BitBlt``——PrintWindow 失败时把窗口提前后屏幕拷贝；
      3. **桌面屏幕 DC 的窗口矩形拷贝**——DirectX 独占全屏 / 硬件加速游戏
         在 1、2 两条路径下常返回全黑占位图，而已由 DWM 合成的桌面通常仍
         有真实画面，按窗口矩形从屏幕 DC 取像素即可截到游戏画面（窗口须
         可见且未最小化）。

    三条路径都空白（窗口本身是纯色画面）时返回有数据的那次结果，全部失败
    才报错。
    """
    width, height = candidate.width, candidate.height
    printed = _capture_window(candidate.handle, width, height, mode="print")
    if printed is not None and not png.looks_blank(printed, 4):
        return printed, width, height
    # PrintWindow 拿不到内容（硬件加速窗口的常见情形）→ 提到最前后屏幕拷贝
    if winapi.raise_window(candidate.handle):
        time.sleep(_RAISE_SETTLE_SECONDS)
    screen = _capture_window(candidate.handle, width, height, mode="bitblt")
    if screen is not None and not png.looks_blank(screen, 4):
        return screen, width, height
    # 仍拿不到内容（DirectX 独占全屏游戏）→ 从桌面屏幕 DC 拷贝窗口矩形区域
    region = _capture_window_from_screen(candidate, width, height)
    if region is not None and not png.looks_blank(region, 4):
        return region, width, height
    for data in (region, screen, printed):
        if data is not None:
            return data, width, height
    raise ScreenshotError(
        f"窗口像素抓取失败（窗口标题: {candidate.title or '<无标题>'}，"
        f"进程 {candidate.pid}）：PrintWindow、窗口 BitBlt 与屏幕区域拷贝均未"
        f"返回有效图像"
    )


def _capture_window_from_screen(candidate: WindowCandidate, width: int,
                                height: int) -> bytes | None:
    """从**桌面屏幕 DC** 拷贝窗口所在矩形区域（DirectX 独占全屏游戏的兜底）。

    窗口自身的 DC（PrintWindow / ``GetWindowDC`` BitBlt）对独占全屏 / 硬件
    加速游戏常返回全黑占位图；经 DWM 合成的桌面通常仍能取到真实画面，因此
    按窗口屏幕矩形从屏幕 DC 取像素（游戏全屏时它就是最上层，不会被遮挡）。

    区域超出虚拟桌面时按交点裁剪、其余像素保持全透明；窗口不可见 / 已最小化
    时直接返回 ``None``（不可见窗口没有可渲染内容）。
    """
    region = screen_region_intersection(candidate, width, height)
    if region is None:
        return None
    offset_x, offset_y, copy_width, copy_height = region
    gdi = winapi.gdi32()
    source_dc = winapi.screen_dc()
    if not source_dc:
        return None
    memory_dc = None
    bitmap = None
    previous = None
    try:
        memory_dc = gdi.CreateCompatibleDC(source_dc)
        if not memory_dc:
            return None
        info = winapi.BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(winapi.BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # 负高度 = 自上而下扫描行
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            source_dc, ctypes.byref(info), winapi.DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits:
            return None
        previous = gdi.SelectObject(memory_dc, bitmap)
        ok = gdi.BitBlt(
            memory_dc, offset_x, offset_y, copy_width, copy_height,
            source_dc, candidate.left + offset_x, candidate.top + offset_y,
            winapi.SRCCOPY,
        )
        if not ok:
            return None
        return ctypes.string_at(bits, width * height * 4)
    finally:
        if memory_dc and previous:
            gdi.SelectObject(memory_dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory_dc:
            gdi.DeleteDC(memory_dc)
        winapi.release_dc(source_dc)


def screen_region_intersection(candidate: WindowCandidate, width: int,
                               height: int) -> tuple[int, int, int, int] | None:
    """窗口矩形与虚拟桌面的交集（窗口内坐标偏移 + 可拷贝尺寸）。

    返回 ``(offset_x, offset_y, copy_width, copy_height)``：``offset_x`` /
    ``offset_y`` 是交集左上角**相对窗口**的像素偏移，用于把屏幕拷贝放进窗口
    尺寸的位图；无交集（窗口完全在屏幕外）或窗口不可见 / 已最小化时返回
    ``None``。
    """
    if getattr(candidate, "minimized", False) or not getattr(candidate, "visible", True):
        return None
    if width <= 0 or height <= 0:
        return None
    vleft, vtop, vwidth, vheight = winapi.virtual_screen_rect()
    left, top = candidate.left, candidate.top
    x0 = max(left, vleft)
    y0 = max(top, vtop)
    x1 = min(left + width, vleft + vwidth)
    y1 = min(top + height, vtop + vheight)
    if x1 <= x0 or y1 <= y0:
        return None
    return (x0 - left, y0 - top, x1 - x0, y1 - y0)


def capture_screen_pixels(monitor: Monitor) -> tuple[bytes, int, int]:
    """从屏幕 DC 拷贝显示器区域像素，返回 ``(BGRA 字节, 宽, 高)``。

    多显示器下虚拟桌面坐标可为负；``BitBlt`` 源坐标直接用屏幕坐标
    （``monitor.left`` / ``monitor.top``），因此负坐标同样正确。

    Raises:
        ScreenshotError: 显示器尺寸非法、取屏幕 DC 失败或 BitBlt 失败。
    """
    width, height = int(monitor.width), int(monitor.height)
    if width <= 0 or height <= 0:
        raise ScreenshotError(f"显示器尺寸非法: {width}x{height}")
    gdi = winapi.gdi32()
    source_dc = winapi.screen_dc()
    if not source_dc:
        raise ScreenshotError("获取屏幕设备上下文失败（无法全屏截取）")
    memory_dc = None
    bitmap = None
    previous = None
    try:
        memory_dc = gdi.CreateCompatibleDC(source_dc)
        if not memory_dc:
            raise ScreenshotError("创建内存 DC 失败（无法全屏截取）")
        info = winapi.BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(winapi.BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            source_dc, ctypes.byref(info), winapi.DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits:
            raise ScreenshotError("创建位图失败（无法全屏截取）")
        previous = gdi.SelectObject(memory_dc, bitmap)
        ok = gdi.BitBlt(memory_dc, 0, 0, width, height, source_dc,
                        int(monitor.left), int(monitor.top), winapi.SRCCOPY)
        if not ok:
            raise ScreenshotError(
                f"屏幕像素拷贝失败（区域 {width}x{height}@({monitor.left},{monitor.top})）"
            )
        return ctypes.string_at(bits, width * height * 4), width, height
    finally:
        if memory_dc and previous:
            gdi.SelectObject(memory_dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory_dc:
            gdi.DeleteDC(memory_dc)
        winapi.release_dc(source_dc)


def _capture_window(hwnd: int, width: int, height: int, *, mode: str) -> bytes | None:
    """按指定方式把窗口渲染进内存 DIB 并返回 BGRA 数据（失败返回 None）。"""
    user = winapi.user32()
    gdi = winapi.gdi32()
    source_dc = user.GetWindowDC(hwnd)
    if not source_dc:
        return None
    memory_dc = None
    bitmap = None
    previous = None
    try:
        memory_dc = gdi.CreateCompatibleDC(source_dc)
        if not memory_dc:
            return None
        info = winapi.BITMAPINFO()
        header = info.bmiHeader
        header.biSize = ctypes.sizeof(winapi.BITMAPINFOHEADER)
        header.biWidth = width
        header.biHeight = -height  # 负高度 = 自上而下扫描行
        header.biPlanes = 1
        header.biBitCount = 32
        header.biCompression = 0  # BI_RGB
        bits = ctypes.c_void_p()
        bitmap = gdi.CreateDIBSection(
            source_dc, ctypes.byref(info), winapi.DIB_RGB_COLORS,
            ctypes.byref(bits), None, 0,
        )
        if not bitmap or not bits:
            return None
        previous = gdi.SelectObject(memory_dc, bitmap)
        if mode == "print":
            ok = user.PrintWindow(hwnd, memory_dc, winapi.PW_RENDERFULLCONTENT)
        else:
            ok = gdi.BitBlt(
                memory_dc, 0, 0, width, height, source_dc, 0, 0, winapi.SRCCOPY
            )
        if not ok:
            return None
        return ctypes.string_at(bits, width * height * 4)
    finally:
        if memory_dc and previous:
            gdi.SelectObject(memory_dc, previous)
        if bitmap:
            gdi.DeleteObject(bitmap)
        if memory_dc:
            gdi.DeleteDC(memory_dc)
        if source_dc:
            user.ReleaseDC(hwnd, source_dc)


__all__ = [
    "SHELL_WINDOW_CLASSES",
    "WindowCandidate",
    "WindowsBackend",
    "capture_screen_pixels",
    "capture_window_pixels",
    "control_window",
    "enumerate_candidates",
    "enumerate_window_infos",
    "list_child_elements",
    "list_elements",
    "list_windows",
    "resolve_window_pids",
    "screen_region_intersection",
    "select_window",
    "visible_region",
    "window_state",
]
