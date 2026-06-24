"""焦点管理系统 — 键盘焦点遍历与焦点 ID 系统。

提供 use_focus / use_focus_manager hooks，支持：
  - Tab / Shift+Tab 焦点遍历（正向/反向循环）
  - autoFocus 属性自动获取初始焦点
  - 焦点 ID 系统（按 ID 精确聚焦）
  - 焦点可见指示器

依赖 Hooks 运行时（_hooks.py）和焦点状态类型（_types.py）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from .hooks import use_effect, use_state, get_hooks_runtime


# ── 焦点条目数据结构 ──────────────────────────────────


@dataclass
class _FocusableEntry:
    """焦点条目 — 记录单个可聚焦组件的元数据。

    Attributes:
        component: 组件实例引用。
        is_focused: 当前是否持有焦点。
        is_active: 是否可聚焦（False 时跳过遍历）。
        auto_focus: 注册后是否自动获取焦点。
        on_focus: 获得焦点时的回调。
        on_blur: 失去焦点时的回调。
    """
    component: Any
    is_focused: bool = False
    is_active: bool = True
    auto_focus: bool = False
    on_focus: Callable[[], None] | None = None
    on_blur: Callable[[], None] | None = None


# ── FocusManager 单例 ───────────────────────────────────


class FocusManager:
    """全局焦点管理器。

    管理焦点组件的注册、注销和遍历。
    由 use_focus hook 自动注册组件。
    使用单例模式 — 多次调用 FocusManager() 返回同一实例。

    Attributes:
        _focusables: 焦点条目字典 (id → _FocusableEntry)。
        _order: 焦点遍历顺序列表。
        _active_id: 当前持有焦点的组件 ID。
        _enabled: 焦点管理是否启用。
    """

    _instance: FocusManager | None = None

    def __new__(cls) -> FocusManager:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        """初始化内部状态（仅在首次创建单例时调用）。"""
        self._focusables: dict[str, _FocusableEntry] = {}
        self._order: list[str] = []
        self._active_id: str | None = None
        self._enabled: bool = True

    # ── 注册/注销 ─────────────────────────────────

    def register(self, component_id: str, entry: _FocusableEntry) -> None:
        """注册可聚焦组件。

        Args:
            component_id: 组件唯一标识。
            entry: 焦点条目元数据（包含组件引用和回调）。
        """
        if component_id in self._focusables:
            return  # 避免重复注册

        # 从 hooks runtime 获取当前组件引用
        runtime = get_hooks_runtime()
        if runtime._current_component is not None:
            entry.component = runtime._current_component

        self._focusables[component_id] = entry
        self._order.append(component_id)

        # autoFocus 且当前无焦点时自动聚焦
        if entry.auto_focus and self._active_id is None and entry.is_active:
            self.focus(component_id)

    def unregister(self, component_id: str) -> None:
        """注销组件。

        若被注销组件当前持有焦点，先执行 blur 回调再清除焦点。

        Args:
            component_id: 组件唯一标识。
        """
        if component_id not in self._focusables:
            return

        entry = self._focusables[component_id]
        # 如果当前焦点是该组件，清除焦点
        if self._active_id == component_id:
            entry.is_focused = False
            if entry.on_blur is not None:
                try:
                    entry.on_blur()
                except Exception:
                    pass
            self._active_id = None
        del self._focusables[component_id]
        if component_id in self._order:
            self._order.remove(component_id)

    # ── 焦点操作 ─────────────────────────────────

    def focus(self, id: str) -> None:
        """聚焦到指定 ID 的组件。

        先执行当前焦点组件的 on_blur 回调，再执行目标组件的 on_focus 回调。

        Args:
            id: 目标组件 ID。
        """
        if not self._enabled:
            return
        if id not in self._focusables:
            return
        entry = self._focusables[id]
        if not entry.is_active:
            return
        if self._active_id == id:
            return  # 已经是当前焦点

        # 先 blur 当前焦点组件
        if self._active_id is not None and self._active_id in self._focusables:
            old = self._focusables[self._active_id]
            old.is_focused = False
            if old.on_blur is not None:
                try:
                    old.on_blur()
                except Exception:
                    pass

        # 聚焦新组件
        self._active_id = id
        entry.is_focused = True
        if entry.on_focus is not None:
            try:
                entry.on_focus()
            except Exception:
                pass

    def focus_next(self) -> None:
        """聚焦到下一个可聚焦组件（Tab 正向循环）。

        仅在 _enabled=True 且有可聚焦组件时生效。
        无当前焦点时聚焦到第一个可聚焦组件。
        """
        if not self._enabled or not self._order:
            return

        active_ids = [
            fid for fid in self._order
            if fid in self._focusables and self._focusables[fid].is_active
        ]
        if not active_ids:
            return

        if self._active_id is None or self._active_id not in active_ids:
            self.focus(active_ids[0])
            return

        idx = active_ids.index(self._active_id)
        next_idx = (idx + 1) % len(active_ids)
        self.focus(active_ids[next_idx])

    def focus_previous(self) -> None:
        """聚焦到上一个可聚焦组件（Shift+Tab 反向循环）。

        仅在 _enabled=True 且有可聚焦组件时生效。
        无当前焦点时聚焦到最后一个可聚焦组件。
        """
        if not self._enabled or not self._order:
            return

        active_ids = [
            fid for fid in self._order
            if fid in self._focusables and self._focusables[fid].is_active
        ]
        if not active_ids:
            return

        if self._active_id is None or self._active_id not in active_ids:
            self.focus(active_ids[-1])
            return

        idx = active_ids.index(self._active_id)
        prev_idx = (idx - 1) % len(active_ids)
        self.focus(active_ids[prev_idx])

    # ── 启用/禁用 ─────────────────────────────────

    def enable(self) -> None:
        """启用焦点管理。"""
        self._enabled = True

    def disable(self) -> None:
        """禁用焦点管理（保留当前焦点状态但不响应遍历操作）。"""
        self._enabled = False

    # ── 查询属性 ─────────────────────────────────

    @property
    def active_id(self) -> str | None:
        """当前焦点组件的 ID，无焦点时返回 None。"""
        return self._active_id

    @property
    def active_component(self) -> Any | None:
        """当前焦点组件实例，无焦点时返回 None。"""
        if self._active_id is not None and self._active_id in self._focusables:
            return self._focusables[self._active_id].component
        return None

    @property
    def enabled(self) -> bool:
        """焦点管理是否启用。"""
        return self._enabled

    @property
    def has_focusables(self) -> bool:
        """是否有已注册的可聚焦组件。"""
        return len(self._focusables) > 0


# ── use_focus Hook ──────────────────────────────────────


def use_focus(options: dict | None = None) -> dict:
    """组件焦点 Hook — 使组件可聚焦。

    使用 use_state 追踪 isFocused 状态，use_effect 处理注册/注销生命周期。
    组件 mount 时注册到 FocusManager，unmount 时自动注销。

    Args:
        options: {
            "autoFocus": bool,      # 自动获取焦点（默认 False）
            "isActive": bool,       # 是否可聚焦（默认 True）
            "id": str,              # 焦点 ID（未提供时自动生成）
            "onFocus": Callable,    # 获得焦点时的回调
            "onBlur": Callable,     # 失去焦点时的回调
        }

    Returns:
        {
            "isFocused": bool,    # 当前是否持有焦点
            "focus": Callable,    # 主动聚焦当前组件
            "blur": Callable,     # 主动取消当前组件焦点
        }

    Raises:
        HookError: 在组件 render 上下文外调用时。
    """
    opts = options or {}
    comp_id: str = opts.get("id", "")
    auto_focus: bool = opts.get("autoFocus", False)
    is_active: bool = opts.get("isActive", True)
    on_focus_user: Callable[[], None] | None = opts.get("onFocus")
    on_blur_user: Callable[[], None] | None = opts.get("onBlur")

    # 未提供 id 时基于组件对象 id() 自动生成
    if not comp_id:
        runtime = get_hooks_runtime()
        comp = runtime._current_component
        comp_id = f"comp_{id(comp)}"

    fm = FocusManager()

    # ── 追踪 isFocused 状态 ──
    is_focused, set_focused = use_state(False)

    # ── 注册/注销生命周期 ──
    def _register_effect() -> Callable[[], None]:
        """mount 时注册，返回 unmount 清理函数。"""

        # 包装用户回调，同时更新本地状态
        def _on_focus() -> None:
            set_focused(True)
            if on_focus_user is not None:
                on_focus_user()

        def _on_blur() -> None:
            set_focused(False)
            if on_blur_user is not None:
                on_blur_user()

        entry = _FocusableEntry(
            component=None,  # 组件引用由 FocusManager 管理
            is_focused=False,
            is_active=is_active,
            auto_focus=auto_focus,
            on_focus=_on_focus,
            on_blur=_on_blur,
        )
        fm.register(comp_id, entry)

        # autoFocus 时触发焦点获取
        if auto_focus:
            fm.focus(comp_id)

        def _cleanup() -> None:
            fm.unregister(comp_id)
            set_focused(False)

        return _cleanup

    use_effect(_register_effect, [is_active, auto_focus])

    # ── 同步状态：若 FocusManager 与本地状态不一致，以 FocusManager 为准 ──
    actual_focused = (fm.active_id == comp_id)
    if actual_focused != is_focused:
        # 本地状态滞后，通过 setter 修正（触发重渲染）
        set_focused(actual_focused)

    def focus() -> None:
        """主动聚焦当前组件。"""
        fm.focus(comp_id)

    def blur() -> None:
        """主动取消当前组件焦点（移到下一个或清除）。"""
        if fm.active_id == comp_id:
            fm.focus_next()

    return {
        "isFocused": is_focused,
        "focus": focus,
        "blur": blur,
    }


# ── use_focus_manager Hook ──────────────────────────────


def use_focus_manager() -> dict:
    """全局焦点管理 Hook — 获取全局 FocusManager 的操作接口。

    返回的操作方法直接绑定到 FocusManager 单例，不绑定特定组件。
    可在任意组件中调用以执行全局焦点操作。

    Returns:
        {
            "focusNext": Callable,         # Tab 正向遍历
            "focusPrevious": Callable,     # Shift+Tab 反向遍历
            "focus": Callable[[str], None],  # 聚焦到指定 ID
            "activeId": str | None,        # 当前焦点 ID
            "enableFocus": Callable,       # 启用焦点管理
            "disableFocus": Callable,      # 禁用焦点管理
        }
    """
    fm = FocusManager()
    return {
        "focusNext": fm.focus_next,
        "focusPrevious": fm.focus_previous,
        "focus": fm.focus,
        "activeId": fm.active_id,
        "enableFocus": fm.enable,
        "disableFocus": fm.disable,
    }
