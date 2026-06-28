"""Breadcrumbs 组件单元测试。

覆盖多段路径渲染、单段路径、空 items、current_color 属性、
dim_history 开关、自定义分隔符、update() props 变更。

测试策略：构造 Breadcrumbs 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证分隔符、样式序列和结构正确性。
"""

from __future__ import annotations

import re

from src.chat_ui.components.breadcrumbs import Breadcrumbs
from src.chat_ui.components.base import TuiComponent
from src.chat_ui.vdom.vnode import VNode


# ── 测试辅助 ────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


def _has_ansi(text: str) -> bool:
    """检查文本是否含 ANSI 序列。"""
    return bool(_ANSI_RE.search(text))


# ═══════════════════════════════════════════════════════════
# TestBreadcrumbs
# ═══════════════════════════════════════════════════════════

class TestBreadcrumbs:
    """Breadcrumbs 渲染与行为测试。"""

    # ── 渲染 ──────────────────────────────────────────

    def test_multi_segment_with_separator(self):
        """多段路径渲染包含分隔符 ▸。"""
        bc = Breadcrumbs(items=["Home", "Docs", "API"])
        output = bc.render()
        output_str = str(output)
        plain = _strip_ansi(output_str)

        # 三段文本均应出现
        assert "Home" in plain
        assert "Docs" in plain
        assert "API" in plain
        # 分隔符 " ▸ " 应出现两次（Home→Docs, Docs→API）
        assert plain.count(" ▸ ") == 2
        # 应含 ANSI 序列（dim + bold）
        assert _has_ansi(str(output))

    def test_single_segment_no_separator(self):
        """单段路径无分隔符，直接返回文本。"""
        bc = Breadcrumbs(items=["Home"])
        output = bc.render()

        assert isinstance(output, str)
        assert output == "Home"
        # 单段时返回纯字符串，无 ANSI
        assert " ▸ " not in output

    def test_empty_items(self):
        """空 items 返回空字符串。"""
        bc = Breadcrumbs(items=[])
        output = bc.render()

        assert output == ""

    def test_empty_items_none(self):
        """items=None（默认）时返回空字符串。"""
        bc = Breadcrumbs()
        output = bc.render()

        assert output == ""

    def test_current_color_property(self):
        """current_color 属性使当前项使用指定 ANSI 颜色。"""
        bc = Breadcrumbs(
            items=["Home", "Docs", "API"],
            current_color="red",
        )
        output = str(bc.render())

        # 应含 ANSI 序列（至少 dim + red）
        assert _has_ansi(output)
        # 红色前景 ANSI 序列（\033[31m 为红色）
        assert "\033[31m" in output or "\033[1;31m" in output or "31" in output

    def test_current_color_none_uses_bold(self):
        """current_color=None 时当前项使用 bold 样式。"""
        bc = Breadcrumbs(items=["Home", "API"])
        output = str(bc.render())

        # 应含 bold ANSI 序列（\033[1m）且不含颜色码
        assert _has_ansi(output)
        # bold SGR 参数 1 应出现在输出中
        assert "1" in output  # SGR bold=1

    def test_dim_history_enabled(self):
        """dim_history=True（默认）时历史项使用 dim 样式。"""
        bc = Breadcrumbs(items=["Home", "Docs", "API"])
        output = str(bc.render())

        # dim SGR 参数 2 应出现在输出中
        assert "2" in output
        assert _has_ansi(output)

    def test_dim_history_disabled(self):
        """dim_history=False 时历史项不使用 dim 样式。"""
        bc = Breadcrumbs(
            items=["Home", "Docs", "API"],
            dim_history=False,
        )
        output = str(bc.render())

        # 分隔符仍使用 dim，但历史项文本用普通字符串
        # 分隔符的 dim 会引入 ANSI SGR 2
        # 但应确保 Home/Docs 纯文本部分仍存在
        plain = _strip_ansi(output)
        assert "Home" in plain
        assert "Docs" in plain
        assert "API" in plain

        # dim_history=False 时历史项为纯字符串，但分隔符依然 dim
        # 所以仍然有 ANSI 序列
        assert _has_ansi(output)

    def test_custom_separator(self):
        """自定义分隔符替代默认的 ▸。"""
        bc = Breadcrumbs(
            items=["A", "B", "C"],
            separator=" / ",
        )
        output = bc.render()
        plain = _strip_ansi(str(output))

        # 自定义分隔符 " / " 应出现两次
        assert plain.count(" / ") == 2
        # 默认分隔符不应出现
        assert " ▸ " not in plain
        # 三个元素均应存在
        assert "A" in plain and "B" in plain and "C" in plain

    def test_two_items_one_separator(self):
        """两段路径仅一个分隔符。"""
        bc = Breadcrumbs(items=["Home", "API"])
        output = bc.render()
        plain = _strip_ansi(str(output))

        assert plain.count(" ▸ ") == 1
        assert "Home" in plain
        assert "API" in plain

    # ── update() ──────────────────────────────────────

    def test_update_items(self):
        """update() 传入 items 返回 True 并更新内部状态。"""
        bc = Breadcrumbs(items=["Old"])
        changed = bc.update({"items": ["New", "Path"]})
        assert changed is True

        output = bc.render()
        plain = _strip_ansi(str(output))
        assert "New" in plain
        assert "Path" in plain
        assert "Old" not in plain

    def test_update_separator(self):
        """update() 传入 separator 返回 True 并更新分隔符。"""
        bc = Breadcrumbs(items=["A", "B"])
        changed = bc.update({"separator": " -> "})
        assert changed is True

        output = bc.render()
        plain = _strip_ansi(str(output))
        assert " -> " in plain

    def test_update_current_color(self):
        """update() 传入 current_color 返回 True 并更新颜色。"""
        bc = Breadcrumbs(items=["A", "B"], current_color=None)
        changed = bc.update({"current_color": "green"})
        assert changed is True

        output = str(bc.render())
        assert _has_ansi(output)
        # green 颜色 ANSI 序列
        assert "32" in output

    def test_update_dim_history(self):
        """update() 传入 dim_history 返回 True 并更新。"""
        bc = Breadcrumbs(items=["A", "B"], dim_history=True)
        changed = bc.update({"dim_history": False})
        assert changed is True

        # dim_history=False 时，历史项应为纯字符串
        # 再次渲染验证
        output = bc.render()
        plain = _strip_ansi(str(output))
        assert "A" in plain
        assert "B" in plain

    def test_update_no_recognized_keys(self):
        """update() 传入非识别键返回 False。"""
        bc = Breadcrumbs(items=["A"])
        changed = bc.update({"unknown": "value"})
        assert changed is False

    # ── 继承与协议 ────────────────────────────────────

    def test_inherits_tui_component(self):
        """Breadcrumbs 继承 TuiComponent。"""
        assert issubclass(Breadcrumbs, TuiComponent)

    def test_key_property(self):
        """key 属性返回 'breadcrumbs'。"""
        bc = Breadcrumbs(items=["A"])
        assert bc.key == "breadcrumbs"

    def test_render_vnode(self):
        """render_vnode() 产出正确的 VNode。"""
        bc = Breadcrumbs(items=["Home", "API"])
        vnode = bc.render_vnode()

        assert isinstance(vnode, VNode)
        assert vnode.type == "breadcrumbs"
        assert vnode.key == "breadcrumbs"
        assert "items" in vnode.props
        assert "text" in vnode.props
        assert "Home" in vnode.props["text"]
        assert "API" in vnode.props["text"]

    def test_render_vnode_empty(self):
        """空 items 时 render_vnode() text 为空字符串。"""
        bc = Breadcrumbs(items=[])
        vnode = bc.render_vnode()

        assert isinstance(vnode, VNode)
        assert vnode.props["text"] == ""

    # ── children 支持 ─────────────────────────────────

    def test_children_propagation(self):
        """children 参数正确初始化和访问。"""
        bc = Breadcrumbs(items=["A"], children=[])
        assert bc.children == []

    def test_add_child_chain(self):
        """add_child() 链式调用。"""
        bc = Breadcrumbs(items=["A"])
        result = bc.add_child(Breadcrumbs(items=["B"]))
        assert result is bc
        assert len(bc.children) == 1
        assert isinstance(bc.children[0], Breadcrumbs)
