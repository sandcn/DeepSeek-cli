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
    PasteHook,
    SyncStoreHook,
    _MISSING,
)
from .element import Element
from .error_boundary import _build_fallback_element
from . import hooks as _hooks
from . import layout as _layout

_logger = logging.getLogger(__name__)

#: 内置 host 标签集合——绝不可能是 context provider（create_context 生成
#: 唯一 ``__ctx_*__`` 标签；内置标签无 provider 注册路径）。reconciler
#: begin_work 跳过注册表 dict 查找（流式开放块每行 TEXT 各省一次 dict miss）。
_BUILTIN_HOSTS = frozenset({"text", "box", "static", "spacer", "app", "fragment"})


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

    ★ BUG-16（memo × context）：同时置 ``_context_dirty = True``——被标记的
    fiber（含 memo 组件）在下一次 ``_memo_should_skip`` 中不短路，强制重渲染
    让 ``use_context`` 重新求值（修复前 memo 短路跳过组件函数 → context 值
    变化后陈旧输出）。``use_context`` 消费时清除标记（见 hooks.py）。
    """
    f = fiber
    while f is not None:
        f._context_cache.clear()
        f._context_cache_version = 0
        f._context_dirty = True
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
        # ★ 焦点管理（React Ink v6）：每帧渲染前重置可聚焦 id 收集列表
        #   （useFocus 渲染期注册）。
        _hooks._reset_focus_ids()
        # 调和 root 的子元素
        self._reconcile_children(root_fiber, [element])
        # 布局（host 树）
        _layout.layout_tree(root_fiber, width)
        # ★ 性能（PERF-25）：合并渲染后置元数据收集——原实现 host ref 填充
        #   （_attach_host_refs）、effects 提交（_traverse_functions 收集后
        #   reversed 提交）、input router（_build_input_router →
        #   _collect_input_hooks）各自独立遍历整棵 fiber 树（每帧 3 次全树
        #   DFS）。合并为**一次 DFS** 同时收集：带 ref 的 host fiber / function
        #   fiber 列表 / InputHook+PasteHook（遍历顺序保持前序——ref 填充顺序
        #   无消费方、effects 后序提交、router 按 hooks_list 前序调用，与各自
        #   原实现语义一致）。``_run_live_effects`` 独立遍历入口已删除
        #   （2026-08-05 死代码清理：生产无调用方）。
        function_fibers, ref_fibers, input_hooks, paste_hooks = (
            self._collect_render_metadata(root_fiber)
        )
        # ★ host ref 填充（方向8）：layout 完成后将 layout_box 写入绑定的
        #   ref（RefHook.current / 函数 ref 回调）——useMeasure 等据此在
        #   layout effect 中读取尺寸。
        self._fill_host_refs(ref_fibers)
        # 提交 effects：先销毁（删除子树），再创建（依赖变化）
        for fiber, hook in self._pending_destroys:
            self._run_destroy(fiber, hook)
        self._pending_destroys = []
        self._run_live_effects_collected(function_fibers)
        # ★ 发布 composite input router（use_input 钩子，INK-1）
        # 用合并遍历已收集的 hooks 构建（免再次全树收集——PERF-25）
        router = self._build_input_router_from_hooks(input_hooks, paste_hooks)
        _hooks._publish_input_router(router)

    # ── 挂载 ────────────────────────────────────────

    @staticmethod
    def create_root() -> Fiber:
        """创建空根 fiber。"""
        return Fiber(TAG_ROOT, props={})

    # ── 调和 ────────────────────────────────────────

    @staticmethod
    def _set_props(fiber: Fiber, props: dict) -> None:
        """设置 fiber.props——**内容相等时保持旧引用**（性能关键）。

        背景：每帧组件树重建产生新 Element（新 props dict），reconciler 若
        无条件 ``fiber.props = element.props`` 则 props 引用每帧变化 →
        layout._measure 的 ``_measure_cache`` 引用级命中（``mc[1] is
        fiber.props``）恒 miss → 每帧对全部 host fiber 重做 props 解析
        （实测 _measure_cache 命中率 0%）。props **值不变**的帧（无变化帧/
        流式帧中已渲染行）本可复用测量结果。

        修复：值比较——新旧 props 内容相等时保持旧引用（内容不变，复用旧
        引用与更新到新引用渲染结果完全等价，满足 props 不可变契约；
        fiber.props 从不原地修改，仅整体替换）；内容变化才更新引用。
        不可比较对象（props 含自定义 ``__eq__`` 抛异常的 value）经
        try/except 兜底视为不等 → 更新引用（安全侧，仅损失一次缓存命中）。

        React 语义对照：React 中 props 对象通常由父组件缓存复用（同一对象
        引用传给子组件）；本框架每帧新建 dict，本优化在**内容等价**前提下
        模拟 React 的对象复用，不改变 props 语义。
        """
        old = fiber.props
        if old is props:
            return
        try:
            if old == props:
                return  # 内容相等：保持引用稳定（_measure_cache 引用级命中）
        except Exception:
            pass  # 不可比较（安全侧：更新引用）
        fiber.props = props
        # props 变化 → key 缓存失效（PERF-24；内容相等路径已提前 return）
        fiber._key_cache = None
    def _reconcile_children(self, return_fiber: Fiber, elements) -> None:
        """调和 return_fiber 的子元素列表（按 key/type diff 子 sibling 链）。

        接受 list 或 tuple（只读遍历——不修改 elements；调用方传
        ``element.children`` tuple 时免 list 复制，PERF-24）。

        方向B 步骤11：记录旧 sibling 链各 fiber 的旧位置索引
        （``old_index_map[key] = idx``）；复用 fiber 时比较旧/新位置，
        不同则置 ``fiber.moved = True``（keyed 列表重排信息，纯信息标记）。

        方向② 步骤6（moved 保留决策）：moved 标记保留——TestMovedFlag
        测试锁定 + 计算成本 O(keyed 子项数) 极低；renderer 行级 diff 暂不
        消费，保留供未来 keyed 子树尾部跳过优化（fiber.py moved 字段注释
        同步标注）。不做移除。

        方向3（无 key 列表复用修复）：元素**无显式 key**（``props.get("key")
        is None``）时按索引匹配旧 sibling 链对应位置——不查 ``existing_map``
        （无 key 同 type 兄弟共享派生 key ``host:text`` 会错误复用首个）；type
        相同复用、不同删除重建。有显式 key 的元素保持 key 匹配。同 key 重复
        元素经 ``seen_keys`` 检测记 warning（当前静默创建新 fiber 行为保留）。

        ★ 性能（PERF-7）：空元素快路径与稳定列表快路径均**在构建
        ``old_list`` 之前**执行（旧实现先构建完整旧子链列表）——空元素
        场景省 O(n) 列表分配；append-only 稳定列表场景（流式开放块每帧
        1000 行 TEXT）经 ``_try_reuse_stable`` 沿链直接比较复用，省 1 次
        O(n) 列表分配 + append。仅快路径不命中（中间插入/删除/重排）才
        构建 old_list 走完整算法。
        """
        # ★ 性能（方向1）：空元素快路径——直接删除全部旧子链（跳过 map
        #   构建与消费循环；``_mark_deleted`` 置 sibling=None，先取 next）。
        if not elements:
            c = return_fiber.child
            while c is not None:
                nxt = c.sibling
                self._mark_deleted(c)
                c = nxt
            return_fiber.child = None
            return

        # ★ 性能（方向1）：append-only 稳定列表快路径——前 N 个位置 key/type
        #   与旧链一致时按序复用（免构建 key maps / consumed 集合）。流式
        #   开放块每帧 1000 行 TEXT（key ``chat-{i}-{row}``）场景关键收益：
        #   ``_try_reuse_stable`` 为 O(N) 线性比较，命中原 2 dict + 1 set
        #   分配 + 逐项 dict 查找 + old_list 列表分配。
        if self._try_reuse_stable(return_fiber, elements):
            return

        old_list: list[Fiber] = []
        child = return_fiber.child
        while child is not None:
            old_list.append(child)
            child = child.sibling

        existing_map: dict[str, Fiber] = {}
        old_index_map: dict[str, int] = {}
        for idx, f in enumerate(old_list):
            existing_map[f.key] = f
            old_index_map[f.key] = idx

        # 已消费旧 fiber 的 id 集合（复用 / 已标记删除的节点；结尾删除跳过）
        consumed: set[int] = set()
        first: Fiber | None = None
        prev: Fiber | None = None
        # 无显式 key 元素按索引匹配的旧链消费计数器（每消费一个旧节点 +1，
        # 含被 _mark_deleted 的节点——保证无 key 列表顺序一致）。
        positional_idx = 0
        seen_keys: set[str] = set()
        for new_idx, element in enumerate(elements):
            explicit_key = element.props.get("key")
            old = None
            if explicit_key is None:
                # 无显式 key → 按索引匹配旧 sibling 链对应位置（跳过已消费
                # 节点——方向1 步骤3：混合 keyed/无 key 列表中 keyed 元素消费
                # 旧节点后，无 key 元素按位置不复用同一旧 fiber，防 fiber 树
                # 环/双父）。项目既有语义锁定：无 key 元素按索引匹配（含
                # keyed 旧节点），TestMixedKeyedNoKeyConsumed 测试契约。
                while (
                    positional_idx < len(old_list)
                    and id(old_list[positional_idx]) in consumed
                ):
                    positional_idx += 1
                if positional_idx < len(old_list):
                    old = old_list[positional_idx]
                    positional_idx += 1
            else:
                key = element.key
                # ★ 同 key 重复元素检测（方向3）：显式 key 重复 → warning +
                #   继续（当前静默创建新 fiber 行为保留，仅加警告）。
                if key in seen_keys:
                    _logger.warning("调和器检测到重复 key: %s", key)
                seen_keys.add(key)
                # 方向1 步骤3（keyed 分支 consumed 检查）：先 ``get`` 再校验
                # 已消费——已消费（被前序元素复用/标记删除）的旧 fiber 不再
                # 复活（修复前 ``pop`` 直接取回已消费节点 → 同一 fiber 双父）。
                old = existing_map.get(key)
                if old is not None and id(old) in consumed:
                    old = None
                elif old is not None:
                    existing_map.pop(key, None)
            if old is not None and _is_same_type(old, element):
                fiber = old
                if explicit_key is None:
                    # 无 key 列表按索引复用：无位置信息语义（moved 恒 False）
                    fiber.moved = False
                else:
                    # ★ moved 标记：旧位置 != 新位置 → True（每帧重算，非累计）
                    fiber.moved = old_index_map.get(key) != new_idx
                # ★ 性能（PERF-7 + props 引用级缓存）：``element.props`` 为
                #   frozen 不可变 dict（``__post_init__`` 拷贝）——经
                #   ``_set_props`` 值比较复用：内容相等时保持旧引用（
                #   _measure_cache 引用级命中，0% → 高命中率），内容变化才
                #   更新（免每帧 O(n) dict 浅拷贝）。
                self._set_props(fiber, element.props)
                was_deleted = fiber.deleted
                fiber.deleted = False
                if was_deleted:
                    # ★ P3 修复（review 方向）：仅「被删除后恢复复用」时清空
                    #   陈旧 state queue——正常帧间复用 deleted 恒 False，
                    #   queue 中的待应用更新必须保留（无条件清空会丢失本帧
                    #   排队的状态更新）。见 _hooks_core
                    #   ``_clear_fiber_state_queues``。
                    _hooks._clear_fiber_state_queues(fiber)
                fiber.return_ = return_fiber
                self._begin_work(fiber, element)
                consumed.add(id(fiber))
            else:
                if old is not None:
                    self._mark_deleted(old)
                    consumed.add(id(old))
                fiber = self._create_and_begin(element, return_fiber)
            # ★ 清除旧 sibling 链——复用 fiber 若保留旧 sibling 指针会形成环
            fiber.sibling = None
            if prev is not None:
                prev.sibling = fiber
            else:
                first = fiber
            prev = fiber
        # ★ 删除未消费的旧 fiber（existing_map 剩余 + 无 key 未消费的旧节点）
        for old in old_list:
            if id(old) not in consumed:
                self._mark_deleted(old)
        return_fiber.child = first

    def _try_reuse_stable(
        self,
        return_fiber: Fiber,
        elements: list[Element],
    ) -> bool:
        """append-only 稳定列表快路径：按序复用旧 fiber + 创建尾部新元素。

        触发条件（方向1）：``len(elements) >= len(old_child_chain)`` 且前 N 个
        位置 key/type 与旧链一致（keyed 元素 key 相等；无 key 元素位置对应）。
        满足时行为与完整调和算法等价（复用/新建结果一致），但省去 key maps
        与 consumed 集合构建——流式开放块每帧 1000 行 TEXT 场景关键收益。

        ★ 性能（PERF-7）：沿 ``return_fiber.child`` 链直接比较/复用，**不
        构建 old_list 列表**（旧实现由调用方构建后传入）——稳定列表命中时
        省 1 次 O(n) 列表分配 + append。

        Returns:
            True — 快路径已执行；False — 条件不满足（调用方走完整算法）。
        """
        child = return_fiber.child
        if child is None:
            return False
        # ★ P-H14（性能）：第一趟统计旧链长度 + 比较前 N 个 key/type 合一——
        #   修复前先 O(n) 数旧链长度，`len(elements) < n_old`（收缩场景）直接
        #   返回 False 前浪费一趟完整遍历（旧实现）——现在统计长度与比较同步
        #   进行：长度统计与 key/type 比较在同一次 while 循环内完成（仅
        #   稳定列表命中场景才多比较 N 项；收缩场景提前返回仍省一趟 O(n)）。
        #   触发条件不变：len(elements) >= n_old 且前 N 个 key/type 一致。
        n_old = 0
        cur = child
        n_new = len(elements)  # 新元素数量（旧链长度上限阈值，与 n_old 对称）
        while cur is not None:
            if n_old >= n_new:
                # 已超出新元素数量：旧链更长 → 不满足「len>=n_old」→ 直接
                # 返回 False（无需继续数完旧链——len 已超新元素数）。
                return False
            el = elements[n_old]
            if not _is_same_type(cur, el):
                return False
            if el.props.get("key") is not None and cur.key != el.key:
                return False
            n_old += 1
            cur = cur.sibling
        # 不可达防御（child 非 None 时第一趟循环至少迭代一次，n_old 恒 >=1；
        # 保留以防未来重构改变前置快路径/调用方语义）。
        if n_old == 0:
            return False
        # 第二趟：按序复用 + 创建尾部新元素
        first: Fiber | None = None
        prev: Fiber | None = None
        cur = child
        for i, el in enumerate(elements):
            if i < n_old:
                fiber = cur
                cur = cur.sibling
                # ★ 性能（PERF-7 + props 引用级缓存）：经 ``_set_props``
                #   值比较复用（内容相等保持旧引用 → _measure_cache 命中）。
                self._set_props(fiber, el.props)
                was_deleted = fiber.deleted
                fiber.deleted = False
                if was_deleted:
                    # ★ P3 修复（review 方向）：仅「被删除后恢复复用」时清空
                    #   陈旧 state queue（见 _hooks_core
                    #   ``_clear_fiber_state_queues``）。
                    _hooks._clear_fiber_state_queues(fiber)
                fiber.return_ = return_fiber
                fiber.moved = False  # 稳定列表：位置不变
                self._begin_work(fiber, el)
            else:
                fiber = self._create_and_begin(el, return_fiber)
            fiber.sibling = None
            if prev is not None:
                prev.sibling = fiber
            else:
                first = fiber
            prev = fiber
        return_fiber.child = first
        return True

    def _reconcile_single(
        self,
        return_fiber: Fiber,
        existing: Fiber | None,
        element: Element,
    ) -> Fiber:
        """调和单个元素（函数组件渲染输出）。"""
        if existing is not None and _is_same_type(existing, element):
            # ★ 性能（PERF-7 + props 引用级缓存）：经 ``_set_props`` 值比较
            #   复用（内容相等保持旧引用 → _measure_cache 命中）。
            self._set_props(existing, element.props)
            was_deleted = existing.deleted
            existing.deleted = False
            if was_deleted:
                # ★ P3 修复（review 方向）：仅「被删除后恢复复用」时清空
                #   陈旧 state queue（见 _hooks_core
                #   ``_clear_fiber_state_queues``）。
                _hooks._clear_fiber_state_queues(existing)
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
        # ★ 性能（PERF-7）：直接复用 ``element.props`` 引用（Element frozen
        # 不可变）——免新 fiber 构造时 dict 浅拷贝。
        fiber = Fiber(tag, element.type, element.props, return_=return_fiber)
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
                if self._memo_should_skip(fiber, element):
                    memo_skip = True
                    rendered = None
                else:
                    # ★ 完善 react ink：函数组件 children 注入——React 中 children
                    #   属于 props 一部分（``props.children``）。本框架元素 children
                    #   为独立字段（``element.children``），函数组件仅收到 props；
                    #   元素带 children（``h(Comp, {}, child)`` 变参）时经副本注入
                    #   ``props["children"]``（不修改 fiber.props，调和比较基准
                    #   保持 props-only）。元素无 children 时零开销（直接传 props）。
                    #   既有组件（App/ChatView/TopHeader 等无变参子级）行为不变。
                    if element.children:
                        call_props = dict(fiber.props)
                        call_props["children"] = element.children
                    else:
                        call_props = fiber.props
                    # ★ forwardRef（完善 react ink）：带 ``_is_forward_ref`` 标记
                    #   的组件改以 ``(props, ref)`` 双参调用（ref 取自
                    #   ``props.ref``——React 约定；不进入普通 props）。
                    if getattr(fiber.type, "_is_forward_ref", False):
                        rendered = fiber.type(call_props, call_props.get("ref"))
                    else:
                        rendered = fiber.type(call_props)
                    if getattr(fiber.type, "_is_memo", False):
                        fiber._last_memo_props = dict(fiber.props)
                        fiber._last_memo_children = element.children
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
            # ★ BUG-36（review 方向）：本帧组件已渲染（使用最新 context 值，
            #   含不消费 context 的 memo 组件）→ 清除 ``_context_dirty`` 标记。
            #   修复前标记仅由 ``use_context`` 消费时清除——位于 provider 子树内
            #   但不调用 ``use_context`` 的 memo 组件：Provider 值首次变化后
            #   ``_context_dirty`` 恒 True → **memo 短路永久失效**（每帧全量重建，
            #   性能退化，树越大越严重）。本帧渲染完成后清除：下次 Provider 值
            #   变化时 ``_clear_context_cache_subtree`` 会重新置位（React 语义：
            #   context 变更强制重渲染消费者，与 memo 无关）。
            fiber._context_dirty = False
            if rendered is None:
                rendered = Element("text", {"children": ""}, ())
            elif not isinstance(rendered, Element):
                rendered = Element("text", {"children": str(rendered)}, ())
            fiber.child = self._reconcile_single(fiber, fiber.child, rendered)
        else:
            children = element.children
            ftype = fiber.type
            # ★ 性能（PERF-21）：叶子内置 host 快路径——TEXT/SPACER/fragment
            #   等叶子（内置 host 标签 + 无 children + 无旧子链）永不可能是
            #   context provider（create_context 生成唯一 ``__ctx_*__`` 标签，
            #   不属于 ``_BUILTIN_HOSTS``），且无子节点无需调和。跳过
            #   ``contexts.clear()``（叶子永不写入）/ provider 检查 / 子调和
            #   ——大组件树每帧数千叶子（1000+ TEXT）省数千次 dict 清空与
            #   isinstance/注册表检查。仅 ``_host_ref`` 仍须设置（叶子也可
            #   绑定 useMeasure ref）。
            if not children and fiber.child is None and ftype in _BUILTIN_HOSTS:
                fiber._host_ref = fiber.props.get("ref")
                return
            # ★ context provider：先重置 contexts（每次渲染不残留旧值），
            #   再按 host 标签匹配注册的 Context（INK-3）——value 写入
            #   fiber.contexts（键为 ctx.tag 唯一标签）供子树 use_context
            #   沿 return_ 链查找。
            fiber.contexts.clear()
            # ★ host ref（方向8）：``ref`` prop 绑定（useMeasure 支持）。
            #   React 语义中 ref 不进入普通 props——本框架经 props 传入
            #   （``h(BOX, {"ref": my_ref})``），此处存入 fiber 供 layout
            #   后填充（不参与样式/布局 props 消费）。
            fiber._host_ref = fiber.props.get("ref")
            # ★ 性能（方向1）：内置 host 标签（text/box/static/spacer/app/
            #   fragment）绝不可能是 context provider（create_context 生成
            #   唯一 ``__ctx_*__`` 标签）——跳过注册表 dict 查找（流式开放
            #   块每行 TEXT 各省一次 dict miss）。
            if isinstance(ftype, str) and ftype not in _BUILTIN_HOSTS:
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
            if children:
                # ★ 性能（PERF-24）：不再 ``list(children)`` 复制——children
                #   为 tuple（Element.children frozen 不变式），``_reconcile_children``
                #   与 ``_try_reuse_stable`` 均为**只读**遍历（索引/enumerate），
                #   tuple 原生支持；修复前每帧对 ChatView 等大 children（流式
                #   开放块 64+ 行 TEXT）复制一次列表（O(n) 分配）。
                self._reconcile_children(fiber, children)
            elif fiber.child is not None:
                # ★ 性能（方向1）：空子元素快路径——叶子/无子节点容器直接
                #   删除旧子链（修复前无条件 ``_reconcile_children(fiber, [])``
                #   仍构建空 maps + 遍历旧链，流式开放块每行 TEXT 都走一遍）。
                #   ★ P3 修复（review 方向）：外层 ``elif fiber.child is not
                #   None`` 已保证旧子链非 None——删除内层重复 ``if fiber.child
                #   is not None``（恒真）及过时 PERF-16 注释。
                self._reconcile_children(fiber, [])

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

    def _memo_should_skip(self, fiber: Fiber, element) -> bool:
        """memo 短路判定：props 相等 + children 相等且无待处理 state 更新。

        React 语义（完善 react ink）：React 中 ``children`` 属于 props 一部分，
        memo 短路须同时比较 props 与 children——本框架 children 为元素独立
        字段（``element.children``），修复前仅比较 props 字典：props 未变但
        子元素变化（如 ``h(MemoComp, {"x":1}, "新子文本")``）时被误跳过 →
        子树陈旧。修复后比较 ``_last_memo_props``（are_equal 或默认 ==）与
        ``_last_memo_children``（值相等）。

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
        # ★ children 值相等比较（React children 属于 props——本框架独立字段）
        try:
            same = element.children == getattr(fiber, "_last_memo_children", ())
        except Exception:
            same = False
        if not same:
            return False
        # ★ BUG-16（review 方向）：context 依赖被 Provider 值变化影响（子树
        #   被 ``_clear_context_cache_subtree`` 标记 ``_context_dirty``）时不得
        #   短路——否则组件函数不被调用 → ``use_context`` 不重新求值 → 子树
        #   保持旧值渲染且 props/children 不再变化时**永久陈旧**（React 语义：
        #   context 变更强制重渲染消费者，与 memo 无关）。无关 Provider 变化
        #   不标记本 fiber（``_context_dirty`` 逐 fiber），正常短路保持。
        if getattr(fiber, "_context_dirty", False):
            return False
        # 有未处理的 state 更新 → 不能短路（须重渲染应用更新）
        for hook in fiber.hooks:
            if isinstance(hook, StateHook) and hook.queue:
                return False
        return True

    # ── input router 构建（INK-1） ─────────────────────

    def _build_input_router(self, root_fiber: Fiber):
        """前序遍历收集 active InputHook/PasteHook，构建 composite router。

        兼容入口（测试/外部调用）：收集 + 构建两步。生产渲染经
        ``_collect_render_metadata``（PERF-25 合并遍历）收集后直接调
        ``_build_input_router_from_hooks``（免重复全树遍历）。
        """
        hooks_list: list[InputHook] = []
        paste_hooks: list[PasteHook] = []
        self._collect_input_hooks(root_fiber, hooks_list, paste_hooks)
        return self._build_input_router_from_hooks(hooks_list, paste_hooks)

    def _build_input_router_from_hooks(self, hooks_list: list[InputHook], paste_hooks: list[PasteHook]):
        """由已收集的 hooks 构建 composite router（PERF-25：合并遍历后免重复收集）。

        无 active hooks 时返回 None（输入走旧路径，零行为变化）。
        Router 按 hook 顺序调用各 handler；任一返回 True 视为消费（返回 True）；
        全部未消费返回 False（放行旧路径）；handler 异常视为未消费（放行）。

        焦点仲裁（方向B 步骤10）：优先仅取 ``focused`` 且 active 的 hook；
        focused 集合为空时回退全部 active hook（无焦点仲裁时行为不变，零回归）。

        React Ink v6（方向 E）：
          - 粘贴优先：事件为粘贴（``kind=="char"`` 且字符数 > 1）且存在 active
            PasteHook（usePaste）时，仅调用 paste handler（消费则阻断 use_input
            通道——React Ink 语义独立通道）。
          - Tab/Shift+Tab 焦点切换：存在可聚焦组件（``useFocus`` 注册）且全局
            焦点管理启用时，Tab → focusNext、Shift+Tab → focusPrevious，并消费
            事件（不再传递给 use_input handler）。

        性能：签名 ``tuple((hook.seq, is_active, id(handler), focused))`` 未变时
        复用上次 router 对象（避免每帧全树重建闭包）；handler/is_active/focused
        变化时签名变 → 重建。方向1 L3：签名首元改用 ``hook.seq``（稳定递增序号）
        替代 ``id(hook)``——修复 id 复用风险（hook 被 GC 后新对象复用旧 id →
        签名误判未变 → 复用过期 router 闭包）。方向1 步骤3：缓存保存
        ``(signature, router, hooks_list)`` 三元组——签名命中时逐一 ``is`` 比对
        hook/handler 引用仍有效（handler 被 GC 后新对象复用旧 id → 签名相同但
        引用不同 → 重建，闭环修复 id 复用）。
        """
        if not hooks_list and not paste_hooks:
            self._input_router_cache = None
            return None
        # ★ 焦点仲裁：focused 集合非空 → 仅保留 focused；为空 → 回退全部 active
        focused_hooks = [h for h in hooks_list if getattr(h, "focused", True)]
        if focused_hooks:
            hooks_list = focused_hooks
        signature = tuple(
            (hook.seq, hook.is_active, id(hook.handler), getattr(hook, "focused", True))
            for hook in hooks_list
        )
        has_focus_ids = bool(getattr(_hooks, "_focus_ids", None)) and bool(getattr(_hooks, "_focus_enabled", True))
        if self._input_router_cache is not None:
            cached_signature, cached_router, cached_hooks = self._input_router_cache
            if cached_signature == signature and cached_router._ink_has_focus_ids == has_focus_ids:
                # ★ 方向1 步骤3（router id 复用修复）：id(hook.handler) 在 handler
                #   被 GC 后 id 可复用 → 签名误判未变 → 复用过期 router 闭包。
                #   缓存保存 hooks_list，命中时逐一 ``is`` 比对 hook/handler
                #   引用仍有效（低开销：每帧一次、hooks 数量极少）。
                if len(cached_hooks) == len(hooks_list) and all(
                    a is b and a.handler is b.handler
                    for a, b in zip(cached_hooks, hooks_list)
                ):
                    return cached_router

        def _event_key_tab(event) -> bool:
            """判断事件是否为 Tab（含 Shift+Tab，CSI u modifier=2）。"""
            if getattr(event, "kind", None) != "tab":
                return False
            return True

        def router(event) -> bool:
            # ── 粘贴优先（React Ink usePaste 独立通道）──
            if paste_hooks:
                if getattr(event, "kind", "") == "char":
                    text = getattr(event, "char", "") or ""
                    if len(text) > 1:
                        for hook in paste_hooks:
                            try:
                                if hook.handler is not None and hook.handler(text):
                                    return True
                            except Exception:
                                continue
            # ── Tab/Shift+Tab 焦点切换（React Ink useFocusManager）──
            if has_focus_ids and _event_key_tab(event):
                # 非 CSI u 时 modifier=0（普通 Tab）；Shift+Tab 需 CSI u 协议
                if getattr(event, "modifier", 0) >= 2:
                    _hooks._focus_previous()
                else:
                    _hooks._focus_next()
                return True
            for hook in hooks_list:
                try:
                    if hook.handler is not None and hook.handler(event):
                        return True
                except Exception:
                    continue
            return False

        router._ink_has_focus_ids = has_focus_ids
        self._input_router_cache = (signature, router, hooks_list)
        return router

    def _collect_input_hooks(self, fiber: Fiber | None, out: list[InputHook], paste_out: list[PasteHook] | None = None) -> None:
        """前序遍历 fiber 树，收集 active 且已设 handler 的 InputHook（跳过已删除）。

        paste_out 非 None 时同步收集 active PasteHook（usePaste——React Ink v6）。

        ★ P-H9（性能）：原实现每次递归 ``self._collect_input_hooks(...)`` 需
        加载 self + 属性查找 + 调用（每帧每节点重复）。改为局部闭包递归调用
        ——``_visit(f.child)`` 仅加载局部变量（省属性查找），10Hz 大组件树每帧
        数千节点时减少可感知开销。

        ★ 性能（PERF-19）：递归闭包 → 显式栈迭代（大组件树每帧数千节点的
        递归调用开销可感知；hook 收集顺序由 router 调用序决定——InputHook
        的 ``seq`` 字段稳定递增，router 按 hooks_list 顺序调用，迭代收集
        顺序与递归前序一致）。
        """
        stack = [fiber]
        while stack:
            f = stack.pop()
            while f is not None:
                if f.deleted:
                    f = f.sibling
                    continue
                if f.is_function:
                    for hook in f.hooks:
                        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
                            out.append(hook)
                        elif paste_out is not None and isinstance(hook, PasteHook) and hook.is_active and hook.handler is not None:
                            paste_out.append(hook)
                if f.child is not None:
                    stack.append(f.sibling)
                    f = f.child
                else:
                    f = f.sibling

    def _collect_render_metadata(self, root_fiber: Fiber):
        """一次 DFS 遍历收集渲染后置元数据（PERF-25）。

        合并原三趟独立全树遍历（``_attach_host_refs`` / ``_traverse_functions`` /
        ``_collect_input_hooks``）为**一次遍历**——大组件树每帧省 2 次 O(节点数)
        的 DFS 分配与递归开销。返回：

          - ``function_fibers``：function fiber 前序列表（effects 后序提交用）；
          - ``ref_fibers``：带 ``_host_ref`` 的 fiber（layout_box 填充用）；
          - ``input_hooks`` / ``paste_hooks``：active 且有 handler 的输入钩子
            （composite router 用）。

        遍历保持前序（与各自原实现一致：ref 填充顺序无消费方、effects 后序
        reversed 提交、router 按 hooks_list 顺序调用）；跳过已删除 fiber。

        function fiber 的 ``_host_ref`` 恒为 None（``_begin_work`` 仅 host 分支
        设置——ref 绑定 useMeasure 仅对 host 元素有意义），故仅收集非 function
        fiber 的 ref（与原 ``_attach_host_refs`` 对所有 fiber 检查等价）。
        """
        function_fibers: list[Fiber] = []
        ref_fibers: list[Fiber] = []
        input_hooks: list[InputHook] = []
        paste_hooks: list[PasteHook] = []
        stack = [root_fiber]
        while stack:
            f = stack.pop()
            while f is not None:
                if f.deleted:
                    f = f.sibling
                    continue
                if f.is_function:
                    function_fibers.append(f)
                    for hook in f.hooks:
                        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
                            input_hooks.append(hook)
                        elif isinstance(hook, PasteHook) and hook.is_active and hook.handler is not None:
                            paste_hooks.append(hook)
                else:
                    ref = f._host_ref
                    if ref is not None:
                        ref_fibers.append(f)
                if f.child is not None:
                    stack.append(f.sibling)
                    f = f.child
                else:
                    f = f.sibling
        return function_fibers, ref_fibers, input_hooks, paste_hooks

    def _fill_host_refs(self, ref_fibers) -> None:
        """将 layout_box 写入绑定的 ref（PERF-25：``_collect_render_metadata``
        收集后的填充阶段，与 ``_attach_host_refs`` 填充逻辑一致）。"""
        for f in ref_fibers:
            ref = f._host_ref
            if ref is not None and f.layout_box is not None:
                if callable(ref):
                    try:
                        ref(f.layout_box)
                    except Exception:
                        _logger.debug("host ref 回调异常 fiber=%s", f.type, exc_info=True)
                elif hasattr(ref, "current"):
                    ref.current = f.layout_box

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

        方向1（删除子树 effect destroy 不执行修复）：**先收集删除子树全部
        function fiber 的 EffectHook.destroy，再置 ``fiber.deleted = True``**
        ——修复前先置 deleted 后 ``_traverse_functions``，首节点即跳过整棵
        子树，destroy 永不收集（删除组件卸载清理依赖缺失）。

        ★ 标准 React Ink 组件化（2026-08-05）：**递归标记子树全部 host/function
        fiber ``deleted=True``**——修复前仅标记根 fiber：外部缓存（如
        ``session._input_fiber`` 指向 InputArea 返回的 Column host fiber /
        ``root._committed_chat_cache`` 指向 StaticLines host fiber）在子树根
        被替换时（函数组件 key 变化 → 函数 fiber 删除但子 host fiber 不标记）
        缓存失效检测失效（`deleted` False 但 fiber 已脱离活跃树）。递归标记后
        外部缓存经 ``deleted`` 正确失效重建。
        """
        fiber.sibling = None
        self._traverse_functions(fiber, self._queue_destroys, include_self=True)
        fiber.deleted = True
        self._cleanup_contexts(fiber)
        # ★ 递归标记子树全部 fiber deleted（含 host 子节点——外部缓存指向
        #   子树内 host fiber，仅标记根无法让缓存失效；复用路径会重置
        #   ``existing.deleted = False``）。
        stack = [fiber]
        while stack:
            f = stack.pop()
            f.deleted = True
            c = f.child
            while c is not None:
                stack.append(c)
                c = c.sibling

    def _cleanup_contexts(self, fiber: Fiber | None) -> None:
        """遍历被删子树（当前为 no-op，保留接口签名与调用点）。

        ★ BUG-18（review 方向）修复：**不再 pop 注册表**——``_context_registry``
        保存的是 ``create_context`` 模块级创建的全局 Context 对象（进程生命周期），
        与 Provider 挂载状态无关。修复前卸载时 ``pop(f.type)`` 后，同一组件重新
        挂载 ``h(ctx.Provider, ...)`` 时 ``begin_work`` 查注册表返回 None →
        ``fiber.contexts`` 不写入 → 子树 ``use_context`` 沿 return_ 链找不到
        provider，静默回退 ``ctx.default``（Provider 重挂载失效）。

        「卸载回退 default」语义由 ``use_context`` 的 return_ 链查找自然实现：
        Provider 卸载后其 fiber 不再在树中，消费者沿 return_ 链找不到 provider
        → 返回 default，无需注册表干预。

        遍历范围注释（历史，可追溯）：`_mark_deleted` 已切断顶层 fiber.sibling，
        内部 sibling 遍历仅覆盖删除子树的**后代兄弟**，安全。多 Provider 同
        Context 卸载语义由 return_ 链查找自然保证（无注册表计数需求）。
        """
        # 当前实现为 no-op——注册表条目（Context 对象）生命周期与 Provider
        # 挂载解耦；保留函数以维持 `_mark_deleted` 调用面与未来挂载计数扩展点。
        return

    def _queue_destroys(self, fiber: Fiber) -> None:
        # ★ P3 修复（review 方向）：组件卸载时若焦点激活指向自身（同自动
        #   id）则清空——防焦点悬挂（见 _hooks_focus ``_clear_focus_active``）。
        _hooks._clear_focus_active(fiber)
        for hook in fiber.hooks:
            if isinstance(hook, EffectHook) and hook.destroy is not None:
                self._pending_destroys.append((fiber, hook))
            elif isinstance(hook, SyncStoreHook) and hook.cleanup is not None:
                self._pending_destroys.append((fiber, hook))

    def _attach_host_refs(self, fiber: Fiber | None) -> None:
        """遍历 host 树，将 layout_box 写入绑定的 ref（useMeasure 支持，方向8）。

        仅处理 ``_host_ref`` 非空的 fiber（React 语义：host ref 指向 DOM
        节点——本框架非全屏流动模型下为布局盒 LayoutBox，含 x/y/w/h）。
        支持两种 ref 形态：
          - RefHook（``use_ref`` 返回）：写入 ``ref.current = box``；
          - 函数 ref（React 回调 ref）：``ref(box)`` 调用。

        与 React 差异（文档注明）：卸载时不置 null（非全屏模型无 DOM 节点
        回收语义；useMeasure 仅挂载期读取尺寸，卸载清理无消费方）。

        ★ P-H10（性能）：绝大多数 fiber 无 ``_host_ref``（仅 useMeasure
        绑定的 host）——仍须递归遍历（无法从根短路判定子树内是否有 ref），
        但跳过 ``hasattr`` 判定（直接读字段，Fiber 定义恒有该属性）。

        ★ 性能（PERF-19）：递归 → 显式栈迭代（大组件树每帧数千节点的
        ``f.child`` 递归调用开销可感知；迭代保持前序语义，ref 填充顺序与
        递归一致——回调 ref 顺序无消费方依赖）。

        Args:
            fiber: 遍历起点（root fiber）。
        """
        stack = [fiber]
        while stack:
            f = stack.pop()
            while f is not None:
                if f.deleted:
                    f = f.sibling
                    continue
                ref = f._host_ref
                if ref is not None and f.layout_box is not None:
                    if callable(ref):
                        try:
                            ref(f.layout_box)
                        except Exception:
                            _logger.debug("host ref 回调异常 fiber=%s", f.type, exc_info=True)
                    elif hasattr(ref, "current"):
                        ref.current = f.layout_box
                if f.child is not None:
                    stack.append(f.sibling)
                    f = f.child
                else:
                    f = f.sibling

    # ── effects 提交 ────────────────────────────────

    def _run_destroy(self, fiber: Fiber, hook: EffectHook) -> None:
        try:
            if isinstance(hook, SyncStoreHook):
                # useSyncExternalStore 卸载：取消外部 store 订阅
                if hook.cleanup is not None:
                    hook.cleanup()
                hook.cleanup = None
                hook.subscribed = False
                return
            if hook.destroy is not None:
                hook.destroy()
            hook.destroy = None
            hook.last_deps = None
        except Exception:
            _logger.debug("effect 销毁执行异常 fiber=%s", fiber.type, exc_info=True)

    def _run_live_effects_collected(self, function_fibers: list) -> None:
        """提交依赖变化的 effect（PERF-25：用 ``_collect_render_metadata`` 合并
        遍历收集的 function fiber 前序列表，reversed 后序提交——子 effect 先于
        父 effect，React 语义）。"""
        # 第一阶段：layout effects（useLayoutEffect）
        for fiber in reversed(function_fibers):
            self._commit_live(fiber, layout=True)
        # 第二阶段：passive effects（useEffect）
        for fiber in reversed(function_fibers):
            self._commit_live(fiber, layout=False)

    def _commit_live(self, fiber: Fiber, layout: bool) -> None:
        """提交依赖变化的 effect（layout=True 仅 layout effects；False 仅 passive）。"""
        for hook in fiber.hooks:
            if not isinstance(hook, EffectHook):
                continue
            if hook.layout != layout:
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
        include_self: bool = False,
    ) -> None:
        """前序遍历 fiber 树，对 function fiber 调用 cb（跳过已删除）。

        Args:
            fiber: 遍历起点。
            cb: 对 function fiber 调用的回调。
            include_self: True 时对起点 fiber 自身也调用 cb（即使其已置
                deleted 标记——``_mark_deleted`` 收集删除子树 destroy 的
                前置场景；默认 False 保持 ``_collect_input_hooks``
                等既有调用语义不变）。

        ★ 性能（PERF-19）：递归 → 显式栈迭代（大组件树每帧数千节点的递归
        调用开销可感知；回调顺序保持前序——``_collect_render_metadata`` 收集
        effects 后 reversed 执行（顺序无关）、``_queue_destroys`` 收集 destroy
        （顺序无关），显式栈后进先出的兄弟顺序不影响语义）。
        """
        # include_self：起点 fiber 已 deleted 时仍调用 cb（收集其 destroy）——
        # 正常路径（起点未 deleted）由下方遍历处理，不重复。
        if include_self and fiber is not None and fiber.deleted and fiber.is_function:
            cb(fiber)
        stack = [fiber]
        while stack:
            f = stack.pop()
            while f is not None:
                if f.deleted:
                    f = f.sibling
                    continue
                if f.is_function:
                    cb(f)
                if f.child is not None:
                    stack.append(f.sibling)
                    f = f.child
                else:
                    f = f.sibling


__all__ = ["Reconciler"]
