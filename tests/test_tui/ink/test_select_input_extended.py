"""SelectInput / MultiSelect 控件扩展测试（全面控件化方案B，2026-08-16）。

控件扩展（供 UserSelectPopup 弹窗界面委托）：
  - vim 风格导航 j/J/k/K/g/G（与 ↑↓ 等价；g 首 / G 末）
  - onCancel（Esc 取消回调——消费 Esc）
  - onHighlight（选中/光标变化回调）
  - renderItem（自定义行渲染 (item, index, isSelected[, isChecked])）
  - consumeAll（弹窗模式：非导航/Enter/Esc 按键也消费；Ctrl+C 放行）

未传新 props 时行为与旧版一致（回归测试）。
"""

from __future__ import annotations

from src.tui.ink.element import TEXT, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets.interactive import SelectInput, MultiSelect
from src.tui._input_parser import KeyEvent


def _render(element, width: int = 80, height: int = 24):
    """调和 + 布局 + 渲染 Element 树，返回 (reconciler, root_fiber, frame)。"""
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


# ── vim 导航 ──────────────────────────────────────────────


def test_select_input_vim_jk_navigation():
    """j/k 与 ↑↓ 等效导航（选中行前缀 > 移动）。"""
    rec, root, frame = _render(h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}))
    assert _frame_plain(frame)[0] == ">a"
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="j")) is True  # 下 → b
    rec.render(root, h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0] == " a" and lines[1] == ">b", f"j 应下移选中到 b: {lines}"
    assert router(KeyEvent(kind="char", char="k")) is True  # 上 → a
    rec.render(root, h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0] == ">a", f"k 应上移选中到 a: {lines}"


def test_select_input_vim_gG_jump():
    """g/G 跳首/末项（大小写区分：g 首、G 末）。"""
    rec, root, frame = _render(h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="G")) is True
    rec.render(root, h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[2] == ">c", f"G 应跳末项 c: {lines}"
    assert router(KeyEvent(kind="char", char="g")) is True
    rec.render(root, h(SelectInput, {"items": ["a", "b", "c"], "prefix": ">"}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0] == ">a", f"g 应跳首项 a: {lines}"


def test_select_input_vim_boundary_no_consume():
    """已在边界时 j/k 无效移动不消费（返回 False，放行父级）。"""
    rec, root, _ = _render(h(SelectInput, {"items": ["a", "b"], "prefix": ">"}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="k")) is False  # 已在首项
    assert router(KeyEvent(kind="char", char="j")) is True
    rec.render(root, h(SelectInput, {"items": ["a", "b"], "prefix": ">"}), 80, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="j")) is False  # 已在末项


# ── onCancel / onHighlight / consumeAll ────────────────────


def test_select_input_escape_calls_on_cancel():
    """Esc 触发 onCancel（消费事件）；未提供 onCancel 时 Esc 放行。"""
    log: list = []
    rec, root, _ = _render(h(SelectInput, {
        "items": ["a", "b"], "onCancel": lambda item: log.append(item["label"]),
    }))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="escape")) is True
    assert log == ["a"]

    # 无 onCancel：Esc 放行（返回 False）
    rec2, root2, _ = _render(h(SelectInput, {"items": ["a", "b"]}))
    r2 = rec2._build_input_router(root2)
    assert r2(KeyEvent(kind="escape")) is False


def test_select_input_on_highlight_on_navigate():
    """导航后 onHighlight 回调（↑↓/j/k/g/G 均触发）。"""
    log: list = []
    rec, root, _ = _render(h(SelectInput, {
        "items": ["a", "b", "c"], "onHighlight": lambda i: log.append(i),
    }))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="j")) is True
    assert router(KeyEvent(kind="char", char="G")) is True
    assert log == [1, 2]


def test_select_input_consume_all_blocks_chars():
    """consumeAll=True：普通字符消费（不放行）；Ctrl+C 放行。"""
    rec, root, _ = _render(h(SelectInput, {
        "items": ["a", "b"], "consumeAll": True,
    }))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="x")) is True  # 普通字符消费
    assert router(KeyEvent(kind="char", char="\x03")) is False  # Ctrl+C 放行


def test_select_input_default_unchanged_without_new_props():
    """未传新 props：行为与旧版一致（j/k 不消费、Esc 放行、字符放行）。"""
    rec, root, _ = _render(h(SelectInput, {"items": ["a", "b"], "prefix": ">"}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="x")) is False  # 非导航字符放行
    assert router(KeyEvent(kind="escape")) is False  # Esc 放行


def test_select_input_render_item_custom_row():
    """renderItem 自定义行渲染（isSelected 传入，命中行由调用方表达）。"""
    def render_item(item, index, is_sel):
        return h(TEXT, {"children": ("[S]" if is_sel else "   ") + item["label"]})

    rec, root, frame = _render(h(SelectInput, {"items": ["a", "b"], "renderItem": render_item}))
    lines = _frame_plain(frame)
    assert lines[0] == "[S]a"
    assert lines[1] == "   b"
    # 导航后选中行变化
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="j")) is True
    rec.render(root, h(SelectInput, {"items": ["a", "b"], "renderItem": render_item}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0] == "   a"
    assert lines[1] == "[S]b"


# ── MultiSelect 扩展 ───────────────────────────────────────


def test_multi_select_vim_navigation_and_space():
    """MultiSelect j/k 导航 + 空格勾选 + g/G 跳转。"""
    rec, root, frame = _render(h(MultiSelect, {"items": ["a", "b", "c"]}))
    router = rec._build_input_router(root)
    # 首项选中勾选
    assert router(KeyEvent(kind="char", char=" ")) is True
    assert router(KeyEvent(kind="char", char="j")) is True
    # 第二项勾选
    assert router(KeyEvent(kind="char", char=" ")) is True
    # G 跳末（第三项未勾选 → ○）
    assert router(KeyEvent(kind="char", char="G")) is True
    rec.render(root, h(MultiSelect, {"items": ["a", "b", "c"]}), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0].startswith("● ") and lines[1].startswith("● "), f"a/b 应勾选: {lines}"
    assert lines[2].startswith("○ "), f"末项光标行未勾选: {lines}"
    # 提交：选中的为 a、b（value == label）
    submitted: list = []
    rec2, root2, _ = _render(h(MultiSelect, {
        "items": ["a", "b", "c"], "onSubmit": lambda sel: submitted.extend(sel),
    }))
    r2 = rec2._build_input_router(root2)
    assert r2(KeyEvent(kind="char", char=" ")) is True  # 勾选 a
    assert r2(KeyEvent(kind="char", char="j")) is True
    assert r2(KeyEvent(kind="char", char=" ")) is True  # 勾选 b
    assert r2(KeyEvent(kind="enter")) is True
    assert submitted == ["a", "b"]


def test_multi_select_escape_on_cancel():
    """MultiSelect Esc 触发 onCancel；未提供时放行。"""
    log: list = []
    rec, root, _ = _render(h(MultiSelect, {
        "items": ["a", "b"], "onCancel": lambda item: log.append(item["label"]),
    }))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="escape")) is True
    assert log == ["a"]

    rec2, root2, _ = _render(h(MultiSelect, {"items": ["a", "b"]}))
    assert rec2._build_input_router(root2)(KeyEvent(kind="escape")) is False


def test_multi_select_consume_all_blocks_chars():
    """MultiSelect consumeAll=True：字符消费、Ctrl+C 放行。"""
    rec, root, _ = _render(h(MultiSelect, {"items": ["a"], "consumeAll": True}))
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="x")) is True
    assert router(KeyEvent(kind="char", char="\x03")) is False


def test_multi_select_render_item_checked():
    """MultiSelect renderItem 接收 isChecked（勾选态由调用方表达）。"""
    def render_item(item, index, is_cursor, is_checked):
        mark = "[X]" if is_checked else "[ ]"
        return h(TEXT, {"children": mark + item["label"]})

    rec, root, frame = _render(h(MultiSelect, {
        "items": ["a", "b"], "renderItem": render_item,
    }))
    lines = _frame_plain(frame)
    assert lines[0] == "[ ]a"
    # 勾选首项
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char=" ")) is True
    rec.render(root, h(MultiSelect, {
        "items": ["a", "b"], "renderItem": render_item,
    }), 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    assert lines[0] == "[X]a"
