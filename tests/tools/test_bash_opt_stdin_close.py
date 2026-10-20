"""bash_opt：op=close_stdin（发送 EOF）/ stdin 编码 / read 行数上限 回归测试。

覆盖以下新增能力：

1. ``op=close_stdin``：pipe 模式关闭 stdin 写端（等 wait_closed、置
   ``stdin_closed``、清空 ``stdin_writer``）；pty 模式写 EOT（0x04），
   只记录 ``eot_sent`` 而**不关闭通道**（PTY 没有真正的 EOF，之后仍可
   继续 stdin / keys）；重复调用 / 未就绪 / 无写端 / 关闭后继续写 stdin
   都有可读提示；该 op 接管任务生命周期（与 stdin 一致）。
2. ``op=stdin`` 的 ``encoding``：缺省 **utf-8**（跟随 bash 工具为子进程
   统一设置的 stdio 编码：包装脚本设置 Console Input/OutputEncoding 与
   PYTHONIOENCODING / PYTHONUTF8，Python 等按 UTF-8 读 stdin——按 GBK 写
   中文会直接 UnicodeDecodeError）；显式编码（如 ``gbk``）按指定值写出
   字节（含换行）；未知编码 / 不可编码字符返回可读错误且不写字节。
3. ``op=read`` 的 ``max_lines``：默认按 1000 行截断（保留尾部）、显式值
   生效、0 关闭行数限制、非法值回退默认，并回报 total_lines /
   returned_lines / dropped_lines。
"""

from __future__ import annotations

import asyncio
import json
import os

from src.tools import bash_opt as bash_opt_module
from src.tools.bash_opt import BashOptFunc


class _FakeAgent:
    """最小 Agent 桩：只提供 bash 后台任务表。"""

    def __init__(self, records=None):
        self._background_tasks = dict(records or {})
        self._subagent_tasks = {}

    def _remove_background_task(self, task_id):
        return self._background_tasks.pop(task_id, None)


class _FakeStdin:
    """asyncio StreamWriter 替身：记录写入 / 关闭顺序。"""

    def __init__(self):
        self.written = []
        self.closed = False
        self.wait_closed_called = False

    def write(self, data):
        self.written.append(data)

    async def drain(self):
        return None

    def close(self):
        self.closed = True

    async def wait_closed(self):
        self.wait_closed_called = True


def _record(mode="pipe", **extra):
    rec = {
        "read_buffer": "",
        "read_buffer_dropped": 0,
        "status": "running",
        "done": False,
        "pid": 4321,
        "task": None,
        "mode": mode,
        "io_lock": asyncio.Lock(),
    }
    rec.update(extra)
    return rec


def _call(agent, task_id="bg-1", **kwargs):
    func = BashOptFunc(task_id=task_id, **kwargs)
    func.set_agent(agent)
    return func.execute()


def _read_rec(text: str) -> dict:
    return {
        "read_buffer": text,
        "read_buffer_dropped": 0,
        "status": "running",
        "done": False,
        "io_lock": asyncio.Lock(),
    }


# ═══════════════════════════════════════════════════════════
# 1. op=close_stdin
# ═══════════════════════════════════════════════════════════

async def test_close_stdin_closes_pipe_writer():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="close_stdin")

    assert stdin.closed is True
    assert stdin.wait_closed_called is True
    assert rec["stdin_closed"] is True
    assert rec["stdin_writer"] is None
    assert "EOF" in result


async def test_close_stdin_takes_over_task():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="close_stdin")

    assert rec.get("managed_by_tool") is True


async def test_close_stdin_twice_is_reported():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="close_stdin")
    again = await _call(agent, op="close_stdin")

    assert again.startswith("(")
    assert "已关闭" in again
    assert "无需重复" in again


async def test_close_stdin_without_mode_is_reported():
    agent = _FakeAgent({"bg-1": _record(mode=None)})

    result = await _call(agent, op="close_stdin")

    assert result.startswith("(")
    assert "尚未就绪" in result


async def test_close_stdin_without_stdin_writer_is_reported():
    agent = _FakeAgent({"bg-1": _record(mode="pipe")})

    result = await _call(agent, op="close_stdin")

    assert result.startswith("(")
    assert "无 stdin 管道" in result


async def test_close_stdin_pty_writes_eot(monkeypatch):
    written = {}

    async def _fake_write(fd, data, **kwargs):
        written["fd"] = fd
        written["data"] = data

    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    rec = _record(mode="pty", master_fd=7)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="close_stdin")

    # Windows ConPTY 的 EOF 是 Ctrl+Z（\x1a），POSIX PTY 是 Ctrl+D（\x04）
    expected = b"\x1a" if os.name == "nt" else b"\x04"
    assert written == {"fd": 7, "data": expected}
    # ★ PTY 没有真正的 EOF：只记录「已发送 EOT」，**不关闭** stdin 通道
    #   （修复前置 stdin_closed=True，之后 op=stdin / op=keys 全部被拒）。
    assert rec.get("eot_sent") is True
    assert not rec.get("stdin_closed")
    assert "EOT" in result


async def test_close_stdin_pty_reports_no_real_eof(monkeypatch):
    """PTY 没有真正的 EOF：提示必须说明「程序不一定退出」并给 kill 兜底。

    实测（Windows 11 26220 ConPTY）：python REPL 收到 EOT 后仍在运行，
    调用方若以为 close_stdin 已结束任务会一直 wait 到超时。
    """

    async def _fake_write(fd, data, **kwargs):
        return None

    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    agent = _FakeAgent({"bg-1": _record(mode="pty", master_fd=7)})

    result = await _call(agent, op="close_stdin")

    assert "没有真正的 EOF" in result
    assert "op=kill" in result
    assert "非 PTY" in result


async def test_stdin_pty_uses_carriage_return(monkeypatch):
    """PTY 任务的「回车」必须是 CR（\\r）：终端里 \\n 不提交输入行。"""
    written = {}

    async def _fake_write(fd, data, **kwargs):
        written["data"] = data

    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    agent = _FakeAgent({"bg-1": _record(mode="pty", master_fd=7)})

    await _call(agent, op="stdin", text="hi")

    assert written["data"] == b"hi\r"


async def test_stdin_pipe_keeps_line_feed(monkeypatch):
    """管道模式仍用 \\n（回归保护：不能把管道也改成 CR）。"""
    stdin = _FakeStdin()
    agent = _FakeAgent({"bg-1": _record(mode="pipe", stdin_writer=stdin)})

    await _call(agent, op="stdin", text="hi")

    assert stdin.written == [b"hi\n"]


async def test_close_stdin_pty_keeps_channel_open(monkeypatch):
    """PTY：close_stdin 之后仍可继续写 stdin（PTY 没有真正的 EOF）。

    修复前 ``close_stdin`` 在 PTY 上直接置 ``stdin_closed=True``：写一次
    Ctrl+D 就把交互通道永久锁死，后续 ``op=stdin`` / ``op=keys`` 全部被拒。
    EOT 只是一次按键，通道必须保持可用。
    """
    written = []

    async def _fake_write(fd, data, **kwargs):
        written.append(bytes(data))

    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    rec = _record(mode="pty", master_fd=7)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="close_stdin")
    result = await _call(agent, op="stdin", text="still-alive")

    assert "still-alive" in result
    assert "无法再写入" not in result
    assert written[-1] == b"still-alive\r"
    # 再次 close_stdin：提示已发送过，但不应报错 / 不应锁死
    again = await _call(agent, op="close_stdin")
    assert "发送过 EOT" in again
    assert rec.get("stdin_closed") is None


async def test_stdin_pty_registers_echo_filter(monkeypatch):
    """Windows ConPTY 下写入的文本行登记到回显过滤队列（读取端去重）。"""
    async def _fake_write(fd, data, **kwargs):
        return None

    class _FakeOs:
        name = "nt"

    monkeypatch.setattr(bash_opt_module, "os", _FakeOs)
    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    rec = _record(mode="pty", master_fd=7, echo_filter={"lines": []})
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="stdin", text="echo hi")

    assert rec["echo_filter"]["lines"] == ["echo hi"]


async def test_stdin_posix_pty_does_not_register_echo_filter(monkeypatch):
    """POSIX PTY 已关闭 ECHO：不得登记回显（否则会误删程序真实输出）。"""
    async def _fake_write(fd, data, **kwargs):
        return None

    class _FakeOs:
        name = "posix"

    monkeypatch.setattr(bash_opt_module, "os", _FakeOs)
    monkeypatch.setattr(bash_opt_module, "_write_pty_bytes", _fake_write)
    rec = _record(mode="pty", master_fd=7, echo_filter={"lines": []})
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="stdin", text="echo hi")

    assert rec["echo_filter"]["lines"] == []


async def test_stdin_after_close_is_rejected():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="close_stdin")
    result = await _call(agent, op="stdin", text="hi")

    assert result.startswith("(")
    assert "close_stdin" in result
    assert not stdin.written


async def test_keys_after_close_is_rejected():
    """终端通道的 keys 同样不能写到已关闭的 stdin。"""
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="close_stdin")
    result = await _call(agent, op="keys", key="ctrl_c")

    assert result.startswith("(")
    assert "close_stdin" in result


# ═══════════════════════════════════════════════════════════
# 2. op=stdin 的 encoding
# ═══════════════════════════════════════════════════════════

async def test_stdin_gbk_encoding_writes_gbk_bytes():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="stdin", text="中文", encoding="gbk")

    assert stdin.written == ["中文".encode("gbk") + b"\n"]
    assert stdin.written[0].decode("gbk") == "中文\n"
    assert "gbk" in result


async def test_stdin_default_is_utf8_matching_bash_stdio():
    """默认编码跟随 bash 工具的 stdio 约定（UTF-8），与系统 ANSI 代码页无关。

    实测（Windows 11 原生）：bash 包装脚本把子进程 ``Console.InputEncoding``
    设为 UTF-8 并设置 ``PYTHONUTF8=1``，Python 等按 UTF-8 读 stdin；旧默认
    按 ACP=936 写 GBK，中文直接 ``UnicodeDecodeError``（0xd6 非法 UTF-8）。
    """
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="stdin", text="中文")

    assert stdin.written == ["中文".encode("utf-8") + b"\n"]
    assert "utf-8" in result


def test_default_stdin_encoding_follows_child_stdio_convention():
    """默认值必须等于 bash 工具给子进程设置的 stdio 编码（单一事实来源）。"""
    from src.tools._process_output import CHILD_STDIO_ENCODING

    assert bash_opt_module.default_stdin_encoding() == CHILD_STDIO_ENCODING
    assert CHILD_STDIO_ENCODING == "utf-8"


async def test_stdin_explicit_encoding_overrides_default():
    """显式 encoding 始终优先（GBK 原生控制台程序 / 指定编码的脚本）。"""
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="stdin", text="中文", encoding="gbk")

    assert stdin.written == ["中文".encode("gbk") + b"\n"]
    assert "gbk" in result


async def test_stdin_encoding_without_newline():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    await _call(agent, op="stdin", text="行", encoding="gb18030", newline=False)

    assert stdin.written == ["行".encode("gb18030")]


async def test_stdin_unknown_encoding_reported():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="stdin", text="hi", encoding="no-such-codec")

    assert result.startswith("(")
    assert "未知编码" in result
    assert not stdin.written


async def test_stdin_unencodable_text_reported():
    stdin = _FakeStdin()
    rec = _record(stdin_writer=stdin)
    agent = _FakeAgent({"bg-1": rec})

    result = await _call(agent, op="stdin", text="😀", encoding="gbk")

    assert result.startswith("(")
    assert "无法按编码" in result
    assert not stdin.written


# ═══════════════════════════════════════════════════════════
# 3. op=read 的 max_lines
# ═══════════════════════════════════════════════════════════

def _lines_payload(count: int) -> str:
    return "".join(f"l{index}\n" for index in range(count))


async def test_read_truncates_to_default_line_limit(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_MAX_LINES_DEFAULT", 3)
    func = BashOptFunc(task_id="bg-1", op="read")

    payload = json.loads(await func._op_read(_read_rec(_lines_payload(10))))

    assert payload["truncated"] is True
    assert payload["total_lines"] == 10
    assert payload["returned_lines"] == 3
    assert payload["dropped_lines"] == 7
    assert payload["max_lines"] == 3
    assert payload["output"] == "l7\nl8\nl9\n"
    assert "1000 行" in payload["hint"]


async def test_read_under_limit_has_no_truncation_fields():
    func = BashOptFunc(task_id="bg-1", op="read", max_lines=5)

    payload = json.loads(await func._op_read(_read_rec(_lines_payload(3))))

    assert payload["total_lines"] == 3
    assert payload["returned_lines"] == 3
    assert payload["max_lines"] == 5
    assert "truncated" not in payload
    assert "hint" not in payload
    assert payload["output"] == _lines_payload(3)


async def test_read_max_lines_zero_disables_line_limit(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_MAX_LINES_DEFAULT", 1)
    func = BashOptFunc(task_id="bg-1", op="read", max_lines=0)

    payload = json.loads(await func._op_read(_read_rec(_lines_payload(6))))

    assert payload["max_lines"] == 0
    assert payload["total_lines"] == 6
    assert payload["returned_lines"] == 6
    assert "truncated" not in payload
    assert payload["output"] == _lines_payload(6)


async def test_read_max_lines_invalid_falls_back_to_default(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_MAX_LINES_DEFAULT", 2)
    func = BashOptFunc(task_id="bg-1", op="read", max_lines="3.5x")

    payload = json.loads(await func._op_read(_read_rec(_lines_payload(5))))

    assert payload["max_lines"] == 2
    assert payload["returned_lines"] == 2
    assert payload["dropped_lines"] == 3


async def test_read_max_lines_keeps_char_limit_after_line_trim(monkeypatch):
    monkeypatch.setattr(BashOptFunc, "_READ_OUTPUT_MAX_CHARS", 4)
    func = BashOptFunc(task_id="bg-1", op="read", max_lines=3)

    payload = json.loads(await func._op_read(_read_rec(_lines_payload(10))))

    assert payload["truncated"] is True
    assert payload["dropped_lines"] == 7
    assert payload["dropped_chars"] == len("l7\nl8\nl9\n") - 4
    assert payload["output"] == "l7\nl8\nl9\n"[-4:]


# ═══════════════════════════════════════════════════════════
# 4. schema / 展示摘要
# ═══════════════════════════════════════════════════════════

def test_schema_exposes_close_stdin_and_new_parameters():
    schema = BashOptFunc.to_tool_schema()
    props = schema["function"]["parameters"]["properties"]

    assert "close_stdin" in props["op"]["enum"]
    assert props["encoding"]["type"] == "string"
    assert props["max_lines"]["type"] == "integer"
    assert "close_stdin" in props["op"]["description"]
    assert "max_lines" in props["op"]["description"]


def test_display_params_close_stdin():
    text = BashOptFunc.display_params({"op": "close_stdin", "task_id": "bg-1"})

    assert "close_stdin" in text
    assert "关闭 stdin" in text


def test_display_params_stdin_encoding():
    text = BashOptFunc.display_params(
        {"op": "stdin", "task_id": "bg-1", "text": "hi", "encoding": "gbk"})

    assert "hi" in text
    assert "encoding=gbk" in text
