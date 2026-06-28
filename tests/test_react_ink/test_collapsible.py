"""Collapsible 组件单元测试。

覆盖折叠/展开状态渲染、toggle 行为、空 title/children 边界、
update() props 变更检测、多行子组件缩进。

测试策略：构造 Collapsible 实例，调用 render() 获取 ANSI 字符串输出，
通过正则匹配验证前缀符号、样式序列和缩进正确性。
"""

from __future__ import annotations

import re
import pytest

from src.chat_ui.react_ink import Collapsible
from src.chat_ui.components.base import TuiComponent


# ── 测试辅助 ────────────────────────────────────────────

class _TextComp(TuiComponent):
    """简单文本子组件，返回固定内容。"""

    def __init__(self, text: str = "hello"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


class _MultiLineComp(TuiComponent):
    """多行文本子组件，每行独立返回。"""

    def __init__(self, lines: list[str] | None = None):
        super().__init__()
        self._lines = lines or ["line1", "line2", "line3"]

    def render(self) -> str:
        return "\n".join(self._lines)


# ANSI 转义序列匹配
_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列（兼容 StyledText，自动转为 str）。"""
    return _ANSI_RE.sub('', str(text))


def _has_dim(text: str) -> bool:
    """检查文本是否含 dim 样式（ANSI 2m）。"""
    return bool(re.search(r'\033\[\d*;?2m', text))


def _has_bold(text: str) -> bool:
    """检查文本是否含 bold 样式（ANSI 1m）。"""
    return bool(re.search(r'\033\[\d*;?1m', text))


# ═══════════════════════════════════════════════════════════
# TestCollapsibleRendering
# ═══════════════════════════════════════════════════════════

class TestCollapsibleRendering:
    """Collapsible 渲染测试。"""

    def test_collapsed_shows_only_dim_title(self):
        """折叠态仅显示 ▶ title（dim 样式），无子组件。"""
        coll = Collapsible(title="详情", collapsed=True)
        coll.add_child(_TextComp("内容行"))
        output = coll.render()

        clean = _strip_ansi(output)
        assert clean == "▶ 详情", f"折叠态应仅显示标题，实际: {clean!r}"
        assert _has_dim(str(output)), "折叠态标题应为 dim 样式"
        assert not _has_bold(str(output)), "折叠态标题不应为 bold 样式"
        assert "内容行" not in clean, "折叠态不应显示子组件内容"

    def test_expanded_shows_bold_title_and_children(self):
        """展开态显示 ▼ title（bold）+ 子组件缩进 2 空格。"""
        coll = Collapsible(title="详情", collapsed=False)
        coll.add_child(_TextComp("内容行"))
        output = coll.render()

        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 2, f"展开态应有 2 行（标题 + 子组件），实际: {lines}"
        assert lines[0] == "▼ 详情", f"首行应为展开标题，实际: {lines[0]!r}"
        assert lines[1] == "  内容行", f"子组件应缩进 2 空格，实际: {lines[1]!r}"

        str_output = str(output)
        # 标题行含 bold
        title_part = str_output.split("\n")[0]
        assert _has_bold(title_part), "展开态标题应为 bold 样式"

    def test_no_children_expanded_shows_only_title(self):
        """空 children 展开时仅有标题行，无子组件输出。"""
        coll = Collapsible(title="详情", collapsed=False)
        output = coll.render()

        clean = _strip_ansi(output)
        assert clean == "▼ 详情", f"空 children 展开应仅有标题，实际: {clean!r}"
        assert "\n" not in clean, "空 children 展开不应有换行"

    def test_no_children_collapsed_empty_title(self):
        """空 children + 空 title + 折叠态 → 返回空字符串。"""
        coll = Collapsible(title="", collapsed=True)
        output = coll.render()
        assert str(output) == "", f"空 title 折叠态应返回空字符串，实际: {output!r}"

    def test_empty_title_expanded_shows_children_only(self):
        """空 title 展开时仅显示缩进子组件，无标题行。"""
        coll = Collapsible(title="", collapsed=False)
        coll.add_child(_TextComp("内容行"))
        output = coll.render()

        clean = _strip_ansi(output)
        assert clean == "  内容行", f"空 title 展开应仅显示缩进子组件，实际: {clean!r}"
        assert "▼" not in clean, "空 title 展开不应有 ▼ 前缀"

    def test_multiline_child_indent(self):
        """多行子组件每行均缩进 2 空格。"""
        coll = Collapsible(title="日志", collapsed=False)
        coll.add_child(_MultiLineComp(["aaa", "bbb", "ccc"]))
        output = coll.render()

        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 4, f"应有 4 行（标题 + 3 行子组件），实际: {len(lines)}"
        assert lines[0] == "▼ 日志", f"首行应为标题，实际: {lines[0]!r}"
        assert lines[1] == "  aaa", f"第 2 行应缩进，实际: {lines[1]!r}"
        assert lines[2] == "  bbb", f"第 3 行应缩进，实际: {lines[2]!r}"
        assert lines[3] == "  ccc", f"第 4 行应缩进，实际: {lines[3]!r}"

    def test_multiple_children_render(self):
        """多个子组件按序渲染且均缩进。"""
        coll = Collapsible(title="列表", collapsed=False)
        coll.add_child(_TextComp("A"))
        coll.add_child(_TextComp("B"))
        output = coll.render()

        clean = _strip_ansi(output)
        lines = clean.split("\n")

        assert len(lines) == 3, f"应有 3 行（标题 + 2 个子组件），实际: {len(lines)}"
        assert lines[0] == "▼ 列表"
        assert lines[1] == "  A"
        assert lines[2] == "  B"


# ═══════════════════════════════════════════════════════════
# TestCollapsibleToggle
# ═══════════════════════════════════════════════════════════

class TestCollapsibleToggle:
    """Collapsible toggle / update 行为测试。"""

    def test_toggle_from_collapsed_to_expanded(self):
        """从折叠切换到展开：update({"collapsed": False})。"""
        coll = Collapsible(title="详情", collapsed=True)
        coll.add_child(_TextComp("内容行"))

        # 初始折叠
        assert _strip_ansi(str(coll.render())) == "▶ 详情"

        # 切换为展开
        changed = coll.update({"collapsed": False})
        assert changed is True, "collapsed 变化时应返回 True"
        clean = _strip_ansi(str(coll.render()))
        assert "▼ 详情" in clean
        assert "  内容行" in clean

    def test_toggle_from_expanded_to_collapsed(self):
        """从展开切换到折叠：update({"collapsed": True})。"""
        coll = Collapsible(title="详情", collapsed=False)
        coll.add_child(_TextComp("内容行"))

        # 初始展开
        clean = _strip_ansi(str(coll.render()))
        assert "▼ 详情" in clean
        assert "内容行" in clean

        # 切换为折叠
        changed = coll.update({"collapsed": True})
        assert changed is True, "collapsed 变化时应返回 True"
        clean = _strip_ansi(str(coll.render()))
        assert clean == "▶ 详情"

    def test_update_no_change_returns_false(self):
        """update() 传入相同值应返回 False。"""
        coll = Collapsible(title="详情", collapsed=False)

        # 相同 collapsed 值
        changed = coll.update({"collapsed": False})
        assert changed is False, "collapsed 未变化应返回 False"

        # 相同 title 值
        changed = coll.update({"title": "详情"})
        assert changed is False, "title 未变化应返回 False"

    def test_update_title_dynamically(self):
        """update() 动态修改 title。"""
        coll = Collapsible(title="旧标题", collapsed=False)
        coll.update({"title": "新标题"})

        clean = _strip_ansi(str(coll.render()))
        assert clean.startswith("▼ 新标题"), f"title 应更新，实际: {clean!r}"

    def test_update_title_and_collapsed_together(self):
        """同时 update title 和 collapsed。"""
        coll = Collapsible(title="旧", collapsed=False)
        coll.add_child(_TextComp("子内容"))

        changed = coll.update({"title": "新", "collapsed": True})
        assert changed is True

        clean = _strip_ansi(str(coll.render()))
        assert clean == "▶ 新", f"应同时应用 title 和 collapsed，实际: {clean!r}"

    def test_update_unknown_key_no_effect(self):
        """update() 传入未知 key 不应标记变更。"""
        coll = Collapsible(title="详情", collapsed=False)
        changed = coll.update({"unknown_key": "value"})
        assert changed is False, "未知 key 不应导致变更"


# ═══════════════════════════════════════════════════════════
# TestCollapsibleKey
# ═══════════════════════════════════════════════════════════

class TestCollapsibleKey:
    """Collapsible key 属性测试。"""

    def test_key_is_collapsible(self):
        """key 属性返回 'collapsible'。"""
        coll = Collapsible(title="测试")
        assert coll.key == "collapsible"

    def test_render_vnode(self):
        """render_vnode() 产出正确结构的 VNode。"""
        coll = Collapsible(title="标题", collapsed=True)
        vnode = coll.render_vnode()

        assert vnode.type == "collapsible"
        assert vnode.key == "collapsible"
        assert vnode.props["collapsed"] is True
        assert vnode.props["title"] == "标题"
        assert "▶ 标题" in vnode.props["text"]

    def test_render_vnode_empty_collapsed(self):
        """空 title 折叠态 render_vnode() text 为空字符串。"""
        coll = Collapsible(title="", collapsed=True)
        vnode = coll.render_vnode()

        assert vnode.props["text"] == ""
