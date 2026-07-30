"""底部栏模块 — DECSTBM 分屏底部固定栏（精简版）。

使用 ``_screen.py`` 纯 ANSI 序列替代 blessed 的 terminfo 封装，
使用 ``wcswidth_simple()`` 替代 ``wcwidth.wcswidth()``，
内联 _StatusMixin、_CompletionPopup、绘制函数、_SystemMonitor。

设计模式: 外观（Facade）— _BottomBar 统一管理底部栏所有子组件
（分隔线/状态行/输入区/补全弹窗/光标）的协调绘制。
"""

from __future__ import annotations

import logging
import math
import platform
import re
import subprocess
import sys
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from src.tui._locks import _try_acquire_output_lock
from src.tui._screen import (
    _get_terminal_size,
    clear_line,
    cursor_goto,
    cursor_restore,
    cursor_save,
    register_sigwinch_callback,
    reset_scroll_region,
    scroll_down,
    scroll_up,
    set_scroll_region,
    sgr_reset,
    TerminalWidthCache,
    wcswidth_simple,
    write_stdout,
)
from src.tui._input import (
    _compute_cursor_visual_pos,
    _expand_tabs,
    _wrap_by_width,
)

if TYPE_CHECKING:
    from ._input import Input
    from ._cursor_tracker import CursorTracker

_logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════
# 布局常量
# ═══════════════════════════════════════════════════════════

_BOTTOM_MIN_HEIGHT = 12
_BOTTOM_MIN_LINES = 5
_MIN_INPUT_ROWS = 1
_TAB_WIDTH = 4


# ═══════════════════════════════════════════════════════════
# ANSI 颜色常量（256 色体系，内联，零第三方依赖）
# ═══════════════════════════════════════════════════════════

_COLOR_ACCENT = "\033[38;5;45m"
_COLOR_DEEP_CYAN = "\033[38;5;32m"
_COLOR_DIM = "\033[38;5;242m"
_COLOR_RESET = "\033[0m"
_COLOR_SEP = "\033[38;5;237m"
_COLOR_TIME = "\033[38;5;110m"
_COLOR_TOKEN = "\033[38;5;68m"
_COLOR_SPEED = "\033[38;5;214m"
_COLOR_TOOL_OK = "\033[38;5;41m"
_COLOR_TOOL_FAIL = "\033[38;5;196m"
_COLOR_SELECT_BG = "\033[48;5;236m"
_COLOR_SELECT_FG = "\033[38;5;15m"
_COLOR_COMPLETE_TITLE = "\033[1;38;5;45m"
_COLOR_COMPLETE_CMD_PREFIX = "\033[1;38;5;45m"
_COLOR_COMPLETE_DIR = "\033[38;5;110m"
_COLOR_COMPLETE_MATCH = "\033[38;5;221m"

_PLACEHOLDER_TEXT = "输入消息 · /help 查看命令 · Ctrl+N 切换模型 · Tab 补全"
_PLACEHOLDER_COMPACT = "/help · Ctrl+N · Tab"
_PLACEHOLDER_STREAMING = "AI 生成中..."


# ═══════════════════════════════════════════════════════════
# 简化动画时钟（替代已删除的 animation/animator.py）
# ═══════════════════════════════════════════════════════════

class _SimpleAnimator:
    """轻量动画帧计数器 — 替代 AnimatorContext + BreathPalette。

    提供 frame（单调递增）和 breath_frame（正弦波周期）两个计数器，
    以及 sine_color() 正弦波颜色插值。线程安全（实例级锁）。
    """

    _instance: _SimpleAnimator | None = None

    def __init__(self) -> None:
        self._frame: int = 0
        self._breath_frame: int = 0
        self._lock = threading.Lock()

    def tick(self) -> None:
        """推进帧计数。"""
        with self._lock:
            self._frame += 1
            self._breath_frame = (self._breath_frame + 1) % 12

    @property
    def frame(self) -> int:
        return self._frame

    @property
    def breath_frame(self) -> int:
        return self._breath_frame

    def sine_color(self, lo: int, hi: int, period: int = 12) -> int:
        """正弦波颜色插值。

        Args:
            lo: 最暗色号。
            hi: 最亮色号。
            period: 周期帧数。

        Returns:
            插值色号（lo~hi 范围）。
        """
        ratio = (math.sin(2 * math.pi * self._breath_frame / period) + 1) / 2
        return lo + int((hi - lo) * ratio)

    @classmethod
    def get_default(cls) -> _SimpleAnimator:
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance


# ═══════════════════════════════════════════════════════════
# 工具函数
# ═══════════════════════════════════════════════════════════

def _is_narrow() -> bool:
    """判断是否为窄屏（宽度 < 60 列）。"""
    w, _ = _get_terminal_size()
    return w < 60


def _visual_width(text: str) -> int:
    """计算字符串的可视宽度（去除 ANSI 转义序列）。

    识别 CSI 序列（\\033[...终止字母），正确跳过。
    """
    w = 0
    i = 0
    while i < len(text):
        if text[i] == '\033':
            j = i + 1
            if j < len(text) and text[j] == '[':
                j += 1
                while j < len(text) and text[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                i = j + 1 if j < len(text) else len(text)
            elif j < len(text) and text[j] in ']PX^_':
                j += 1
                while j < len(text):
                    if text[j] == '\033' and j + 1 < len(text) and text[j + 1] == '\\':
                        i = j + 2
                        break
                    elif text[j] == '\a':
                        i = j + 1
                        break
                    j += 1
                else:
                    i = len(text)
            else:
                i = j + 1
        else:
            cw = wcswidth_simple(text[i])
            w += cw if cw >= 0 else 1
            i += 1
    return w


def _truncate_by_width(s: str, max_width: int) -> str:
    """按终端列宽截断字符串。"""
    w = 0
    for i, ch in enumerate(s):
        cw = wcswidth_simple(ch) if wcswidth_simple(ch) >= 0 else 1
        if w + cw > max_width:
            return s[:i]
        w += cw
    return s


def _ansi_truncate(s: str, max_width: int) -> str:
    """按终端列宽截断字符串（ANSI 转义序列感知）。

    完整保留 ANSI 颜色/样式序列，仅截断可见字符部分。
    截断后自动追加 ``\\033[0m`` 防止颜色溢出。

    Args:
        s: 含 ANSI 转义序列的输入字符串。
        max_width: 最大终端显示列数。

    Returns:
        截断后的字符串（仍含有效 ANSI 序列 + 尾部 reset）。
    """
    if max_width <= 0 or not s:
        return ""
    w = 0
    parts: list[str] = []
    has_ansi = False
    i = 0
    while i < len(s):
        if s[i] == '\033':
            # 收集整个 ANSI 转义序列
            seq_start = i
            j = i + 1
            if j < len(s) and s[j] == '[':
                j += 1
                while j < len(s) and s[j] not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz':
                    j += 1
                end = j + 1 if j < len(s) else len(s)
            elif j < len(s) and s[j] in ']PX^_':
                j += 1
                while j < len(s):
                    if s[j] == '\033' and j + 1 < len(s) and s[j + 1] == '\\':
                        end = j + 2
                        break
                    elif s[j] == '\a':
                        end = j + 1
                        break
                    j += 1
                else:
                    end = len(s)
            else:
                end = j + 1
            parts.append(s[seq_start:end])
            has_ansi = True
            i = end
            continue
        cw = wcswidth_simple(s[i])
        cw = cw if cw >= 0 else 1
        if w + cw > max_width:
            if has_ansi:
                parts.append('\033[0m')
            return ''.join(parts)
        w += cw
        parts.append(s[i])
        i += 1
    return ''.join(parts)


def _build_gradient(width: int, start_color: int = 45, end_color: int = 237,
                    char: str = "\u2501") -> str:
    """构建渐变分隔线。

    Args:
        width: 分隔线宽度（列数）。
        start_color: 起始 256 色号。
        end_color: 结束 256 色号。
        char: 分隔线字符。

    Returns:
        带 ANSI 颜色序列的分隔线字符串。
    """
    if width <= 0:
        return ""
    parts: list[str] = []
    for i in range(width):
        ratio = i / max(1, width - 1)
        color = start_color + int((end_color - start_color) * ratio)
        parts.append(f"\033[38;5;{color}m{char}")
    parts.append("\033[0m")
    return "".join(parts)


def _build_glow_ansi(frame: int, base_color: int, amplitude: int) -> str:
    """构建呼吸辉光 ANSI 前景色序列。

    Args:
        frame: 当前帧号。
        base_color: 基础色号。
        amplitude: 波动幅度。

    Returns:
        ANSI 256 色前景色序列。
    """
    animator = _SimpleAnimator.get_default()
    color = animator.sine_color(base_color, base_color + amplitude, 12)
    return f"\033[38;5;{color}m"


# ═══════════════════════════════════════════════════════════
# _SystemMonitor — 跨平台系统监控
# ═══════════════════════════════════════════════════════════

class _SystemMonitor:
    """跨平台系统监控 — CPU 与内存使用率采集。

    惰性初始化：仅在首次采集时才检测平台和尝试导入 psutil。
    使用 1 秒缓存消峰。
    """

    CPU_CACHE_TTL: float = 1.0
    MEM_CACHE_TTL: float = 1.0

    def __init__(self) -> None:
        self._platform: str = self._detect_platform()
        self._psutil: Any = None
        self._has_psutil: bool = False
        self._try_init_psutil()
        self._cpu_percent: float = 0.0
        self._last_cpu_time: float = 0.0
        self._prev_cpu_total: int = 0
        self._prev_cpu_idle: int = 0
        self._have_prev_cpu: bool = False
        self._mem_percent: float = 0.0
        self._last_mem_time: float = 0.0

    @staticmethod
    def _detect_platform() -> str:
        raw = platform.system().lower()
        if "cygwin" in raw or (sys.platform and "cygwin" in sys.platform):
            return "cygwin"
        if raw == "linux":
            return "linux"
        if raw == "darwin":
            return "darwin"
        if raw == "windows":
            return "windows"
        return "unknown"

    def _try_init_psutil(self) -> None:
        try:
            import psutil
            self._psutil = psutil
            self._has_psutil = True
        except ImportError:
            self._psutil = None
            self._has_psutil = False

    def get_cpu_percent(self) -> float:
        now = time.monotonic()
        if now - self._last_cpu_time < self.CPU_CACHE_TTL:
            return self._cpu_percent
        self._last_cpu_time = now
        try:
            if self._has_psutil:
                self._cpu_percent = float(self._psutil.cpu_percent(interval=0))
            elif self._platform in ("linux", "cygwin"):
                self._cpu_percent = self._read_cpu_proc_stat()
            elif self._platform == "darwin":
                self._cpu_percent = self._read_cpu_macos()
            elif self._platform == "windows":
                self._cpu_percent = self._read_cpu_windows()
            else:
                self._cpu_percent = 0.0
        except Exception:
            _logger.warning("Failed to read CPU usage", exc_info=True)
            self._cpu_percent = 0.0
        self._cpu_percent = max(0.0, min(100.0, self._cpu_percent))
        return self._cpu_percent

    def get_memory_percent(self) -> float:
        now = time.monotonic()
        if now - self._last_mem_time < self.MEM_CACHE_TTL:
            return self._mem_percent
        self._last_mem_time = now
        try:
            if self._has_psutil:
                self._mem_percent = float(self._psutil.virtual_memory().percent)
            elif self._platform in ("linux", "cygwin"):
                self._mem_percent = self._read_mem_proc_meminfo()
            elif self._platform == "darwin":
                self._mem_percent = self._read_mem_macos()
            elif self._platform == "windows":
                self._mem_percent = self._read_mem_windows()
            else:
                self._mem_percent = 0.0
        except Exception:
            _logger.warning("Failed to read memory usage", exc_info=True)
            self._mem_percent = 0.0
        self._mem_percent = max(0.0, min(100.0, self._mem_percent))
        return self._mem_percent

    def get_cpu_and_mem(self) -> tuple[float, float]:
        self.get_cpu_percent()
        self.get_memory_percent()
        return (self._cpu_percent, self._mem_percent)

    def _read_cpu_proc_stat(self) -> float:
        try:
            with open("/proc/stat") as f:
                line = f.readline()
        except (OSError, IOError):
            return 0.0
        parts = line.split()
        if not parts or parts[0] != "cpu":
            return 0.0
        values = []
        for v in parts[1:]:
            try:
                values.append(int(v))
            except (ValueError, IndexError):
                break
        if len(values) < 4:
            return 0.0
        total = sum(values)
        idle = values[3]
        if not self._have_prev_cpu:
            self._prev_cpu_total = total
            self._prev_cpu_idle = idle
            self._have_prev_cpu = True
            return 0.0
        delta_total = total - self._prev_cpu_total
        delta_idle = idle - self._prev_cpu_idle
        self._prev_cpu_total = total
        self._prev_cpu_idle = idle
        if delta_total <= 0:
            return 0.0
        return 100.0 * (1.0 - delta_idle / delta_total)

    def _read_cpu_macos(self) -> float:
        try:
            result = subprocess.run(
                ["iostat", "-c", "2", "2"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            lines = result.stdout.strip().splitlines()
            if len(lines) >= 4:
                parts = lines[-1].split()
                if len(parts) >= 6:
                    idle_str = parts[-1].replace("%", "")
                    return max(0.0, 100.0 - float(idle_str))
            return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: iostat -c 2 2", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_cpu_windows(self) -> float:
        try:
            result = subprocess.run(
                ["wmic", "cpu", "get", "loadpercentage", "/format:value"],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.startswith("LoadPercentage="):
                    return float(line.split("=", 1)[1].strip())
            return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: wmic cpu get loadpercentage", exc_info=True)
            return 0.0
        except (OSError, ValueError, IndexError):
            return 0.0

    def _read_mem_proc_meminfo(self) -> float:
        meminfo: dict[str, int] = {}
        try:
            with open("/proc/meminfo") as f:
                for line in f:
                    if ":" not in line:
                        continue
                    key, rest = line.split(":", 1)
                    key = key.strip()
                    val_str = rest.strip().split()[0]
                    try:
                        meminfo[key] = int(val_str)
                    except (ValueError, IndexError):
                        continue
        except (OSError, IOError):
            return 0.0
        total = meminfo.get("MemTotal", 0)
        if total <= 0:
            return 0.0
        available = meminfo.get("MemAvailable")
        if available is not None and available > 0:
            used = total - available
            return 100.0 * used / total
        free = meminfo.get("MemFree", 0)
        cached = meminfo.get("Cached", 0)
        buffers = meminfo.get("Buffers", 0)
        used = total - free - cached - buffers
        return 100.0 * max(0, used) / total

    def _read_mem_macos(self) -> float:
        total_bytes = 0
        try:
            result = subprocess.run(
                ["sysctl", "-n", "hw.memsize"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode == 0:
                total_bytes = int(result.stdout.strip())
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: sysctl -n hw.memsize", exc_info=True)
            return 0.0
        except (OSError, ValueError):
            return 0.0
        if total_bytes <= 0:
            return 0.0
        try:
            result = subprocess.run(
                ["vm_stat"],
                capture_output=True, text=True, timeout=3.0,
            )
            if result.returncode != 0:
                return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: vm_stat", exc_info=True)
            return 0.0
        except OSError:
            return 0.0
        page_size = 4096
        active_pages = 0
        wired_pages = 0
        compressed_pages = 0
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if "page size of" in line:
                m = re.search(r"page size of (\d+)", line)
                if m:
                    page_size = int(m.group(1))
            elif line.startswith("Pages active:"):
                try:
                    active_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
            elif line.startswith("Pages wired down:"):
                try:
                    wired_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
            elif line.startswith("Pages stored in compressor:"):
                try:
                    compressed_pages = int(line.split(":")[-1].strip().rstrip("."))
                except ValueError:
                    pass
        used_bytes = (active_pages + wired_pages + compressed_pages) * page_size
        return 100.0 * used_bytes / total_bytes

    def _read_mem_windows(self) -> float:
        try:
            result = subprocess.run(
                [
                    "wmic", "OS", "get",
                    "TotalVisibleMemorySize,FreePhysicalMemory",
                    "/format:value",
                ],
                capture_output=True, text=True, timeout=5.0,
            )
            if result.returncode != 0:
                return 0.0
        except subprocess.TimeoutExpired:
            _logger.warning("子进程超时: wmic OS get TotalVisibleMemorySize,FreePhysicalMemory", exc_info=True)
            return 0.0
        except OSError:
            return 0.0
        total_kb = 0
        free_kb = 0
        for line in result.stdout.strip().splitlines():
            line = line.strip()
            if line.startswith("TotalVisibleMemorySize="):
                try:
                    total_kb = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
            elif line.startswith("FreePhysicalMemory="):
                try:
                    free_kb = int(line.split("=", 1)[1].strip())
                except (ValueError, IndexError):
                    pass
        if total_kb <= 0:
            return 0.0
        return 100.0 * (total_kb - free_kb) / total_kb


# ═══════════════════════════════════════════════════════════
# _CompletionPopup — 补全弹窗
# ═══════════════════════════════════════════════════════════

class _CompletionPopup:
    """补全弹窗 — 无边框扁平样式，在输入区顶部绘制。"""

    _COMPLETION_MAX_ITEMS = 999

    def __init__(self, cursor_tracker: "CursorTracker | None" = None):
        self._visible = False
        self._title = "补全"
        self._items: list[str] = []
        self._texts: list[str] = []
        self._start_pos: int = 0
        self._orig_prefix: str = ""
        self._types: list[str] = []
        self._match_prefix: str = ""
        self._is_selection: bool = False
        self._idx: int = 0
        self._last_idx_before_hide: int = 0
        self._popup_height: int = 0
        self._animator = _SimpleAnimator.get_default()
        self._tracker = cursor_tracker

    @property
    def is_visible(self) -> bool:
        return self._visible

    @property
    def height(self) -> int:
        return self._popup_height

    @property
    def idx(self) -> int:
        return self._idx

    def cycle(self, delta: int = 1) -> int:
        if not self._visible or not self._items:
            return 0
        n = len(self._items)
        self._idx = (self._idx + delta) % n
        return self._idx

    def get_selected(self) -> tuple[str, int, str]:
        if not self._visible or not self._texts:
            return ("", 0, "")
        idx = min(self._idx, len(self._texts) - 1)
        return (self._texts[idx], self._start_pos, self._orig_prefix)

    @staticmethod
    def _calc_popup_width(items: list[str], term_width: int) -> int:
        if not items:
            return min(term_width - 2, 50)
        max_w = max(_visual_width(item) for item in items)
        return min(max(max_w + 4, 20), term_width - 2)

    def _render_item_line(self, out, r: int, item: str, item_type: str,
                          match_prefix: str, cell_w: int, is_selected: bool) -> None:
        truncated_raw = _truncate_by_width(item, cell_w)
        display = self._render_display_text(truncated_raw, item_type, match_prefix)
        pad = " " * max(0, cell_w - _visual_width(truncated_raw))
        if is_selected:
            if not _is_narrow():
                bg_color = self._animator.sine_color(235, 240, 10)
                bg_ansi = f"\033[48;5;{bg_color}m"
            else:
                bg_ansi = _COLOR_SELECT_BG
            out.write(
                f"{cursor_goto(r, 1)}\033[K"
                f" {bg_ansi}{_COLOR_SELECT_FG}\u25b6{_COLOR_RESET}"
                f"{bg_ansi}{_COLOR_SELECT_FG} {display}{pad}{_COLOR_RESET}"
            )
        else:
            out.write(f"{cursor_goto(r, 1)}\033[K  {display}{pad}")

    def _render_display_text(self, text: str, item_type: str, match_prefix: str) -> str:
        if item_type == "command" and text.startswith("/"):
            cmd_rest = text[1:]
            if match_prefix and len(match_prefix) > 1 and cmd_rest.startswith(match_prefix[1:]):
                inner = match_prefix[1:]
                matched = cmd_rest[:len(inner)]
                rest = cmd_rest[len(inner):]
                return (
                    f"{_COLOR_COMPLETE_CMD_PREFIX}/{_COLOR_RESET}"
                    f"{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{rest}"
                )
            return f"{_COLOR_COMPLETE_CMD_PREFIX}/{_COLOR_RESET}{cmd_rest}"
        if item_type == "dir" and text.endswith("/"):
            return f"{_COLOR_COMPLETE_DIR}{text}{_COLOR_RESET}"
        if item_type == "session":
            base = f"{_COLOR_TIME}{text}{_COLOR_RESET}"
        else:
            base = text
        if match_prefix and text.startswith(match_prefix):
            matched = text[:len(match_prefix)]
            rest = text[len(match_prefix):]
            if item_type == "session":
                return f"{_COLOR_TIME}{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{_COLOR_TIME}{rest}{_COLOR_RESET}"
            return f"{_COLOR_COMPLETE_MATCH}{matched}{_COLOR_RESET}{rest}"
        return base

    def render(self, out, r_start: int, term_width: int) -> int:
        popup_height = self._popup_height
        if popup_height <= 0 or not self._items:
            return 0
        popup_w = self._calc_popup_width(self._items, term_width)
        n = len(self._items)
        total_items = len(self._texts)
        if not _is_narrow():
            title_color = self._animator.sine_color(45, 81, 12)
            title_ansi = f"\033[1;38;5;{title_color}m"
        else:
            title_ansi = _COLOR_COMPLETE_TITLE
        header = f" {title_ansi}{self._title}{_COLOR_RESET} {_COLOR_DIM}({total_items}项){_COLOR_RESET}"
        out.write(f"{cursor_goto(r_start, 1)}\033[K" + header)
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = r_start + 1 + i
            self._render_item_line(out, r, item, types[i], self._match_prefix, cell_w,
                                   is_selected=(i == self._idx))
        footer_r = r_start + 1 + n
        truncated = total_items > n
        is_selection = self._is_selection
        hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
        if truncated:
            hint = (f" {_COLOR_TIME}{self._idx + 1}/{n}{_COLOR_RESET}"
                    f" {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} ")
        else:
            hint = f" {hint_prefix} "
        if not _is_narrow():
            dot_color = self._animator.sine_color(45, 81, 12)
            hint_dot = f" \033[38;5;{dot_color}m\u25c9{_COLOR_RESET}"
        else:
            hint_dot = ""
        out.write(f"{cursor_goto(footer_r, 1)}\033[K" + f"{_COLOR_DIM}{hint}{_COLOR_RESET}{hint_dot}")
        return popup_height

    def render_cycle_update(self, out, popup_r_start: int, term_width: int) -> None:
        if not self._visible or not self._items:
            return
        self._animator.tick()
        n = len(self._items)
        popup_w = self._calc_popup_width(self._items, term_width)
        cell_w = popup_w - 3
        types = self._types if len(self._types) == n else [""] * n
        for i, item in enumerate(self._items):
            r = popup_r_start + 1 + i
            self._render_item_line(out, r, item, types[i], self._match_prefix, cell_w,
                                   is_selected=(i == self._idx))
        total_items = len(self._texts)
        footer_r = popup_r_start + 1 + n
        truncated = total_items > n
        is_selection = self._is_selection
        hint_prefix = "\u2191\u2193 Enter Esc" if is_selection else "Tab \u2191\u2193 Esc"
        if truncated:
            hint = (f" {_COLOR_TIME}{self._idx + 1}/{n}{_COLOR_RESET}"
                    f" {_COLOR_DIM}(\u524d{n}/{total_items}){_COLOR_RESET}  {hint_prefix} ")
        else:
            hint = f" {hint_prefix} "
        if not _is_narrow():
            dot_color = self._animator.sine_color(45, 81, 12)
            hint_dot = f" \033[38;5;{dot_color}m\u25c9{_COLOR_RESET}"
        else:
            hint_dot = ""
        out.write(f"{cursor_goto(footer_r, 1)}\033[K" + f"{_COLOR_DIM}{hint}{_COLOR_RESET}{hint_dot}")


# ═══════════════════════════════════════════════════════════
# 阶段→显示文本映射
# ═══════════════════════════════════════════════════════════

_PHASE_DISPLAY: dict[str, str] = {
    "thinking": "思考",
    "answering": "回答",
    "parsing": "接收工具参数",
}


def _build_status_text(status_active: bool, main_phase: str, main_phase_start: float,
                       tool_count: int, tool_phase_start: float) -> str:
    """构建分隔线状态文本（纯文本，不含 ANSI 颜色）。"""
    if tool_count > 0:
        status = "工具调用中"
        start_time = tool_phase_start
    elif main_phase in _PHASE_DISPLAY:
        status = _PHASE_DISPLAY[main_phase]
        start_time = main_phase_start
    else:
        return ""
    if start_time <= 0.0:
        return ""
    elapsed = time.monotonic() - start_time
    return f"\u00b7 {status} {elapsed:.2f}s"


# ═══════════════════════════════════════════════════════════
# _BottomBar — 终端底部固定输入栏
# ═══════════════════════════════════════════════════════════

class _BottomBar:
    """终端底部固定输入栏，流式输出期间始终可见。

    使用 ANSI DECSTBM 滚动区域：上方内容区正常滚动，
    底部行位于滚动区域之外，通过手动定位绘制保持固定。
    """

    _MIN_HEIGHT = _BOTTOM_MIN_HEIGHT

    def __init__(self, cursor_tracker: "CursorTracker | None" = None):
        self._active = False
        self._last_text = ""
        self._last_status = ""
        # _StatusMixin 字段
        self._status_active: bool = False
        self._model_name: str = ""
        self._tool_count: int = 0
        self._tool_fail_count: int = 0
        self._tool_total: int = 0
        self._subagent_lines: list[str] = []
        self._last_subagent_lines: list[str] = []
        self._main_phase: str = ""
        self._main_phase_start: float = 0.0
        self._tool_phase_start: float = 0.0
        # 布局/光标
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._input_cursor_pos: int = -1
        self._last_cursor_pos: int = -1
        self._cached_wrapped_for: str = ""
        self._cached_wrapped_width: int = 0
        self._cached_wrapped_lines: list[str] | None = None
        self._cached_input_rows: int = _MIN_INPUT_ROWS
        self._last_rendered_text: str = ""
        self._last_scroll_end: int = 0
        self._last_height: int = 0
        self._last_sync_height: int = 0
        # 补全弹窗
        self._completion = _CompletionPopup(cursor_tracker=cursor_tracker)
        # stdout 行追踪器
        from ._stdout_tracker import _StdoutLineTracker
        self._tracker: _StdoutLineTracker | None = None
        # 光标坐标追踪器
        from ._cursor_tracker import CursorTracker as _CT
        self._cursor_tracker = cursor_tracker or _CT()
        # 动画时钟
        self._animator = _SimpleAnimator.get_default()
        # 终端尺寸缓存
        self._width_cache = TerminalWidthCache.get_default()
        self._sigwinch_cb: Any = None
        self._needs_full_repaint: bool = False
        self._request_redraw_cb: Callable[[], None] | None = None
        self._input: "Input | None" = None
        # 系统监控
        self._system_monitor: _SystemMonitor | None = None
        self._cached_cpu_percent: float = 0.0
        self._cached_mem_percent: float = 0.0
        self._last_system_stats_time: float = 0.0
        self._SYSTEM_STATS_INTERVAL: float = 1.0

    # ── 属性 ──────────────────────────────────────

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def is_completion_visible(self) -> bool:
        return self._completion.is_visible

    @property
    def is_status_active(self) -> bool:
        return self._status_active

    @property
    def _completion_idx(self) -> int:
        return self._completion._idx

    @_completion_idx.setter
    def _completion_idx(self, value: int) -> None:
        self._completion._idx = value

    @property
    def _completion_popup_height(self) -> int:
        return self._completion._popup_height

    @_completion_popup_height.setter
    def _completion_popup_height(self, value: int) -> None:
        self._completion._popup_height = value

    @property
    def _bottom_lines(self) -> int:
        return 2 + len(self._subagent_lines) + self._compute_input_rows()

    # ── 尺寸查询 ──────────────────────────────────

    def _term_height(self) -> int:
        _, h = _get_terminal_size()
        return h or 24

    def _term_width(self) -> int:
        w, _ = _get_terminal_size()
        return w or 80

    def _compute_input_rows(self) -> int:
        text = self._last_text or ""
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            max_input = max(1, self._term_width() - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return 2 + base + self._completion.height

    def _compute_bottom_lines_for(self, text: str, term_width: int) -> int:
        if not text:
            base = _MIN_INPUT_ROWS
        else:
            max_input = max(1, term_width - 4)
            expanded = _expand_tabs(text)
            wrapped = _wrap_by_width(expanded, max_input)
            base = max(_MIN_INPUT_ROWS, len(wrapped))
        return 4 + len(self._subagent_lines) + base + self._completion.height

    # ── 系统监控 ──────────────────────────────────

    def _update_system_stats(self) -> None:
        now = time.monotonic()
        if now - self._last_system_stats_time < self._SYSTEM_STATS_INTERVAL:
            return
        self._last_system_stats_time = now
        if self._system_monitor is None:
            self._system_monitor = _SystemMonitor()
        try:
            cpu_pct, mem_pct = self._system_monitor.get_cpu_and_mem()
            self._cached_cpu_percent = cpu_pct
            self._cached_mem_percent = mem_pct
        except Exception:
            pass

    # ── resize 保护 ───────────────────────────────

    def set_full_repaint_needed(self) -> None:
        self._needs_full_repaint = True

    def force_refresh_dimensions(self) -> None:
        self._width_cache.force_refresh()
        self.set_full_repaint_needed()

    def set_request_redraw_cb(self, cb: Callable[[], None] | None) -> None:
        """设置请求重绘回调（由 TuiEngine.request_bottom_redraw 驱动）。

        Args:
            cb: 无参回调，SIGWINCH 时调用以触发底部栏重绘和光标重定位。
        """
        self._request_redraw_cb = cb

    # ── 光标定位 ──────────────────────────────────

    def get_scroll_end(self) -> int:
        return self._last_scroll_end

    def get_cursor_info(self) -> tuple[str, int, int, int]:
        text = self._last_rendered_text if self._last_rendered_text else self._last_text
        cursor_pos = min(self._input_cursor_pos, len(text))
        return (text, cursor_pos, self._term_height(), self._term_width())

    def compute_cursor_position(self, text: str, cursor_pos: int, h: int, w: int) -> tuple[int, int]:
        if self._input is not None:
            bottom_for_text = self._compute_bottom_lines_for(text, w)
            r_cursor, cursor_col, _, _ = self._input.compute_cursor(
                text, cursor_pos, bottom_for_text,
                len(self._subagent_lines), self._completion.height,
            )
            return (r_cursor, cursor_col)
        max_input = max(1, w - 4)
        vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
        total_bottom = max(5, self._compute_bottom_lines_for(text, w))
        popup_offset = self._completion.height
        subagent_offset = len(self._subagent_lines)
        r_cursor = max(1, h - total_bottom + 4 + subagent_offset + popup_offset + vis_row)
        cursor_col = min(3 + vis_col, w)
        return (r_cursor, cursor_col)

    # ── 同步滚动区域 ──────────────────────────────

    def sync_bottom_lines(self) -> None:
        if not self._active:
            return
        height = self._term_height()
        scroll_end = height - self._bottom_lines
        if scroll_end == self._last_scroll_end and height == self._last_sync_height:
            return
        resized = height != self._last_sync_height
        if scroll_end < 1:
            scroll_end = height
        old_scroll = self._last_scroll_end
        self._last_scroll_end = scroll_end
        self._last_sync_height = height
        if self._tracker is not None:
            self._tracker.set_scroll_end(scroll_end)
        out = sys.__stdout__
        _buf = [set_scroll_region(1, scroll_end)]
        if not resized:
            if scroll_end >= 1:
                _buf.append(f"{cursor_goto(scroll_end, 1)}\033[K")
                if old_scroll > scroll_end:
                    for r in range(scroll_end + 1, min(old_scroll, height) + 1):
                        _buf.append(f"{cursor_goto(r, 1)}\033[K")
                elif old_scroll < scroll_end:
                    for r in range(old_scroll + 1, scroll_end + 1):
                        _buf.append(f"{cursor_goto(r, 1)}\033[K")
        if resized:
            if old_scroll > scroll_end:
                for r in range(scroll_end + 1, min(old_scroll, height) + 1):
                    _buf.append(f"{cursor_goto(r, 1)}\033[K")
            elif old_scroll < scroll_end:
                for r in range(old_scroll + 1, scroll_end + 1):
                    _buf.append(f"{cursor_goto(r, 1)}\033[K")
            _buf.append(cursor_goto(scroll_end, 1) + cursor_save())
        else:
            _buf.append(cursor_goto(scroll_end, 1) + cursor_save())
        out.write(''.join(_buf))
        out.flush()

    # ── 光标区域切换 ──────────────────────────────

    def ensure_cursor_in_upper(self) -> None:
        if not self._active:
            return
        scroll_end = self._last_scroll_end
        if scroll_end < 1:
            scroll_end = self._term_height()
        sys.__stdout__.write(cursor_goto(scroll_end, 1))
        self._cursor_tracker.set(scroll_end, 1)

    def ensure_cursor_in_lower(self) -> None:
        if not self._active:
            return
        with _try_acquire_output_lock(name="bottom_bar.ensure_cursor_in_lower", timeout=0.3) as locked:
            if not locked:
                return
            height = self._term_height()
            term_w = self._term_width()
            text = self._last_rendered_text if self._last_rendered_text else self._last_text
            cursor_pos = min(self._input_cursor_pos, len(text))
            if self._input is not None:
                total = max(_BOTTOM_MIN_LINES, self._last_bottom_lines)
                r_cursor, col, _, _ = self._input.compute_cursor(
                    text, cursor_pos, total,
                    len(self._subagent_lines), self._completion.height,
                )
            else:
                max_input = max(1, term_w - 4)
                vis_row, vis_col = _compute_cursor_visual_pos(text, cursor_pos, max_input)
                total = max(_BOTTOM_MIN_LINES, self._last_bottom_lines)
                subagent_offset = len(self._subagent_lines)
                r_cursor = height - total + 4 + subagent_offset + self._completion.height + vis_row
                r_cursor = max(1, min(r_cursor, height))
                col = min(3 + vis_col, term_w)
            sys.__stdout__.write(cursor_goto(r_cursor, col))
            sys.__stdout__.flush()
            self._cursor_tracker.set(r_cursor, col)

    # ── 子Agent面板 ───────────────────────────────

    def set_subagent_frame(self, lines: list[str]) -> None:
        self._subagent_lines = list(lines)

    # ── 生命周期 ──────────────────────────────────

    def set_input(self, input_instance: "Input") -> None:
        self._input = input_instance

    def set_input_state(self, text: str, cursor_pos: int) -> None:
        self._last_text = text
        self._input_cursor_pos = cursor_pos

    def set_main_phase(self, phase: str) -> None:
        if phase != self._main_phase:
            self._main_phase_start = time.monotonic()
        self._main_phase = phase

    def _register_sigwinch(self) -> None:
        def _on_sigwinch(cols: int, rows: int) -> None:
            self._width_cache.force_refresh()
            self._needs_full_repaint = True
            # 通知引擎立即触发重绘和光标重定位
            if self._request_redraw_cb is not None:
                try:
                    self._request_redraw_cb()
                except Exception:
                    pass
        self._sigwinch_cb = _on_sigwinch
        register_sigwinch_callback(self._sigwinch_cb)

    def setup(self) -> None:
        if self._active:
            return
        height = self._term_height()
        if height < self._MIN_HEIGHT:
            return
        self._active = True
        self._register_sigwinch()
        from ._stdout_tracker import _StdoutLineTracker as _ST
        if self._tracker is None:
            self._tracker = _ST(sys.__stdout__)
        if sys.__stdout__ is not self._tracker:
            sys.__stdout__ = self._tracker
        with _try_acquire_output_lock(name="bottom_bar.setup", timeout=1.0) as locked:
            if locked:
                self._last_text = ""
                self._last_bottom_lines = self._bottom_lines
                scroll_end = height - self._bottom_lines
                self._last_scroll_end = scroll_end
                self._last_sync_height = height
                self._tracker.set_scroll_end(scroll_end)
                out = sys.__stdout__
                _buf = [
                    cursor_save(),
                    set_scroll_region(1, scroll_end),
                    cursor_restore(),
                    cursor_goto(scroll_end, 1) + cursor_save(),
                    cursor_goto(height, 1),
                ]
                out.write(''.join(_buf))
                out.flush()
            else:
                sys.__stdout__.write("\n" + "\u2501" * 40 + "\n")
                sys.__stdout__.flush()

    def teardown(self) -> None:
        if not self._active:
            return
        self._active = False
        if self._sigwinch_cb is not None:
            try:
                from src.tui._screen import unregister_sigwinch_callback
                unregister_sigwinch_callback(self._sigwinch_cb)
            except Exception:
                pass
            self._sigwinch_cb = None
        if self._tracker is not None and sys.__stdout__ is self._tracker:
            sys.__stdout__ = self._tracker._real_stdout
            try:
                self._tracker._flush_history()
            except Exception:
                pass
            self._tracker = None
        with _try_acquire_output_lock(name="bottom_bar.teardown", timeout=1.0) as locked:
            if locked:
                out = sys.__stdout__
                height = self._term_height()
                start_row = max(1, height - self._last_bottom_lines + 1)
                _buf = [reset_scroll_region(), cursor_save()]
                for r in range(start_row, height + 1):
                    _buf.append(f"{cursor_goto(r, 1)}\033[K")
                _buf.append(cursor_restore())
                _buf.append(cursor_save())
                out.write(''.join(_buf))
                out.flush()
        self._last_bottom_lines = _BOTTOM_MIN_LINES
        self._last_height = 0
        self._last_sync_height = 0

    # ── force_redraw ──────────────────────────────

    def force_redraw(self) -> None:
        if not self._active:
            return
        self._animator.tick()
        self._update_system_stats()
        height = self._term_height()
        with _try_acquire_output_lock(name="bottom_bar.force_redraw", timeout=1.0) as locked:
            if not locked:
                return
            try:
                text = self._last_text
                total = self._bottom_lines
                new_status = self._format_status()
                old_bottom_lines = self._last_bottom_lines
                scroll_end = height - total
                delta = total - old_bottom_lines
                old_scroll_end = (
                    (self._last_height if self._last_height > 0 else height) - old_bottom_lines
                )
                self._last_status = new_status
                self._last_subagent_lines = list(self._subagent_lines)
                out = sys.__stdout__
                out.write(cursor_save())
                out.write(reset_scroll_region())
                self._last_bottom_lines = total
                full_repaint = self._needs_full_repaint
                self._needs_full_repaint = False

                # SU 上滚
                if delta > 0 and old_scroll_end > 0 and not full_repaint:
                    out.write(set_scroll_region(1, old_scroll_end))
                    out.write(cursor_goto(old_scroll_end, 1))
                    out.write(scroll_up(delta))
                    out.write(reset_scroll_region())

                # 终端过小
                if scroll_end < 1:
                    for r in range(1, height + 1):
                        out.write(f"{cursor_goto(r, 1)}\033[K")
                    out.write(cursor_restore())
                    out.write(cursor_goto(height, 1) + cursor_save())
                    out.flush()
                    self._cursor_tracker.set(height, 1)
                    self._last_cursor_pos = self._input_cursor_pos
                    self._last_height = height
                    self._last_scroll_end = height
                    if self._tracker is not None:
                        self._tracker.set_scroll_end(height)
                    return

                # 清除旧区域
                if full_repaint:
                    clear_start = scroll_end + 1
                else:
                    clear_start = max(old_scroll_end, scroll_end) + 1
                clear_end = height
                clear_buf: list[str] = []
                for r in range(clear_start, clear_end + 1):
                    clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
                if not full_repaint and self._last_height > 0 and height < self._last_height:
                    for r in range(max(scroll_end + 1, 1), min(old_scroll_end, height) + 1):
                        clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
                elif not full_repaint and self._last_height > 0 and height > self._last_height:
                    for r in range(old_scroll_end + 1, scroll_end + 1):
                        clear_buf.append(f"{cursor_goto(r, 1)}\033[K")
                # ★ 修Bug：full_repaint 时滚动区域扩大（scroll_end > old_scroll_end），
                #   新内容区行 [old_scroll_end+1, scroll_end] 须清除以防残留旧内容
                if full_repaint and scroll_end > old_scroll_end and self._last_height > 0:
                    for r in range(old_scroll_end + 1, scroll_end + 1):
                        clear_buf.append(f"{cursor_goto(r, 1)}\033[K")

                r1 = height - total + 1
                subagent_start = r1 + 1
                r2 = subagent_start + len(self._subagent_lines)
                tw = self._term_width()

                # 分隔线
                if _is_narrow():
                    sep_len = min(tw - 2, 40)
                    sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * sep_len
                    clear_buf.append(f"{cursor_goto(r1, 1)}  {sep}")
                else:
                    sep_start = 45
                    if self._animator.breath_frame > 0:
                        sep_start = self._animator.sine_color(40, 45, 10)
                    status_text = _build_status_text(
                        self._status_active, self._main_phase, self._main_phase_start,
                        self._tool_count, self._tool_phase_start,
                    ) if self._status_active else ""
                    if self._status_active and status_text:
                        status_colored = f"{_COLOR_ACCENT}{status_text}{_COLOR_RESET}"
                        remaining = max(1, tw - 2 - _visual_width(status_text) - 1)
                        sep = _build_gradient(remaining, start_color=sep_start)
                        clear_buf.append(f"{cursor_goto(r1, 1)}  {status_colored} {sep}")
                    else:
                        sep = _build_gradient(tw - 2, start_color=sep_start)
                        clear_buf.append(f"{cursor_goto(r1, 1)}  {sep}")

                # subagent 面板行（每行按终端宽度截断，防止折行破坏布局）
                for i, line in enumerate(self._subagent_lines):
                    sr = subagent_start + i
                    line = _ansi_truncate(line, tw)
                    clear_buf.append(f"{cursor_goto(sr, 1)}\033[K" + line)

                # 状态行
                clear_buf.append(f"{cursor_goto(r2, 1)}\033[K" + new_status)
                out.write(''.join(clear_buf))

                # 输入行
                self._draw_input_lines(out, text, r2 + 1, tw)
                input_rows = self._cached_input_rows

                # 清除底部残留 + 设置 DECSTBM
                for r in range(r2 + 1 + input_rows, height + 1):
                    out.write(f"{cursor_goto(r, 1)}\033[K")
                self._last_scroll_end = scroll_end
                if self._tracker is not None:
                    self._tracker.set_scroll_end(scroll_end)
                out.write(set_scroll_region(1, scroll_end))
                if delta < 0 and old_scroll_end > 0 and not full_repaint:
                    for r in range(old_scroll_end + 1, scroll_end + 1):
                        out.write(f"{cursor_goto(r, 1)}\033[K")
                out.write(cursor_restore())
                out.write(cursor_goto(scroll_end, 1) + cursor_save())
                out.flush()
                self._last_cursor_pos = self._input_cursor_pos
                self._last_height = height
            except (OSError, ValueError, AttributeError):
                _logger.warning("force_redraw 写入失败", exc_info=True)
                # 终端状态恢复（PTY 断开后 sgr_reset 也可能失败，用 try/except 包裹）
                try:
                    sgr_reset()
                except Exception:
                    pass
                return

    # ── 输入行绘制 ────────────────────────────────

    def _draw_input_lines(self, out, text: str, r_start: int, term_width: int) -> None:
        """绘制输入行（含补全弹窗、CPU/MEM 行、输入文本行、时间戳行）。

        调用时需确保 render_lock 已被持有（由 ``force_redraw`` 中的
        ``_try_acquire_output_lock`` 保证）。此方法名中的 ``_locked``
        语义已移除——实际锁由调用方通过 ``_try_acquire_output_lock``
        控制，本方法不自行获取或释放任何锁。
        """
        max_input = max(1, term_width - 4)
        expanded = _expand_tabs(text)
        wrapped = _wrap_by_width(expanded, max_input)
        self._cached_wrapped_for = text
        self._cached_wrapped_width = max_input
        self._cached_wrapped_lines = wrapped
        base_rows = max(_MIN_INPUT_ROWS, len(wrapped))
        self._cached_input_rows = base_rows + self._completion.height + 2
        self._last_rendered_text = text

        # 补全弹窗
        self._completion.render(out, r_start, term_width)
        popup_height = self._completion.height
        text_start = r_start + popup_height

        # 上分割线（CPU/MEM）
        cpu_int = max(0, min(100, round(self._cached_cpu_percent)))
        mem_int = max(0, min(100, round(self._cached_mem_percent)))
        cpu_mem_info = (
            f" {_COLOR_ACCENT}CPU:{_COLOR_RESET}"
            f" {_COLOR_SPEED}{cpu_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
            f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
            f"{_COLOR_ACCENT}MEM:{_COLOR_RESET}"
            f" {_COLOR_SPEED}{mem_int}{_COLOR_ACCENT}%{_COLOR_RESET}"
        )
        cpu_mem_w = _visual_width(cpu_mem_info)
        top_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - cpu_mem_w) + cpu_mem_info
        out.write(f"{cursor_goto(text_start, 1)}\033[K" + top_sep)

        # 输入文本行
        buf: list[str] = []
        for i, segment in enumerate(wrapped):
            r = text_start + 1 + i
            if i == 0:
                if _is_narrow():
                    prompt_color = _COLOR_DEEP_CYAN
                    prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
                else:
                    prompt_color = _build_glow_ansi(self._animator.breath_frame, 32, 49)
                    prompt_prefix = f"{prompt_color}>{_COLOR_RESET} "
                if text:
                    buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + segment)
                else:
                    if _is_narrow():
                        placeholder_color = _COLOR_DIM
                    else:
                        placeholder_color = _build_glow_ansi(self._animator.breath_frame, 242, 10)
                    if self._status_active:
                        ph = _PLACEHOLDER_STREAMING
                        buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
                    else:
                        ph = _PLACEHOLDER_COMPACT if self._completion.is_visible else _PLACEHOLDER_TEXT
                        buf.append(f"{cursor_goto(r, 1)}\033[K" + prompt_prefix + f"{placeholder_color}{ph}\033[0m")
            else:
                buf.append(f"{cursor_goto(r, 1)}\033[K" + f"{_COLOR_DIM}\u00b7{_COLOR_RESET} {segment}")

        # 下分割线（时间戳）
        now_local = time.localtime()
        ts = (
            f"{now_local.tm_year}-{now_local.tm_mon:02d}-"
            f"{now_local.tm_mday:02d} {now_local.tm_hour:02d}:"
            f"{now_local.tm_min:02d}:{now_local.tm_sec:02d}"
        )
        time_info = f" {_COLOR_DIM}{ts}{_COLOR_RESET}"
        time_w = _visual_width(time_info)
        bottom_sep = f"{_COLOR_SEP}\u2501{_COLOR_RESET}" * max(1, term_width - time_w) + time_info
        bottom_sep_row = text_start + 1 + base_rows
        buf.append(f"{cursor_goto(bottom_sep_row, 1)}\033[K" + bottom_sep)
        if buf:
            out.write(''.join(buf))

    # ── 补全弹窗委托 ──────────────────────────────

    def show_completions(self, items: list[str], selected_idx: int,
                         texts: list[str] | None = None, start_pos: int = 0,
                         orig_prefix: str = "", title: str = "补全",
                         types: list[str] | None = None,
                         match_prefix: str = "") -> None:
        if not items or not self._active:
            return
        total_items = len(items)
        h_items = min(total_items, _CompletionPopup._COMPLETION_MAX_ITEMS)
        popup_height = h_items + 2
        max_avail = self._term_height() - 7
        if max_avail <= 0:
            return
        if popup_height > max_avail:
            h_items = max(1, max_avail - 2)
            popup_height = h_items + 2
        visible_items = items[:h_items]
        selected_idx = min(selected_idx, h_items - 1)
        self._completion._popup_height = popup_height
        self._completion._visible = True
        self._completion._title = title
        self._completion._is_selection = (title != "补全")
        self._completion._items = list(visible_items)
        self._completion._texts = list(texts) if texts is not None else list(visible_items)
        self._completion._idx = selected_idx
        self._completion._start_pos = start_pos
        self._completion._orig_prefix = orig_prefix
        self._completion._types = list(types) if types is not None else []
        self._completion._match_prefix = match_prefix
        self.force_redraw()

    def hide_completions(self) -> None:
        if not self._completion.is_visible or not self._active:
            return
        # ★ 保存最后选中的索引（供 Enter 后读取，防止竞态）
        # 使用局部变量确保读取原子性，防止并发线程中 _idx 被修改
        saved_idx = self._completion._idx
        self._completion._last_idx_before_hide = saved_idx
        self._completion._popup_height = 0
        self._completion._visible = False
        self._completion._title = "补全"
        self._completion._is_selection = False
        self._completion._items = []
        self._completion._texts = []
        self._completion._idx = 0
        self._completion._start_pos = 0
        self._completion._orig_prefix = ""
        self._completion._types = []
        self._completion._match_prefix = ""
        self.force_redraw()

    def cycle_completion(self, delta: int = 1) -> int:
        if not self._completion.is_visible or not self._completion._items:
            return 0
        self._completion.cycle(delta)
        with _try_acquire_output_lock(name="bottom_bar.cycle_completion", timeout=0.3) as locked:
            if locked:
                self._redraw_cycle_only()
        return self._completion._idx

    def get_selected_completion(self) -> tuple[str, int, str]:
        return self._completion.get_selected()

    def get_selected_completion_index(self) -> int:
        """返回当前选中的补全项索引（0-based）。

        弹窗可见时返回实时 _idx；关闭时返回最后保存的索引，
        防止 Enter 处理中 _dismiss_completion() 重置 _idx 导致的竞态。
        """
        if self._completion.is_visible:
            return self._completion._idx
        return self._completion._last_idx_before_hide

    def _redraw_cycle_only(self) -> None:
        if not self._completion.is_visible or not self._completion._items:
            return
        out = sys.__stdout__
        out.write(cursor_save())
        height = self._term_height()
        total = self._bottom_lines
        popup_start = height - total + 3 + len(self._subagent_lines)
        tw = self._term_width()
        self._completion.render_cycle_update(out, popup_start, tw)
        r2 = height - total + 2 + len(self._subagent_lines)
        status = self._format_status()
        self._last_status = status
        if status:
            if self._animator.breath_frame > 0 and not _is_narrow():
                dot_color = self._animator.sine_color(45, 81, 12)
                dot_ansi = f"\033[38;5;{dot_color}m\u00b7{_COLOR_RESET}"
                out.write(f"{cursor_goto(r2, 1)}\033[K" + status + " " + dot_ansi)
            else:
                out.write(f"{cursor_goto(r2, 1)}\033[K" + status)
        else:
            out.write(f"{cursor_goto(r2, 1)}\033[K")
        out.write(cursor_restore())
        out.flush()
        self._last_height = height

    # ── 状态管理（_StatusMixin 内联） ───────────────

    def enable_status(self) -> None:
        self._status_active = True
        self._last_status = ""

    def disable_status(self) -> None:
        self._status_active = False

    def increment_tool(self) -> None:
        if self._tool_count == 0:
            self._tool_phase_start = time.monotonic()
        self._tool_count += 1
        self._tool_total += 1

    def decrement_tool(self) -> None:
        self._tool_count = max(0, self._tool_count - 1)

    def increment_tool_fail(self) -> None:
        self._tool_fail_count += 1

    def reset_tool_count(self) -> None:
        self._tool_count = 0
        self._tool_fail_count = 0
        self._tool_total = 0
        self._tool_phase_start = 0.0

    def set_model_name(self, name: str) -> None:
        self._model_name = name

    def get_status_elapsed(self) -> float:
        try:
            from src.tui._snapshot import _get_snapshot
            snap_func = _get_snapshot()
            if snap_func is None:
                return 0.0
            return snap_func().get("elapsed_seconds", 0.0)
        except Exception:
            return 0.0

    def _format_status(self) -> str:
        if self._model_name:
            if self._status_active:
                _bf = self._animator.breath_frame
                if _bf > 0:
                    _pulse_color = self._animator.sine_color(36, 45, 4)
                else:
                    _pulse_color = 45
                model_part = (
                    f"\033[38;5;{_pulse_color}m\u00b7\033[0m"
                    f" {_COLOR_ACCENT}{self._model_name}{_COLOR_RESET}"
                )
            else:
                model_part = (
                    f"{_COLOR_ACCENT}\u00b7{_COLOR_RESET}"
                    f" {_COLOR_ACCENT}{self._model_name}{_COLOR_RESET}"
                )
        else:
            model_part = ""
        if not self._status_active:
            return model_part
        try:
            from src.tui._snapshot import _get_snapshot
            snap_func = _get_snapshot()
            if snap_func is None:
                return model_part
            snap = snap_func()
        except Exception:
            return model_part
        total = snap.get("total_tokens", 0)
        elapsed = snap.get("elapsed_seconds", 0.0)
        per_second_speed = snap.get("per_second_speed", 0.0)
        if total <= 0 and elapsed <= 0 and per_second_speed <= 0 and self._tool_total <= 0:
            return model_part
        parts = []
        if self._tool_total > 0:
            if not _is_narrow():
                glow_gear = f"{_build_glow_ansi(self._animator.frame, 45, 12)}\u00b7\033[0m "
            else:
                glow_gear = ""
            if self._tool_count > 0:
                if self._tool_fail_count > 0:
                    total_colored = f"{_COLOR_TOOL_FAIL}{self._tool_total}{_COLOR_RESET}"
                else:
                    total_colored = f"{_COLOR_TOOL_OK}{self._tool_total}{_COLOR_RESET}"
                parts.append(
                    f"{glow_gear}"
                    f"{_COLOR_ACCENT}{self._tool_count}{_COLOR_RESET}"
                    f"{_COLOR_DIM}→{_COLOR_RESET}"
                    f"{total_colored}"
                )
            else:
                done = self._tool_total - self._tool_count - self._tool_fail_count
                if self._tool_fail_count > 0:
                    parts.append(
                        f"{glow_gear}"
                        f"{_COLOR_TOOL_OK}{done}{_COLOR_RESET}"
                        f"{_COLOR_DIM}/{_COLOR_RESET}"
                        f"{_COLOR_TOOL_FAIL}{self._tool_total}{_COLOR_RESET}"
                    )
                else:
                    parts.append(f"{glow_gear}{_COLOR_TOOL_OK}{self._tool_total}{_COLOR_RESET}")
        if elapsed > 0:
            if elapsed >= 60:
                mins = int(elapsed // 60)
                secs = int(elapsed % 60)
                dur = f"{mins}:{secs:02d}" if mins < 60 else f"{mins // 60}:{mins % 60:02d}:{secs:02d}"
            else:
                dur = f"{elapsed:.1f}s"
            parts.append(f"{_COLOR_TIME}{dur}{_COLOR_RESET}")
        if total > 0:
            tok_str = f"{total / 1000:.1f}k" if total >= 1000 else str(total)
            parts.append(f"{_COLOR_TOKEN}{tok_str}t{_COLOR_RESET}")
        if per_second_speed > 0:
            speed_str = f"{per_second_speed:.1f}" if per_second_speed >= 1 else f"{per_second_speed:.2f}"
            parts.append(f"{_COLOR_SPEED}{speed_str}t/s{_COLOR_RESET}")
        sep = f" {_COLOR_DIM}\u00b7{_COLOR_RESET} "
        status = sep.join(parts) if parts else ""
        if status and not _is_narrow():
            glow_dot = f"{_build_glow_ansi(self._animator.frame, 45, 12)}\u00b7\033[0m"
            status = f"{status}  {glow_dot}"
        if model_part and status:
            return f"{model_part}  {status}"
        return model_part or status
