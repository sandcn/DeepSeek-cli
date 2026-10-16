"""修复回归测试 — fire-and-forget 任务强引用 + 运行期不变量守卫。

覆盖：
  1. ``asyncio.create_task`` 返回值未保存强引用（信号回调 / 内核 emit /
     延迟自检），事件循环空闲时任务可能被 GC 回收并静默丢失
     → 统一经 ``src.kernel.task_refs`` 跟踪；
  2. ``assert`` 承担运行期校验（``python -O`` 下被剥离）
     → ``_text_width._build_flat_ranges`` / ``InkRenderer._assert_renderer_invariants``
     / MCP stdio 循环改为显式抛错或显式返回。
"""

from __future__ import annotations

import ast
import asyncio
import pathlib

import pytest

from src.kernel.task_refs import (
    release_all,
    spawn_tracked,
    track_task,
    tracked_count,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]


def _function_node(rel_path: str, name: str) -> ast.AST:
    tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{rel_path} 中未找到函数 {name}")


# ── 1. 任务强引用注册表 ──────────────────────────────────────


async def test_track_task_keeps_reference_until_done():
    release_all()
    task = asyncio.ensure_future(asyncio.sleep(0.01))
    track_task(task)
    assert tracked_count() == 1
    await task
    await asyncio.sleep(0)
    assert tracked_count() == 0


async def test_spawn_tracked_runs_coroutine_and_names_task():
    release_all()
    ran: list = []

    async def _work():
        ran.append(1)

    loop = asyncio.get_running_loop()
    task = spawn_tracked(loop, _work(), name="probe-task")
    assert task is not None
    await task
    assert ran == [1]
    assert task.get_name() == "probe-task"
    assert tracked_count() == 0


async def test_spawn_tracked_without_loop_closes_coroutine():
    coro = asyncio.sleep(0)
    assert spawn_tracked(None, coro) is None
    # 协程被显式关闭：不会留下「coroutine was never awaited」告警
    assert coro.cr_frame is None


async def test_spawn_tracked_task_exception_is_consumed():
    release_all()

    async def _boom():
        raise ValueError("boom")

    loop = asyncio.get_running_loop()
    task = spawn_tracked(loop, _boom())
    with pytest.raises(ValueError):
        await task
    await asyncio.sleep(0)
    assert tracked_count() == 0


@pytest.mark.parametrize("rel_path,forbidden", [
    ("src/app_init/_signal.py", "asyncio.create_task"),
    ("src/kernel/events.py", "loop.create_task(_await_quietly"),
    ("src/plugins/invariants.py", "loop.create_task(self._deferred_startup_check()"),
])
def test_create_task_sites_use_tracked_spawn(rel_path, forbidden):
    src = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
    assert "spawn_tracked" in src
    assert forbidden not in src


# ── 2. 运行期守卫（assert → 显式抛错/返回） ──────────────────


def test_text_width_range_builder_raises_value_error():
    from src._text_width import _build_flat_ranges

    with pytest.raises(ValueError):
        _build_flat_ranges([(5, 4)])          # lo > hi
    with pytest.raises(ValueError):
        _build_flat_ranges([(0, 5), (3, 9)])  # 区间重叠


def test_text_width_range_builder_has_no_assert():
    node = _function_node("src/_text_width.py", "_build_flat_ranges")
    assert not any(isinstance(n, ast.Assert) for n in ast.walk(node))


def test_renderer_invariants_have_no_assert_statement():
    node = _function_node("src/tui/ink/renderer.py", "_assert_renderer_invariants")
    assert not any(isinstance(n, ast.Assert) for n in ast.walk(node))
    assert any(isinstance(n, ast.Raise) for n in ast.walk(node))


def _method_node(rel_path: str, class_name: str, method_name: str) -> ast.AST:
    tree = ast.parse((REPO_ROOT / rel_path).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for item in node.body:
                if (
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name == method_name
                ):
                    return item
    raise AssertionError(f"{rel_path} 中未找到 {class_name}.{method_name}")


def test_mcp_transport_loops_have_no_assert_statement():
    for name in ("_read_loop", "_stderr_loop"):
        node = _method_node("src/mcp/transport.py", "StdioTransport", name)
        assert not any(isinstance(n, ast.Assert) for n in ast.walk(node))


def test_renderer_invariant_violation_raises_runtime_error():
    """破坏不变量时必须显式抛错（即便在 ``python -O`` 下）。"""
    from src.tui.ink.renderer import InkRenderer

    renderer = InkRenderer.__new__(InkRenderer)
    renderer._buf_h = -1
    renderer._top_aligned = True
    renderer._prev = None
    renderer._start_row = None
    renderer._height = 24
    with pytest.raises(RuntimeError):
        renderer._assert_renderer_invariants()
