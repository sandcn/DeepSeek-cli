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

    ★ GFM 列数宽容：数据行的单元格数可与表头**不同**——少于表头时补齐空
    单元格、多于表头时忽略多余单元格（GFM 规范：「If a row has fewer cells
    than the header row, empty cells are inserted. If a row has more cells
    than the header row, the excess is ignored.」）。修复前超列行被判为
    非数据行 → 表格提前结束、该行退化为段落。归一化由
    ``_normalize_table_cells`` 完成（调用方负责）。

    Args:
        stripped: 去除首尾空白（无换行）的行文本。
        header_cols: 表头列数（仅用于调用方归一化，本判定不再据此拒绝）。

    Returns:
        是否为当前表格的数据行。
    """
    check = stripped.replace('\\|', '')
    if '|' not in check or _is_table_separator(stripped):
        return False
    cells = _parse_table_row(stripped)
    return bool(cells)


def _normalize_table_cells(cells: list[str], ncols: int | None) -> list[str]:
    """把数据行单元格归一化到表头列数（GFM：缺列补空、多列忽略）。

    ``ncols`` 为 ``None``（表头未知）时返回原单元格列表副本。
    """
    if ncols is None or ncols < 0:
        return list(cells)
    if len(cells) > ncols:
        return list(cells[:ncols])
    if len(cells) < ncols:
        return list(cells) + [''] * (ncols - len(cells))
    return list(cells)


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


# ── Pandoc 表格扩展（grid / multiline / simple） ────────────
#
# 除 GFM 管道表格外，Pandoc 还定义了三类「字符对齐」表格：**grid table**
# （``+---+`` 显式边框，单元格可多行）、**multiline table**（``---  ---`` 顶/
# 底边框 + 列段分隔）与 **simple table**（仅一条列段分隔行）。三者共用「按
# 列位置切分」的核心（列边界由边框/分隔行的 ``-`` 段或 ``+`` 位置给出）。


def _is_grid_table_border(stripped: str) -> bool:
    """网格表格边界行 ``+---+---+``（Pandoc grid table）。

    要求：以 ``+`` 起止、至少两个 ``+``、仅由 ``+-=:``（与空白）构成，
    且含 ``-`` 或 ``=`` 字符（``===`` 为表头分隔）。
    """
    s = stripped.strip()
    if len(s) < 3 or s[0] != '+' or s[-1] != '+':
        return False
    if s.count('+') < 2:
        return False
    for ch in s:
        if ch not in '+-=: ':
            return False
    return ('-' in s) or ('=' in s)


def _is_grid_table_row(stripped: str) -> bool:
    """网格表格数据行 ``| a | b |``（以 ``|`` 起止）。"""
    s = stripped.strip()
    return len(s) >= 2 and s.startswith('|') and s.endswith('|')


def _dash_spans(stripped: str) -> list[tuple[int, int]]:
    """列段分隔行 ``-----  ------`` 中各 ``-`` 段的字符区间（Pandoc simple/multiline）。"""
    s = stripped.strip()
    spans: list[tuple[int, int]] = []
    i = 0
    n = len(s)
    while i < n:
        if s[i] == '-':
            j = i
            while j < n and s[j] == '-':
                j += 1
            spans.append((i, j))
            i = j
        else:
            i += 1
    return spans


def _dash_layout(stripped: str) -> tuple[list[tuple[int, int]], list[str]]:
    """列段分隔行 → ``(列区间, 对齐)``；列区间包含对齐冒号。

    对齐冒号（``:-----`` / ``-----:`` / ``:----:``）位于 ``-`` 段两侧，占一
    个字符位置。内容行没有该冒号，若直接按 ``-`` 段区间切片会整体错位 1 列
    （左对齐列丢首字符 / 右对齐列丢尾字符）。此处把冒号并入列区间，供表格
    单元格切分与字段归属使用；``_dash_spans`` 仍保留纯 ``-`` 段（分隔线
    识别 / 段间距判断用）。
    """
    s = stripped.strip()
    spans: list[tuple[int, int]] = []
    aligns: list[str] = []
    for a, b in _dash_spans(s):
        left_colon = a > 0 and s[a - 1] == ':'
        right_colon = b < len(s) and s[b] == ':'
        a2 = a - 1 if left_colon else a
        b2 = b + 1 if right_colon else b
        spans.append((a2, b2))
        if left_colon and right_colon:
            aligns.append('center')
        elif right_colon:
            aligns.append('right')
        else:
            aligns.append('left')
    return spans, aligns


def _is_dashed_separator(stripped: str) -> bool:
    """Pandoc simple / multiline table 的列段分隔行 ``-----  ------``。

    要求：整行只含 ``-`` / 空格 / 对齐冒号、至少两个 ``-`` 段；相邻段之间
    通常至少 2 个空格，Pandoc 文档示例中允许 **1 个空格**（此时要求两侧
    ``-`` 段足够长（≥5），避免把 ``--- ---`` / ``- - -`` 这类分隔线误判为
    表格）。
    """
    s = stripped.strip()
    if not s or '-' not in s:
        return False
    for ch in s:
        if ch != '-' and ch != ' ' and ch != ':':
            return False
    spans = _dash_spans(s)
    if len(spans) < 2:
        return False
    for k in range(1, len(spans)):
        gap = spans[k][0] - spans[k - 1][1]
        if gap >= 2:
            continue
        # 仅 1 个空格：两侧段须足够长（Pandoc simple table 常见形态）
        prev_len = spans[k - 1][1] - spans[k - 1][0]
        cur_len = spans[k][1] - spans[k][0]
        if prev_len < 5 or cur_len < 5:
            return False
    return True


def _is_multiline_separator(stripped: str) -> bool:
    """Pandoc multiline table 的列段分隔行（允许段间 **1 个空格**）。

    与 ``_is_dashed_separator`` 的区别：multiline table 已由顶边框 + 表头行
    确认上下文，段间一个空格也是合法的列分隔（Pandoc 文档示例即如此）；
    仍要求每段至少 2 个 ``-``，避免 ``- - -`` 之类分隔线被误判。允许对齐
    冒号（``:-----`` / ``-----:`` / ``:----:``）。
    """
    s = stripped.strip()
    if not s or (s[0] != '-' and s[0] != ':') or '-' not in s:
        return False
    for ch in s:
        if ch != '-' and ch != ' ' and ch != ':':
            return False
    spans = _dash_spans(s)
    if len(spans) < 2:
        return False
    for a, b in spans:
        if b - a < 2:
            return False
    return True


def _is_multiline_border(stripped: str) -> bool:
    """Pandoc multiline table 的顶/底边框（单段长横线，≥10 个 ``-``）。

    阈值 10 避免把常见 ``---`` / ``--------`` 分隔线误判为表格候选；
    真正误判时解析层会回退为「分隔线 + 普通段落」，内容不丢。
    """
    s = stripped.strip()
    if len(s) < 10 or s[0] != '-' or '-' not in s:
        return False
    for ch in s:
        if ch != '-' and ch != ' ':
            return False
    return len(_dash_spans(s)) == 1


def parse_multiline_table(header_lines: list[str], sep_line: str,
                          body_lines: list[str]
                          ) -> tuple[list[list[str]], list[str]] | None:
    """Pandoc multiline table → ``(rows, alignments)``（无效返回 ``None``）。

    列边界由列段分隔行的 ``-`` 段给出；表头可多行（按列位置拼接），正文行
    以空行分组、组内多行拼接为同一单元格（Pandoc multiline table 语义）。
    分隔行的对齐冒号（``:-----`` / ``-----:`` / ``:----:``）并入列区间并
    产出对应 ``left/center/right`` 对齐。
    """
    spans, aligns = _dash_layout(sep_line)
    if len(spans) < 2:
        return None
    headers = [ln for ln in header_lines if ln.strip()]
    if not headers:
        return None

    #: Pandoc multiline/simple table 的列对齐基准：内容可能右对齐/居中，
    #: 最后一个字符可落入列段右侧的列间空格（甚至下一列段起点前 1 列）。
    #: 按「字段与列段的区间重叠」归属列，而不是机械切片，避免列首/列尾
    #: 字符被切掉（``Center`` → ``Cente``、``Example`` → ``Exampl``）。
    def _line_fields(line: str) -> list[tuple[int, int, str]]:
        """按 **2 个及以上连续空格** 切出字段（单空格保留在字段内）。

        Returns:
            ``(start, end, text)`` 列表（``text`` 已去首尾空白）。
        """
        fields: list[tuple[int, int, str]] = []
        n = len(line)
        i = 0
        while i < n:
            if line[i] == ' ':
                i += 1
                continue
            start = i
            j = i
            while j < n:
                if line[j] != ' ':
                    j += 1
                    continue
                # 连续空格 run：>=2 视为列分隔；单个空格属于字段内容
                k = j
                while k < n and line[k] == ' ':
                    k += 1
                if k - j >= 2 or k >= n:
                    break
                j = k
            text = line[start:j].strip()
            if text:
                fields.append((start, j, text))
            i = j
        return fields

    def _fields_by_column(line: str) -> list[list[str]]:
        """把一行字段按区间重叠归入各列（重叠最多者；并列取最近列中心）。"""
        cols: list[list[str]] = [[] for _ in spans]
        for start, end, text in _line_fields(line):
            best = 0
            best_score = -1
            best_center_dist = 1 << 30
            for idx, (a, b) in enumerate(spans):
                overlap = min(end, b) - max(start, a)
                if overlap < 0:
                    overlap = 0
                center_dist = abs((start + end) - (a + b))
                if (overlap > best_score
                        or (overlap == best_score
                            and center_dist < best_center_dist)):
                    best = idx
                    best_score = overlap
                    best_center_dist = center_dist
            cols[best].append(text)
        return cols

    ncols = len(spans)
    header_cells: list[str] = []
    header_cols = [_fields_by_column(ln) for ln in headers]
    for i in range(ncols):
        vals = [cols[i] for cols in header_cols if cols[i]]
        flat = [v for group in vals for v in group]
        header_cells.append(" ".join(flat))

    groups: list[list[str]] = []
    current: list[str] = []
    for raw in body_lines:
        if not str(raw).strip():
            if current:
                groups.append(current)
                current = []
            continue
        current.append(str(raw))
    if current:
        groups.append(current)
    rows: list[list[str]] = [header_cells]
    for group in groups:
        row: list[str] = []
        group_cols = [_fields_by_column(ln) for ln in group]
        for i in range(ncols):
            vals = [cols[i] for cols in group_cols if cols[i]]
            flat = [v for field_group in vals for v in field_group]
            row.append(" ".join(flat))
        rows.append(row)
    return rows, aligns


def parse_dashed_table(header_lines: list[str], sep_line: str,
                       body_lines: list[str]) -> tuple[list[list[str]], list[str]] | None:
    """Pandoc simple table → ``(rows, alignments)``（无效返回 ``None``）。

    列边界由分隔行的 ``-`` 段区间给出；表头行取 ``header_lines`` 的最后一行
    （simple table 表头为分隔行上方紧邻行），数据行取 ``body_lines``（连续
    非空行；空行由调用方截断）。对齐冒号并入列区间并产出对齐。
    """
    spans, aligns = _dash_layout(sep_line)
    if len(spans) < 2:
        return None
    header = [ln for ln in header_lines if ln.strip()]
    if not header:
        return None

    def _cells(row: str) -> list[str]:
        out: list[str] = []
        for a, b in spans:
            seg = row[a:b] if a < len(row) else ''
            out.append(seg.rstrip())
        return out

    header_cells = _cells(header[-1])
    data_rows = [ln for ln in body_lines if ln.strip()]
    rows = [header_cells] + [_cells(ln.rstrip()) for ln in data_rows]
    ncols = len(spans)
    norm: list[list[str]] = []
    for r in rows:
        norm.append((r + [''] * ncols)[:ncols])
    return norm, aligns


def _grid_border_positions(stripped: str) -> list[int]:
    """边界行中 ``+`` 的下标列表（列边界位置）。"""
    s = stripped.strip()
    return [i for i, ch in enumerate(s) if ch == '+']


def _grid_cells(row: str, positions: list[int]) -> list[str]:
    """按列边界位置从数据行切出单元格（两端 ``|`` 内内容 strip）。"""
    s = row.rstrip('\n').strip()
    if len(s) < positions[-1] + 1:
        s = s.ljust(positions[-1] + 1)
    cells: list[str] = []
    for i in range(len(positions) - 1):
        a = positions[i]
        b = positions[i + 1]
        seg = s[a + 1:b]
        cells.append(seg.strip())
    return cells


def _grid_alignments(lines: list[str], positions: list[int]) -> list[str]:
    """从边界行的 ``:`` 位置推断列对齐（Pandoc grid table）。

    列区间 ``[pos[i]+1, pos[i+1])`` 内：首字符 ``:`` → 左对齐标记、
    末字符 ``:`` → 右对齐标记；仅左/仅右/两者决定 ``left`` / ``right`` /
    ``center``（缺省 ``left``）。
    """
    aligns: list[str] = []
    for i in range(len(positions) - 1):
        left_colon = right_colon = False
        for raw in lines:
            if not _is_grid_table_border(raw):
                continue
            s = raw.strip()
            a = positions[i] + 1
            b = positions[i + 1]
            if b > len(s):
                b = len(s)
            seg = s[a:b].strip()
            if not seg:
                continue
            if seg.startswith(':'):
                left_colon = True
            if seg.endswith(':'):
                right_colon = True
        if left_colon and right_colon:
            aligns.append('center')
        elif right_colon:
            aligns.append('right')
        else:
            aligns.append('left')
    return aligns


def parse_grid_table(lines: list[str]) -> tuple[list[list[str]], list[str]] | None:
    """Pandoc grid table 行序列 → ``(rows, alignments)``（无效返回 ``None``）。

    ``rows[0]`` 为表头、其余为数据行；单元格可含多行（同一行块内多个 ``|``
    行以 ``\\n`` 连接）。列边界取最宽的 ``+`` 位置集合（``+===+`` 表头分隔
    行之后为数据区）。
    """
    if not lines:
        return None
    border_positions = [
        _grid_border_positions(ln) for ln in lines if _is_grid_table_border(ln)
    ]
    if len(border_positions) < 2:
        return None
    positions = max(border_positions, key=len)
    ncols = len(positions) - 1
    if ncols < 1:
        return None

    # 按边界行把 ``| ... |`` 行划分成「行块」（每块 = 一个逻辑行，多行为多行单元格）
    blocks: list[tuple[str, list[str]]] = []
    section = 'header'
    cur: list[str] = []
    seen_border = False
    for ln in lines:
        if _is_grid_table_border(ln):
            if cur:
                blocks.append((section, cur))
                cur = []
            if not seen_border:
                seen_border = True
                section = 'header'
            elif '=' in ln:
                section = 'body'
            continue
        if _is_grid_table_row(ln):
            cur.append(ln)
    if cur:
        blocks.append((section, cur))
    if not blocks:
        return None

    def _block_cells(block_lines: list[str]) -> list[str]:
        cells: list[str] | None = None
        for ln in block_lines:
            row = _grid_cells(ln, positions)
            if cells is None:
                cells = row
                continue
            for i in range(min(len(cells), len(row))):
                if row[i]:
                    cells[i] = (cells[i] + '\n' + row[i]) if cells[i] else row[i]
        return cells or [''] * ncols

    header_blocks = [b for s, b in blocks if s == 'header']
    body_blocks = [b for s, b in blocks if s == 'body']
    if not header_blocks and not body_blocks:
        return None
    if not body_blocks:
        # 无 ``+===+`` 表头分隔：首块作表头，其余作数据（与管道表格一致）
        body_blocks = header_blocks[1:]
        header_blocks = header_blocks[:1]
    if not header_blocks:
        return None
    rows = [_block_cells(header_blocks[0])]
    rows.extend(_block_cells(b) for b in header_blocks[1:])
    rows.extend(_block_cells(b) for b in body_blocks)
    norm: list[list[str]] = []
    for r in rows:
        norm.append((r + [''] * ncols)[:ncols])
    if not norm:
        return None
    aligns = _grid_alignments(lines, positions)
    return norm, (aligns + ['left'] * ncols)[:ncols]
