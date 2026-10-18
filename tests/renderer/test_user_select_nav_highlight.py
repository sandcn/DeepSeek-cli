"""user_select 弹窗上下键导航高亮渲染回归测试（2026-10-18）。

bug：多选弹窗中按 ↑↓ 后标题 ``(n/N)`` 与 ``us.selected`` 都更新了，但整行
背景高亮始终停在首次渲染的选项行——「上下键不会切换选项」的渲染错误。

根因：``_layout_measure._measure_text`` 的 ``_wrap_cache`` 值比较快路径只
比较 ref（纯文本分支为文本字符串）与宽度键，**不含 style**。多选选项行
文本固定（``○ 城市N``），只有整行 style（选中背景）变化——文本相同即
命中旧缓存，复用旧 ``StyledRun``（旧样式），高亮不跟随。

修复：值比较短路仅用于 styled runs 分支（``StyledRun`` 值比较已含 style）；
纯文本分支必须比较 ``style_fingerprint``。

本文件覆盖两层：
  1. 布局层：同文本、仅 style 变化的 TEXT 跨帧渲染样式必须更新；
  2. 端到端：user_select 多选/单选弹窗经 composite input router 按 ↑↓，
     渲染帧的高亮行必须跟随。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.app import App
from src.tui.app.model import AppModel, UserSelectState
from src.tui.core.style import Style
from src.tui.ink import BOX, TEXT, h
from src.tui.ink import components as _components
from src.tui.ink.reconciler import Reconciler

_OPTS = [f"城市{i}" for i in range(1, 17)]
_SEL_BG = 237


def _key(kind: str) -> SimpleNamespace:
    """构造按键事件（对齐 KeyEvent 属性访问语义）。"""
    return SimpleNamespace(kind=kind, char="", modifier=0, keycode=0, raw=b"")


# ── 1. 布局层：同文本 + style 变化必须失效换行缓存 ─────────

def _render_text_frame(rec, root, style: Style) -> object:
    element = h(
        BOX, {"width": 12},
        h(TEXT, {"children": "相同文本", "style": style, "height": 1}),
    )
    rec.render(root, element, 12, 4)
    return _components.render_frame(root, 12)


def test_same_text_style_change_updates_rendered_style():
    """文本不变、仅 style 变化：跨帧渲染（fiber 复用 + wrap 缓存）样式必须更新。

    修复前 ``_wrap_cache`` 值比较只比文本，命中后复用旧 ``StyledRun``——
    新样式不生效（user_select 多选高亮不跟随的直接根因）。
    """
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()

    frame = _render_text_frame(rec, root, Style(bg=1))
    assert frame.lines[0].plain == "相同文本"
    assert frame.lines[0].runs[0].style.bg == 1

    frame = _render_text_frame(rec, root, Style(bg=2))
    assert frame.lines[0].plain == "相同文本"
    assert frame.lines[0].runs[0].style.bg == 2, "文本相同但 style 变化时复用了旧样式"

    frame = _render_text_frame(rec, root, Style(bg=2))
    assert frame.lines[0].runs[0].style.bg == 2


def test_same_text_and_style_keeps_wrap_cache_reuse():
    """文本与样式均未变化时仍复用换行结果（性能优化不被修复破坏）。"""
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()

    _render_text_frame(rec, root, Style(bg=1))
    text_fiber = root.child.child  # BOX → TEXT
    cached_lines = text_fiber._wrapped_lines
    assert cached_lines is not None

    _render_text_frame(rec, root, Style(bg=1))
    assert text_fiber._wrapped_lines is cached_lines, "无变化帧不应重算换行"


# ── 2. user_select 端到端：↑↓ 高亮行跟随 ────────────────────

def _highlight_text(frame) -> str:
    """当前高亮选项行文本（整行背景 = 弹窗选中样式）。"""
    hits = []
    for line in frame.lines:
        style = line.runs[0].style if line.runs else None
        if style is not None and style.bg == _SEL_BG:
            hits.append(line.plain.strip())
    return hits[0] if hits else ""


def _build_popup(multi: bool, monkeypatch):
    """构建真实组件树（App）+ 弹窗状态，返回渲染器与按键 router。"""
    monkeypatch.setattr("src.tui.app.user_select._popup_item_rows", lambda: 21)
    model = AppModel()
    model.width = 80
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()

    def render() -> object:
        rec.render(root, h(App, {"model": model, "width": 80}), 80, 24)
        return _components.render_frame(root, 80)

    render()
    model.user_select = UserSelectState(
        visible=True, seq=1, title="选择城市", options=list(_OPTS),
        multi_select=multi, checked=[] if multi else None,
    )
    model.user_selects = [model.user_select]
    model.bottom_view = "user_select"
    render()
    router = rec._build_input_router(root)
    assert router is not None, "弹窗打开后应发布 composite input router"
    return model, render, router


def test_multi_select_arrow_down_moves_highlight(monkeypatch):
    """多选弹窗：↑↓ 切换选项时整行高亮跟随（修复前恒停在第 1 项）。"""
    model, render, router = _build_popup(True, monkeypatch)
    assert _highlight_text(render()) == "○  城市1"

    assert router(_key("arrow_down")) is True
    frame = render()
    assert "(2/16)" in next(ln.plain for ln in frame.lines if "选择城市" in ln.plain)
    assert _highlight_text(frame) == "○  城市2", "按下键后高亮应移动到第 2 项"

    assert router(_key("arrow_down")) is True
    assert _highlight_text(render()) == "○  城市3"

    assert router(_key("arrow_up")) is True
    frame = render()
    assert "(2/16)" in next(ln.plain for ln in frame.lines if "选择城市" in ln.plain)
    assert _highlight_text(frame) == "○  城市2", "按上键后高亮应回到第 2 项"
    # us.selected 与渲染高亮一致
    assert model.user_select.selected == 1


def test_multi_select_arrow_down_scrolls_into_view(monkeypatch):
    """多选弹窗：导航越过可见窗口时高亮行随窗口滚动仍可见。"""
    device = _build_popup(True, monkeypatch)
    model, render, router = device
    monkeypatch.setattr("src.tui.app.user_select._popup_item_rows", lambda: 3)

    # 刷新一帧使 limit=3 生效，然后连按 ↓ 越过首屏
    render()
    for _ in range(5):
        assert router(_key("arrow_down")) is True
    frame = render()
    assert _highlight_text(frame) == "○  城市6", "高亮应跟随滚动到第 6 项"
    assert model.user_select.selected == 5
    # 窗口滚动后首项不再渲染，第 6 项在可见区
    plains = [ln.plain.strip() for ln in frame.lines]
    assert "○  城市1" not in plains
    assert "○  城市6" in plains


def test_single_select_arrow_down_moves_highlight(monkeypatch):
    """单选弹窗上下键高亮跟随（既有行为回归锁定）。"""
    model, render, router = _build_popup(False, monkeypatch)
    assert _highlight_text(render()) == "▶  城市1"

    assert router(_key("arrow_down")) is True
    frame = render()
    assert _highlight_text(frame) == "▶  城市2"
    assert model.user_select.selected == 1

    assert router(_key("arrow_up")) is True
    assert _highlight_text(render()) == "▶  城市1"
