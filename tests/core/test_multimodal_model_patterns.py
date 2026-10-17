"""多模态模型模式匹配回归测试。

覆盖 2026-10 review 修复：``glm-5.3`` 旗舰（仅文本）曾被 ``glm-5`` 宽前缀
误判为多模态，导致 read_image 返回 base64 图片而模型不支持视觉输入。
"""

from __future__ import annotations

import pytest

from src.core.multimodal import clear_multimodal_cache, is_multimodal_model


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_multimodal_cache()
    yield
    clear_multimodal_cache()


@pytest.mark.parametrize("model", [
    "glm-5.3",
    "glm-5.3-0813",
    "glm-5.2",
    "glm-5.1",
    "glm-5",
    "glm-4.7",
])
def test_glm_text_models_are_not_multimodal(model):
    """GLM 纯文本型号（含 5.3 旗舰）不得判为多模态。"""
    assert is_multimodal_model(model) is False


@pytest.mark.parametrize("model", [
    "glm-5.3-flash",
    "glm-5.3-flashx",
    "glm-5v",
    "glm-4.5v",
    "glm-4v",
])
def test_glm_vision_models_are_multimodal(model):
    """明确的多模态 GLM 型号仍判为多模态。"""
    assert is_multimodal_model(model) is True


@pytest.mark.parametrize("model", [
    "claude-opus-5-5",
    "claude-sonnet-5-5",
    "deepseek-flash",
    "deepseek-v4-flash",
    "mimo-v2.6-pro",
    "mimo-v2.6-flash",
    "gpt-4o",
    "gemini-2.5-pro",
])
def test_known_multimodal_models(model):
    assert is_multimodal_model(model) is True


@pytest.mark.parametrize("model", [
    "deepseek-v4-pro",
])
def test_known_text_models(model):
    assert is_multimodal_model(model) is False


def test_empty_model_is_not_multimodal():
    assert is_multimodal_model("") is False
    assert is_multimodal_model(None) is False
