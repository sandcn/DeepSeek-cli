"""动画系统 — use_animation Hook + AnimationClock 全局时钟。

提供声明式动画支持，通过全局 AnimationClock 单例驱动所有动画实例。
使用弱引用管理动画注册，防止组件卸载后内存泄漏。

架构：
  AnimationClock（全局定时器）
    └─ 持久 daemon 线程 + threading.Event 周期性触发 on_tick → push CmdAnimationTick 到命令队列
       └─ render 线程处理 CmdAnimationTick → AnimationClock._tick()
          └─ 遍历所有注册的 _AnimationState，按各自 interval 更新 frame/time/delta

关键约束：
  - _tick() 在 render 线程中执行，确保无竞态
  - 使用 dict[id(anim), weakref.ref] 持有动画引用，组件卸载自动清理
  - use_animation 通过 use_effect 在 mount/unmount 时注册/注销
"""

from __future__ import annotations

import os
import threading
import time
import weakref
from dataclasses import dataclass
from typing import Any, Callable


# ── 预设 Spinner 帧集合 ──────────────────────────────────

SPINNER_FRAMES: dict[str, list[str]] = {
    "braille": ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"],
    "dots": ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"],
    "line": ["|", "/", "-", "\\"],
    "pulse": ["█", "▓", "▒", "░", "▒", "▓"],
    "bounce": ["[= ]", "[==]", "[ =]", "[==]"],
    "dots_wave": ["⠁", "⠂", "⠄", "⡀", "⠄", "⠂"],
    "arrow": ["→", "↘", "↓", "↙", "←", "↖", "↑", "↗"],
    "dots_matrix": ["⠁⠁⠁", "⠂⠂⠂", "⠄⠄⠄", "⡀⡀⡀", "⠄⠄⠄", "⠂⠂⠂"],
    "arc": ["◜", "◝", "◞", "◟"],
    "bouncing_ball": ["(●    )", "( ●   )", "(  ●  )", "(   ● )", "(    ●)", "(   ● )", "(  ●  )", "( ●   )"],
    "clock": ["🕐", "🕑", "🕒", "🕓", "🕔", "🕕", "🕖", "🕗", "🕘", "🕙", "🕚", "🕛"],
    "shark": ["▁", "▂", "▃", "▄", "▅", "▆", "▇", "█", "▇", "▆", "▅", "▄", "▃", "▂"],
}


# ── 动画状态 ────────────────────────────────────────────


@dataclass
class _AnimationState:
    """单个动画实例的运行时状态。

    由 use_animation 创建，注册到 AnimationClock 的弱引用集合中。
    所有时间以 time.monotonic() 为基准，单位为秒。

    Attributes:
        interval: 帧间隔（毫秒），每经过此时间 frame +1。
        is_active: 是否激活，False 时暂停更新但保留状态。
        frame: 离散帧计数（每次 interval 达标后 +1）。
        time: 累计毫秒（自动画创建/重置以来的连续时间）。
        delta: 上一帧到当前的毫秒差。
        _start_mono: 动画创建/重置时的 time.monotonic() 值。
        _last_frame_mono: 上一次 frame 递增时的 time.monotonic() 值。
    """
    interval: int = 100
    is_active: bool = True
    frame: int = 0
    time: float = 0.0
    delta: float = 0.0
    _start_mono: float = 0.0
    _last_frame_mono: float = 0.0


# ── 全局动画时钟 ────────────────────────────────────────


class AnimationClock:
    """全局动画时钟（单例）。

    所有 use_animation 实例共享同一个定时器。
    在 TuiEngine 启动时创建，停止时销毁。

    使用方式：
        clock = AnimationClock(on_tick=lambda: engine.push_cmd(CmdAnimationTick()))
        clock.start()   # 启动定时器
        ...
        clock.stop()    # 停止定时器

    Attributes:
        _instance: 全局单例引用（类级属性）。
        _running: 时钟运行标志（类级属性，防止单例泄漏）。
        _on_tick: 每帧回调（通常推送 CmdAnimationTick 到命令队列）。
        _animations: 已注册的动画状态集合（弱引用，自动清理）。
        _start_time: 时钟启动时的 time.monotonic() 值。
        _frame: 全局帧计数（每次 _tick() +1）。
        _tick_event: 控制持久 tick 线程启停的 Event。
        _tick_thread: 持久 tick 线程句柄。
    """

    _instance: "AnimationClock | None" = None
    _running: bool = False  # 类级运行标志，防止单例泄漏
    _DEFAULT_TICK_INTERVAL: float = 0.05  # 50ms = 20fps 时钟频率

    def __init__(
        self,
        on_tick: Callable[[], None],
        tick_interval: float = _DEFAULT_TICK_INTERVAL,
    ):
        """初始化动画时钟。

        Args:
            on_tick: 每帧回调函数，由定时器线程调用。
            tick_interval: 定时器间隔（秒），默认 0.05（20fps）。
        """
        self._on_tick = on_tick
        self._tick_interval = tick_interval
        # 使用 dict[id(anim), weakref.ref] 存储动画弱引用
        # weakref.ref 的哈希委托给目标对象，_AnimationState 不可哈希（mutable dataclass），
        # 因此使用 id(anim) 作为键来追踪注册的动画实例。
        self._animations: dict[int, weakref.ref[_AnimationState]] = {}
        self._start_time: float = 0.0
        self._frame: int = 0
        self._tick_event = threading.Event()
        self._tick_thread: threading.Thread | None = None
        self._lock = threading.Lock()

    # ── 单例管理 ──────────────────────────────────

    @classmethod
    def get_instance(cls) -> "AnimationClock | None":
        """获取全局单例实例。

        Returns:
            当前活跃的 AnimationClock 实例，未启动时返回 None。
        """
        return cls._instance

    @classmethod
    def _set_instance(cls, instance: "AnimationClock | None") -> None:
        """设置全局单例（内部使用）。"""
        cls._instance = instance

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> None:
        """启动动画时钟。

        设置全局单例引用，记录启动时间，启动持久 tick 线程。
        若单例已存在则拒绝重复启动（防止单例泄漏）。
        """
        if AnimationClock._instance is not None:
            return
        AnimationClock._running = True
        self._start_time = time.monotonic()
        AnimationClock._set_instance(self)
        self._tick_thread = threading.Thread(target=self._tick_loop, daemon=True)
        self._tick_thread.start()

    def stop(self) -> None:
        """停止动画时钟。

        取消持久 tick 线程，清除全局单例引用。
        重复调用安全（幂等）。
        """
        AnimationClock._running = False
        self._tick_event.set()  # 唤醒等待中的 tick 线程
        if self._tick_thread is not None:
            self._tick_thread.join(timeout=1.0)
            self._tick_thread = None
        AnimationClock._set_instance(None)

    # ── 持久 tick 线程 ────────────────────────────

    def _tick_loop(self) -> None:
        """持久 tick 循环，使用 Event 控制启停。

        在 daemon 线程中运行，以 _tick_interval 间隔周期性调用 _on_tick。
        Event.wait() 替代 Timer 避免高频线程创建开销。
        """
        while AnimationClock._running:
            self._tick_event.wait(self._tick_interval)
            if not AnimationClock._running:
                break
            try:
                self._on_tick()
            except Exception:
                import logging
                _log = logging.getLogger(__name__)
                _log.debug("AnimationClock tick 异常", exc_info=True)

    # ── 动画注册 ──────────────────────────────────

    def register(self, anim: _AnimationState) -> None:
        """注册动画实例。

        使用弱引用存储，以 id(anim) 为键去重。
        组件卸载后弱引用自动失效，_tick() 中清理。

        Args:
            anim: _AnimationState 实例。
        """
        with self._lock:
            self._animations[id(anim)] = weakref.ref(anim)

    def unregister(self, anim: _AnimationState) -> None:
        """注销动画实例。

        Args:
            anim: _AnimationState 实例。
        """
        with self._lock:
            self._animations.pop(id(anim), None)

    # ── 帧更新（在 render 线程中执行）────────────

    def _tick(self) -> None:
        """执行一帧：更新所有注册的动画状态。

        此方法在 render 线程中通过命令队列调用，确保无竞态条件。
        遍历所有注册的动画，对每个活跃动画：
        1. 更新累计时间 time
        2. 若距离上次帧递增已超过 interval，frame +1 并更新 delta
        同时清理已死亡的弱引用条目。
        """
        self._frame += 1
        now = time.monotonic()

        with self._lock:
            # 收集活跃动画并清理死亡引用
            dead_keys: list[int] = []
            animations: list[_AnimationState] = []
            for key, ref in self._animations.items():
                anim = ref()
                if anim is None:
                    dead_keys.append(key)
                else:
                    animations.append(anim)
            for key in dead_keys:
                del self._animations[key]

        for anim in animations:
            if not anim.is_active:
                continue

            # 更新累计时间（毫秒）
            anim.time = (now - anim._start_mono) * 1000

            # 检查是否到达下一帧
            elapsed_since_last = (now - anim._last_frame_mono) * 1000
            if elapsed_since_last >= anim.interval:
                anim.delta = elapsed_since_last
                anim._last_frame_mono = now
                anim.frame += 1

    # ── 属性 ──────────────────────────────────────

    @property
    def elapsed(self) -> float:
        """从时钟启动到现在的毫秒数。

        Returns:
            累计毫秒，时钟未启动时返回 0.0。
        """
        if self._start_time == 0.0:
            return 0.0
        return (time.monotonic() - self._start_time) * 1000


# ── use_animation Hook ──────────────────────────────────


def use_animation(options: dict | None = None) -> dict:
    """动画 Hook — 在组件内驱动帧动画。

    通过全局 AnimationClock 单例驱动，所有 use_animation 实例共享同一时钟。
    使用 use_effect 在 mount 时注册、unmount 时注销。

    Args:
        options: 可选配置字典：
            - "interval" (int): 帧间隔毫秒，默认 100。
            - "isActive" (bool): 是否激活，默认 True。False 时暂停更新但保留状态。

    Returns:
        {
            "frame": int,       # 离散帧计数（每次 interval 达标后 +1）
            "time": float,      # 累计毫秒（连续时间，不受 interval 影响）
            "delta": float,     # 上一帧到当前的毫秒差
            "reset": Callable,  # 重置所有计数器（frame/time/delta 归零）
        }

    Raises:
        HookError: 在组件 render 上下文外调用时。

    示例:
        >>> anim = use_animation({"interval": 200})
        >>> spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
        >>> char = spinner[anim["frame"] % len(spinner)]
    """
    from ..vdom.hooks import use_effect, use_ref

    opts = options or {}
    interval = int(opts.get("interval", 100))
    is_active = bool(opts.get("isActive", True))

    # 使用 use_ref 持有跨渲染周期的 _AnimationState
    state_ref = use_ref(None)

    if state_ref["current"] is None:
        state = _AnimationState(
            interval=interval,
            is_active=is_active,
            _start_mono=time.monotonic(),
            _last_frame_mono=time.monotonic(),
        )
        state_ref["current"] = state
    else:
        state = state_ref["current"]
        state.interval = interval
        state.is_active = is_active

    # 注册/注销到全局时钟（mount/unmount 生命周期）
    def _mount() -> Callable[[], None] | None:
        clock = AnimationClock.get_instance()
        if clock is not None:
            clock.register(state)

        def _cleanup() -> None:
            c = AnimationClock.get_instance()
            if c is not None:
                c.unregister(state)

        return _cleanup

    use_effect(_mount, [])

    def reset() -> None:
        """重置所有计数器（frame/time/delta 归零，保留 start_time）。"""
        state.frame = 0
        state.time = 0.0
        state.delta = 0.0
        state._start_mono = time.monotonic()
        state._last_frame_mono = time.monotonic()

    return {
        "frame": state.frame,
        "time": state.time,
        "delta": state.delta,
        "reset": reset,
    }


# ── use_spinner Hook ────────────────────────────────────


def use_spinner(options: dict | None = None) -> dict:
    """预设 spinner 动画 Hook。

    基于 SPINNER_FRAMES 预设帧集合，返回当前帧的 spinner 字符。
    内部调用 use_animation 驱动帧更新。

    Args:
        options: 可选配置字典：
            - "type" (str): spinner 类型，默认 "dots"。
              可选值见 SPINNER_FRAMES 的键：braille, dots, line, pulse,
              bounce, dots_wave, arrow。
            - "interval" (int): 帧间隔毫秒，默认 80。
            - "color" (str | None): 颜色名（预留），默认 None。

    Returns:
        {
            "char": str,       # 当前 spinner 字符
            "frame": int,      # 当前帧号
            "time": float,     # 累计毫秒
        }

    示例:
        >>> spinner = use_spinner({"type": "braille", "interval": 100})
        >>> print(spinner["char"])  # 每帧输出不同的 braille 字符
    """
    opts = options or {}
    spinner_type = opts.get("type", "dots")
    interval = int(opts.get("interval", 80))

    frames = SPINNER_FRAMES.get(spinner_type, SPINNER_FRAMES["dots"])
    anim = use_animation({"interval": interval})

    idx = anim["frame"] % len(frames)
    return {
        "char": frames[idx],
        "frame": anim["frame"],
        "time": anim["time"],
    }


# ── use_progress Hook ───────────────────────────────────


def use_progress(options: dict | None = None) -> dict:
    """进度条 Hook。

    支持确定模式（value 给定）和 indeterminate 模式（value=None）：
    - 确定模式：渲染填充块 + 百分比数字。
    - indeterminate 模式：3 格亮块动画扫过，无百分比。

    Args:
        options: 可选配置字典：
            - "value" (float | None): 进度值 0.0~1.0，None 表示 indeterminate 模式。
            - "width" (int): 进度条宽度（字符数），默认 20。
            - "style" (str): 样式名（预留），默认 "bar"。
            - "color" (str): 颜色名（预留），默认 "cyan"。

    Returns:
        {
            "rendered": str,   # 渲染后的进度条字符串，如 "[████████░░░░░░░░] 50%"
            "percent": int,    # 百分比整数（indeterminate 模式返回 0）
        }

    示例:
        >>> prog = use_progress({"value": 0.5, "width": 10})
        >>> print(prog["rendered"])  # "[█████░░░░░] 50%"
        >>> indet = use_progress({"value": None, "width": 20})
        >>> print(indet["rendered"])  # 动画扫过
    """
    opts = options or {}
    value = opts.get("value", None)
    width = int(opts.get("width", 20))

    if value is not None:
        # 确定模式
        clamped = max(0.0, min(1.0, float(value)))
        filled = int(clamped * width)
        percent = int(clamped * 100)
        rendered = "[" + "█" * filled + "░" * (width - filled) + f"] {percent}%"
    else:
        # indeterminate 模式：动画扫过 3 格亮块
        anim = use_animation({"interval": int(opts.get("interval", 80))})
        frame = anim["frame"]
        # 亮块在宽度为 width 的条上滑动，周期为 width*2
        pos = frame % max(width * 2, 1)

        # 3 格亮块扫过
        bars = ["░"] * width
        for i in range(3):
            idx = (pos + i) % width
            bars[idx] = "█"

        rendered = "[" + "".join(bars) + "]"
        percent = 0

    return {"rendered": rendered, "percent": percent}


# ── use_typewriter Hook ─────────────────────────────────


def use_typewriter(text: str, options: dict | None = None) -> dict:
    """打字机效果 Hook。

    逐字显示文本，配合可选光标闪烁，模拟打字机输出效果。
    内部调用 use_animation 驱动帧更新，speed 控制每帧前进字符数。

    Args:
        text: 要逐字显示的文本。非 str 时自动 str() 转换。
        options: 可选配置字典：
            - "speed" (int): 每字符前进所需毫秒，默认 30。speed 越小越快
              （每 speed 毫秒前进约 1 字符）。
            - "cursor" (bool): 是否显示光标，默认 True。
            - "cursor_style" (str): 光标样式，支持 "block"（默认 ▊）、
              "line"（|）、"underscore"（_）。
            - "cursor_char" (str): 光标字符，默认由 cursor_style 决定，
              也可直接指定覆盖 style 推导值。

    Returns:
        {
            "output": str,           # 当前可见文本（含光标字符，done 后延迟移除）
            "progress": float,       # 0.0~1.0 完成度
            "done": bool,            # 是否已完整显示全部文本
            "cursor_visible": bool,  # 当前光标是否可见
            "cursor_char": str,      # 当前使用的光标字符
            "reset": Callable,       # 重置打字机状态（重新开始）
        }

    示例:
        >>> tw = use_typewriter("Hello, World!", {"speed": 50})
        >>> print(tw["output"])   # "Hel▊"（逐帧增加）
        >>> print(tw["done"])     # False（未完成时）
    """
    opts = options or {}
    speed = int(opts.get("speed", 30))
    cursor = bool(opts.get("cursor", True))
    cursor_style = str(opts.get("cursor_style", "block"))
    cursor_chars = {"block": "▊", "line": "|", "underscore": "_"}
    cursor_char = str(opts.get("cursor_char", cursor_chars.get(cursor_style, "▊")))

    # 环境变量 CHAT_UI_TYPING_CURSOR 全局控制光标启用/禁用
    env_cursor_val = os.environ.get("CHAT_UI_TYPING_CURSOR", "").strip()
    if env_cursor_val and env_cursor_val.lower() in ("0", "false", "no", "off"):
        cursor = False

    # 安全处理 text
    safe_text = str(text) if not isinstance(text, str) else text
    text_len = len(safe_text)

    anim = use_animation({"interval": max(speed // 10, 16)})

    # 每帧前进字符数 = 累计时间 / speed
    chars_shown = int(anim["time"] // speed) if speed > 0 else text_len
    visible_len = min(chars_shown, text_len)

    done = visible_len >= text_len

    output = safe_text[:visible_len]

    # 光标闪烁（与文本前进解耦，使用 anim["frame"] 独立控制）
    cursor_visible = False
    if cursor and not done:
        cursor_visible = anim["frame"] % 2 == 0
        if cursor_visible:
            output += cursor_char
    elif cursor and done:
        # done 后延迟约 300ms 再移除光标
        done_time = text_len * speed
        if anim["time"] < done_time + 300:
            cursor_visible = anim["frame"] % 2 == 0
            if cursor_visible:
                output += cursor_char

    progress = visible_len / text_len if text_len > 0 else 1.0

    def reset() -> None:
        """重置打字机状态以重新开始。"""
        anim["reset"]()

    return {
        "output": output,
        "progress": progress,
        "done": done,
        "cursor_visible": cursor_visible,
        "cursor_char": cursor_char,
        "reset": reset,
    }
