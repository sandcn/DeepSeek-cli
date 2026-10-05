"""read_image 恢复完整性测试（2026-08-22）。

背景：用户要求接入 deepseek-v4-flash-vision-exp（多模态视觉模型），
并恢复此前删除的 read_image 工具（含支撑模块 src.api.multimodal /
MULTIMODAL_MODELS 配置）。
本文件验证恢复后的完整性：
  1. 工具注册表/导出中存在 read_image；
  2. 支撑模块 src.api.multimodal、实现模块 src.tools.read_image 可导入；
  3. 配置系统重新包含 MULTIMODAL_MODELS / multimodal_models；
  4. 显示名映射与配置视图模型包含 read_image / MULTIMODAL_MODELS。
"""

from __future__ import annotations

import importlib

import pytest


def test_tool_registry_has_read_image():
    """工具注册表自动发现后包含 read_image。"""
    from src.tools.registry import get_tools
    tools = get_tools()
    assert "read_image" in tools
    # 其余核心工具不受影响
    for name in ("read_file", "write_file", "bash", "search", "subagent"):
        assert name in tools


def test_tools_package_exports_read_image():
    """src.tools 包重新导出 ReadImage。"""
    import src.tools as tools
    assert hasattr(tools, "ReadImage")


def test_tools_module_read_image_exists():
    """实现模块 src.tools.read_image 可导入。"""
    mod = importlib.import_module("src.tools.read_image")
    assert hasattr(mod, "ReadImageFunc")


def test_api_multimodal_exists():
    """支撑模块 src.api.multimodal 可导入。"""
    mod = importlib.import_module("src.api.multimodal")
    for name in ("is_multimodal_model", "build_image_content_blocks",
                 "clear_multimodal_cache"):
        assert hasattr(mod, name)


def test_display_name_has_read_image():
    """TOOL_DISPLAY_NAME 映射包含 read_image。"""
    from src.tools._constants import TOOL_DISPLAY_NAME
    assert "read_image" in TOOL_DISPLAY_NAME


def test_config_keys_has_multimodal_models():
    """CONFIG_KEYS 元数据重新包含 MULTIMODAL_MODELS。"""
    from src.config.defaults import CONFIG_KEYS, DEFAULTS
    assert "MULTIMODAL_MODELS" in CONFIG_KEYS
    assert CONFIG_KEYS["MULTIMODAL_MODELS"]["rc_path"] == ("multimodal_models",)
    assert "multimodal_models" in DEFAULTS


def test_config_access_multimodal_models():
    """延迟属性访问 MULTIMODAL_MODELS 返回列表。"""
    import src.config as config
    assert isinstance(getattr(config, "MULTIMODAL_MODELS"), list)


def test_view_model_has_multimodal_models():
    """配置视图模型包含 MULTIMODAL_MODELS 描述。"""
    from src.config.view_model import CONFIG_ENTRY_DESCS, build_config_entries
    assert "MULTIMODAL_MODELS" in CONFIG_ENTRY_DESCS
    keys = [e["key"] for e in build_config_entries()]
    assert "MULTIMODAL_MODELS" in keys
