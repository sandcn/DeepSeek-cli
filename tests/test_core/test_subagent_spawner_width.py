"""测试 BUG-A3 修复：_subagent_spawner 使用共享 get_terminal_width。

覆盖：ChatUI 分支宽度来自共享 ioctl 查询（不再直接用 os.get_terminal_size()）。
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.core.internal.agent._subagent_spawner import SubAgentSpawner


class TestChatUiWidthShared:
    """BUG-A3：ChatUI 分支宽度来自共享 ioctl。"""

    def test_chatui_width_uses_shared_ioctl_regression(self) -> None:
        """mock get_terminal_width 返回 100，断言传入 IncrementalRenderer 的 width == 100。"""
        spawner = SubAgentSpawner(MagicMock(), MagicMock())
        specs = [{"description": "t1", "prompt": "p1"}]

        mock_ui = MagicMock()
        with patch("src.core.display_target.get_display_target", return_value=mock_ui), \
             patch("src.core._terminal.get_terminal_width", return_value=100) as mock_width, \
             patch("src.renderer.IncrementalRenderer") as mock_renderer:
            spawner._render_subagent_display(specs)

        mock_width.assert_called_once()
        _, kwargs = mock_renderer.call_args
        assert kwargs.get("width") == 100
