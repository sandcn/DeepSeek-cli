"""测试 Layer 枚举与类型定义。"""

from src.chat_ui.layer.types import Layer, DEFAULT_LAYER, MAX_LAYERS


class TestLayerEnum:
    """Layer 枚举值测试。"""

    def test_values(self):
        assert Layer.BACKGROUND == 0
        assert Layer.CONTENT == 10
        assert Layer.OVERLAY == 20

    def test_ordering(self):
        """数值越大越靠前。"""
        assert Layer.BACKGROUND < Layer.CONTENT < Layer.OVERLAY

    def test_int_compatible(self):
        """IntEnum 可与 int 比较。"""
        assert Layer.CONTENT == 10
        assert isinstance(Layer.CONTENT, int)

    def test_default_layer(self):
        assert DEFAULT_LAYER == Layer.CONTENT

    def test_max_layers(self):
        assert MAX_LAYERS == 8
