"""TUI 统一状态容器 + 共享常量 — 合并 _state_tree + _constants

将分散在 _state_tree.py（状态容器）和 _constants.py（常量+辅助函数）
中的内容合并为单一模块，消除跨文件引用碎片。

层次：
  - 子状态值对象：UISessionState, InputState, StreamingState
  - TUIStateTree：聚合三子状态的统一容器
"""

from __future__ import annotations

import dataclasses
import threading
import time
from dataclasses import field
from src._compat import dataclass


# ═══════════════════════════════════════════════════════════
# 常量
# ═══════════════════════════════════════════════════════════

_ESC_DOUBLE_CLICK_INTERVAL = 0.5
"""两次 Esc 间隔 < 500ms 视为双击。"""


# ═══════════════════════════════════════════════════════════
# 子状态值对象
# ═══════════════════════════════════════════════════════════


@dataclass(frozen=True, slots=True)
class UISessionState:
    """会话级数据（不可变值对象）。

    替代 StatusBarState + TUIState 的会话字段。
    修改时使用 dataclasses.replace() 创建新快照。
    """
    model: str = ""
    message_count: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    status_text: str = ""
    session_title: str = ""
    session_duration: float = 0.0
    show_time: bool = True
    show_tokens: bool = True
    show_duration: bool = False
    cost_usd: float = 0.0       # Claude 风格：费用估算（基于 token 数粗略估算）
    context_pct: float = 0.0    # Claude 风格：上下文使用百分比


@dataclass(slots=True)
class InputState:
    """输入状态（可变，线程安全）。

    替代 TUIInputState。Esc 双击检测使用内部锁保护。

    注意：模型状态统一由 UISessionState.model 管理（单数据源），
    InputState 不再持有 current_model 字段。
    """
    _last_esc_time: float = 0.0
    _esc_lock: threading.Lock = field(default_factory=threading.Lock)

    def record_esc_press(self) -> bool:
        """记录一次 Esc 按键，返回 True 表示双击（<500ms 内两次按下）。"""
        now = time.monotonic()
        with self._esc_lock:
            if now - self._last_esc_time < _ESC_DOUBLE_CLICK_INTERVAL:
                self._last_esc_time = 0.0
                return True
            self._last_esc_time = now
            return False

    def reset_esc_state(self) -> None:
        """重置 Esc 双击检测状态。"""
        with self._esc_lock:
            self._last_esc_time = 0.0


@dataclass(slots=True)
class StreamingState:
    """流式输出临时状态（可变，高频更新）。

    与 UISessionState 分离的原因：变化频率极高，不可变快照开销大。

    ``speed`` 为基于 output_tokens / elapsed 自动计算的 property，
    无需外部调用 update_streaming_speed()，消除 P1 Bug（永为 0.0）。
    """
    active: bool = False
    start_time: float = 0.0
    output_tokens: int = 0
    _speed_override: float = 0.0  # 可选手动覆盖（外部不调用时保持 0.0）

    @property
    def speed(self) -> float:
        """获取 token 速率（tok/s）。

        优先使用 output_tokens / elapsed 自动计算，
        仅在未启动或无输出时回退 `_speed_override`（0.0）。
        """
        if self.active and self.elapsed > 0 and self.output_tokens > 0:
            return self.output_tokens / self.elapsed
        return self._speed_override

    @speed.setter
    def speed(self, value: float) -> None:
        """设置速率覆盖值（供外部手动注入使用）。"""
        self._speed_override = value

    @property
    def elapsed(self) -> float:
        """流式输出已进行的时间（秒）。"""
        if not self.active or self.start_time <= 0:
            return 0.0
        return time.monotonic() - self.start_time

    def start(self) -> None:
        """进入流式状态。已在流式模式时不重置（工具间隙保持连续）。"""
        if self.active:
            return
        self.active = True
        self.start_time = time.monotonic()
        self.output_tokens = 0
        self._speed_override = 0.0

    def stop(self) -> None:
        """退出流式状态，同时重置 token 计数和速率。"""
        self.active = False
        self.output_tokens = 0
        self._speed_override = 0.0


# ═══════════════════════════════════════════════════════════
# TUIStateTree — 统一状态容器
# ═══════════════════════════════════════════════════════════


class TUIStateTree:
    """TUI 统一状态容器。

    聚合四个子状态，提供单一入口访问全部 TUI 运行时数据。
    与 ITUIStateTree Protocol 一致，支持结构类型匹配。

    用法：
        tree = TUIStateTree()
        tree.session = dataclasses.replace(tree.session, model="gpt-4")
        tree.streaming.start()
    """

    __slots__ = ("_session", "_input", "_streaming")

    def __init__(self) -> None:
        self._session: UISessionState = UISessionState()
        self._input: InputState = InputState()
        self._streaming: StreamingState = StreamingState()

    # ── 子状态属性 ──

    @property
    def session(self) -> UISessionState:
        return self._session

    @property
    def input(self) -> InputState:
        return self._input

    @property
    def streaming(self) -> StreamingState:
        return self._streaming

    def update_session(self, **kwargs) -> None:
        """批量更新会话字段（使用 dataclasses.replace 创建新快照）。"""
        self._session = dataclasses.replace(self._session, **kwargs)


__all__ = [
    # 子状态
    "UISessionState",
    "InputState",
    "StreamingState",
    # 统一容器
    "TUIStateTree",
]
