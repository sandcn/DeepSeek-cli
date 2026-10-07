"""editmsg 第二批增强测试（2026-10-07 用户需求：更好的显示 / 操作 / 更多功能）。

覆盖：
  1. 搜索命中子串高亮（``_highlight_runs`` + 弹窗列表渲染）；
  2. 预览区 markdown 渲染（``_preview_rows`` + 缓存）；
  3. 预览区滚动（``[``/``]`` + ``es.preview_scroll``）；
  4. 弹窗内搜索计数（标题 (n/total) 与过滤标注）。

风格对齐既有 ``test_editmsg_*.py``：手动 fiber 上下文渲染 + handler 直调。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel, EditMsgSelectState
from src.tui.app.editmsg_select import (
    EditMsgSelectPopup,
    _filter_indices,
    _highlight_runs,
    _preview_rows,
    _preview_rows_cache,
)
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber, InputHook

# ═══════════════════════════════════════════════════════════
# 通用辅助
# ═══════════════════════════════════════════════════════════


def _render_popup(model, width=80):
    fiber = Fiber(TAG_FUNCTION, EditMsgSelectPopup, {"model": model, "width": width})
    hooks._push_current(fiber)
    try:
        el = EditMsgSelectPopup({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _input_handler(fiber):
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(
        kind=kind, char=char, modifier=0, keycode=0, raw=b"",
        kitty_bits=-1, event_type="",
    )


def _texts(el) -> list:
    return [str(c.props.get("children", "")) for c in el.children]


def _collect_styled(el) -> list:
    """递归收集元素树中的 (text, style) run。"""
    out: list = []

    def walk(node):
        for c in getattr(node, "children", None) or []:
            styled = c.props.get("styled")
            if styled:
                for r in styled:
                    out.append((r.text, r.style))
            walk(c)

    walk(el)
    return out


# ═══════════════════════════════════════════════════════════
# 1. 搜索命中子串高亮
# ═══════════════════════════════════════════════════════════


class TestHighlightRuns:

    def test_no_query_single_run(self):
        runs = _highlight_runs("hello", "")
        assert len(runs) == 1 and runs[0].text == "hello"

    def test_match_split(self):
        from src.tui.app.editmsg_select import _S_HIT, _S_ITEM
        runs = _highlight_runs("hello world", "world", _S_ITEM, _S_HIT)
        assert "".join(r.text for r in runs) == "hello world"
        assert any(r.style is _S_HIT and r.text == "world" for r in runs)

    def test_case_insensitive(self):
        from src.tui.app.editmsg_select import _S_HIT
        runs = _highlight_runs("Hello", "he", None, _S_HIT)
        assert any(r.style is _S_HIT and r.text == "He" for r in runs)

    def test_no_match(self):
        from src.tui.app.editmsg_select import _S_HIT
        runs = _highlight_runs("abc", "zzz", None, _S_HIT)
        assert not any(r.style is _S_HIT for r in runs)

    def test_multiple_matches(self):
        from src.tui.app.editmsg_select import _S_HIT
        runs = _highlight_runs("aXbXc", "x", None, _S_HIT)
        hits = [r.text for r in runs if r.style is _S_HIT]
        assert hits == ["X", "X"]

    def test_render_highlights(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="t",
            options=["alpha", "beta"], selected=0, filter="bet",
        )
        _fiber, el = _render_popup(model)
        render_item = None
        for c in el.children:
            if c.props.get("renderItem"):
                render_item = c.props["renderItem"]
        assert render_item is not None
        child = render_item({"label": "beta", "value": 1}, 0, True)
        styled = child.props.get("styled")
        assert any(r.style is not None and r.style.fg == 214 and "bet" in r.text
                   for r in styled)


# ═══════════════════════════════════════════════════════════
# 2. 预览区 markdown 渲染
# ═══════════════════════════════════════════════════════════


class TestPreviewRows:

    def test_rows_non_empty(self):
        rows = _preview_rows("**bold** text", 40)
        assert rows
        text = "".join(r.text for row in rows for r in row)
        assert "bold" in text

    def test_plain_fallback(self):
        rows = _preview_rows("plain line", 40)
        assert rows
        assert any("plain line" in r.text for row in rows for r in row)

    def test_cache_identity(self):
        _preview_rows_cache.clear()
        r1 = _preview_rows("cached-text", 40)
        r2 = _preview_rows("cached-text", 40)
        assert r1 is r2

    def test_code_block(self):
        rows = _preview_rows("```python\nprint(1)\n```", 60)
        text = "".join(r.text for row in rows for r in row)
        assert "print(1)" in text


# ═══════════════════════════════════════════════════════════
# 3. 预览区滚动
# ═══════════════════════════════════════════════════════════


class TestPreviewScroll:

    def _model(self, previews):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="t", options=["a", "b"],
            previews=previews, selected=0,
        )
        return model

    def test_scroll_down(self):
        long_preview = "\n".join(f"line{i}" for i in range(60))
        model = self._model([long_preview, long_preview])
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("char", "]")) is True
        assert model.editmsg_select.preview_scroll == 3

    def test_scroll_up_at_top(self):
        long_preview = "\n".join(f"line{i}" for i in range(60))
        model = self._model([long_preview, long_preview])
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("char", "[")) is False

    def test_scroll_down_then_up(self):
        long_preview = "\n".join(f"line{i}" for i in range(60))
        model = self._model([long_preview, long_preview])
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        handler(_ev("char", "]"))
        assert handler(_ev("char", "[")) is True
        assert model.editmsg_select.preview_scroll == 0

    def test_scroll_no_preview(self):
        model = self._model([])
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("char", "]")) is False

    def test_short_preview_not_scrollable(self):
        model = self._model(["one line", "one line"])
        fiber, _el = _render_popup(model)
        handler = _input_handler(fiber)
        assert handler(_ev("char", "]")) is False


# ═══════════════════════════════════════════════════════════
# 4. 过滤计数
# ═══════════════════════════════════════════════════════════


class TestFilterCount:

    def test_filter_indices_core(self):
        assert _filter_indices(["a", "b"], "") == [0, 1]
        assert _filter_indices(["Hello", "world"], "he") == [0]

    def test_title_count_with_filter(self):
        model = AppModel()
        model.editmsg_select = EditMsgSelectState(
            visible=True, seq=1, title="选择",
            options=["alpha", "beta", "gamma"], selected=0, filter="a",
        )
        _fiber, el = _render_popup(model)
        texts = "\n".join(_texts(el))
        assert "\u8fc7\u6ee4 3/3" in texts
