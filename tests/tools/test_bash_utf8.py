"""bash 工具 UTF-8 统一（子进程环境 / 输出解码 / 返回大模型）的回归测试。

覆盖 2026-10-26 用户需求「bash.py 的所有命令行输出和返回大模型都转成
UTF-8」：

- 子进程环境注入 ``PYTHONIOENCODING=utf-8`` / ``PYTHONUTF8=1``；
- POSIX / Cygwin 下非 UTF-8 locale 提升到系统可用的 UTF-8 locale，
  已是 UTF-8 / 无可用项时保持原环境；
- ``locale -a`` 的真实名字解析（大小写 / ``-`` / ``_`` 归一化）；
- Windows cmd 回退路径加 ``chcp 65001``；
- 输出行与返回大模型的 JSON 清洗孤立代理项（可无损按 UTF-8 编码），
  且非 ASCII 字符保持原文（``ensure_ascii=False``）。
"""

from __future__ import annotations

import json
import os

import pytest

from src.tools import bash as bash_module
from src.tools.bash import (
    BashFunc,
    _apply_utf8_locale_env,
    _available_utf8_locale,
    _conpty_launch_argv,
    _is_utf8_encoding_name,
    _redact_command,
    _remember_echo_lines,
)

_posix_only = pytest.mark.skipif(
    os.name == "nt", reason="POSIX / Cygwin 的 locale 提升逻辑",
)


def test_is_utf8_encoding_name() -> None:
    assert _is_utf8_encoding_name("UTF-8") is True
    assert _is_utf8_encoding_name("utf8") is True
    assert _is_utf8_encoding_name("zh_CN.utf-8") is True
    assert _is_utf8_encoding_name("C.utf8") is True
    assert _is_utf8_encoding_name("cp936") is False
    assert _is_utf8_encoding_name("ANSI_X3.4-1968") is False
    assert _is_utf8_encoding_name("") is False


def test_child_env_sets_utf8_stdio() -> None:
    """所有平台的子进程 stdio 都注入 UTF-8 变量（Python 子进程受其控制）。"""
    env = BashFunc._get_subprocess_env("bash")
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


@_posix_only
def test_apply_utf8_locale_promotes_legacy_locale(monkeypatch) -> None:
    """当前 locale 非 UTF-8 且存在可用 UTF-8 locale → 子进程环境提升。"""
    monkeypatch.setattr(
        bash_module.locale, "getpreferredencoding", lambda *a, **k: "cp936")
    monkeypatch.setattr(
        bash_module, "_available_utf8_locale", lambda: "C.utf8")
    env = {"LANG": "zh_CN.GBK"}
    _apply_utf8_locale_env(env)
    assert env["LC_ALL"] == "C.utf8"
    assert env["LANG"] == "C.utf8"


@_posix_only
def test_apply_utf8_locale_keeps_existing_utf8(monkeypatch) -> None:
    """已是 UTF-8 locale 时不覆盖用户环境。"""
    monkeypatch.setattr(
        bash_module.locale, "getpreferredencoding", lambda *a, **k: "UTF-8")
    monkeypatch.setattr(
        bash_module, "_available_utf8_locale", lambda: "C.utf8")
    env = {"LANG": "zh_CN.utf-8", "LC_ALL": "zh_CN.utf-8"}
    _apply_utf8_locale_env(env)
    assert env == {"LANG": "zh_CN.utf-8", "LC_ALL": "zh_CN.utf-8"}


@_posix_only
def test_apply_utf8_locale_without_candidate_keeps_env(monkeypatch) -> None:
    """没有可用 UTF-8 locale 时保持原环境（由解码层兜底转成 UTF-8 文本）。"""
    monkeypatch.setattr(
        bash_module.locale, "getpreferredencoding",
        lambda *a, **k: "ANSI_X3.4-1968")
    monkeypatch.setattr(
        bash_module, "_available_utf8_locale", lambda: None)
    env = {"LANG": "C"}
    _apply_utf8_locale_env(env)
    assert env == {"LANG": "C"}


class _Completed:
    """``subprocess.run`` 的最小替身（只需要 stdout）。"""

    def __init__(self, stdout: str) -> None:
        self.stdout = stdout


def test_available_utf8_locale_returns_real_name(monkeypatch) -> None:
    """``locale -a`` 的名字大小写 / 分隔符不一 → 返回系统真实名字。"""
    monkeypatch.setattr(
        bash_module.subprocess, "run",
        lambda *a, **k: _Completed("C\nC.utf8\nzh_CN.utf-8\n"))
    _available_utf8_locale.cache_clear()
    try:
        assert _available_utf8_locale() == "C.utf8"
    finally:
        _available_utf8_locale.cache_clear()


def test_available_utf8_locale_falls_back_to_any_utf8(monkeypatch) -> None:
    """候选都不在时，返回系统里任一 UTF-8 locale。"""
    monkeypatch.setattr(
        bash_module.subprocess, "run",
        lambda *a, **k: _Completed("POSIX\nfr_FR.utf8\n"))
    _available_utf8_locale.cache_clear()
    try:
        assert _available_utf8_locale() == "fr_FR.utf8"
    finally:
        _available_utf8_locale.cache_clear()


def test_available_utf8_locale_none_without_utf8(monkeypatch) -> None:
    """系统无 UTF-8 locale → None（调用方保持原环境）。"""
    monkeypatch.setattr(
        bash_module.subprocess, "run",
        lambda *a, **k: _Completed("C\nPOSIX\n"))
    _available_utf8_locale.cache_clear()
    try:
        assert _available_utf8_locale() is None
    finally:
        _available_utf8_locale.cache_clear()


def test_conpty_cmd_sets_utf8_codepage(monkeypatch) -> None:
    """cmd 回退路径把控制台输出代码页切到 UTF-8，且不污染命令输出。"""
    monkeypatch.setenv("ComSpec", r"C:\Windows\System32\cmd.exe")
    argv = _conpty_launch_argv("echo 中文", "cmd")
    assert argv[0] == r"C:\Windows\System32\cmd.exe"
    assert argv[1] == "/c"
    assert argv[2] == "chcp 65001 >nul & echo 中文"


def test_format_result_is_utf8_safe() -> None:
    """返回大模型的 JSON：中文保持原文，孤立代理项被清洗，整体可 UTF-8 编码。"""
    payload = BashFunc._format_result({
        "stdout": "中文输出 α\n坏字符 \ud83d\n",
        "stderr": "错误 \ude00",
        "returncode": 1,
    })
    # ensure_ascii=False：非 ASCII 以原文出现，不用 \uXXXX 转义
    assert "中文输出" in payload
    assert "\\u4e2d" not in payload
    data = json.loads(payload)
    assert "\ud83d" not in data["stdout"]
    assert "\ude00" not in data["stderr"]
    assert "\ufffd" in data["stdout"]
    payload.encode("utf-8")  # 不抛 UnicodeEncodeError


def test_error_result_is_utf8_safe() -> None:
    payload = BashFunc._error_result("(执行出错: 坏字节 \ud83d)")
    assert "\ud83d" not in payload
    assert "执行出错" in payload
    payload.encode("utf-8")


def test_display_params_and_redact_are_utf8_safe() -> None:
    text = BashFunc.display_params({"command": "echo \ud83d"})
    assert "\ud83d" not in text
    text.encode("utf-8")
    redacted = _redact_command("echo \ud83d --token=secret")
    assert "\ud83d" not in redacted
    assert "secret" not in redacted
    redacted.encode("utf-8")


def test_echo_filter_registration_is_utf8_safe() -> None:
    """输入回显登记与读取端文本形态一致（都清洗孤立代理项）。"""
    echo_filter: dict = {"lines": []}
    _remember_echo_lines(echo_filter, "echo \ud83d")
    assert echo_filter["lines"] == ["echo \ufffd"]
    echo_filter["lines"][0].encode("utf-8")


async def test_read_loop_sanitizes_output_lines() -> None:
    """读循环产出的每一行都可无损按 UTF-8 编码（lines / display / read_buffer）。"""

    class _Decoder:
        """模拟解码器：返回带孤立代理项的行。"""

        def feed(self, chunk: bytes) -> list[str]:
            return ["ok\ud83d\n"]

        def flush(self) -> list[str]:
            return []

    class _Reader:
        def __init__(self) -> None:
            self._sent = False

        async def read(self, size: int) -> bytes:
            if self._sent:
                return b""
            self._sent = True
            return b"x"

    class _Process:
        def kill(self) -> None:  # pragma: no cover - 未中断时不调用
            pass

    published: list[str] = []

    async def _publish(text: str, is_stderr: bool) -> None:
        published.append(text)

    lines: list[str] = []
    interrupted = await BashFunc._read_loop(
        _Reader(), _Process(), lines, _publish, decoder=_Decoder(),
    )
    assert interrupted is False
    assert lines == ["ok\ufffd\n"]
    # display（发布回调）与 read_buffer 的上游拿到同一份清洗后文本
    assert published == lines
    "".join(lines).encode("utf-8")
