"""ListView 控件扩展测试（全面控件化方案B，2026-08-16）。

控件扩展（供 TraceView 台账委托）：
  - cursor 受控模式（外部控制光标）
  - onNavigate（光标变化回调）
  - page_up/page_down 翻页；g/G 首末（与 home/end 等价）
  - items 中 None 为不可选分隔行（导航自动跳过、不触发 onSelect）
  - renderItem 三参签名（item, index, isSelected）

未传新 props 时行为与旧版一致（回归测试）。
"""

from __future__ import annotations

from src.tui.ink.element import TEXT, h
from src.tui.ink.reconciler import Reconciler
from src.tui.ink.components import render_frame
from src.tui.ink.widgets.listview import ListView
from src.tui._input_parser import KeyEvent


def _render(element, width: int = 80, height: int = 24):
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, element, width, height)
    frame = render_frame(root, width)
    return rec, root, frame


def _frame_plain(frame) -> list[str]:
    return [ln.plain for ln in frame.lines]


def _marked_renderer(items: list):
    """renderItem：选中行 ▶ 前缀（显式表达选中位置——plain 可见）。"""

    def render_item(item, index, is_sel):
        mark = "\u25b6" if is_sel else " "
        return h(TEXT, {"children": mark + str(item)})

    return render_item


def _cursor_line(frame) -> int:
    """找到 ▶ 选中行下标。"""
    for i, ln in enumerate(frame.lines):
        if ln.plain.startswith("\u25b6"):
            return i
    return -1


# ── 受控 cursor + onNavigate ──────────────────────────────


def test_listview_controlled_cursor():
    """cursor prop 提供时渲染期用外部值（受控模式）。"""
    items = ["a", "b", "c"]
    render_item = _marked_renderer(items)
    rec, root, frame = _render(h(ListView, {
        "items": items, "height": 3, "cursor": 1, "renderItem": render_item,
    }))
    assert _cursor_line(frame) == 1, f"外部 cursor=1 → 第 2 行高亮: {_frame_plain(frame)}"
    # 无受控时内部 state 生效（initialIndex=0）
    rec2, root2, frame2 = _render(h(ListView, {
        "items": items, "height": 3, "renderItem": render_item,
    }))
    assert _cursor_line(frame2) == 0, f"无受控 cursor=0: {_frame_plain(frame2)}"


def test_listview_on_navigate_callback():
    """导航后 onNavigate 回调（写回外部选中索引）。"""
    log: list = []
    el = h(ListView, {
        "items": ["a", "b", "c"], "height": 3,
        "onNavigate": lambda i: log.append(i),
    })
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is True
    assert router(KeyEvent(kind="char", char="G")) is True
    assert log == [1, 2]


# ── page_up / page_down ───────────────────────────────────


def test_listview_page_down_up():
    """page_down 下翻一页（视口高度）、page_up 上翻。"""
    items = [f"item{i}" for i in range(30)]
    render_item = _marked_renderer(items)
    el = h(ListView, {"items": items, "height": 5, "renderItem": render_item})
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    # 5 次 down（0→5）→ page_down +5 → cursor 10
    for _ in range(5):
        router(KeyEvent(kind="arrow_down"))
    rec.render(root, el, 80, 24)
    assert router(KeyEvent(kind="page_down")) is True
    rec.render(root, el, 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    # 光标 index 10，offset=10-5+1=6 → 首行 item6、光标行 item10（第 5 行）
    assert lines[0].strip() == "item6"
    assert _cursor_line(render_frame(root, 80)) == 4  # item10 行（视口内第 5 行）
    assert router(KeyEvent(kind="page_up")) is True
    rec.render(root, el, 80, 24)
    lines = _frame_plain(render_frame(root, 80))
    # 光标 index 5，offset=5 → 首行 item5、光标行 item5（首行 ▶ 标记）
    assert "item5" in lines[0]
    assert _cursor_line(render_frame(root, 80)) == 0


def test_listview_page_boundary_no_consume():
    """翻页越过边界（不可达）时不消费（返回 False）。"""
    items = [f"item{i}" for i in range(10)]
    el = h(ListView, {"items": items, "height": 3})
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="page_up")) is False  # 已在首项
    assert router(KeyEvent(kind="page_down")) is True  # +3 → cursor 3
    rec.render(root, el, 80, 24)
    router = rec._build_input_router(root)
    # 连续翻页至越界（cursor 3 → 6 → 9 → 越界不消费）
    assert router(KeyEvent(kind="page_down")) is True
    rec.render(root, el, 80, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="page_down")) is True
    rec.render(root, el, 80, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="page_down")) is False  # 3+3*3=12 越界


# ── g / G 首末 ────────────────────────────────────────────


def test_listview_gG_jump():
    """g/G 跳首/末（与 home/end 等价）。"""
    items = ["a", "b", "c"]
    render_item = _marked_renderer(items)
    el = h(ListView, {"items": items, "height": 3, "renderItem": render_item})
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="G")) is True
    rec.render(root, el, 80, 24)
    assert _cursor_line(render_frame(root, 80)) == 2, "G 应跳末项（第 3 行）"
    assert router(KeyEvent(kind="char", char="g")) is True
    rec.render(root, el, 80, 24)
    assert _cursor_line(render_frame(root, 80)) == 0, "g 应跳首项（第 1 行）"


# ── None 分隔行跳过 ───────────────────────────────────────


def test_listview_none_separator_skipped_in_navigation():
    """items 含 None（分隔行）时导航自动跳过（不选中分隔行）。"""
    items = ["a", None, "b", None, "c"]
    render_item = _marked_renderer(items)
    el = h(ListView, {"items": items, "height": 5, "renderItem": render_item})
    rec, root, frame = _render(el)
    # 初始光标 index 0（a）
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    frame = render_frame(root, 80)
    assert _cursor_line(frame) == 2, f"↓ 应从 a 跳到 b（跳过 None 分隔行）: {_frame_plain(frame)}"
    # 再下 → c
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    frame = render_frame(root, 80)
    assert _cursor_line(frame) == 4, f"↓ 应从 b 跳到 c: {_frame_plain(frame)}"
    # 已在末项（c）再下不消费
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is False


def test_listview_none_separator_enter_no_select():
    """光标在 None 分隔行时 enter 不触发 onSelect。"""
    log: list = []
    items = [None, "b"]  # 首项为分隔行——初始光标钳制后可能停在不可选项
    el = h(ListView, {
        "items": items, "height": 3,
        "onSelect": lambda item, i: log.append((item, i)),
    })
    rec, root, _ = _render(el)
    router = rec._build_input_router(root)
    # 初始光标 index 0（None）——enter 不触发 onSelect
    assert router(KeyEvent(kind="enter")) is True  # 仍消费（阻断默认行为）
    assert log == []
    # 下移跳过 None → b，enter 触发
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter")) is True
    assert log == [("b", 1)]


def test_listview_render_item_is_selected_arg():
    """renderItem 第三参 isSelected 注入（选中行由调用方表达）。"""
    items = ["a", "b"]
    render_item = _marked_renderer(items)
    el = h(ListView, {"items": items, "height": 2, "renderItem": render_item})
    rec, root, frame = _render(el)
    assert _cursor_line(frame) == 0
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down")) is True
    rec.render(root, el, 80, 24)
    assert _cursor_line(render_frame(root, 80)) == 1


# ── 回归：默认行为不变 ────────────────────────────────────


def test_listview_default_behavior_regression():
    """未传新 props：行为与旧版一致（↓/enter、renderItem 两参）。"""
    el = h(ListView, {"items": ["a", "b"], "height": 2})
    rec, root, frame = _render(el)
    assert _frame_plain(frame) == ["a", "b"]
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="j")) is False  # 无 vim 导航（旧版）
    assert router(KeyEvent(kind="arrow_down")) is True
