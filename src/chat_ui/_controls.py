"""chat_ui 控件模块 — Control 基类 + TextControl / MarkdownControl + ControlList。

TextControl: Layer 1 — 依赖 _const（Style 常量引用由调用方传入）。
MarkdownControl: Layer 2 — 依赖 api.renderer（IncrementalRenderer / OutputAdapter）。

控件体系：
  Control (ABC)           — 控件基类（write / close / refresh_width / is_closed）
    ├── TextControl       — 纯文本控件（prefix + style + raw/ansi 路径）
    │     ├── ToolOutputControl  — 工具输出控件（dim+缩进，封装 \\r 处理）
    │     └── ParseInfoControl   — 解析进度控件（替代 \\r\\033[K 进度条）
    ├── MarkdownControl   — 流式 Markdown 控件（封装 IncrementalRenderer）
    └── ToolSummaryControl — 工具汇总控件（成功/失败着色）

ControlList — 控件列表管理器，按 start_line 排序维护控件线性列表。

设计目标：将 ContentRenderer 中分散的 _render_styled_line() / _write_text_or_ansi()
逻辑抽取到控件中，使渲染方法通过控件抽象操作，提升复用性和可扩展性。

Style 常量（_STYLE_BOLD / _STYLE_DIM / _STYLE_ERROR 等）由调用方从
._const 导入并作为参数传入 TextControl，本模块不直接引用。
"""

from __future__ import annotations

import logging
import math
import sys
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

_logger = logging.getLogger(__name__)

from rich.style import Style
from rich.text import Text
from wcwidth import wcswidth

if TYPE_CHECKING:
    from ..api.renderer import IncrementalRenderer
    from ..api.renderer.output import OutputAdapter


# ═══════════════════════════════════════════════════════════
# Control — 控件抽象基类
# ═══════════════════════════════════════════════════════════

class Control(ABC):
    """控件抽象基类 — 定义 write / close / refresh_width 公共接口。

    所有终端渲染控件（Text、Markdown、未来 Table/CodeBlock 等）
    统一实现此接口，使 ContentRenderer 可以通过多态委托渲染。

    生命周期：
      write(text) → ... → close()
      创建后即处于"打开"状态，close() 后 is_closed=True。
      close() 后 write() 静默跳过（幂等保护）。
    """

    def write(self, text: str) -> None:
        """写入文本内容（子类覆盖实现具体渲染逻辑）。

        默认 no-op——不强制所有 Control 子类支持流式写入。
        需流式写入的子类（TextControl、MarkdownControl 等）覆盖此方法。
        """
        return

    # ── 脏检查 ────────────────────────────────────────

    def _is_unchanged(self, new_output: str) -> bool:
        """比较新输出与上次缓存是否相同——相同则跳过渲染。

        子类在 write/update 入口调用此方法，若返回 True 则跳过本次渲染。
        首次调用时 _last_output 为 None，始终返回 False。
        """
        if not hasattr(self, '_last_output'):
            self._last_output: str | None = None
        if self._last_output == new_output:
            return True
        self._last_output = new_output
        return False

    # ── 公共属性 ────────────────────────────────────────

    @property
    def start_line(self) -> int:
        """起始行号。"""
        return self._start_line

    @start_line.setter
    def start_line(self, value: int) -> None:
        self._start_line = value

    @property
    def level(self) -> int:
        """层级。"""
        return self._level

    @level.setter
    def level(self, value: int) -> None:
        self._level = value

    # ── 生命周期 ────────────────────────────────────────

    def close(self) -> None:
        """关闭控件，释放资源。

        默认 no-op，子类可覆盖以添加清理逻辑（flush、close 渲染器等）。
        幂等——多次调用无副作用。
        """

    @property
    @abstractmethod
    def is_closed(self) -> bool:
        """控件是否已关闭。"""
        ...

    def refresh_width(self) -> None:
        """刷新终端宽度缓存（对应 OutputAdapter.force_refresh_width()，绕过 5s TTL）。

        默认 no-op，子类可覆盖以委托内部渲染器刷新。
        供 resize 检测路径调用。
        """


# ═══════════════════════════════════════════════════════════
# TextControl — 纯文本控件（prefix + style）
# ═══════════════════════════════════════════════════════════

class TextControl(Control):
    """纯文本渲染控件 — 前缀 + Rich Style 样式化输出。

    封装原 ContentRenderer._render_styled_line() 和 _write_text_or_ansi()
    的逻辑，通过 OutputAdapter 输出到终端。

    支持三种写入路径：
      write()      — 前缀 + 样式渲染（如 "> 用户消息"）
      write_raw()  — 纯文本直写（跳过 Rich 样式管线）
      write_ansi() — ANSI 转义序列文本解析 + 回退
    """

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        prefix: str = "",
        style: Style | None = None,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 TextControl 实例。

        Args:
            output_adapter: Rich Console 输出适配器（必填，不能为 None）
            prefix: 前缀字符串（如 "\\n  > "、"\\n  ! "、"\\n  · "），直接拼接到文本前
            style: Rich Style 对象，同时作用于前缀和文本
            start_line: 起始行号（默认 0）
            level: 层级（默认 0）

        Raises:
            ValueError: 若 output_adapter 为 None
        """
        if output_adapter is None:
            raise ValueError("TextControl: output_adapter 不能为 None")
        self._adapter = output_adapter
        self._prefix = prefix
        self._style = style
        self._start_line = start_line
        self._level = level
        self._closed = False

    # ── 公共接口 ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入含前缀和样式的文本行。

        已关闭时静默跳过（幂等保护）。
        空字符串时仍输出前缀以实现换行效果（前提是 prefix 非空）。
        内容未变时跳过渲染（脏检查）。
        """
        if self._closed:
            return
        if self._is_unchanged(text):
            return
        self._adapter.write(
            Text.assemble((self._prefix, self._style), (text, self._style))
        )

    def write_raw(self, text: str) -> None:
        """纯文本直写（跳过 Rich 样式管线，无前缀）。

        适用于 \\r 覆盖输出、解析进度等信息。
        已关闭时静默跳过。
        """
        if self._closed:
            return
        self._adapter.write_raw(text)

    def _try_write_ansi(self, text: str) -> None:
        """尝试用 Text.from_ansi() 解析渲染，失败回退到 write_raw()。

        封装 ANSI 文本写入的通用回退逻辑，供 TextControl 自身和
        ToolOutputControl 子类复用，消除重复代码。

        Args:
            text: 含 ANSI 转义序列的文本（已去除 \\r 等控制字符）
        """
        try:
            self._adapter.write(Text.from_ansi(text))
        except Exception:
            _logger.warning(
                "Text.from_ansi 解析失败，回退到 write_raw",
                exc_info=True,
            )
            try:
                self._adapter.write_raw(text)
            except Exception:
                _logger.warning(
                    "write_raw 回退也失败",
                    exc_info=True,
                )

    def write_ansi(self, text: str) -> None:
        """写入含 ANSI 转义序列的文本。

        委托 _try_write_ansi 执行 ANSI 解析→回退流程。
        已关闭时静默跳过。
        """
        if self._closed:
            return
        self._try_write_ansi(text)

    def close(self) -> None:
        """关闭控件（标记关闭 + flush 适配器）。幂等。"""
        if self._closed:
            return
        self._closed = True
        self._adapter.flush()

    @property
    def is_closed(self) -> bool:
        return self._closed


# ═══════════════════════════════════════════════════════════
# MarkdownControl — 流式 Markdown 控件
# ═══════════════════════════════════════════════════════════

class MarkdownControl(Control):
    """流式 Markdown 渲染控件 — 封装 IncrementalRenderer。

    统一推理/内容两个流式 Markdown 路径，对外暴露统一 Control 接口。
    内部直接委托 IncrementalRenderer，不做二次封装——避免重复创建
    Console 实例和 output_lock 竞争。

    is_closed 由本类自维护 _closed 标志保证幂等性，
    不依赖 IncrementalRenderer 内部实现细节。
    """

    def __init__(
        self,
        style: str = "",
        show_indicator: bool = False,
        typing_speed: int = 1000,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 MarkdownControl 实例。

        内部创建 IncrementalRenderer 实例（自行管理 Console 和
        OutputAdapter）。与 TextControl 的 _tool_adapter 各用各的
        Console——两条渲染管线（流式 Markdown / 工具输出）的宽度缓存
        独立刷新（5s TTL），写入串行化由全局 output_lock 保证。

        Args:
            style: Rich Console style 字符串（如 "dim"）
            show_indicator: 是否显示流式光标指示器
            typing_speed: 打字机效果速度（字符/秒，1000=即时）
            start_line: 起始行号（默认 0）
            level: 层级（默认 0）
        """
        from ..api.renderer import IncrementalRenderer
        self._renderer: "IncrementalRenderer" = IncrementalRenderer(
            style=style,
            _file=sys.__stdout__,
            typing_speed=typing_speed,
            show_indicator=show_indicator,
        )
        self._start_line = start_line
        self._level = level
        self._closed = False

    # ── 公共接口 ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入 Markdown 文本（委托 IncrementalRenderer）。

        已关闭时静默跳过（幂等保护）。
        """
        if self._closed:
            return
        self._renderer.write(text)

    def close(self) -> None:
        """关闭 Markdown 渲染器。幂等——多次调用无副作用。"""
        if self._closed:
            return
        self._closed = True
        self._renderer.close()

    def refresh_width(self) -> None:
        """刷新终端宽度缓存（委托 IncrementalRenderer.force_refresh_width()，
        绕过内部 OutputAdapter 的 5s TTL）。
        """
        self._renderer.force_refresh_width()

    @property
    def is_closed(self) -> bool:
        return self._closed


# ═══════════════════════════════════════════════════════════
# ControlList — 控件列表管理器
# ═══════════════════════════════════════════════════════════

class ControlList:
    """控件列表管理器 — 按 start_line 排序维护控件线性列表。

    维护一个有序控件列表，提供添加/移除/查找/关闭等操作。
    start_line 由内部 _next_line 计数器自动分配，调用方也可显式指定。

    生命周期：
      add(control) → ... → close_all()
      通过 add() 加入的控件由 ControlList 统一管理。
    """

    def __init__(self) -> None:
        """创建空的控件列表。"""
        self._controls: list[Control] = []
        self._next_line: int = 1

    # ── 公共接口 ────────────────────────────────────────

    def add(self, control: Control, start_line: int | None = None) -> None:
        """添加控件到列表。

        Args:
            control: 要添加的控件实例
            start_line: 起始行号（None 时自动分配 _next_line）
        """
        if start_line is None:
            start_line = self._next_line
        control.start_line = start_line
        # 二分插入保持按 start_line 有序（Python 3.9 兼容——手动二分）
        lo, hi = 0, len(self._controls)
        while lo < hi:
            mid = (lo + hi) // 2
            if self._controls[mid].start_line < start_line:
                lo = mid + 1
            else:
                hi = mid
        self._controls.insert(lo, control)
        # 更新 _next_line（确保单调递增）
        self._next_line = max(self._next_line, start_line + 1)

    def remove(self, control: Control) -> None:
        """从列表中移除控件。

        Args:
            control: 要移除的控件实例（不在列表中时静默跳过）
        """
        try:
            self._controls.remove(control)
        except ValueError:
            pass

    def close_all(self) -> None:
        """关闭并移除所有控件，重置 _next_line。"""
        for ctrl in self._controls:
            try:
                ctrl.close()
            except Exception:
                _logger.debug(
                    "ControlList.close_all: 关闭控件 %s 失败",
                    type(ctrl).__name__, exc_info=True,
                )
        self._controls.clear()
        self._next_line = 1

    def refresh_width_all(self) -> None:
        """刷新所有活跃控件的终端宽度缓存。"""
        for ctrl in self._controls:
            if not ctrl.is_closed:
                try:
                    ctrl.refresh_width()
                except Exception:
                    pass


# ═══════════════════════════════════════════════════════════
# ToolOutputControl — 工具输出控件
# ═══════════════════════════════════════════════════════════

class ToolOutputControl(TextControl):
    """工具输出渲染控件 — dim 样式 + "   " 缩进，封装 \\r 处理。

    继承 TextControl，覆盖 write() 以处理工具输出的特殊逻辑：
      - 自动添加 dim 样式和 3 空格缩进
      - 处理 \\r 字符（进度条行内覆盖）
      - ANSI 转义序列检测分流
      - 超长文本截断（>10000 字符）
      - 跨 write() 调用的 last_was_carriage 状态维护

    Style 常量从 _const 模块导入，不硬编码。
    """

    # ── 超长截断阈值 ──
    _MAX_OUTPUT_LEN = 10000

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        dim_style: Style,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 ToolOutputControl 实例。

        Args:
            output_adapter: Rich Console 输出适配器
            dim_style: dim 样式（来自 _const._STYLE_DIM）
            start_line: 起始行号
            level: 层级
        """
        super().__init__(
            output_adapter,
            prefix="   ",
            style=dim_style,
            start_line=start_line,
            level=level,
        )
        self._dim_style = dim_style
        self._last_was_carriage: bool = False

    # ── 公共接口 ────────────────────────────────────────

    def write(self, text: str) -> None:
        """写入工具输出文本（覆盖父类 write）。

        处理路径：
          1. 超长截断（>10000 字符 → ...(truncated)）
          2. 无 \\r → 标准样式输出
          3. 含 \\r → 进度条覆盖输出（ANSI 分流）
        内容未变时跳过渲染（脏检查）。
        """
        if self._closed:
            return

        # 超长截断
        if len(text) > self._MAX_OUTPUT_LEN:
            text = text[:self._MAX_OUTPUT_LEN] + "...(truncated)"

        # 脏检查：内容未变且状态未变 → 跳过
        if self._is_unchanged(text) and not self._last_was_carriage:
            return

        # ── 无 \r：标准输出 ─────────────────────────────────
        if '\r' not in text:
            if self._last_was_carriage:
                self._adapter.write_raw("\n")
                self._last_was_carriage = False
            self._adapter.write(
                Text.assemble((self._prefix, self._dim_style), (text, self._dim_style))
            )
            return

        # ── 含 \r：进度条覆盖输出 ────────────────────────────
        if '\033[' in text:
            self._write_with_ansi(text)
        else:
            # 纯 \r 文本 → 按 \r 分割取最后一段
            self._adapter.write_raw(text.split('\r')[-1])

        # ── \r 结尾标记 ──────────────────────────────────────
        if text.endswith('\r'):
            self._last_was_carriage = True
        else:
            self._adapter.write_raw('\n')
            self._last_was_carriage = False

    def close(self) -> None:
        """关闭控件：若上一行以 \\r 结尾则补写换行，然后委托父类 close。幂等。"""
        if self._closed:
            return
        if self._last_was_carriage:
            try:
                self._adapter.write_raw("\n")
            except Exception:
                pass
            self._last_was_carriage = False
        super().close()

    # ── 内部 ────────────────────────────────────────────

    def _write_with_ansi(self, text: str) -> None:
        """处理含 ANSI 转义序列的工具输出（移除 \\r 后解析渲染）。

        复用 TextControl._try_write_ansi() 的 ANSI 回退逻辑。
        """
        clean_text = text.replace('\r', '')
        self._try_write_ansi(clean_text)


# ═══════════════════════════════════════════════════════════
# ToolSummaryControl — 工具汇总控件
# ═══════════════════════════════════════════════════════════

class ToolSummaryControl(Control):
    """工具汇总渲染控件 — 成功/失败着色 + 详情列表。

    不继承 TextControl，因其渲染逻辑差异大（一次性 summarize 而非流式 write）。
    """

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        style_success: Style,
        style_fail: Style,
        style_warn: Style,
        style_dim: Style,
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 ToolSummaryControl 实例。

        Args:
            output_adapter: Rich Console 输出适配器
            style_success: 成功样式（绿色）
            style_fail: 失败样式（红色）
            style_warn: 警告样式（橙色）
            style_dim: dim 样式
            start_line: 起始行号
            level: 层级
        """
        if output_adapter is None:
            raise ValueError("ToolSummaryControl: output_adapter 不能为 None")
        self._adapter = output_adapter
        self._style_success = style_success
        self._style_fail = style_fail
        self._style_warn = style_warn
        self._style_dim = style_dim
        self._start_line = start_line
        self._level = level
        self._closed = False

    # ── 公共接口 ────────────────────────────────────────

    def summarize(self, successful: tuple, failed: tuple) -> None:
        """渲染工具汇总。内容未变时跳过渲染（脏检查）。

        Args:
            successful: 成功工具列表
            failed: 失败工具列表（(name, error) 元组构成）
        """
        if self._closed:
            return
        if successful is None or failed is None:
            _logger.debug(
                "ToolSummaryControl.summarize 收到 None 参数: successful=%s, failed=%s",
                successful, failed,
            )
        successful = successful or ()
        failed = failed or ()

        # 脏检查：比较 (successful, failed) 元组哈希
        cache_key = (tuple(str(s) for s in successful), tuple(str(f) for f in failed))
        if self._is_unchanged(str(cache_key)):
            return

        total = len(successful) + len(failed)
        if failed:
            self._render_failure_summary(failed, total)
        elif successful:
            self._adapter.write(Text.assemble(
                ("  · ", self._style_success),
                (f"{len(successful)}工具完成", self._style_success),
            ))

    def close(self) -> None:
        """关闭控件。幂等。"""
        if self._closed:
            return
        self._closed = True
        self._adapter.flush()

    @property
    def is_closed(self) -> bool:
        return self._closed

    # ── 内部 ────────────────────────────────────────────

    @staticmethod
    def _truncate_by_visual_width(s: str, max_width: int) -> str:
        """按视觉宽度截断字符串。"""
        if not s:
            return s
        w = 0
        cut = len(s)
        for i, ch in enumerate(s):
            cw = wcswidth(ch) if wcswidth(ch) >= 0 else 1
            if w + cw > max_width - 3:
                cut = i
                break
            w += cw
        if cut < len(s):
            return s[:cut] + "..."
        return s

    def _render_failure_summary(self, failed: tuple, total: int) -> None:
        """渲染失败工具汇总（着色图标 + 彩色计数 + 详情列表）。

        迁移自 ContentRenderer._render_failure_summary。
        """
        # ★ 解包保护：若元素非 (name, error) 格式，整体转为字符串显示
        safe_failed = []
        for item in failed:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                error = str(item[1]) if item[1] is not None else ""
                if len(item) > 2:
                    extras = ", ".join(str(x) for x in item[2:])
                    if error:
                        error += f" [{extras}]"
                    else:
                        error = f"[{extras}]"
                safe_failed.append((str(item[0]), error))
            else:
                safe_failed.append((str(item), ""))
        failed = tuple(safe_failed)

        failed_names = ", ".join(n for n, _ in failed)
        if len(failed) == total:
            self._adapter.write(Text.assemble(
                ("  ! ", self._style_fail),
                (f"全部失败: {failed_names}", self._style_fail),
            ))
        else:
            self._adapter.write(Text.assemble(
                ("  ! ", self._style_warn),
                (f"{len(failed)}/{total} 失败: {failed_names}", self._style_warn),
            ))

        for name, error in failed[:3]:
            short = ""
            if error:
                short = error.split("\n")[0].strip()
                if short:
                    short = self._truncate_by_visual_width(short, 80)
            self._adapter.write(Text.assemble(
                (f"    {name}", self._style_dim),
                (f"  {short}", self._style_dim) if short else ("", self._style_dim),
            ))
        if len(failed) > 3:
            self._adapter.write(Text.assemble(
                (f"    ... 及其他 {len(failed) - 3} 个", self._style_dim),
            ))


# ═══════════════════════════════════════════════════════════
# ParseInfoControl — 解析进度控件
# ═══════════════════════════════════════════════════════════

class ParseInfoControl(TextControl):
    """解析进度渲染控件 — 替代 \\r\\033[K 进度条，改为普通文本行。

    继承 TextControl，通过 write_raw 直写解析进度信息。
    不再使用 \\r 覆盖——每次更新输出为独立行。
    """

    # ── 清除进度哨兵（与 _const._CLEAR_PARSE_LINE 值一致） ──
    _CLEAR_SENTINEL = -1

    def write(self, text: str) -> None:
        """不支持流式写入——ParseInfoControl 通过 update() 渲染。

        显式覆盖为 no-op：因为 ParseInfoControl 继承 TextControl，
        若依赖 MRO 解析会错误调用 TextControl.write() 产生非预期输出。
        """
        return

    def __init__(
        self,
        output_adapter: "OutputAdapter",
        start_line: int = 0,
        level: int = 0,
    ) -> None:
        """创建 ParseInfoControl 实例。

        Args:
            output_adapter: Rich Console 输出适配器
            start_line: 起始行号
            level: 层级
        """
        super().__init__(
            output_adapter,
            prefix="",
            style=None,
            start_line=start_line,
            level=level,
        )

    # ── 公共接口 ────────────────────────────────────────

    def update(self, tool_names: str, tokens: int | float, elapsed: float) -> None:
        """渲染解析进度行 — 同行原地更新（\\r\\033[K 覆写，不产生新行）。

        每次调用覆写当前行，行尾不追加 \\n，保持单行进度条行为。
        _CLEAR_SENTINEL 时输出 \\n 结束当前进度行。
        内容未变时跳过渲染（脏检查）。

        Args:
            tool_names: 工具名列表字符串
            tokens: token 数量（_CLEAR_SENTINEL 时仅输出换行）
            elapsed: 耗时秒数
        """
        if self._closed:
            return

        if tokens == self._CLEAR_SENTINEL:
            self._adapter.write_raw("\n")
            self._last_output = None  # 重置缓存
            return

        # ★ 类型保护：tokens 非 (int, float) 时显示原始字符串
        if isinstance(tokens, (int, float)):
            if math.isfinite(tokens):
                tokens_str = f"{tokens}t"
            else:
                tokens_str = "?"
        else:
            tokens_str = str(tokens)

        output = f"\r\033[K  ~ {tool_names} {tokens_str} {elapsed:.2f}s"
        if self._is_unchanged(output):
            return
        self._adapter.write_raw(output)

    def close(self) -> None:
        """关闭控件。幂等。"""
        if self._closed:
            return
        self._closed = True
        self._adapter.flush()
