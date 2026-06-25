"""测试 LayerManager。"""

import pytest
from src.chat_ui.layer.types import Layer, MAX_LAYERS
from src.chat_ui.layer.manager import LayerManager


class TestLayerManager:
    """LayerManager 单元测试。"""

    @pytest.fixture
    def mgr(self):
        return LayerManager(height=10, width=80)

    def test_init_creates_buffers(self, mgr):
        assert mgr.height == 10
        assert mgr.width == 80
        assert Layer.CONTENT in mgr.layers
        assert Layer.OVERLAY in mgr.layers
        # 默认不包含 BACKGROUND
        assert Layer.BACKGROUND not in mgr.layers

    def test_write_single_row(self, mgr):
        mgr.write(Layer.CONTENT, 0, "hello")
        buf = mgr.get_buffer(Layer.CONTENT)
        assert buf[0] == "hello"
        assert buf[1] is None  # 其他行仍为透明

    def test_write_out_of_bounds(self, mgr):
        """越界写入应静默跳过。"""
        mgr.write(Layer.CONTENT, 10, "oob")  # height=10, row 10 越界
        mgr.write(Layer.CONTENT, -1, "negative")
        buf = mgr.get_buffer(Layer.CONTENT)
        assert all(x is None for x in buf)

    def test_write_lines_batch(self, mgr):
        mgr.write_lines(Layer.CONTENT, 2, ["a", "b", "c"])
        buf = mgr.get_buffer(Layer.CONTENT)
        assert buf[2] == "a"
        assert buf[3] == "b"
        assert buf[4] == "c"

    def test_append(self, mgr):
        written = mgr.append(Layer.CONTENT, "line1\nline2\nline3")
        assert written == 3
        buf = mgr.get_buffer(Layer.CONTENT)
        assert buf[0] == "line1"
        assert buf[1] == "line2"
        assert buf[2] == "line3"

    def test_append_continues_from_last(self, mgr):
        mgr.append(Layer.CONTENT, "a")
        mgr.append(Layer.CONTENT, "b")
        buf = mgr.get_buffer(Layer.CONTENT)
        assert buf[0] == "a"
        assert buf[1] == "b"

    def test_append_buffer_full(self, mgr):
        """buffer 满时 append 返回 0。"""
        # 填满 buffer
        for i in range(10):
            mgr.write(Layer.CONTENT, i, f"row{i}")
        written = mgr.append(Layer.CONTENT, "overflow")
        assert written == 0

    def test_get_buffer_returns_copy(self, mgr):
        mgr.write(Layer.CONTENT, 0, "hello")
        buf = mgr.get_buffer(Layer.CONTENT)
        buf[0] = "modified"
        # 内部 buffer 不受影响
        assert mgr.get_buffer(Layer.CONTENT)[0] == "hello"

    def test_clear(self, mgr):
        mgr.write(Layer.CONTENT, 0, "hello")
        mgr.clear(Layer.CONTENT)
        buf = mgr.get_buffer(Layer.CONTENT)
        assert all(x is None for x in buf)

    def test_clear_all(self, mgr):
        mgr.write(Layer.CONTENT, 0, "a")
        mgr.write(Layer.OVERLAY, 1, "b")
        mgr.clear_all()
        assert all(x is None for x in mgr.get_buffer(Layer.CONTENT))
        assert all(x is None for x in mgr.get_buffer(Layer.OVERLAY))

    def test_resize_smaller(self, mgr):
        mgr.write(Layer.CONTENT, 9, "last")
        mgr.resize(5, 80)
        assert mgr.height == 5
        buf = mgr.get_buffer(Layer.CONTENT)
        assert len(buf) == 5
        # 被截断的行丢失
        assert buf[4] is None

    def test_resize_larger(self, mgr):
        mgr.write(Layer.CONTENT, 0, "hello")
        mgr.resize(20, 80)
        assert mgr.height == 20
        buf = mgr.get_buffer(Layer.CONTENT)
        assert len(buf) == 20
        assert buf[0] == "hello"  # 保留
        assert buf[10] is None  # 新增行为 None

    def test_custom_layers(self):
        mgr = LayerManager(5, 40, layers=[Layer.BACKGROUND, Layer.CONTENT, Layer.OVERLAY])
        assert Layer.BACKGROUND in mgr.layers
        assert len(mgr.layers) == 3

    def test_write_to_nonexistent_layer(self, mgr):
        """写入不存在的层应静默跳过。"""
        mgr.write(Layer.BACKGROUND, 0, "bg")  # BACKGROUND 不在默认 layers 中
        # 不应抛出异常

    def test_max_layers_truncation(self):
        """超过 MAX_LAYERS 的层级应被截断。"""
        # 重复传入同一 layer 模拟超量层级（LayerManager 按索引截断列表）
        too_many = [Layer.CONTENT] * (MAX_LAYERS + 5)
        mgr = LayerManager(5, 40, layers=too_many)
        assert len(mgr.layers) <= MAX_LAYERS
