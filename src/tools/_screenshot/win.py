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
"""

from __future__ import annotations

import ctypes
import logging
import time
from dataclasses import dataclass

from . import png, proctree, transform, winapi
from .result import CaptureResult, NoWindowError, ScreenshotError
from .transform import CropError, CropRegion

logger = logging.getLogger(__name__)

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


@dataclass
class WindowCandidate:
    """候选窗口（可排序选择，纯数据便于单测）。"""

    handle: int
    pid: int
    title: str
    class_name: str
    width: int
    height: int
    tool_window: bool = False
    minimized: bool = False
    #: 窗口矩形左上角的屏幕坐标（用于换算 DWM 可见边界的裁边偏移）
    left: int = 0
    top: int = 0

    @property
    def area(self) -> int:
        return self.width * self.height


def select_window(candidates: list[WindowCandidate]) -> WindowCandidate | None:
    """从候选窗口中选择最可能的「主窗口」。

    排序优先级：非工具窗口 > 未最小化 > 有标题 > 面积大 > 进程号小
    （后两项保证结果稳定）。
    """
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda item: (
            not item.tool_window,
            not item.minimized,
            bool(item.title.strip()),
            item.area,
            -item.pid,
        ),
    )


class WindowsBackend:
    """Windows 平台截图后端。"""

    name = "windows"

    def supports(self) -> bool:
        return winapi.is_windows_platform()

    def capture(self, pid: int, path: str,
                crop: CropRegion | None = None) -> CaptureResult:
        """截取 ``pid``（及其子进程）的窗口到 ``path``（PNG）。

        ``crop`` 非空时只写出指定像素区域（在内存 BGRA 上裁剪后再编码，
        无需解码 PNG）；区域越界抛 ``CropError``。
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
        target = select_window(candidates)
        if target is None:  # pragma: no cover - candidates 非空时不会发生
            raise NoWindowError(f"进程 {pid} 没有可用的截图窗口")
        bgra, width, height = capture_window_pixels(target)
        # 去掉 Win10 DWM 为阴影/缩放预留的不可见边框（截图后呈黑边）
        trim = visible_region(target)
        if trim is not None:
            bgra = transform.crop_bgra(bgra, width, height, trim)
            width, height = trim.width, trim.height
        if crop is not None:
            bgra = transform.crop_bgra(bgra, width, height, crop)
            width, height = crop.width, crop.height
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
        )


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


def enumerate_candidates(window_pids: set[int]) -> list[WindowCandidate]:
    """枚举属于 ``window_pids`` 的可截图顶层窗口。"""
    user = winapi.user32()
    candidates: list[WindowCandidate] = []
    for hwnd in winapi.enum_children_windows():
        window_pid = winapi.window_pid(hwnd)
        if window_pid not in window_pids:
            continue
        if not user.IsWindowVisible(hwnd):
            continue
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
        candidates.append(WindowCandidate(
            handle=hwnd,
            pid=window_pid,
            title=winapi.window_text(hwnd),
            class_name=class_name,
            width=width,
            height=height,
            tool_window=winapi.window_is_toolwindow(hwnd),
            minimized=bool(user.IsIconic(hwnd)),
            left=left,
            top=top,
        ))
    return candidates


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

    先 PrintWindow（可截后台/被遮挡窗口），结果为空白（全黑占位）或失败时
    回退 BitBlt 屏幕拷贝；两者都空白（窗口本身是纯色画面）时返回有数据的
    那次结果，两者都失败才报错。
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
    for data in (screen, printed):
        if data is not None:
            return data, width, height
    raise ScreenshotError(
        f"窗口像素抓取失败（窗口标题: {candidate.title or '<无标题>'}，"
        f"进程 {candidate.pid}）：PrintWindow 与 BitBlt 均未返回有效图像"
    )


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
    "capture_window_pixels",
    "enumerate_candidates",
    "resolve_window_pids",
    "select_window",
    "visible_region",
]
