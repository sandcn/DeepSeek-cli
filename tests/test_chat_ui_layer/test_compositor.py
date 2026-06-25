"""测试 Compositor — 层级合并器。"""

import pytest
from src.chat_ui.layer.types import Layer
from src.chat_ui.layer.compositor import Compositor


class TestCompositor:
    """Compositor 单元测试。"""

    @pytest.fixture
    def comp(self):
        return Compositor()

    def test_empty_buffers(self):
        result = Compositor().composite({})
        assert result == []

    def test_single_layer(self, comp):
        buffers = {
            Layer.CONTENT: ["hello", "world"],
        }
        result = comp.composite(buffers)
        assert result == ["hello", "world"]

    def test_overlay_covers_content(self, comp):
        """OVERLAY 层覆盖 CONTENT 层。"""
        buffers = {
            Layer.CONTENT: ["content_a", "content_b", "content_c"],
            Layer.OVERLAY: ["overlay_x", None, "overlay_z"],
        }
        result = comp.composite(buffers)
        assert result == ["overlay_x", "content_b", "overlay_z"]

    def test_transparent_none(self, comp):
        """None 表示透明，允许下层穿透。"""
        buffers = {
            Layer.CONTENT: ["a", "b", "c"],
            Layer.OVERLAY: [None, "X", None],
        }
        result = comp.composite(buffers)
        assert result == ["a", "X", "c"]

    def test_empty_string_is_opaque(self, comp):
        """空字符串 '' 视为不透明（有意空行）。"""
        buffers = {
            Layer.CONTENT: ["a", "b"],
            Layer.OVERLAY: ["", None],
        }
        result = comp.composite(buffers)
        assert result == ["", "b"]  # 第0行被空字符串覆盖

    def test_three_layers(self, comp):
        buffers = {
            Layer.BACKGROUND: ["bg0", "bg1", "bg2"],
            Layer.CONTENT: [None, "content1", None],
            Layer.OVERLAY: [None, None, "over2"],
        }
        result = comp.composite(buffers)
        assert result == ["bg0", "content1", "over2"]

    def test_different_lengths(self, comp):
        """不同层长度不同时，短层末尾视为透明。"""
        buffers = {
            Layer.CONTENT: ["a", "b", "c", "d"],
            Layer.OVERLAY: ["X", "Y"],
        }
        result = comp.composite(buffers)
        assert result == ["X", "Y", "c", "d"]

    def test_trailing_empty_removed(self, comp):
        """尾部空行自动去除。"""
        buffers = {
            Layer.CONTENT: ["a", "b", "", ""],
        }
        result = comp.composite(buffers)
        assert result == ["a", "b"]

    def test_middle_empty_preserved(self, comp):
        """中间空行保留。"""
        buffers = {
            Layer.CONTENT: ["a", "", "c"],
        }
        result = comp.composite(buffers)
        assert result == ["a", "", "c"]
