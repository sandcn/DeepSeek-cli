"""pytest 配置

提供全局 fixture 确保测试间状态隔离。
"""

import sys
import pytest


@pytest.fixture(autouse=True)
def _cleanup_command_core_commands():
    """每个测试前后清理全局 _commands 字典，确保测试隔离。"""
    _do_cleanup_commands()
    yield
    _do_cleanup_commands()


def _do_cleanup_commands():
    """清理 _command_core._commands 字典。"""
    mod = sys.modules.get('src.core._command_core')
    if mod is not None and hasattr(mod, '_commands'):
        mod._commands.clear()
