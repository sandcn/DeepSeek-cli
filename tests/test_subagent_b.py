"""测试模块 B — 占位（不测试任何 src/ 模块，仅为文件夹完整性保留）

此文件不依赖任何 src/ 模块，仅用于确保 tests/ 目录在未完全填充时保持完整。
"""

import pytest


def test_placeholder_b():
    """占位测试，确保 pytest 收集时不会因空文件报错"""
    assert True
