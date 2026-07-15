"""终端输入读取器 — 将 Blessed 输入封装为框架事件。

提供统一的键盘和鼠标输入解析，将 Blessed 的 Keystroke 对象
和 ANSI 转义序列转化为框架标准事件类型（KeyPressEvent/MouseEvent）。

线程安全：使用 threading.Lock 保护 Blessed Terminal 访问。

使用方式：
    reader = InputReader()
    event = reader.read_key(timeout=0.1)
    if event:
        print(f"Pressed: {event.key}")

降级策略：
  - Blessed 不可用时，InputReader 静默降级（所有方法返回 None）
  - 终端不支持鼠标时，read_mouse() 返回 None
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional

from .event_types import KeyPressEvent, MouseEvent

_logger = logging.getLogger(__name__)

# ── Blessed 按键码 → 标准化名称映射 ──────────────────────

# 延迟导入 blessed 常量，避免模块加载时触发终端检测
_KEY_MAP: dict[int, str] = {}
_KEY_MAP_INITIALIZED = False


def _init_key_map() -> dict[int, str]:
    """惰性初始化按键码映射表。"""
    global _KEY_MAP, _KEY_MAP_INITIALIZED
    if _KEY_MAP_INITIALIZED:
        return _KEY_MAP

    try:
        import blessed.keyboard as _kb
        curses_codes = _kb.get_curses_keycodes()

        _KEY_MAP = {}
        # 导航键
        _KEY_MAP[curses_codes.get("KEY_UP", 259)] = "up"
        _KEY_MAP[curses_codes.get("KEY_DOWN", 258)] = "down"
        _KEY_MAP[curses_codes.get("KEY_LEFT", 260)] = "left"
        _KEY_MAP[curses_codes.get("KEY_RIGHT", 261)] = "right"
        # 功能键
        _KEY_MAP[curses_codes.get("KEY_ENTER", 343)] = "enter"
        _KEY_MAP[curses_codes.get("KEY_BACKSPACE", 263)] = "backspace"
        _KEY_MAP[curses_codes.get("KEY_TAB", 9)] = "tab"
        _KEY_MAP[curses_codes.get("KEY_DC", 330)] = "delete"    # Delete Character
        _KEY_MAP[curses_codes.get("KEY_IC", 331)] = "insert"    # Insert Character
        _KEY_MAP[curses_codes.get("KEY_HOME", 262)] = "home"
        _KEY_MAP[curses_codes.get("KEY_END", 360)] = "end"
        _KEY_MAP[curses_codes.get("KEY_NPAGE", 338)] = "page_down"
        _KEY_MAP[curses_codes.get("KEY_PPAGE", 339)] = "page_up"
        # END 在 blessed 中可能和 HOME 同码，补充
        # F1-F12
        for i in range(1, 13):
            code = curses_codes.get(f"KEY_F{i}")
            if code is not None:
                _KEY_MAP[code] = f"f{i}"

        # 添加 escape 处理（通常不作为 curses keycode 出现，但保留兼容）
        # ESC 码 = 27，但由 Blessed 作为普通字符处理，不进入 is_sequence 路径

    except ImportError:
        _KEY_MAP = {}

    _KEY_MAP_INITIALIZED = True
    return _KEY_MAP


# ── 鼠标解析 ────────────────────────────────────────────

# SGR 鼠标转义序列正则：\033[<Btn;X;Y{ M | m }
_SGR_MOUSE_RE = re.compile(r"\x1b\[<(\d+);(\d+);(\d+)([Mm])")

# 鼠标按钮位掩码（SGR 扩展模式）
_BTN_LEFT = 0
_BTN_MIDDLE = 1
_BTN_RIGHT = 2
_BTN_RELEASE = 3       # 按钮释放（仅低 2 位）
_BTN_SCROLL_UP = 64
_BTN_SCROLL_DOWN = 65
_BTN_MOTION = 32       # 拖动（加到按钮码上）

_MOD_SHIFT = 4
_MOD_ALT = 8
_MOD_CTRL = 16

_BUTTON_NAMES: dict[int, str] = {
    0: "left",
    1: "middle",
    2: "right",
}


def _parse_sgr_mouse(raw: str) -> MouseEvent | None:
    """解析 SGR 扩展模式鼠标转义序列。

    SGR 格式: ``\\033[<btn;col;row{M|m}``
    - ``M`` = 按下, ``m`` = 释放
    - btn 低 2 位: 0=左键, 1=中键, 2=右键, 3=释放
    - btn bit 6 (64): 滚轮
    - btn bit 5 (32): 拖动
    - btn bit 2 (4): Shift
    - btn bit 3 (8): Alt/Meta
    - btn bit 4 (16): Ctrl

    Returns:
        MouseEvent 或 None（解析失败时）。
    """
    match = _SGR_MOUSE_RE.match(raw)
    if not match:
        return None

    btn_raw = int(match.group(1))
    x = int(match.group(2))
    y = int(match.group(3))
    is_press = match.group(4) == "M"

    # 提取修饰键
    shift = bool(btn_raw & _MOD_SHIFT)
    alt = bool(btn_raw & _MOD_ALT)
    ctrl = bool(btn_raw & _MOD_CTRL)

    # 判断事件类型（优先级：滚轮 > 拖动 > 释放 > 点击）
    if btn_raw & _BTN_SCROLL_UP:
        # 滚轮事件：64=上滚, 65=下滚（最低位 0/1 区分方向）
        if (btn_raw & 1) == 0:
            button = "wheel_up"
            action = "scroll_up"
        else:
            button = "wheel_down"
            action = "scroll_down"
    elif btn_raw & _BTN_MOTION:
        # 拖动事件
        real_btn = btn_raw & 0b11
        button = _BUTTON_NAMES.get(real_btn, "left")
        action = "drag"
    elif (btn_raw & 0b11) == _BTN_RELEASE:
        # 释放事件（低 2 位 == 3）
        # 无法从释放事件中确定具体按钮，默认 left
        button = "left"
        action = "release"
    else:
        # 点击事件
        button = _BUTTON_NAMES.get(btn_raw & 0b11, "left")
        action = "click" if is_press else "release"

    return MouseEvent(
        x=x,
        y=y,
        button=button,
        action=action,
        ctrl=ctrl,
        alt=alt,
        shift=shift,
    )


# ── InputReader ──────────────────────────────────────────


class InputReader:
    """终端输入读取器。

    封装 Blessed Terminal.inkey() 调用，将原始按键/鼠标输入
    转换为框架标准事件类型。

    ## 设计要点

    - **依赖注入**：terminal 参数支持注入 Mock Terminal（测试时）。
      默认使用全局共享的 Blessed Terminal 实例。
    - **线程安全**：使用 threading.Lock 保护 Blessed 终端访问，
      避免并发读取导致的数据竞争。
    - **降级策略**：Blessed 不可用时自动降级，所有方法返回 None。

    ## 使用示例

    >>> reader = InputReader()
    >>> event = reader.read_key(timeout=0.1)
    >>> if event:
    ...     print(f"Key: {event.key}, Ctrl: {event.ctrl}")
    """

    # ── 鼠标启用/禁用 ANSI 序列 ─────────────────────────
    _ENABLE_SGR_MOUSE = "\033[?1000h\033[?1002h\033[?1006h"
    _DISABLE_SGR_MOUSE = "\033[?1006l\033[?1002l\033[?1000l"

    def __init__(self, terminal=None):
        """初始化输入读取器。

        Args:
            terminal: Blessed Terminal 实例（可选）。
                      传入 None 则使用全局共享实例。
                      测试时可注入 MockTerminal。
        """
        self._terminal = terminal  # None 表示惰性获取
        self._lock = threading.Lock()
        self._mouse_enabled = False
        self._blessed_available: bool | None = None

    # ── 内部 ─────────────────────────────────────────────

    def _get_terminal(self):
        """获取 Blessed Terminal 实例（惰性初始化）。"""
        if self._terminal is not None:
            return self._terminal
        try:
            from ..terminal.blessed import get_terminal
            self._terminal = get_terminal()
            return self._terminal
        except ImportError:
            return None

    def _check_blessed(self) -> bool:
        """检查 Blessed 是否可用（缓存结果）。"""
        if self._blessed_available is None:
            try:
                import blessed  # noqa: F401
                self._blessed_available = True
            except ImportError:
                self._blessed_available = False
                _logger.debug("Blessed 不可用，InputReader 降级")
        return self._blessed_available

    # ── 键盘输入 ─────────────────────────────────────────

    def read_key(self, timeout: float | None = None) -> KeyPressEvent | None:
        """读取单个按键事件。

        阻塞模式（timeout=None）：无限等待，直到用户按下按键。
        非阻塞模式（timeout>=0）：在 timeout 秒内等待，超时返回 None。

        Args:
            timeout: 超时时间（秒）。None=阻塞，>=0=非阻塞。

        Returns:
            KeyPressEvent 或 None（超时/不可用）。
        """
        if not self._check_blessed():
            return None

        term = self._get_terminal()
        if term is None:
            return None

        try:
            with self._lock:
                key = term.inkey(timeout=timeout)
        except Exception:
            _logger.debug("inkey() 异常", exc_info=True)
            return None

        if not key:
            return None

        return self._keystroke_to_event(key)

    def _keystroke_to_event(self, key) -> KeyPressEvent:
        """将 Blessed Keystroke 转换为 KeyPressEvent。

        Args:
            key: Blessed Keystroke 对象。

        Returns:
            KeyPressEvent 实例。
        """
        raw = str(key) if key else ""

        # 功能键（转义序列）
        if key.is_sequence:
            key_map = _init_key_map()
            key_name = key_map.get(key.code, "")
            if key_name:
                return KeyPressEvent(
                    key=key_name,
                    raw=raw,
                )
            # 未知序列键：保留原始字符
            return KeyPressEvent(
                key=raw,
                raw=raw,
            )

        # 可打印字符 / 控制字符
        key_str = str(key)
        if key_str == "\n" or key_str == "\r":
            return KeyPressEvent(key="enter", raw=raw)
        elif key_str == "\t":
            return KeyPressEvent(key="tab", raw=raw)
        elif key_str == "\x1b":
            return KeyPressEvent(key="escape", raw=raw)
        elif key_str == "\x7f" or key_str == "\x08":
            return KeyPressEvent(key="backspace", raw=raw)
        elif key_str == " ":
            return KeyPressEvent(key="space", raw=raw)

        # 普通字符
        return KeyPressEvent(key=key_str, raw=raw)

    # ── 鼠标输入 ─────────────────────────────────────────

    def enable_mouse(self) -> bool:
        """启用 SGR 扩展鼠标模式。

        向终端发送 \033[?1000h\033[?1002h\033[?1006h 序列，
        启用鼠标按下/释放/拖动/滚轮事件上报。

        Returns:
            True=启用成功，False=不支持或失败。
        """
        if not self._check_blessed():
            return False

        term = self._get_terminal()
        if term is None:
            return False

        try:
            with self._lock:
                # 通过 Blessed stream 写入（绕过应用级 stdout 包装）
                term.stream.write(self._ENABLE_SGR_MOUSE)
                term.stream.flush()
                self._mouse_enabled = True
                _logger.debug("SGR 鼠标模式已启用")
                return True
        except Exception:
            _logger.debug("启用鼠标模式失败", exc_info=True)
            return False

    def disable_mouse(self) -> bool:
        """禁用 SGR 扩展鼠标模式。

        Returns:
            True=禁用成功。
        """
        if not self._mouse_enabled:
            return True

        term = self._get_terminal()
        if term is None:
            return False

        try:
            with self._lock:
                term.stream.write(self._DISABLE_SGR_MOUSE)
                term.stream.flush()
                self._mouse_enabled = False
                _logger.debug("SGR 鼠标模式已禁用")
                return True
        except Exception:
            _logger.debug("禁用鼠标模式失败", exc_info=True)
            return False

    def read_mouse(self, raw_sequence: str) -> MouseEvent | None:
        """解析原始鼠标转义序列为 MouseEvent。

        调用方需要从 Blessed inkey() 流中识别以 ``\\033[<`` 开头的
        序列，然后调用此方法进行解析。

        Args:
            raw_sequence: 原始 ANSI 转义序列字符串。

        Returns:
            MouseEvent 或 None（序列不是有效的 SGR 鼠标事件）。
        """
        if not raw_sequence or not raw_sequence.startswith("\033[<"):
            return None
        return _parse_sgr_mouse(raw_sequence)

    def read_input(self, timeout: float | None = None) -> KeyPressEvent | MouseEvent | None:
        """读取输入（键盘或鼠标），自动区分类型。

        先尝试读取按键，如果按键序列以 ``\\033[<`` 开头，
        则尝试解析为鼠标事件。

        Args:
            timeout: 超时时间（秒）。None=阻塞，>=0=非阻塞。

        Returns:
            KeyPressEvent、MouseEvent 或 None。
        """
        if not self._check_blessed():
            return None

        term = self._get_terminal()
        if term is None:
            return None

        try:
            with self._lock:
                key = term.inkey(timeout=timeout)
        except Exception:
            _logger.debug("inkey() 异常", exc_info=True)
            return None

        if not key:
            return None

        raw = str(key) if key else ""

        # 鼠标序列检测
        if raw.startswith("\033[<"):
            mouse_event = _parse_sgr_mouse(raw)
            if mouse_event:
                return mouse_event

        return self._keystroke_to_event(key)

    # ── 属性 ─────────────────────────────────────────────

    @property
    def mouse_enabled(self) -> bool:
        """是否已启用鼠标模式。"""
        return self._mouse_enabled

    @property
    def blessed_available(self) -> bool:
        """Blessed 是否可用。"""
        return self._check_blessed()


__all__ = [
    "InputReader",
    "_parse_sgr_mouse",
]
