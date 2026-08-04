"""user_select React Ink 化后的单元测试。

验证（2026-08-05 React Ink 化）：
  - _execute_terminal_async 不再直接读 stdin（os.read/select.select/read_byte）
  - 不再 stop/start EscapeMonitor、不再操作补全弹窗私有字段
  - 设置 model.user_select 弹窗状态（visible=True, seq+1, 选项/说明/默认值）
  - 轮询等待 UserSelectPopup 组件提交（confirmed）
  - 超时回退（timeout）/ 非交互回退（non_interactive）/ Windows 回退
  - 结束后清理弹窗状态（model.user_select = UserSelectState()）
"""

from __future__ import annotations

import asyncio
import json

import pytest
from unittest.mock import MagicMock, patch

from src._compat_termios import HAS_TERMIOS
from src.tui.app.model import AppModel, UserSelectState
from src.tools.user_select import UserSelectFunc


def _make_chat_ui(model):
    """创建 mock ChatUIConsumer（get_model 返回真实 AppModel）。"""
    ui = MagicMock()
    ui.get_model.return_value = model
    bb = MagicMock()
    bb.is_completion_visible = False
    bb._last_text = ""
    ui.bottom_bar = bb
    input_ = MagicMock()
    input_.flush_stdin_buffer = MagicMock()
    ui.get_input_component.return_value = input_
    ui.request_bottom_redraw = MagicMock()
    return ui, input_


# ── 基础断言 ──────────────────────────────────────────────

class TestUserSelectReactInkBasics:
    """React Ink 化后的实现约束（不再 raw I/O / 不再操作补全弹窗）。"""

    def test_methods_deleted(self):
        """旧 raw I/O 方法（_stop_monitor/_start_monitor）已删除。"""
        from src.tools.user_select import UserSelectFunc
        assert not hasattr(UserSelectFunc, '_stop_monitor'), \
            "_stop_monitor 应已删除"
        assert not hasattr(UserSelectFunc, '_start_monitor'), \
            "_start_monitor 应已删除"

    def test_no_os_read_in_source(self):
        """源码中不应再出现直接 stdin 读取（os.read/select.select/read_byte）。"""
        import inspect
        from src.tools import user_select as us_mod
        source = inspect.getsource(us_mod.UserSelectFunc._execute_terminal_async)
        for token in ("os.read", "select.select", "read_byte", "read_with_timeout"):
            assert token not in source, f"发现残留 raw I/O 调用: {token}"

    def test_no_completion_private_access_in_source(self):
        """不应再直接操作补全弹窗私有字段（_completion_idx 等）。"""
        import inspect
        from src.tools import user_select as us_mod
        source = inspect.getsource(us_mod.UserSelectFunc._execute_terminal_async)
        assert "_completion_idx" not in source, "发现残留补全弹窗私有字段访问"
        assert "show_completions" not in source, "发现残留 show_completions 调用"

    def test_uses_user_select_state(self):
        """新实现写入 model.user_select（UserSelectState），而非补全弹窗。"""
        import inspect
        from src.tools import user_select as us_mod
        source = inspect.getsource(us_mod.UserSelectFunc._execute_terminal_async)
        assert "user_select" in source
        assert "UserSelectState" in source


# ── 弹窗状态设置 ─────────────────────────────────────────

class TestUserSelectPopupState:
    """验证 _execute_terminal_async 正确设置弹窗状态。"""

    @pytest.mark.skipif(not HAS_TERMIOS, reason="需 termios 支持")
    @pytest.mark.asyncio
    async def test_sets_popup_state_with_options_and_descriptions(self):
        """弹窗状态包含 title/options/descriptions/multi/default 全字段。"""
        model = AppModel()
        ui, input_ = _make_chat_ui(model)

        with patch("src.tools.user_select.get_active_chat_ui", return_value=ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc(
                "测试", ["a", "b", "c"],
                multi_select=True,
                default_options=["a"],
                option_descriptions=["说明A"],
                timeout=30,
            )
            task = asyncio.ensure_future(us._execute_terminal_async())
            # 等待弹窗状态写入
            for _ in range(20):
                await asyncio.sleep(0.02)
                if model.user_select.visible:
                    break
            st = model.user_select
            assert st.visible is True
            assert st.seq == 1
            assert st.title == "测试"
            assert st.options == ["a", "b", "c"]
            assert st.option_descriptions == ["说明A", "", ""]  # 补齐
            assert st.multi_select is True
            assert st.default_options == ["a"]
            assert st.selected == 0          # 默认选项首项
            assert st.checked == [0]         # 默认选项预勾选
            assert st.deadline > 0
            # 模拟组件提交 → 工具返回
            model.user_select.done = True
            model.user_select.action = "confirmed"
            model.user_select.result = ["a", "c"]
            result = await task
        data = json.loads(result)
        assert data["action"] == "confirmed"
        assert data["selected"] == ["a", "c"]
        # 结束后清理
        assert model.user_select.visible is False

    @pytest.mark.skipif(not HAS_TERMIOS, reason="需 termios 支持")
    @pytest.mark.asyncio
    async def test_flushes_stdin_and_redraw(self):
        """打开弹窗前 flush stdin 残留 + request_bottom_redraw。"""
        model = AppModel()
        ui, input_ = _make_chat_ui(model)

        with patch("src.tools.user_select.get_active_chat_ui", return_value=ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b"])
            task = asyncio.ensure_future(us._execute_terminal_async())
            for _ in range(20):
                await asyncio.sleep(0.02)
                if model.user_select.visible:
                    break
            input_.flush_stdin_buffer.assert_called()
            ui.request_bottom_redraw.assert_called()
            model.user_select.done = True
            model.user_select.action = "cancel"
            model.user_select.result = ["a"]
            await task
        # finally 清理也 flush + redraw
        assert input_.flush_stdin_buffer.call_count >= 2
        assert ui.request_bottom_redraw.call_count >= 2


# ── 交互结果 ──────────────────────────────────────────────

class TestUserSelectResults:
    """验证工具读取组件交互结果（confirmed/cancel/timeout）。"""

    @pytest.mark.skipif(not HAS_TERMIOS, reason="需 termios 支持")
    @pytest.mark.asyncio
    async def test_confirmed_single(self):
        model = AppModel()
        ui, _ = _make_chat_ui(model)
        with patch("src.tools.user_select.get_active_chat_ui", return_value=ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b", "c"])
            task = asyncio.ensure_future(us._execute_terminal_async())
            for _ in range(20):
                await asyncio.sleep(0.02)
                if model.user_select.visible:
                    break
            model.user_select.done = True
            model.user_select.action = "confirmed"
            model.user_select.result = ["b"]
            result = await task
        data = json.loads(result)
        assert data["action"] == "confirmed"
        assert data["selected"] == ["b"]

    @pytest.mark.skipif(not HAS_TERMIOS, reason="需 termios 支持")
    @pytest.mark.asyncio
    async def test_cancel_uses_default(self):
        model = AppModel()
        ui, _ = _make_chat_ui(model)
        with patch("src.tools.user_select.get_active_chat_ui", return_value=ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b"], default_options=["a"])
            task = asyncio.ensure_future(us._execute_terminal_async())
            for _ in range(20):
                await asyncio.sleep(0.02)
                if model.user_select.visible:
                    break
            model.user_select.done = True
            model.user_select.action = "cancel"
            model.user_select.result = ["a"]
            result = await task
        data = json.loads(result)
        assert data["action"] == "cancel"
        assert data["selected"] == ["a"]

    @pytest.mark.skipif(not HAS_TERMIOS, reason="需 termios 支持")
    @pytest.mark.asyncio
    async def test_timeout_uses_default(self):
        """超时（无交互）自动回退默认选项。"""
        model = AppModel()
        ui, _ = _make_chat_ui(model)
        with patch("src.tools.user_select.get_active_chat_ui", return_value=ui), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b"], default_options=["a"], timeout=0.2)
            result = await us._execute_terminal_async()
        data = json.loads(result)
        assert data["action"] == "timeout"
        assert data["selected"] == ["a"]
        assert model.user_select.visible is False


# ── 回退路径 ──────────────────────────────────────────────

class TestUserSelectFallbacks:
    """非交互 / Windows / ChatUI 缺失回退。"""

    @pytest.mark.asyncio
    async def test_non_interactive(self):
        with patch("src.tools.user_select.get_active_chat_ui", return_value=None), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=False):
            us = UserSelectFunc("测试", ["a", "b"], default_options=["a"])
            result = await us._execute_terminal_async()
        data = json.loads(result)
        assert data["action"] == "non_interactive"
        assert data["selected"] == ["a"]

    @pytest.mark.asyncio
    async def test_windows_no_termios(self):
        with patch("src.tools.user_select.HAS_TERMIOS", False), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b"], default_options=["a"])
            result = await us._execute_terminal_async()
        data = json.loads(result)
        assert data["action"] == "non_interactive"

    @pytest.mark.asyncio
    async def test_no_chat_ui(self):
        with patch("src.tools.user_select.get_active_chat_ui", return_value=None), \
             patch("sys.stdin.fileno", return_value=0), \
             patch("os.isatty", return_value=True):
            us = UserSelectFunc("测试", ["a", "b"])
            result = await us._execute_terminal_async()
        data = json.loads(result)
        assert data["action"].startswith("error:")

    @pytest.mark.asyncio
    async def test_empty_options(self):
        us = UserSelectFunc("测试", [])
        result = await us.execute()
        data = json.loads(result)
        assert data["action"] == "empty"
        assert data["selected"] == []

    def test_option_descriptions_alignment(self):
        """option_descriptions 与 options 对齐（缺省补齐、超出截断、缺省空）。"""
        from src.tools.user_select import UserSelectFunc

        # 长度不足 → 补齐空字符串
        us = UserSelectFunc("t", ["a", "b", "c"], option_descriptions=["x"])
        assert us.option_descriptions == ["x", "", ""]
        # 长度超出 → 截断
        us2 = UserSelectFunc("t", ["a"], option_descriptions=["x", "y"])
        assert us2.option_descriptions == ["x"]
        # 缺省 → 全空
        us3 = UserSelectFunc("t", ["a", "b"])
        assert us3.option_descriptions == ["", ""]
        # None → 全空
        us4 = UserSelectFunc("t", ["a"], option_descriptions=None)
        assert us4.option_descriptions == [""]
