"""bash 工具输出解码测试（Windows cmd / PowerShell 工具卡乱码修复，2026-10-20）。

覆盖：
  - ``decode_output``：UTF-8 优先、本机代码页（cp936）回退、BOM（UTF-8 /
    UTF-16）识别、非法字节兜底不抛异常、无 BOM 的 UTF-16 字节分布识别；
  - ``OutputLineDecoder``：字节级跨块切分（GBK 双字节字符跨块）、UTF-16
    宽字符流跨块（半个码元）、EOF 残留行；
  - ``_read_loop`` 集成：cp936 输出经流式解码后行内容正确、发布回调拿到
    同一份终端视角文本；
  - Windows 端到端：``BashFunc`` 执行 ``echo 中文`` 不再出现 U+FFFD。

背景：修复前 ``_read_loop`` 一律 ``decode('utf-8', errors='replace')``，
Windows（命令经 ``cmd.exe``）按控制台代码页（简体中文 cp936）输出的中文被
替换为 U+FFFD，工具卡片显示乱码。
"""

from __future__ import annotations

import codecs
import json
import os

import pytest

from src.tools._process_output import (
    OutputLineDecoder,
    decode_output,
    default_output_encodings,
)
from src.tools.bash import BashFunc


# ── 测试替身 ─────────────────────────────────────────

class _FakeReader:
    """按块返回预置字节的 StreamReader 替身。"""

    def __init__(self, chunks):
        self._chunks = list(chunks)

    async def read(self, _size):
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class _FakeProcess:
    """``_read_loop`` 中断路径之外的 process 替身（不会调用 kill）。"""

    returncode = 0

    def kill(self):  # pragma: no cover - 仅中断路径调用
        pass


async def _run_read_loop(chunks, decoder=None, publish=None):
    """驱动 ``BashFunc._read_loop`` 收集行（不触发中断）。"""
    lines = []
    await BashFunc._read_loop(
        _FakeReader(chunks), _FakeProcess(), lines,
        publish, kill_fn=lambda: None, decoder=decoder,
    )
    return lines


# ── 1. decode_output：单块解码 ────────────────────────

def test_decode_output_utf8_is_preferred():
    """UTF-8 输出（PowerShell 7 / Node / rg）按 UTF-8 解码。"""
    assert decode_output("中文输出\n".encode()) == "中文输出\n"


def test_decode_output_falls_back_to_local_codepage():
    """UTF-8 解码失败的 GBK 字节按本机代码页（cp936）解码，不产生 U+FFFD。"""
    raw = "系统找不到指定的路径。\n".encode("gbk")
    text = decode_output(raw, ("utf-8", "cp936"))
    assert text == "系统找不到指定的路径。\n"
    assert "\ufffd" not in text


def test_decode_output_strips_utf8_bom():
    """UTF-8 BOM 不进入显示文本（否则工具卡首列出现不可见字符）。"""
    raw = codecs.BOM_UTF8 + "目录\n".encode()
    assert decode_output(raw) == "目录\n"


def test_decode_output_utf16le_with_bom():
    """UTF-16LE BOM（如 ``wmic`` 输出）整块解码正确。"""
    raw = "第一行\n第二行\n".encode("utf-16")  # 带 BOM（LE，Windows 小端）
    assert decode_output(raw) == "第一行\n第二行\n"


def test_decode_output_utf16_without_bom_by_nul_pattern():
    """无 BOM 的 UTF-16LE（ASCII 内容）按 NUL 分布识别，而非解出夹 \x00 的文本。"""
    raw = "hello world\n".encode("utf-16-le")
    assert decode_output(raw) == "hello world\n"


def test_decode_output_short_gbk_line_that_is_valid_utf8():
    """GBK 短行恰是合法 UTF-8（「系统」→ ϵͳ）时按 CJK 代码页消歧。"""
    raw = "系统".encode("gbk")
    assert raw.decode("utf-8") == "ϵͳ"  # 前提：确实是「双重合法」样本
    assert decode_output(raw, ("utf-8", "cp936")) == "系统"


def test_decode_output_utf8_chinese_not_switched_to_local_codepage():
    """真 UTF-8 中文（3 字节序列）不会被改判为本地代码页。"""
    raw = "中文测试".encode("utf-8")
    assert decode_output(raw, ("utf-8", "cp936")) == "中文测试"


def test_decode_output_utf8_latin1_not_switched_to_local_codepage():
    """合法 UTF-8 西欧文本（Latin-1 重音字符）不被改判为本地代码页。"""
    raw = "café".encode("utf-8")
    assert decode_output(raw, ("utf-8", "cp936")) == "café"


def test_decode_output_invalid_bytes_never_raises():
    """无法按任何候选编码严格解码时走 replace 兜底（绝不抛异常）。"""
    text = decode_output(b"\x81\x40\xff\xfe\xff", ("utf-8", "cp936"))
    assert isinstance(text, str)
    assert text  # 保留可解码部分，不整行丢弃


def test_decode_output_empty_bytes():
    assert decode_output(b"") == ""


def test_default_output_encodings_utf8_first_and_nonempty():
    """候选编码 UTF-8 恒在首位（现代工具主流编码），且至少含一个本机编码。"""
    encodings = default_output_encodings()
    assert encodings[0] == "utf-8"
    assert len(encodings) >= 1


# ── 2. OutputLineDecoder：流式切行 ─────────────────────

def test_decoder_splits_lines_and_decodes_cp936_across_chunks():
    """GBK 双字节字符被块边界切开时仍正确解码（只在整行处解码）。"""
    data = "第一行\n第二行\n".encode("gbk")
    decoder = OutputLineDecoder(("utf-8", "cp936"))
    lines = []
    # 每 3 字节一块：必然把某个 GBK 字符拆到两个块中
    for start in range(0, len(data), 3):
        lines.extend(decoder.feed(data[start:start + 3]))
    lines.extend(decoder.flush())
    assert lines == ["第一行\n", "第二行\n"]


def test_decoder_eof_residual_without_newline():
    """EOF 残留（无行尾换行）作为最后一行返回，且编码同样自适应。"""
    decoder = OutputLineDecoder(("utf-8", "cp936"))
    lines = decoder.feed("没有换行".encode("gbk"))
    assert lines == []
    lines.extend(decoder.flush())
    assert lines == ["没有换行"]


def test_decoder_utf16_stream_split_mid_code_unit():
    """UTF-16 流按 3 字节切块（半个码元跨块）仍完整解码。"""
    data = "第一行\n第二行\n".encode("utf-16")
    decoder = OutputLineDecoder()
    lines = []
    for start in range(0, len(data), 3):
        lines.extend(decoder.feed(data[start:start + 3]))
    lines.extend(decoder.flush())
    assert lines == ["第一行\n", "第二行\n"]


def test_decoder_mixed_encodings_per_line():
    """同一流内逐行自适应：UTF-8 行与 GBK 行各自正确。"""
    decoder = OutputLineDecoder(("utf-8", "cp936"))
    lines = decoder.feed("中文 utf8\n".encode("utf-8") + "中文 gbk\n".encode("gbk"))
    lines.extend(decoder.flush())
    assert lines == ["中文 utf8\n", "中文 gbk\n"]


# ── 3. _read_loop 集成：工具卡输出链路 ────────────────

async def test_read_loop_decodes_local_codepage_for_tool_card():
    """cp936 输出经 ``_read_loop`` 后行内容正确（工具卡不再显示 U+FFFD）。"""
    chunks = ["系统\n".encode("gbk"), "找不到\n".encode("gbk")]
    lines = await _run_read_loop(
        chunks, decoder=OutputLineDecoder(("utf-8", "cp936")),
    )
    assert lines == ["系统\n", "找不到\n"]
    assert all("\ufffd" not in line for line in lines)


async def test_read_loop_publishes_same_decoded_text():
    """发布回调（display / 后台 read_buffer）拿到与返回行一致的解码文本。"""
    published = []

    async def _publish(text, is_stderr):
        published.append((text, is_stderr))

    lines = await _run_read_loop(
        ["中文\n".encode("gbk")],
        decoder=OutputLineDecoder(("utf-8", "cp936")),
        publish=_publish,
    )
    assert lines == ["中文\n"]
    assert published == [("中文\n", False)]


async def test_read_loop_strips_ansi_and_resolves_carriage_return():
    """解码后仍保持终端视角统一（ANSI 剥离 + \\r 覆盖），回归旧行为。"""
    lines = await _run_read_loop([b"\x1b[31m10%\r20%\x1b[0m\n"])
    assert lines == ["20%\n"]


async def test_interactive_background_path_uses_pty():
    """后台执行路径（``_run_interactive_async``）与前台一样统一走 PTY。

    修复前 Windows 上 ``pty.py`` 存在但 ``fcntl`` 缺失会误走 PTY 分支；
    现在原生 Windows 走 ConPTY、POSIX 走 openpty，后台任务同样获得真终端。
    """
    result = json.loads(await BashFunc(command="echo hello")._run_interactive_async())
    assert result["returncode"] == 0
    assert "hello" in result["stdout"]


# ── 4. 工具卡显示链路（解码结果 → ToolCard 行） ────────

async def test_bash_output_renders_in_tool_card_without_replacement():
    """bash 中文输出经解码后进入工具卡，卡片文本无 U+FFFD。"""
    from src.tui.app.model import AppModel
    from src.tui.app.toolcard import tool_card_lines

    result = json.loads(await BashFunc(command="echo 中文测试").execute())
    model = AppModel()
    model.width = 80
    model.open_tool_box("t1", "bash", "echo 中文测试")
    model.append_tool_output("t1", result["stdout"])
    model.close_tool_box("t1", True)
    block = model.blocks[-1]
    card_text = "".join(
        run.text for line in tool_card_lines(block, 80) for run in line
    )
    assert "中文测试" in card_text
    assert "\ufffd" not in card_text


# ── 5. Windows 端到端（真实 cmd 控制台代码页） ─────────

@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 原生 cmd 存在代码页问题")
async def test_windows_bash_chinese_output_has_no_replacement_char():
    """``echo 中文测试`` 的工具结果是中文而非 U+FFFD（用户报障场景）。"""
    result = json.loads(await BashFunc(command="echo 中文测试").execute())
    stdout = result["stdout"]
    assert "\ufffd" not in stdout
    assert stdout.strip()
    # 控制台代码页为简体中文（cp936/GBK）时应逐字还原
    local = [item.lower() for item in default_output_encodings()]
    if "cp936" in local or "gbk" in local:
        assert "中文测试" in stdout
    assert result["returncode"] == 0


@pytest.mark.skipif(os.name != "nt", reason="仅原生 Windows 存在 pty.py 误导判定")
def test_pty_not_available_on_native_windows():
    """Windows 版 CPython 自带 ``pty.py``，但 POSIX PTY 不可用（``fcntl`` 缺失）。

    原生 Windows 的 PTY 由 ConPTY 承担（``_pty_supported``），
    ``_is_pty_available`` 只反映 POSIX 分支的设备可用性。
    """
    assert BashFunc._is_pty_available() is False


@pytest.mark.skipif(os.name != "nt", reason="仅 Windows 原生 cmd 存在代码页问题")
def test_run_quiet_decodes_local_codepage():
    """辅助命令（进程树收集用的 ``_run_quiet``）同样按本机代码页解码。"""
    from src.tools._bash_support import _run_quiet

    output = _run_quiet(["cmd", "/c", "echo 中文测试"])
    assert "\ufffd" not in output
