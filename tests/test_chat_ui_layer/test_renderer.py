"""测试 IncrementalLayerRenderer — 增量行输出。"""

import pytest
from src.chat_ui.layer.renderer import IncrementalLayerRenderer


class MockOutput:
    """模拟终端输出适配器。"""

    def __init__(self):
        self.writes: list[str] = []

    def write_raw(self, text: str) -> None:
        self.writes.append(text)

    def flush(self) -> None:
        pass

    @property
    def output(self) -> str:
        return "".join(self.writes)


class TestIncrementalLayerRenderer:
    """IncrementalLayerRenderer 单元测试。"""

    @pytest.fixture
    def out(self):
        return MockOutput()

    @pytest.fixture
    def renderer(self, out):
        return IncrementalLayerRenderer(out)

    def test_first_frame_full_output(self, renderer, out):
        """首次渲染应全量输出。"""
        lines = ["hello", "world"]
        result = renderer.render(lines)
        assert result == 2
        # 首帧：逐行 \r\033[K{line}\n + \033[s
        assert "\033[s" in out.output
        assert "hello" in out.output
        assert "world" in out.output

    def test_empty_frame(self, renderer):
        """空输入应返回 0。"""
        result = renderer.render([])
        assert result == 0

    def test_second_frame_no_change(self, renderer, out):
        """第二帧无变化时不输出。"""
        lines = ["a", "b"]
        renderer.render(lines)
        out.writes.clear()
        result = renderer.render(["a", "b"])
        assert result == 2
        # 无变化：不应输出任何内容
        assert len(out.writes) == 0

    def test_incremental_one_row_changed(self, renderer, out):
        """增量：仅输出变化的行。"""
        renderer.render(["a", "b", "c"])
        out.writes.clear()
        result = renderer.render(["a", "X", "c"])
        assert result == 3
        output = out.output
        # 应包含第2行的更新（X）
        assert "X" in output

    def test_rows_increased(self, renderer, out):
        """行数增加时追加新行。"""
        renderer.render(["a", "b"])
        out.writes.clear()
        result = renderer.render(["a", "b", "c", "d"])
        assert result == 4  # last_count = max(2,4)

    def test_rows_decreased(self, renderer, out):
        """行数减少时清除多余行。"""
        renderer.render(["a", "b", "c", "d"])
        out.writes.clear()
        result = renderer.render(["a", "b"])
        assert result == 4  # 保留峰值
        # 应包含清除序列
        output = out.output
        # 清除多余行应有 \033[K
        assert "\033[K" in output

    def test_reset_forces_full(self, renderer, out):
        """reset 后应全量重新输出。"""
        renderer.render(["a", "b"])
        renderer.reset()
        out.writes.clear()
        result = renderer.render(["a", "b"])
        assert result == 2
        # reset 后 first_frame → 全量输出
        assert "\033[s" in out.output
