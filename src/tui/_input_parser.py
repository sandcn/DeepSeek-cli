"""InputParser — TUI 输入 ANSI 解析逻辑（提取自 _input.py，方向⑤）。

将 Input 上帝类中的解析算法族提取为独立策略对象，Input 组合持有：
  - feed_byte: 单字节推入解析状态机
  - _decode_control_char: ASCII 控制字符解码（静态）
  - _parse_escape_sequence / _read_csi_sequence / _read_ss3_sequence: ESC 序列读取（I/O）
  - _dispatch_csi / _params_to_bytes: CSI 参数分发（静态）
  - parse_sequence: ESC 序列解析入口（I/O）

KeyEvent 数据类随解析逻辑搬移至本模块（Input 层 re-export，公开 API 不变）。

★ 批量读取优化（2026-08-14）：构造注入 InputIO 后，ESC/SS3 序列的后续字节
  经 ``InputIO.read_with_timeout`` 读取——优先消费 ``_pending``（批量读取
  剩余字节已在内存在，零等待、零 select 超时）；io 未注入时回退旧
  select+os.read 逻辑（独立可用，兼容直接构造场景）。

设计模式:
  策略（Strategy）— 解析算法族从 Input 提取为独立策略对象，Input 组合持有。

依赖方向:
  _input.py → _input_parser.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` 供回退路径使用；可被 ``patch("select.select", ...)``
全局拦截（与 _input.py 原行为等价）。
"""

from __future__ import annotations

import os
import re
import select

from src._compat import dataclass

__all__ = ["InputParser", "KeyEvent"]

# ── 常量 ──────────────────────────────────────────────────

_CSI_READ_TIMEOUT = 0.01     # CSI 参数读取超时（秒）
_SS3_READ_TIMEOUT = 0.01     # SS3 读取超时（秒）
_UTF8_READ_TIMEOUT = 0.05    # UTF-8 多字节序列读取超时（秒）
# P2（2026-08-07）：ESC 后续字节等待超时 0.05 → 0.01s——``_parse_escape_sequence``
# 在 render 线程同步执行 select.select 等待 ESC 后续字节，每次按 Esc 渲染帧
# 冻结最长 50ms；降至 0.01s（与 _CSI_READ_TIMEOUT/_SS3_READ_TIMEOUT 一致），
# 不改变解析逻辑（0.01s 内 ESC 后续字节正常到达；超时仍按纯 Esc 处理）。
_ESC_FOLLOWUP_TIMEOUT = 0.01
_ALT_BACKSPACE_DRAIN_TIMEOUT = 0.01  # Alt+Backspace 后续字节排空检测超时（秒）
# ★ P1-1（review 2026-08-06）：CSI 序列最大字节数上限——正常 CSI 序列
#   （方向键/Home/End/CSI u）参数极短（<16 字节）；异常/恶意输入流（fd
#   持续可读且无终止符的数字流）若无限读取将阻塞 render 线程（DoS）。
#   达上限视为解析失败（unknown，raw 保留已读部分）。
_CSI_MAX_BYTES = 64


# ═══════════════════════════════════════════════════════════
# KeyEvent — 按键事件数据类
# ═══════════════════════════════════════════════════════════

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
                     # "home" | "end" | "delete" | "ctrl_key" | "interrupt" | "csi_u" | "unknown" |
                     # "alt_char" | "f1".."f12"（方向A 步骤1 新增 f1-f4；
                     # 2026-10 扩展 f5-f12——F12 会话日志视图开关）
    char: str = ""
    modifier: int = 0
    keycode: int = 0
    raw: bytes = b""
    #: kitty 键盘协议修饰位（``modifier`` 字段减 1；-1 表示非 kitty 来源）。
    #: kittyModifiers 位定义：shift=1/alt=2/ctrl=4/super=8/hyper=16/meta=32/
    #: capsLock=64/numLock=128。
    kitty_bits: int = -1
    #: kitty 事件类型（``press``/``repeat``/``release``；空串表示未知/非 kitty）。
    event_type: str = ""
    #: 鼠标事件（kind="mouse"，SGR 扩展坐标模式）：
    #:   ``mouse_button``：left/middle/right/none（none=释放/移动无按键）
    #:   ``mouse_action``：press/release/move/wheel
    #:   ``mouse_wheel``：0 无 / -1 上滚 / +1 下滚
    #:   ``mouse_x``/``mouse_y``：1-based 终端坐标（与 xterm 一致）
    #:   ``mouse_modifiers``：位掩码 4=Shift / 8=Alt / 16=Ctrl
    mouse_button: str = ""
    mouse_action: str = ""
    mouse_wheel: int = 0
    mouse_x: int = 0
    mouse_y: int = 0
    mouse_modifiers: int = 0


# ═══════════════════════════════════════════════════════════
# kitty 键盘协议辅助（修饰位 / 事件类型）
# ═══════════════════════════════════════════════════════════

#: kitty 键盘协议 —— 修饰键位定义（与 ``src.tui.ink.kitty.kittyModifiers``
#: 同源语义；此处独立定义避免 _input_parser → tui.ink 的依赖倒挂）。
_KITTY_MODIFIER_BITS = {
    "shift": 1,
    "alt": 2,
    "ctrl": 4,
    "super": 8,
    "hyper": 16,
    "meta": 32,
    "capsLock": 64,
    "numLock": 128,
}

#: kitty 事件类型码 → 名称兜底快照（reportEventTypes 标志下 CSI-u 的
#: ``:<event>`` 子参数）；「一切皆插件」：数据来自表现层数据注册表
#: （``kitty_protocol`` 表），可按 Patch/Overlay 覆盖或禁用。
_KITTY_EVENT_TYPES = {1: "press", 2: "repeat", 3: "release"}

#: 括号粘贴（bracketed paste）起止标记：``ESC[200~`` … ``ESC[201~``。
_PASTE_START_MARK = b"\x1b[200~"
_PASTE_END_MARK = b"\x1b[201~"
#: 粘贴内容读取超时（秒）——终端在同一次写入中送出整段粘贴，短窗口足够。
_PASTE_READ_TIMEOUT = 0.05
#: 单次粘贴最大字节数（4 MiB）——防无界缓冲/畸形输入流。
_PASTE_MAX_BYTES = 4 * 1024 * 1024

#: SGR 鼠标序列（``ESC[<b;x;yM`` 按下/移动、``ESC[<b;x;ym`` 释放）。
_SGR_MOUSE_RE = re.compile(rb"\x1b\[<(\d+);(\d+);(\d+)([Mm])")
#: SGR 鼠标按键码 → 名称（低 2 位）。
_MOUSE_BUTTONS = {0: "left", 1: "middle", 2: "right", 3: "none"}
#: SGR 鼠标修饰位（xterm 约定）。
_MOUSE_MOD_SHIFT = 4
_MOUSE_MOD_ALT = 8
_MOUSE_MOD_CTRL = 16
_MOUSE_WHEEL_BIT = 64
_MOUSE_MOTION_BIT = 32


def decode_sgr_mouse(raw: bytes) -> KeyEvent | None:
    """解析 SGR 鼠标序列为 ``kind="mouse"`` KeyEvent（不匹配返回 None）。

    按键码 ``b`` 编码（xterm SGR 1006）：
      - 低 2 位：0=左键 / 1=中键 / 2=右键 / 3=释放（1000 模式）
      - ``+64``：滚轮（64=上滚、65=下滚）
      - ``+32``：移动（拖拽/悬停，配合 1002/1003 上报）
      - ``+4/+8/+16``：Shift/Alt/Ctrl 修饰
    终结符 ``M``=按下/移动、``m``=释放。
    """
    match = _SGR_MOUSE_RE.search(raw)
    if match is None:
        return None
    try:
        code = int(match.group(1))
        x = int(match.group(2))
        y = int(match.group(3))
    except (TypeError, ValueError):
        return None
    final = match.group(4)
    base = code & 0x03
    wheel = 0
    if code & _MOUSE_WHEEL_BIT:
        action = "wheel"
        wheel = -1 if base == 0 else 1
        button = "none"
    elif code & _MOUSE_MOTION_BIT:
        action = "move"
        button = _MOUSE_BUTTONS.get(base, "none")
    elif final == b"m":
        action = "release"
        button = _MOUSE_BUTTONS.get(base, "none")
    else:
        action = "press"
        button = _MOUSE_BUTTONS.get(base, "none")
    return KeyEvent(
        kind="mouse",
        mouse_button=button,
        mouse_action=action,
        mouse_wheel=wheel,
        mouse_x=x,
        mouse_y=y,
        mouse_modifiers=code & (_MOUSE_MOD_SHIFT | _MOUSE_MOD_ALT | _MOUSE_MOD_CTRL),
        raw=bytes(raw),
    )


def _kitty_modifier_bits() -> dict:
    """kitty 修饰键位定义（数据注册表优先，缺席时回退兜底快照）。"""
    from src.presentation_data import kitty_protocol

    value = (kitty_protocol() or {}).get("modifier_bits")
    if isinstance(value, dict) and value:
        return value
    return _KITTY_MODIFIER_BITS


def _kitty_event_types() -> list:
    """kitty 事件类型名列表（索引 = 事件码 - 1；数据注册表优先）。"""
    from src.presentation_data import kitty_protocol

    value = (kitty_protocol() or {}).get("event_types")
    if isinstance(value, (list, tuple)) and value:
        return [str(item) for item in value]
    return [_KITTY_EVENT_TYPES[1], _KITTY_EVENT_TYPES[2], _KITTY_EVENT_TYPES[3]]


def decode_kitty_modifiers(bits: int) -> dict:
    """把 kitty 修饰位掩码解码为 ``{名称: bool}``（未知/负数位 → 全 False）。"""
    try:
        bits = int(bits)
    except (TypeError, ValueError):
        bits = 0
    mapping = _kitty_modifier_bits()
    if bits <= 0:
        return {name: False for name in mapping}
    return {name: bool(bits & int(flag)) for name, flag in mapping.items()}


def _kitty_bits_from_modifier(modifier: int) -> int:
    """kitty 修饰值 → 位掩码（协议规定 ``值 = 1 + 位掩码``）。"""
    try:
        value = int(modifier)
    except (TypeError, ValueError):
        return 0
    return max(0, value - 1)


#: US 布局 Shift 符号映射兜底快照（数据注册表 ``kitty_protocol.us_shift_map``
#: 优先）。仅用于增强键盘协议下终端**未**上报 alternate key 的场景。
_US_SHIFT_MAP_FALLBACK: dict = {
    "`": "~", "1": "!", "2": "@", "3": "#", "4": "$", "5": "%",
    "6": "^", "7": "&", "8": "*", "9": "(", "0": ")",
    "-": "_", "=": "+", "[": "{", "]": "}", "\\": "|",
    ";": ":", "'": "\"", ",": "<", ".": ">", "/": "?",
}


def _us_shift_map() -> dict:
    """US 布局 Shift 映射（数据注册表优先，缺席/异常回退内置快照）。"""
    try:
        from src.presentation_data import kitty_protocol

        value = (kitty_protocol() or {}).get("us_shift_map")
        if isinstance(value, dict) and value:
            return {str(k): str(v) for k, v in value.items()}
    except Exception:
        pass
    return _US_SHIFT_MAP_FALLBACK


def shifted_printable_char(base_code: int, shifted_code: int = 0,
                           shift: bool = True) -> str:
    """增强键盘协议（CSI u）按键 → 实际输入字符（无可生成字符返回空串）。

    - 终端上报 alternate key（``\\x1b[47:63;2u`` 中的 63）时直接采用该字符；
    - 未上报时按 US 布局对 base 字符做 Shift 映射（``/`` → ``?``、``a`` →
      ``A``、``1`` → ``!``）；
    - ``shift`` 为假且无 shifted keycode 时返回 base 字符本身（可打印 ASCII
      限定）；非可打印 keycode 一律返回空串（调用方保持 ``csi_u`` 语义）。
    """
    try:
        shifted = int(shifted_code or 0)
    except (TypeError, ValueError):
        shifted = 0
    if 32 <= shifted <= 0x10FFFF:
        try:
            return chr(shifted)
        except (ValueError, OverflowError):
            pass
    try:
        code = int(base_code or 0)
    except (TypeError, ValueError):
        return ""
    if not (32 <= code <= 126):
        return ""
    ch = chr(code)
    if not shift:
        return ch
    if ch.isalpha():
        return ch.upper()
    return _us_shift_map().get(ch, ch)


def _kitty_event_type(groups) -> str:
    """从 CSI-u 子参数分组提取事件类型名（无 → 空串）。"""
    if len(groups) >= 2 and len(groups[1]) >= 2:
        code = groups[1][1]
        types = _kitty_event_types()
        if isinstance(code, int) and 1 <= code <= len(types):
            return types[code - 1]
    return ""


# ═══════════════════════════════════════════════════════════
# InputParser — ANSI 解析策略（无共享实例状态，fd 均以参数传入）
# ═══════════════════════════════════════════════════════════

class InputParser:
    """ANSI 输入解析策略。

    从 Input 类提取的解析算法族；Input 组合持有本类实例并委托。
    所有方法保持与 _input.py 原实现逐行等价（零逻辑改动）。
    """

    def __init__(self, io=None) -> None:
        """构造解析策略。

        Args:
            io: InputIO 实例（可选）。注入后 ESC/SS3/UTF-8 的后续字节经
                ``io.read_with_timeout`` 读取——优先消费批量读取 pending
                （已在内存在，零等待、零 select 超时）；None 时回退旧
                select+os.read 逻辑（独立可用，兼容直接构造场景）。
        """
        self._io = io

    def _read_with_timeout(self, fd: int, timeout: float) -> bytes | None:
        """读取单个后续字节（优先 InputIO.pending，回退 select+os.read）。

        io 已注入（正常装配路径）时委托 ``self._io.read_with_timeout``——
        pending 缓冲有字节（批量读取剩余）则零等待直取；io 为 None（直接
        构造场景）时保持旧 select+os.read 逻辑（可被 patch 全局拦截）。

        Returns:
            单字节 bytes；None — 超时/EOF/异常（无后续字节）。
        """
        if self._io is not None:
            return self._io.read_with_timeout(timeout, fd)
        try:
            ready, _, _ = select.select([fd], [], [], timeout)
        except (ValueError, OSError, TypeError, AttributeError):
            return None
        if not ready:
            return None
        try:
            raw = os.read(fd, 1)
            return raw if raw else None
        except (ValueError, OSError, TypeError):
            return None

    def _restore_byte(self, data: bytes) -> None:
        """将未消费的单字节回写到待处理缓冲（供后续解析正常消费）。

        P2-1（review）：Alt+Backspace 排空检测误读的多字节首字节等场景——
        回写 pending 前缀（io 未注入时忽略——回退 select+os.read 场景无法
        回写，放弃该字节）。
        """
        if self._io is not None:
            self._io.prepend_pending(data)

    def feed_byte(self, byte: int) -> KeyEvent | None:
        """单字节推入解析状态机。

        Args:
            byte: 单字节整数值 (0-255)。

        Returns:
            KeyEvent — 完整按键事件；None — 需要解析完整转义序列。
        """
        # ── ESC 序列入口 ──
        if byte == 0x1b:
            return None

        # ── ASCII 控制字符分发 ──
        if byte <= 0x1f or byte == 0x7f:
            return self._decode_control_char(byte)

        # ── ASCII 可打印 ──
        if byte < 0x80:
            return KeyEvent(kind="char", char=chr(byte), raw=bytes([byte]))

        # ── 高位字节（UTF-8 多字节序列的一部分） ──
        # P2-2 修复：单字节 feed_byte 无法构成完整 UTF-8 字符——旧实现以
        # errors="replace" 解码产出 U+FFFD 字符事件（孤立续字节被当作可打印
        # 字符；except UnicodeDecodeError 分支为死代码，因 replace 不抛错）。
        # 高位字节经 read_utf8_char 完整序列路径处理，本方法返回 unknown
        # （不再产生 U+FFFD 字符事件）。
        return KeyEvent(kind="unknown", raw=bytes([byte]))

    def parse_sequence(self, fd: int) -> KeyEvent:
        """解析 ESC 转义序列（含 I/O）。

        在首字节已确认为 0x1b 后调用。fd 由调用方显式传入
        （Input.parse_sequence 负责注入 self._fd 或 fd_override）。

        Args:
            fd: 输入文件描述符。

        Returns:
            解析后的 KeyEvent。
        """
        return self._parse_escape_sequence(fd)

    def _parse_escape_sequence(self, fd: int) -> KeyEvent:
        """读取并解析 ESC 转义序列（含 I/O）。

        优化（2026-08-14 批量读取）：后续字节经 ``_read_with_timeout``
        读取——pending 中有字节（同批 read 的方向键等）零等待直取；无
        pending 时 select 等待 ``_ESC_FOLLOWUP_TIMEOUT``（纯 Esc 判定）。
        """
        # 读取 ESC 后的下一个字节（pending 优先，无则 select 等待）
        raw2 = self._read_with_timeout(fd, _ESC_FOLLOWUP_TIMEOUT)
        if not raw2:
            return KeyEvent(kind="escape", raw=b"\x1b")
        next_byte = raw2[0]

        # ── CSI 序列：ESC [ ──
        if next_byte == ord('['):
            return self._read_csi_sequence(fd)

        # ── SS3 序列：ESC O ──
        if next_byte == ord('O'):
            return self._read_ss3_sequence(fd)

        # ── Alt+Backspace：ESC DEL ──
        if next_byte == 0x7f:
            # 排空紧随的一个字节（原 select+os.read 语义；pending/无数据时
            # read_with_timeout 立即返回或超时，忽略结果）
            # P2-1（review）：仅当取到的字节为 LF/CR（0x0a/0x0d）时才丢弃——
            # 修复前无条件排空一个字节，慢速输入中 Alt+Backspace 紧随多字节
            # UTF-8 首字节（如 ESC DEL 后紧跟中文首字节 0xE4）时首字节被误吞
            # （多字节字符静默丢失）。非 LF/CR 字节回写 pending（交由解析器
            # 正常消费）；io 未注入（回退 select+os.read）时无法回写，保持
            # 旧语义（放弃该字节）。
            drained = self._read_with_timeout(fd, _ALT_BACKSPACE_DRAIN_TIMEOUT)
            if drained is not None and drained[0] not in (0x0a, 0x0d):
                self._restore_byte(drained)
            return KeyEvent(kind="backspace", modifier=1, raw=b"\x1b\x7f")

        # ── 双 Esc ──
        if next_byte == 0x1b:
            return KeyEvent(kind="interrupt", raw=b"\x1b\x1b")

        # ── 其他 ESC 组合 → Alt+可打印字符 或 中断 ──
        # 方向A 步骤1：ESC+可打印 ASCII（0x20 <= nb < 0x7f）→ alt_char 事件
        # （modifier=3 表示 Alt），Alt+B/F 词跳转由 _dispatch_key_event 消费；
        # 其余 alt_char 经 input router（router 未消费则 no-op，不产生中断）。
        # 非打印组合保持 interrupt（旧语义保留，行为变更符合需求）。
        if 0x20 <= next_byte < 0x7f:
            return KeyEvent(
                kind="alt_char",
                char=chr(next_byte),
                modifier=3,
                raw=b"\x1b" + bytes([next_byte]),
            )
        # P3（2026-08-07）：ESC 后跟高位字节（≥0x80，UTF-8 多字节序列首字节，
        # 如 Alt+中文）→ 不再静默丢弃为 unknown——继续读完整 UTF-8 字符生成
        # alt_char 事件（P2-6 review）。io 注入（正常装配）时经 read_utf8_char
        # 慢速续读（超时保留 partial 待补齐）；io 未注入时回退 select+os.read
        # 单次读取。0x7f 已在上方 Alt+Backspace 分支处理，此处 next_byte 仅
        # 可能 < 0x20 或 ≥ 0x80。
        if next_byte >= 0x80:
            if self._io is not None:
                ch = self._io.read_utf8_char(fd, next_byte)
            else:
                ch = self._read_utf8_fallback(fd, next_byte)
            if ch:
                return KeyEvent(
                    kind="alt_char", char=ch, modifier=3,
                    raw=b"\x1b" + ch.encode("utf-8", errors="replace"),
                )
            return KeyEvent(kind="unknown", raw=b"\x1b" + bytes([next_byte]))
        return KeyEvent(kind="interrupt", raw=b"\x1b" + bytes([next_byte]))

    def _read_utf8_fallback(self, fd: int, first_byte: int) -> str | None:
        """io 未注入时的多字节 UTF-8 续读回退（select+os.read，单次读取）。

        P2-6：ESC 后跟高位字节（Alt+中文）在 io=None（直接构造）场景的续读
        回退——字节数判定与 ``InputIO.read_utf8_char`` 一致；续读超时/非法
        序列返回 None（调用方降级 unknown，不误触发中断）。
        """
        if (first_byte & 0xE0) == 0xC0:
            total = 2
        elif (first_byte & 0xF0) == 0xE0:
            total = 3
        elif (first_byte & 0xF8) == 0xF0:
            total = 4
        else:
            return None
        buf = bytes([first_byte])
        for _ in range(total - 1):
            raw = self._read_with_timeout(fd, _UTF8_READ_TIMEOUT)
            if raw is None:
                break
            buf += raw
        try:
            return buf.decode("utf-8")
        except UnicodeDecodeError:
            return None

    @staticmethod
    def _decode_control_char(byte: int) -> KeyEvent:
        """将 ASCII 控制字符 (0x00-0x1F / 0x7F) 解码为 KeyEvent。"""
        raw = bytes([byte])
        if byte in (0x0d, 0x0a):        # \r / \n
            return KeyEvent(kind="enter", raw=raw)
        if byte == 0x09:                 # \t
            return KeyEvent(kind="tab", raw=raw)
        if byte == 0x7f:                 # DEL
            return KeyEvent(kind="backspace", raw=raw)
        if byte == 0x08:                 # Ctrl+H（BS 字节）
            # ★ 轨迹视图开关（2026-08-19）：0x08（Ctrl+H）从 backspace 改判为
            #   ctrl_key '\x08'——现代终端（Windows Terminal/iTerm2/kitty/
            #   wezterm/Termux 等）Backspace 键发送 0x7f（DEL），Ctrl+H 发送
            #   0x08，字节可区分。InputDispatcher 的 ctrl_key 分发（router
            #   优先）消费为轨迹视图开关；未注入回调时回退 backspace 语义
            #   （0x08 传统 BS 兼容，行为与修复前一致）。
            return KeyEvent(kind="ctrl_key", char="\x08", raw=raw)
        if byte == 0x03:                 # Ctrl+C
            return KeyEvent(kind="interrupt", raw=raw)
        if byte == 0x01:                 # Ctrl+A → Home
            return KeyEvent(kind="home", raw=raw)
        # 标准 readline 编辑键（2026-08-05 增加操作）：
        #   Ctrl+E（0x05）→ 光标移到当前逻辑行尾（end 语义，readline 标准）。
        #   修复前（方向1 B1）为 ctrl_key no-op——用户要求增加更多操作，恢复
        #   readline 行尾键；与 End 键（\x1b[F / CSI u 4u）走同一事件分支。
        if byte == 0x05:
            return KeyEvent(kind="end", raw=raw)
        #   Ctrl+F（0x06）→ 光标右移一个字符（readline forward-char）。
        #   修复前为 unknown（静默丢弃）——readline 标准编辑键，与 → 箭头
        #   （\x1b[C）走同一 arrow_right 事件分支。
        if byte == 0x06:
            return KeyEvent(kind="arrow_right", raw=raw)
        if byte == 0x17:                 # Ctrl+W → delete word left
            return KeyEvent(kind="delete", modifier=1, raw=raw)
        if byte == 0x15:                 # Ctrl+U → kill to BOL
            return KeyEvent(kind="delete", modifier=2, raw=raw)
        if byte == 0x0b:                 # Ctrl+K → kill to EOL
            return KeyEvent(kind="delete", modifier=3, raw=raw)
        # Claude TUI parity 步骤 1.4：Ctrl+L(0x0c 清屏) / Ctrl+D(0x04 EOF) /
        # Ctrl+T(0x14 主题) 加入特殊按键（分发在 dispatcher 处理）
        # 2026-08-05（增加操作）：Ctrl+E（0x05）已恢复为 end 事件（不再在
        # ctrl_key 集合）；Ctrl+P（0x10）加入 ctrl_key（dispatcher 处理为
        # readline 历史上一条——与 Ctrl+N 被 switch_model 占用的对称补充）。
        # Ctrl+B(0x02) → 主 agent 空模式切换（0x02 非打印控制，不与 Enter 冲突）
        if byte in (0x02, 0x04, 0x07, 0x0c, 0x0e, 0x0f, 0x10, 0x12, 0x14,
                    0x19, 0x1a):  # Ctrl+B/D/G/L/N/O/P/R/T/Y/Z
            return KeyEvent(kind="ctrl_key", char=chr(byte), raw=raw)
        # 其他控制字符 → unknown
        return KeyEvent(kind="unknown", raw=raw)

    def _read_csi_sequence(self, fd: int) -> KeyEvent:
        """读取 CSI 序列参数 + 终结符并解析为 KeyEvent。

        方向1 B6：循环内累积已读原始字节到 ``raw_acc``（初始 ``b"\\x1b["``，
        每读入字节先 ``raw_acc += raw_bytes`` 再 decode 处理）；超时
        （terminator 为 None）时 unknown 事件 raw 含已读参数（原返回
        ``b"\\x1b["`` 丢失已读部分）；成功路径 raw 构建不变（经
        ``_params_to_bytes``，与 _dispatch_csi 内部构建一致）。

        优化（2026-08-14 批量读取）：后续字节经 ``_read_with_timeout`` 读取
        ——pending 有字节（同批 read 的方向键等）零等待直取；无 pending 时
        select 等待 ``_CSI_READ_TIMEOUT``。
        """
        params: list[int] = []
        #: 子参数分组（kitty 键盘协议）：外层按 ';' 分组、内层按 ':' 分子参数。
        #: 例如 ``\x1b[97:65;5:2u`` → ``[[97, 65], [5, 2]]``。``params`` 为其
        #: 扁平化（保持既有参数消费路径完全不变）。
        groups: list[list[int]] = [[]]
        current = ""
        terminator: str | None = None
        raw_acc = b"\x1b["  # 方向1 B6：累积已读原始字节（超时 raw 保留）

        def _flush_group_value() -> None:
            """把当前数字串落入当前分组末尾（空则 0，与旧行为一致）。"""
            try:
                value = int(current) if current else 0
            except ValueError:
                value = 0
            params.append(value)
            groups[-1].append(value)

        while True:
            # P1-1（review 2026-08-06）：无终止符输入流（fd 持续可读的
            # 数字/异常字节）无限循环阻塞 render 线程——达上限 break 视为
            # 解析失败（unknown）。
            if len(raw_acc) >= _CSI_MAX_BYTES:
                break
            # 读取下一字节：pending 优先（零等待），空则 select 等待；超时
            # /EOF/异常均返回 None → break（等价原 while select 条件 False）。
            raw_c = self._read_with_timeout(fd, _CSI_READ_TIMEOUT)
            if raw_c is None:
                break
            raw_acc += raw_c  # 方向1 B6：先累积再 decode 处理
            c = raw_c.decode("utf-8", errors="replace")
            if c == ';':
                _flush_group_value()
                groups.append([])
                current = ""
            # P1-1（review 2026-08-06）：``str.isdigit()`` / ``str.isalpha()``
            # 对 Unicode 数字/字母（'²'/'٣'/'é' 等）返回 True——UTF-8 续字节
            # 或异常字节 decode 后可能被误当参数数字/终止符（污染 current 或
            # 提前终止 CSI 解析）。限制为 ASCII（``c.isascii()``，Py3.7+）。
            elif c.isascii() and 0x3A <= ord(c) <= 0x3F and c != ';':
                # ★ P3（review 2026-08-22）：ECMA-48 参数中间字节（':' 0x3A 及
                #   '<' 0x3C '=' 0x3D '>' 0x3E '?' 0x3F）——修复前落入无分支，
                #   仅累积 raw_acc（如 \x1b[38:2:255:0:0m 被解析 params=[382,...]
                #   数字粘连）。按参数分隔符处理（与 ';' 等价）：完成当前 param
                #   并忽略该字节（框架仅支持 ';' 分隔；子参数子分隔语义无消费方）。
                #   ★ kitty 键盘协议：':' 为**同组子参数分隔**（不新开外层组，
                #   供 ``\x1b[<code>:<shifted>:<base>;<mod>:<event>u`` 解析）。
                _flush_group_value()
                current = ""
            elif c.isascii() and c.isdigit():
                current += c
            # P2-2（review）：CSI 终止符集合不完整——原仅 ``isalpha()`` 或
            # '~'（缺 '@'、'['、']'、'^'、'_'、'`'、'{'、'|'、'}' 等 ECMA-48
            # 最终字节）。按 CSI 最终字节全范围 ``0x40 <= ord(c) <= 0x7E``
            # 判定（';' 0x3B / 数字 0x30-0x39 已在上方分支先行处理，不会到达
            # 此处）。
            elif c.isascii() and 0x40 <= ord(c) <= 0x7E:
                if current:
                    _flush_group_value()
                terminator = c
                break

        if terminator is None:
            # 方向1 B6：超时 → unknown raw 保留已读参数（原返回 b"\x1b[" 丢失）
            return KeyEvent(kind="unknown", raw=raw_acc)

        # ── 括号粘贴（bracketed paste）：ESC[200~ … ESC[201~ ──
        # 整段内容（含换行/控制字符）作为单个 pasted-text 事件到达，不再被
        # 误判为逐字符按键（React Ink v7 自动启用括号粘贴后的官方语义）。
        if raw_acc.startswith(_PASTE_START_MARK) or (
            terminator == "~" and params and params[0] == 200
        ):
            text = self._read_bracketed_paste(fd)
            return KeyEvent(kind="paste", char=text, raw=raw_acc)

        # ── SGR 鼠标（xterm 1006）：ESC[<b;x;yM/m ──
        if terminator in ("M", "m") and raw_acc.startswith(b"\x1b[<"):
            mouse_event = decode_sgr_mouse(raw_acc)
            if mouse_event is not None:
                return mouse_event

        event = self._dispatch_csi(params, terminator, groups)
        # ★ 修复（2026-10）：raw 保真——``_dispatch_csi`` 的 raw 由展平参数重建
        #   （``\x1b[47:63;2u`` 的 kitty 子参数分隔符 ``:`` 会变成 ``;``），而
        #   ``unknown`` 事件经 dispatcher 回写捕获缓冲（``_captured_input``）
        #   会还原出错误字节。统一以本方法累积的原始字节覆盖（对既有等价形式
        #   零变化，仅修正子参数形式）。
        event.raw = raw_acc
        if terminator == 'u':
            # ★ kitty 键盘协议元信息落位（统一在解析出口写入，避免在
            #   ``_dispatch_csi`` 的多个 return 分支逐处补字段）：
            #   kitty_bits = 原始 CSI-u 修饰值 - 1（**取自分组参数，而非
            #   ``event.modifier``**——映射分支可能重写 modifier，如 Ctrl+A
            #   → home 的 modifier 被置 0，用事件字段会丢失超键/锁定键位）；
            #   event_type = 次组第二子参数映射（press/repeat/release）。
            #   非 'u' 终结符保持默认（kitty_bits=-1 / event_type=""）。
            kitty_modifier = groups[1][0] if len(groups) >= 2 and groups[1] else 1
            event.kitty_bits = _kitty_bits_from_modifier(kitty_modifier)
            event.event_type = _kitty_event_type(groups)
        return event

    def _read_bracketed_paste(self, fd: int) -> str:
        """读取括号粘贴内容（``ESC[201~`` 结束标记前）并解码为文本。

        终端在括号粘贴模式下把整段粘贴内容包在 ``ESC[200~`` … ``ESC[201~``
        之间一次送出——内容可含换行/控制字符而不会被解释为按键。读取到结束
        标记后，标记**之后**同批到达的字节回写 pending（后续解析正常消费）；
        超时/超限（``_PASTE_MAX_BYTES``）时返回已读内容（不丢用户输入）。

        Returns:
            粘贴文本（UTF-8 解码，非法字节以 U+FFFD 兜底）。
        """
        buf = b""
        while True:
            idx = buf.find(_PASTE_END_MARK)
            if idx >= 0:
                content = buf[:idx]
                trailing = buf[idx + len(_PASTE_END_MARK):]
                if trailing:
                    self._restore_byte(trailing)
                return content.decode("utf-8", errors="replace")
            if len(buf) >= _PASTE_MAX_BYTES:
                break
            chunk = self._read_paste_chunk(fd)
            if chunk is None:
                break
            buf += chunk
        # 未见结束标记（截断/超限）：丢弃标记前缀后返回已读内容
        if buf.startswith(_PASTE_START_MARK):
            buf = buf[len(_PASTE_START_MARK):]
        return buf.decode("utf-8", errors="replace")

    def _read_paste_chunk(self, fd: int) -> bytes | None:
        """读取一段粘贴内容（io 注入时批量读，否则回退逐字节）。"""
        if self._io is not None:
            reader = getattr(self._io, "read_bulk", None)
            if reader is not None:
                return reader(fd, 65536, _PASTE_READ_TIMEOUT)
        return self._read_with_timeout(fd, _PASTE_READ_TIMEOUT)

    def _read_ss3_sequence(self, fd: int) -> KeyEvent:
        """读取 SS3 序列（ESC O + 字符，通常为 F1-F4）。

        方向A 步骤1：ESC O P/Q/R/S → f1/f2/f3/f4 功能键事件；
        其余 SS3 字符保持 unknown（raw 保留完整字节供调试/未来消费）。

        优化（2026-08-14 批量读取）：后续字节经 ``_read_with_timeout`` 读取
        ——pending 有字节零等待直取；无 pending 时 select 等待
        ``_SS3_READ_TIMEOUT``。
        """
        raw_c = self._read_with_timeout(fd, _SS3_READ_TIMEOUT)
        if raw_c:
            raw = b"\x1bO" + raw_c
            mapping = {
                ord('P'): "f1",
                ord('Q'): "f2",
                ord('R'): "f3",
                ord('S'): "f4",
                # ★ 应用光标键模式（DECCKM，2026-08-06）：部分终端
                #   （SSH 客户端/kitty 等）默认开启应用模式，方向键
                #   发送 \x1bOA/B/C/D 而非 \x1b[A/B/C/D——修复前
                #   mapping 缺失 → unknown 静默丢弃，↑↓←→ 全部失效。
                #   与 CSI 箭头语义一致（modifier 无修饰）。
                ord('A'): "arrow_up",
                ord('B'): "arrow_down",
                ord('C'): "arrow_right",
                ord('D'): "arrow_left",
            }
            kind = mapping.get(raw_c[0], "unknown")
            return KeyEvent(kind=kind, raw=raw)
        return KeyEvent(kind="unknown", raw=b"\x1bO")

    @staticmethod
    def _dispatch_csi(params: list[int], terminator: str, groups: list[list[int]] | None = None) -> KeyEvent:
        """根据 CSI 参数和终结符分发到对应的 KeyEvent。

        Args:
            params: 扁平参数列表（所有分组按顺序展开——既有消费路径不变）。
            terminator: CSI 最终字节。
            groups: 子参数分组（kitty 键盘协议 ``':'`` 子参数分隔）——
                ``\\x1b[97:65;5:2u`` → ``[[97, 65], [5, 2]]``；None 时按每个
                param 独立成组（兼容仅传 params 的旧调用/测试）。
        """
        if groups is None:
            groups = [[p] for p in params]
        # ── CSI u 模式: \x1b[<keycode>;<modifier>u ──
        if terminator == 'u':
            # ★ kitty 键盘协议（子参数分组）：完整形式
            #   ``\x1b[<code>:<shifted>:<base>;<mod>:<event>u`` 的参数经 ':' 分子
            #   参数（groups）解析——keycode 取首组首值、modifier 取次组首值
            #   （标准形式 ``\x1b[<code>;<mod>u`` 下与旧的 params[0]/params[1]
            #   完全等价，零回归）。event 子参数（次组第二值）由
            #   ``_read_csi_sequence`` 事后写入 ``KeyEvent.event_type``。
            keycode = groups[0][0] if groups and groups[0] else 0
            modifier = (groups[1][0] if len(groups) >= 2 and groups[1] else 1)
            raw = b"\x1b[" + InputParser._params_to_bytes(params) + b"u"
            # ★ L2（2026-08-15）：CSI-u 修饰 Enter 语义对齐——Shift/Ctrl/Alt+
            #   Enter（keycode=13, modifier 2/3/5）由「插入换行」（kind="char"
            #   char="\n"，被 _dispatch_key_event 当可打印字符插入缓冲）改为
            #   「提交」（kind="enter"，与普通 Enter 0x0d / \x1b[13;1u 一致）。
            #   对齐提交语义：router 优先消费（UserSelectPopup 等组件可正常
            #   消费 enter）；未消费走 _dispatch_key_event ``kind=="enter"``
            #   → _enter() 提交（含搜索模式/残留 LF 丢弃）；_hooks_input.py
            #   useInput ``"return": kind == "enter"`` 同步触发（与普通 Enter
            #   一致，符合用户直觉）。
            if keycode == 13 and modifier in (2, 3, 5):
                return KeyEvent(kind="enter", modifier=modifier,
                                keycode=keycode, raw=raw)
            # 方向A 步骤1：CSI u Shift+Tab（keycode=9, modifier=2）→ tab modifier=2
            # （_dispatch_key_event 消费：补全可见时反向循环）。
            if keycode == 9 and modifier == 2:
                return KeyEvent(kind="tab", modifier=2, keycode=keycode, raw=raw)
            # ★ P1-1 修复（CSI u 键盘协议 Alt+Backspace/Delete）：显式处理
            #   ``\x1b[8;3u``（keycode=8, modifier=3 即 Alt）→ backspace
            #   modifier=1（词删除，与 ESC DEL / Ctrl+W 传统路径语义一致）；
            #   ``\x1b[127;3u`` → delete modifier=1——修复前此类事件落入
            #   ``csi_u`` no-op，真 Alt+Backspace 失效。
            if keycode in (8, 127) and modifier == 3:
                kind = "backspace" if keycode == 8 else "delete"
                return KeyEvent(
                    kind=kind, modifier=1, keycode=keycode, raw=raw,
                )
            # ★ 方向2（CSI u 增强键盘协议 modifier=1 映射）：无修饰键的
            #   Enter/Tab/Home/End/方向键在增强键盘协议下发送 ``keycode;1u``——
            #   修复前这些事件落入 ``csi_u`` 被静默丢弃（P3-4 no-op 分支）。
            #   方向键覆盖 kitty 码位（57417-57420）——**不含 ASCII 变体**
            #   （方向1 修复：CSI-u 协议中 keycode 65/66/67/68 即大写字母
            #   A/B/C/D，不是方向键；旧映射把增强键盘终端输入的大写字母吞成
            #   方向键。遗留 CSI 箭头 ``\x1b[A`` 已由下方终结符分支处理）。
            #   未知 keycode modifier=1 且为可打印 ASCII（32-126）→ char 事件
            #   （大写/小写字母、数字、标点经 CSI-u 输入的修复——旧实现落入
            #   csi_u no-op 被静默丢弃）；其余仍走 csi_u（router 可消费）。
            if modifier == 1:
                if keycode == 13:
                    return KeyEvent(kind="enter", modifier=1, keycode=keycode, raw=raw)
                if keycode == 9:
                    return KeyEvent(kind="tab", modifier=1, keycode=keycode, raw=raw)
                if keycode == 1:
                    return KeyEvent(kind="home", modifier=1, keycode=keycode, raw=raw)
                if keycode == 4:
                    return KeyEvent(kind="end", modifier=1, keycode=keycode, raw=raw)
                # ★ P1-1 修复（CSI u 增强键盘协议下 Backspace/Delete/Esc 映射）：
                #   kitty/wezterm 等启用键盘协议（modifyOtherKeys）的终端发送
                #   ``\x1b[8;1u``（普通 Backspace）/``\x1b[127;1u``（普通 Delete）/
                #   ``\x1b[27;1u``（Esc）。**modifier=1 表示无修饰键**——映射为
                #   modifier=0 事件走普通删除语义（修复前误用 modifier=1 词删除
                #   语义，普通退格/删除每次删除整个词）；显式 Alt+Backspace/
                #   Delete（modifier=3）已在上述独立分支处理为 modifier=1。
                if keycode == 8:
                    return KeyEvent(kind="backspace", modifier=0, keycode=keycode, raw=raw)
                if keycode == 127:
                    return KeyEvent(kind="delete", modifier=0, keycode=keycode, raw=raw)
                if keycode == 27:
                    return KeyEvent(kind="escape", modifier=1, keycode=keycode, raw=raw)
                if keycode == 57417:   # ↑
                    return KeyEvent(kind="arrow_up", modifier=1, keycode=keycode, raw=raw)
                if keycode == 57418:   # ↓
                    return KeyEvent(kind="arrow_down", modifier=1, keycode=keycode, raw=raw)
                if keycode == 57419:   # ←
                    return KeyEvent(kind="arrow_left", modifier=1, keycode=keycode, raw=raw)
                if keycode == 57420:   # →
                    return KeyEvent(kind="arrow_right", modifier=1, keycode=keycode, raw=raw)
                # ★ 2026-08-05（增加操作）：kitty/wezterm 增强键盘协议 PageUp/
                #   PageDown（57358/57359）→ page_up/page_down 事件（补全弹窗
                #   翻页；与 ``\x1b[5~``/``\x1b[6~`` 同语义）——修复前落入
                #   csi_u no-op 被静默丢弃，CSI-u 终端无法翻页。
                if keycode == 57358:   # PageUp
                    return KeyEvent(kind="page_up", modifier=1, keycode=keycode, raw=raw)
                if keycode == 57359:   # PageDown
                    return KeyEvent(kind="page_down", modifier=1, keycode=keycode, raw=raw)
                # ★ CSI-u 可打印 ASCII 键（无修饰键）→ char 事件（方向1 修复：
                #   kitty/wezterm/iTerm2 等增强键盘终端输入普通字母/数字/标点
                #   发送 ``keycode;1u``——keycode 即 ASCII 码（如 'A'=65）。
                #   修复前大写 A/B/C/D 被误映射方向键、小写字母/数字落入
                #   csi_u no-op 被静默丢弃，CSI-u 终端无法正常打字。）
                if 32 <= keycode <= 126:
                    return KeyEvent(kind="char", char=chr(keycode), modifier=1,
                                    keycode=keycode, raw=raw)
            # P2-5（review）：CSI u Ctrl+方向键（modifier=5，kitty/wezterm
            # 码位 57417-57420）→ 方向键 modifier=5（词跳转语义，与
            # ``\x1b[1;5C`` 等传统 CSI 路径一致）——修复前落入 csi_u
            # no-op，增强键盘协议终端 Ctrl+方向键失效。
            if modifier == 5:
                if keycode == 57417:   # Ctrl+↑
                    return KeyEvent(kind="arrow_up", modifier=5, keycode=keycode, raw=raw)
                if keycode == 57418:   # Ctrl+↓
                    return KeyEvent(kind="arrow_down", modifier=5, keycode=keycode, raw=raw)
                if keycode == 57419:   # Ctrl+←
                    return KeyEvent(kind="arrow_left", modifier=5, keycode=keycode, raw=raw)
                if keycode == 57420:   # Ctrl+→
                    return KeyEvent(kind="arrow_right", modifier=5, keycode=keycode, raw=raw)
            # 方向A 步骤1：CSI u Ctrl+字母（keycode 97-122, modifier=5）→ 复用
            # _decode_control_char(keycode-96) 语义（Ctrl+A=Home、Ctrl+W=delete word 等）。
            if 97 <= keycode <= 122 and modifier == 5:
                decoded = InputParser._decode_control_char(keycode - 96)
                return KeyEvent(kind=decoded.kind, char=decoded.char,
                                modifier=decoded.modifier, keycode=keycode, raw=raw)
            # 方向1 B1：CSI u Ctrl 字母解码扩展至 keycode 1-26（modifier=5），
            # 使 \x1b[5;5u（Ctrl+E）等小键码也映射 ctrl_key——真实增强键盘
            # 协议终端以 keycode 而非 ASCII 字母发送 Ctrl 组合（修复 Ctrl 组合
            # 经 CSI u 路径失效）。keycode 1-26 即 ASCII 控制码（Ctrl+X 编码
            # = X 在字母表中的位置），直接经 _decode_control_char 解码
            # （keycode=5 → 0x05 → ctrl_key '\x05'）。★ 防御排除修正（P3
            # review）：13（modifier=5 → enter 提交语义）已在更早分支处理；
            # 9 仅 modifier=2（Tab）已处理——9/5（Ctrl+Tab）未在其他分支处理，
            # 落入本分支排除后走 csi_u（router 可消费），语义归属以本注释为准。
            if 1 <= keycode <= 26 and modifier == 5 and keycode not in (9, 13):
                # ★ P3（review）：Ctrl+Backspace（增强键盘协议 keycode=8/127,
                #   modifier=5）显式映射为词删除——修复前 keycode=8 落入本分支
                #   经 ``_decode_control_char(8)`` 得 ctrl_key "\x08"，生产路径
                #   dispatcher 将其消费为轨迹视图开关（与 Ctrl+W /
                #   Alt+Backspace 的既有词删除语义冲突）。
                if keycode in (8, 127):
                    return KeyEvent(
                        kind="delete", modifier=1, keycode=keycode, raw=raw,
                    )
                decoded = InputParser._decode_control_char(keycode)
                return KeyEvent(kind=decoded.kind, char=decoded.char,
                                modifier=decoded.modifier, keycode=keycode, raw=raw)
            # ★ 修复（2026-10）：CSI u **Shift 组合可打印字符**（``?`` / ``:`` /
            #   ``@`` / ``{`` / 大写字母…）——增强键盘协议（kitty / WezTerm /
            #   iTerm2 / Windows Terminal 等 "report all keys as escape codes"）
            #   下 Shift+键发送 ``\x1b[<base>[:<shifted>];2u``，修复前落入
            #   ``csi_u`` no-op 被静默丢弃：**Shift 符号完全打不出来**（输入框
            #   打不出 ``?``/``:``/``{``，模态视图的 ``?`` 帮助键与 ``:`` 等
            #   快捷键失效）。规则：
            #     - 优先采用终端上报的 shifted keycode（``47:63`` → ``?``）；
            #     - 缺席时按 US 布局映射 base 字符（``/`` → ``?``、``a`` → ``A``）；
            #     - 含 Ctrl/Super/Hyper/Meta 的组合不生成字符（保持 ``csi_u``
            #       交由 router/旧路径处理）；CapsLock/NumLock 位不影响判定；
            #     - 同时含 Alt 时生成 ``alt_char``（与 ESC+字符 的 Alt 语义一致）。
            mod_bits = _kitty_bits_from_modifier(modifier)
            if not (mod_bits & (4 | 8 | 16 | 32)):
                _key_group = groups[0] if groups else []
                _base_code = _key_group[0] if _key_group else 0
                _shifted_code = _key_group[1] if len(_key_group) > 1 else 0
                if (mod_bits & 1) or _shifted_code:
                    _text = shifted_printable_char(
                        _base_code, _shifted_code, bool(mod_bits & 1),
                    )
                    if _text:
                        if mod_bits & 2:
                            return KeyEvent(kind="alt_char", char=_text,
                                            modifier=modifier,
                                            keycode=_base_code, raw=raw)
                        return KeyEvent(kind="char", char=_text,
                                        modifier=modifier,
                                        keycode=_base_code, raw=raw)
            return KeyEvent(kind="csi_u", modifier=modifier, keycode=keycode, raw=raw)

        raw = b"\x1b[" + InputParser._params_to_bytes(params) + terminator.encode()

        # ── 功能键序列: \x1b[N~（F5-F12；\x1b[11~..14~ 为部分终端的 F1-F4） ──
        if terminator == '~':
            p = params[0] if params else 0
            _fkey = {
                11: "f1", 12: "f2", 13: "f3", 14: "f4",
                15: "f5", 17: "f6", 18: "f7", 19: "f8",
                20: "f9", 21: "f10", 23: "f11", 24: "f12",
            }.get(p)
            if _fkey is not None:
                return KeyEvent(kind=_fkey, raw=raw)
            if p in (1, 7):
                return KeyEvent(kind="home", raw=raw)
            if p == 3:
                return KeyEvent(kind="delete", raw=raw)
            if p in (4, 8):
                return KeyEvent(kind="end", raw=raw)
            # Page Up (\x1b[5~) / Page Down (\x1b[6~)——React Ink v6
            # useInput key.pageUp/pageDown（方向 G1）
            if p == 5:
                return KeyEvent(kind="page_up", raw=raw)
            if p == 6:
                return KeyEvent(kind="page_down", raw=raw)
            return KeyEvent(kind="unknown", raw=raw)

        # ── Home (\x1b[H) ──
        if terminator == 'H':
            return KeyEvent(kind="home", raw=raw)

        # ── End (\x1b[F) ──
        if terminator == 'F':
            return KeyEvent(kind="end", raw=raw)

        # ── 右箭头 / Shift+右 / Alt+右 / Ctrl+右 ──
        # 方向1 B7：保留 modifier 2/3/5（Alt/Shift 箭头不再降级为普通箭头）——
        # 事件字段增强，消费方按需使用（_dispatch_key_event：modifier 5 → 词跳转；
        # 2/3 → 单字符移动；input router 可消费带修饰符事件）。
        if terminator == 'C':
            if len(params) >= 2 and params[1] in (2, 3, 5):
                return KeyEvent(kind="arrow_right", modifier=params[1], raw=raw)
            return KeyEvent(kind="arrow_right", raw=raw)

        # ── 左箭头 / Shift+左 / Alt+左 / Ctrl+左 ──
        if terminator == 'D':
            if len(params) >= 2 and params[1] in (2, 3, 5):
                return KeyEvent(kind="arrow_left", modifier=params[1], raw=raw)
            return KeyEvent(kind="arrow_left", raw=raw)

        # ── 上箭头 / Shift+上 / Alt+上 / Ctrl+上 ──
        if terminator == 'A':
            if len(params) >= 2 and params[1] in (2, 3, 5):
                return KeyEvent(kind="arrow_up", modifier=params[1], raw=raw)
            return KeyEvent(kind="arrow_up", raw=raw)

        # ── 下箭头 / Shift+下 / Alt+下 / Ctrl+下 ──
        if terminator == 'B':
            if len(params) >= 2 and params[1] in (2, 3, 5):
                return KeyEvent(kind="arrow_down", modifier=params[1], raw=raw)
            return KeyEvent(kind="arrow_down", raw=raw)

        # ── Shift+Tab (\x1b[Z) — 部分终端发送 CSI Z 而非 CSI u(9;2u) ──
        # Claude TUI parity 步骤 1.4：映射为 tab modifier=2（反向补全导航）。
        if terminator == 'Z':
            return KeyEvent(kind="tab", modifier=2, keycode=9, raw=raw)

        # ── 其他 CSI 序列 ──
        return KeyEvent(kind="unknown", raw=raw)

    @staticmethod
    def _params_to_bytes(params: list[int]) -> bytes:
        """将参数列表转为 CSI 参数字节串。"""
        if not params:
            return b""
        return ";".join(str(p) for p in params).encode()
