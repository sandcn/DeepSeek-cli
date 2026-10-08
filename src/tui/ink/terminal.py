"""terminal — 终端能力控制（备用屏/括号粘贴/鼠标/标题/光标形状/超链接 + raw 模式）。

React Ink 官方 API 对齐与框架扩展的**单一真源**：所有终端能力协商序列在此
集中定义（禁止散落魔法字符串），供 ``render()`` 选项、``InkSession`` 生命周期
与组件 hooks（useStdin/usePaste/useMouseInput）共享。

职责：
  - ``MODES`` / ``TerminalModeManager`` — 可开关终端模式的序列对 + 幂等状态
    管理（启用写入、退出还原；重复调用无副作用）。
  - ``RawModeController`` — 终端 raw/cbreak 模式切换（termios 不可用时降级为
    不支持，不抛异常）。
  - ``hyperlink()`` — OSC 8 超链接包裹（终端可点击 URL）。
  - ``linkify_urls()`` — 从纯文本中识别 URL 并转 StyledRun 列表（含链接）。
  - ``window_title_sequence()`` / ``cursor_shape_sequence()`` — OSC 0 / DECSCUSR。
  - ``strip_sequences()`` — 尽力剥离控制序列（供不支持能力的终端降级）。

依赖方向：本模块 → 标准库 + ``output``（StyledRun）；不反向依赖任何 ink 模块
（``__init__`` re-export 保持在门面层）。
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Callable, Iterable

from .output import StyledRun, hyperlink as _hyperlink

_logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════
# 终端模式序列（唯一真源）
# ═══════════════════════════════════════════════════════════

#: 备用屏缓冲（DECSET/DECRST ?1049）——进入时清屏并保存原屏幕内容，退出还原。
_ALT_SCREEN_ON = "\x1b[?1049h\x1b[H"
_ALT_SCREEN_OFF = "\x1b[?1049l"

#: 括号粘贴模式（bracketed paste，DECSET ?2004）——粘贴内容以
#: ``ESC[200~ ... ESC[201~`` 包裹整段到达，不会被拆成逐字符按键。
_BRACKETED_PASTE_ON = "\x1b[?2004h"
_BRACKETED_PASTE_OFF = "\x1b[?2004l"

#: 鼠标上报（SGR 扩展坐标 + 1000 基本上报 + 1002 拖拽上报）。
_MOUSE_ON = "\x1b[?1000h\x1b[?1002h\x1b[?1006h"
_MOUSE_OFF = "\x1b[?1006l\x1b[?1002l\x1b[?1000l"

#: 光标形状（DECSCUSR）——值遵循 xterm 约定。
CURSOR_SHAPES = {
    "default": 0,
    "block": 1,
    "block_blink": 2,
    "bar": 5,
    "bar_blink": 6,
    "underline": 3,
    "underline_blink": 4,
}

#: 可开关终端模式表：名称 → (启用序列, 禁用序列)。新增能力只需加条目。
MODES: dict[str, tuple[str, str]] = {
    "alternateScreen": (_ALT_SCREEN_ON, _ALT_SCREEN_OFF),
    "bracketedPaste": (_BRACKETED_PASTE_ON, _BRACKETED_PASTE_OFF),
    "mouse": (_MOUSE_ON, _MOUSE_OFF),
}


def window_title_sequence(title: str) -> str:
    """OSC 0 窗口/图标标题序列（控制字符剥离，防序列注入）。"""
    safe = re.sub(r"[\x00-\x1f\x7f]", "", str(title))
    return f"\x1b]0;{safe}\x07"


def cursor_shape_sequence(shape: Any) -> str:
    """DECSCUSR 光标形状序列（未知形状回退 ``default``）。"""
    if isinstance(shape, str):
        value = CURSOR_SHAPES.get(shape, CURSOR_SHAPES["default"])
    else:
        try:
            value = int(shape)
        except (TypeError, ValueError, OverflowError):
            value = 0
        if value not in set(CURSOR_SHAPES.values()):
            value = 0
    return f"\x1b[{value} q"


# ═══════════════════════════════════════════════════════════
# OSC 8 超链接
# ═══════════════════════════════════════════════════════════

#: URL 识别（终端超链接自动转换用）：http/https/ftp + www. 前缀裸域名。
_URL_RE = re.compile(
    r"(?:https?|ftp)://[^\s<>\x00-\x1f\x7f]+"
    r"|www\.[^\s<>\x00-\x1f\x7f]+",
    re.IGNORECASE,
)

#: URL 尾部常被误吞的标点（句末句号/逗号/右括号等）——识别时回退。
_TRAILING_PUNCT = ".,;:!?)]}'\"»"


def _normalize_url(url: str) -> str:
    """补全裸域名 URL 的协议前缀（``www.x.com`` → ``http://www.x.com``）。"""
    if url.lower().startswith("www."):
        return "http://" + url
    return url


def hyperlink(url: str, text: str) -> str:
    """把 ``text`` 包裹为指向 ``url`` 的 OSC 8 超链接（终端可点击）。

    ★ 实现位于输出模型 ``output.hyperlink``（``StyledRun.render`` 直接调用，
    避免 ``output → terminal`` 反向依赖）；此处 re-export 供本模块门面使用。
    """
    return _hyperlink(url, text)


def _split_trailing_punct(url: str) -> tuple[str, str]:
    """把 URL 尾部的标点拆出（返回 ``(url, trailing)``）。"""
    cut = len(url)
    while cut > 0 and url[cut - 1] in _TRAILING_PUNCT:
        cut -= 1
    return url[:cut], url[cut:]


def linkify_runs(text: str, style: Any = None, link_style: Any = None) -> list[StyledRun]:
    """把纯文本按 URL 切分为 StyledRun 列表（URL 片段携带 link 字段）。

    Args:
        text: 纯文本（不含 ANSI 序列）。
        style: 非 URL 片段的样式（None 表示无样式）。
        link_style: URL 片段的样式（None 时继承 ``style``）。

    Returns:
        StyledRun 列表（拼接后与原文等值；URL 片段 ``link`` 为对应 URL）。
    """
    out: list[StyledRun] = []
    cursor = 0
    for match in _URL_RE.finditer(text):
        start, end = match.span()
        url, trailing = _split_trailing_punct(match.group(0))
        if not url:
            continue
        if start > cursor:
            out.append(StyledRun(text[cursor:start], style))
        out.append(
            StyledRun(url, link_style if link_style is not None else style, _normalize_url(url))
        )
        if trailing:
            out.append(StyledRun(trailing, style))
        cursor = end
    if cursor < len(text):
        out.append(StyledRun(text[cursor:], style))
    return out


def attach_links(runs: list[StyledRun], link_style: Any = None) -> list[StyledRun]:
    """为 runs 中的 URL 子串附加 OSC 8 超链接（无 URL 时原样返回同引用）。

    快速守卫：任一 run 文本不含 ``http``/``ftp``/``www.`` 时**直接返回原列表
    引用**（渲染热路径零开销、零对象分配，身份缓存/行级 diff 短路不受影响）。
    含 URL 的 run 被重切为「前缀 + 带 link 的 URL + 后缀」若干 run（样式不变，
    仅 URL 片段携带 ``link``）。

    Args:
        runs: 原始 StyledRun 列表。
        link_style: URL 片段样式（None=沿用该 run 自身样式）。

    Returns:
        StyledRun 列表（无 URL 时与入参同一对象）。
    """
    if not runs:
        return runs
    hit = False
    for run in runs:
        text = run.text
        if ("http" in text or "ftp" in text or "www." in text) and run.link is None:
            hit = True
            break
    if not hit:
        return runs
    out: list[StyledRun] = []
    for run in runs:
        text = run.text
        if run.link is None and ("http" in text or "ftp" in text or "www." in text):
            out.extend(
                linkify_runs(text, run.style, link_style if link_style is not None else run.style)
            )
        else:
            out.append(run)
    return out


def linkify_text(text: str) -> str:
    """把纯文本中的 URL 直接替换为 OSC 8 超链接（返回 ANSI 字符串）。

    裸域名（``www.``）补 ``http://`` 协议前缀；URL 尾部标点保留在链接之外。
    """
    def _sub(match) -> str:
        url, trailing = _split_trailing_punct(match.group(0))
        return hyperlink(_normalize_url(url), url) + trailing

    return _URL_RE.sub(_sub, text)


# ═══════════════════════════════════════════════════════════
# 终端模式管理
# ═══════════════════════════════════════════════════════════


class TerminalModeManager:
    """终端模式开关管理（启用写入 + 幂等还原）。

    线程安全性：所有公开方法在内部锁内写流（渲染线程与外部清理线程可能
    并发调用 disable_all）。
    """

    def __init__(self, stream: Any = None, is_tty: Callable[[], bool] | None = None) -> None:
        self._stream = stream
        self._is_tty_fn = is_tty
        self._enabled: set[str] = set()
        import threading

        self._lock = threading.Lock()

    def set_stream(self, stream: Any) -> None:
        self._stream = stream

    def _writable(self) -> bool:
        stream = self._stream
        if stream is None:
            return False
        if self._is_tty_fn is not None:
            try:
                return bool(self._is_tty_fn())
            except Exception:
                _logger.debug("终端能力判定异常", exc_info=True)
                return False
        try:
            return bool(stream.isatty())
        except (AttributeError, ValueError, OSError):
            # 无 isatty（StringIO 等）——仍允许写序列（测试/重定向场景），
            # 与官方 ink 一致：非 TTY 下序列无害。
            return True

    def _write(self, data: str) -> None:
        stream = self._stream
        if stream is None:
            return
        try:
            stream.write(data)
            flush = getattr(stream, "flush", None)
            if flush is not None:
                flush()
        except (OSError, ValueError, AttributeError):
            _logger.debug("终端模式序列写入失败", exc_info=True)

    def enable(self, mode: str) -> bool:
        """启用模式（未知模式/已启用/非 TTY 返回 False，不重复写序列）。"""
        pair = MODES.get(mode)
        if pair is None:
            return False
        with self._lock:
            if mode in self._enabled:
                return False
            if not self._writable():
                return False
            self._write(pair[0])
            self._enabled.add(mode)
            return True

    def disable(self, mode: str) -> bool:
        """禁用模式（未启用时无操作，幂等）。"""
        pair = MODES.get(mode)
        if pair is None:
            return False
        with self._lock:
            if mode not in self._enabled:
                return False
            self._write(pair[1])
            self._enabled.discard(mode)
            return True

    def is_enabled(self, mode: str) -> bool:
        with self._lock:
            return mode in self._enabled

    def disable_all(self) -> None:
        """还原全部已启用模式（退出/清理路径调用，幂等）。"""
        for mode in list(MODES):
            self.disable(mode)

    def write_raw(self, data: str) -> None:
        """直接写入任意控制序列（窗口标题/光标形状等一次性能力）。"""
        with self._lock:
            if not self._writable():
                return
            self._write(data)


# ═══════════════════════════════════════════════════════════
# raw 模式
# ═══════════════════════════════════════════════════════════


class RawModeController:
    """stdin raw/cbreak 模式控制器（termios 不可用时不支持，安全降级）。

    ``enable()`` 保存进入前属性，``disable()`` 还原；重复调用幂等。
    与 ``tty.setcbreak`` 语义一致（关 ICANON/ECHO，保留信号处理）。
    """

    def __init__(self, fd: int | None = None) -> None:
        self._fd = fd
        self._saved: Any = None

    @property
    def supported(self) -> bool:
        """当前平台/fd 是否支持 raw 模式切换。"""
        if self._fd is None:
            return False
        try:
            from src._compat_termios import HAS_TERMIOS, termios

            if not HAS_TERMIOS:
                return False
            return bool(os.isatty(self._fd))
        except Exception:
            _logger.debug("raw 模式支持判定异常", exc_info=True)
            return False

    def enable(self) -> bool:
        """进入 raw/cbreak 模式（成功记录原属性，返回是否生效）。"""
        if self._saved is not None:
            return True
        if not self.supported:
            return False
        try:
            from src._compat_termios import termios, termios_lock, tty

            fd = int(self._fd)
            # ★ termios 读-改-写序列互斥（见 src/_compat_termios 模块
            #   docstring）：与 EscapeMonitor 的 cbreak 设置 / 首帧 CPR 光标行
            #   查询 / kitty 能力查询串行化，避免交错恢复用陈旧快照覆盖。
            with termios_lock():
                saved = termios.tcgetattr(fd)
                tty.setcbreak(fd)
        except Exception:
            _logger.debug("进入 raw 模式失败", exc_info=True)
            return False
        self._saved = saved
        return True

    def disable(self) -> bool:
        """还原 raw 模式（未进入时无操作）。"""
        if self._saved is None:
            return False
        try:
            from src._compat_termios import termios, termios_lock

            # ★ 同 enable：恢复序列持 TERMIOS_LOCK（防交错覆盖）。
            with termios_lock():
                termios.tcsetattr(int(self._fd), termios.TCSADRAIN, self._saved)
        except Exception:
            _logger.debug("还原 raw 模式失败", exc_info=True)
        finally:
            self._saved = None
        return True

    @property
    def active(self) -> bool:
        return self._saved is not None


# ═══════════════════════════════════════════════════════════
# 控制序列剥离（不支持能力时的降级渲染）
# ═══════════════════════════════════════════════════════════

#: OSC 序列（含 OSC 8 超链接）/ CSI 序列 / 双字符转义。
_OSC_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_CSI_RE = re.compile(r"\x1b\[[\x20-\x3F]*[@-~]")
_ESC_RE = re.compile(r"\x1b[@-Z\\-_]")


def strip_sequences(text: str) -> str:
    """剥离控制序列（OSC/CSI/ESC）返回纯文本。"""
    if "\x1b" not in text:
        return text
    return _ESC_RE.sub("", _CSI_RE.sub("", _OSC_RE.sub("", text)))


def capability_report(modes: Iterable[str]) -> dict[str, bool]:
    """给定模式名列表，返回「是否被本模块识别」的报告（诊断用）。"""
    return {name: name in MODES for name in modes}


__all__ = [
    "MODES",
    "CURSOR_SHAPES",
    "TerminalModeManager",
    "RawModeController",
    "hyperlink",
    "linkify_runs",
    "linkify_text",
    "attach_links",
    "window_title_sequence",
    "cursor_shape_sequence",
    "strip_sequences",
    "capability_report",
]
