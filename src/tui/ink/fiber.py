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


@dataclass
class InputHook:
    """use_input hook 节点。

    Attributes:
        handler: 按键处理回调（签名 ``(event) -> bool``，True=消费）。
        is_active: 是否参与输入路由（False 时 hook 不参与）。
        focused: 焦点仲裁标志（useFocus 设置；True=参与焦点优先路由）。
        seq: 稳定递增序号（方向1 L3）——hook 实例唯一标识，router 签名以此
            替代 ``id(hook)``（id 复用风险：hook 被 GC 后新对象可能复用旧 id，
            导致 router 签名误判为未变而复用过期 router）。单调递增，无复用。
    """

    handler: Callable[[Any], bool] | None = None
    is_active: bool = True
    focused: bool = True
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
        subscribed: 是否已订阅（防止重复订阅）。
        last_subscribe: 上次订阅的 subscribe 函数引用——subscribe 身份变化
            时重订阅（BUG-38：修复前 ``subscribed=True`` 短路，新 subscribe
            永不调用、旧订阅永不取消）。
    """

    subscribe: Any = None
    get_snapshot: Any = None
    snapshot: Any = None
    cleanup: Any = None
    subscribed: bool = False
    last_subscribe: Any = None


#: hook 节点联合类型（Python 3.9 兼容：不用 `X | Y` 运行时求值）。
HookNode = Union[StateHook, RefHook, EffectHook, MemoHook, InputHook, SyncStoreHook, PasteHook]


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
    "SyncStoreHook",
    "Context",
    "HookNode",
    "Fiber",
]
