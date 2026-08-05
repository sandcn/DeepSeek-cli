"""math_parser_extra_commands — MathParser 更多 LaTeX 命令 Mixin（第2组）。

包含 smash、pmod/mod/pod、not、tag、operatorname、xarrow、
vector_arrow、overline/underline、substack、prescript、sideset、
abs/norm、rule/hhline、clap、scalebox/rotatebox、hbox 等命令。
"""

from __future__ import annotations

import re as _re
from typing import Tuple
from rich.style import Style
from rich.text import Text

from .math_parser_helpers import (
    _STYLE_DEFAULT, _STYLE_FUNCTION, _STYLE_OPERATOR,
    _STYLE_SUPERSCRIPT, _STYLE_SUBSCRIPT, _STYLE_ACCENT,
    _STYLE_BOXED, _STYLE_CANCEL, _STYLE_TAG, _STYLE_TEXT,
    _convert_to_superscript_progressive, _convert_to_subscript_progressive,
    _extract_braced_group, _skip_spaces, _skip_group,
)
from .math_symbols import (
    _COMMAND_MAP, _RELATION_SYMBOLS,
)


class MathParserExtraCommandsMixin:
    """MathParser 更多命令 Mixin（第2组）。

    提供以下方法：
      _parse_smash()
      _parse_pmod()
      _parse_mod()
      _parse_pod()
      _parse_not()
      _parse_tag()
      _parse_operatorname()
      _parse_xarrow()
      _parse_vector_arrow()
      _parse_overline_underline()
      _parse_substack()
      _parse_prescript()
      _parse_sideset()
      _parse_abs()
      _parse_norm()
      _parse_rule()
      _parse_hhline()
      _parse_clap()
      _parse_scalebox()
      _parse_rotatebox()
      _parse_hbox()
    """

    # ── 垂直压缩 \smash[t/b]{content} ─────────────────

    def _parse_smash(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\smash{content} 和 \\smash[t]{content}。

        终端无法模拟垂直压缩效果，直接渲染内容。
        """
        try:
            i = _skip_spaces(s, i, n)
            # 跳过可选参数 [t]/[b]
            if i < n and s[i] == '[':
                close_bracket = s.find(']', i)
                if close_bracket != -1:
                    i = close_bracket + 1
                i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        return self.parse(content_raw), i

    # ── 取模 \pmod{n} ──────────────────────────────────

    def _parse_pmod(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\pmod{n} → (mod n)。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text("(mod)"), i

        result = Text()
        result.append("(mod ", style=_STYLE_FUNCTION)
        result.append_text(self.parse(content_raw))
        result.append(")", style=_STYLE_FUNCTION)
        return result, i

    # ── 模运算 \mod{...} / \pod{...} ──────────────────

    def _parse_mod(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\mod{...} → "mod ..."（带前空格）。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        result = Text(" mod ", style=_STYLE_FUNCTION)
        if content_raw:
            result.append_text(self.parse(content_raw))
        return result, i

    def _parse_pod(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\pod{...} → "(...)"。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        result = Text("(", style=_STYLE_FUNCTION)
        if content_raw:
            result.append_text(self.parse(content_raw))
        result.append(")", style=_STYLE_FUNCTION)
        return result, i

    # ── 否定前缀 \not ──────────────────────────────────

    def _parse_not(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\not 否定前缀。\\not= → ≠，\\not< → ≮ 等。

        策略：先尝试匹配组合符号（如 \\not= 已在关系表中），
        否则在原符号前加否定斜线。
        """
        # 跳过空白
        i = _skip_spaces(s, i, n)
        if i >= n:
            return Text("̸"), i  # 单独否定斜线

        c = s[i]

        # 如果是命令，先读取命令名
        if c == '\\':
            i += 1
            start = i
            while i < n and s[i].isalpha():
                i += 1
            next_cmd = s[start:i]

            # 直接尝试组合否定（如 \notin 已经在关系表中）
            combined = f"not{next_cmd}"
            if combined in _COMMAND_MAP:
                char, style = _COMMAND_MAP[combined]
                return Text(char, style=style), i

            # 否则在原符号前加否定斜线
            if combined in _RELATION_SYMBOLS:
                return Text(_RELATION_SYMBOLS[combined]), i

            # 单独否定斜线 + 原命令
            cmd_char, _ = _COMMAND_MAP.get(next_cmd, (f"\\{next_cmd}", _STYLE_DEFAULT))
            result = Text("̸", style=_STYLE_OPERATOR)
            result.append(cmd_char)
            return result, i
        else:
            # 单字符关系符前加否定斜线
            i += 1
            result = Text("̸", style=_STYLE_OPERATOR)
            result.append(c)
            return result, i

    # ── 标签 \tag{...} ─────────────────────────────────

    def _parse_tag(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\tag{...} 和 \\tag*{...}。"""
        try:
            i = _skip_spaces(s, i, n)
            # 检测星号变体 \tag*
            starred = False
            if i < n and s[i] == '*':
                starred = True
                i += 1
                i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        text = content_raw.strip()
        if not starred and not text.startswith('(') and not text.startswith('['):
            text = f"({text})"

        return Text(f" {text}", style=_STYLE_TAG), i

    # ── 自定义算子 \operatorname{name} ─────────────────

    def _parse_operatorname(self, s: str, i: int, n: int,
                            starred: bool = False) -> Tuple[Text, int]:
        """解析 \\operatorname{name} 或 \\operatorname*{name}。

        以函数样式渲染算子名。
        \\operatorname*{name} 为带星号版本，后续的 _{} 和 ^{}
        会被渲染为极限形式（类似 \\max_{x}）。
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        op_text = Text(content_raw, style=_STYLE_FUNCTION)

        if starred:
            # 启用极限追踪，使 \\operatorname*{argmax}_{x} 渲染为 argmax(x) 形式
            self._bigop_stack.append({"op": op_text.plain + " ", "is_limit_fn": True})
            return Text(), i  # 由 _flush_bigop_limits 统一发射

        return op_text, i

    # ── 可扩展箭头 \xrightarrow{text} ─────────────────

    def _parse_xarrow(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\xrightarrow{text}、\\xleftarrow{text} 等可扩展箭头。

        \\xrightarrow{text} → 上标文字 + 箭头符号
        \\xleftarrow{text}  → 箭头符号 + 上标文字
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            content_raw = ""

        # 箭头字符映射
        arrow_chars = {
            "xrightarrow": "→", "xleftarrow": "←", "xmapsto": "⟼",
            "xRightarrow": "⇒", "xLeftarrow": "⇐", "xLeftrightarrow": "⇔",
            "xhookrightarrow": "↪", "xhookleftarrow": "↩",
            "xrightharpoonup": "⇀", "xrightharpoondown": "⇁",
            "xleftharpoonup": "↼", "xleftharpoondown": "↽",
            "xrightleftharpoons": "⇌", "xleftrightharpoons": "⇋",
            "xlongequal": "═",
            "xleftrightarrow": "⟷",
        }
        arrow = arrow_chars.get(cmd, "→")

        result = Text()
        if cmd in ("xleftarrow", "xhookleftarrow", "xLeftarrow",
                    "xleftharpoonup", "xleftharpoondown",
                    "xleftrightharpoons", "xleftrightarrow"):
            # 左向箭头：文字在箭头上方
            if content_raw:
                sup = _convert_to_superscript_progressive(content_raw)
                if sup:
                    result.append(sup, style=_STYLE_SUPERSCRIPT)
            result.append(arrow)
        else:
            # 右向箭头：文字在箭头上方
            result.append(arrow)
            if content_raw:
                sup = _convert_to_superscript_progressive(content_raw)
                if sup:
                    result.append(sup, style=_STYLE_SUPERSCRIPT)
        return result, i

    # ── 向量箭头 \overrightarrow{AB} ─────────────────────

    def _parse_vector_arrow(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\overrightarrow{AB} / \\overleftarrow{AB} 等向量箭头。"""
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

        arrow_char = {
            "overrightarrow": "\u20D7",
            "overleftarrow": "\u20D6",
            "overleftrightarrow": "\u20E1",
            "underrightarrow": "\u20D7",
            "underleftarrow": "\u20D6",
        }.get(cmd, "\u20D7")

        result = Text()
        for ch in content_text.plain:
            result.append(ch)
            result.append(arrow_char, style=_STYLE_ACCENT)
        return result, i

    # ── 上划线/下划线 \overline{z} / \underline{x} ──

    def _parse_overline_underline(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\overline{z} 和 \\underline{x}。"""
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

        combining = "\u0305" if cmd == "overline" else "\u0332"

        result = Text()
        for ch in content_text.plain:
            result.append(ch)
            result.append(combining, style=_STYLE_ACCENT)
        return result, i

    # ── 下标堆叠 \substack{...\\...} ──────────────────

    def _parse_substack(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\substack{...\\...}。用于求和/积分下标的多行堆叠。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        if not content_raw:
            return Text(), i

        # 按 \\ 分割
        parts = [p.strip() for p in _re.split(r'\\\\|\\{2}', content_raw) if p.strip()]
        result = Text()
        for pi, part in enumerate(parts):
            if pi > 0:
                result.append(" | ", style=_STYLE_SUBSCRIPT)
            result.append_text(self.parse(part))
        return result, i

    # ── 前上下标 \prescript{sup}{sub}{base} ─────────

    def _parse_prescript(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\prescript{sup}{sub}{base} → 前上下标 + 基础符号。"""
        try:
            i = _skip_spaces(s, i, n)
            sup_raw, i = _extract_braced_group(s, i)
            i = _skip_spaces(s, i, n)
            sub_raw, i = _extract_braced_group(s, i)
            i = _skip_spaces(s, i, n)
            base_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        result = Text()
        if sup_raw:
            sup_chars = _convert_to_superscript_progressive(sup_raw)
            if sup_chars:
                result.append(sup_chars, style=_STYLE_SUPERSCRIPT)
        if sub_raw:
            sub_chars = _convert_to_subscript_progressive(sub_raw)
            if sub_chars:
                result.append(sub_chars, style=_STYLE_SUBSCRIPT)
        if base_raw:
            result.append_text(self.parse(base_raw))
        return result, i

    # ── 多重上下标 \sideset{}{}{} ────────────────────

    def _parse_sideset(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\sideset{_left^left}{_right^right}{base}。

        用于 ∑ 等算符的多重上下标。
        """
        try:
            i = _skip_spaces(s, i, n)
            left_raw, i = _extract_braced_group(s, i)
            i = _skip_spaces(s, i, n)
            right_raw, i = _extract_braced_group(s, i)
            i = _skip_spaces(s, i, n)
            base_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i

        result = Text()
        # 左侧上下标
        if left_raw:
            left_text = self.parse(left_raw)
            result.append_text(left_text)
        # 基础符号
        if base_raw:
            result.append_text(self.parse(base_raw))
        # 右侧上下标
        if right_raw:
            right_text = self.parse(right_raw)
            result.append_text(right_text)
        return result, i

    # ── 绝对值 \abs{...} / 范数 \norm{...} ─────────

    def _parse_braced_delim(self, s: str, i: int, n: int, left: str, right: str) -> Tuple[Text, int]:
        """解析 \\<cmd>{...} → left + 内容 + right。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        result = Text(left)
        if content_raw:
            result.append_text(self.parse(content_raw))
        result.append(right)
        return result, i

    def _parse_abs(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\abs{...} → |...|。"""
        return self._parse_braced_delim(s, i, n, "|", "|")

    def _parse_norm(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\norm{...} → ‖...‖。"""
        return self._parse_braced_delim(s, i, n, "‖", "‖")

    # ── 水平线 \rule[raise]{width}{height} ─────────────

    def _parse_rule(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\rule[raise]{width}{height}。终端中以 dim 样式的横线表示。"""
        try:
            i = _skip_spaces(s, i, n)
            # 可选参数 [raise]
            if i < n and s[i] == '[':
                close = s.find(']', i)
                if close != -1:
                    i = close + 1
            i = _skip_spaces(s, i, n)
            # 第一个参数 {width}
            _, i = _skip_group(s, i)
            i = _skip_spaces(s, i, n)
            # 第二个参数 {height}
            _, i = _skip_group(s, i)
        except Exception:
            return Text(), i
        return Text("────", style=Style(dim=True, color="bright_black")), i

    # ── 表格横线 \hline / \hdashline ──────────────────

    def _parse_hhline(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\hline 或 \\hdashline。终端中以 dim 横线表示。"""
        return Text(" ── ", style=Style(dim=True, color="bright_black")), i

    # ── 限宽盒 \mathclap / \mathrlap / \mathllap ─────

    def _parse_clap(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\mathclap{content}、\\mathrlap{content}、\\mathllap{content}。

        终端无法真实模拟 clap/rlap/llap，直接渲染内容。
        """
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        if not content_raw:
            return Text(), i
        return self.parse(content_raw), i

    # ── 缩放/旋转盒 \scalebox / \reflectbox / \rotatebox ──

    def _parse_scalebox(self, cmd: str, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\scalebox{factor}{content} 或 \\reflectbox{content}。"""
        try:
            i = _skip_spaces(s, i, n)
            if cmd == "reflectbox":
                content_raw, i = _extract_braced_group(s, i)
            else:
                # \scalebox{factor}{content}
                _, i = _skip_group(s, i)  # skip factor
                i = _skip_spaces(s, i, n)
                content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        if not content_raw:
            return Text(), i
        return self.parse(content_raw), i

    # ── 旋转盒 \rotatebox{angle}{content} ─────────────

    def _parse_rotatebox(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\rotatebox{angle}{content}。"""
        try:
            i = _skip_spaces(s, i, n)
            _, i = _skip_group(s, i)  # skip angle
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        if not content_raw:
            return Text(), i
        return self.parse(content_raw), i

    # ── 水平盒 \hbox{content} / \mbox{content} ───────

    def _parse_hbox(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\hbox{content}。以文本样式渲染内容。"""
        try:
            i = _skip_spaces(s, i, n)
            content_raw, i = _extract_braced_group(s, i)
        except Exception:
            return Text(), i
        if not content_raw:
            return Text(), i
        return Text(content_raw, style=_STYLE_TEXT), i
