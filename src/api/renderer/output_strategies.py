"""output_strategies — 打字机效果策略模式。

将 OutputAdapter.write_typing 的 80 行字符级输出逻辑提取为可替换的策略。
每种策略单一职责，支持逐字符/逐行/即时三种模式。

使用方式：
  strategy = CharByCharStrategy()
  strategy.write(text, console, speed, end, fill_style, lock, width)
"""

from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from ._utils import _precise_delay, cjk_display_width

from rich.console import Console
from rich.text import Text
from rich.style import Style

# ── 常量 ──────────────────────────────────────────────
_LOCK_TIMEOUT = 0.5  # 锁获取超时（秒）


def _split_text_with_spans(text: Text) -> list[tuple[str, list[tuple[int, int, Style]]]]:
    """将 Rich Text 按行拆分，同时提取每行的样式区间。

    Args:
        text: 带样式的 Rich Text

    Returns:
        [(line_text, [(start, end, style), ...]), ...] 列表
    """
    if not text or not text.plain:
        return []
    lines = text.plain.split('\n')
    # 移除 split('\n') 产生的尾缀空串（文本以 \n 结尾时最后一个元素为空串）
    if lines and text.plain.endswith('\n'):
        lines = lines[:-1]
    spans = sorted(text.spans, key=lambda s: s.start)
    result = []
    pos = 0
    for line_text in lines:
        line_len = len(line_text)
        line_end = pos + line_len
        line_spans = []
        for span in spans:
            if span.start >= line_end:
                break
            if span.end > pos:
                s_start = max(span.start - pos, 0)
                s_end = min(span.end - pos, line_len)
                line_spans.append((s_start, s_end, span.style))
        result.append((line_text, line_spans))
        pos = line_end + 1
    return result


# ═══════════════════════════════════════════════════════════
# 基类
# ═══════════════════════════════════════════════════════════

class TypewriterStrategy(ABC):
    """打字机效果策略基类。

    控制文本逐步显示到终端的方式。
    子类需实现 write() 方法。
    """

    @abstractmethod
    def write(self, text: Text, console: Console, speed: int,
              end: str, fill_style: Style | None,
              lock: threading.Lock, width: int) -> None:
        """将 text 输出到终端。

        Args:
            text: 带样式的 Rich Text
            console: Rich Console 实例
            speed: 字符/秒（0=即时）
            end: 尾部字符串
            fill_style: 行尾填充样式（代码块背景色）
            lock: 线程锁
            width: 终端宽度
        """


# ═══════════════════════════════════════════════════════════
# 逐字符策略（默认行为，从原 write_typing 迁移）
# ═══════════════════════════════════════════════════════════

class CharByCharStrategy(TypewriterStrategy):
    """逐字符输出，带自动折行和行尾填充。

    适用于：普通段落、代码行等需要逐字动画的场景。
    每 ~16ms 批量输出一次，避免每字符 console.print 的系统调用开销。

    性能优化 — 延时合并 (Delay Accumulation)：
      非 Linux 环境（Windows/macOS）的 time.sleep 精度仅 ~15.6ms，
      若逐字符 _precise_delay(1/speed)，高 speed 下会产生大量
      短延时（<15ms），触发纯忙等，导致 CPU 100%。

      改进：将逐字符延时累积到 ≥MIN_SLEEP 后一次 sleep：
        - 每次 sleep 参数 ≥16ms → 走 time.sleep 路径，避免忙等
        - 调用次数从 O(n) 降为 O(n/16)
        - 字符输出仍是逐字动画（由帧刷新保证），速率精确度不变
    """

    _FLUSH_INTERVAL = 0.016  # 60fps
    # 最小合并延时 — 非 Linux 系统 time.sleep 在此阈值以上可精确 sleep
    _MIN_SLEEP = 0.016

    @staticmethod
    def _accumulate_delay(accumulator: float, increment: float,
                          threshold: float) -> float:
        """累积延时，达到阈值时一次 sleep，返回剩余累积量。

        将逐字符的短延时（如 5ms）合并为批量长延时（≥16ms），
        避免非 Linux 系统上短延时触发纯忙等导致的 CPU 100%。

        Args:
            accumulator: 当前已累积的延时（秒）
            increment:   本次要累加的延时（秒）
            threshold:   触发 sleep 的阈值（秒）

        Returns:
            剩余未 sleep 的累积量（秒）
        """
        accumulator += increment
        if accumulator >= threshold:
            _precise_delay(accumulator)
            return 0.0
        return accumulator

    def write(self, text: Text, console: Console, speed: int,
              end: str, fill_style: Style | None,
              lock: threading.Lock, width: int) -> None:
        if not text or not text.plain:
            return

        col = 0
        line_indent = 0
        at_line_start = True

        char_styles = self._precompute_styles(text)

        # 延时累积器 — 将逐字符短延时合并为批量长延时
        # 解决非 Linux 系统逐字符 _precise_delay 触发纯忙等的问题
        delay_acc = 0.0

        # 输出缓冲区：(char, style) 对，延迟批量发送
        _buffer: list[tuple[str, Style | None]] = []
        _last_flush = time.monotonic()
        # 每 N 字符检查一次 flush，减少 time.monotonic() 调用次数
        _flush_check_counter = 0
        _FLUSH_CHECK_INTERVAL = 4  # 约每 4 字符调用一次 time.monotonic()，平衡精度与性能

        def _emit() -> None:
            """将缓冲区合并输出。单字符时直接 console.print 避免 Text 对象创建。"""
            nonlocal _last_flush, _flush_check_counter
            if not _buffer:
                return
            if len(_buffer) == 1:
                ch, st = _buffer[0]
                if st is not None:
                    # 构建带样式字符串直接写入，避免 Rich 内部开销
                    styled_text = Text(ch, style=st)
                    console.print(styled_text, end='')
                else:
                    console.file.write(ch)
            else:
                # ★ 改进：O(n) 单次遍历合并相邻同样式 run，替代原 O(n²) 嵌套循环
                merged = Text()
                run_chars: list[str] = []
                run_style = _buffer[0][1]
                for ch, st in _buffer:
                    if st == run_style:
                        run_chars.append(ch)
                    else:
                        run = ''.join(run_chars)
                        if run_style is not None:
                            merged.append(run, style=run_style)
                        else:
                            merged.append(run)
                        run_chars = [ch]
                        run_style = st
                # 最后一段 run
                run = ''.join(run_chars)
                if run_style is not None:
                    merged.append(run, style=run_style)
                else:
                    merged.append(run)
                console.print(merged, end='')
            try:
                console.file.flush()
            finally:
                _buffer.clear()
                _last_flush = time.monotonic()
                _flush_check_counter = 0  # 复位计数，确保字符计数式 flush 检查持续有效

        # ★ P1修复：一次获取锁覆盖整个 write()，消除逐字符锁竞争（~2000次→1次）
        acquired = lock.acquire(timeout=_LOCK_TIMEOUT)
        try:
            for i, char in enumerate(text.plain):
                if char == '\n':
                    _emit()
                    if fill_style is not None and col > 0:
                        pad_w = width - col
                        if pad_w > 0:
                            console.print(Text(" " * pad_w, style=fill_style), end='')
                    console.file.write('\n')
                    console.file.flush()
                    col = 0
                    at_line_start = True
                    line_indent = 0
                    continue

                cw = cjk_display_width(char)

                # 行首空白：追踪缩进宽度
                if at_line_start and (char == ' ' or char == '\t'):
                    col += (4 if char == '\t' else 1)
                    line_indent = col
                    _buffer.append((char, char_styles[i]))
                    if speed > 0:
                        delay_acc = self._accumulate_delay(delay_acc, 1.0 / speed, self._MIN_SLEEP)
                    continue

                if at_line_start:
                    at_line_start = False

                # 判断折行和填充需求
                needs_newline = False
                needs_pad = False
                pad_w = 0
                needs_second_wrap = False
                if col + cw > width:
                    needs_newline = True
                    if fill_style is not None:
                        pad_w = width - col
                        needs_pad = pad_w > 0
                    col_next = line_indent + cw
                    # ★ 二次折行保护：折行后缩进太深导致仍超出宽度时，
                    # 放弃缩进直接折到行首，避免字符溢出终端右边界。
                    if col_next > width:
                        col_next = cw
                        line_indent = 0
                        needs_second_wrap = True
                else:
                    col_next = col + cw

                if needs_newline:
                    _emit()
                    if needs_pad:
                        console.print(Text(" " * pad_w, style=fill_style), end='')
                    console.file.write("\n")
                    at_line_start = False
                    if line_indent > 0 and not needs_second_wrap:
                        if fill_style is not None:
                            console.print(Text(" " * line_indent, style=fill_style), end='')
                        else:
                            console.file.write(" " * line_indent)

                _buffer.append((char, char_styles[i]))

                _flush_check_counter += 1
                if _flush_check_counter >= _FLUSH_CHECK_INTERVAL:
                    _flush_check_counter = 0
                    now = time.monotonic()
                    if now - _last_flush >= self._FLUSH_INTERVAL:
                        _emit()

                col = col_next

                if speed > 0:
                    delay_acc = self._accumulate_delay(delay_acc, 1.0 / speed, self._MIN_SLEEP)

            # 循环结束：flush 剩余累积延时（锁内，最大 ~16ms 可接受）
            if delay_acc > 0:
                _precise_delay(delay_acc)

            _emit()
            if fill_style is not None and col > 0:
                pad_w = width - col
                if pad_w > 0:
                    console.print(Text(" " * pad_w, style=fill_style), end='')
            if end is not None:
                console.file.write(end)
            console.file.flush()
        finally:
            if acquired:
                lock.release()

    @staticmethod
    def _precompute_styles(text: Text) -> list[Style | None]:
        """预处理每个字符的样式，使用区间事件扫描 O(n+m)。

        将 span 转为 (pos, delta) 事件，一次扫描得到每个字符的
        合并样式，替代原先每个字符遍历所有 span 的 O(n*m) 方式。
        
        ★ 优化：使用计数器 dict 替代 list.remove()，将 O(n) 移除降为 O(1)。
        """
        spans = text.spans
        if not spans:
            default = text.style
            # ★ 与下方 spans 分支保持一致：解析 str 为 Style 对象
            if isinstance(default, str):
                default = Style.parse(default)
            return [default] * len(text.plain)

        default_style = text.style
        # ★ 统一为 Style | None，避免后续与 Style 对象合并时类型错误
        if isinstance(default_style, str):
            default_style = Style.parse(default_style)
        plain = text.plain
        n = len(plain)

        # 构建区间事件：(位置, 添加/移除, style, span索引)
        events: list[tuple[int, bool, Style, int]] = []
        for idx, span in enumerate(spans):
            if span.style and span.start < span.end:  # ★ 跳过空 span
                style = Style.parse(span.style) if isinstance(span.style, str) else span.style
                events.append((span.start, True, style, idx))
                events.append((span.end, False, style, idx))
        events.sort(key=lambda e: (e[0], not e[1]))  # 同位置先添加后移除 → 排序 key 为 (pos, not is_add):
                                                     # is_add=True → not True=False=0 → 添加在前
                                                     # is_add=False → not False=True=1 → 移除在后
                                                     # 实际效果：同位置先添加新 span 再移除旧 span，
                                                     # 确保该位置的字符获得新 span 的样式。

        char_styles: list[Style | None] = []
        # ★ 优化：用有序字典（Python 3.7+ dict）以 span 索引为 key，确保 combine 时能按索引排序
        active: dict[int, Style] = {}  # span索引 → Style
        ei = 0
        for i in range(n):
            while ei < len(events) and events[ei][0] <= i:
                _, is_add, style, idx = events[ei]
                if is_add:
                    active[idx] = style
                else:
                    active.pop(idx, None)
                ei += 1
            if not active:
                char_styles.append(default_style)
            else:
                styles_to_combine = []
                if default_style is not None:
                    styles_to_combine.append(default_style)
                # ★ 按 span 索引排序，确保后添加的 span 具有更高优先级
                for _, style in sorted(active.items()):
                    styles_to_combine.append(style)
                if len(styles_to_combine) == 1:
                    char_styles.append(styles_to_combine[0])
                else:
                    combined = styles_to_combine[0]
                    for s in styles_to_combine[1:]:
                        combined += s
                    char_styles.append(combined)

        return char_styles


# ═══════════════════════════════════════════════════════════
# 即时策略（speed=0 优化路径）
# ═══════════════════════════════════════════════════════════

class InstantStrategy(TypewriterStrategy):
    """即时输出，无打字延迟。

    适用于：speed=0 场景（非 TTY、测试环境、批量输出）。
    """

    @staticmethod
    def _render_fill_style_text(text: Text, console: Console,
                                 fill_style: Style, width: int) -> None:
        """渲染带 fill_style 行尾填充的文本（逐行处理）。

        每行文本输出后，填充剩余宽度到终端右边界，确保背景色连贯。
        """
        lines_and_spans = _split_text_with_spans(text)
        for li, (line_text, line_spans) in enumerate(lines_and_spans):
            line_text_obj = Text(line_text, style=text.style)
            for s_start, s_end, style in line_spans:
                line_text_obj.stylize(style, s_start, s_end)
            remaining = width - cjk_display_width(line_text)
            if line_text and remaining > 0:
                line_text_obj.append(" " * remaining, style=fill_style)
            console.print(line_text_obj, end='')
            if li < len(lines_and_spans) - 1:
                console.file.write("\n")

    def write(self, text: Text, console: Console, speed: int,
              end: str, fill_style: Style | None,
              lock: threading.Lock, width: int) -> None:
        if not text or not text.plain:
            return

        acquired = lock.acquire(timeout=_LOCK_TIMEOUT)
        if acquired:
            try:
                if fill_style is not None:
                    self._render_fill_style_text(text, console, fill_style, width)
                else:
                    console.print(text, end='')
                if end is not None:
                    console.file.write(end)
                console.file.flush()
            finally:
                lock.release()
        else:
            # 锁超时降级：直写 plain 文本，不调用 console.print
            console.file.write(text.plain)
            if end is not None:
                console.file.write(end)
            console.file.flush()


# ═══════════════════════════════════════════════════════════
# 逐行策略（适合代码块）
# ═══════════════════════════════════════════════════════════

class LineByLineStrategy(TypewriterStrategy):
    """逐行输出，每行之间有延迟。

    适用于：代码块、表格等行结构清晰的场景。
    每行一次性输出，行间延迟 = 1/speed 秒。
    """

    def write(self, text: Text, console: Console, speed: int,
              end: str, fill_style: Style | None,
              lock: threading.Lock, width: int) -> None:
        if not text or not text.plain:
            return

        line_delay = 1.0 / speed if speed > 0 else 0

        lines_and_spans = _split_text_with_spans(text)
        for li, (line_text, line_spans) in enumerate(lines_and_spans):
            # 构建该行的 Text
            line_text_obj = Text(line_text, style=text.style)
            for s_start, s_end, style in line_spans:
                line_text_obj.stylize(style, s_start, s_end)

            # 锁内输出
            acquired = lock.acquire(timeout=_LOCK_TIMEOUT)
            if acquired:
                try:
                    if fill_style is not None:
                        remaining = width - cjk_display_width(line_text)
                        if remaining > 0:
                            line_text_obj.append(" " * remaining, style=fill_style)
                    console.print(line_text_obj, end='')
                    if li < len(lines_and_spans) - 1:
                        console.file.write("\n")
                    console.file.flush()
                finally:
                    lock.release()
            else:
                # 锁超时降级：直写 plain 文本，不调用 console.print
                console.file.write(line_text)
                if li < len(lines_and_spans) - 1:
                    console.file.write("\n")
                console.file.flush()

            if line_delay > 0:
                _precise_delay(line_delay)

        # 结尾
        acquired = lock.acquire(timeout=_LOCK_TIMEOUT)
        if acquired:
            try:
                if end is not None:
                    console.file.write(end)
                console.file.flush()
            finally:
                lock.release()
        else:
            if end is not None:
                console.file.write(end)
            console.file.flush()


# ═══════════════════════════════════════════════════════════
# 策略工厂
# ═══════════════════════════════════════════════════════════

_DEFAULT_STRATEGY = CharByCharStrategy()
_INSTANT_STRATEGY = InstantStrategy()
_LINE_BY_LINE_STRATEGY = LineByLineStrategy()


def get_strategy(speed: int = 80, mode: str = "char") -> TypewriterStrategy:
    """根据速度和模式获取合适的打字机策略。

    Args:
        speed: 字符/秒，0 表示即时
        mode: "char"（逐字符）、"line"（逐行）、"instant"（即时）

    Returns:
        对应的策略实例

    Raises:
        ValueError: mode 不是有效值时
    """
    _VALID_MODES = {"char", "line", "instant"}
    if mode not in _VALID_MODES:
        raise ValueError(
            f"无效的输出策略 mode='{mode}'，有效值: {', '.join(sorted(_VALID_MODES))}"
        )

    if speed <= 0 or mode == "instant":
        return _INSTANT_STRATEGY
    if mode == "line":
        return _LINE_BY_LINE_STRATEGY
    return _DEFAULT_STRATEGY
