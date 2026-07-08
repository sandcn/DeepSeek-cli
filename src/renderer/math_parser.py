"""MathParser — LaTeX 数学公式解析器（递归下降）。

将 LaTeX 数学表达式解析为 Rich Text 对象，支持：
  分数 \\frac{}{}、根式 \\sqrt{}、积分/求和 \\int/\\sum、
  上下标 ^_、矩阵环境 \\begin{matrix}、分段函数 cases、
  二项式 \\binom、划除 \\cancel、颜色 \\color、
  自定义算子 \\operatorname、可扩展箭头 \\xrightarrow、
  向量箭头 \\overrightarrow、重音符号 \\hat/\\bar/\\vec、
  上划线/下划线、上下堆积 \\underset/\\overset、
  前上下标 \\prescript、\\sideset、\\substack 等。

内部依赖 math_symbols.py 提供的符号表和样式常量。

拆分说明：
  - math_parser_helpers.py  — 模块级辅助函数
  - math_parser_fractions.py — 分数/二项式 Mixin
  - math_parser_matrix.py    — 矩阵环境 Mixin
  - math_parser_core_commands.py  — 主要命令 Mixin (sqrt/text/delim/accent/color等)
  - math_parser_extra_commands.py — 更多命令 Mixin (smash/pmod/not/tag/xarrow等)
"""

from __future__ import annotations

from typing import List, Tuple

from rich.style import Style
from rich.text import Text

from .math_symbols import (
    # 样式常量
    _STYLE_NUMBER,
    _STYLE_OPERATOR,
    _STYLE_SUPERSCRIPT,
    _STYLE_SUBSCRIPT,
    _STYLE_FUNCTION,
    _STYLE_TEXT,
    # 映射/字典
    _OPERATOR_CHARS,
    _SPACE_MAP,
    _ACCENT_MAP,
    _DELIMITER_MAP,
    _COMMAND_MAP,
    _LIMIT_FUNCTIONS,
    _BIG_OPERATOR_COMMANDS,
    _LOGICAL_ARROWS,
    _SILENT_COMMANDS,
    # 上下标映射
    _SUPERSCRIPT_MAP,
    _SUBSCRIPT_MAP,
)

# 从辅助函数模块导入
from .math_parser_helpers import (
    _SUBSCRIPT_UNICHARS, _SUPERSCRIPT_UNICHARS,
    _extract_braced_group, _skip_group, _skip_spaces,
    _convert_to_superscript, _convert_to_subscript,
    _convert_to_superscript_progressive, _convert_to_subscript_progressive,
    _all_chars_mapped, _has_operator, _find_tex_primitive,
    re_split_rows,
)

# 从 Mixin 模块导入
from .math_parser_fractions import MathParserFractionsMixin
from .math_parser_matrix import MathParserMatrixMixin
from .math_parser_core_commands import MathParserCoreCommandsMixin
from .math_parser_extra_commands import MathParserExtraCommandsMixin


class MathParser(
    MathParserFractionsMixin,
    MathParserMatrixMixin,
    MathParserCoreCommandsMixin,
    MathParserExtraCommandsMixin,
):
    """LaTeX 数学公式解析器，将 LaTeX 表达式转换为 Rich Text 对象。

    通过多继承从各 Mixin 模块获取分数、矩阵、重音、颜色、命令等的
    解析方法。核心解析逻辑（parse、_parse_command）在此类中直接定义。
    """

    def __init__(self, is_block: bool = False) -> None:
        self._is_block = is_block
        # 大算符极限追踪 — 栈结构，支持连续多个大算符
        self._bigop_stack: list[dict] = []  # 每个元素: {op, sup?, sub?, is_limit_fn?}

    # ── 解析核心 ────────────────────────────────────────

    def parse(self, s: str) -> Text:
        """递归下降解析 LaTeX 表达式，返回带样式的 Text。"""
        # ── 保存/重置大算符状态（防止递归解析时误触发）──
        saved_stack = list(self._bigop_stack)
        self._bigop_stack = []

        result = Text()
        i = 0
        n = len(s)

        while i < n:
            c = s[i]

            if c == '\\':
                # ── 清空待处理大算符极限 ────────────────
                self._flush_bigop_limits(result)
                cmd_text, i = self._parse_command(s, i, n)
                # 大算符命令返回空 Text，由 _flush_bigop_limits 发射
                if cmd_text.plain:
                    result.append_text(cmd_text)

            elif c == '^':
                if self._bigop_stack:
                    sup_text, i = self._parse_script(s, i, n, is_sup=True)
                    self._bigop_stack[-1]["sup"] = sup_text
                    if "sub" in self._bigop_stack[-1]:
                        self._flush_bigop_limits(result)
                else:
                    sup, i = self._parse_script(s, i, n, is_sup=True)
                    result.append_text(sup)

            elif c == '_':
                if self._bigop_stack:
                    sub_text, i = self._parse_script(s, i, n, is_sup=False)
                    self._bigop_stack[-1]["sub"] = sub_text
                    if "sup" in self._bigop_stack[-1]:
                        self._flush_bigop_limits(result)
                else:
                    sub, i = self._parse_script(s, i, n, is_sup=False)
                    result.append_text(sub)

            elif c == '{':
                # ── 清空待处理大算符极限 ────────────────
                self._flush_bigop_limits(result)
                content, end = _extract_braced_group(s, i)
                if end > i:
                    # ── 检测 TeX 原语 \over / \choose ──────────
                    over_idx = _find_tex_primitive(content, "over")
                    choose_idx = _find_tex_primitive(content, "choose")
                    if over_idx is not None:
                        num_raw_ov = content[:over_idx].strip()
                        den_raw_ov = content[over_idx + 5:].strip()
                        num_text = self.parse(num_raw_ov) if num_raw_ov else Text()
                        den_text = self.parse(den_raw_ov) if den_raw_ov else Text()
                        frac = self._make_fraction(num_text, den_text, num_raw_ov, den_raw_ov)
                        result.append_text(frac)
                    elif choose_idx is not None:
                        top_raw = content[:choose_idx].strip()
                        bot_raw = content[choose_idx + 7:].strip()
                        top_text = self.parse(top_raw) if top_raw else Text()
                        bot_text = self.parse(bot_raw) if bot_raw else Text()
                        choose = self._make_binom(top_text, bot_text, top_raw, bot_raw)
                        result.append_text(choose)
                    else:
                        group_text = self.parse(content)
                        result.append_text(group_text)
                    i = end
                else:
                    result.append(c)
                    i += 1

            elif c == '}':
                i += 1

            else:
                # ── 清空待处理大算符极限 ────────────────
                self._flush_bigop_limits(result)
                if c.isdigit():
                    result.append(c, style=_STYLE_NUMBER)
                elif c in _OPERATOR_CHARS:
                    result.append(c, style=_STYLE_OPERATOR)
                else:
                    result.append(c)
                i += 1

        # ── 循环结束，刷出残留的大算符极限 ────────────
        self._flush_bigop_limits(result)
        # ── 恢复上级大算符状态（用于递归返回后） ──────
        self._bigop_stack = saved_stack
        return result

    # ── 大算符/极限函数处理 ────────────────────────────

    def _flush_bigop_limits(self, result: Text) -> None:
        """刷出待处理的大算符/极限函数缓存到 result 中。"""
        if not self._bigop_stack:
            return

        limits = self._bigop_stack.pop()
        op = limits.get("op", "∑")
        sup_text = limits.get("sup")
        sub_text = limits.get("sub")
        is_limit_fn = limits.get("is_limit_fn", False)

        if is_limit_fn:
            # ── 极限函数：lim(x→0) 括号格式 ──────────
            result.append(op.rstrip())
            if sup_text is not None and sub_text is not None:
                result.append("(")
                result.append(sub_text.plain.rstrip())
                result.append(" → ")
                result.append(sup_text.plain.rstrip())
                result.append(")")
            elif sub_text is not None:
                result.append("(")
                result.append(sub_text.plain.rstrip())
                result.append(")")
            elif sup_text is not None:
                result.append("(")
                result.append(sup_text.plain.rstrip())
                result.append(")")
        else:
            # ── 大算符：∑_{sub}^{sup} 紧凑格式 ────────
            result.append(op)
            if sub_text is not None and sup_text is not None:
                sub_plain = sub_text.plain.strip()
                sup_plain = sup_text.plain.strip()
                if ("∫" in op and
                    sub_plain and sup_plain and
                    len(sub_plain) <= 2 and len(sup_plain) <= 2 and
                    all(c in _SUBSCRIPT_UNICHARS for c in sub_plain) and
                    all(c in _SUPERSCRIPT_UNICHARS for c in sup_plain)):
                    result.append_text(sub_text)
                    result.append_text(sup_text)
                else:
                    result.append("_{")
                    result.append(sub_text.plain.rstrip(),
                                  style=sub_text.style if sub_text.style != Style() else _STYLE_SUBSCRIPT)
                    result.append("}^{")
                    result.append(sup_text.plain.rstrip(),
                                  style=sup_text.style if sup_text.style != Style() else _STYLE_SUPERSCRIPT)
                    result.append("}")
            elif sub_text is not None:
                result.append("_{")
                result.append(sub_text.plain.rstrip(),
                              style=sub_text.style if sub_text.style != Style() else _STYLE_SUBSCRIPT)
                result.append("}")
            elif sup_text is not None:
                result.append("^{")
                result.append(sup_text.plain.rstrip(),
                              style=sup_text.style if sup_text.style != Style() else _STYLE_SUPERSCRIPT)
                result.append("}")

    # ── 命令解析 ────────────────────────────────────────

    def _parse_command(self, s: str, i: int, n: int) -> Tuple[Text, int]:
        """解析 \\command 形式的 LaTeX 命令。

        Returns:
            (渲染后的 Text, 新位置)
        """
        i += 1
        if i >= n:
            return Text("\\"), i

        c = s[i]

        # ── 非字母命令（\\, \\! 等） ────────────────────
        if not c.isalpha():
            cmd = c
            i += 1
            if cmd in _SPACE_MAP:
                return Text(_SPACE_MAP[cmd]), i
            if cmd == ' ':
                return Text(" "), i
            return Text(cmd), i

        # 读取命令名（连续字母）
        start = i
        while i < n and s[i].isalpha():
            i += 1
        cmd = s[start:i]

        # ── 分数 \frac{num}{den} ────────────────────────
        if cmd == "frac":
            return self._parse_frac(s, i, n)

        # ── 分数变体 \tfrac \dfrac \cfrac ───────────────
        if cmd in ("tfrac", "dfrac", "cfrac"):
            return self._parse_frac(s, i, n)

        # ── 根式 \sqrt 和 \sqrt[n] ─────────────────────
        if cmd == "sqrt":
            return self._parse_sqrt(s, i, n)

        # ── 文本模式 ────────────────────────────────────
        if cmd in ("text", "textbf", "textit", "mathrm", "mathbf",
                   "mathcal", "mathit", "mathbb", "mathscr", "boldsymbol",
                   "textnormal", "normalfont", "mathbfit",
                   "mathsf", "mathtt", "mathfrak",
                   "Bbb",
                   "cal", "rm", "it", "sc", "sf", "tt"):
            return self._parse_textcmd(cmd, s, i, n)

        # ── 粗体 \bm（同 \boldsymbol）───────────────────
        if cmd == "bm":
            return self._parse_textcmd("boldsymbol", s, i, n)

        # ── 定界符 ──────────────────────────────────────
        if cmd in ("left", "right", "middle"):
            return self._parse_delimiter(cmd, s, i, n)

        # ── 尺寸定界符 \bigl \bigr \Bigl \Bigr 等 ──────
        if cmd in ("big", "Big", "bigg", "Bigg",
                   "bigl", "Bigl", "biggl", "Biggl",
                   "bigr", "Bigr", "biggr", "Biggr",
                   "bigm", "Bigm", "biggm", "Biggm"):
            return self._parse_size_delimiter(cmd, s, i, n)

        if cmd in ("lvert", "rvert", "lVert", "rVert",
                   "langle", "rangle",
                   "lfloor", "rfloor", "lceil", "rceil"):
            char = _DELIMITER_MAP.get(cmd, cmd)
            return Text(char), i

        # ── 矩阵环境 \begin{matrix}...\end{matrix} ──────
        if cmd == "begin":
            return self._parse_matrix_env(s, i, n)

        # ── 重音符号 ────────────────────────────────────
        if cmd in _ACCENT_MAP:
            return self._parse_accent(cmd, s, i, n)

        # ── 二项式系数 \binom{n}{k} ─────────────────────
        if cmd == "binom":
            return self._parse_binom(s, i, n)

        # ── 上下堆积 \underset{below}{base} / \overset{above}{base}
        if cmd in ("underset", "overset", "stackrel"):
            return self._parse_stacked(cmd, s, i, n)

        # ── 划除 \cancel{x} / \bcancel / \xcancel / \cancelto ──
        if cmd in ("cancel", "bcancel", "xcancel", "sout", "cancelto"):
            return self._parse_cancel(cmd, s, i, n)

        # ── 颜色 \color{red}{text} / \textcolor{red}{text} ──
        if cmd in ("color", "textcolor"):
            return self._parse_color(cmd, s, i, n)

        # ── 框 \boxed{expr} ─────────────────────────────
        if cmd == "boxed":
            return self._parse_boxed(s, i, n)

        # ── 垂直压缩 \smash[t/b]{content} ──────────────
        if cmd == "smash":
            return self._parse_smash(s, i, n)

        # ── 水平线 \rule[raise]{width}{height} ────────
        if cmd == "rule":
            return self._parse_rule(s, i, n)

        # ── 表格横线 \hline / \hdashline ──────────────
        if cmd in ("hline", "hdashline"):
            return self._parse_hhline(s, i, n)

        # ── 取模 \pmod{n} ───────────────────────────────
        if cmd == "pmod":
            return self._parse_pmod(s, i, n)

        # ── 否定前缀 \not ───────────────────────────────
        if cmd == "not":
            return self._parse_not(s, i, n)

        # ── 标签 \tag{...} ──────────────────────────────
        if cmd == "tag":
            return self._parse_tag(s, i, n)

        # ── 带星号的 \operatorname* ──────────────────────
        if cmd == "operatorname":
            # 检查后面是否有 *
            i_saved = i
            i = _skip_spaces(s, i, n)
            if i < n and s[i] == '*':
                i += 1  # 跳过 *
                return self._parse_operatorname(s, i, n, starred=True)
            return self._parse_operatorname(s, i, n, starred=False)

        # ── 水平盒 \hbox{content} / \mbox{content} ────
        if cmd in ("hbox", "mbox"):
            return self._parse_hbox(s, i, n)

        # ── 可扩展箭头 \xrightarrow{text} / \xleftarrow{text} ──
        if cmd in (
            "xrightarrow", "xleftarrow", "xmapsto",
            "xRightarrow", "xLeftarrow", "xLeftrightarrow",
            "xhookrightarrow", "xhookleftarrow",
            "xrightharpoonup", "xrightharpoondown",
            "xleftharpoonup", "xleftharpoondown",
            "xrightleftharpoons", "xleftrightharpoons",
            "xlongequal",
            "xleftrightarrow",
        ):
            return self._parse_xarrow(cmd, s, i, n)

        # ── 向量箭头 \overrightarrow{AB} / \overleftarrow{AB} ──
        if cmd in ("overrightarrow", "overleftarrow", "overleftrightarrow",
                   "underrightarrow", "underleftarrow"):
            return self._parse_vector_arrow(cmd, s, i, n)

        # ── 下标堆叠 \substack{...\\...} ────────────────
        if cmd == "substack":
            return self._parse_substack(s, i, n)

        # ── 上划线/下划线 \overline{z} / \underline{x} ──
        if cmd in ("overline", "underline"):
            return self._parse_overline_underline(cmd, s, i, n)

        # ── 前上下标 \prescript{sup}{sub}{base} ─────────
        if cmd == "prescript":
            return self._parse_prescript(s, i, n)

        # ── 多重上下标 \sideset{}{}{} ─────────────────
        if cmd == "sideset":
            return self._parse_sideset(s, i, n)

        # ── 限宽盒 \mathclap / \mathrlap / \mathllap ──
        if cmd in ("mathclap", "mathrlap", "mathllap"):
            return self._parse_clap(cmd, s, i, n)

        # ── 缩放盒 \scalebox / \reflectbox ────────────
        if cmd in ("scalebox", "reflectbox"):
            return self._parse_scalebox(cmd, s, i, n)

        # ── 旋转盒 \rotatebox{angle}{content} ─────────
        if cmd == "rotatebox":
            return self._parse_rotatebox(s, i, n)

        # ── 模运算变体 \mod{...} / \pod{...} ──────────
        if cmd == "mod":
            return self._parse_mod(s, i, n)
        if cmd == "pod":
            return self._parse_pod(s, i, n)
        if cmd == "bmod":
            return Text(" mod ", style=_STYLE_FUNCTION), i

        # ── 绝对值/范数 \abs{...} / \norm{...} ─────────
        if cmd == "abs":
            return self._parse_abs(s, i, n)
        if cmd == "norm":
            return self._parse_norm(s, i, n)

        # ── 花括号 \overbrace{expr} / \underbrace{expr} ──
        if cmd in ("overbrace", "underbrace"):
            return self._parse_overunderbrace(cmd, s, i, n)

        # ── 彩色框 \colorbox{color}{text} / \fcolorbox{border}{fill}{text} ──
        if cmd in ("colorbox", "fcolorbox"):
            return self._parse_colorbox(s, i, n)

        # ── 排版样式 \displaystyle / \textstyle ──────────
        if cmd == "displaystyle":
            self._is_block = True
            return Text(), i
        if cmd == "textstyle":
            self._is_block = False
            return Text(), i

        # ── 极限位置 \limits / \nolimits ────────────────
        if cmd == "limits":
            return Text(), i
        if cmd == "nolimits":
            if self._bigop_stack:
                dummy = Text()
                limits = self._bigop_stack.pop()
                op = limits.get("op", "∑")
                dummy.append(op)
                return dummy, i
            self._bigop_stack.clear()
            return Text(), i

        # ── 逻辑箭头（自动加空格）───────────────────────
        if cmd in _LOGICAL_ARROWS:
            return Text(_LOGICAL_ARROWS[cmd], style=_STYLE_OPERATOR), i

        # ── 间距控制命令 \mathbin / \mathrel / \mathord / \mathop ──
        if cmd in ("mathbin", "mathrel", "mathord", "mathop", "mathinner"):
            content_raw, end = _extract_braced_group(s, i)
            if end > i:
                i = end
                if content_raw:
                    parsed = self.parse(content_raw)
                    if cmd == "mathbin":
                        result = Text(" ")
                        result.append_text(parsed)
                        result.append(" ")
                        return result, i
                    elif cmd in ("mathrel", "mathinner"):
                        result = Text("  ")
                        result.append_text(parsed)
                        result.append("  ")
                        return result, i
                    else:
                        return parsed, i
            return Text(), i

        # ── 静默忽略 ────────────────────────────────────
        if cmd in _SILENT_COMMANDS:
            if cmd in ("hspace", "vspace", "label", "ref", "raisebox",
                       "kern", "mkern", "mskip", "hrule"):
                content, end = _skip_group(s, i)
                if end > i:
                    i = end
            if cmd in ("phantom", "hphantom", "vphantom"):
                content, end = _skip_group(s, i)
                if end > i:
                    i = end
            if cmd == "mathchoice":
                for _ in range(4):
                    content, end = _skip_group(s, i)
                    if end > i:
                        i = end
            if cmd == "definecolor":
                for _ in range(3):
                    content, end = _skip_group(s, i)
                    if end > i:
                        i = end
            if cmd in ("intertext", "shortintertext"):
                content_raw, end = _extract_braced_group(s, i)
                if end > i:
                    i = end
                    if content_raw:
                        return Text(content_raw, style=_STYLE_TEXT), i
            return Text(), i

        # ── 符号表查找 ──────────────────────────────────
        if cmd in _COMMAND_MAP:
            char, style = _COMMAND_MAP[cmd]
            if style == _STYLE_FUNCTION and i < n:
                next_c = s[i]
                if next_c.isalpha() or next_c.isdigit() or next_c in ('_', '^'):
                    char += " "
            if cmd in _LIMIT_FUNCTIONS:
                self._bigop_stack.append({"op": char, "is_limit_fn": True})
                return Text(), i
            if cmd in _BIG_OPERATOR_COMMANDS:
                self._bigop_stack.append({"op": char})
                return Text(), i
            return Text(char, style=style), i

        # ── 未知命令 — 原样输出 ────────────────────────
        return Text(f"\\{cmd}"), i

    # ── 上下标 ──────────────────────────────────────────

    def _parse_script(self, s: str, i: int, n: int, is_sup: bool) -> Tuple[Text, int]:
        """解析上标 ^ 或下标 _。"""
        i += 1
        if i >= n:
            return Text(), i

        try:
            if s[i] == '{':
                raw_content, i = _extract_braced_group(s, i)
            else:
                raw_content = s[i]
                i += 1
        except Exception:
            return Text(), i

        if not raw_content:
            return Text(), i

        script_style = _STYLE_SUPERSCRIPT if is_sup else _STYLE_SUBSCRIPT
        mapping = _SUPERSCRIPT_MAP if is_sup else _SUBSCRIPT_MAP

        has_latex = any(ch in raw_content for ch in '\\^_{}')
        if not has_latex and _all_chars_mapped(raw_content, mapping):
            converted = (_convert_to_superscript if is_sup else _convert_to_subscript)(raw_content)
            return Text(converted, style=script_style), i

        try:
            parsed = self.parse(raw_content)
        except Exception:
            parsed = Text(raw_content)
        parsed.stylize(script_style)
        return parsed, i
