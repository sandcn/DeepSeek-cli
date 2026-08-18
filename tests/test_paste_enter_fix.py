"""editmsg「很多上文时按回车不能编辑对应消息」根因修复回归测试。

★ 背景（2026-08-19 bug，粘贴路径 Enter 吞噬）：

渲染线程忙（大量上文一帧 100ms~1s）时，用户的打字与 Enter 在同一次批量
``os.read`` 中累积（stdin 缓冲合并）。``InputDispatcher._dispatch_byte`` 的
可打印字符路径调用 ``InputIO.try_read_paste`` 检测粘贴——突发字节流（含
Enter 键的 ``\\r`` / ``\\n`` / ``\\r\\n``）被整体判定为「粘贴文本」：
``handle_chars`` 把 CR replace 过滤、LF 留在缓冲 → **enter 事件丢失**。

用户可见症状（/editmsg 场景）：
  - 输入 ``/editmsg`` 后按回车 → 无反应（Enter 被吞）；
  - 再按一次回车 → 提交的命令文本带 ``\\n``（``'/editmsg\\n'``）→ 命令
    不匹配 →「未编辑任何消息，已取消」——很多上文时**大概率**复现；
  - prefill 注入后按回车提交编辑消息同样被吞。

修复（``InputIO.try_read_paste`` 末尾 Enter 字节剥离，与既有 ESC 回写
同模式）：
  - 末尾 ``\\r`` 总是剥离（单独 CR 几乎必是 Enter 键；粘贴文本的 CR 伴随
    ``\\n`` 且 ``handle_chars`` 本就过滤，剥离无行为差异）；
  - 末尾 ``\\n`` / ``\\r\\n`` 仅当剩余部分无换行时剥离（多行粘贴保持原
    行为——整段进缓冲不自动提交）；
  - 剥离字节 ``prepend_pending`` 回写，由后续 ``read_stdin_once`` 正常
    分发为 enter 事件（衔接 ``_mark_enter_residual`` 的 CR+LF 残留丢弃）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from src.tui._input_io import InputIO
from src.tui._input_buffer import InputBufferEditor
from src.tui._input_parser import InputParser
from src.tui._input_dispatcher import InputDispatcher


def _make_dispatcher(pipe_r: int):
    """构造真实 InputDispatcher（pipe fd 模拟 stdin）。"""
    io = InputIO(pipe_r)
    be = InputBufferEditor(Path("/dev/null"))
    parser = InputParser(io=io)
    return InputDispatcher(io, be, parser), be


def _drain_all(dispatcher: InputDispatcher, rounds: int = 6, gap: float = 0.03):
    """模拟渲染线程忙一帧后统一 process_events（含后续 pending 轮次）。"""
    for _ in range(rounds):
        time.sleep(gap)
        dispatcher.process_events()


# ═══════════════════════════════════════════════════════════
# 场景 1：/editmsg + Enter 同批 read（很多上文时 stdin 累积）
# ═══════════════════════════════════════════════════════════

def test_cmd_crlf_same_batch_enter_not_swallowed():
    """`/editmsg\r\n` 同批到达 → Enter 产生 enter 事件提交 '/editmsg'，
    命令文本不带 '\n'（修复前 queued=None、缓冲='/editmsg\n'）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"/editmsg\r\n")  # 一次 write 模拟同批 os.read 累积
    _drain_all(d)

    assert be.get_queued_input() == "/editmsg"
    assert be.get_current_text() == ""
    os.close(w)
    os.close(r)


def test_cmd_lf_same_batch_enter_not_swallowed():
    """ICRNL 终端（Enter 读到 \\n）：`/editmsg\\n` 同批 → 正常提交。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"/editmsg\n")
    _drain_all(d)

    assert be.get_queued_input() == "/editmsg"
    os.close(w)
    os.close(r)


def test_single_char_cr_same_batch_enter_not_swallowed():
    """快速打字：`a\\r` 两字节同批 → 'a' 入缓冲 + Enter 提交 'a'。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"a\r")
    _drain_all(d)

    assert be.get_queued_input() == "a"
    assert be.get_current_text() == ""
    os.close(w)
    os.close(r)


def test_trailing_crlf_residual_lf_dropped_after_submit():
    """剥离的 `\\r\\n` 回写后：CR 提交 + LF 作为残留被丢弃
    （不产生第二次 enter——prefill 不被误提交重发）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"/editmsg\r\n")
    _drain_all(d)

    assert be.get_queued_input() == "/editmsg"
    # 残留 LF 已被丢弃：无第二次提交挂起
    assert be.has_queued_input() is False
    os.close(w)
    os.close(r)


# ═══════════════════════════════════════════════════════════
# 场景 2：多行粘贴行为保持（不自动提交）
# ═══════════════════════════════════════════════════════════

def test_multiline_paste_lf_not_autosubmitted():
    """Linux 多行粘贴（中间 \\n）→ 整段进缓冲，不触发 enter 提交（原行为）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"line1\nline2\n")
    _drain_all(d)

    assert be.get_queued_input() is None
    assert be.get_current_text() == "line1\nline2\n"
    os.close(w)
    os.close(r)


def test_multiline_paste_crlf_not_autosubmitted():
    """Windows 多行粘贴（中间 \\r\\n）→ CR 过滤 + LF 保留进缓冲，不提交。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"line1\r\nline2\r\n")
    _drain_all(d)

    assert be.get_queued_input() is None
    assert be.get_current_text() == "line1\nline2\n"
    os.close(w)
    os.close(r)


def test_singleline_paste_without_newline_unchanged():
    """单行粘贴（无换行）→ 原行为保持（整段进缓冲等手动 Enter）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"hello world pasted")
    _drain_all(d)

    assert be.get_queued_input() is None
    assert be.get_current_text() == "hello world pasted"
    os.close(w)
    os.close(r)


def test_ime_multibyte_enter_not_swallowed():
    """IME 上屏（多字节 UTF-8）+ Enter 同批 → 文本入缓冲 + Enter 提交
    （多字节路径同样调用 try_read_paste，剥离同样生效）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    payload = "你好世界".encode("utf-8") + b"\r\n"
    os.write(w, payload)
    _drain_all(d)

    assert be.get_queued_input() == "你好世界"
    assert be.get_current_text() == ""
    os.close(w)
    os.close(r)


# ═══════════════════════════════════════════════════════════
# 场景 3：try_read_paste 单元级（末尾 Enter 剥离规则）
# ═══════════════════════════════════════════════════════════

def test_paste_tail_cr_stripped_to_pending():
    """末尾单独 CR → 剥离回写 pending（无论 body 是否含换行）。"""
    r, w = os.pipe()
    io = InputIO(r)
    os.write(w, b"cmd\r")
    time.sleep(0.05)
    # 模拟 read_stdin_once 已批量读入：直接灌 pending 后走 paste 路径
    raw = os.read(r, 4096)
    io.set_pending(raw[1:])
    result = io.try_read_paste(r, chr(raw[0]))

    assert result == "cmd"
    assert io.drain_pending() == b"\r"
    os.close(w)
    os.close(r)


def test_paste_tail_lf_with_inner_newline_kept():
    """末尾 LF 但 body 含换行（多行粘贴）→ 不剥离（整段返回粘贴文本）。"""
    r, w = os.pipe()
    io = InputIO(r)
    payload = b"line1\nline2\n"
    os.write(w, payload)
    time.sleep(0.05)
    raw = os.read(r, 4096)
    io.set_pending(raw[1:])
    result = io.try_read_paste(r, chr(raw[0]))

    assert result == "line1\nline2\n"
    assert io.drain_pending() == b""
    os.close(w)
    os.close(r)


def test_paste_only_enter_tail_returns_first_char():
    """extra 仅剩 Enter 字节（打字字符 + Enter 紧邻）→ 剥离后返回首字符，
    Enter 字节留在 pending 待正常分发。"""
    r, w = os.pipe()
    io = InputIO(r)
    os.write(w, b"x\n")
    time.sleep(0.05)
    raw = os.read(r, 4096)
    io.set_pending(raw[1:])
    result = io.try_read_paste(r, chr(raw[0]))

    assert result == "x"
    assert io.drain_pending() == b"\n"
    os.close(w)
    os.close(r)


def test_paste_no_newline_untouched():
    """无换行粘贴流 → 剥离逻辑不介入（原行为零变化）。"""
    r, w = os.pipe()
    io = InputIO(r)
    payload = b"plain text paste only"
    os.write(w, payload)
    time.sleep(0.05)
    raw = os.read(r, 4096)
    io.set_pending(raw[1:])
    result = io.try_read_paste(r, chr(raw[0]))

    assert result == "plain text paste only"
    assert io.drain_pending() == b""
    os.close(w)
    os.close(r)


# ═══════════════════════════════════════════════════════════
# 场景 4：短突发降级路径（≤2 可打印字符）不回归
# ═══════════════════════════════════════════════════════════

def test_short_burst_printable_still_degraded():
    """≤2 字节纯 ASCII 可打印突发 → 降级逐字节（2026-08-18 修复不回归）。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"ab")
    _drain_all(d)

    assert be.get_queued_input() is None
    assert be.get_current_text() == "ab"
    os.close(w)
    os.close(r)


def test_short_burst_with_cr_enter_submitted():
    """2 字节含 CR（`a\\r`）→ 非可打印突发不降级 → 剥离 CR → Enter 提交。"""
    r, w = os.pipe()
    d, be = _make_dispatcher(r)
    os.write(w, b"a\r")
    _drain_all(d, rounds=4)

    assert be.get_queued_input() == "a"
    os.close(w)
    os.close(r)
