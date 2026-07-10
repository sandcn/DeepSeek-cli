"""测试 /help 命令（原 commands_tools.py 现已合并到 _command_core.py）

命令合并说明
------------
commands_tools.py 中的 _cmd_help 已合并到 _command_core.py。
此测试文件验证合并后的功能完整性。
"""

import sys
import types
import pytest
from unittest.mock import MagicMock, patch


# ═══════════════════════════════════════════════════════════════════════════
# 测试 _cmd_help — 通过 importlib 加载
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdHelp:
    """_cmd_help：显示帮助信息（使用 patch 隔离依赖）。"""
    
    def test_writes_help_and_returns_true(self):
        """写入帮助文本，返回 True。"""
        from src.core.internal.commands._command_core import _cmd_help, get_dynamic_help_text
        
        mock_out = MagicMock()
        with patch('src.core.internal.commands._command_core._get_out', return_value=mock_out):
            ctx = MagicMock()
            ctx.arg = ""
            result = _cmd_help(ctx)
            
        assert result is True
        mock_out.write.assert_called_once()
        text = mock_out.write.call_args[0][0]
        assert "可用命令" in text
        assert "exit" in text
    
    def test_get_dynamic_help_text_content(self):
        """帮助文本包含基本命令和退出提示。"""
        from src.core.internal.commands._command_core import get_dynamic_help_text
        
        text = get_dynamic_help_text()
        assert text
        assert "exit" in text
