"""CompletionPopup 控件化测试（全面控件化方案B，2026-08-16）。

补全弹窗候选项经标准控件 ``SelectInput`` 表达：
  - 导航（↑↓）消费并写回 completion.selected（onHighlight）
  - Enter 放行（补全确认由 InputDispatcher 旧路径接管——无 onSelect）
  - Esc 放行（关闭弹窗由 InputDispatcher 处理——无 onCancel）
  - limit = 锁定高度可见行数 + 底部补白（高度锁定防闪烁）
  - renderItem：▶ 高亮 + match 前缀高亮 + 描述灰显
  - 不可见 → 空 TEXT（零高度不占行）
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.input_area import CompletionPopup
from src.tui.ink.element import TEXT, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets.interactive import SelectInput
from src.tui._input_parser import KeyEvent


def _render(element, width: int = 80, height: int = 24):
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


def _completion(items, selected=0, title="补全", descriptions=(), types=(),
                match_prefix="", visible=True, locked_height=0):
    return SimpleNamespace(
        visible=visible, items=items, texts=list(items), selected=selected,
        title=title, descriptions=list(descriptions), types=list(types),
        match_prefix=match_prefix, split_desc=False, locked_height=locked_height,
        _popup_lines_cache=None,
    )


def test_completion_popup_invisible_empty_text():
    """不可见/无 items → 空 TEXT（零高度不占行）。"""
    el = CompletionPopup({"completion": _completion([], visible=False), "width": 40})
    assert el.type == TEXT
    assert el.props.get("children") == ""


def test_completion_popup_uses_select_input_control():
    """候选项经 SelectInput 控件表达（Column: 标题 + SelectInput + 提示）。"""
    comp = _completion(["a", "b", "c"], selected=1, title="test")
    el = CompletionPopup({"completion": comp, "width": 40})
    # Column 根：标题 + SelectInput + 提示
    assert el.type.__name__ == "Column"
    children = list(el.children)
    assert len(children) == 3
    head = children[0]
    assert "test" in "".join(r.text for r in head.props.get("styled", []))
    assert "(2/3)" in "".join(r.text for r in head.props.get("styled", []))
    # 候选项 = SelectInput 控件
    assert children[1].type is SelectInput, f"候选项应为 SelectInput: {children[1].type}"
    lv = children[1].props
    assert [i["value"] for i in lv["items"]] == ["a", "b", "c"]
    assert lv["initialIndex"] == 1
    # 无 onSelect / 无 onCancel：Enter/Esc 放行（InputDispatcher 旧路径接管）
    assert lv.get("onSelect") is None
    assert lv.get("onCancel") is None
    # 提示行
    hint = children[-1]
    assert "Tab" in "".join(r.text for r in hint.props.get("styled", []))
    # 标题 + 候选项 + 提示 = 3 个子元素（无补白——items 数 ≥ n_rows 上限）
    assert len(children) == 3


def test_completion_popup_navigation_updates_selected():
    """导航（↑↓）写回 completion.selected（onHighlight 链路）。"""
    comp = _completion(["a", "b", "c"], selected=0, title="test")
    el = CompletionPopup({"completion": comp, "width": 40})
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is True
    assert comp.selected == 1, f"↓ 应写回 completion.selected=1: {comp.selected}"
    assert router(KeyEvent(kind="char", char="j")) is True  # vim 导航
    assert comp.selected == 2


def test_completion_popup_enter_esc_pass_through():
    """Enter/Esc 放行（补全确认/关闭由 InputDispatcher 旧路径接管）。"""
    comp = _completion(["a", "b"], selected=0, title="test")
    el = CompletionPopup({"completion": comp, "width": 40})
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter")) is False, "Enter 应放行（无 onSelect）"
    assert router(KeyEvent(kind="escape")) is False, "Esc 应放行（无 onCancel）"


def test_completion_popup_render_highlight_and_match():
    """renderItem：选中行 ▶ 高亮 + match 前缀高亮 + 命令描述灰显。"""
    comp = _completion(
        ["/help", "/load"], selected=0, title="命令",
        types=["command", "command"], descriptions=["显示帮助", "加载会话"],
        match_prefix="/", locked_height=4,
    )
    el = CompletionPopup({"completion": comp, "width": 40})
    rec, root, frame = _render(el)
    lines = _frame_plain(frame)
    # 标题行 + 候选项 + 提示行
    assert len(lines) >= 3
    assert "(1/2)" in lines[0]
    # 选中行含 ▶ + 候选项文本
    assert any(ln.startswith(" \u25b6 /") for ln in lines)
    # 描述灰显出现
    joined = "\n".join(lines)
    assert "显示帮助" in joined
    assert "加载会话" in joined
    # 导航后高亮移动（renderItem 基于 isSelected）
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert any(ln.startswith(" \u25b6 /load") for ln in lines), f"↓ 后高亮应到 /load: {lines}"


def test_completion_popup_locked_height_padding():
    """高度锁定：items 少于可见行数时底部补白（doc 高度不变——防闪烁）。"""
    comp = _completion(["a"], selected=0, title="t", locked_height=5)
    el = CompletionPopup({"completion": comp, "width": 40})
    children = list(el.children)
    # 标题 + SelectInput + 补白 + 提示（items=1 < n_rows → 补白行）
    assert len(children) == 4
    pad = children[2]
    assert pad.props.get("children") == ""
