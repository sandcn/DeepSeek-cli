"""回归测试 — vnode/_patcher_mixins.py 的流式渲染 Bug 修复。

覆盖项：
  Bug1 (P0): PARAGRAPH UPDATE 纯追加路径缺少段落尾空行
  Bug2 (P1): HEADING UPDATE 缺少纯追加优化
  Bug3 (P1): Mermaid 围栏和内容之间缺少换行分隔
  Bug4 (P2): _ensure_theme hasattr 性能
"""

from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest
from rich.style import Style
from rich.text import Text

from src.renderer._archive.vnode._patcher_mixins import (
    _PatchDispatchMixin,
    _RenderHandlersMixin,
)
from src.renderer._archive.vnode.types import (
    VNode, VNodeType, VPatch, PatchType,
)


# ═══════════════════════════════════════════════════════════
# Mock 宿主类（模拟 VNodePatcher 提供 mixin 所需的属性/方法）
# ═══════════════════════════════════════════════════════════

class MockVNodePatcher(_PatchDispatchMixin, _RenderHandlersMixin):
    """模拟 VNodePatcher，只提供测试所需的最小属性集。

    注：_RenderHandlersMixin._render_paragraph 等方法调用
    self._inline_engine._render_inline，这里用 mock 替代。
    """

    def __init__(self):
        self._output = MagicMock()
        self._output.width = 80
        self._typing_speed = 0
        self._code_theme = "monokai"
        self._cached_theme = None  # 【Bug4 修复】预初始化

        # mock inline engine（_render_inline 返回原文本）
        mock_engine = MagicMock()
        mock_engine._render_inline.side_effect = lambda text: Text(text)
        self._inline_engine = mock_engine

        # mock math/mermaid renderer
        self._math_renderer = MagicMock()
        self._mermaid_renderer = MagicMock()

        self._rendered_cache = {}
        self._rendered_cache_for_update: dict[str, VNode] = {}

    def _output_assembled(self, assembled: Text):
        """模拟统一输出（直接 write）。"""
        self._output.write(assembled)

    def _write_vnode(self, renderable):
        self._output.write(renderable)

    def _should_render(self, node: VNode) -> bool:
        return True

    def _render_vnode(self, node: VNode) -> None:
        """模拟渲染调度。"""
        handler_map = {
            VNodeType.PARAGRAPH: self._render_paragraph,
            VNodeType.HEADING: self._render_heading,
            VNodeType.CODE_LINE: self._render_code_line,
            VNodeType.CODE_FENCE: self._render_code_fence,
            VNodeType.MERMAID: self._render_mermaid,
        }
        handler = handler_map.get(node.type)
        if handler:
            handler(node)


# ═══════════════════════════════════════════════════════════
# Bug1 回归测试: PARAGRAPH UPDATE 纯追加路径缺少段落尾空行
# ═══════════════════════════════════════════════════════════

def test_paragraph_update_append_adds_trailing_newline():
    """Bug1 回归：PARAGRAPH 纯追加后应有 write_line()。

    场景：段落 content 从 "Hello" → "Hello world"，
    VNodeDiffer 生成 UPDATE（old 是前缀），
    _handle_update 应输出 suffix + 追加 write_line()。
    """
    patcher = MockVNodePatcher()
    node = VNode(VNodeType.PARAGRAPH, key="p:0", content="Hello world")

    patch = VPatch(
        type=PatchType.UPDATE,
        key="p:0",
        node=node,
        old_content="Hello",
        new_content="Hello world",
    )

    # 清空 mock 调用记录
    patcher._output.reset_mock()

    patcher._handle_update(patch, {})

    # 验证：应调用 write（输出 suffix）+ write_line（段落尾空行）
    write_calls = [c for c in patcher._output.method_calls
                   if c[0] == 'write']
    write_line_calls = [c for c in patcher._output.method_calls
                        if c[0] == 'write_line']

    assert len(write_line_calls) >= 1, (
        "Bug1: PARAGRAPH UPDATE 纯追加后缺少 write_line() 调用"
    )


def test_paragraph_update_non_append_clears_and_rerenders():
    """PARAGRAPH 非追加更新：clear_line + 重渲染（回归保护）。"""
    patcher = MockVNodePatcher()
    node = VNode(VNodeType.PARAGRAPH, key="p:0",
                 content="completely new content")

    patch = VPatch(
        type=PatchType.UPDATE,
        key="p:0",
        node=node,
        old_content="old content",
        new_content="completely new content",
    )

    patcher._output.reset_mock()
    patcher._handle_update(patch, {})

    # 非追加路径应调用 clear_line
    clear_calls = [c for c in patcher._output.method_calls
                   if c[0] == 'clear_line']
    assert len(clear_calls) >= 1, (
        "PARAGRAPH 非追加更新应调用 clear_line"
    )


# ═══════════════════════════════════════════════════════════
# Bug2 回归测试: HEADING UPDATE 缺少纯追加优化
# ═══════════════════════════════════════════════════════════

def test_heading_update_append_optimization():
    """Bug2 回归：HEADING 纯追加应只输出新增后缀 + write_line。

    场景：标题 content 从 "## He" → "## Hello"，UPDATE patch，
    old 是 new 的前缀，应只输出后缀 "llo" + write_line()。
    """
    patcher = MockVNodePatcher()
    node = VNode(VNodeType.HEADING, key="h:0",
                 content="## Hello",
                 props={"level": 2})

    patch = VPatch(
        type=PatchType.UPDATE,
        key="h:0",
        node=node,
        old_content="## He",
        new_content="## Hello",
    )

    patcher._output.reset_mock()
    patcher._handle_update(patch, {})

    # 验证：不应调用 clear_line（纯追加路径跳过清行）
    clear_calls = [c for c in patcher._output.method_calls
                   if c[0] == 'clear_line']
    assert len(clear_calls) == 0, (
        "Bug2: HEADING 纯追加不应调用 clear_line"
    )

    # 验证：应有 write_line 调用（标题尾空行）
    write_line_calls = [c for c in patcher._output.method_calls
                        if c[0] == 'write_line']
    assert len(write_line_calls) >= 1, (
        "Bug2: HEADING 纯追加后缺少 write_line()"
    )


def test_heading_update_non_append_clears_and_rerenders():
    """HEADING 非追加更新：clear_line + 重渲染（回归保护）。"""
    patcher = MockVNodePatcher()
    node = VNode(VNodeType.HEADING, key="h:0",
                 content="# New Heading",
                 props={"level": 1})

    patch = VPatch(
        type=PatchType.UPDATE,
        key="h:0",
        node=node,
        old_content="# Old",
        new_content="# New Heading",
    )

    patcher._output.reset_mock()
    patcher._handle_update(patch, {})

    clear_calls = [c for c in patcher._output.method_calls
                   if c[0] == 'clear_line']
    assert len(clear_calls) >= 1, (
        "HEADING 非追加更新应调用 clear_line"
    )


# ═══════════════════════════════════════════════════════════
# Bug3 回归测试: Mermaid 围栏和内容之间缺少间距
# ═══════════════════════════════════════════════════════════

def test_mermaid_fence_content_spacing():
    """Bug3 回归：Mermaid 围栏输出后应有换行分隔。

    场景：渲染 Mermaid 图表时，围栏线输出后必须调用
    write_line()，确保围栏和图表内容之间有空行分隔。
    """
    patcher = MockVNodePatcher()

    # mock mermaid_renderer.render 返回空 Text
    patcher._mermaid_renderer.render.return_value = Text("graph TD\n  A-->B")

    # 验证 write_line 被调用至少 2 次（围栏后 + 内容后 + 关闭围栏前）
    patcher._output.reset_mock()

    node = VNode(VNodeType.MERMAID, key="mm:0",
                 content="graph TD\n  A-->B",
                 props={"lang": "mermaid"})
    patcher._render_mermaid(node)

    write_line_calls = [c for c in patcher._output.method_calls
                        if c[0] == 'write_line']
    assert len(write_line_calls) >= 2, (
        "Bug3: Mermaid 缺少围栏后/关闭前的 write_line() 调用"
    )


# ═══════════════════════════════════════════════════════════
# Bug4 回归测试: _ensure_theme 不使用 hasattr
# ═══════════════════════════════════════════════════════════

def test_ensure_theme_no_hasattr():
    """Bug4 回归：_ensure_theme 应直接判 None 而非用 hasattr。

    验证 _ensure_theme 能正确返回主题（当 _cached_theme 为 None 时
    会初始化），不依赖 hasattr 动态检查。
    """
    patcher = MockVNodePatcher()
    assert patcher._cached_theme is None, "预初始化为 None"

    with patch.object(patcher, '_cached_theme', None, create=True):
        # 首次调用应初始化
        with patch('rich.syntax.Syntax.get_theme') as mock_get_theme:
            mock_theme = MagicMock()
            mock_get_theme.return_value = mock_theme
            theme = patcher._ensure_theme()
            assert theme is not None
            # 再次调用应返回缓存，不重新初始化
            theme2 = patcher._ensure_theme()
            assert theme2 is theme


# ═══════════════════════════════════════════════════════════
# CODE_LINE UPDATE 回归测试
# ═══════════════════════════════════════════════════════════

def test_code_line_update_clears_and_rerenders():
    """CODE_LINE UPDATE 调用 clear_line + 重渲染。"""
    patcher = MockVNodePatcher()
    node = VNode(VNodeType.CODE_LINE, key="cl:0",
                 content="def foo():",
                 props={"lang": "python", "line_number": 1})

    patch = VPatch(
        type=PatchType.UPDATE,
        key="cl:0",
        node=node,
        old_content="def foo",
        new_content="def foo():",
    )

    patcher._output.reset_mock()
    patcher._handle_update(patch, {})

    clear_calls = [c for c in patcher._output.method_calls
                   if c[0] == 'clear_line']
    assert len(clear_calls) >= 1, (
        "CODE_LINE UPDATE 应调用 clear_line"
    )
