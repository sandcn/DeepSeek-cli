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
