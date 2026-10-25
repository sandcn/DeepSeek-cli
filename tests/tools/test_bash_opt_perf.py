"""bash_opt 性能优化的回归测试。

覆盖四类改动（每项都只验证行为契约，不做耗时断言——耗时随机器波动，
性能证据记录在交付说明的基准数据里）：

1. 工具加载不拖入 GUI 能力（截图 / 窗口输入 / 终端按键）——延迟导入；
2. ``read_buffer`` 分块队列的增量语义、上限裁剪与旧字符串记录兼容；
3. PNG 编码（去掉大块中间副本后）与参考实现**逐字节一致**；
4. 窗口定位缓存：TTL 内复用、空结果不缓存、清空后重新解析。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib
from collections import deque

import pytest

from src.tools._bash_opt_schema import build_bash_opt_schema
from src.tools._window_input import INPUT_OPS
from src.tools.bash_opt import BashOptFunc, _INPUT_OPS_LITERAL


def asyncio_run(coro) -> None:
    """运行协程（不依赖 pytest-asyncio 的 fixture 形态）。"""
    import asyncio

    asyncio.run(coro)


# ── 1. 工具契约 / 延迟导入 ────────────────────────────────

def test_input_ops_literal_matches_window_input() -> None:
    """op 分派用的字面量必须与 GUI 模块的 INPUT_OPS 一致（防止两处漂移）。"""
    assert tuple(INPUT_OPS) == _INPUT_OPS_LITERAL
    # release 不接管任务生命周期（全局释放按键，与任务无关）
    assert "release" not in BashOptFunc._MANAGED_OPS
    assert set(_INPUT_OPS_LITERAL) - {"release"} <= set(BashOptFunc._MANAGED_OPS)


def test_bash_opt_import_does_not_load_gui_modules() -> None:
    """加载 bash_opt 不应导入截图 / 窗口输入模块（启动省一次 GUI 依赖解析）。"""
    code = (
        "import sys, src.tools.bash_opt;"
        "print([m for m in ('src.tools._screenshot', 'src.tools._window_input')"
        " if m in sys.modules])"
    )
    completed = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True, text=True, timeout=120,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "[]"


def test_schema_and_dispatch_still_expose_all_ops() -> None:
    """延迟导入后 schema 与操作分派仍完整（不可因引入常量而漏 op）。"""
    props = build_bash_opt_schema()["function"]["parameters"]["properties"]
    assert "op" in props
    supported = ("read", "wait", "kill", "stdin", "close_stdin",
                 "keys", "screenshot", *_INPUT_OPS_LITERAL)
    for op in supported:
        assert op in props["op"]["enum"], f"schema 缺少 op={op}"


# ── 2. read_buffer 分块队列 ───────────────────────────────

def test_read_buffer_incremental_and_legacy_string() -> None:
    """read 取走拼接内容并清空；旧字符串记录仍可被正确消费。"""
    from src.tools import bash

    async def scenario() -> None:
        rec: dict = {"read_buffer": deque(), "read_buffer_len": 0}
        await bash._append_read_buffer(rec, "a\n")
        await bash._append_read_buffer(rec, "b\n")
        func = BashOptFunc(task_id="bg-1", op="read")
        payload = json.loads(await func._op_read(rec))
        assert payload["output"] == "a\nb\n"
        assert payload["total_lines"] == 2
        assert rec["read_buffer"] == deque()
        assert rec["read_buffer_len"] == 0

        legacy = {"read_buffer": "old\n", "read_buffer_len": 4}
        payload = json.loads(await func._op_read(legacy))
        assert payload["output"] == "old\n"
        assert legacy["read_buffer"] == deque()

    asyncio_run(scenario())


def test_read_buffer_trim_keeps_tail_and_reports_dropped() -> None:
    """超过上限时只保留尾部最新内容，并累计被丢弃的字符数。"""
    from src.tools import bash

    async def scenario() -> None:
        rec: dict = {"read_buffer": deque(), "read_buffer_len": 0}
        line = "x" * 99 + "\n"  # 每行 100 字符
        count = bash._READ_BUFFER_MAX_CHARS // 100 + 500
        for _ in range(count):
            await bash._append_read_buffer(rec, line)
        total = sum(len(chunk) for chunk in rec["read_buffer"])
        assert total <= bash._READ_BUFFER_MAX_CHARS + bash._READ_BUFFER_TRIM_SLACK
        assert rec[bash.READ_BUFFER_DROPPED_KEY] > 0
        func = BashOptFunc(task_id="bg-1", op="read", max_lines=0)
        payload = json.loads(await func._op_read(rec))
        assert payload["output"].endswith(line)
        assert payload["total_lines"] == total // 100
        assert payload["buffer_overflow_chars"] > 0

    asyncio_run(scenario())


# ── 3. PNG 编码与参考实现逐字节一致 ───────────────────────

def _reference_png(width: int, height: int, rgb: bytes, level: int = 6) -> bytes:
    """改动前的编码实现（整幅 raw + 一次性 zlib.compress），作为对拍基准。"""
    from src.tools._screenshot import png

    stride = width * 3
    raw = bytearray(0)
    for y in range(height):
        raw.append(png.FILTER_NONE)
        raw += rgb[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, png.BIT_DEPTH,
                       png.COLOR_TYPE_RGB, 0, 0, 0)
    return (
        png.PNG_SIGNATURE
        + png._chunk(b"IHDR", ihdr)  # noqa: SLF001 - 与产物对拍需要内部 chunk 构造
        + png._chunk(b"IDAT", zlib.compress(bytes(raw), level))  # noqa: SLF001
        + png._chunk(b"IEND", b"")  # noqa: SLF001
    )


@pytest.mark.parametrize("width,height", [(1, 1), (7, 5), (64, 33)])
def test_png_bgra_bytes_identical_to_reference(width: int, height: int) -> None:
    from src.tools._screenshot import png

    bgra = bytes((index * 37) & 0xFF for index in range(width * height * 4))
    rgb = bytearray(width * height * 3)
    rgb[0::3] = bgra[2::4]
    rgb[1::3] = bgra[1::4]
    rgb[2::3] = bgra[0::4]
    assert png.encode_png_bgra(width, height, bgra, level=6) == _reference_png(
        width, height, bytes(rgb))
    assert png.encode_png_rgb(width, height, bytes(rgb), level=6) == _reference_png(
        width, height, bytes(rgb))


def test_png_rejects_bad_sizes() -> None:
    from src.tools._screenshot import png

    with pytest.raises(ValueError):
        png.encode_png_rgb(0, 1, b"")
    with pytest.raises(ValueError):
        png.encode_png_bgra(2, 2, b"\x00" * 15)


# ── 4. 窗口定位缓存 ───────────────────────────────────────

def test_process_tree_ps_cache_reuses_and_clears(monkeypatch) -> None:
    """``ps`` 进程树在 TTL 内只取一次；清空缓存后重新取。"""
    from src.tools import _bash_support as support

    support.clear_process_tree_cache()
    calls: list[list[str]] = []
    real = support._run_quiet

    def spy(argv):
        calls.append(list(argv))
        return real(argv)

    monkeypatch.setattr(support, "_run_quiet", spy)
    first = support._children_map_from_ps()
    if not first:
        pytest.skip("当前环境没有可用的 ps 进程表")
    after_first = len(calls)
    assert after_first >= 1
    assert support._children_map_from_ps() is first
    assert len(calls) == after_first
    support.clear_process_tree_cache()
    support._children_map_from_ps()
    assert len(calls) > after_first


def test_process_snapshot_reused_within_ttl(monkeypatch) -> None:
    """Toolhelp32 进程表在一次窗口定位里只枚举一次。"""
    from src.tools._screenshot import win as win_backend
    from src.tools._screenshot import winapi

    if not winapi.is_windows_platform():
        pytest.skip("仅 Windows 后端有进程表快照")
    win_backend.clear_window_cache()
    calls: list[int] = []
    real = winapi.list_processes

    def spy():
        calls.append(1)
        return real()

    monkeypatch.setattr(winapi, "list_processes", spy)
    first = win_backend._process_snapshot()
    second = win_backend._process_snapshot()
    assert len(calls) == 1
    assert first[1] is second[1]


def test_resolve_window_pids_cached_but_empty_not_cached(monkeypatch) -> None:
    from src.tools._screenshot import win as win_backend
    from src.tools._screenshot import winapi

    if not winapi.is_windows_platform():
        pytest.skip("仅 Windows 后端有窗口归属缓存")
    win_backend.clear_window_cache()
    calls: list[int] = []
    real = win_backend._resolve_window_pids_uncached

    def spy(pid):
        calls.append(pid)
        return real(pid)

    monkeypatch.setattr(win_backend, "_resolve_window_pids_uncached", spy)
    pid = os.getpid()
    resolved = win_backend.resolve_window_pids(pid)
    assert len(calls) == 1
    if not resolved:
        pytest.skip("该环境无法把当前进程映射为 Windows PID（空结果按设计不缓存）")
    assert win_backend.resolve_window_pids(pid) == resolved
    assert len(calls) == 1
    win_backend.clear_window_cache()
    win_backend.resolve_window_pids(pid)
    assert len(calls) == 2


def test_empty_resolution_is_not_cached(monkeypatch) -> None:
    from src.tools._screenshot import win as win_backend
    from src.tools._screenshot import winapi

    if not winapi.is_windows_platform():
        pytest.skip("仅 Windows 后端有窗口归属缓存")
    win_backend.clear_window_cache()
    calls: list[int] = []

    def empty(pid):
        calls.append(pid)
        return set()

    monkeypatch.setattr(win_backend, "_resolve_window_pids_uncached", empty)
    assert win_backend.resolve_window_pids(123456) == set()
    assert win_backend.resolve_window_pids(123456) == set()
    assert len(calls) == 2
