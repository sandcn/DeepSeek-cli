"""Code Review 输入子系统修复回归测试（P1-1/P1-2/P2-1~P2-10）。

覆盖（对应修复点）：
  - P1-1: try_read_paste 批量读取路径不吞 ESC 转义序列（方向键事件不丢失；
           _take_valid_prefix 含控制字节尾部不存入 partial）
  - P1-2: 历史字面量反斜杠 \\n 往返不损坏（_unescape 逐字符转义解析）
  - P2-1: Alt+Backspace（ESC DEL）排空不吞多字节 UTF-8 首字节
  - P2-2: CSI 终止符集合覆盖 0x40-0x7E（ECMA-48 最终字节全范围）
  - P2-3: 慢速 UTF-8 输入中断后旧 partial 以 replace 消费（不直接丢弃）
  - P2-4: read_utf8_char 的 fd 参数经 read_with_timeout 透传（不再忽略）
  - P2-5: kitty/wezterm 增强键盘协议 Ctrl+方向键映射
  - P2-6: ESC+UTF-8 高位字节（Alt+中文）生成 alt_char 事件
  - P2-7: unknown 事件完整捕获（extend(event.raw)）
  - P2-8: _handle_special_key 的 _enter 注入 append_history
  - P2-9: _HistoryDiskWriter.submit 同步写盘在 _submit_lock 锁外执行
  - P2-10: _wrap_by_width 首字符超宽保留零宽占位（光标定位不丢字）

测试约定：os.read / select.select 经 ``patch("src.tui._input_io.os.read")`` /
``patch("src.tui._input_io.select.select")`` 拦截（模块级 import，等价全局
拦截）。构造 InputIO / InputParser / InputDispatcher 直接调用，不经真实 stdin。
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from src.tui._history_disk import _HistoryDiskWriter
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_dispatcher import InputDispatcher
from src.tui._input_io import InputIO
from src.tui._input_layout import _wrap_by_width
from src.tui._input_parser import InputParser, KeyEvent
from src.tui._width import wcswidth_simple


# ═══════════════════════════════════════════════════════════
# P1-1：try_read_paste 不吞 ESC 转义序列
# ═══════════════════════════════════════════════════════════

def test_try_read_paste_esc_sequence_not_swallowed():
    """P1-1：批量读取剩余字节含 ESC（方向键序列）时不得作为粘贴文本——
    回写 pending 交由解析器逐字节消费（方向键事件不丢失）。"""
    io = InputIO(fd=0)
    io.set_pending(b"\x1b[A")  # 批量读取剩余：方向键序列
    with patch("src.tui._input_io.select.select", return_value=([], [], [])):
        result = io.try_read_paste(0, "a")
    # 非粘贴：仅返回首字符，ESC 序列完整回写 pending
    assert result == "a"
    assert io.has_pending()
    assert io.drain_pending() == b"\x1b[A"
    assert io._paste_partial == b""


def test_try_read_paste_pure_paste_still_decoded():
    """P1-1 回归：纯文本粘贴（无控制字节）仍走粘贴解码路径（不误伤）。"""
    io = InputIO(fd=0)
    io.set_pending("中".encode("utf-8"))  # 纯粘贴内容
    with patch("src.tui._input_io.select.select", return_value=([], [], [])):
        result = io.try_read_paste(0, "a")
    assert result == "a中"
    assert not io.has_pending()


def test_take_valid_prefix_control_tail_not_stored():
    """P1-1：_take_valid_prefix 含控制字节的尾部不存入 partial（修复前
    ``b"ab\\xc2\\x1b"`` 的尾部 ``b"\\xc2\\x1b"`` 含 ESC 残留，被整段存入
    partial 污染后续粘贴）。"""
    text, partial = InputIO._take_valid_prefix(b"ab\xc2\x1b")
    assert text == "ab"
    assert partial == b""


def test_decode_paste_bytes_truncated_utf8_retained():
    """P1-1 回归：合法截断 UTF-8 尾部仍正常保留待补齐（不误伤）。"""
    io = InputIO(fd=0)
    text = io._decode_paste_bytes(b"abc\xe4")
    assert text == "abc"
    assert io._paste_partial == b"\xe4"


# ═══════════════════════════════════════════════════════════
# P1-2：历史字面量反斜杠 \n 往返不损坏
# ═══════════════════════════════════════════════════════════

def test_history_literal_backslash_roundtrip():
    """P1-2：字面量反斜杠+n（如路径 C:\\new）写盘（双重转义）→ 读盘（逐字符
    还原）往返不损坏；真实换行同时正确还原。"""
    text = "C:\\new\nnext"  # 字面量 \n + 真实换行 + next
    escaped = text.replace("\\", "\\\\").replace("\n", "\\n")
    assert escaped == "C:\\\\new\\nnext"
    assert InputBufferEditor._unescape(escaped) == text


def test_unescape_literal_backslash_n_not_newline():
    """P1-2：字面量反斜杠+n（写盘双重转义后 \\\\n）还原为字面量 \\n（非换行）。"""
    assert InputBufferEditor._unescape("C:\\\\new") == "C:\\new"


def test_unescape_escaped_newline():
    """P1-2 回归：真实换行（写盘转义后 \\n）还原为换行。"""
    assert InputBufferEditor._unescape("a\\nb") == "a\nb"


def test_load_history_literal_backslash_roundtrip():
    """P1-2：load_history 全链路——模拟写盘转义后的历史文件内容，加载后
    字面量反斜杠往返不损坏。"""

    class _FakeHistoryIO:
        def __init__(self, raw: str):
            self._raw = raw

        def read(self):
            return self._raw, False

        def compact(self):
            return True

    be = InputBufferEditor(history_file=Path("unused"))
    be._history_io = _FakeHistoryIO("C:\\\\new\\nnext")
    be.load_history()
    assert be._history == ["C:\\new\nnext"]


# ═══════════════════════════════════════════════════════════
# P2-1：Alt+Backspace 不吞多字节 UTF-8 首字节
# ═══════════════════════════════════════════════════════════

def test_alt_backspace_does_not_swallow_multibyte_first_byte():
    """P2-1：ESC DEL 排空检测读到的多字节 UTF-8 首字节（0xE4）回写 pending
    （修复前被无条件排空丢弃，多字节字符静默丢失）。"""
    io = InputIO(fd=0)
    io.set_pending(b"\x7f\xe4")  # ESC DEL + 中文首字节
    parser = InputParser(io=io)
    ev = parser._parse_escape_sequence(0)
    assert ev.kind == "backspace"
    assert ev.modifier == 1
    assert io.has_pending()
    assert io.take_pending_byte() == b"\xe4"


def test_alt_backspace_drains_lf():
    """P2-1：仅 LF/CR 才被排空丢弃（保留原排空语义）。"""
    io = InputIO(fd=0)
    io.set_pending(b"\x7f\x0a")  # ESC DEL + LF
    parser = InputParser(io=io)
    ev = parser._parse_escape_sequence(0)
    assert ev.kind == "backspace"
    assert not io.has_pending()


# ═══════════════════════════════════════════════════════════
# P2-2：CSI 终止符集合覆盖 0x40-0x7E
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("term", ["@", "[", "]", "^", "_", "`", "{", "|", "}"])
def test_csi_terminator_full_range(term):
    """P2-2：非字母/非 ~ 的 ECMA-48 最终字节（0x40-0x7E）均被识别为终止符
    （修复前 '@' 等落入循环继续读 → 超时 unknown 丢失终止符）。"""
    io = InputIO(fd=0)
    io.set_pending(f"1{term}".encode())
    parser = InputParser(io=io)
    ev = parser._read_csi_sequence(0)
    assert ev.raw == f"\x1b[1{term}".encode()


def test_csi_terminator_alpha_still_works():
    """P2-2 回归：字母终止符（方向键等）保持既有行为。"""
    io = InputIO(fd=0)
    io.set_pending(b"A")
    parser = InputParser(io=io)
    ev = parser._read_csi_sequence(0)
    assert ev.kind == "arrow_up"
    assert ev.raw == b"\x1b[A"


# ═══════════════════════════════════════════════════════════
# P2-3：慢速 UTF-8 中断不丢字节
# ═══════════════════════════════════════════════════════════

def test_read_utf8_char_interrupted_partial_replaced():
    """P2-3：慢速 UTF-8 partial（b"\\xe4"）被新字符首字节打断——旧 partial
    以 errors="replace" 消费（U+FFFD 呈现）+ 新字符完整读取（字节不静默丢失）。"""
    io = InputIO(fd=0)
    io._utf8_partial = b"\xe4"  # 中(e4 b8 ad) 慢速首字节（未完成）
    io.set_pending(b"\x96\x87")  # 新字符 文(e6 96 87) 的续字节
    result = io.read_utf8_char(0, 0xe6)
    assert result == "\ufffd文"
    assert io._utf8_partial == b""


def test_read_utf8_char_interrupted_partial_new_incomplete():
    """P2-3：新字符也未读完整——返回 replace 消费的前缀（不丢），新字符首
    字节保留为 partial 待补齐。"""
    io = InputIO(fd=0)
    io._utf8_partial = b"\xe4"
    io.set_pending(b"")  # 无续字节 → select 立即返回无数据
    with patch("src.tui._input_io.select.select", return_value=([], [], [])):
        result = io.read_utf8_char(0, 0xe6)
    assert result == "\ufffd"
    assert io._utf8_partial == b"\xe6"


def test_read_utf8_char_continuation_normal():
    """P2-3 回归：合法续字节路径不受影响（partial + 续字节完整解码）。"""
    io = InputIO(fd=0)
    io._utf8_partial = b"\xe4"
    io.set_pending(b"\xad")  # 中(e4 b8 ad) 的第三个字节（第二个经 first_byte 传入）
    result = io.read_utf8_char(0, 0xb8)
    assert result == "中"
    assert io._utf8_partial == b""


# ═══════════════════════════════════════════════════════════
# P2-4：read_utf8_char 的 fd 参数透传（不再忽略）
# ═══════════════════════════════════════════════════════════

def test_read_with_timeout_uses_passed_fd():
    """P2-4：read_with_timeout 新增 fd 参数——select/os.read 使用透传 fd
    （而非 self._fd）。"""
    io = InputIO(fd=0)
    with patch("src.tui._input_io.select.select", return_value=([99], [], [])) as m_sel, \
         patch("src.tui._input_io.os.read", return_value=b"\xad") as m_read:
        result = io.read_with_timeout(0.05, fd=99)
    assert result == b"\xad"
    m_sel.assert_called_once_with([99], [], [], 0.05)
    m_read.assert_called_once_with(99, 1)


def test_read_utf8_char_passes_fd_to_read_with_timeout():
    """P2-4：read_utf8_char 的 fd 参数经 read_with_timeout 透传（不再忽略）。"""
    io = InputIO(fd=0)
    io.set_pending(b"\xb8\xad")
    captured: dict = {}
    orig = io.read_with_timeout

    def spy(timeout, fd=None):
        captured["fd"] = fd
        return orig(timeout, fd)

    io.read_with_timeout = spy
    result = io.read_utf8_char(99, 0xe4)
    assert result == "中"
    assert captured["fd"] == 99


# ═══════════════════════════════════════════════════════════
# P2-5：CSI u Ctrl+方向键映射
# ═══════════════════════════════════════════════════════════

@pytest.mark.parametrize("keycode,kind", [
    (57417, "arrow_up"),
    (57418, "arrow_down"),
    (57419, "arrow_left"),
    (57420, "arrow_right"),
])
def test_csi_u_ctrl_arrow_mapping(keycode, kind):
    """P2-5：kitty/wezterm 增强键盘协议 Ctrl+方向键（\\x1b[<keycode>;5u）→
    方向键 modifier=5（词跳转语义）——修复前落入 csi_u no-op。"""
    ev = InputParser._dispatch_csi([keycode, 5], "u")
    assert ev.kind == kind
    assert ev.modifier == 5
    assert ev.keycode == keycode


def test_csi_u_plain_arrow_regression():
    """P2-5 回归：无修饰方向键（modifier=1）映射不受影响。"""
    ev = InputParser._dispatch_csi([57417, 1], "u")
    assert ev.kind == "arrow_up"
    assert ev.modifier == 1


# ═══════════════════════════════════════════════════════════
# P2-6：ESC+UTF-8 高位字节（Alt+中文）生成 alt_char
# ═══════════════════════════════════════════════════════════

def test_esc_utf8_high_byte_alt_char():
    """P2-6：ESC 后跟 UTF-8 多字节首字节（Alt+中文）→ 继续读完整 UTF-8 字符
    生成 alt_char 事件（修复前静默丢弃为 unknown）。"""
    io = InputIO(fd=0)
    io.set_pending(b"\xe4\xb8\xad")  # 中
    parser = InputParser(io=io)
    ev = parser._parse_escape_sequence(0)
    assert ev.kind == "alt_char"
    assert ev.char == "中"
    assert ev.modifier == 3
    assert ev.raw == b"\x1b\xe4\xb8\xad"


def test_esc_utf8_high_byte_alt_char_io_none_fallback():
    """P2-6：io 未注入（直接构造）回退 select+os.read 单次续读同样生成
    alt_char（不误触发中断）。"""
    parser = InputParser(io=None)
    with patch(
        "src.tui._input_parser.select.select",
        side_effect=iter([([0], [], []), ([0], [], []), ([0], [], [])]),
    ), patch(
        "src.tui._input_parser.os.read",
        side_effect=[b"\xe4", b"\xb8", b"\xad"],
    ):
        ev = parser._parse_escape_sequence(0)
    assert ev.kind == "alt_char"
    assert ev.char == "中"
    assert ev.modifier == 3


def test_esc_plain_alt_char_regression():
    """P2-6 回归：ESC+可打印 ASCII（Alt+B）保持既有 alt_char 行为。"""
    io = InputIO(fd=0)
    io.set_pending(b"B")
    parser = InputParser(io=io)
    ev = parser._parse_escape_sequence(0)
    assert ev.kind == "alt_char"
    assert ev.char == "B"
    assert ev.modifier == 3


# ═══════════════════════════════════════════════════════════
# P2-7：unknown 事件完整捕获
# ═══════════════════════════════════════════════════════════

def test_unknown_event_full_raw_capture():
    """P2-7：unknown 事件捕获完整 raw（extend(event.raw)）——修复前仅捕获
    event.raw[0] 首字节，多字节 unknown 后续字节丢失。"""
    io = InputIO(fd=0)
    be = InputBufferEditor(history_file=Path("unused"))
    parser = InputParser(io=io)
    disp = InputDispatcher(io=io, buffer_editor=be, parser=parser)
    disp._dispatch_key_event(KeyEvent(kind="unknown", raw=b"\x1b[1@"))
    assert disp.drain_captured() == "\x1b[1@"


# ═══════════════════════════════════════════════════════════
# P2-8：_handle_special_key 的 _enter 注入 append_history
# ═══════════════════════════════════════════════════════════

def test_handle_special_key_enter_injects_append_history():
    """P2-8：_handle_special_key（editmsg/retry 提交）的 _enter 注入
    append_history——注入回调被调用（与 Input._enter 行为一致）。"""
    io = InputIO(fd=0)
    be = InputBufferEditor(history_file=Path("unused"))
    parser = InputParser(io=io)
    disp = InputDispatcher(io=io, buffer_editor=be, parser=parser)
    calls: list[str] = []
    disp.set_enter_append_history(lambda text: calls.append(text))
    disp.set_special_key_callback(lambda action, text: "/editmsg")
    be.set_buffer("prefix")
    disp._handle_special_key("editmsg")
    assert calls == ["/editmsg"]
    assert be.get_queued_input() == "/editmsg"


# ═══════════════════════════════════════════════════════════
# P2-9：_HistoryDiskWriter.submit 同步写盘在锁外执行
# ═══════════════════════════════════════════════════════════

def test_submit_sync_write_not_holding_lock():
    """P2-9：降级路径（线程未启动）的同步写盘在 _submit_lock 锁外执行——
    append 执行期间锁应可获取（修复前持锁做 fsync，render 线程被阻塞）。"""
    writer = _HistoryDiskWriter.__new__(_HistoryDiskWriter)
    writer._queue = queue.Queue(maxsize=writer._MAX_PENDING)
    writer._submit_lock = threading.Lock()
    writer._sentinel_count = 0
    writer._thread = None  # 降级路径（线程未启动）

    entered = threading.Event()
    release = threading.Event()

    class _SlowIO:
        def append(self, text):
            entered.set()
            assert release.wait(2)
            return True

    t = threading.Thread(target=writer.submit, args=(_SlowIO(), "x"))
    t.start()
    assert entered.wait(1), "同步写盘未开始"
    # append 执行期间 _submit_lock 应可获取（同步写盘在锁外）
    acquired = writer._submit_lock.acquire(timeout=0.2)
    assert acquired, "同步写盘持锁执行（P2-9 未修复）"
    writer._submit_lock.release()
    release.set()
    t.join(2)
    assert not t.is_alive()


# ═══════════════════════════════════════════════════════════
# P2-10：_wrap_by_width 首字符超宽不丢字
# ═══════════════════════════════════════════════════════════

def test_wrap_by_width_overwide_char_placeholder_preserved():
    """P2-10：混合内容（"a가b", max_width=1）中超宽字符以零宽占位保留——
    字符计数不丢（光标映射不丢位）；行宽不变量保持。"""
    lines = _wrap_by_width("a가b", 1)
    assert len("".join(lines)) == len("a가b") == 3
    for ln in lines:
        assert wcswidth_simple(ln) <= 1, f"{ln!r} 宽 {wcswidth_simple(ln)} > 1"


def test_wrap_by_width_trailing_overwide_char_placeholder_preserved():
    """P2-10：超宽字符位于段尾（"a가", max_width=1）同样保留占位（字符计数
    不丢）——修复前该字符被静默丢弃，换行结果拼接不回原文本。"""
    lines = _wrap_by_width("a가", 1)
    assert len("".join(lines)) == len("a가") == 2
    for ln in lines:
        assert wcswidth_simple(ln) <= 1, f"{ln!r} 宽 {wcswidth_simple(ln)} > 1"


def test_wrap_by_width_all_overwide_keeps_empty_segment():
    """P2-10 边界：全部字符均超宽（"가나", max_width=1）保持 L1 空段语义
    （调用方 ``or [""]`` 兜底），不破坏既有回归断言。"""
    assert _wrap_by_width("가나", 1) == [""]


def test_wrap_by_width_cjk_fits_regression():
    """P2-10 回归：正常可容纳的 CJK 拆分不受影响。"""
    assert _wrap_by_width("가나", 2) == ["가", "나"]
