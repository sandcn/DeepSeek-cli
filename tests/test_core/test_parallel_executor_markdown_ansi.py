"""测试 BUG-A4 修复：markdown 文本不含 ANSI 转义。

覆盖：
  - parallel_executor._stream_results_via_chatui 输出文本不含 \\x1b
  - _subagent_spawner._render_subagent_display ChatUI 分支不含 \\x1b
  - _subagent_spawner._render_subagent_display 非 ChatUI 分支不含 \\x1b
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.internal.agent._subagent_spawner import SubAgentSpawner
from src.core.parallel_executor import ParallelExecutor


class TestParallelExecutorMarkdownNoAnsi:
    """BUG-A4：parallel_executor markdown 文本不含 \\x1b。"""

    def test_markdown_no_ansi_injection_regression(self) -> None:
        """捕获传入 renderer.write 的 md_text，断言不含 \\x1b。"""
        executor = ParallelExecutor(MagicMock())
        results = [
            {"label": "agent-1", "description": "t1", "result": "ok", "error": "",
             "agent_type": "execute"},
        ]

        captured = {}

        class _FakeRenderer:
            def __init__(self, **kwargs):
                self._buf = []

            def write(self, text):
                self._buf.append(text)

            def close(self):
                captured["text"] = "".join(self._buf)

        mock_ui = MagicMock()
        with patch("src.tui.consumer.get_active_chat_ui", return_value=mock_ui), \
             patch("src.renderer.IncrementalRenderer", _FakeRenderer), \
             patch("src.core.parallel_executor._get_terminal_width", return_value=80):
            executor._stream_results_via_chatui(results)

        assert "\x1b" not in captured["text"]
        assert "### 1. [ex] t1" in captured["text"]


class TestSubAgentSpawnerMarkdownNoAnsi:
    """BUG-A4：_subagent_spawner markdown 文本不含 \\x1b。"""

    def test_subagent_spawner_markdown_no_ansi_regression(self) -> None:
        """ChatUI 分支：捕获 renderer.write 的 md_text，断言不含 \\x1b。"""
        spawner = SubAgentSpawner(MagicMock(), MagicMock())
        specs = [{"description": "t1", "prompt": "p1", "agent_type": "execute"}]

        captured = {}

        class _FakeRenderer:
            def __init__(self, **kwargs):
                self._buf = []

            def write(self, text):
                self._buf.append(text)

            def close(self):
                captured["text"] = "".join(self._buf)

        mock_ui = MagicMock()
        with patch("src.core.display_target.get_display_target", return_value=mock_ui), \
             patch("src.core._terminal.get_terminal_width", return_value=100), \
             patch("src.renderer.IncrementalRenderer", _FakeRenderer):
            spawner._render_subagent_display(specs)

        assert "\x1b" not in captured["text"]
        assert "### 1. [ex] t1" in captured["text"]

    def test_subagent_spawner_markdown_no_ansi_direct_regression(self) -> None:
        """非 ChatUI 分支（直接写 __stdout__）同样不含 \\x1b。"""
        spawner = SubAgentSpawner(MagicMock(), MagicMock())
        specs = [{"description": "t1", "prompt": "p1", "agent_type": "execute"}]

        captured = []
        with patch("src.core.display_target.get_display_target", return_value=None), \
             patch("src.renderer.IncrementalRenderer") as mock_renderer:
            mock_renderer.return_value.write.side_effect = lambda text: captured.append(text)
            spawner._render_subagent_display(specs)

        text = "".join(captured)
        assert "\x1b" not in text
        assert "### 1. [ex] t1" in text
