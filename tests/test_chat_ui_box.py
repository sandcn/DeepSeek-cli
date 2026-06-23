"""单元测试 — chat_ui Box/Static/Text 组件。"""

from __future__ import annotations
import pytest
from unittest.mock import MagicMock, ANY

from rich.style import Style
from rich.text import Text as RichText
from src.chat_ui._box import Box, Static, Text, FlexDirection
from src.chat_ui._components import TuiComponent


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture
def mock_output_adapter():
    """创建 mock OutputAdapter 以隔离终端依赖。"""
    return MagicMock()


# ── 测试用间谍组件 ──────────────────────────────────────

class _SpyComponent(TuiComponent):
    """测试用间谍组件，跟踪 render() 调用次数。"""

    def __init__(self, content: str = "spy"):
        super().__init__(children=None)
        self.content = content
        self.render_count = 0

    def render(self) -> str:
        self.render_count += 1
        return self.content


# ═══════════════════════════════════════════════════════════
# TestText
# ═══════════════════════════════════════════════════════════

class TestText:
    """Text 叶子组件单元测试。"""

    def test_text_plain_content(self):
        """Text("hello") 渲染纯文本。"""
        t = Text("hello")
        result = t.render()
        assert result == "hello"
        assert isinstance(result, str)

    def test_text_with_style(self):
        """Text("hello", style=Style(bold=True)) 渲染样式文本。"""
        style = Style(bold=True)
        t = Text("hello", style=style)
        result = t.render()
        assert isinstance(result, RichText)
        assert result.plain == "hello"
        assert result.style.bold is True

    def test_text_empty(self):
        """Text("") 渲染空字符串。"""
        t = Text("")
        result = t.render()
        assert result == ""

    def test_text_children_empty(self):
        """Text 默认无 children。"""
        t = Text("hello")
        assert t.children == []

    def test_text_render_to_adapter(self, mock_output_adapter):
        """Text render_to_adapter 调用 adapter.write。"""
        t = Text("hello")
        t.render_to_adapter(mock_output_adapter)
        mock_output_adapter.write.assert_called_once_with("hello")


# ═══════════════════════════════════════════════════════════
# TestStatic
# ═══════════════════════════════════════════════════════════

class TestStatic:
    """Static 不可变区域组件单元测试。"""

    def test_static_caches_result(self):
        """Static 首次渲染后缓存结果。"""
        spy = _SpyComponent("cached")
        s = Static(children=[spy])
        result = s.render()
        assert result == "cached"
        assert spy.render_count == 1

    def test_static_returns_cache_on_second_render(self):
        """第二次 render() 返回缓存（不调用子组件 render）。"""
        spy = _SpyComponent("cached")
        s = Static(children=[spy])
        s.render()  # 首次渲染，缓存
        result = s.render()  # 第二次应命中缓存
        assert result == "cached"
        assert spy.render_count == 1  # 子组件仅渲染 1 次

    def test_static_invalidate_cache(self):
        """invalidate_cache() 后重新渲染。"""
        spy = _SpyComponent("cached")
        s = Static(children=[spy])
        s.render()
        s.invalidate_cache()
        result = s.render()
        assert result == "cached"
        assert spy.render_count == 2  # 失效后重新渲染

    def test_static_with_children(self):
        """Static 包含多个子组件时正常工作。"""
        s = Static(children=[Text("A"), Text("B")])
        result = s.render()
        # render_children 以换行拼接纯文本子组件
        assert result == "A\nB"

    def test_static_render_to_adapter(self, mock_output_adapter):
        """Static render_to_adapter 调用 adapter.write。"""
        s = Static(children=[Text("hello")])
        s.render_to_adapter(mock_output_adapter)
        mock_output_adapter.write.assert_called_once_with("hello")


# ═══════════════════════════════════════════════════════════
# TestBox
# ═══════════════════════════════════════════════════════════

class TestBox:
    """Box 布局容器单元测试。"""

    def test_box_empty_children(self):
        """Box 无 children 返回空输出。"""
        box = Box()
        result = box.render()
        assert result == ""

    def test_box_column_layout(self):
        """Box(flex_direction=COLUMN) 垂直排列 children。"""
        box = Box(
            flex_direction=FlexDirection.COLUMN,
            children=[Text("A"), Text("B")],
        )
        result = box.render()
        # COLUMN: 子组件间换行分隔
        assert result == "A\n\nB"

    def test_box_row_layout(self):
        """Box(flex_direction=ROW) 水平排列 children。"""
        box = Box(
            flex_direction=FlexDirection.ROW,
            children=[Text("A"), Text("B")],
        )
        result = box.render()
        # ROW: 空格连接
        assert result == "A B"

    def test_box_padding_uniform(self):
        """Box(padding=2) 应用均匀 padding。"""
        box = Box(padding=2, children=[Text("X")])
        result = box.render()
        # padding=2 → (2,2,2,2): 上2空行, 左2空格, 右2空格, 下2空行
        assert result == "\n\n  X  \n\n"

    def test_box_margin(self):
        """Box(margin=1) 应用 margin。"""
        box = Box(margin=1, children=[Text("X")])
        result = box.render()
        # margin=1 → (1,1,1,1): 上1空行(左右各1空格=2空格), 内容行(左右各1空格), 下1空行(左右各1空格=2空格)
        assert result == "  \n X \n  "

    def test_box_border(self):
        """Box(border_style=Style(...)) 渲染边框。"""
        border_style = Style(color="blue")
        box = Box(border_style=border_style, children=[Text("hi")])
        result = box.render()
        assert isinstance(result, RichText)
        # 边框样式仅应用于边框字符（通过 Span），不应污染整体 base style
        assert not result.style
        # 验证边框字符的 Span 携带 border_style
        spans_with_border = [s for s in result.spans if s.style == border_style]
        assert len(spans_with_border) > 0
        plain = result.plain
        assert "┌" in plain and "┐" in plain
        assert "└" in plain and "┘" in plain
        assert "hi" in plain

    def test_box_nested(self):
        """Box 嵌套 Box 递归渲染。"""
        inner = Box(children=[Text("inner")])
        outer = Box(children=[inner])
        result = outer.render()
        assert result == "inner"

    def test_box_default_direction(self):
        """Box 默认 flex_direction 为 COLUMN。"""
        box = Box()
        assert box.flex_direction == FlexDirection.COLUMN

    def test_box_padding_tuple(self):
        """Box(padding=(1,2,3,4)) 正确处理四元组。"""
        box = Box(padding=(1, 2, 3, 4), children=[Text("X")])
        result = box.render()
        # padding=(1,2,3,4): top=1, right=2, bottom=3, left=4
        # COLUMN: lines=["X"]
        # padded: [""] + ["    X  "] + ["", "", ""]
        assert result == "\n    X  \n\n\n"

    def test_box_render_to_adapter(self, mock_output_adapter):
        """Box render_to_adapter 调用 adapter.write。"""
        box = Box(children=[Text("hello")])
        box.render_to_adapter(mock_output_adapter)
        mock_output_adapter.write.assert_called_once_with("hello")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
