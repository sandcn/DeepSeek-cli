"""Fiber — 调和器工作单元（React 风格 fiber 节点）。

Fiber 表示组件树中一个待调和/已调和的节点，通过 child/sibling/return
指针构成链表树。fiber 复用（同 key/type 保留 hooks 状态）由 reconciler
按元素 key 匹配实现，fiber 自身不再持有 alternate 字段。

fiber 的 ``layout_box`` 在 layout 阶段填充（LayoutBox(x,y,w,h)）。

零依赖：仅 typing（Layer 0）。
"""

from __future__ import annotations

import itertools

from src._compat import dataclass
from dataclasses import field
from typing import Any, Callable, Optional, Union

# ── fiber tag 常量 ─────────────────────────────────────────
TAG_ROOT = "root"
TAG_HOST = "host"
TAG_FUNCTION = "function"

#: InputHook 稳定序号真源（方向1 L3）——模块级递增计数器，替代 id(hook)
#:   （id 复用风险）。fiber 复用/删除均不重置；仅进程生命周期内递增。
_HOOK_SEQ = itertools.count()

#: provider 值变更检测哨兵（``Fiber._last_provider_value`` 的未初始化标记；
#:   None 是合法 provider 值，不能用 None 作哨兵）。
_MISSING = object()


@dataclass
class StateHook:
    """use_state / use_reducer hook 节点。

    Attributes:
        state: 当前状态值。
        queue: 待处理更新队列（列表，None 表示无）。
        reducer: use_reducer 传入的 reducer（None 表示 use_state）。
        setter: 缓存的 set_state/dispatch 闭包（★ P3：身份跨渲染稳定——
            React 保证 dispatch 身份稳定；修复前每次渲染新建闭包导致
            ``memo`` 子组件 props 浅比较恒不等、memo 短路失效）。
    """

    state: Any = None
    queue: list | None = None
    reducer: Callable[[Any, Any], Any] | None = None
    setter: Any = None


@dataclass
class RefHook:
    """use_ref hook 节点。"""

    current: Any = None


@dataclass
class EffectHook:
    """use_effect / useLayoutEffect hook 节点。

    Attributes:
        create: effect 创建函数（挂载/依赖变化时调用，返回销毁函数）。
        deps: 依赖列表。
        destroy: 上次的销毁函数。
        last_deps: 上次提交的依赖列表（用于检测变化）。
        layout: True=useLayoutEffect（布局后立即同步执行）；False=useEffect
            （passive，帧渲染后执行）。React 语义：layout effects 先于
            passive effects 提交。
    """

    create: Any = None
    deps: Any = None
    destroy: Any = None
    last_deps: Any = None
    layout: bool = False


@dataclass
class MemoHook:
    """use_memo / use_callback hook 节点。

    Attributes:
        factory: 计算结果工厂函数（use_callback 时返回 fn 本身）。
        deps: 依赖列表。
        value: 缓存的计算结果（use_callback 时为函数对象）。
        last_deps: 上次计算时记录的依赖列表（用于检测变化）。
    """

    factory: Callable[[], Any] | None = None
    deps: Any = None
    value: Any = None
    last_deps: Any = None
    #: useImperativeHandle 最近一次写入的 ref 对象——卸载清理据此释放句柄
    #: （父组件换入新 ref 时旧 destroy 须清理最近写入的那个 ref）。
    #: 显式声明（架构修复）：此前由 ``_hooks_component`` 动态挂载。
    _last_ref: Any = None


@dataclass
class InputHook:
    """use_input hook 节点。

    Attributes:
        handler: 按键处理回调（签名 ``(event) -> bool``，True=消费）。
        is_active: 是否参与输入路由（False 时 hook 不参与）。
        mask: 输入掩码（React Ink 生态 mask 语义，password 输入）——非 None
            时，本 hook 收到的可打印输入（kind=="char"）以 ``mask * len(input)``
            替代后再传给 handler（与 ink-text-input 的
            ``mask.repeat(value.length)`` 显示掩码公式一致）。None=不掩码。
        focused: 焦点仲裁标志（useFocus 设置；True=参与焦点优先路由）。
        seq: 稳定递增序号（方向1 L3）——hook 实例唯一标识，router 签名以此
            替代 ``id(hook)``（id 复用风险：hook 被 GC 后新对象可能复用旧 id，
            导致 router 签名误判为未变而复用过期 router）。单调递增，无复用。
    """

    handler: Callable[[Any], bool] | None = None
    is_active: bool = True
    mask: str | None = None
    focused: bool = True
    seq: int = field(default_factory=lambda: next(_HOOK_SEQ))


@dataclass
class FullscreenHook:
    """use_fullscreen / use_modal hook 节点（模态输入接管声明，通用机制）。

    模态语义与「全屏」无关——router 仅用 ``is_active`` 判定是否吞掉未消费
    事件。消费方：
      - 模态全屏视图（TraceView，经 ``use_fullscreen``）；
      - 模态底部视图（UserSelectPopup 等，经 ``use_modal``——底部视图激活
        时底部区只渲染该视图、状态栏/输入区不渲染，未消费按键同样须被吞掉
        避免落入不存在的输入缓冲）。
    两种 hook 创建同一类型节点（``_next_hook`` 按下标复用须类型一致），
    语义名由调用侧选择。

    Attributes:
        is_active: 是否处于模态激活态。True 时 input router 在全部
            use_input handler 未消费时**吞掉**事件（返回 True）→ 事件不落入
            InputDispatcher 旧路径（输入缓冲），实现「打开时独占键盘输入」。
            False 时 hook 零影响（组件非模态渲染/已关闭）。
        seq: 稳定递增序号（与 InputHook 共享 ``_HOOK_SEQ`` 计数器——序号
            仅需全局唯一，router 签名依赖；替代 id 复用风险）。
    """

    is_active: bool = True
    seq: int = field(default_factory=lambda: next(_HOOK_SEQ))


@dataclass
class PasteHook:
    """usePaste hook 节点（React Ink usePaste 等价物）。

    Attributes:
        handler: 粘贴处理回调 ``(text: str) -> bool``（True=消费粘贴事件，
            阻断 use_input 通道——React Ink 语义：usePaste 与 useInput 独立
            通道，粘贴内容不转发给 useInput handler）。
        is_active: 是否参与粘贴路由（False 时 hook 不参与）。
        seq: 稳定递增序号（同 InputHook）。
    """

    handler: Callable[[str], bool] | None = None
    is_active: bool = True
    seq: int = field(default_factory=lambda: next(_HOOK_SEQ))


@dataclass
class MouseHook:
    """useMouseInput hook 节点（鼠标事件独立通道，框架扩展）。

    鼠标事件（SGR 1006：滚轮/点击/拖拽/移动）与键盘事件走同一 router，
    但仅在存在 active MouseHook 时会被消费（未声明鼠标交互的组件树零影响，
    事件放行给 InputDispatcher 的兜底路径）。

    Attributes:
        handler: 鼠标处理回调 ``(event) -> bool``（True=消费）。
        is_active: 是否参与鼠标路由（False 时 hook 不参与）。
        focused: 焦点仲裁标志（与 InputHook 一致——焦点组件优先收鼠标事件）。
        seq: 稳定递增序号（同 InputHook）。
    """

    handler: Callable[[Any], bool] | None = None
    is_active: bool = True
    focused: bool = True
    seq: int = field(default_factory=lambda: next(_HOOK_SEQ))


@dataclass
class Context:
    """create_context 创建的 context 对象。

    Attributes:
        default: 默认值（未找到 Provider 时返回）。
        tag: 唯一标签（provider host 标签）。
        Provider: provider host 标签字符串（``h(ctx.Provider, {"value": v}, ...)`` 可用）。
    """

    default: Any = None
    tag: str = ""
    Provider: str = ""


@dataclass
class SyncStoreHook:
    """useSyncExternalStore hook 节点（React 18 useSyncExternalStore 等价物）。

    Attributes:
        subscribe: 外部 store 订阅函数 ``(listener) -> cleanup_fn``。
        get_snapshot: 快照读取函数 ``() -> snapshot``。
        snapshot: 最近一次读取的快照值（跨渲染缓存）。
        cleanup: 订阅清理函数（卸载时调用取消订阅）。
        last_subscribe: 上次订阅的 subscribe 函数引用——subscribe 身份变化
            时重订阅（BUG-38：修复前 ``subscribed=True`` 短路，新 subscribe
            永不调用、旧订阅永不取消）。
    """

    subscribe: Any = None
    get_snapshot: Any = None
    snapshot: Any = None
    cleanup: Any = None
    last_subscribe: Any = None


#: hook 节点联合类型（Python 3.9 兼容：不用 `X | Y` 运行时求值）。
HookNode = Union[StateHook, RefHook, EffectHook, MemoHook, InputHook, SyncStoreHook, PasteHook, FullscreenHook, MouseHook]


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
    hooks: list = field(default_factory=list)
    hook_index: int = 0
    layout_box: Any = None
    deleted: bool = False
    #: context provider 值传递（每次渲染重置；子树 use_context 沿 return_ 链查找）
    contexts: dict = field(default_factory=dict)
    #: ErrorBoundary 边界标记（ErrorBoundary 组件渲染时置位，供异常沿 return_ 查找）
    _is_boundary: bool = False
    #: ErrorBoundary 捕获的异常对象（含类型/消息/栈）；None=无错误
    _boundary_error: Any = None
    #: onError 是否已回调（一次）
    _boundary_on_error_called: bool = False
    #: memo 组件上次渲染的 props（memo 短路比较基准）
    _last_memo_props: Any = None
    #: memo 组件上次渲染的元素 children（React children 属 props 一部分——
    #:   memo 短路须同时比较 children，方向4 修复）
    _last_memo_children: Any = None
    #: keyed 列表调和 moved 标记（方向B 步骤11）——位置变化信息（纯信息，
    #:   renderer 暂不消费；文档注明未来可用于 diff 尾部跳过）。
    moved: bool = False
    #: context 逐 fiber 缓存（方向B 步骤11）：ctx.tag → value。
    #:   同 fiber 多次 use_context 同 ctx 只 O(depth) 一次；provider 值变化时
    #:   reconciler 清空子树缓存并递增 ``hooks._context_version``。
    _context_cache: dict = field(default_factory=dict)
    #: context 缓存版本（命中校验：== ``hooks._context_version``）。
    _context_cache_version: int = 0
    #: context 依赖脏标记（BUG-16）：Provider 值变化经 ``_clear_context_cache_subtree``
    #:   置位；``use_context`` 消费后清除；memo 短路据此强制重渲染（React 语义：
    #:   context 变更强制重渲染消费者，与 memo 无关）。
    _context_dirty: bool = False
    #: provider 值变更检测基准（``_MISSING`` 表示未初始化；None 是合法值）。
    _last_provider_value: Any = _MISSING
    #: useId 分配的稳定唯一 ID（React 18 useId 语义；挂载时分配，fiber 复用
    #: 期间保持不变，卸载后不再访问）。
    _use_id: Any = None
    #: host ref 绑定（方向8 完善 react ink，useMeasure 支持）：host 元素
    #: ``ref`` prop 存入此处（RefHook/函数 ref）。layout 阶段后 reconciler
    #: 将 ``layout_box`` 写入 ``ref.current``（或调用函数 ref）——React 语义
    #: 中 host ref 指向 DOM 节点，本框架非全屏流动模型下指向布局盒（尺寸）。
    _host_ref: Any = None
    #: key 缓存（PERF-24）：``key`` property 首次访问时计算并缓存；props
    #: 变化（``reconciler._set_props``）时置 None 失效。调和热路径（
    #: ``_try_reuse_stable`` / 完整算法每帧对每个 fiber 访问 key）免重复
    #: ``props.get("key")`` + 派生字符串构建。
    _key_cache: str | None = None

    # ── 渲染/布局扩展状态（显式声明——此前为动态挂载的隐式属性，见下） ──
    # 说明（架构修复）：下列字段原由各渲染/布局模块以 ``fiber._xxx = ...``
    # 动态挂载、以 ``getattr(fiber, "_xxx", default)`` 读取——无静态检查、
    # 拼写错误静默降级、无生命周期契约。现统一在 Fiber 声明默认值，全部
    # 使用点改为直接属性访问（``hasattr``/``del`` 模式一并消除）。
    #: 组件树中是否存在静态行批量渲染 host（StaticLines）——``layout_tree``
    #: 每帧复位、``_measure`` 命中时置位；``components._find_committed_chat``
    #: 据此 O(1) 判定是否需 DFS（无 StaticLines 的树零 DFS）。
    _committed_chat_present: bool = False
    #: 组件树中是否存在 ``position="absolute"`` 节点——``layout_tree`` 每帧
    #: 复位、``_measure`` 容器分支检测到 absolute 子节点时置位；无绝对定位
    #: 的树跳过第二遍绝对定位遍历。
    _has_absolute_present: bool = False
    #: TEXT 测量缓存 ``(props, styled_len, avail_w, fill, result)``——同 props
    #: 引用 + 同宽度 + 同 styled 长度的帧免重测（``_layout_measure`` 唯一
    #: 写入点；绝对定位第二遍临时改写 props 后恢复并失效缓存）。
    _measure_cache: Any = None
    #: TEXT 布局换行结果（``list[Line]``）——``_measure`` 计算、``_paint``
    #: 复用（免二次包裹）；行宽/内容变化时由 ``_measure`` 重算覆盖。
    _wrapped_lines: Any = None
    #: 容器几何解析缓存 ``(props, 原始值快照, 结果)``——``_layout_measure``
    #: 维护（无变化帧跳过 padding/border/margin/gap 重复解析；props 引用或
    #: 原始值变化即失效，见 ``_container_geometry``）。
    _geom_cache: Any = None
    #: 输入区换行布局缓存 ``((text, max_input), (rows, wrapped_by_logical))``
    #: ——``input_area`` / ``_cursor`` 共享（同文本/宽度帧零重复换行计算）。
    _input_layout_cache: Any = None
    #: 静态行 host fiber 查找缓存（``components._find_committed_chat``）——
    #: 指向命中的 StaticLines host fiber；未找到时为 None。
    _committed_chat_cache: Any = None
    #: committed 前缀超宽行截断结果缓存 ``(src_prefix, src_len, truncated,
    #: width)``——``components.render_frame`` 维护（仅超宽前缀路径使用）。
    _truncated_prefix_cache: Any = None
    #: 静态行帧前缀缓存 ``(key, prefix, all_ok)``——``widgets.staticlines``
    #: 维护（命中即跳过画布写入，增量提交仅追加新增行）；
    #: ``components.render_frame`` 读取（前缀复用 + 行宽守卫；顶部/非顶部
    #: 统一本字段——此前 ``_static_prefix`` / ``_committed_prefix`` 双别名
    #: 同值同步，已收敛为单一字段）。
    _committed_prefix: Any = None
    #: 非 list 的 lines props 解析缓存 ``(原始对象, 解析后 list)``——
    #: ``widgets.staticlines._resolve_lines`` 维护（生成器只消费一次）。
    _resolved_lines: Any = None
    #: ErrorBoundary fallback 根标记——fallback 组件自身渲染异常时不再被边界
    #: 捕获（防递归重建 fallback）；由 ``reconciler._begin_work`` 设置。
    _is_fallback_root: bool = False
    #: useFocus 分配的焦点 id（显式 id 或自动 ``__focus_<n>__``）——组件卸载时
    #: ``_clear_focus_active`` 据此清空指向自身的激活焦点（防焦点悬挂）。
    _focus_id: Any = None
    #: TEXT 换行结果缓存 ``(ref, cache_wt, style_fp, lines, ref_len)``——
    #: ``_layout_measure`` 维护（同 props 引用/宽度/样式指纹的帧免重复换行）。
    _wrap_cache: Any = None
    #: 输入区占位符渐显状态键 ``(占位符文本, 时间桶)``——``_popup_builder``
    #: 维护（组件级持久，跨帧渐显进度连续）。
    _placeholder_fade_key: Any = None
    #: 父容器传播的可用高度（``height="50%"`` 百分比解析用；``None`` = 高度
    #: 内容驱动/未知，百分比无效）。由 ``_layout_measure`` 布局子节点前写入
    #: （子节点复用时会覆盖或清零，防残留旧父高度）。
    _parent_avail_h: Any = None

    # ── 派生属性 ──────────────────────────────────────

    @property
    def key(self) -> str:
        """fiber key（优先 props.key，否则按 type 派生；结果缓存）。"""
        cached = self._key_cache
        if cached is not None:
            return cached
        key = self.props.get("key")
        if key is None:
            if isinstance(self.type, str):
                key = f"host:{self.type}"
            else:
                # 模块限定：消除跨模块同名组件 key 冲突（仅影响无显式 key 的函数组件）
                mod = getattr(self.type, "__module__", "?")
                name = getattr(self.type, "__name__", repr(self.type))
                key = f"fn:{mod}.{name}"
        else:
            key = str(key)
        self._key_cache = key
        return key

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
    "MemoHook",
    "InputHook",
    "PasteHook",
    "FullscreenHook",
    "MouseHook",
    "SyncStoreHook",
    "Context",
    "HookNode",
    "Fiber",
]
