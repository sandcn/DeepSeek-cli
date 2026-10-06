"""HookContext — 渲染会话级 hooks 状态容器（多会话隔离的唯一真源）。

架构修复（P0）：原实现把全部 hooks 可变状态（当前 fiber 栈 / 重渲染回调 /
context 注册表 / 焦点 / 窗口尺寸 / 输入 router 回调 / app control / std
访问器 / 屏幕阅读器 / 动画驱动）定义为 ``hooks`` 模块级全局变量——同一进程
内**只能存在一个活跃渲染会话**：第二个 ``Reconciler`` / ``InkSession`` /
``render()`` 构造即覆盖第一个的状态（重渲染回调、router 注入、app control
全部串台）。本模块把状态收拢为**可实例化对象**：

  - 每个 ``Reconciler`` 持有一个 ``HookContext``（渲染根级状态）；
  - 渲染期间经 ``push_context`` 激活为「当前上下文」，hooks 函数读取它；
  - 输入事件处理 / 订阅回调等**非渲染期**访问经闭包捕获的 ctx 实例完成
    （不依赖「当前上下文」——那些时机渲染栈可能为空）；
  - ``hooks`` 模块以 PEP 562 ``__getattr__`` / ``__setattr__`` 代理到
    「当前上下文」，未激活时回退 ``default_context()``——保持既有
    ``hooks._xxx`` 读写契约（测试/外部注入）单会话语义不变。

线程模型：当前上下文用 ``threading.local`` 保存——渲染线程拥有自己的激活
上下文，测试线程/主线程互不干扰；未激活线程回退到模块级默认上下文
（单会话/独立使用场景，与旧行为等价）。
"""

from __future__ import annotations

import itertools
import threading

#: HookContext 全部状态字段（__slots__ 固化，防拼写错误静默创建新字段）。
_SLOTS = (
    # ── hook 基础设施 ──
    "current_fiber_stack",
    "schedule_callback",
    # ── context（注册表为进程级共享，见 hooks.py；此处仅会话级版本号） ──
    "context_version",
    # ── 流访问器 ──
    "stdin_accessor",
    "stdout_accessor",
    "stderr_accessor",
    # ── 会话注入 ──
    "input_router_callback",
    "app_control",
    "render_flush_fn",
    "suspend_terminal_fn",
    "cursor_position_fn",
    "window_size_accessor",
    "screen_reader_enabled",
    # ── 焦点 ──
    "focus_enabled",
    "focus_active",
    "focus_ids",
    "focus_id_seq",
    # ── 窗口尺寸 ──
    "window_size",
    "window_size_version",
    "window_size_listeners",
    # ── 输入标志 ──
    "any_key_pressed",
    # ── 动画共享驱动 ──
    "animation_tick",
    "animation_last_time",
    "animation_listeners",
)


class HookContext:
    """单个渲染根的 hooks 状态容器。

    字段语义与旧模块级全局变量一一对应（``current_fiber_stack`` →
    ``_current_fiber_stack`` 等）；命名去掉前导下划线（私有性由 ``hooks``
    门面代理的公开契约承担）。
    """

    __slots__ = _SLOTS

    def __init__(self) -> None:
        self.current_fiber_stack: list = []
        self.schedule_callback = None
        self.context_version = 0
        self.stdin_accessor = None
        self.stdout_accessor = None
        self.stderr_accessor = None
        self.input_router_callback = None
        self.app_control = None
        self.render_flush_fn = None
        self.suspend_terminal_fn = None
        self.cursor_position_fn = None
        self.window_size_accessor = None
        self.screen_reader_enabled = False
        self.focus_enabled = True
        self.focus_active = None
        self.focus_ids: list = []
        #: useFocus 自动 id 分配序号（``__focus_<n>__``）——每会话独立计数器
        #: （多会话下 id 不跨会话碰撞）。
        self.focus_id_seq = itertools.count()
        self.window_size = (80, 24)
        self.window_size_version = 0
        self.window_size_listeners: set = set()
        self.any_key_pressed = False
        self.animation_tick = 0
        self.animation_last_time = 0.0
        self.animation_listeners: set = set()

    # ── 快照 / 还原（renderToString 等隔离场景） ──

    def snapshot(self) -> dict:
        """复制全部状态为普通 dict（浅拷贝容器，元素引用共享）。"""
        out = {}
        for name in _SLOTS:
            value = getattr(self, name)
            if isinstance(value, list):
                value = list(value)
            elif isinstance(value, set):
                value = set(value)
            out[name] = value
        return out

    def restore(self, saved: dict) -> None:
        """从 ``snapshot()`` 结果还原全部状态（缺失键忽略）。"""
        for name, value in saved.items():
            if name in _SLOTS:
                setattr(self, name, value)

    def install_headless(self, columns: int = 80, rows: int = 24) -> None:
        """安装「无终端」环境（renderToString 用）：流/焦点/光标返回安全默认。"""
        self.stdin_accessor = lambda: None
        self.stdout_accessor = lambda: None
        self.stderr_accessor = lambda: None
        self.app_control = None
        self.render_flush_fn = None
        self.suspend_terminal_fn = None
        self.cursor_position_fn = None
        self.window_size_accessor = lambda: (columns, rows)
        self.input_router_callback = None

    def reset_animation(self) -> None:
        """复位动画共享驱动（会话复用/测试）。"""
        self.animation_tick = 0
        self.animation_last_time = 0.0
        self.animation_listeners.clear()


#: 模块级默认上下文（无激活上下文时使用——单会话/独立 hooks 调用）。
_default_context = HookContext()
_local = threading.local()


def default_context() -> HookContext:
    """返回默认上下文（无激活上下文时的回退）。"""
    return _default_context


def current_context() -> HookContext:
    """返回当前线程的激活上下文；未激活时回退默认上下文。"""
    ctx = getattr(_local, "ctx", None)
    return ctx if ctx is not None else _default_context


def push_context(ctx: HookContext) -> None:
    """激活上下文（返回上一个，供 ``pop_context`` 还原；支持嵌套）。"""
    previous = getattr(_local, "ctx", None)
    _local.stack = getattr(_local, "stack", None) or []
    _local.stack.append(previous)
    _local.ctx = ctx


def pop_context() -> None:
    """还原上一个激活上下文。"""
    stack = getattr(_local, "stack", None)
    if stack:
        _local.ctx = stack.pop()
    else:
        _local.ctx = None


__all__ = [
    "HookContext",
    "default_context",
    "current_context",
    "push_context",
    "pop_context",
]
