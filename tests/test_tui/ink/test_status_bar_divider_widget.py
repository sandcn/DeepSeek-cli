"""StatusBar 分隔线控件化测试（全面控件化方案B，2026-08-16）。

状态栏分隔线经标准控件 ``Divider`` 表达（纯填充分隔线——与 sep_line
构建语义等价，控件化表达）；状态行保持 TEXT（React Ink 基础控件）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.status_bar import StatusBar
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber
from src.tui.ink.widgets.display import Divider


def _model_stub(status_active: bool = False, **status_kw) -> SimpleNamespace:
    st = {"status_active": status_active, "model_name": "test-model"}
    st.update(status_kw)
    return SimpleNamespace(status=SimpleNamespace(**st))


def _render(component, props, fiber=None):
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def test_status_bar_divider_control():
    """StatusBar 分隔线经 Divider 控件表达（Column 首子元素）。"""
    model = _model_stub(status_active=False)
    el, _ = _render(StatusBar, {"model": model, "width": 80})
    # Column 根：Divider + 状态行 TEXT
    assert el.type.__name__ == "Column"
    children = list(el.children)
    assert len(children) == 2
    divider = children[0]
    assert divider.type is Divider, f"分隔线应为 Divider 控件: {divider.type}"
    dprops = divider.props
    assert dprops["width"] == 80
    assert dprops["char"] == "\u2501"  # 与 sep_line 分隔字符一致
    assert dprops["style"] is not None


def test_status_bar_divider_active_style():
    """活跃状态：Divider style 为呼吸色（非静态 _S_SEP）。"""
    from src.tui.app._theme import _S_SEP
    model = _model_stub(status_active=True)
    el, _ = _render(StatusBar, {"model": model, "width": 80})
    divider = list(el.children)[0]
    assert divider.type is Divider
    style = divider.props["style"]
    assert style is not None
    # 活跃时呼吸色（sep_style(True) 动态 Style——与静态 _S_SEP 不同对象）
    assert style is not _S_SEP or style.fg != _S_SEP.fg


def test_status_bar_empty_state_single_divider():
    """空状态（无模型名/统计）：仅 Divider 一行（Divider 控件）。"""
    model = _model_stub(status_active=False, model_name="")
    el, _ = _render(StatusBar, {"model": model, "width": 80})
    children = list(el.children)
    assert len(children) == 1
    assert children[0].type is Divider
