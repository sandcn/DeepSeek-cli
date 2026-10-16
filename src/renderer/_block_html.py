"""HTML 块级结构解析辅助（``<table>`` / ``<pre>`` / ``<dl>`` / ``<ul>`` → Token）。

从 ``src/renderer/_block_parser.py`` 抽出（该文件曾达 4500+ 行，HTML 扫描
辅助是其中自成一体的字符级扫描职责）。抽出后块级解析器只保留 Markdown
结构扫描，HTML 相关辅助集中在本模块，便于独立维护与测试。

本模块只做**字符级扫描与结构提取**，不产出 Token；样式与行内格式交由
调用方（块级解析器）与行内解析器处理。
"""

from __future__ import annotations

from ._html_attrs import align_of, language_of, parse_attrs, parse_open_tag


def _iter_html_tag_blocks(text: str, tag: str):
    """逐个产出 ``text`` 中 ``<tag ...>...</tag>`` 的内部文本（字符级扫描）。

    标签名按边界匹配（``<tr`` 不匹配 ``<track``）；自闭合写法产出空串；
    未闭合（无结束标签）时取其后的全部文本。
    """
    low = text.lower()
    open_pat = '<' + tag
    close_pat = '</' + tag + '>'
    i = 0
    n = len(text)
    while i < n:
        s = low.find(open_pat, i)
        if s < 0:
            return
        after = s + len(open_pat)
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = text.find('>', s)
        if gt < 0:
            return
        if text[gt - 1:gt] == '/':  # 自闭合
            i = gt + 1
            yield ''
            continue
        e = low.find(close_pat, gt)
        if e < 0:
            yield text[gt + 1:]
            return
        yield text[gt + 1:e]
        i = e + len(close_pat)


def _iter_html_cells(tr_text: str):
    """逐个产出 ``<tr>`` 内的 ``(单元格文本, 属性文本)``（保持文档顺序）。

    单元格文本为 ``<td>``/``<th>`` 的内部内容（未剥离行内标签，交由行内解析
    器处理）；属性文本为该单元格开标签的属性区（供列对齐等语义使用）。
    """
    low = tr_text.lower()
    i = 0
    n = len(tr_text)
    while i < n:
        s_td = low.find('<td', i)
        s_th = low.find('<th', i)
        cands = [x for x in (s_td, s_th) if x >= 0]
        if not cands:
            break
        s = min(cands)
        tag = 'td' if s == s_td else 'th'
        after = s + 3
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = tr_text.find('>', s)
        if gt < 0:
            break
        attrs = tr_text[s:gt + 1]
        close = low.find('</' + tag + '>', gt)
        if close < 0:
            yield tr_text[gt + 1:].strip(), attrs
            break
        yield tr_text[gt + 1:close].strip(), attrs
        i = close + len(tag) + 3


def _extract_html_cells(tr_text: str) -> list[str]:
    """提取 ``<tr>`` 内部的 ``<th>``/``<td>`` 单元格文本（保持文档顺序）。"""
    return [text for text, _attrs in _iter_html_cells(tr_text)]


def _extract_html_cell_aligns(tr_text: str) -> list[str]:
    """提取 ``<tr>`` 内每个单元格的列对齐（``align`` / ``style=text-align``）。

    返回与 ``_extract_html_cells`` 等长的列表，未知对齐为空串（渲染层按左对齐）。
    """
    aligns: list[str] = []
    for _text, attrs in _iter_html_cells(tr_text):
        try:
            aligns.append(align_of(parse_attrs(attrs)))
        except Exception:
            aligns.append("")
    return aligns


def _html_span_attr(attrs_text: str, name: str) -> int:
    """从单元格开标签文本取跨列/跨行数（``colspan`` / ``rowspan``）。

    缺失/非法时返回 ``1``；值钳制到 ``[1, 50]``（防御异常大值导致布局膨胀）。
    """
    try:
        raw = parse_attrs(attrs_text).get(name)
    except Exception:
        return 1
    if not raw:
        return 1
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return 1
    return max(1, min(50, value))


def _layout_html_table_rows(rows: list[list[str]],
                            row_attrs: list[list[str]]) -> tuple[list[list[str]], int]:
    """HTML 表格单元格布局（``colspan`` / ``rowspan`` 展开为规则矩形）。

    返回 ``(grid, ncols)``：``grid`` 每行长度恒为 ``ncols``——跨列单元格内容
    落在其首列、其余列为空串；被上方 ``rowspan`` 覆盖的位置填空串；行宽不足的
    行右侧补空。列数按各行展开后的最大行宽自适应（列数不符的行**不丢弃**）。
    """
    if not rows:
        return [], 0
    ncols = 0
    for attrs_list in row_attrs:
        total = sum(_html_span_attr(a, 'colspan') for a in attrs_list)
        if total > ncols:
            ncols = total
    if ncols <= 0:
        ncols = max((len(r) for r in rows), default=0)
    if ncols <= 0:
        return [], 0
    nrows = len(rows)
    grid = [[''] * ncols for _ in range(nrows)]
    occupied = [[False] * ncols for _ in range(nrows)]
    for r, cells in enumerate(rows):
        attrs_list = row_attrs[r] if r < len(row_attrs) else []
        col = 0
        for idx, cell in enumerate(cells):
            while col < ncols and occupied[r][col]:
                col += 1
            if col >= ncols:
                break
            attrs_text = attrs_list[idx] if idx < len(attrs_list) else ''
            colspan = _html_span_attr(attrs_text, 'colspan')
            rowspan = _html_span_attr(attrs_text, 'rowspan')
            grid[r][col] = cell
            for k in range(colspan):
                c2 = col + k
                if c2 >= ncols:
                    break
                occupied[r][c2] = True
                for rr in range(r + 1, min(r + rowspan, nrows)):
                    occupied[rr][c2] = True
            col += colspan
    return grid, ncols


def _html_pre_language(text: str) -> str:
    """从 ``<pre>`` / 内层 ``<code>`` 标签提取代码语言（无则空串）。

    支持 ``<pre class="language-python">``、``<code class="lang-py">``、
    ``<pre lang="js">`` 等写法（``_html_attrs.language_of`` 单一真源）。
    """
    low = text.lower()
    for tag in ('code', 'pre'):
        s = low.find('<' + tag)
        while s >= 0:
            after = s + 1 + len(tag)
            if after >= len(low) or not (low[after].isalnum()
                                         or low[after] in '-:'):
                gt = text.find('>', s)
                if gt < 0:
                    break
                try:
                    _t, attrs = parse_open_tag(text[s:gt + 1])
                except Exception:
                    attrs = {}
                lang = language_of(attrs)
                if lang:
                    return lang
            s = low.find('<' + tag, after)
    return ""


def _after_open_tag_text(line: str) -> str:
    """开标签后的剩余文本（``<img …> 说明`` → ``说明``；无则空串）。

    属性值中的 ``>``（罕见）按首个 ``>`` 截断——与解析器其它 HTML 扫描口径
    一致；空元素单行识别只关心标签后是否有可见文本。
    """
    s = (line or "").strip()
    if not s.startswith("<"):
        return ""
    gt = s.find(">")
    if gt < 0:
        return ""
    return s[gt + 1:].strip()


def _strip_inline_code_tags(text: str) -> str:
    """剥离 ``<pre>`` 内容中的行内 ``<code ...>`` / ``</code>`` 标签。

    仅删除 **code** 标签本身（语言由 class 提前提取），其余行内标签原样保留
    （由行内解析器处理），保证代码内容不被标签字面污染。
    """
    out: list[str] = []
    low = text.lower()
    i = 0
    n = len(text)
    while i < n:
        s = low.find("<code", i)
        c = low.find("</code>", i)
        cands = [x for x in (s, c) if x >= 0]
        if not cands:
            out.append(text[i:])
            break
        x = min(cands)
        out.append(text[i:x])
        if x == c:
            i = x + len("</code>")
            continue
        after = x + 5
        if after < n and (low[after].isalnum() or low[after] in "-:_"):
            out.append(text[x:after])
            i = after
            continue
        gt = text.find(">", x)
        if gt < 0:
            out.append(text[x:])
            break
        i = gt + 1
    return "".join(out)


def _html_int_attr(raw_tag_text: str, name: str) -> int | None:
    """从标签内文本提取整数属性（``<ol start="3">`` / ``<li value="5">``）。

    属性不存在或非整数时返回 ``None``（调用方保持默认编号）。
    """
    if not raw_tag_text or name not in raw_tag_text.lower():
        return None
    try:
        attrs = parse_attrs("<" + raw_tag_text + ">")
        value = attrs.get(name)
        if value is None or value == "":
            return None
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _parse_html_dl_items(text: str) -> list[tuple[str, str]]:
    """解析 ``<dl>`` 内容 → ``[(kind, text)]``（``kind`` 为 ``dt`` / ``dd``）。

    保持文档顺序；未闭合标签取其后的全部文本（内容不丢）。
    """
    items: list[tuple[str, str]] = []
    low = text.lower()
    i = 0
    n = len(text)
    while i < n:
        s_dt = low.find('<dt', i)
        s_dd = low.find('<dd', i)
        cands = [x for x in (s_dt, s_dd) if x >= 0]
        if not cands:
            break
        s = min(cands)
        kind = 'dt' if s == s_dt else 'dd'
        after = s + 3
        if after < n and (low[after].isalnum() or low[after] in '-:'):
            i = after
            continue
        gt = text.find('>', s)
        if gt < 0:
            break
        close = low.find('</' + kind + '>', gt)
        if close < 0:
            items.append((kind, text[gt + 1:].strip()))
            break
        items.append((kind, text[gt + 1:close].strip()))
        i = close + len(kind) + 3
    return items


def _extract_html_tag_text(text: str, tag: str) -> str | None:
    """提取 ``text`` 中 ``<tag>...</tag>`` 的文本（无则 None）。"""
    for block in _iter_html_tag_blocks(text, tag):
        return block.strip()
    return None


#: HTML 列表块内的结构标签（其余标签作为行内文本保留，交由行内解析器处理）
_HTML_LIST_STRUCT_TAGS: frozenset = frozenset({'ul', 'ol', 'li', 'dl', 'dt', 'dd'})


def _parse_html_list_items(text: str, root_kind: str = 'ul', start: int | None = None):
    """把 HTML 列表块（``<ul>`` / ``<ol>``）解析为列表项序列。

    维护标签栈跟踪嵌套层级（``<ul>``/``<ol>`` 入栈 → 决定该层项目符号类型
    与深度），``<li>`` 开启列表项（其文本收集到下一个结构标签之前）。
    行内标签（``<b>``/``<a href>``/``<br>`` 等）与实体原样保留在文本中，
    由行内解析器处理——不丢失行内格式。

    Args:
        text: 收集到的 HTML 行（含标签）。
        root_kind: 根列表类型（``ul`` / ``ol``）。
        start: ``<ol start="n">`` 的起始编号（``None`` 为 1）。

    Returns:
        ``(depth, ordered, number, text)`` 元组序列（``depth`` 1-based）。
    """
    items: list[tuple[int, bool, int, str]] = []
    stack: list[str] = []
    counters: list[int] = []
    cur_text: list[str] = []
    cur_depth = 1
    cur_ordered = False
    in_item = False

    def _flush():
        nonlocal cur_text, in_item
        if not in_item:
            return
        content = ' '.join(''.join(cur_text).split())
        number = counters[-1] if (counters and cur_ordered) else 1
        items.append((max(1, cur_depth), cur_ordered, number, content))
        cur_text = []
        in_item = False

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch != '<':
            j = text.find('<', i)
            if j < 0:
                j = n
            if in_item:
                cur_text.append(text[i:j])
            i = j
            continue
        gt = text.find('>', i)
        if gt < 0:
            if in_item:
                cur_text.append(text[i:])
            break
        raw = text[i + 1:gt]
        low = raw.lower().strip()
        i = gt + 1
        if low.startswith('/'):
            name = low[1:].split()[0] if low[1:].strip() else ''
            if name in ('ul', 'ol'):
                _flush()
                if stack:
                    stack.pop()
                if counters:
                    counters.pop()
                if stack:
                    cur_depth = len(stack)
                    cur_ordered = stack[-1] == 'ol'
            elif name == 'li':
                _flush()
            elif name in ('dl', 'dt', 'dd', 'p'):
                _flush()
            elif in_item:
                cur_text.append(text[i - len(raw) - 2:i])
            continue
        name = low.split()[0] if low else ''
        if name in ('ul', 'ol'):
            _flush()
            stack.append(name)
            counters.append(
                (start - 1) if (start is not None and name == root_kind
                                and len(stack) == 1) else 0)
            cur_depth = len(stack)
            cur_ordered = name == 'ol'
        elif name == 'li':
            _flush()
            if not stack and root_kind in ('ul', 'ol'):
                # 兜底：起始 ``<ul>``/``<ol>`` 行缺失（纯 ``<li>`` 片段输入）时
                # 按根列表类型建栈，保证深度与编号正确。
                stack.append(root_kind)
                counters.append(0)
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = bool(stack) and stack[-1] == 'ol'
            value = _html_int_attr(raw, 'value')
            if value is not None and counters:
                counters[-1] = value - 1
            if counters and cur_ordered:
                counters[-1] += 1
        elif name == 'dt':
            _flush()
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = False
        elif name == 'dd':
            _flush()
            in_item = True
            cur_depth = max(1, len(stack))
            cur_ordered = False
        elif in_item:
            # 非结构标签（行内 HTML）原样保留
            cur_text.append(text[i - len(raw) - 2:i])
    _flush()
    return items


__all__ = [
    "_iter_html_tag_blocks",
    "_iter_html_cells",
    "_extract_html_cells",
    "_extract_html_cell_aligns",
    "_html_span_attr",
    "_layout_html_table_rows",
    "_html_pre_language",
    "_after_open_tag_text",
    "_strip_inline_code_tags",
    "_html_int_attr",
    "_parse_html_dl_items",
    "_extract_html_tag_text",
    "_HTML_LIST_STRUCT_TAGS",
    "_parse_html_list_items",
]
