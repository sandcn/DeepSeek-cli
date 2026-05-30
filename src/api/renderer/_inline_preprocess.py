"""内联渲染预处理函数（字符级，无正则）。"""

from __future__ import annotations

from ._utils import _HTML_ENTITIES
from .emoji_map import EMOJI_MAP


# 内联渲染最大嵌套深度，防止过度递归导致 RecursionError
_MAX_RECURSION_DEPTH = 20

# 内联格式标记字符（快速筛查用）
_INLINE_FORMAT_CHARS = frozenset('_*~`=[!<&$^\\+|{%')

# ── 智能排版触发字符（用于快速跳过检查） ──
# 这些字符的出现可能触发智能排版转换（emoji/entities/dashes/ellipsis/arrows/copyright/comparisons）
# 注：分数符号由 / 触发（与分子数字组合），不单独引入数字字符
_PREPROCESS_TRIGGER_CHARS = frozenset(':-&.(/><=!~+/')

# ── 智能排版：分数符号映射 ──
_FRACTIONS: dict[tuple[str, str], str] = {
    ('1', '2'): '\u00BD',  # ½
    ('1', '3'): '\u2153',  # ⅓
    ('2', '3'): '\u2154',  # ⅔
    ('1', '4'): '\u00BC',  # ¼
    ('3', '4'): '\u00BE',  # ¾
    ('1', '5'): '\u2155',  # ⅕
    ('2', '5'): '\u2156',  # ⅖
    ('3', '5'): '\u2157',  # ⅗
    ('4', '5'): '\u2158',  # ⅘
    ('1', '6'): '\u2159',  # ⅙
    ('5', '6'): '\u215A',  # ⅚
    ('1', '8'): '\u215B',  # ⅛
    ('3', '8'): '\u215C',  # ⅜
    ('5', '8'): '\u215D',  # ⅝
    ('7', '8'): '\u215E',  # ⅞
}


def _has_inline_format(text: str) -> bool:
    """检测文本是否包含内联格式标记字符。

    用于 render() 纯文本快速通道：不含这些字符则跳过解析器。
    """
    return any(ch in text for ch in _INLINE_FORMAT_CHARS)


# ═══════════════════════════════════════════════════════════
# 子函数：按类型拆分的智能排版预处理
# ═══════════════════════════════════════════════════════════


def _skip_html_comment(text: str, i: int, n: int, result: list[str], in_comment: bool) -> tuple[int, bool]:
    """检测并跳过 HTML 注释 <!-- ... --> 边界。

    若 text[i] 处于注释内或注释边界，则向 result 追加相应内容，
    返回 (新i, 新in_comment)；否则返回 (i, in_comment) 不变。
    调用方通过比较返回值 i 是否变化来判断是否需要 continue。
    """
    if not in_comment and text[i] == '<' and i + 3 < n and text[i:i + 4] == '<!--':
        result.append('<!--')
        return i + 4, True
    if in_comment and text[i] == '-' and i + 2 < n and text[i:i + 3] == '-->':
        result.append('-->')
        return i + 3, False
    if in_comment:
        result.append(text[i])
        return i + 1, True
    return i, in_comment


def _preprocess_dashes(text: str) -> str:
    """处理破折号和省略号：--- → —, -- → –, ... → …。

    HTML 注释 <!-- ... --> 体内的 dash 受保护不被转换。
    """
    result: list[str] = []
    i, n = 0, len(text)
    in_comment = False

    while i < n:
        ch = text[i]

        # HTML 注释边界检测
        ni, in_comment = _skip_html_comment(text, i, n, result, in_comment)
        if ni != i:
            i = ni
            continue

        # --- → em-dash
        if ch == '-' and i + 2 < n and text[i + 1] == '-' and text[i + 2] == '-':
            if i >= 2 and text[i - 2:i] == '<!':
                # <!-- 中的 --- 不转换
                result.append('---')
                i += 3
                continue
            result.append('\u2014')  # —
            i += 3
            continue

        # -- → en-dash（边界检查，避免 --verbose 等技术文本误触发）
        if ch == '-' and i + 1 < n and text[i + 1] == '-':
            prev_is_boundary = (i == 0 or not text[i - 1].isalnum())
            next_is_boundary = (i + 2 >= n or not text[i + 2].isalnum())
            not_in_comment = not (
                (i > 0 and text[i - 1] == '!' and (i == 1 or text[i - 2] == '<'))
                or (i + 2 < n and text[i + 2] == '>')
            )
            if prev_is_boundary and next_is_boundary and not_in_comment:
                result.append('\u2013')  # –
                i += 2
                continue
            result.append('--')
            i += 2
            continue

        # ... → ellipsis
        if ch == '.' and i + 2 < n and text[i + 1] == '.' and text[i + 2] == '.':
            result.append('\u2026')  # …
            i += 3
            continue

        result.append(ch)
        i += 1

    return ''.join(result)


def _preprocess_arrows(text: str) -> str:
    """处理箭头符号。

    -> → →, <- → ←, => → ⇒, <-> → ↔
    ==> → ⟹, <== → ⟸, <==> → ⟺
    均在边界检查后转换，避免代码误触发。
    """
    result: list[str] = []
    i, n = 0, len(text)
    in_comment = False

    while i < n:
        ch = text[i]

        # HTML 注释边界检测（dash 转换后 <!--/--> 已受保护，但仍需跳过体内 < = - 等）
        ni, in_comment = _skip_html_comment(text, i, n, result, in_comment)
        if ni != i:
            i = ni
            continue

        # <==> → ⟺（最长匹配优先）
        if ch == '<' and i + 3 < n and text[i + 1] == '=' and text[i + 2] == '=' and text[i + 3] == '>':
            if not (i > 0 and text[i - 1] in '<=>'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 4 >= n or (not text[i + 4].isalnum() and text[i + 4] not in '<=>'))
                if prev_ok and next_ok:
                    result.append('\u27FA')  # ⟺
                    i += 4
                    continue

        # <== → ⟸
        if ch == '<' and i + 2 < n and text[i + 1] == '=' and text[i + 2] == '=':
            if not (i > 0 and text[i - 1] in '<=>'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 3 >= n or text[i + 3].isspace() or text[i + 3] in ',.;:!?\'")]}'
                           or (not text[i + 3].isalnum() and text[i + 3] not in '<=>'))
                if prev_ok and next_ok:
                    result.append('\u27F8')  # ⟸
                    i += 3
                    continue

        # ==> → ⟹
        if ch == '=' and i + 1 < n and text[i + 1] == '=':
            if (i + 2 < n and text[i + 2] == '>'
                    and not (i > 0 and text[i - 1] in '=>')):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 3 >= n or text[i + 3].isspace() or text[i + 3] in ',.;:!?\'")]}'
                           or (not text[i + 3].isalnum() and text[i + 3] not in '=>'))
                if prev_ok and next_ok:
                    result.append('\u27F9')  # ⟹
                    i += 3
                    continue

        # <-> → ↔
        if ch == '<' and i + 2 < n and text[i + 1] == '-' and text[i + 2] == '>':
            if not (i > 0 and text[i - 1] in '<>'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 3 >= n or (not text[i + 3].isalnum() and text[i + 3] not in '<>'))
                if prev_ok and next_ok:
                    result.append('\u2194')  # ↔
                    i += 3
                    continue

        # -> → →
        if ch == '-' and i + 1 < n and text[i + 1] == '>':
            prev_ok = (i == 0 or not text[i - 1].isalnum())
            next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'"'
                       or (not text[i + 2].isalnum() and text[i + 2] not in '><-'))
            if prev_ok and next_ok:
                result.append('\u2192')  # →
                i += 2
                continue

        # <- → ←
        if ch == '<' and i + 1 < n and text[i + 1] == '-':
            if text[i + 2] != '-' if i + 2 < n else True:  # 不是 <!--
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'"'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '><-'))
                if prev_ok and next_ok:
                    result.append('\u2190')  # ←
                    i += 2
                    continue

        # => → ⇒
        if ch == '=' and i + 1 < n and text[i + 1] == '>':
            prev_ok = (i == 0 or not text[i - 1].isalnum())
            next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'"'
                       or (not text[i + 2].isalnum() and text[i + 2] != '>'))
            if prev_ok and next_ok:
                result.append('\u21D2')  # ⇒
                i += 2
                continue

        result.append(ch)
        i += 1

    return ''.join(result)


def _preprocess_math_symbols(text: str) -> str:
    """处理数学比较符号。

    <= → ≤, >= → ≥, != → ≠, ~= → ≈, +- → ±, +/- → ±
    均在边界检查后转换，避免代码误触发。
    """
    result: list[str] = []
    i, n = 0, len(text)
    in_comment = False

    while i < n:
        ch = text[i]

        # HTML 注释边界检测
        ni, in_comment = _skip_html_comment(text, i, n, result, in_comment)
        if ni != i:
            i = ni
            continue

        # +/- → ±
        if ch == '+' and i + 2 < n and text[i + 1] == '/' and text[i + 2] == '-':
            prev_ok = (i == 0 or not text[i - 1].isalnum())
            next_ok = (i + 3 >= n or text[i + 3].isspace() or text[i + 3] in ',.;:!?\'")]}'
                       or (not text[i + 3].isalnum() and text[i + 3] not in '+-*/'))
            if prev_ok and next_ok:
                result.append('\u00B1')  # ±
                i += 3
                continue

        # +- → ±
        if ch == '+' and i + 1 < n and text[i + 1] == '-':
            if not (i > 0 and text[i - 1] in '+-'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'")]}'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '+-*/'))
                if prev_ok and next_ok:
                    result.append('\u00B1')  # ±
                    i += 2
                    continue

        # <= → ≤
        if ch == '<' and i + 1 < n and text[i + 1] == '=':
            if not (i > 0 and text[i - 1] in '<>'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'")]}'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '><='))
                if prev_ok and next_ok:
                    result.append('\u2264')  # ≤
                    i += 2
                    continue

        # >= → ≥
        if ch == '>' and i + 1 < n and text[i + 1] == '=':
            if not (i > 0 and text[i - 1] in '<>'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'")]}'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '><='))
                if prev_ok and next_ok:
                    result.append('\u2265')  # ≥
                    i += 2
                    continue

        # != → ≠
        if ch == '!' and i + 1 < n and text[i + 1] == '=':
            if not (i > 0 and text[i - 1] == '!'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'")]}'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '!='))
                if prev_ok and next_ok:
                    result.append('\u2260')  # ≠
                    i += 2
                    continue

        # ~= → ≈
        if ch == '~' and i + 1 < n and text[i + 1] == '=':
            if not (i > 0 and text[i - 1] == '~'):
                prev_ok = (i == 0 or not text[i - 1].isalnum())
                next_ok = (i + 2 >= n or text[i + 2].isspace() or text[i + 2] in ',.;:!?\'")]}'
                           or (not text[i + 2].isalnum() and text[i + 2] not in '~='))
                if prev_ok and next_ok:
                    result.append('\u2248')  # ≈
                    i += 2
                    continue

        result.append(ch)
        i += 1

    return ''.join(result)


def _preprocess_html_entities(text: str) -> str:
    """处理 HTML 实体、Emoji 短代码、版权/商标符号、分数符号。

    - &amp; &lt; &gt; 等命名实体 → Unicode 字符
    - &#NNN; &#xHHH; 数字实体 → Unicode 字符
    - :emoji: 短代码 → Emoji Unicode
    - (c) → ©, (r) → ®, (tm) → ™
    - N/M → 分数 Unicode 符号（共14种）
    """
    result: list[str] = []
    i, n = 0, len(text)
    in_comment = False

    while i < n:
        ch = text[i]

        # HTML 注释边界检测
        ni, in_comment = _skip_html_comment(text, i, n, result, in_comment)
        if ni != i:
            i = ni
            continue

        # (c) → ©, (r) → ®, (tm) → ™
        if (ch == '(' and i + 2 < n and text[i + 2] == ')'
                and (i == 0 or not text[i - 1].isalnum())):
            inner = text[i + 1]
            if inner in ('c', 'C'):
                if i + 3 >= n or not text[i + 3].isalnum():
                    result.append('\u00A9')  # ©
                    i += 3
                    continue
            elif inner in ('r', 'R'):
                if i + 3 >= n or not text[i + 3].isalnum():
                    result.append('\u00AE')  # ®
                    i += 3
                    continue
        if (ch == '(' and i + 3 < n
                and text[i + 1:i + 3].lower() == 'tm'
                and text[i + 3] == ')'
                and (i == 0 or not text[i - 1].isalnum())):
            if i + 4 >= n or not text[i + 4].isalnum():
                result.append('\u2122')  # ™
                i += 4
                continue

        # 分数符号（边界检查）
        if ch.isdigit() and i + 2 < n and text[i + 1] == '/':
            prev_ok = (i == 0 or not text[i - 1].isalnum())
            if prev_ok:
                key = (ch, text[i + 2])
                if key in _FRACTIONS and (i + 3 >= n or not text[i + 3].isalnum()):
                    result.append(_FRACTIONS[key])
                    i += 3
                    continue

        # Emoji 短代码 :name:
        if ch == ':':
            name_start = i + 1
            j = name_start
            while j < n and (text[j].isalnum() or text[j] in '_-+'):
                j += 1
            if j < n and text[j] == ':' and j > name_start:
                name = text[name_start:j]
                full = f':{name}:'
                if full in EMOJI_MAP:
                    result.append(EMOJI_MAP[full])
                    i = j + 1
                    continue
            result.append(':')
            i += 1
            continue

        # HTML 实体 &xxx;
        if ch == '&':
            semicolon = text.find(';', i)
            if semicolon > i + 1 and semicolon - i <= 10:
                entity = text[i:semicolon + 1]
                if entity in _HTML_ENTITIES:
                    result.append(_HTML_ENTITIES[entity])
                    i = semicolon + 1
                    continue
                # 数字实体 &#NNN; 或 &#xHHH;
                if entity.startswith('&#') and len(entity) >= 5:
                    try:
                        num_str = entity[2:-1]
                        cp = int(num_str[1:], 16) if num_str.startswith(('x', 'X')) else int(num_str)
                        if cp > 0x10FFFF or (0xD800 <= cp <= 0xDFFF):
                            result.append(entity)
                            i = semicolon + 1
                            continue
                        if cp < 32 and cp not in (9, 10, 13):
                            result.append(entity)
                            i = semicolon + 1
                            continue
                        result.append(chr(cp))
                        i = semicolon + 1
                        continue
                    except (ValueError, OverflowError):
                        pass

            result.append(text[i])
            i += 1
            continue

        result.append(ch)
        i += 1

    return ''.join(result)


# ═══════════════════════════════════════════════════════════
# 主函数：依次调用子函数进行智能排版预处理
# ═══════════════════════════════════════════════════════════

def _preprocess_text(text: str) -> str:
    """多遍扫描处理 Emoji 短代码、HTML 实体解码、智能排版（无正则）。

    按类型拆分为 4 个子函数依次调用，每个子函数处理一种转换类型，
    确保转换顺序正确（箭头优先于比较符号，避免 <== 被 <= 截胡）。

    ★ 智能排版（非 HTML 语法）：
      --- → — (em-dash)，-- → – (en-dash)，... → … (ellipsis)
      (c) → ©, (r) → ®, (tm) → ™ — 版权/商标符号
      N/2, N/3, N/4, N/5, N/6, N/8 → 分数 Unicode 符号（共14种，边界检查）
      -> → →, <- → ←, => → ⇒, <-> → ↔ — 箭头符号（边界检查）
      <= → ≤, >= → ≥, != → ≠, ~= → ≈, +- → ±, +/- → ± — 比较/数学符号
      HTML 注释 <!-- ... --> 体内所有 dash 受保护，不被转换
    """
    if not any(ch in text for ch in _PREPROCESS_TRIGGER_CHARS):
        return text
    text = _preprocess_dashes(text)
    text = _preprocess_arrows(text)
    text = _preprocess_math_symbols(text)
    text = _preprocess_html_entities(text)
    return text

