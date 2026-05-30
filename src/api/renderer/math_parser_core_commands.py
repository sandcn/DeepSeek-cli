"""math_parser_core_commands — MathParser 主要 LaTeX 命令 Mixin（第1组）。

包含 sqrt、textcmd、定界符、重音、堆积、划除、花括号、颜色、框等命令。
"""

from __future__ import annotations

import logging
from typing import Tuple

_logger = logging.getLogger(__name__)

from rich.style import Style
from rich.text import Text

from .math_parser_helpers import (
    _STYLE_DEFAULT, _STYLE_FUNCTION, _STYLE_NUMBER, _STYLE_OPERATOR,
    _STYLE_SUPERSCRIPT, _STYLE_SUBSCRIPT, _STYLE_FRAC_LINE,
    _STYLE_TEXT, _STYLE_BOLD, _STYLE_ITALIC,
    _STYLE_CANCEL, _STYLE_TAG, _STYLE_BOXED,
    _STYLE_ACCENT, _STYLE_COLOR_NOTICE,
    _convert_to_superscript, _has_operator,
    _extract_braced_group, _skip_spaces, _skip_group,
)
from .math_symbols import (
    _ACCENT_MAP, _DELIMITER_MAP, _COLOR_ALIAS,
    _COMMAND_MAP,
)


class MathParserCoreCommandsMixin:
    """MathParser 核心命令 Mixin（第1组）。

    提供以下方法：
      _parse_sqrt()
      _parse_textcmd()
      _parse_delimiter()
      _parse_size_delimiter()
      _parse_accent()
      _parse_stacked()
      _parse_cancel()
      _parse_overunderbrace()
      _parse_color()
      _parse_colorbox()
      _parse_boxed()
    """

    # ── 根式 ────────────────────────────────────────────

    def _parse_sqrt(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\sqrt[n]{x} 或 \\sqrt{x}。

        当根号内包含运算符时自动加括号，明确根式边界。
        """
        n_root: str | None = None
        try:
            i = _skip_spaces(s, i, n)
            if i < n and s[i] == '[':
                close = s.find(']', i)
                if close != -1:
                    n_root = s[i + 1:close]
                    i = close + 1
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
            if not content_raw:
                return Text("√"), i
        except Exception:
            return Text("√"), i

        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        result = Text()
        if n_root:
            sup = _convert_to_superscript(n_root)
            result.append(sup, style=_STYLE_SUPERSCRIPT)
        result.append("√")
        # 根号内包含运算符时自动加括号
        if _has_operator(content_raw):
            result.append("(")
            result.append_text(content_text)
            result.append(")")
        else:
            result.append_text(content_text)
        return result, i

    # ── 文本命令 ────────────────────────────────────────

    def _parse_textcmd(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\text{...}、\\textbf{...} 等文本命令。

        支持：text, textbf, textit, mathrm, mathbf, mathcal,
              mathit, mathbb, mathscr, boldsymbol

        \\boldsymbol 和 \\mathbf 会递归解析内部 LaTeX 命令，
        使 \\boldsymbol{\\alpha} 能正确渲染为粗体 α。
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
            if not content_raw:
                return Text(), i
        except Exception:
            return Text(), i

        # ── 递归解析型命令 ──────────────────────────
        if cmd in ("boldsymbol", "mathbf"):
            try:
                # 保存并清空大算符栈，防止内部 \sum/\int/\lim 走大算符追踪路径
                saved_stack = list(getattr(self, '_bigop_stack', []))
                self._bigop_stack = []
                parsed = self.parse(content_raw)
                self._flush_bigop_limits(parsed)
                self._bigop_stack = saved_stack
            except Exception:
                parsed = Text(content_raw)
            if cmd == "boldsymbol":
                parsed.stylize(Style(bold=True, color="bright_white"))
            else:
                parsed.stylize(Style(bold=True))
            return parsed, i

        # ── 纯文本型命令 ────────────────────────────
        if cmd in ("mathrm",):
            return Text(content_raw, style=Style()), i

        style = _STYLE_DEFAULT
        if cmd == "text":
            # 检测内容中是否包含嵌套的 $...$ 或 \(...\) 数学公式
            if '$' in content_raw or '\\(' in content_raw:
                try:
                    parsed = self.parse(content_raw)
                    parsed.stylize(_STYLE_TEXT)
                    return parsed, i
                except Exception:
                    _logger.debug("嵌套数学公式解析失败（非关键）")
            style = _STYLE_TEXT
        elif cmd == "textbf":
            style = _STYLE_BOLD
        elif cmd == "textit":
            style = _STYLE_ITALIC
        elif cmd == "mathcal":
            style = Style(color="bright_magenta")
        elif cmd == "mathit":
            style = Style(italic=True)
        elif cmd == "mathbb":
            style = Style(color="bright_white", bold=True)
        elif cmd == "mathscr":
            style = Style(color="magenta", italic=True)
        elif cmd == "Bbb":
            style = Style(color="bright_white", bold=True)
        elif cmd == "cal":
            style = Style(color="bright_magenta")
        elif cmd == "rm":
            style = Style()
        elif cmd == "it":
            style = Style(italic=True)
        elif cmd == "sc":
            style = Style(color="bright_white", italic=True)
        elif cmd == "sf":
            style = Style(color="bright_cyan")
        elif cmd == "tt":
            style = Style(color="green")

        return Text(content_raw, style=style), i

    # ── 定界符 ──────────────────────────────────────────

    def _parse_delimiter(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\left / \\right / \\middle 后的定界符。"""
        try:
            i = _skip_spaces(s, i, n)
            if i >= n:
                return Text(), i

            c = s[i]
            i += 1

            if c == '\\':
                start = i
                while i < n and s[i].isalpha():
                    i += 1
                delim_cmd = s[start:i]
                char = _DELIMITER_MAP.get(delim_cmd, delim_cmd)
                return Text(char), i

            if c == '.':
                return Text(), i

            if c in _DELIMITER_MAP:
                return Text(_DELIMITER_MAP[c]), i

            return Text(c), i
        except Exception:
            return Text(), i

    # ── 尺寸定界符 \bigl \bigr \Bigl \Bigr 等 ─────────

    def _parse_size_delimiter(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\bigl 等尺寸定界符前缀后的定界符。"""
        try:
            i = _skip_spaces(s, i, n)
            if i >= n:
                return Text(), i

            c = s[i]
            i += 1

            if c == '\\':
                start = i
                while i < n and s[i].isalpha():
                    i += 1
                delim_cmd = s[start:i]
                char = _DELIMITER_MAP.get(delim_cmd, delim_cmd)
                return Text(char, style=Style(bold=True)), i

            if c == '.':
                return Text(), i

            char = _DELIMITER_MAP.get(c, c)
            return Text(char, style=Style(bold=True)), i
        except Exception:
            return Text(), i

    # ── 重音符号 ────────────────────────────────────────

    def _parse_accent(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析重音符号，如 \\hat{x}、\\bar{x} 等。"""
        try:
            i = _skip_spaces(s, i, n)
            if i >= n:
                return Text(), i

            if s[i] == '{':
                content_raw, i = _extract_braced_group(s, i)
            else:
                content_raw = s[i]
                i += 1
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        accent_char = _ACCENT_MAP[cmd]

        result = Text()
        for ch in content_raw:
            result.append(ch + accent_char)
        return result, i

    # ── 上下堆积 \underset / \overset / \stackrel ─────

    def _parse_stacked(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\underset{below}{base} / \\overset{above}{base} / \\stackrel{above}{base}。"""
        try:
            i = _skip_spaces(s, i, n)
            start_i = i
            sub_raw, i = _extract_braced_group(s, i)
            if not sub_raw and i == start_i:
                return Text(f"\\{cmd}"), i
            i = _skip_spaces(s, i, n)
            start_i = i
            base_raw, i = _extract_braced_group(s, i)
            if not base_raw and i == start_i:
                return Text(f"\\{cmd}"), i
        except Exception:
            return Text(f"\\{cmd}"), i

        try:
            base_text = self.parse(base_raw)
            sub_text = self.parse(sub_raw)
        except Exception:
            return Text(f"{base_raw}({sub_raw})"), i

        result = Text()
        if cmd in ("overset", "stackrel"):
            # \\overset{above}{base} → 上标(above) + base
            sub_text.stylize(_STYLE_SUPERSCRIPT)
            result.append_text(sub_text)
            result.append_text(base_text)
        else:
            # \\underset{below}{base} → base + 下标(below)
            result.append_text(base_text)
            sub_text.stylize(_STYLE_SUBSCRIPT)
            result.append_text(sub_text)

        return result, i

    # ── 划除 \cancel / \bcancel / \xcancel ─────────────

    def _parse_cancel(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\cancel{x}、\\bcancel{x}、\\xcancel{x}、\\sout{x}、\\cancelto{value}{expr}。

        \\cancel 系列使用红色删除线样式（_STYLE_CANCEL），
        \\sout 使用普通删除线样式（strike=True）。
        \\cancelto{value}{expr} 渲染为 value 上标 + expr(划除)。
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
            if not content_raw:
                return Text(), i
        except Exception:
            return Text(), i

        # ── \\cancelto{value}{expr} ──────────────────────
        if cmd == "cancelto":
            try:
                value_text = self.parse(content_raw)
                i = _skip_spaces(s, i, n)
                expr_raw, i = _extract_braced_group(s, i)
                if not expr_raw:
                    return value_text, i
                expr_text = self.parse(expr_raw)
            except Exception:
                return Text(content_raw), i

            result = Text()
            # value 作为上标
            value_text.stylize(_STYLE_SUPERSCRIPT)
            result.append_text(value_text)
            # 添加箭头表示"取消并变为"（\cancelto{value}{expr}: value→expr）
            result.append("→", style=Style(color="red", bold=True))
            # expr 带删除线
            expr_text.stylize(_STYLE_CANCEL)
            result.append_text(expr_text)
            return result, i

        # ── 普通 \\cancel/ \\bcancel/ \\xcancel/ \\sout ───
        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        if cmd == "sout":
            content_text.stylize(Style(strike=True))
        else:
            content_text.stylize(_STYLE_CANCEL)
        return content_text, i

    # ── 花括号 \overbrace{expr} / \underbrace{expr} ────

    def _parse_overunderbrace(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\overbrace{expr} 和 \\underbrace{expr}。

        格式：\\overbrace{expr} 或 \\overbrace{expr}^{label}
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
            if not content_raw:
                return Text(), i
        except Exception:
            return Text(), i

        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        # 检查是否有上标/下标标签
        label = None
        i_saved = i
        i = _skip_spaces(s, i, n)
        if i < n and s[i] in ('^', '_'):
            is_sup = (s[i] == '^')
            try:
                label_text, i = self._parse_script(s, i, n, is_sup=is_sup)
                if label_text.plain:
                    label = label_text
            except Exception:
                i = i_saved

        result = Text()
        brace_char = "⏞" if cmd == "overbrace" else "⏟"
        if cmd == "overbrace":
            result.append(brace_char, style=_STYLE_OPERATOR)
            result.append_text(content_text)
            if label:
                result.append_text(Text("  "))
                result.append_text(label)
        else:
            result.append_text(content_text)
            result.append(brace_char, style=_STYLE_OPERATOR)
            if label:
                result.append_text(Text("  "))
                result.append_text(label)

        return result, i

    # ── 颜色 \color{red}{text} / \textcolor{red}{text} ─

    def _parse_color(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\color{red}{text} 或 \\textcolor{red}{text}。"""
        try:
            i = _skip_spaces(s, i, n)
            color_raw, i = _extract_braced_group(s, i)
            if not color_raw:
                return Text(), i
            color_name = color_raw.strip().lower()
            rich_color = _COLOR_ALIAS.get(color_name, color_name)

            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)

            if cmd == "color" and not content_raw:
                # \color{red} 后面可能跟着未分组的字符
                if i < n and s[i] not in ('\\', '^', '_', '{', '}'):
                    char = s[i]
                    i += 1
                    return Text(char, style=Style(color=rich_color)), i
                return Text(), i
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        content_text.stylize(Style(color=rich_color))
        return content_text, i

    # ── 框 \boxed{expr} ─────────────────────────────────

    def _parse_boxed(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\boxed{expr}。在终端中用颜色和方框视觉表示。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
            if not content_raw:
                return Text(), i
        except Exception:
            return Text(), i

        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        result = Text("[")
        result.append_text(content_text)
        result.append("]", style=_STYLE_BOXED)
        result.stylize_before(_STYLE_BOXED)
        return result, i

    # ── 彩色框 \colorbox{color}{text} / \fcolorbox{border}{fill}{text} ──

    def _parse_colorbox(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\colorbox{color}{text} 或 \\fcolorbox{border}{fill}{text}。"""
        try:
            i = _skip_spaces(s, i, n)
            if i < n and s[i] == '{':
                _, i = _skip_group(s, i)  # skip color name
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        try:
            content_text = self.parse(content_raw)
        except Exception:
            content_text = Text(content_raw)

        result = Text("[")
        result.append_text(content_text)
        result.append("]", style=_STYLE_BOXED)
        return result, i
