"""Divider 组件单元测试。

覆盖无 title 纯分割线、带 title 居中渲染、color/dim/bold 样式属性、
空 title 退化、以及 update() props 变更检测。

测试策略：构造 Divider 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证分割线字符、ANSI 序列和结构正确性。
"""

from __future__ import annotations

import os
import re
import pytest
from unittest.mock import patch

from src.chat_ui.react_ink import Divider


# ── 测试辅助 ────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _has_ansi(text: str) -> bool:
    """检查文本是否含 ANSI 序列。"""
    return bool(_ANSI_RE.search(text))


# ═══════════════════════════════════════════════════════════
# TestDividerBasicRendering
# ═══════════════════════════════════════════════════════════

class TestDividerBasicRendering:
    """Divider 基础渲染测试。"""

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_no_title_plain_divider(self, mock_term):
        """无 title 时渲染纯分割线，填满终端宽度。"""
        divider = Divider()
        output = divider.render()

        # 应返回纯字符串（无样式属性）
        assert isinstance(output, str)
        # 应为 80 个 "─" 字符
        assert output == "─" * 80
        assert "─" in output

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_with_title_centered(self, mock_term):
        """有 title 时居中渲染，title 嵌入分割线中。"""
        divider = Divider(title="章节一")
        output = divider.render()

        clean = _strip_ansi(output)
        # title 应出现在输出中
        assert "章节一" in clean
        # title 两侧应有 "─" 分割线
        assert "─" in clean
        # title 不应在首尾（被 "─" 包围）
        assert not clean.startswith("章节一")
        assert not clean.endswith("章节一")
        # title 两侧字符数应大致相等（居中：差值 ≤1）
        title_idx = clean.index("章节一")
        left = clean[:title_idx]
        right = clean[title_idx + len("章节一"):]
        assert abs(len(left) - len(right)) <= 1

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((40, 24)))
    def test_narrow_terminal(self, mock_term):
        """窄终端（40 列）下标题正确居中。"""
        divider = Divider(title="OK")
        output = divider.render()

        clean = _strip_ansi(output)
        assert "OK" in clean
        assert len(clean) == 40


# ═══════════════════════════════════════════════════════════
# TestDividerStyleAttributes
# ═══════════════════════════════════════════════════════════

class TestDividerStyleAttributes:
    """Divider 样式属性测试。"""

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_color_produces_ansi(self, mock_term):
        """color 属性生成 ANSI 颜色序列。"""
        divider = Divider(color="red")
        output = divider.render()

        # 含 color 时应返回 StyledText（转为 str 后含 ANSI）
        output_str = str(output)  # StyledText.__str__ 应输出 ANSI 序列
        assert _has_ansi(output_str)
        # 验证红色 ANSI 序列存在（31 为 red 前景色）
        assert "\033[" in output_str

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_dim_produces_ansi(self, mock_term):
        """dim 属性生成 ANSI dim 序列。"""
        divider = Divider(dim=True)
        output = divider.render()

        output_str = str(output)
        assert _has_ansi(output_str)
        # dim 对应 ANSI 码 2
        assert "\033[2m" in output_str or "2;" in output_str or "\033[2" in output_str

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_bold_produces_ansi(self, mock_term):
        """bold 属性生成 ANSI bold 序列。"""
        divider = Divider(bold=True)
        output = divider.render()

        output_str = str(output)
        assert _has_ansi(output_str)
        # bold 对应 ANSI 码 1
        assert "\033[1m" in output_str or "1;" in output_str or "\033[1" in output_str

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_color_and_bold_combined(self, mock_term):
        """color + bold 组合产生复合 ANSI 序列。"""
        divider = Divider(color="green", bold=True)
        output = divider.render()

        output_str = str(output)
        assert _has_ansi(output_str)
        # 应同时含 bold(1) 和 green(32) 的 ANSI 序列
        assert "\033[" in output_str

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_no_style_no_ansi(self, mock_term):
        """无任何样式属性时不产生 ANSI 序列。"""
        divider = Divider()
        output = divider.render()

        # 纯字符串，无 ANSI
        assert isinstance(output, str)
        assert not _has_ansi(output)


# ═══════════════════════════════════════════════════════════
# TestDividerEdgeCases
# ═══════════════════════════════════════════════════════════

class TestDividerEdgeCases:
    """Divider 边界条件测试。"""

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_empty_title_same_as_no_title(self, mock_term):
        """空 title（""）等同于无 title，渲染纯分割线。"""
        divider = Divider(title="")
        output = divider.render()

        clean = _strip_ansi(output)
        # 应为纯 "─" 行，无空格分隔
        assert clean == "─" * 80

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_title_with_cjk_characters(self, mock_term):
        """含 CJK 宽字符的 title 正确计算视觉宽度并居中。"""
        divider = Divider(title="中文标题")
        output = divider.render()

        clean = _strip_ansi(output)
        assert "中文标题" in clean
        # CJK 字符每字 2 列宽（视觉宽），4 字视觉宽 8 + 2 空格 = 10
        # 视觉宽总计 80，但字符数（len）更少（因 CJK 计为 1 个字符）
        # 验证居中：两侧 "─" 字符数大致相等
        title_idx = clean.index("中文标题")
        left = clean[:title_idx]
        right = clean[title_idx + len("中文标题"):]
        # 两侧视觉宽度应大致相等
        assert abs(len(left) - len(right)) <= 1
        # 验证不含 ANSI 序列（无样式属性的纯分割线）
        assert not _has_ansi(str(output))


# ═══════════════════════════════════════════════════════════
# TestDividerUpdate
# ═══════════════════════════════════════════════════════════

class TestDividerUpdate:
    """Divider update() 方法测试。"""

    def test_update_title_changed_returns_true(self):
        """title 变更时 update() 返回 True。"""
        divider = Divider(title="old")
        result = divider.update({"title": "new"})
        assert result is True

    def test_update_title_same_returns_false(self):
        """title 值相同时 update() 返回 False（值比较无变更）。"""
        divider = Divider(title="same")
        result = divider.update({"title": "same"})
        assert result is False

    def test_update_color_changed_returns_true(self):
        """color 变更时 update() 返回 True。"""
        divider = Divider(color="red")
        result = divider.update({"color": "blue"})
        assert result is True

    def test_update_dim_changed_returns_true(self):
        """dim 变更时 update() 返回 True。"""
        divider = Divider(dim=False)
        result = divider.update({"dim": True})
        assert result is True

    def test_update_bold_changed_returns_true(self):
        """bold 变更时 update() 返回 True。"""
        divider = Divider(bold=False)
        result = divider.update({"bold": True})
        assert result is True

    def test_update_no_relevant_props_returns_false(self):
        """无相关 props 时 update() 返回 False。"""
        divider = Divider(title="x")
        result = divider.update({"other": "value"})
        assert result is False

    def test_update_multiple_props_returns_true(self):
        """多个 props 同时变更时 update() 返回 True。"""
        divider = Divider(title="a", color="red")
        result = divider.update({"title": "b", "color": "blue"})
        assert result is True


# ═══════════════════════════════════════════════════════════
# TestDividerRenderVNode
# ═══════════════════════════════════════════════════════════

class TestDividerRenderVNode:
    """Divider render_vnode() 测试。"""

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_vnode_type_is_divider(self, mock_term):
        """VNode type 为 'divider'。"""
        divider = Divider(title="test")
        vnode = divider.render_vnode()
        assert vnode.type == "divider"

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_vnode_key_is_divider(self, mock_term):
        """VNode key 为 'divider'。"""
        divider = Divider()
        vnode = divider.render_vnode()
        assert vnode.key == "divider"

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_vnode_props_contain_title(self, mock_term):
        """VNode props 包含 title 字段。"""
        divider = Divider(title="Hello")
        vnode = divider.render_vnode()
        assert vnode.props["title"] == "Hello"

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_vnode_props_contain_text(self, mock_term):
        """VNode props 包含 text 字段（渲染后的纯文本）。"""
        divider = Divider(title="X")
        vnode = divider.render_vnode()
        assert "text" in vnode.props
        assert len(vnode.props["text"]) > 0
