"""render 线程 finally 无 return 回归测试（2026-09-08）。

背景：``InkSession._render``（src/tui/ink/session.py）的 ``finally`` 块原以
``return`` 跳过「版本已更新（崩溃恢复新线程已启动）」场景下的队列排空，
触发 ``SyntaxWarning: 'return' in a 'finally' block``，且会在异常传播路径上
静默吞掉进行中的异常。

修复：``finally`` 改为 if/else 结构（版本变化 → 仅记日志跳过排空；否则
排空 keep_content=True 并按丢弃量写紧急 stderr）。

覆盖：
  1. 语法回归：session.py 编译零 SyntaxWarning；
  2. 行为回归：版本未变 → finally 排空 keep_content=True；
  3. 版本已变（崩溃恢复新线程已启动）→ 跳过排空；
  4. 崩溃恢复路径（_handle_render_crash 返回 True）→ finally 跳过排空；
  5. 排空丢弃 >0 → 紧急 stderr 输出；
  6. finally 不再吞异常（版本变化 + BaseException 传播时不被静默吞掉）。
"""

from __future__ import annotations

import threading
import time
import warnings
from types import SimpleNamespace

import pytest

from src.tui.ink.session import InkSession


class _Config:
    render_interval = 0.0


class _RenderStub:
    """``_render`` 桩（借用真实方法，覆盖循环依赖的最小状态面）。"""

    _render = InkSession._render

    def __init__(self):
        self._render_version = 0
        self._render_running = True
        self._exit_requested = False
        self._recover_attempts = 0
        self._last_recover_time = 0.0
        self._config = _Config()
        self._cmd_event = threading.Event()
        self._bottom_redraw_requested = threading.Event()
        self.drain_queue_calls = 0
        self.drain_safe_calls = []
        self.emergency_messages = []

    def _drain_queue(self):
        self.drain_queue_calls += 1
        self._render_running = False

    def _handle_render_crash(self, exc):
        return False

    def _drain_queue_safe(self, keep_content=False):
        self.drain_safe_calls.append(keep_content)
        return 0

    def _write_emergency(self, text, stream="stderr"):
        self.emergency_messages.append((text, stream))


# ── 1. 语法回归：编译零 SyntaxWarning ───────────────────

class TestSyntaxRegression:
    def test_session_compiles_without_syntax_warning(self):
        path = "src/tui/ink/session.py"
        with open(path, encoding="utf-8") as f:
            source = f.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            compile(source, path, "exec")

    def test_no_return_in_finally_by_ast(self):
        import ast

        with open("src/tui/ink/session.py", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for stmt in ast.walk(ast.Module(body=node.finalbody, type_ignores=[])):
                assert not isinstance(stmt, ast.Return), (
                    f"L{getattr(stmt, 'lineno', '?')}: finally 中不得使用 return"
                )


# ── 2. 版本未变：finally 正常排空 ───────────────────────

class TestDrainOnNormalExit:
    def test_drains_keep_content_when_version_unchanged(self):
        stub = _RenderStub()
        stub._render()
        assert stub.drain_safe_calls == [True]

    def test_emergency_output_when_commands_dropped(self):
        stub = _RenderStub()
        stub._drain_queue_safe = lambda keep_content=False: 3
        stub._render()
        assert len(stub.emergency_messages) == 1
        text, stream = stub.emergency_messages[0]
        assert "丢弃 3 条待处理命令" in text
        assert stream == "stderr"

    def test_no_emergency_output_when_nothing_dropped(self):
        stub = _RenderStub()
        stub._render()
        assert stub.emergency_messages == []


# ── 3. 版本已变：跳过排空 ───────────────────────────────

class TestSkipDrainOnVersionBump:
    def test_skips_drain_when_version_bumped_mid_loop(self):
        """运行中版本递增（模拟崩溃恢复启动新线程）→ 退出时跳过排空。"""

        stub = _RenderStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            stub._render_version += 1
            stub._render_running = False

        stub._drain_queue = drain_queue
        stub._render()
        assert stub.drain_safe_calls == []

    def test_drains_when_only_recover_attempt_bumped(self):
        """仅恢复计数变化（版本未变，max 耗尽路径）→ 仍正常排空。"""
        stub = _RenderStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            stub._recover_attempts += 1
            stub._render_running = False

        stub._drain_queue = drain_queue
        stub._render()
        assert stub.drain_safe_calls == [True]


# ── 4. 崩溃恢复路径：旧线程退出跳过排空 ─────────────────

class TestCrashRecoveryPath:
    def test_recovered_thread_skips_drain(self):
        """_handle_render_crash 返回 True（新线程已启动，版本+1）→
        旧线程 return 经 finally 跳过排空（队列移交新线程）。"""

        class _RecoveringStub(_RenderStub):
            def _handle_render_crash(self, exc):
                self._render_version += 1
                return True

        stub = _RecoveringStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            raise RuntimeError("render crash")

        stub._drain_queue = drain_queue
        stub._render()
        assert stub.drain_safe_calls == []
        assert stub.drain_queue_calls == 1

    def test_unrecovered_crash_still_drains(self):
        """max 恢复次数耗尽（_handle_render_crash 返回 False）→ break 退出，
        版本未变 → finally 仍排空。"""

        class _ExhaustedStub(_RenderStub):
            def _handle_render_crash(self, exc):
                self._render_running = False
                return False

        stub = _ExhaustedStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            raise RuntimeError("render crash")

        stub._drain_queue = drain_queue
        stub._render()
        assert stub.drain_safe_calls == [True]


# ── 5. finally 不吞异常 ─────────────────────────────────

class TestFinallyDoesNotSwallowExceptions:
    def test_base_exception_propagates_when_version_unchanged(self):
        stub = _RenderStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            stub._render_running = False
            raise KeyboardInterrupt

        stub._drain_queue = drain_queue
        with pytest.raises(KeyboardInterrupt):
            stub._render()
        assert stub.drain_safe_calls == [True]

    def test_base_exception_propagates_when_version_bumped(self):
        stub = _RenderStub()

        def drain_queue():
            stub.drain_queue_calls += 1
            stub._render_version += 1
            stub._render_running = False
            raise KeyboardInterrupt

        stub._drain_queue = drain_queue
        with pytest.raises(KeyboardInterrupt):
            stub._render()
        assert stub.drain_safe_calls == []


# ── 6. 真实线程集成：stop 语义下排空一次 ─────────────────

class TestRealThreadIntegration:
    def test_render_thread_drains_once_on_stop(self):
        stub = _RenderStub()
        done = threading.Event()

        def target():
            stub._render()
            done.set()

        t = threading.Thread(target=target, daemon=True)
        t.start()
        assert done.wait(timeout=5.0)
        assert stub.drain_safe_calls == [True]
