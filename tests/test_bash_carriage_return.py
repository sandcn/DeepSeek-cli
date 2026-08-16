"""bash 返回给大模型的信息按终端语义处理 \\r 测试。

需求：bash.py 返回给大模型的信息要像终端一样处理 \\r——终端里 \\r（回车）
不产生新行，而是把光标移回当前行首、后续字符覆盖（进度条/行内刷新输出
最终只显示最后状态）。修复前返回内容保留字面 \\r（及 ANSI 转义序列），
大模型侧可能把 \\r 当作换行/字符，导致返回的行数与真实终端不一致
（比终端显示的多）。

修复：``_read_loop._handle_line`` 在数据源头统一 ``_strip_ansi`` + 
``_simulate_terminal``，lines（最终返回给大模型的输出）、show_output、
publish_line_fn（display/web_display 与后台 read_buffer）三方拿到同一份
「终端视角」文本。

覆盖：
- ``_simulate_terminal`` 单元：进度条折叠 / 部分覆盖 / 快路径 / 多行独立覆盖；
- ``_strip_ansi`` + ``_simulate_terminal`` 顺序契约组合；
- 集成：execute() 返回进度条折叠后内容、行数与终端一致、ANSI 已剥、
  普通输出不受影响；
- 集成：超过 MAX_LINES 的 \\r 折叠输出不触发误截断（行数按终端语义）；
- 集成：后台任务 read_buffer（background=True 与前台自动转后台两条路径）
  中同样不含字面 \\r。
"""
from __future__ import annotations

import asyncio
import json
import time

from src.tools._bash_support import _simulate_terminal, _strip_ansi
from src.tools.bash import BashFunc
from src.tools.bash_task import BashTaskFunc


class FakeAgent:
    """最小 Agent 替身：实现 bash 工具依赖的后台任务记录契约。"""

    def __init__(self):
        self._background_tasks: dict = {}

    def _register_background_task(self, task_id: str, record: dict) -> None:
        self._background_tasks[task_id] = record

    def _complete_background_task(self, task_id: str, result: str,
                                  status: str = "completed") -> None:
        rec = self._background_tasks.get(task_id)
        if rec is not None:
            rec["result"] = result
            rec["status"] = status
            rec["done"] = True

    def _remove_background_task(self, task_id: str) -> dict | None:
        return self._background_tasks.pop(task_id, None)


async def _wait_until(predicate, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.05)
    raise AssertionError("等待条件超时未满足")


# ── _simulate_terminal 单元 ───────────────────────────────

def test_simulate_terminal_progress_collapse():
    """\\r 进度条折叠为最终状态（终端只显示最后一次覆盖）。"""
    assert _simulate_terminal("10%\r20%\r30%") == "30%"


def test_simulate_terminal_partial_overwrite():
    """\\r 后内容从行首覆盖，未覆盖尾部保留（abc\\rXY → XYc）。"""
    assert _simulate_terminal("abc\rXY") == "XYc"


def test_simulate_terminal_no_cr_fast_path():
    """不含 \\r 时原样返回（零开销快路径）。"""
    assert _simulate_terminal("hello world") == "hello world"


def test_simulate_terminal_multi_line_independent():
    """\\r 覆盖只影响当前行内，多行各自独立。"""
    assert _simulate_terminal("a\rA\nb\rB") == "A\nB"


def test_simulate_terminal_trailing_cr():
    """行尾 \\r（无后续覆盖）不产生多余字符。"""
    assert _simulate_terminal("line\r") == "line"


# ── _strip_ansi + _simulate_terminal 顺序契约 ─────────────

def test_strip_ansi_before_simulate_terminal():
    """先剥 ANSI 再兑现 \\r 覆盖（含颜色码的进度行折叠为最终纯文本）。"""
    raw = "\x1b[31m10%\x1b[0m\r20%\r30%\n"
    assert _simulate_terminal(_strip_ansi(raw)) == "30%\n"


def test_strip_ansi_plain():
    """ANSI 转义序列被剥离，普通文本保留。"""
    assert _strip_ansi("\x1b[31mred\x1b[0m") == "red"


# ── execute() 集成：返回给大模型的内容与终端一致 ──────────

async def test_execute_collapses_carriage_return_progress():
    """execute() 返回的进度条输出折叠为最终状态，不含字面 \\r。"""
    out = await BashFunc("printf '10%%\\r20%%\\r30%%\\n'").execute()
    assert out == "30%"
    assert "\r" not in out


async def test_execute_line_count_matches_terminal():
    """返回的行数与真实终端一致：a\\rb\\nc\\n 终端显示 b / c 两行。"""
    out = await BashFunc("printf 'a\\rb\\nc\\n'").execute()
    assert out == "b\nc"
    assert len(out.split("\n")) == 2  # 与终端相同：2 行


async def test_execute_strips_ansi():
    """execute() 返回内容剥离 ANSI 转义序列（大模型不需要颜色码）。"""
    out = await BashFunc("printf '\\x1b[31mred\\x1b[0m\\n'").execute()
    assert out == "red"
    assert "\x1b" not in out


async def test_execute_plain_output_unchanged():
    """无 \\r/ANSI 的普通输出不受影响。"""
    out = await BashFunc("echo hello").execute()
    assert out == "hello"


async def test_execute_cr_collapse_not_mistruncated():
    """1500 次 \\r 覆盖折叠为 1 行：行数按终端语义统计，不触发 MAX_LINES 误截断。

    真实终端下 ``a\\rb``×1500 后接 ``END``：`a` 追加、`\\r` 回行首、`b` 覆盖
    第 1 列，后续每次 `a` 都覆盖第 2 列、`b` 覆盖第 1 列，最终 `E` 覆盖第 2 列
    的 `a`、`N`/`D` 追加 → 终端只显示 ``bEND`` 一行。返回内容与终端一致。
    """
    cmd = ("i=0; while [ $i -lt 1500 ]; do printf 'a\\rb'; i=$((i+1)); done; "
           "printf 'END\\n'")
    out = await BashFunc(cmd).execute()
    assert "\r" not in out
    assert out == "bEND"
    assert len(out.split("\n")) == 1  # 1500 次覆盖在终端只占 1 行
    assert "...输出已截断" not in out


# ── 后台任务 read_buffer：同样按终端语义 ───────────────────

async def test_background_read_buffer_terminal_clean():
    """background=True 任务的 read_buffer（bash_task read）不含字面 \\r。"""
    agent = FakeAgent()
    cmd = BashFunc("printf '10%%\\r20%%\\r30%%\\n'; sleep 3", background=True)
    cmd.set_agent(agent)
    r = await cmd.execute()
    tid = json.loads(r)["task_id"]

    await _wait_until(lambda: "30%" in agent._background_tasks[tid].get("read_buffer", ""))

    tool = BashTaskFunc(task_id=tid, op="read")
    tool.set_agent(agent)
    payload = json.loads(await tool.execute())
    assert "\r" not in payload["output"]
    assert "30%" in payload["output"]

    kill = BashTaskFunc(task_id=tid, op="kill")
    kill.set_agent(agent)
    await kill.execute()


async def test_auto_promoted_read_buffer_terminal_clean(monkeypatch):
    """前台命令自动转后台后，read_buffer（_line_proxy 路径）同样不含字面 \\r。"""
    agent = FakeAgent()
    monkeypatch.setattr(BashFunc, "_AUTO_BG_TIMEOUT", 0.2)
    cmd = BashFunc("sleep 0.5; printf 'x\\ry\\n'; sleep 2; echo done", background=False)
    cmd.set_agent(agent)
    r = await cmd.execute()
    tid = json.loads(r)["task_id"]
    rec = agent._background_tasks[tid]

    await _wait_until(lambda: "y" in rec.get("read_buffer", ""))

    tool = BashTaskFunc(task_id=tid, op="read")
    tool.set_agent(agent)
    payload = json.loads(await tool.execute())
    assert "\r" not in payload["output"]
    assert "y" in payload["output"]

    kill = BashTaskFunc(task_id=tid, op="kill")
    kill.set_agent(agent)
    await kill.execute()
