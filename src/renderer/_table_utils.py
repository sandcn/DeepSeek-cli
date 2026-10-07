"""_table_utils — 表格解析辅助函数（无正则表达式）。

从 recursive_parser.py 中提取的表格相关函数，独立模块化以降低模块复杂度。
"""

from __future__ import annotations

from ._block_helpers import _has_only_chars


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
    """判断是否为表格分隔行（支持单列表格 ``|---|`` 与 GFM 无边框 ``--- | ---``）。

    分隔行由若干 ``-``/``:`` 单元构成，单元之间以 ``|`` 分隔；首尾允许省略
    边框（``|`` 前后为空段）。单元格本身不得为空（``| --- | |`` 含空列）。
    """
    if '|' not in stripped:
        return False
    parts = [p.strip() for p in stripped.split('|')]
    # 去掉前导/尾随边框产生的空段（``|`` 作为行首/行尾）
    if parts and parts[0] == '':
        parts = parts[1:]
    if parts and parts[-1] == '':
        parts = parts[:-1]
    if not parts:
        return False
    for p in parts:
        core = p.replace(':', '')
        if not core or not _has_only_chars(core, '-'):
            return False
    return True


def _split_table_cells(s: str) -> list[str]:
    """按分隔 pipe 切分表格行单元格（跳过转义与行内代码 span 内的 ``|``）。

    GFM 允许单元格内容通过反斜杠转义 ``\\|`` 或行内代码 ``` `a|b` ``` 携带
    字面管道；朴素 ``split('|')`` 会把它们当作列分隔，导致列数错乱、表格
    结构被撕裂（如 ``| `x|y` | 2 |`` 被切成 3 列）。此处按字符扫描：转义
    管道直接并入缓冲；进入/退出反引号代码 span（按反引号连续个数配对，
    与 ``_InlineParser._try_inline_code`` 同一规则）期间管道不作为分隔符。
    """
    cells: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(s)
    code_delim = 0
    while i < n:
        ch = s[i]
        if ch == '\\' and i + 1 < n and s[i + 1] == '|':
            buf.append('|')
            i += 2
            continue
        if ch == '`':
            j = i
            while j < n and s[j] == '`':
                j += 1
            run = j - i
            if code_delim == 0:
                code_delim = run
            elif run == code_delim:
                code_delim = 0
            buf.append(s[i:j])
            i = j
            continue
        if ch == '|' and code_delim == 0:
            cells.append(''.join(buf).strip())
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    cells.append(''.join(buf).strip())
    return cells


def _parse_table_row(row_str: str) -> list[str]:
    """解析表格行为单元格列表（转义/行内代码内的 ``|`` 不参与切分）。"""
    s = row_str.strip()
    cells = _split_table_cells(s)
    # 去掉行首/行尾边框产生的空段（``|`` 作为行首/行尾）
    if cells and cells[0] == '':
        cells.pop(0)
    if cells and cells[-1] == '':
        cells.pop()
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
