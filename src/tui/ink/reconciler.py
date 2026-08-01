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
    StateHook,
    EffectHook,
    InputHook,
    _MISSING,
)
from .element import Element
from .error_boundary import _build_fallback_element
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


def _safe_eq(a, b) -> bool:
    """安全相等比较（provider 值变更检测用）。

    值比较抛异常（不可比较对象）时视为不等——重清缓存，安全侧。
    """
    try:
        return a == b
    except Exception:
        return False


def _clear_context_cache_subtree(fiber: Fiber | None) -> None:
    """遍历子树清空各 fiber 的 ``_context_cache``（provider 值变化时调用，低频）。

    只清空缓存 dict 与版本标记（置 0），不改变 contexts 内容；误清只导致
    多一次沿 return_ 链查找，无正确性风险。版本号经 ``_hooks._bump_context_version``
    同步递增（版本号与 contexts 内容解耦，见 hooks.use_context）。
    """
    f = fiber
    while f is not None:
        f._context_cache.clear()
        f._context_cache_version = 0
        _clear_context_cache_subtree(f.child)
        f = f.sibling


class Reconciler:
    """组件树调和器。

    Args:
        schedule_callback: 状态更新触发重渲染的回调（session 注入）。
    """

    def __init__(self, schedule_callback: Callable[[], None] | None = None) -> None:
        self._schedule_callback = schedule_callback
        self._pending_destroys: list[tuple[Fiber, EffectHook]] = []
        #: input router 签名缓存（同签名复用上次 router，免每帧重建闭包）
        self._input_router_cache: tuple[tuple, object] | None = None
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
        for fiber, hook in self._pending_destroys:
            self._run_destroy(fiber, hook)
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
        """调和 return_fiber 的子元素列表（按 key/type diff 子 sibling 链）。

        方向B 步骤11：记录旧 sibling 链各 fiber 的旧位置索引
        （``old_index_map[key] = idx``）；复用 fiber 时比较旧/新位置，
        不同则置 ``fiber.moved = True``（keyed 列表重排信息，纯信息标记）。
        """
        existing_map: dict[str, Fiber] = {}
        old_index_map: dict[str, int] = {}
        child = return_fiber.child
        idx = 0
        while child is not None:
            existing_map[child.key] = child
            old_index_map[child.key] = idx
            child = child.sibling
            idx += 1

        first: Fiber | None = None
        prev: Fiber | None = None
        for new_idx, element in enumerate(elements):
            key = element.key
            old = existing_map.pop(key, None)
            if old is not None and _is_same_type(old, element):
                fiber = old
                # ★ moved 标记：旧位置 != 新位置 → True（每帧重算，非累计）
                fiber.moved = old_index_map.get(key) != new_idx
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
        """开始处理一个 fiber：函数组件调用渲染函数；host 调和子元素。

        函数组件渲染异常处理（方向B 步骤9）：
          - HookStateError（hook 顺序/类型错误，编程错误）→ 直接 re-raise 传播；
          - 其他 Exception → 沿 return_ 链查找最近 ``_is_boundary`` fiber：
              找到 → 记录 ``_boundary_error`` + onError 回调一次 + 本帧渲染 fallback；
              未找到 → re-raise（保持既有崩溃恢复路径）。

        memo 短路（方向B 步骤10）：props 未变且无待处理 state 更新时跳过
        组件调用与子树重建（保留 ``fiber.child``），``reset_hooks`` 仍执行
        保持 hook 一致性。

        context 选择性重渲染评估（方向B 步骤11，保守版）：
          当前架构每帧全量重建元素树 + reconciler 全树调和，Provider 值
          变化触发子树重渲染属正常路径；完整「仅重渲染 context 消费者」
          需引入消费者依赖图，收益低（树规模小、10Hz）风险高——本步仅落地
          「use_context 逐 fiber 缓存 + provider 值变更清缓存传播」，
          不做消费者级剪枝（评估结论在代码注释中可追溯）。
        """
        if fiber.is_function:
            fiber.reset_hooks()
            _hooks._push_current(fiber)
            memo_skip = False
            try:
                if self._memo_should_skip(fiber):
                    memo_skip = True
                    rendered = None
                else:
                    rendered = fiber.type(fiber.props)
                    if getattr(fiber.type, "_is_memo", False):
                        fiber._last_memo_props = dict(fiber.props)
            except Exception as exc:
                if isinstance(exc, _hooks.HookStateError):
                    raise  # hook 状态机异常：编程错误，不参与 boundary 捕获
                # P1-3：fallback 函数组件自身渲染异常——直接传播（递归边界）。
                #   _build_fallback_element 为 callable fallback 构造独立 fiber
                #   渲染（props 带内部 ``_fallback`` 标记）；若此处再次被 boundary
                #   捕获会递归重建 fallback（无限循环）→ 传播保持崩溃恢复语义。
                if fiber.props.get("_fallback"):
                    raise
                boundary = self._find_boundary(fiber)
                if boundary is None:
                    raise  # 无边界：异常照常传播（崩溃恢复语义保留）
                # ★ 记录 boundary error + onError 回调一次
                self._record_boundary_error(boundary, exc)
                # 本帧该子树渲染 fallback（fallback 自身抛异常 → 直接传播）
                rendered = _build_fallback_element(boundary.props, exc)
            finally:
                _hooks._pop_current()
            if memo_skip:
                return  # 保留 fiber.child（不重建子树）
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
                    value = fiber.props.get("value", ctx.default)
                    fiber.contexts[ctx.tag] = value
                    # ★ provider 值变更检测（方向B 步骤11，保守版）：
                    #   与上次记录值比较，变化 → 递增全局版本号 + 清空子树
                    #   逐 fiber context 缓存（低频遍历）。完整「仅重渲染
                    #   context 消费者」剪枝评估结论见本方法 docstring——
                    #   本步仅落地缓存优化 + 清缓存传播，不做消费者级剪枝。
                    last = getattr(fiber, "_last_provider_value", _MISSING)
                    if last is _MISSING or not _safe_eq(last, value):
                        fiber._last_provider_value = value
                        _hooks._bump_context_version()
                        _clear_context_cache_subtree(fiber.child)
            children = list(element.children)
            self._reconcile_children(fiber, children)

    # ── ErrorBoundary（方向B 步骤9） ────────────────────

    def _find_boundary(self, fiber: Fiber) -> Fiber | None:
        """沿 return_ 链向上查找最近带 _is_boundary 标记的 fiber（ErrorBoundary）。

        Args:
            fiber: 抛异常的组件 fiber。

        Returns:
            最近的 boundary fiber；无则 None。
        """
        f = fiber.return_
        while f is not None:
            if getattr(f, "_is_boundary", False):
                return f
            f = f.return_
        return None

    def _record_boundary_error(self, boundary: Fiber, error: Exception) -> None:
        """在 boundary fiber 上记录异常对象并回调 onError（一次）。

        onError 回调失败仅记录日志（非关键降级，不阻断 fallback 渲染）。
        """
        boundary._boundary_error = error
        if boundary._boundary_on_error_called:
            return
        boundary._boundary_on_error_called = True
        on_error = boundary.props.get("onError")
        if on_error is not None:
            try:
                on_error(error)
            except Exception:
                _logger.debug("ErrorBoundary onError 回调异常", exc_info=True)

    # ── memo 短路（方向B 步骤10） ──────────────────────

    def _memo_should_skip(self, fiber: Fiber) -> bool:
        """memo 短路判定：props 相等（are_equal 或默认浅比较）且无待处理 state 更新。

        首渲染（无 ``_last_memo_props``）不短路；props 含不可比较对象时
        默认比较 try/except 兜底为不相等（重渲染，安全侧）。
        """
        if not getattr(fiber.type, "_is_memo", False):
            return False
        last_props = getattr(fiber, "_last_memo_props", None)
        if last_props is None:
            return False
        are_equal = getattr(fiber.type, "_are_equal", None)
        if are_equal is not None:
            try:
                same = bool(are_equal(last_props, fiber.props))
            except Exception:
                same = False
        else:
            try:
                same = fiber.props == last_props
            except Exception:
                same = False
        if not same:
            return False
        # 有未处理的 state 更新 → 不能短路（须重渲染应用更新）
        for hook in fiber.hooks:
            if isinstance(hook, StateHook) and hook.queue:
                return False
        return True

    # ── input router 构建（INK-1） ─────────────────────

    def _build_input_router(self, root_fiber: Fiber):
        """前序遍历收集 active InputHook，构建 composite router。

        无 active hooks 时返回 None（输入走旧路径，零行为变化）。
        Router 按 hook 顺序调用各 handler；任一返回 True 视为消费（返回 True）；
        全部未消费返回 False（放行旧路径）；handler 异常视为未消费（放行）。

        焦点仲裁（方向B 步骤10）：优先仅取 ``focused`` 且 active 的 hook；
        focused 集合为空时回退全部 active hook（无焦点仲裁时行为不变，零回归）。

        性能：签名 ``tuple((id(hook), is_active, id(handler), focused))`` 未变时
        复用上次 router 对象（避免每帧全树重建闭包）；handler/is_active/focused
        变化时签名变 → 重建。
        """
        hooks_list: list[InputHook] = []
        self._collect_input_hooks(root_fiber, hooks_list)
        if not hooks_list:
            self._input_router_cache = None
            return None
        # ★ 焦点仲裁：focused 集合非空 → 仅保留 focused；为空 → 回退全部 active
        focused_hooks = [h for h in hooks_list if getattr(h, "focused", True)]
        if focused_hooks:
            hooks_list = focused_hooks
        signature = tuple(
            (id(hook), hook.is_active, id(hook.handler), getattr(hook, "focused", True))
            for hook in hooks_list
        )
        if (
            self._input_router_cache is not None
            and self._input_router_cache[0] == signature
        ):
            return self._input_router_cache[1]

        def router(event) -> bool:
            for hook in hooks_list:
                try:
                    if hook.handler is not None and hook.handler(event):
                        return True
                except Exception:
                    continue
            return False

        self._input_router_cache = (signature, router)
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
        """标记子树删除（收集其 effect 销毁函数 + 清理 context 注册表）。

        P1-2 修复：删除前将 ``fiber.sibling`` 置 None——兄弟链可能仍指向
        已被复用为活跃 fiber 的旧节点（如列表删除/重排 ``[A,B] → [B]`` 时
        A.sibling 仍指向活跃 B）。不切断会误遍历活跃兄弟：
          - ``_cleanup_contexts(A)`` 走到 B（若 B 是 context provider）→
            ``_context_registry`` 误删注册条目（子树 use_context 回退 default）；
          - B 的 effect destroy 被误收集进 ``_pending_destroys``（活跃 B 的
            effect 被错误销毁重建）。
        置 None 后遍历只覆盖 fiber.child 子树（删除子树的全部后代）。
        """
        fiber.deleted = True
        fiber.sibling = None
        self._traverse_functions(fiber, self._queue_destroys)
        self._cleanup_contexts(fiber)

    def _cleanup_contexts(self, fiber: Fiber | None) -> None:
        """遍历被删子树，将 context provider host 从注册表移除（动态 context 卸载）。

        仅遍历 ``fiber.child`` 子树（不走 sibling 链——P1-2 修复：删除 fiber 的
        sibling 指针可能仍指向已被复用的活跃 fiber，误遍历会误删其 context 注册）。
        仅当 fiber 为 host 且 ``contexts`` 非空（本帧确实充当 provider）且注册表
        含对应标签时移除；卸载后重新 create_context 会以新 tag 重新注册。
        """
        f = fiber
        while f is not None:
            if isinstance(f.type, str):
                ctx = _hooks._context_registry.get(f.type)
                if ctx is not None and f.contexts:
                    _hooks._context_registry.pop(f.type, None)
            self._cleanup_contexts(f.child)
            f = f.sibling

    def _queue_destroys(self, fiber: Fiber) -> None:
        for hook in fiber.hooks:
            if isinstance(hook, EffectHook) and hook.destroy is not None:
                self._pending_destroys.append((fiber, hook))

    # ── effects 提交 ────────────────────────────────

    def _run_destroy(self, fiber: Fiber, hook: EffectHook) -> None:
        try:
            if hook.destroy is not None:
                hook.destroy()
            hook.destroy = None
            hook.last_deps = None
        except Exception:
            _logger.debug("effect 销毁执行异常 fiber=%s", fiber.type, exc_info=True)

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
                _logger.debug("effect 执行异常 fiber=%s", fiber.type, exc_info=True)

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
