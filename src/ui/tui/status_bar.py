"""StatusBar — 单数据源状态栏（从 TUIStateTree 直接读取）

职责：
  - 从 TUIStateTree 读取数据，无本地状态持有
  - 所有的 setter 直接写入 TUIStateTree
  - 渲染委托给同模块的纯函数 render_normal/render_streaming_line

状态分两层（均在 TUIStateTree 中）：
  - UISessionState（不可变值对象）：会话级稳定数据
  - StreamingState（可变）：流式输出临时状态

用法：
    status_bar = StatusBar(tree)
    status_bar.set_model("gpt-4")       # → tree.update_session(model="gpt-4")
    line = status_bar.render()           # → reads from tree.session + tree.streaming
"""

from __future__ import annotations

import time

from ..ansi import strip_ansi, truncate_ansi_sgr
from ..colors import RESET, BOLD, CYAN_256, GREEN_256, \
    DARK_GRAY_256, GRAY_256, gradient_range
from ..theme import THEME
from ..parallel._text_formatter import TextFormatter
from ._terminal import is_narrow, get_terminal_width
from ._time_format import format_elapsed, format_speed
from ._state import TUIStateTree, UISessionState, StreamingState

# ── 流式状态行空格常量（图标与数值间视觉间距） ──
_SP = " "  # 单一空格，视觉平衡

# 状态栏极窄屏阈值：窄于此宽度仅显示模型名+消息数
# 与 _terminal.py 的 EXTRA_NARROW_THRESHOLD (50) 对齐，统一窄屏三级阈值为 80/50
_STATUS_BAR_COMPACT_THRESHOLD = 50


class StatusBar:
    """单数据源状态栏 — 所有数据从 TUIStateTree 实时读取，实例本身无本地缓存。

    不再持有 _state 和 _streaming 副本，消除双状态源问题。
    保留 render() / streaming / start_streaming / stop_streaming 等有价值的方法。
    直接状态更新请使用 tree.update_session()（消除纯转发 setter 层）。

    用法：
        tree = TUIStateTree()
        status_bar = StatusBar(tree)
        line = status_bar.render()           # 从 tree.session / tree.streaming 读取
        tree.update_session(model="gpt-4")   # 直接写状态树
    """

    def __init__(self, state_tree: TUIStateTree) -> None:
        self._tree = state_tree

    # ── 状态属性（从 TUIStateTree 读取） ─────────────────

    @property
    def streaming(self) -> bool:
        """是否处于流式输出状态。"""
        return self._tree.streaming.active

    @property
    def state_snapshot(self) -> UISessionState:
        """获取当前会话数据的不可变快照。"""
        return self._tree.session

    # ── Streaming mode（直接写入 tree.streaming） ─────

    def start_streaming(self) -> None:
        """进入流式输出状态：记录开始时间。

        已在流式模式时调用不重置计时（工具调用间隙保持连续）。
        """
        self._tree.streaming.start()

    def stop_streaming(self) -> None:
        """退出流式输出状态（token 计数/速率由 StreamingState.stop() 清零）。"""
        self._tree.streaming.stop()

    def update_streaming_tokens(self, output_tokens: int) -> None:
        """更新流式输出期间的 token 计数（从全局统计读取）。"""
        self._tree.streaming.output_tokens = output_tokens

    def update_streaming_speed(self, tok_per_sec: float) -> None:
        """更新流式输出期间的 token 速率（tokens/sec）。"""
        self._tree.streaming.speed = tok_per_sec

    # ── Render ──────────────────────────────────────────

    def render(self) -> str:
        """渲染状态栏文本（返回 ANSI 格式字符串）。

        流式模式 → 推进脉动指示器动画 + 实时耗时/token（委托 render_streaming_line）
        普通模式 → 拼装各信息段（委托 render_normal）
        自动根据终端宽度精简显示。

        数据从 TUIStateTree 直接读取，无本地缓存。
        """
        if self._tree.streaming.active:
            # ★ 推进脉动指示器动画（P1 修复：确保 tick_pulse() 在每次渲染前调用）
            self._tree.streaming.tick_pulse()
            return render_streaming_line(self._tree.session, self._tree.streaming)
        return render_normal(self._tree.session)


# ── 纯渲染函数（无 Side Effect） ──────────────────────────
#
# 输入 UISessionState + StreamingState（值对象），输出 ANSI 字符串。
# 可独立测试（不依赖 StatusBar 实例）。


def _narrow_split_line(line: str, max_w: int, half: int) -> str:
    """窄屏时左右分栏截断 ANSI 字符串（保留两侧，中间用 .. 连接）。

    Args:
        line: 含 ANSI 转义序列的原始文本。
        max_w: 最大可见字符宽度。
        half: 左右各保留的可见字符数。

    Returns:
        截断后的文本（ANSI 安全）。
    """
    plain = strip_ansi(line)
    if len(plain) <= max_w:
        return line
    left = truncate_ansi_sgr(line, half)
    right = truncate_ansi_sgr(line, half, from_end=True)
    return f"{left}{GRAY_256}\u00b7\u00b7\u00b7{RESET}{right}"


def render_normal(state: UISessionState) -> str:
    """渲染普通模式（非流式）状态栏文本。

    Args:
        state: 会话级状态快照。

    Returns:
        ANSI 格式的状态栏文本。
    """
    narrow = is_narrow()
    parts = build_normal_parts(state, narrow=narrow)
    if not narrow:
        sep = f" {DARK_GRAY_256}\u00b7{RESET} "
    else:
        sep = " "
    line = sep.join(parts)
    # 窄屏判定分工：
    # - build_normal_parts(): 极窄屏(≤60)提前返回精简部件
    # - render_normal(): 一般窄屏(<80)左右分栏截断
    if narrow:
        tw = get_terminal_width()
        max_w = max(tw - 2, 20)
        half = max_w // 2 - 3
        line = _narrow_split_line(line, max_w, half)
    return line


def _model_label(state: UISessionState) -> str:
    """模型名标签 — 使用 256 色青色图标 + 主题高亮色模型名。"""
    if state.model:
        return f"{CYAN_256}\u25c9{RESET} {BOLD}{THEME['title']}{state.model}{RESET}"
    return f"{GRAY_256}\u25c9 no model{RESET}"


def _build_detail_parts(state: UISessionState, narrow: bool) -> list[str]:
    """构建详细信息部件（消息数/标题/Token/状态/时长/时间）。

    Args:
        state: 会话级状态快照。
        narrow: 是否为窄屏模式。

    Returns:
        信息段字符串列表。
    """
    parts: list[str] = []

    # 消息数（带 ◆ 图标 — 亮色）
    if state.message_count > 0:
        count_str = f"{state.message_count}m"
        parts.append(f"{GREEN_256}\u25c6{RESET} {count_str}")

    # 会话标题
    if state.session_title and not narrow:
        title_trunc = state.session_title[:20]
        parts.append(f"{DARK_GRAY_256}\u300c{title_trunc}\u300d{RESET}")

    # Token 用量（带 ⬡ 图标 — 双色：输入↑青色，输出↓绿色）
    if state.show_tokens and (state.input_tokens > 0 or state.output_tokens > 0):
        in_str = TextFormatter.format_token_count(state.input_tokens)
        out_str = TextFormatter.format_token_count(state.output_tokens)
        parts.append(f"{CYAN_256}\u2b21{RESET}{CYAN_256}{in_str}\u2191{RESET}{GREEN_256}{out_str}\u2193{RESET}")

    # 状态文本
    if state.status_text:
        parts.append(f"{THEME['success']}{state.status_text}{RESET}")

    # 会话持续时间（带 ⏱ 图标 — 琥珀色）
    if state.show_duration and state.session_duration > 0:
        dur_str = format_elapsed(state.session_duration)
        parts.append(f"\033[38;5;214m\u23f1{RESET}{dur_str}")

    # 当前时间
    if state.show_time and not narrow:
        t = time.strftime("%H:%M")
        parts.append(f"{DARK_GRAY_256}{t}{RESET}")

    return parts


def build_normal_parts(state: UISessionState, narrow: bool | None = None) -> list[str]:
    """构建状态栏部件列表（公开 API，可独立调用）。

    Args:
        state: 会话级状态快照。
        narrow: 是否窄屏。None 时自动检测（调用 is_narrow()）。

    Returns:
        信息段字符串列表，调用方自行拼接（含分隔符）。
    """
    parts: list[str] = []
    if narrow is None:
        narrow = is_narrow()
    tw = get_terminal_width()

    parts.append(_model_label(state))

    # 极窄屏：只显示模型名 + 消息数，提前返回
    if narrow and tw < _STATUS_BAR_COMPACT_THRESHOLD:
        if state.message_count > 0:
            parts.append(f"{GRAY_256}{state.message_count}m{RESET}")
        return parts

    parts.extend(_build_detail_parts(state, narrow))
    return parts


# ── 脉动呼吸色号序列（暗青→亮青→暗青） ──
_PULSE_COLORS: list[int] = gradient_range(36, 45, 3) + [40]
"""脉动呼吸色号：[36(暗青), 40(中青), 45(亮青), 40(中青)]，对称呼吸周期。"""

# ── 模型名呼吸色序（暗青32→亮青45→中青40→亮青45，4帧柔和呼吸） ──
_MODEL_BREATH_COLORS: list[int] = [32, 45, 40, 45]
"""模型名呼吸色号：暗青(32)↔亮青(45)↔中青(40)↔亮青(45)，4 帧柔和呼吸。"""


def render_streaming_line(state: UISessionState, streaming: StreamingState) -> str:
    """渲染流式输出状态行。

    格式：◉ gpt-4 ◍ · ⏱ 3.2s · ⬡ 450t · ⚡ 120t/s

    Args:
        state: 会话级状态快照（主要用于获取模型名）。
        streaming: 流式输出状态（计时/Token/速率）。

    Returns:
        ANSI 格式的流式状态行文本（含脉动指示器）。
    """
    elapsed = streaming.elapsed
    tokens = streaming.output_tokens
    speed = streaming.speed

    # ── 脉动指示器帧 ──
    _PULSE_FRAMES = ["\u25cc", "\u25cd", "\u25cf", "\u25cd"]  # ◌ ◍ ● ◍
    pulse_idx = streaming.pulse_phase % 4
    pulse_char = _PULSE_FRAMES[pulse_idx]
    pulse_color = _PULSE_COLORS[pulse_idx]  # 脉动呼吸色号：暗青→亮青→暗青

    parts: list[str] = []
    if state.model:
        # ── 模型名呼吸色（复用 pulse_phase，窄屏降级到固定色） ──
        if is_narrow():
            model_color = THEME['title']
        else:
            breath_idx = streaming.pulse_phase % len(_MODEL_BREATH_COLORS)
            model_color = f"\033[38;5;{_MODEL_BREATH_COLORS[breath_idx]}m"
        
        parts.append(f"{CYAN_256}\u25c9{RESET} {BOLD}{model_color}{state.model}{RESET}"
                     f" \033[38;5;{pulse_color}m{pulse_char}{RESET}")
    parts.append(f"\033[38;5;214m\u23f1{RESET}{_SP}{format_elapsed(elapsed)}")
    tok_str = TextFormatter.format_token_count(tokens)
    parts.append(f"{CYAN_256}\u2b21{RESET}{_SP}{CYAN_256}{tok_str}t{RESET}")
    if speed > 0:
        parts.append(f"\033[38;5;214m\u26a1{RESET}{_SP}{format_speed(speed)}t/s")
    else:
        parts.append(f"{DARK_GRAY_256}\u26a1{RESET}{_SP}{format_speed(speed)}t/s")
    line = f" {DARK_GRAY_256}\u00b7{RESET} ".join(parts)
    # 窄屏截断（复用 _narrow_split_line 消除重复）
    if is_narrow():
        tw = get_terminal_width()
        max_w = max(tw - 2, 30)
        half = max(tw // 2 - 4, 10)
        line = _narrow_split_line(line, max_w, half)
    return line


__all__ = [
    "StatusBar",
    "render_normal",
    "build_normal_parts",
    "render_streaming_line",
]
