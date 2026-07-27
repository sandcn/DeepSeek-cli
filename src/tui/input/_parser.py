"""InputParser — 统一 ANSI CSI/SS3 序列解析。

从 EscapeMonitor._handle_escape() 和 _run_selection_raw() 中
提取的 CSI 参数解析 + 终结符判断逻辑。

零 I/O 设计（feed_byte），仅 parse_escape_sequence 含 os.read + select。
"""

from __future__ import annotations

import os
import sys
import select
import logging
from src._compat import dataclass

_logger = logging.getLogger(__name__)


@dataclass(slots=True)
class KeyEvent:
    """按键事件数据类。

    字段:
        kind: 按键类型标识字符串
        char: 可打印字符值（kind="char" 时有效）
        modifier: 修饰键位掩码（CSI u 模式使用，1=无修饰, 2=Shift, 3=Alt, 5=Ctrl）
        keycode: CSI u 键码（如 13=Enter）
        raw: 原始字节序列（调试用）
    """
    kind: str        # "char" | "enter" | "tab" | "backspace" | "escape" |
                     # "arrow_up" | "arrow_down" | "arrow_left" | "arrow_right" |
                     # "home" | "end" | "delete" | "ctrl_key" | "interrupt" | "csi_u" | "unknown"
    char: str = ""
    modifier: int = 0
    keycode: int = 0
    raw: bytes = b""


class InputParser:
    """ANSI CSI/SS3 序列解析器（零 I/O 状态机）。

    职责：将原始字节序列解析为 KeyEvent。
    - feed_byte(): 单字节推入状态机，简单字节立即返回 KeyEvent，
      ESC (0x1b) 返回 None 表示需走 parse_escape_sequence() 读取完整序列。
    - parse_escape_sequence(): 含 I/O 的转义序列解析（os.read + select）。

    不包含「分发后做什么」的逻辑，只做解析。
    """

    # CSI 参数读取超时（秒）
    _CSI_READ_TIMEOUT = 0.01

    # SS3 读取超时（秒）
    _SS3_READ_TIMEOUT = 0.01

    def feed_byte(self, byte: int) -> KeyEvent | None:
        """单字节推入状态机。

        对于非 ESC 的字节，立即返回对应的 KeyEvent。
        对于 ESC (0x1b)，返回 None，调用方应转而调用 parse_escape_sequence(fd)。

        Args:
            byte: 单字节整数值 (0-255)。

        Returns:
            KeyEvent — 完整按键事件；None — 需要 parse_escape_sequence 读取完整序列。
        """
        # ── ESC 序列入口 ──
        if byte == 0x1b:
            return None

        # ── ASCII 控制字符分发 ──
        if byte <= 0x1f or byte == 0x7f:
            return self._decode_control_char(byte)

        # ── ASCII 可打印 / 高位字节（UTF-8 多字节由调用方处理） ──
        try:
            ch = bytes([byte]).decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            ch = chr(byte)
        return KeyEvent(kind="char", char=ch, raw=bytes([byte]))

    def parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O）。

        在首字节已确认为 0x1b 后调用。使用 os.read + select 逐字节读取
        并解析 CSI/SS3/Alt+Backspace/双 Esc 等序列。

        Args:
            fd: stdin 文件描述符（sys.stdin.fileno()）。

        Returns:
            解析后的 KeyEvent。超时/不完整序列返回 escape KeyEvent。
        """
        # 读取 ESC 后的下一个字节
        try:
            has_more, _, _ = select.select([fd], [], [], 0.05)
        except (ValueError, OSError, TypeError, AttributeError):
            return KeyEvent(kind="escape", raw=b"\x1b")

        if not has_more:
            return KeyEvent(kind="escape", raw=b"\x1b")

        try:
            raw2 = os.read(fd, 1)
            if not raw2:
                return KeyEvent(kind="escape", raw=b"\x1b")
            next_byte = raw2[0]
        except (ValueError, OSError, TypeError):
            return KeyEvent(kind="escape", raw=b"\x1b")

        # ── CSI 序列：ESC [ ──
        if next_byte == ord('['):
            return self._read_csi_sequence(fd)

        # ── SS3 序列：ESC O ──
        if next_byte == ord('O'):
            return self._read_ss3_sequence(fd)

        # ── Alt+Backspace：ESC DEL ──
        if next_byte == 0x7f:
            # 消耗可能跟随的额外字节
            try:
                if select.select([fd], [], [], 0.01)[0]:
                    os.read(fd, 1)
            except (ValueError, OSError, TypeError):
                pass
            return KeyEvent(kind="backspace", modifier=1, raw=b"\x1b\x7f")

        # ── 双 Esc ──
        if next_byte == 0x1b:
            return KeyEvent(kind="interrupt", raw=b"\x1b\x1b")

        # ── 其他 ESC 组合 → 视为中断 ──
        return KeyEvent(kind="interrupt", raw=b"\x1b" + bytes([next_byte]))

    # ── 内部方法 ──────────────────────────────────────────

    @staticmethod
    def _decode_control_char(byte: int) -> KeyEvent:
        """将 ASCII 控制字符 (0x00-0x1F / 0x7F) 解码为 KeyEvent。

        调用方负责后续分发（如 Ctrl+C → 中断、Tab → 补全）。
        """
        raw = bytes([byte])
        if byte in (0x0d, 0x0a):        # \r / \n
            return KeyEvent(kind="enter", raw=raw)
        if byte == 0x09:                 # \t
            return KeyEvent(kind="tab", raw=raw)
        if byte in (0x7f, 0x08):        # DEL / BS
            return KeyEvent(kind="backspace", raw=raw)
        if byte == 0x03:                 # Ctrl+C
            return KeyEvent(kind="interrupt", raw=raw)
        if byte == 0x01:                 # Ctrl+A → Home
            return KeyEvent(kind="home", raw=raw)
        if byte == 0x05:                 # Ctrl+E → End
            return KeyEvent(kind="end", raw=raw)
        if byte == 0x17:                 # Ctrl+W → delete word left
            return KeyEvent(kind="delete", modifier=1, raw=raw)
        if byte == 0x15:                 # Ctrl+U → kill to BOL
            return KeyEvent(kind="delete", modifier=2, raw=raw)
        if byte == 0x0b:                 # Ctrl+K → kill to EOL
            return KeyEvent(kind="delete", modifier=3, raw=raw)
        if byte in (0x07, 0x0f, 0x0e, 0x12):  # Ctrl+G/O/N/R → 特殊按键
            return KeyEvent(kind="ctrl_key", char=chr(byte), raw=raw)
        # 其他控制字符 → unknown
        return KeyEvent(kind="unknown", raw=raw)

    def _read_csi_sequence(self, fd: int) -> KeyEvent:
        """读取 CSI 序列参数 + 终结符并解析为 KeyEvent。

        支持：
          - 简单 CSI: \\x1b[A (上箭头), \\x1b[H (Home), \\x1b[F (End)
          - 功能键:  \\x1b[1~ (Home), \\x1b[4~ (End)
          - 修饰符:  \\x1b[1;5D (Ctrl+左), \\x1b[1;5C (Ctrl+右)
          - CSI u:   \\x1b[13;2u (Shift+Enter), \\x1b[13;3u (Alt+Enter)

        Args:
            fd: stdin 文件描述符。

        Returns:
            解析后的 KeyEvent。不完整序列返回 KeyEvent(kind="unknown")。
        """
        params: list[int] = []
        current = ""
        terminator: str | None = None

        try:
            while select.select([fd], [], [], self._CSI_READ_TIMEOUT)[0]:
                raw_c = os.read(fd, 1)
                if not raw_c:
                    break
                c = raw_c.decode("utf-8", errors="replace")
                if c == ';':
                    try:
                        params.append(int(current) if current else 0)
                    except ValueError:
                        params.append(0)
                    current = ""
                elif c.isdigit():
                    current += c
                elif c.isalpha() or c == '~':
                    if current:
                        try:
                            params.append(int(current))
                        except ValueError:
                            params.append(0)
                    terminator = c
                    break
        except (ValueError, OSError, TypeError):
            pass

        if terminator is None:
            return KeyEvent(kind="unknown", raw=b"\x1b[")

        # ── 分发 CSI 序列 ──
        return self._dispatch_csi(params, terminator)

    def _read_ss3_sequence(self, fd: int) -> KeyEvent:
        """读取 SS3 序列（ESC O + 字符，通常为 F1-F4）。

        消耗后续字节后返回 unknown（F1-F4 当前不处理）。
        """
        try:
            if select.select([fd], [], [], self._SS3_READ_TIMEOUT)[0]:
                raw_c = os.read(fd, 1)
                if raw_c:
                    return KeyEvent(kind="unknown", raw=b"\x1bO" + raw_c)
        except (ValueError, OSError, TypeError):
            pass
        return KeyEvent(kind="unknown", raw=b"\x1bO")

    @staticmethod
    def _dispatch_csi(params: list[int], terminator: str) -> KeyEvent:
        """根据 CSI 参数和终结符分发到对应的 KeyEvent。

        Args:
            params: CSI 参数列表（如 [1, 5]）。
            terminator: CSI 终结符（字母或 ~）。

        Returns:
            对应的 KeyEvent。
        """
        # ── CSI u 模式: \\x1b[<keycode>;<modifier>u ──
        if terminator == 'u':
            keycode = params[0] if len(params) >= 1 else 0
            modifier = params[1] if len(params) >= 2 else 1
            raw = b"\x1b[" + _params_to_bytes(params) + b"u"
            if keycode == 13 and modifier in (2, 3, 5):
                # Shift+Enter(2) / Alt+Enter(3) / Ctrl+Enter(5) → char '\n'
                return KeyEvent(kind="char", char="\n", modifier=modifier,
                                keycode=keycode, raw=raw)
            return KeyEvent(kind="csi_u", modifier=modifier, keycode=keycode, raw=raw)

        raw = b"\x1b[" + _params_to_bytes(params) + terminator.encode()

        # ── 功能键序列: \\x1b[N~ ──
        if terminator == '~':
            p = params[0] if params else 0
            if p in (1, 7):
                return KeyEvent(kind="home", raw=raw)
            if p == 3:
                return KeyEvent(kind="delete", raw=raw)
            if p in (4, 8):
                return KeyEvent(kind="end", raw=raw)
            return KeyEvent(kind="unknown", raw=raw)

        # ── Home (\\x1b[H) ──
        if terminator == 'H':
            return KeyEvent(kind="home", raw=raw)

        # ── End (\\x1b[F) ──
        if terminator == 'F':
            return KeyEvent(kind="end", raw=raw)

        # ── 右箭头 / Ctrl+右 ──
        if terminator == 'C':
            if len(params) >= 2 and params[1] == 5:
                return KeyEvent(kind="arrow_right", modifier=5, raw=raw)
            return KeyEvent(kind="arrow_right", raw=raw)

        # ── 左箭头 / Ctrl+左 ──
        if terminator == 'D':
            if len(params) >= 2 and params[1] == 5:
                return KeyEvent(kind="arrow_left", modifier=5, raw=raw)
            return KeyEvent(kind="arrow_left", raw=raw)

        # ── 上箭头 ──
        if terminator == 'A':
            return KeyEvent(kind="arrow_up", raw=raw)

        # ── 下箭头 ──
        if terminator == 'B':
            return KeyEvent(kind="arrow_down", raw=raw)

        # ── 其他 CSI 序列 ──
        return KeyEvent(kind="unknown", raw=raw)


def _params_to_bytes(params: list[int]) -> bytes:
    """将参数列表转为 CSI 参数字节串。"""
    if not params:
        return b""
    return ";".join(str(p) for p in params).encode()
