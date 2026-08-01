"""Fiber — 调和器工作单元（React 风格 fiber 节点）。

Fiber 表示组件树中一个待调和/已调和的节点，通过 child/sibling/return
指针构成链表树。alternate 指向上一次渲染的对应 fiber（用于复用 DOM /
保留 hooks 状态）。

fiber 的 ``layout_box`` 在 layout 阶段填充（LayoutBox(x,y,w,h)）。

零依赖：仅 typing（Layer 0）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

# ── fiber tag 常量 ─────────────────────────────────────────
TAG_ROOT = "root"
TAG_HOST = "host"
TAG_FUNCTION = "function"


@dataclass
class StateHook:
    """use_state / use_reducer hook 节点。

    Attributes:
        state: 当前状态值。
        queue: 待处理更新队列（列表，None 表示无）。
        reducer: use_reducer 传入的 reducer（None 表示 use_state）。
    """

    state: Any = None
    queue: list | None = None
    reducer: Callable[[Any, Any], Any] | None = None


@dataclass
class RefHook:
    """use_ref hook 节点。"""

    current: Any = None


@dataclass
class EffectHook:
    """use_effect hook 节点。

    Attributes:
        create: effect 创建函数（挂载/依赖变化时调用，返回销毁函数）。
        deps: 依赖列表。
        destroy: 上次的销毁函数。
        last_deps: 上次提交的依赖列表（用于检测变化）。
    """

    create: Any = None
    deps: Any = None
    destroy: Any = None
    last_deps: Any = None


#: hook 节点联合类型。
HookNode = StateHook | RefHook | EffectHook


@dataclass
class Fiber:
    """调和器工作单元。

    Attributes:
        tag: TAG_ROOT / TAG_HOST / TAG_FUNCTION。
        type: host 标签或 function component。
        props: 元素 props。
        child: 第一个子 fiber。
        sibling: 下一个兄弟 fiber。
        return_: 父 fiber。
        alternate: 上一次渲染的对应 fiber。
        hooks: hook 节点列表。
        hook_index: 当前 hook 索引（渲染期递增）。
        layout_box: layout 阶段填充的 LayoutBox（None 表示未布局）。
        deleted: 是否已标记删除（调和期）。
    """

    tag: str
    type: Any = None
    props: dict = field(default_factory=dict)
    child: Optional["Fiber"] = None
    sibling: Optional["Fiber"] = None
    return_: Optional["Fiber"] = None
    alternate: Optional["Fiber"] = None
    hooks: list = field(default_factory=list)
    hook_index: int = 0
    layout_box: Any = None
    deleted: bool = False

    # ── 派生属性 ──────────────────────────────────────

    @property
    def key(self) -> str:
        """fiber key（优先 props.key，否则按 type 派生）。"""
        key = self.props.get("key")
        if key is None:
            if isinstance(self.type, str):
                return f"host:{self.type}"
            return f"fn:{getattr(self.type, '__name__', repr(self.type))}"
        return str(key)

    @property
    def is_host(self) -> bool:
        return self.tag == TAG_HOST

    @property
    def is_function(self) -> bool:
        return self.tag == TAG_FUNCTION

    # ── hook 访问 ─────────────────────────────────────

    def reset_hooks(self) -> None:
        """渲染开始前重置 hook 索引。"""
        self.hook_index = 0

    def push_hook(self, hook: HookNode) -> HookNode:
        """记录当前 hook（返回之，供 hooks.py 使用）。"""
        idx = self.hook_index
        self.hook_index += 1
        if idx < len(self.hooks):
            self.hooks[idx] = hook
        else:
            self.hooks.append(hook)
        return hook

    def __repr__(self) -> str:  # pragma: no cover - 调试用
        return f"Fiber(tag={self.tag}, type={self.type!r})"


__all__ = [
    "TAG_ROOT",
    "TAG_HOST",
    "TAG_FUNCTION",
    "StateHook",
    "RefHook",
    "EffectHook",
    "HookNode",
    "Fiber",
]
