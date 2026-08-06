"""InputParser — TUI 输入 ANSI 解析逻辑（提取自 _input.py，方向⑤）。

将 Input 上帝类中的解析算法族提取为独立策略对象，Input 组合持有：
  - feed_byte: 单字节推入解析状态机
  - _decode_control_char: ASCII 控制字符解码（静态）
  - _parse_escape_sequence / _read_csi_sequence / _read_ss3_sequence: ESC 序列读取（I/O）
  - _dispatch_csi / _params_to_bytes: CSI 参数分发（静态）
  - parse_sequence: ESC 序列解析入口（I/O）

KeyEvent 数据类随解析逻辑搬移至本模块（Input 层 re-export，公开 API 不变）。

设计模式:
  策略（Strategy）— 解析算法族从 Input 提取为独立策略对象，Input 组合持有。

依赖方向:
  _input.py → _input_parser.py 单向依赖；本模块不得 import _input（避免循环）。

模块级 ``import select`` 供 I/O 方法使用；可被 ``patch("select.select", ...)``
全局拦截（与 _input.py 原行为等价）。
"""

from __future__ import annotations

import os
import select

from src._compat import dataclass

__all__ = ["InputParser", "KeyEvent"]

# ── 常量 ──────────────────────────────────────────────────

_CSI_READ_TIMEOUT = 0.01     # CSI 参数读取超时（秒）
_SS3_READ_TIMEOUT = 0.01     # SS3 读取超时（秒）
_UTF8_READ_TIMEOUT = 0.05    # UTF-8 多字节序列读取超时（秒）
_ESC_FOLLOWUP_TIMEOUT = 0.05  # ESC 后续字节等待超时（秒）
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
                     # "alt_char" | "f1" | "f2" | "f3" | "f4"（方向A 步骤1 新增）
    char: str = ""
    modifier: int = 0
    keycode: int = 0
    raw: bytes = b""


# ═══════════════════════════════════════════════════════════
# InputParser — ANSI 解析策略（无共享实例状态，fd 均以参数传入）
# ═══════════════════════════════════════════════════════════

class InputParser:
    """ANSI 输入解析策略。

    从 Input 类提取的解析算法族；Input 组合持有本类实例并委托。
    所有方法保持与 _input.py 原实现逐行等价（零逻辑改动）。
    """

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
        """读取并解析 ESC 转义序列（含 I/O）。"""
        # 读取 ESC 后的下一个字节
        try:
            has_more, _, _ = select.select([fd], [], [], _ESC_FOLLOWUP_TIMEOUT)
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
            try:
                if select.select([fd], [], [], _ALT_BACKSPACE_DRAIN_TIMEOUT)[0]:
                    os.read(fd, 1)
            except (ValueError, OSError, TypeError):
                pass
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
        return KeyEvent(kind="interrupt", raw=b"\x1b" + bytes([next_byte]))

    @staticmethod
    def _decode_control_char(byte: int) -> KeyEvent:
        """将 ASCII 控制字符 (0x00-0x1F / 0x7F) 解码为 KeyEvent。"""
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
        if byte in (0x02, 0x04, 0x07, 0x0c, 0x0e, 0x0f, 0x10, 0x12, 0x14):  # Ctrl+B/D/G/L/N/O/P/R/T
            return KeyEvent(kind="ctrl_key", char=chr(byte), raw=raw)
        # 其他控制字符 → unknown
        return KeyEvent(kind="unknown", raw=raw)

    def _read_csi_sequence(self, fd: int) -> KeyEvent:
        """读取 CSI 序列参数 + 终结符并解析为 KeyEvent。

        方向1 B6：循环内累积已读原始字节到 ``raw_acc``（初始 ``b"\\x1b["``，
        每 ``os.read`` 到字节先 ``raw_acc += raw_bytes`` 再 decode 处理）；
        超时（terminator 为 None）时 unknown 事件 raw 含已读参数（原返回
        ``b"\\x1b["`` 丢失已读部分）；成功路径 raw 构建不变（经
        ``_params_to_bytes``，与 _dispatch_csi 内部构建一致）。
        """
        params: list[int] = []
        current = ""
        terminator: str | None = None
        raw_acc = b"\x1b["  # 方向1 B6：累积已读原始字节（超时 raw 保留）

        try:
            while select.select([fd], [], [], _CSI_READ_TIMEOUT)[0]:
                # P1-1（review 2026-08-06）：无终止符输入流（fd 持续可读的
                # 数字/异常字节）无限循环阻塞 render 线程——达上限 break 视为
                # 解析失败（unknown）。
                if len(raw_acc) >= _CSI_MAX_BYTES:
                    break
                raw_c = os.read(fd, 1)
                if not raw_c:
                    break
                raw_acc += raw_c  # 方向1 B6：先累积再 decode 处理
                c = raw_c.decode("utf-8", errors="replace")
                if c == ';':
                    try:
                        params.append(int(current) if current else 0)
                    except ValueError:
                        params.append(0)
                    current = ""
                # P1-1（review 2026-08-06）：``str.isdigit()`` / ``str.isalpha()``
                # 对 Unicode 数字/字母（'²'/'٣'/'é' 等）返回 True——UTF-8 续字节
                # 或异常字节 decode 后可能被误当参数数字/终止符（污染 current 或
                # 提前终止 CSI 解析）。限制为 ASCII（``c.isascii()``，Py3.7+）。
                elif c.isascii() and c.isdigit():
                    current += c
                elif (c.isascii() and c.isalpha()) or c == '~':
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
            # 方向1 B6：超时 → unknown raw 保留已读参数（原返回 b"\x1b[" 丢失）
            return KeyEvent(kind="unknown", raw=raw_acc)

        return self._dispatch_csi(params, terminator)

    def _read_ss3_sequence(self, fd: int) -> KeyEvent:
        """读取 SS3 序列（ESC O + 字符，通常为 F1-F4）。

        方向A 步骤1：ESC O P/Q/R/S → f1/f2/f3/f4 功能键事件；
        其余 SS3 字符保持 unknown（raw 保留完整字节供调试/未来消费）。
        """
        try:
            if select.select([fd], [], [], _SS3_READ_TIMEOUT)[0]:
                raw_c = os.read(fd, 1)
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
        except (ValueError, OSError, TypeError):
            pass
        return KeyEvent(kind="unknown", raw=b"\x1bO")

    @staticmethod
    def _dispatch_csi(params: list[int], terminator: str) -> KeyEvent:
        """根据 CSI 参数和终结符分发到对应的 KeyEvent。"""
        # ── CSI u 模式: \x1b[<keycode>;<modifier>u ──
        if terminator == 'u':
            keycode = params[0] if len(params) >= 1 else 0
            modifier = params[1] if len(params) >= 2 else 1
            raw = b"\x1b[" + InputParser._params_to_bytes(params) + b"u"
            if keycode == 13 and modifier in (2, 3, 5):
                return KeyEvent(kind="char", char="\n", modifier=modifier,
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
            # （keycode=5 → 0x05 → ctrl_key '\x05'）。keycode 9/13 已在更早
            # 分支处理（modifier=5 时 13 → char '\n'），防御排除防重复。
            if 1 <= keycode <= 26 and modifier == 5 and keycode not in (9, 13):
                decoded = InputParser._decode_control_char(keycode)
                return KeyEvent(kind=decoded.kind, char=decoded.char,
                                modifier=decoded.modifier, keycode=keycode, raw=raw)
            return KeyEvent(kind="csi_u", modifier=modifier, keycode=keycode, raw=raw)

        raw = b"\x1b[" + InputParser._params_to_bytes(params) + terminator.encode()

        # ── 功能键序列: \x1b[N~ ──
        if terminator == '~':
            p = params[0] if params else 0
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
