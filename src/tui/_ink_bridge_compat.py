"""InkBridge 兼容访问器域（生命周期 no-op + _BottomBar 内部字段）。

方向C 步骤8：从 ``_ink_bridge.py`` 拆出的兼容访问器 mixin。

职责域：
  - 生命周期 no-op：``setup`` / ``teardown`` / ``is_active`` / ``set_active`` /
    ``ensure_cursor_in_upper`` / ``ensure_cursor_in_lower``
    （非全屏流动模型无 DECSTBM，全部 no-op）
  - _BottomBar 内部字段兼容：``_last_text`` / ``_last_rendered_text`` /
    ``_bottom_lines`` / ``_last_bottom_lines`` / ``_last_scroll_end`` /
    ``_completion_idx`` / ``_completion`` / ``force_redraw`` / ``_MIN_HEIGHT``

依赖约束：mixin 仅依赖 ``self._model``（AppModel 引用）与
``self._request_redraw()``（重绘请求），两者由 ``InkBridge`` 提供；
不依赖其他桥接域（状态/补全/输入/子代理）。

注意：``_CompletionProxy`` 随 mixin 迁移（外部无直接引用），
``_ink_bridge`` 模块 re-export 保持路径兼容。属性 setter 副作用逐项保留
（``_last_text`` / ``_last_rendered_text`` / ``_completion_idx`` setter 触发
``_request_redraw``）。
"""

from __future__ import annotations


class _CompletionProxy:
    """兼容 _BottomBar._completion 内部字段访问（user_select 等直接读写）。"""

    def __init__(self, model):
        self._model = model

    @property
    def _visible(self) -> bool:
        return self._model.completion.visible

    @_visible.setter
    def _visible(self, value: bool) -> None:
        self._model.completion.visible = bool(value)

    @property
    def _popup_height(self) -> int:
        return self._model.completion.popup_height

    @_popup_height.setter
    def _popup_height(self, value: int) -> None:
        self._model.completion.popup_height = int(value)

    @property
    def _items(self) -> list:
        return self._model.completion.items

    @_items.setter
    def _items(self, value: list) -> None:
        self._model.completion.items = list(value)

    @property
    def _texts(self) -> list:
        return self._model.completion.texts

    @_texts.setter
    def _texts(self, value: list) -> None:
        self._model.completion.texts = list(value)


class _BottomBarCompatMixin:
    """兼容访问器 mixin（生命周期 no-op + _BottomBar 内部字段）。

    混入 ``InkBridge``（须提供 ``_model`` / ``_request_redraw()``）。
    """

    # 兼容 user_select 的 _MIN_HEIGHT 检查（is_active 恒 True，实际不会走到）
    _MIN_HEIGHT = 12

    # ── 兼容 _BottomBar 内部字段（user_select 等直接读写） ──

    @property
    def _last_text(self) -> str:
        return self._model.input_text

    @_last_text.setter
    def _last_text(self, value: str) -> None:
        self._model.input_text = value
        self._request_redraw()

    @property
    def _last_rendered_text(self) -> str:
        return self._model.input_text

    @_last_rendered_text.setter
    def _last_rendered_text(self, value: str) -> None:
        self._model.input_text = value
        self._request_redraw()

    @property
    def _bottom_lines(self) -> int:
        """非全屏模型无 DECSTBM：返回输入区近似行数（兼容访问）。"""
        return 5

    @property
    def _last_bottom_lines(self) -> int:
        return 5

    @_last_bottom_lines.setter
    def _last_bottom_lines(self, value: int) -> None:
        pass

    @property
    def _last_scroll_end(self) -> int:
        return 0

    @_last_scroll_end.setter
    def _last_scroll_end(self, value: int) -> None:
        pass

    @property
    def _completion_idx(self) -> int:
        return self._model.completion.selected

    @_completion_idx.setter
    def _completion_idx(self, value: int) -> None:
        self._model.completion.selected = int(value)
        self._request_redraw()

    @property
    def _completion(self) -> "_CompletionProxy":
        """兼容 _completion._visible/_popup_height/_items/_texts 访问。"""
        return _CompletionProxy(self._model)

    def force_redraw(self) -> None:
        """强制重绘（非全屏模型：请求下一帧渲染）。"""
        self._request_redraw()

    # ── 生命周期 no-op ─────────────────────────────

    @property
    def is_active(self) -> bool:
        return True

    def set_active(self, active: bool) -> None:
        pass

    def setup(self) -> None:
        pass

    def teardown(self) -> None:
        pass

    def ensure_cursor_in_upper(self) -> None:
        pass

    def ensure_cursor_in_lower(self) -> None:
        pass


__all__ = ["_BottomBarCompatMixin", "_CompletionProxy"]
