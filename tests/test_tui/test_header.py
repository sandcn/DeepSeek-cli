"""TopHeader 窄终端截断防御测试（M1）。

修复背景（2026-08-15 M1）：TopHeader 三个 TEXT（✦/渐变标题/版本号）在
Row(height=1) 中按内容宽度排列，极窄终端（width<26）总宽超宽 wraparound。
修复：TopHeader 接收 width prop（app.py:66 已传，修复前未使用），按预算
截断——优先级：✦（2 列）保留 > 渐变标题 > 版本号（最可丢弃）；预算为 0
时 runs 截断为空（TEXT 渲染空行零高度不影响 Row height=1）。

本测试锁定：width=80/26 不截断（内容完整）、width=20/10/1 总宽 <= width、
✦ 保留优先级（width=2 时仅 ✦）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.header import TopHeader, _title_runs, _version_runs
from src.tui.ink import hooks
from src.tui.ink.fiber import Fiber, TAG_FUNCTION
from src.tui.ink.element import Element


def _model_stub() -> SimpleNamespace:
    """最小模型桩：model.status.status_active=False（版本号走空闲静态）。"""
    return SimpleNamespace(status=SimpleNamespace(status_active=False))


def _render_header(width: int):
    """在 hook 环境下调用 TopHeader，返回元素树。

    TopHeader 用 ``use_memo``——组件外直接调用会抛 HookStateError
    （``use_* hook 只能在函数组件渲染期间调用``）；经 Fiber +
    ``hooks._push_current`` 注入 hook 环境（与 reconciler 渲染路径一致）。
    """
    props = {"model": _model_stub(), "width": width}
    f = Fiber(TAG_FUNCTION, TopHeader, dict(props))
    hooks._push_current(f)
    try:
        return TopHeader(props)
    finally:
        hooks._pop_current()


def _collect_text_widths(el) -> list:
    """遍历元素树，收集所有 TEXT 子节点 styled runs 总宽。

    ★ 全面控件化（方案B）：TopHeader 渐变标题经标准控件 ``Gradient``
    （函数组件）渲染——收集宽度时对函数组件调用其渲染函数展开（Gradient
    无 hooks，可安全直接调用），穿透组件层到 TEXT 子节点。
    """
    if el.type == "text":
        styled = el.props.get("styled")
        return [sum(r.width for r in styled)] if styled else [0]
    if callable(el.type) and not isinstance(el.type, str):
        # 函数组件（控件）：把 Element.children 注入 props 后调用渲染展开
        # （与 reconciler 渲染语义一致；仅限无 hooks 的展示控件）
        try:
            cp = dict(el.props)
            cp["children"] = getattr(el, "children", None) or ()
            sub = el.type(cp)
            if isinstance(sub, Element):
                return _collect_text_widths(sub)
            return [0]
        except Exception:
            return [0]
    out = []
    for child in el.children:
        out.extend(_collect_text_widths(child))
    return out


def _untruncated_widths() -> list:
    """未截断参考宽度（dot=2 + 渐变标题 + 版本号）。"""
    return [
        2,
        sum(r.width for r in _title_runs()),
        sum(r.width for r in _version_runs(False)),
    ]


def test_header_wide_no_truncate_regression():
    """M1 width=80 正常宽度：不触发截断（标题/版本完整，总宽 == 未截断宽度）。"""
    el = _render_header(80)
    widths = _collect_text_widths(el)
    assert widths == _untruncated_widths(), f"width=80 应不截断: {widths}"
    assert sum(widths) <= 80


def test_header_critical_width_regression():
    """M1 临界 width=26（内容总宽 < 26）：不截断。"""
    el = _render_header(26)
    widths = _collect_text_widths(el)
    assert widths == _untruncated_widths(), f"width=26 应不截断: {widths}"
    assert sum(widths) <= 26


def test_header_narrow_truncate_regression():
    """M1 width=20/10：总宽 <= width（版本号先消失、标题次之、✦ 保留）。"""
    for width in (20, 10):
        el = _render_header(width)
        widths = _collect_text_widths(el)
        total = sum(widths)
        assert total <= width, f"width={width} 总宽 {total} > {width}: {widths}"
        # ✦ 保留（dot 列 > 0）
        assert widths[0] >= 1, f"width={width} ✦ 应保留: {widths}"


def test_header_width_one_regression():
    """M1 width=1：总宽 <= 1（✦ 截断为单列，标题/版本为空）。"""
    el = _render_header(1)
    widths = _collect_text_widths(el)
    assert sum(widths) <= 1, f"width=1 总宽 {sum(widths)}: {widths}"


def test_header_dot_priority_regression():
    """M1 优先级：width=2 时仅 ✦（2 列）保留，标题/版本为空。"""
    el = _render_header(2)
    widths = _collect_text_widths(el)
    assert widths[0] == 2, f"width=2 ✦ 应完整保留: {widths}"
    assert widths[1] == 0 and widths[2] == 0, f"width=2 标题/版本应为空: {widths}"
    assert sum(widths) <= 2
