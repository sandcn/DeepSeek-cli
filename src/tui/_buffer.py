"""RenderBuffer 渲染缓冲区 — 二维字符网格，支持叠加合成（精简版）。

★ 保留决策（2026-07-31 方向C）：
  RenderBuffer 运行时无实例化消费方（全项目仅测试与公共 re-export 引用），
  但因 `src/tui/__init__.py` 公共 API 与 `tests/test_tui/test_tui_structure.py`
  断言约束，**保留不删**，标记为 P2 遗留（未来若解除公共 API 约束可评估删除
  或接入 captured 数据）；本模块零改动。

★ 职责（2026-07-31 步骤2-A 输出路径统一）：
  RenderBuffer = 布局合成（底部栏/补全弹窗等二维网格合成），零 I/O，
  纯内存操作。不属于内容输出管线——内容行由 OutputAdapter 写入，
  输出历史由 _StdoutLineTracker 跟踪（缓冲职责矩阵见 src/tui/_output.py）。

提供：
  - RenderBuffer: 二维字符缓冲区，支持 write/merge/render 操作
  - 用于 Widget 渲染输出的统一目标

设计原则：
  - 零 I/O：纯内存操作，不涉及终端输出
  - 行优先：list[list[str]] 二维数组存储
  - 越界安全：超出边界的写入静默丢弃
  - 轻量级：无外部依赖（仅使用标准库）
  - 固定尺寸：不支持 _grow_to() 自动扩展，与旧版 RenderBuffer 的 style 参数改为 str | None

与旧 render_buffer.py 的差异：
  - 移除 Style 类型依赖 — style 参数改为 str | None（预构建 ANSI SGR 字符串）
  - 移除 _grow_to() 自动扩展 — RenderBuffer 固定尺寸，越界静默丢弃
  - 其余公开 API 完全兼容

使用示例:
    buf = RenderBuffer(20, 3)
    buf.write(0, 0, "Hello")
    buf.write(0, 1, "World")
    print(buf.render())
    # Hello
    # World
    #
"""

from __future__ import annotations

import logging

_logger = logging.getLogger(__name__)


__all__: list[str] = [
    "RenderBuffer",
]


# ═══════════════════════════════════════════════════════════
# RenderBuffer — 渲染缓冲区
# ═══════════════════════════════════════════════════════════


class RenderBuffer:
    """二维字符渲染缓冲区。

    管理一个 width×height 的字符网格，提供写入、合并和输出能力。
    每格可存储字符（含 ANSI 转义序列），支持 style 字符串前缀。

    Args:
        width: 缓冲区宽度（列数）。
        height: 缓冲区高度（行数）。
        default_char: 空白填充字符，默认空格 ' '。
    """

    def __init__(
        self,
        width: int,
        height: int,
        default_char: str = " ",
    ) -> None:
        if width < 0:
            width = 0
        if height < 0:
            height = 0
        self._width: int = width
        self._height: int = height
        self._default_char: str = default_char
        # 使用 list[list[str]] 二维数组，行优先
        self._grid: list[list[str]] = [
            [default_char] * width for _ in range(height)
        ]

    # ── 属性 ──────────────────────────────────────────────

    @property
    def width(self) -> int:
        """缓冲区宽度（列数）。"""
        return self._width

    @property
    def height(self) -> int:
        """缓冲区高度（行数）。"""
        return self._height

    def is_empty(self) -> bool:
        """缓冲区是否为空（width=0 或 height=0）。"""
        return self._width == 0 or self._height == 0

    # ── 写入 ──────────────────────────────────────────────

    def write(
        self,
        x: int,
        y: int,
        text: str,
        style: str | None = None,
    ) -> None:
        """在 (x, y) 位置写入文本。

        支持自动换行和 ANSI 样式前缀。
        超出边界的部分静默丢弃（不抛异常）。
        文本中的 ``\\n`` 会触发自动换行。

        Args:
            x: 起始列号（0-based，从左到右）。
            y: 起始行号（0-based，从上到下）。
            text: 要写入的文本。
            style: 可选的 ANSI SGR 字符串前缀（应用于整段文本）。
        """
        if not text or self.is_empty():
            return
        if y < 0:
            return

        # 应用样式前缀（style 为预构建 ANSI 字符串时直接拼接）
        if style is not None:
            text = style + text

        # 按行拆分
        lines = text.split("\n")
        for i, line in enumerate(lines):
            row = y + i
            if row < 0 or row >= self._height:
                continue
            self._write_line(row, x, line)

    def _write_line(self, row: int, x: int, text: str) -> None:
        """在指定行的 (x, 0) 位置写入文本。

        Args:
            row: 行号（0-based）。
            x: 起始列号（0-based）。
            text: 要写入的文本（单行，无换行符）。
        """
        if not text or row < 0 or row >= self._height:
            return
        if x < 0:
            # x 为负时，从第 0 列开始写入，跳过前 |x| 个字符
            text = text[-x:]
            x = 0
        if x >= self._width:
            return

        line = self._grid[row]
        for i, ch in enumerate(text):
            col = x + i
            if col >= self._width:
                break
            line[col] = ch

    def write_char(
        self,
        x: int,
        y: int,
        char: str,
        style: str | None = None,
    ) -> None:
        """在 (x, y) 位置写入单个字符。

        Args:
            x: 列号（0-based）。
            y: 行号（0-based）。
            char: 要写入的字符。
            style: 可选的 ANSI SGR 字符串前缀。
        """
        if not char or self.is_empty():
            return
        if y < 0 or y >= self._height:
            return
        if x < 0 or x >= self._width:
            return
        if style is not None:
            char = style + char
        self._grid[y][x] = char

    # ── 合并 ──────────────────────────────────────────────

    def merge(
        self,
        other: RenderBuffer,
        x: int = 0,
        y: int = 0,
        transparent_char: str | None = None,
    ) -> None:
        """将 other 缓冲区叠加到当前位置。

        非空白字符（非 transparent_char）覆盖当前网格内容。

        Args:
            other: 源 RenderBuffer 实例。
            x: 目标起始列号（0-based）。
            y: 目标起始行号（0-based）。
            transparent_char: 视为透明的字符，不覆盖。默认 None
                             （仅覆盖非默认字符的内容）。
        """
        if other.is_empty():
            return

        transparent = (
            transparent_char if transparent_char is not None
            else other._default_char
        )

        for src_y in range(other._height):
            dst_y = y + src_y
            if dst_y < 0 or dst_y >= self._height:
                continue
            src_line = other._grid[src_y]
            for src_x in range(other._width):
                dst_x = x + src_x
                if dst_x < 0 or dst_x >= self._width:
                    continue
                ch = src_line[src_x]
                if ch != transparent:
                    self._grid[dst_y][dst_x] = ch

    def fill(self, char: str, x: int, y: int, w: int, h: int) -> None:
        """在指定区域填充字符。

        Args:
            char: 填充字符。
            x: 起始列号（0-based）。
            y: 起始行号（0-based）。
            w: 填充宽度。
            h: 填充高度。
        """
        if not char or self.is_empty():
            return
        for row in range(max(0, y), min(y + h, self._height)):
            for col in range(max(0, x), min(x + w, self._width)):
                self._grid[row][col] = char

    # ── 清空 ──────────────────────────────────────────────

    def clear(self) -> None:
        """清空所有网格为 default_char。"""
        for row in range(self._height):
            self._grid[row] = [self._default_char] * self._width

    def clear_row(self, row: int) -> None:
        """清空指定行。

        Args:
            row: 行号（0-based）。
        """
        if 0 <= row < self._height:
            self._grid[row] = [self._default_char] * self._width

    def clear_col(self, col: int) -> None:
        """清空指定列。

        Args:
            col: 列号（0-based）。
        """
        if 0 <= col < self._width:
            for row in range(self._height):
                self._grid[row][col] = self._default_char

    # ── 子缓冲区 ──────────────────────────────────────────

    def sub_buffer(
        self,
        x: int,
        y: int,
        width: int,
        height: int,
    ) -> RenderBuffer:
        """创建子缓冲区（独立副本，数据由源缓冲区复制而来）。

        返回一个包含源缓冲区子区域数据拷贝的新 RenderBuffer 实例。
        修改子缓冲区不影响源缓冲区。

        若区域超出边界，自动裁剪到可用范围。

        Args:
            x: 起始列号。
            y: 起始行号。
            width: 子缓冲区宽度。
            height: 子缓冲区高度。

        Returns:
            子缓冲区 RenderBuffer 实例。
        """
        # 裁剪到边界
        x = max(0, min(x, self._width))
        y = max(0, min(y, self._height))
        width = max(0, min(width, self._width - x))
        height = max(0, min(height, self._height - y))

        buf = RenderBuffer(width, height, self._default_char)
        for row in range(height):
            src_y = y + row
            if 0 <= src_y < self._height:
                buf._grid[row] = self._grid[src_y][x:x + width]
        return buf

    # ── 输出 ──────────────────────────────────────────────

    def render(self) -> str:
        """将缓冲区渲染为字符串。

        每行用 ``\\n`` 分隔，末尾无多余换行。
        自动去除每行末尾的空白字符（避免不必要的空格）。

        Returns:
            渲染后的字符串。缓冲区为空时返回空字符串。
        """
        if self.is_empty():
            return ""
        lines: list[str] = []
        for row in range(self._height):
            line = "".join(self._grid[row])
            # 移除行尾空白（保留 ANSI 序列后的空格不动）
            line = line.rstrip()
            lines.append(line)
        # 移除末尾空行
        while lines and not lines[-1]:
            lines.pop()
        return "\n".join(lines)

    def render_raw(self) -> str:
        """将缓冲区渲染为字符串（保留行尾空格）。

        与 render() 不同，此方法不修剪行尾空白。

        Returns:
            渲染后的字符串（含行尾空格）。
        """
        if self.is_empty():
            return ""
        lines: list[str] = []
        for row in range(self._height):
            line = "".join(self._grid[row])
            lines.append(line)
        while lines and not lines[-1].strip():
            lines.pop()
        return "\n".join(lines)

    # ── 测量 ──────────────────────────────────────────────

    @staticmethod
    def measure_text(text: str) -> tuple[int, int]:
        """测量文本占用的 (宽度, 高度)。

        考虑换行符划分行数，每行的视觉宽度通过 len() 估算。
        精确测量应使用 visual_width()（来自 ansi_utils）。

        Args:
            text: 要测量的文本。

        Returns:
            (max_width, num_lines) 元组。
        """
        if not text:
            return (0, 0)
        lines = text.split("\n")
        max_w = max((len(line) for line in lines), default=0)
        return (max_w, len(lines))

    # ── 操作 ──────────────────────────────────────────────

    def hcenter(self, text: str, y: int, style: str | None = None) -> None:
        """在 (self.width/2, y) 居中对齐写入文本。

        Args:
            text: 要写入的文本。
            y: 行号（0-based）。
            style: 可选的 ANSI SGR 字符串前缀。
        """
        w, _ = self.measure_text(text)
        x = max(0, (self._width - w) // 2)
        self.write(x, y, text, style)

    def hline(self, y: int, char: str = "\u2500") -> None:
        """在第 y 行绘制水平线。

        Args:
            y: 行号（0-based）。
            char: 水平线字符，默认 ─ (U+2500)。
        """
        if y < 0 or y >= self._height:
            return
        self._grid[y] = [char] * self._width
