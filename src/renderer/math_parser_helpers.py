"""math_parser_helpers — MathParser 模块级辅助函数与常量。

包含 math_parser.py 中所有模块级辅助函数、工具函数、
以及需要从 math_symbols 导入的共享常量。

从 math_parser.py 拆分而来，供 math_parser.py 及各 Mixin 模块使用。
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .math_symbols import (
    _SUPERSCRIPT_MAP,
    _SUBSCRIPT_MAP,
    # 以下为 Mixin 模块通过 math_parser_helpers 间接导入的样式常量
    _STYLE_DEFAULT, _STYLE_FUNCTION, _STYLE_NUMBER, _STYLE_OPERATOR,
    _STYLE_SUPERSCRIPT, _STYLE_SUBSCRIPT, _STYLE_FRAC_LINE,
    _STYLE_TEXT, _STYLE_BOLD, _STYLE_ITALIC,
    _STYLE_CANCEL, _STYLE_TAG, _STYLE_BOXED,
    _STYLE_ACCENT, _STYLE_COLOR_NOTICE,
)

# Unicode 上下标字符集（用于积分美化验证）
_SUBSCRIPT_UNICHARS: set[str] = set(_SUBSCRIPT_MAP.values())
_SUPERSCRIPT_UNICHARS: set[str] = set(_SUPERSCRIPT_MAP.values())


# ═══════════════════════════════════════════════════════════
# 花括号组提取
# ═══════════════════════════════════════════════════════════

def _extract_braced_group(s: str, start: int) -> Tuple[str, int]:
    """从位置 start 开始提取匹配的 {...} 组内容。

    增加边界保护：深度超过 100 层时截断，防止恶意输入导致栈溢出。
    """
    if start >= len(s) or s[start] != '{':
        return '', start

    depth = 1
    i = start + 1
    n = len(s)
    max_depth = 100

    while i < n and depth > 0:
        c = s[i]
        if c == '{':
            depth += 1
            if depth > max_depth:
                raise ValueError("Nesting too deep")
        elif c == '}':
            depth -= 1
        i += 1

    # ★ 修复（review 方向）：未闭合（depth > 0）时 i 停在末尾且未消费
    #   任何 '}'——修复前统一 ``s[start+1:i-1]`` 多截掉最后一个字符
    #   （'{abc' 得 'ab' 而非 'abc'，'{a{b}' 丢失结尾 '}'）。
    if depth > 0:
        content = s[start + 1:i]
    else:
        content = s[start + 1:i - 1]
    return content, i


def _skip_group(s: str, start: int) -> Tuple[str, int]:
    """跳过一组 {...}，返回 (内容, 结束位置)。"""
    return _extract_braced_group(s, start)


def _skip_spaces(s: str, i: int, n: int) -> int:
    """跳过空白字符，返回新位置。"""
    while i < n and s[i] == ' ':
        i += 1
    return i


# ═══════════════════════════════════════════════════════════
# Unicode 上下标转换
# ═══════════════════════════════════════════════════════════

def _convert_to_superscript(text: str) -> str:
    """将普通文本转换为上标 Unicode 字符（映射不到的字符保留原样）。"""
    return ''.join(_SUPERSCRIPT_MAP.get(c, c) for c in text)


def _convert_to_subscript(text: str) -> str:
    """将普通文本转换为下标 Unicode 字符（映射不到的字符保留原样）。"""
    return ''.join(_SUBSCRIPT_MAP.get(c, c) for c in text)


# 历史命名别名：与 _convert_to_superscript/_convert_to_subscript 行为完全一致，
# 保留以兼容既有调用方（math_parser.py / math_parser_extra_commands.py）。
_convert_to_superscript_progressive = _convert_to_superscript
_convert_to_subscript_progressive = _convert_to_subscript


def _all_chars_mapped(text: str, mapping: Dict[str, str]) -> bool:
    """检查文本中的所有字符是否都有对应的 Unicode 映射。"""
    for c in text:
        if c not in mapping:
            return False
    return True


# ═══════════════════════════════════════════════════════════
# 运算符检测
# ═══════════════════════════════════════════════════════════

# 需要加括号的运算符字符集合
_OPEN_PAREN_OPS = frozenset('+-−=<>&|')


def _has_operator(text: str) -> bool:
    """检测文本中是否包含二元运算符（加减等号等），考虑分组。

    在 LaTeX 中只有 { } 是真正的分组符（用于界定参数），
    [ ] 是可选参数定界符，不作为分组深度计入。
    """
    depth = 0
    for ch in text:
        if ch == '{':
            depth += 1
        elif ch == '}':
            depth = max(0, depth - 1)
        elif depth == 0 and ch in _OPEN_PAREN_OPS:
            return True
    return False


# ═══════════════════════════════════════════════════════════
# TeX 原语查找
# ═══════════════════════════════════════════════════════════

def _find_tex_primitive(content: str, name: str) -> int | None:
    """在花括号组内容中查找 TeX 原语 \\over 或 \\choose。

    TeX 原语 \\over 和 \\choose 在 {组} 内作为分子/分母分隔符。
    此函数找出 \\over/\\choose 的位置，跳过已嵌套 {} 组内的。

    Returns:
        \\over/\\choose 前的反斜杠索引，未找到返回 None
    """
    i = 0
    n = len(content)
    depth = 0
    while i < n:
        c = content[i]
        if c == '{':
            depth += 1
            i += 1
        elif c == '}':
            depth = max(0, depth - 1)
            i += 1
        elif c == '\\' and depth == 0:
            # 检查是否为目标命令
            if i + 1 + len(name) < n and content[i + 1:i + 1 + len(name)] == name:
                # 确保是完整的命令名（后面跟非字母或结尾）
                end_pos = i + 1 + len(name)
                if end_pos >= n or not content[end_pos].isalpha():
                    return i  # 返回反斜杠位置
            i += 1
        else:
            i += 1
    return None


# ═══════════════════════════════════════════════════════════
# 矩阵行分割
# ═══════════════════════════════════════════════════════════

def re_split_rows(content: str) -> List[str]:
    """智能分割矩阵行，忽略行尾可选参数 \\\\[2pt]。

    支持的换行符：
      1. \\\\  — 标准 LaTeX 换行
      2. \\   — 非字母命令后跟空白/换行（常见于简写用法）
      3. \\[4pt] — 带间距参数的换行（[4pt] 被忽略）

    在 {} 分组内的反斜杠不会被误判为换行。
    """
    rows: List[str] = []
    depth = 0
    current: List[str] = []
    i = 0
    n = len(content)
    _NEWLINE_CHARS = {'\n', '\r', ' '}

    while i < n:
        c = content[i]

        # ── 花括号深度跟踪 ────────────────────────────
        if c == '{':
            depth += 1
            current.append(c)
            i += 1
            continue

        if c == '}':
            depth = max(0, depth - 1)
            current.append(c)
            i += 1
            continue

        # ── 仅在顶层 (depth == 0) 检测行分隔符 ──────────
        if depth == 0 and c == '\\':
            # 情况 1：\\\\（双反斜杠，标准 LaTeX 换行）
            if i + 1 < n and content[i + 1] == '\\':
                current_str = ''.join(current).strip()
                if current_str:
                    rows.append(current_str)
                current = []
                i += 2
                # 跳过空白
                while i < n and content[i] == ' ':
                    i += 1
                # 跳过可选参数 * 或 [10pt] 等
                if i < n and content[i] == '*':
                    i += 1
                if i < n and content[i] == '[':
                    close = content.find(']', i)
                    if close != -1:
                        i = close + 1
                continue

            # 情况 2：\ 后跟空白/换行符（单反斜杠隐式换行）
            if i + 1 < n and content[i + 1] in _NEWLINE_CHARS:
                current_str = ''.join(current).strip()
                if current_str:
                    rows.append(current_str)
                current = []
                i += 1  # 跳过反斜杠自身
                # 跳过后续空白/换行
                while i < n and content[i] in _NEWLINE_CHARS:
                    i += 1
                continue

        # ── 普通字符 ──────────────────────────────────
        current.append(c)
        i += 1

    # 剩余内容
    remaining = ''.join(current).strip()
    if remaining:
        rows.append(remaining)

    return rows if rows else [content]
