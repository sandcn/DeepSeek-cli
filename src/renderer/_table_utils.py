"""_table_utils — 表格解析辅助函数（无正则表达式）。

从 recursive_parser.py 中提取的表格相关函数，独立模块化以降低模块复杂度。
"""

from __future__ import annotations

from ._block_helpers import _has_only_chars


# ── 安全性常量 ──────────────────────────────────────────

_SAFE_SENTINEL = '\uffffPIPE\uffff'


# ── 表格检测函数 ───────────────────────────────────────

def _is_table_row(stripped: str) -> bool:
    """判断是否为表格行（支持 GFM 无前导 pipe 的语法，如 `a|b`）。"""
    check = stripped.replace('\\|', '')
    if '|' not in check:
        return False
    if _is_table_separator(stripped):
        return False
    if check.startswith('|'):
        return check.count('|') >= 2
    # ★ GFM-style: 无前导 pipe 的表格行，如 "Name|Age|City"
    # 要求至少 2 个 pipe（≥3 列），单 pipe 太模糊容易误判（如 "a|b" 是普通文本）
    if check.count('|') >= 2:
        parts = [p.strip() for p in check.split('|')]
        return len(parts) >= 3
    return False


def _is_table_data_row(stripped: str, header_cols: int | None = None) -> bool:
    """表格已建立（TABLE_ACTIVE）时的数据行判定——比 ``_is_table_row`` 宽松。

    表头建立后数据行的歧义已消除：GFM 允许数据行不带前导/尾随 pipe，
    单个分隔 pipe 即构成两列（如 ``1 | 2``）。而 ``_is_table_row`` 对
    无前导 pipe 的行要求 ≥2 个 pipe（避免把 ``a|b`` 这类普通文本误判为
    独立表格）——该保护在表格已建立后不适用，否则表头与数据行判定标准
    不一致（表头用 ≥1 pipe、数据行用 ≥2 pipe），导致数据行被当作段落。

    Args:
        stripped: 去除首尾空白（无换行）的行文本。
        header_cols: 表头列数；给出时要求数据行列数不超过表头（兼容缺列）。

    Returns:
        是否为当前表格的数据行。
    """
    check = stripped.replace('\\|', '')
    if '|' not in check or _is_table_separator(stripped):
        return False
    cells = _parse_table_row(stripped)
    if not cells:
        return False
    if header_cols is not None and len(cells) > header_cols:
        return False
    return True


def _is_table_separator(stripped: str) -> bool:
    """判断是否为表格分隔行。"""
    if '|' not in stripped:
        return False
    parts = [p.strip() for p in stripped.split('|') if p.strip()]
    if len(parts) < 2:
        return False
    for p in parts:
        stripped_p = p.replace(':', '')
        if not stripped_p or not _has_only_chars(stripped_p, '-'):
            return False
    return True


def _parse_table_row(row_str: str) -> list[str]:
    """解析表格行为单元格列表。"""
    s = row_str.strip()
    if s.startswith('|'):
        s = s[1:]
    if s.endswith('|'):
        s = s[:-1]
    s = s.replace('\\|', _SAFE_SENTINEL)
    cells = [c.strip() for c in s.split('|')]
    cells = [c.replace(_SAFE_SENTINEL, '|') for c in cells]
    return cells


def _parse_table_alignments(sep_str: str) -> list[str]:
    """解析表格对齐方式。"""
    cells = _parse_table_row(sep_str)
    aligns = []
    for c in cells:
        c = c.strip()
        if c.startswith(':') and c.endswith(':'):
            aligns.append('center')
        elif c.endswith(':'):
            aligns.append('right')
        else:
            aligns.append('left')
    return aligns
