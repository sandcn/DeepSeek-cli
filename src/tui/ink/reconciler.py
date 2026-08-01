"""调和器 — 挂载/更新 fiber 树 + effect 队列。

React 风格调和：
  1. begin_work：函数组件调用组件函数得到渲染元素；host 组件调和子元素。
  2. 按 key/type 调和子列表（复用同 key/type 的旧 fiber，保留 hooks 状态）。
  3. 整棵调和完成后提交 effects：先跑被删除子树的销毁函数，再跑依赖变化的
     effect（先销毁后创建）。
  4. 布局阶段（layout_tree）为每个 host fiber 填充 LayoutBox。

调用期绑定：渲染函数组件期间，``hooks`` 模块的 ``use_*`` 读取
``_current_fiber_stack`` 顶部的当前 fiber（由 begin_work push/pop）。
"""

from __future__ import annotations

import logging
from typing import Callable

from .fiber import (
    Fiber,
    TAG_ROOT,
    TAG_FUNCTION,
    EffectHook,
    InputHook,
)
from .element import Element
from . import hooks as _hooks
from . import layout as _layout

_logger = logging.getLogger(__name__)


def _is_same_type(old_fiber: Fiber, element: Element) -> bool:
    """判断旧 fiber 与新元素 type 是否相同（决定是否复用）。"""
    old_t = old_fiber.type
    new_t = element.type
    if isinstance(old_t, str) or isinstance(new_t, str):
        return old_t == new_t
    return old_t is new_t


class Reconciler:
    """组件树调和器。

    Args:
        schedule_callback: 状态更新触发重渲染的回调（session 注入）。
    """

    def __init__(self, schedule_callback: Callable[[], None] | None = None) -> None:
        self._schedule_callback = schedule_callback
        self._pending_destroys: list[EffectHook] = []
        _hooks.set_schedule_callback(schedule_callback)

    def render(
        self,
        root_fiber: Fiber,
        element: Element,
        width: int,
        height: int,
    ) -> None:
        """调和 + 布局 + 提交 effects。

        Args:
            root_fiber: 根 fiber（TAG_ROOT，挂载时由 ``create_root`` 创建）。
            element: 根元素（App）。
            width: 文档宽度。
            height: 文档高度（预留给未来视口约束；当前内容驱动）。
        """
        self._pending_destroys = []
        _hooks.set_schedule_callback(self._schedule_callback)
        # 调和 root 的子元素
        self._reconcile_children(root_fiber, [element])
        # 布局（host 树）
        _layout.layout_tree(root_fiber, width)
        # 提交 effects：先销毁（删除子树），再创建（依赖变化）
        for hook in self._pending_destroys:
            self._run_destroy(hook)
        self._pending_destroys = []
        self._run_live_effects(root_fiber)
        # ★ 发布 composite input router（use_input 钩子，INK-1）
        router = self._build_input_router(root_fiber)
        _hooks._publish_input_router(router)

    # ── 挂载 ────────────────────────────────────────

    @staticmethod
    def create_root() -> Fiber:
        """创建空根 fiber。"""
        return Fiber(TAG_ROOT, props={})

    # ── 调和 ────────────────────────────────────────

    def _reconcile_children(self, return_fiber: Fiber, elements: list[Element]) -> None:
        """调和 return_fiber 的子元素列表（按 key/type diff 子 sibling 链）。"""
        existing_map: dict[str, Fiber] = {}
        child = return_fiber.child
        while child is not None:
            existing_map[child.key] = child
            child = child.sibling

        first: Fiber | None = None
        prev: Fiber | None = None
        for element in elements:
            key = element.key
            old = existing_map.pop(key, None)
            if old is not None and _is_same_type(old, element):
                fiber = old
                fiber.props = dict(element.props)
                fiber.deleted = False
                fiber.return_ = return_fiber
                self._begin_work(fiber, element)
            else:
                if old is not None:
                    self._mark_deleted(old)
                fiber = self._create_and_begin(element, return_fiber)
            # ★ 清除旧 sibling 链——复用 fiber 若保留旧 sibling 指针会形成环
            fiber.sibling = None
            if prev is not None:
                prev.sibling = fiber
            else:
                first = fiber
            prev = fiber
        for old in existing_map.values():
            self._mark_deleted(old)
        return_fiber.child = first

    def _reconcile_single(
        self,
        return_fiber: Fiber,
        existing: Fiber | None,
        element: Element,
    ) -> Fiber:
        """调和单个元素（函数组件渲染输出）。"""
        if existing is not None and _is_same_type(existing, element):
            existing.props = dict(element.props)
            existing.deleted = False
            existing.return_ = return_fiber
            existing.sibling = None
            self._begin_work(existing, element)
            return existing
        if existing is not None:
            self._mark_deleted(existing)
        return self._create_and_begin(element, return_fiber)

    def _create_and_begin(self, element: Element, return_fiber: Fiber) -> Fiber:
        """创建新 fiber 并 begin_work。"""
        if callable(element.type):
            tag = TAG_FUNCTION
        else:
            tag = "host"
        fiber = Fiber(tag, element.type, dict(element.props), return_=return_fiber)
        self._begin_work(fiber, element)
        return fiber

    def _begin_work(self, fiber: Fiber, element: Element) -> None:
        """开始处理一个 fiber：函数组件调用渲染函数；host 调和子元素。"""
        if fiber.is_function:
            fiber.reset_hooks()
            _hooks._push_current(fiber)
            try:
                rendered = fiber.type(fiber.props)
            finally:
                _hooks._pop_current()
            if rendered is None:
                rendered = Element("text", {"children": ""}, ())
            elif not isinstance(rendered, Element):
                rendered = Element("text", {"children": str(rendered)}, ())
            fiber.child = self._reconcile_single(fiber, fiber.child, rendered)
        else:
            # ★ context provider：先重置 contexts（每次渲染不残留旧值），
            #   再按 host 标签匹配注册的 Context（INK-3）——value 写入
            #   fiber.contexts（键为 ctx.tag 唯一标签）供子树 use_context
            #   沿 return_ 链查找。
            fiber.contexts.clear()
            ftype = fiber.type
            if isinstance(ftype, str):
                ctx = _hooks._context_registry.get(ftype)
                if ctx is not None:
                    fiber.contexts[ctx.tag] = fiber.props.get("value", ctx.default)
            children = list(element.children)
            self._reconcile_children(fiber, children)

    # ── input router 构建（INK-1） ─────────────────────

    def _build_input_router(self, root_fiber: Fiber):
        """前序遍历收集 active InputHook，构建 composite router。

        无 active hooks 时返回 None（输入走旧路径，零行为变化）。
        Router 按 hook 顺序调用各 handler；任一返回 True 视为消费（返回 True）；
        全部未消费返回 False（放行旧路径）；handler 异常视为未消费（放行）。
        """
        hooks_list: list[InputHook] = []
        self._collect_input_hooks(root_fiber, hooks_list)
        if not hooks_list:
            return None

        def router(event) -> bool:
            for hook in hooks_list:
                try:
                    if hook.handler is not None and hook.handler(event):
                        return True
                except Exception:
                    continue
            return False

        return router

    def _collect_input_hooks(self, fiber: Fiber | None, out: list[InputHook]) -> None:
        """前序遍历 fiber 树，收集 active 且已设 handler 的 InputHook（跳过已删除）。"""
        f = fiber
        while f is not None:
            if f.deleted:
                f = f.sibling
                continue
            if f.is_function:
                for hook in f.hooks:
                    if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
                        out.append(hook)
            self._collect_input_hooks(f.child, out)
            f = f.sibling

    def _mark_deleted(self, fiber: Fiber) -> None:
        """标记子树删除（收集其 effect 销毁函数）。"""
        fiber.deleted = True
        self._traverse_functions(fiber, self._queue_destroys)

    def _queue_destroys(self, fiber: Fiber) -> None:
        for hook in fiber.hooks:
            if isinstance(hook, EffectHook) and hook.destroy is not None:
                self._pending_destroys.append(hook)

    # ── effects 提交 ────────────────────────────────

    def _run_destroy(self, hook: EffectHook) -> None:
        try:
            if hook.destroy is not None:
                hook.destroy()
            hook.destroy = None
            hook.last_deps = None
        except Exception:
            _logger.debug("effect 销毁执行异常", exc_info=True)

    def _run_live_effects(self, root: Fiber) -> None:
        """遍历活树，提交依赖变化的 effect。"""
        self._traverse_functions(root, self._commit_live)

    def _commit_live(self, fiber: Fiber) -> None:
        for hook in fiber.hooks:
            if not isinstance(hook, EffectHook):
                continue
            if hook.create is None and hook.destroy is None:
                continue
            if not _hooks.deps_changed(hook):
                continue
            try:
                if hook.destroy is not None:
                    hook.destroy()
                hook.destroy = None
                result = hook.create() if hook.create is not None else None
                if callable(result):
                    hook.destroy = result
                _hooks.mark_effect_committed(hook)
            except Exception:
                _logger.debug("effect 执行异常", exc_info=True)

    def _traverse_functions(
        self,
        fiber: Fiber | None,
        cb: Callable[[Fiber], None],
    ) -> None:
        """前序遍历 fiber 树，对 function fiber 调用 cb（跳过已删除）。"""
        f = fiber
        while f is not None:
            if f.deleted:
                f = f.sibling
                continue
            if f.is_function:
                cb(f)
            self._traverse_functions(f.child, cb)
            f = f.sibling


__all__ = ["Reconciler"]
