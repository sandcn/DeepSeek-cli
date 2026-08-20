"""read_image 插件删除完整性测试（2026-08-20）。

背景：用户要求删除 read_image 插件（含支撑模块 multimodal）。
本文件验证删除后的完整性：
  1. 工具注册表/导出中不再存在 read_image；
  2. 支撑模块 src.api.multimodal、实现模块 src.tools.read_image 已删除；
  3. 配置系统不再有 MULTIMODAL_MODELS / multimodal_models；
  4. 显示名映射与配置视图模型无残留。
"""

from __future__ import annotations

import importlib

import pytest


def test_tool_registry_has_no_read_image():
    """工具注册表自动发现后不含 read_image。"""
    from src.tools.registry import get_tools
    tools = get_tools()
    assert "read_image" not in tools
    # 其余核心工具不受影响
    for name in ("read_file", "write_file", "bash", "search", "subagent"):
        assert name in tools


def test_tools_package_has_no_read_image_export():
    """src.tools 包不再导出 ReadImage。"""
    import src.tools as tools
    assert not hasattr(tools, "ReadImage")


def test_tools_module_read_image_deleted():
    """实现模块 src.tools.read_image 已删除（导入失败）。"""
    with pytest.raises(ImportError):
        importlib.import_module("src.tools.read_image")


def test_api_multimodal_deleted():
    """支撑模块 src.api.multimodal 已删除（导入失败）。"""
    with pytest.raises(ImportError):
        importlib.import_module("src.api.multimodal")


def test_display_name_no_read_image():
    """TOOL_DISPLAY_NAME 映射无 read_image 残留。"""
    from src.tools._constants import TOOL_DISPLAY_NAME
    assert "read_image" not in TOOL_DISPLAY_NAME


def test_config_keys_no_multimodal_models():
    """CONFIG_KEYS 元数据不再含 MULTIMODAL_MODELS。"""
    from src.config.defaults import CONFIG_KEYS
    assert "MULTIMODAL_MODELS" not in CONFIG_KEYS
    assert all("multimodal" not in ".".join(v["rc_path"]) for v in CONFIG_KEYS.values())


def test_config_access_no_multimodal_models():
    """延迟属性访问 MULTIMODAL_MODELS 应报 AttributeError。"""
    import src.config as config
    with pytest.raises(AttributeError):
        getattr(config, "MULTIMODAL_MODELS")


def test_view_model_no_multimodal_models():
    """配置视图模型无 MULTIMODAL_MODELS 描述残留。"""
    from src.config.view_model import CONFIG_ENTRY_DESCS, build_config_entries
    assert "MULTIMODAL_MODELS" not in CONFIG_ENTRY_DESCS
    keys = [e["key"] for e in build_config_entries()]
    assert "MULTIMODAL_MODELS" not in keys
