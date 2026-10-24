"""bash 工具纯函数与关键行为的回归测试。

覆盖 2026-10-24 review 修复项：

- 危险命令按「命令位」判定（``echo sudo`` 不再误杀；``bash -c "rm -rf /"``
  仍拒绝），且 ``display()`` 路径（主 Agent 的生产路径）同样拦截；
- 读流异常时终止进程树、保留已读输出、快速返回（不再等命令自然结束）；
- ``exit 130`` 不再被误判为「命令已被中断」；
- stdout 保留前导缩进 / 尾随空白（不再 ``strip()``）；
- 输入回显过滤不再误吞短输入对应的真实输出；
- 输出截断只去掉一个尾换行；
- PowerShell 语法提示只在命令失败时追加；
- ``display_params`` 遵守 ``max_len``；日志命令脱敏；``#`` 注释判定；
  Cygwin 路径的 WSL bash 桩识别；解释器执行链解析。

需要真实 PTY 的用例在无 PTY 平台自动跳过。
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from src.core import base_agent as base_agent_module
from src.tools import bash as bash_module
from src.tools.bash import (
    BashFunc,
    _WIN_CTRL_C_EXIT,
    _consume_echo_line,
    _is_ctrl_c_exit,
    _is_wsl_bash_stub,
    _program_candidates,
    _pty_launch_argv,
    _redact_command,
    _scan_dangerous_command,
    _split_chain_operators,
)

#: 当前平台是否支持 PTY（POSIX openpty / Windows ConPTY）
_PTY_SUPPORTED = BashFunc("true")._pty_supported()
_needs_pty = pytest.mark.skipif(not _PTY_SUPPORTED, reason="当前平台不支持 PTY")


# ═══════════════════════════════════════════════════════════════
# 危险命令：按「命令位」判定
# ═══════════════════════════════════════════════════════════════

@pytest.mark.parametrize("command", [
    "echo sudo",
    "grep sudo README.md",
    'echo "rm -rf /"',
    "printf '%s' sudo",
    "which sudo",
    "bash -c 'echo sudo'",
    "nohup echo 'rm -rf /'",
])
def test_danger_scan_allows_plain_text_arguments(command: str) -> None:
    """纯文本命令的参数只是数据，不应触发危险命令拦截。"""
    assert _scan_dangerous_command(command) is None


@pytest.mark.parametrize("command", [
    "sudo -n true",
    "sudo ls",
    "rm -rf /",
    "bash -c 'rm -rf /'",
    'cmd /c "rm -rf /"',
    "sh -c 'sudo ls'",
    "find . -exec rm -rf / ;",
    "chmod -R 777 .",
    "chown a b",
    "echo $(sudo ls)",
    "echo `rm -rf /`",
    "ls && sudo ls",
    "xargs rm -rf /",
])
def test_danger_scan_rejects_executed_commands(command: str) -> None:
    """命令位 / 解释器脚本 / 命令替换里的危险命令必须拒绝。"""
    assert _scan_dangerous_command(command)


async def test_display_path_rejects_dangerous_command(monkeypatch) -> None:
    """``display()``（主 Agent 生产路径）必须与 ``execute()`` 同样拦截。"""
    calls: list[dict] = []

    async def fake_run_pty(self, **kwargs):
        calls.append(kwargs)
        return {"stdout": "SHOULD-NOT-RUN", "stderr": "", "returncode": 0}

    monkeypatch.setattr(BashFunc, "_run_pty", fake_run_pty)
    payload = json.loads(await BashFunc("rm -rf /").display())
    assert not calls
    assert payload["returncode"] == -1
    assert "拒绝执行危险命令" in payload["stderr"]


async def test_execute_allows_plain_text_sudo(monkeypatch) -> None:
    """``echo sudo`` 在 execute 路径也不再被误拒。"""

    async def fake_run_pty(self, **kwargs):
        return {"stdout": "sudo", "stderr": "", "returncode": 0}

    monkeypatch.setattr(BashFunc, "_run_pty", fake_run_pty)
    payload = json.loads(await BashFunc("echo sudo").execute())
    assert payload["stdout"] == "sudo"
    assert payload["returncode"] == 0


def test_program_candidates_follow_launcher_chain() -> None:
    """执行链要包含透传启动器本身（``sudo -n true`` 的 sudo 必须可见）。"""
    candidates = _program_candidates(["sudo", "-n", "true"])
    assert [name for name, _index in candidates] == ["sudo", "true"]
    assert _program_candidates(["echo", "sudo"]) == [("echo", 0)]


# ═══════════════════════════════════════════════════════════════
# 中断判定 / 输出保真
# ═══════════════════════════════════════════════════════════════

def test_ctrl_c_detection_uses_raw_returncode() -> None:
    """被 SIGINT 杀死（原始 -2）才算中断；用户 ``exit 130`` 不算。"""
    assert _is_ctrl_c_exit(-2) is True
    assert _is_ctrl_c_exit(_WIN_CTRL_C_EXIT) is True
    assert _is_ctrl_c_exit(130) is False
    assert _is_ctrl_c_exit(0) is False
    assert _is_ctrl_c_exit(None) is False


def test_echo_filter_short_input_requires_full_match() -> None:
    """短输入只允许整行相等匹配，不能按行尾吞掉真实输出。"""
    assert _consume_echo_line({"lines": ["ls"]}, "total 0 ls\n") is False
    assert _consume_echo_line({"lines": ["y"]}, "already\n") is False
    assert _consume_echo_line({"lines": ["y"]}, "y\n") is True
    # 较长输入仍支持「提示符 + 输入」同行的回显
    assert _consume_echo_line({"lines": ["hello"]}, ">>> hello\n") is True
    # 只消费一次
    pending = {"lines": ["hello"]}
    assert _consume_echo_line(pending, "hello\n") is True
    assert _consume_echo_line(pending, "hello\n") is False


def test_truncate_output_keeps_exact_line_count() -> None:
    truncate = BashFunc._truncate_output
    assert len(truncate("\n".join(str(i) for i in range(1000))).split("\n")) == 1000
    truncated = truncate("\n".join(str(i) for i in range(1200)))
    lines = truncated.split("\n")
    assert lines[-1].startswith("...(输出已截断")
    assert lines[0] == "200"
    # 内容以空行结尾时不再额外丢掉一行
    assert len(truncate("a\n\n\n" * 400).split("\n")) == 1000


def test_syntax_hint_only_appended_on_failure() -> None:
    marker = "not a valid statement separator"
    ok = json.loads(BashFunc._format_result(
        {"stdout": f"log: {marker}\n", "stderr": "", "returncode": 0}))
    assert "环境提示" not in ok["stdout"]
    failed = json.loads(BashFunc._format_result(
        {"stdout": f"{marker}\n", "stderr": "", "returncode": 1}))
    assert "环境提示" in failed["stdout"]


def test_display_params_respects_max_len() -> None:
    text = BashFunc.display_params({"command": "x" * 500})
    assert len(text) <= 80 + 2
    assert text.endswith("...'")


def test_redact_command_masks_credentials() -> None:
    redacted = _redact_command(
        'curl --token=abc123 -H "Authorization: Bearer sk-abcdefgh12345"')
    assert "abc123" not in redacted
    assert "sk-abcdefgh12345" not in redacted
    assert "***" in redacted


# ═══════════════════════════════════════════════════════════════
# PowerShell 链式运算符 / 路径识别
# ═══════════════════════════════════════════════════════════════

def test_chain_operator_hash_is_not_always_comment() -> None:
    """``echo a#b && c`` 的 ``#`` 不是注释（PowerShell 仅在词首起注释）。"""
    assert _split_chain_operators("echo a#b && c") is not None
    assert _split_chain_operators("echo 'a#b' && c") is not None
    # 真正的行注释仍被忽略
    assert _split_chain_operators("echo a # comment && b") is None


def test_wsl_bash_stub_recognizes_cygwin_path() -> None:
    assert _is_wsl_bash_stub("/cygdrive/c/Windows/System32/bash.exe") is True
    assert _is_wsl_bash_stub(r"C:\Windows\System32\bash.exe") is True
    assert _is_wsl_bash_stub("/bin/bash") is False


def test_launch_argv_uses_given_shell(monkeypatch) -> None:
    """调用方已检测 shell 时不再二次 detect（避免每条命令遍历父进程链）。"""

    def boom():
        raise AssertionError("不应再次调用 detect_shell")

    monkeypatch.setattr(bash_module, "detect_shell", boom)
    argv = _pty_launch_argv("echo hi", "bash")
    assert "-c" in argv and argv[-1] == "echo hi"


# ═══════════════════════════════════════════════════════════════
# 转后台竞态 / Job 句柄释放
# ═══════════════════════════════════════════════════════════════

async def test_promote_returns_result_for_finished_task() -> None:
    """超时窗口刚过、任务已完成时不得再注册成「运行中」。"""

    async def runner():
        return json.dumps({"stdout": "done", "stderr": "", "returncode": 0})

    task = asyncio.ensure_future(runner())
    await task
    result = await BashFunc("echo done")._promote_to_background(task, {}, None)
    assert json.loads(result)["stdout"] == "done"


def test_collect_done_background_closes_job(monkeypatch) -> None:
    """对话轮次自动消费任务记录时同样要关闭 Windows Job 句柄。"""
    agent = base_agent_module.BaseAgent.__new__(base_agent_module.BaseAgent)
    agent._background_tasks = {
        "bg-1": {
            "done": True, "stdout": "x", "stderr": "", "returncode": 0,
            "job_handle": 123,
        },
    }
    closed: list[dict] = []
    monkeypatch.setattr(
        base_agent_module, "_close_background_job",
        lambda rec: closed.append(rec),
    )
    monkeypatch.setattr(
        base_agent_module.BaseAgent, "_publish_background_task_event",
        lambda self: None,
    )
    messages = base_agent_module.BaseAgent._collect_done_background_messages(agent)
    assert len(messages) == 1
    assert closed and closed[0]["job_handle"] == 123
    assert "bg-1" not in agent._background_tasks


# ═══════════════════════════════════════════════════════════════
# 真实 PTY 的端到端行为
# ═══════════════════════════════════════════════════════════════

@_needs_pty
async def test_stdout_keeps_indentation() -> None:
    payload = json.loads(await BashFunc("printf '    indented  '").execute())
    assert payload["stdout"] == "    indented  "


@_needs_pty
async def test_exit_130_is_not_interrupt() -> None:
    payload = json.loads(await BashFunc("exit 130").execute())
    assert payload["returncode"] == 130
    assert payload["stderr"] == ""


@_needs_pty
async def test_read_error_terminates_process(monkeypatch) -> None:
    """读流异常：返回工具级错误、终止进程树，而不是等命令自然结束。"""

    class BoomDecoder:
        def feed(self, chunk):
            raise RuntimeError("decoder boom")

        def flush(self):
            return []

    monkeypatch.setattr(bash_module, "OutputLineDecoder", BoomDecoder)
    started = time.monotonic()
    payload = json.loads(await BashFunc("printf 'x\\n'; sleep 5").execute())
    elapsed = time.monotonic() - started
    assert payload["returncode"] == -1
    assert "读取输出异常" in payload["stderr"]
    assert elapsed < 4.0, f"读流异常后未及时终止进程（耗时 {elapsed:.2f}s）"
