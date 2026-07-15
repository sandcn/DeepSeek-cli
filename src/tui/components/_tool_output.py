"""工具执行输出块 — ToolOutputBlock。

处理工具执行的标准输出/错误，支持 \\r 回车叠加和 ANSI 转义序列。

动效（2026-07-12）：
  - 宽屏：左侧添加极淡青色呼吸边框字符 │（使用 build_glow_ansi 微呼吸，色号 23↔24）
  - 窄屏：降级为无左边缘的纯文本（与原始行为一致）
  - \\r 分支（实时工具输出流）不受动效影响，保持原始行为

语法高亮（2026-07-16）：
  - JSON key 着色（青色）
  - 数字着色（黄色）
  - 字符串着色（绿色）
  - URL 下划线
  - 长度阈值：>5000 字符跳过，避免性能影响

【inline 模式 · 2026-07-16】
新增 render_to_target() 直写 ANSI 到 IOutputTarget，绕过 Rich Console。
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ...renderer.output import OutputAdapter
    from ...tui_framework.terminal.output_target import IOutputTarget

from rich.text import Text

from ..consumer.const import _STYLE_DIM, _MAX_OUTPUT_LEN
from ..core.animator import AnimatorContext
from ..terminal.terminal import is_narrow
from ..core.text_utils import build_left_border_ansi
from ...core.constants import DARK_GRAY_256
from ._base import TuiComponent, _estimate_content_lines, _rich_text_to_ansi

_logger = logging.getLogger(__name__)

# ── 语法高亮常量 ──────────────────────────────────────────

_HIGHLIGHT_MAX_LEN = 5000  # 超过此长度跳过语法高亮

# ANSI 色号
_HL_KEY_COLOR = 51     # JSON key — 青色
_HL_NUMBER_COLOR = 226  # 数字 — 黄色
_HL_STRING_COLOR = 42   # 字符串值 — 绿色
_HL_URL_COLOR = 33      # URL — 蓝色
_HL_RESET = "\033[0m"

# 正则预编译
_RE_JSON_KEY = re.compile(r'(?:^|[\s{,])\s*"([^"]+)"\s*:')
_RE_NUMBER = re.compile(r'\b(\d+\.?\d*)\b')
_RE_STRING = re.compile(r'(?<=:)\s*"([^"]*)"')
_RE_URL = re.compile(r'(https?://[^\s)\]}>,;]+)')


def _apply_syntax_highlight(text: str) -> str:
    """对输出文本应用基本语法高亮。

    高亮规则（按优先级）：
      1. URL — 蓝色 + 下划线
      2. JSON key — 青色
      3. 数字 — 黄色
      4. 字符串 — 绿色

    超过 _HIGHLIGHT_MAX_LEN 的文本跳过处理，直接返回原文。

    Args:
        text: 原始文本。

    Returns:
        带 ANSI 语法高亮的文本。
    """
    if len(text) > _HIGHLIGHT_MAX_LEN:
        return text

    # 使用占位符保护已处理的片段，避免交叉替换
    protected: dict[str, str] = {}  # placeholder → original
    _counter = [0]

    def _protect(match: re.Match, color: int, *, underline: bool = False) -> str:
        key = f"\x00HL{_counter[0]}\x00"
        _counter[0] += 1
        underline_seq = "\033[4m" if underline else ""
        protected[key] = f"\033[38;5;{color}m{underline_seq}{match.group(0)}{_HL_RESET}"
        return key

    # 1. URL — 蓝色 + 下划线
    text = _RE_URL.sub(lambda m: _protect(m, _HL_URL_COLOR, underline=True), text)

    # 2. JSON key — 青色
    # 匹配 "key": 模式，只对 key 部分着色
    def _json_key_replacer(m: re.Match) -> str:
        key = f"\x00HL{_counter[0]}\x00"
        _counter[0] += 1
        prefix = m.group(0)[:m.group(0).index('"')]
        key_part = m.group(1)
        suffix = m.group(0)[m.group(0).index('"', m.group(0).index('"') + 1):]
        protected[key] = (
            f"{prefix}\033[38;5;{_HL_KEY_COLOR}m\"{key_part}\"{_HL_RESET}{suffix}"
        )
        return key
    text = _RE_JSON_KEY.sub(_json_key_replacer, text)

    # 3. 数字 — 黄色（避免替换已保护的片段）
    def _number_replacer(m: re.Match) -> str:
        key = f"\x00HL{_counter[0]}\x00"
        _counter[0] += 1
        protected[key] = f"\033[38;5;{_HL_NUMBER_COLOR}m{m.group(1)}{_HL_RESET}"
        return key
    text = _RE_NUMBER.sub(_number_replacer, text)

    # 4. 字符串值 — 绿色
    def _string_replacer(m: re.Match) -> str:
        key = f"\x00HL{_counter[0]}\x00"
        _counter[0] += 1
        # 保留前导空白和冒号，只对字符串内容着色
        prefix = m.group(0)[:m.group(0).index('"')]
        str_content = m.group(1)
        suffix = m.group(0)[m.group(0).rindex('"') + 1:]
        protected[key] = (
            f"{prefix}\033[38;5;{_HL_STRING_COLOR}m\"{str_content}\"{_HL_RESET}{suffix}"
        )
        return key
    text = _RE_STRING.sub(_string_replacer, text)

    # 恢复所有受保护片段
    for placeholder, original in protected.items():
        text = text.replace(placeholder, original)

    return text


class ToolOutputBlock(TuiComponent):
    """工具执行输出块，支持 \\r 叠加和基本语法高亮。"""
    def __init__(self, text: str):
        self.text = text

    def _prepare_text(self) -> tuple[str, bool, bool]:
        """预处理文本：截断，检测回车/ANSI。

        Returns:
            (处理后的文本, 是否含回车, 是否含 ANSI)
        """
        text = self.text
        if len(text) > _MAX_OUTPUT_LEN:
            text = text[:_MAX_OUTPUT_LEN] + "...(truncated)"
        has_carriage = '\r' in text
        has_ansi = '\033[' in text
        return text, has_carriage, has_ansi

    def render_to_adapter(self, adapter: "OutputAdapter") -> int:
        """渲染到 OutputAdapter，返回行数。"""
        text, has_carriage, has_ansi = self._prepare_text()
        if has_carriage:
            if has_ansi:
                clean = text.replace('\r', '')
                try:
                    adapter.write(Text.from_ansi(clean))
                except Exception:
                    _logger.debug("tool_output ANSI 解析失败, 回退 raw 输出", exc_info=True)
                    adapter.write_raw(clean)
            else:
                clean = text.split('\r')[-1]
                adapter.write_raw(clean)
            if not text.endswith('\r'):
                adapter.write_raw('\n')
                return _estimate_content_lines(clean)
            return 0
        else:
            # 语法高亮（大文本跳过）
            highlighted = _apply_syntax_highlight(text) if len(text) <= _HIGHLIGHT_MAX_LEN else text
            frame = AnimatorContext.get_default().frame
            if is_narrow():
                hl_text = Text.from_ansi(highlighted) if highlighted != text else text
                adapter.write(Text.assemble(("   ", _STYLE_DIM), (hl_text, _STYLE_DIM)))
            else:
                edge_ansi = build_left_border_ansi(frame, 23, 24)
                adapter.write(Text.from_ansi(f"  {edge_ansi}   {highlighted}"))
            return _estimate_content_lines(text)

    def render_to_target(self, target: "IOutputTarget") -> int:
        """渲染到 IOutputTarget（inline 模式），返回行数。

        inline 模式：直写 ANSI 字符串，绕过 Rich Console。
        """
        text, has_carriage, has_ansi = self._prepare_text()
        if has_carriage:
            if has_ansi:
                clean = text.replace('\r', '')
                target.write_line(clean)
            else:
                clean = text.split('\r')[-1]
                target.write(clean)
            if not text.endswith('\r'):
                target.write_line("")
                return _estimate_content_lines(clean) if not has_ansi else _estimate_content_lines(clean)
            return 0
        else:
            # 语法高亮（大文本跳过）
            highlighted = _apply_syntax_highlight(text) if len(text) <= _HIGHLIGHT_MAX_LEN else text
            frame = AnimatorContext.get_default().frame
            if is_narrow():
                # 窄屏：使用 Rich Text 组装 → ANSI 转换
                hl_text = Text.from_ansi(highlighted) if highlighted != text else text
                dim_text = Text.assemble(("   ", _STYLE_DIM), (hl_text, _STYLE_DIM))
                target.write_line(_rich_text_to_ansi(dim_text))
            else:
                edge_ansi = build_left_border_ansi(frame, 23, 24)
                target.write_line(f"  {edge_ansi}   {highlighted}")
            return _estimate_content_lines(text)

    def render(self) -> str:
        return self.text
