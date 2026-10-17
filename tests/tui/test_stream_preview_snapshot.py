"""流式预览快照复用（性能）回归。

渲染器在预览内容未变化时返回同一列表对象；TUI 侧必须保持
``block.preview_lines`` 引用不变，ChatView 的 use_memo / styled 缓存才能在
空转帧命中（免每帧复制列表 + 重建全部预览行元素）。
"""
from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.apply import _flush_renderer_to_block
from src.tui.app._tool_output_mixin import _ToolOutputMixin


class _FakeRenderer:
    def __init__(self, preview):
        self.snap = preview
        self.lines = []

    def take_lines(self):
        return list(self.lines)

    def take_preview_lines(self):
        return self.snap


def _fake_block():
    return SimpleNamespace(
        lines=[], preview_lines=[], committed_line_count=0,
        extra={}, source_text="",
    )


def _fake_model(block):
    return SimpleNamespace(
        content_block_index=0,
        reasoning_block_index=-1,
        blocks=[block],
        commit_open_block=lambda b: None,
    )


def test_apply_keeps_preview_list_identity():
    block = _fake_block()
    model = _fake_model(block)
    snap = ["row"]
    renderer = _FakeRenderer(snap)

    _flush_renderer_to_block(model, "content", renderer)
    assert block.preview_lines is snap

    # 渲染器复用同一快照对象（内容未变）→ 不应复制/替换列表对象
    _flush_renderer_to_block(model, "content", renderer)
    assert block.preview_lines is snap


def test_apply_replaces_preview_when_changed():
    block = _fake_block()
    model = _fake_model(block)
    first = ["a"]
    second = ["a", "b"]
    renderer = _FakeRenderer(first)
    _flush_renderer_to_block(model, "content", renderer)
    assert block.preview_lines is first

    renderer.snap = second
    _flush_renderer_to_block(model, "content", renderer)
    assert block.preview_lines is second


def test_tool_marker_version_stable_without_changes():
    block = SimpleNamespace(extra={})
    renderer = _FakeRenderer(["preview"])
    block.extra["_tool_md_lines"] = []
    block.extra["_tool_md_renderer"] = renderer
    block.extra["_tool_md_preview"] = []

    _ToolOutputMixin._sync_tool_md_lines(None, block)
    version = block.extra["_tool_md_version"]
    assert block.extra["_tool_md_preview"] is renderer.snap

    # 空转帧（无新行、预览对象未变）：版本不递增 → 工具卡缓存可复用
    _ToolOutputMixin._sync_tool_md_lines(None, block)
    assert block.extra["_tool_md_version"] == version

    # 预览内容变化：版本递增（工具卡重建）
    renderer.snap = ["preview", "more"]
    _ToolOutputMixin._sync_tool_md_lines(None, block)
    assert block.extra["_tool_md_version"] == version + 1
