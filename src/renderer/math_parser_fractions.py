"""math_parser_fractions — MathParser 分数与二项式系数 Mixin。

包含 \frac (tfrac/dfrac/cfrac)、\binom 的相关解析与渲染方法。
"""

from __future__ import annotations

from typing import Tuple

from rich.text import Text

from .math_parser_helpers import (
    _SUPERSCRIPT_MAP, _SUBSCRIPT_MAP,
    _STYLE_SUPERSCRIPT, _STYLE_SUBSCRIPT, _STYLE_FRAC_LINE,
    _convert_to_superscript, _convert_to_subscript,
    _all_chars_mapped, _has_operator,
    _extract_braced_group, _skip_spaces,
)


class MathParserFractionsMixin:
    """MathParser 分数与二项式系数 Mixin。

    提供以下方法，供 MathParser 通过多继承使用：
      _make_fraction()
      _parse_frac()
      _make_binom()
      _parse_binom()
    """

    # ── 分数 ────────────────────────────────────────────

    def _make_fraction(self, num_text: Text, den_text: Text,
                       num_raw: str = "", den_raw: str = "") -> Text:
        """用分数斜线（U+2044）渲染分数： num⁄den

        当分子/分母包含运算符时自动加括号，避免歧义。
        对于纯文本简单分数，自动使用上标分子+下标分母美化： ᵃ⁄ₙ
        """
        result = Text()
        needs_paren_num = bool(num_raw) and _has_operator(num_raw)
        needs_paren_den = bool(den_raw) and _has_operator(den_raw)

        # ── 对无运算符的简单分数使用 Unicode 上标/下标美化 ──
        if not needs_paren_num and not needs_paren_den:
            num_plain = num_text.plain.strip()
            den_plain = den_text.plain.strip()
            if (num_plain and den_plain and
                _all_chars_mapped(num_plain, _SUPERSCRIPT_MAP) and
                _all_chars_mapped(den_plain, _SUBSCRIPT_MAP)):
                result.append(_convert_to_superscript(num_plain), style=_STYLE_SUPERSCRIPT)
                result.append("\u2044", style=_STYLE_FRAC_LINE)  # ⁄
                result.append(_convert_to_subscript(den_plain), style=_STYLE_SUBSCRIPT)
                return result

        if needs_paren_num:
            result.append("(")
        result.append_text(num_text)
        if needs_paren_num:
            result.append(")")
        result.append("\u2044", style=_STYLE_FRAC_LINE)  # ⁄ 分数斜线
        if needs_paren_den:
            result.append("(")
        result.append_text(den_text)
        if needs_paren_den:
            result.append(")")
        return result

    def _parse_frac(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\frac{num}{den}。"""
        try:
            i = _skip_spaces(s, i, n)
            # 可选参数 [l]/[r]/[c]（用于 \cfrac）
            if i < n and s[i] == '[':
                close_bracket = s.find(']', i)
                if close_bracket != -1:
                    i = close_bracket + 1
                i = _skip_spaces(s, i, n)
            start_i = i
            num_raw, i = _extract_braced_group(s, i)
            if not num_raw and i == start_i:
                return Text("\\frac"), i
            i = _skip_spaces(s, i, n)
            start_i = i
            den_raw, i = _extract_braced_group(s, i)
            if not den_raw and i == start_i:
                return Text("\\frac"), i
        except Exception:
            return Text("\\frac"), i

        try:
            num_text = self.parse(num_raw)
            den_text = self.parse(den_raw)
        except Exception:
            return Text(f"{num_raw}\u2044{den_raw}"), i

        return self._make_fraction(num_text, den_text, num_raw, den_raw), i

    # ── 二项式系数 \binom{n}{k} ─────────────────────────

    def _make_binom(self, top_text: Text, bot_text: Text,
                    top_raw: str = "", bot_raw: str = "") -> Text:
        """渲染二项式系数为 (n|k) 紧凑格式。

        包含运算符时自动加括号。
        """
        result = Text()
        result.append("(")
        if top_raw and _has_operator(top_raw):
            result.append("(")
        result.append_text(top_text)
        if top_raw and _has_operator(top_raw):
            result.append(")")
        result.append("\u00A6")  # broken bar (¦) as separator
        if bot_raw and _has_operator(bot_raw):
            result.append("(")
        result.append_text(bot_text)
        if bot_raw and _has_operator(bot_raw):
            result.append(")")
        result.append(")")
        return result

    def _parse_binom(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\binom{n}{k}。渲染为 (n|k) 紧凑格式。"""
        try:
            i = _skip_spaces(s, i, n)
            start_i = i
            top_raw, i = _extract_braced_group(s, i)
            if not top_raw and i == start_i:
                return Text("\\binom"), i
            i = _skip_spaces(s, i, n)
            start_i = i
            bot_raw, i = _extract_braced_group(s, i)
            if not bot_raw and i == start_i:
                return Text("\\binom"), i
        except Exception:
            return Text("\\binom"), i

        try:
            top_text = self.parse(top_raw)
            bot_text = self.parse(bot_raw)
        except Exception:
            return Text(f"({top_raw}|{bot_raw})"), i

        return self._make_binom(top_text, bot_text, top_raw, bot_raw), i
