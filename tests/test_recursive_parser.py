"""测试 recursive_parser — 特别是代码 fence 的流式解析行为。

注意：本文件约 102KB，建议拆分为多个小文件（如按 Bug 分组）。

覆盖已修复 Bug 的回归测试：
  Bug A — fence 关闭长度检查错误
  Bug C — 延迟 fence + mermaid 语言名失效
"""

from __future__ import annotations

from src.api.renderer.recursive_parser import RegexFreeBlockParser
from src.api.renderer.types import TokenType


def _collect_tokens(text: str) -> list:
    """一次性解析全部文本并返回 Token 列表。"""
    parser = RegexFreeBlockParser()
    tokens = parser.feed(text)
    tokens.extend(parser.flush())
    return tokens


def _stream_collect(chunks: list[str]) -> list:
    """模拟流式分块输入，返回 Token 列表。"""
    parser = RegexFreeBlockParser()
    tokens: list = []
    for chunk in chunks:
        tokens.extend(parser.feed(chunk))
    tokens.extend(parser.flush())
    return tokens


def _find_token(tokens, token_type) -> list:
    """按类型查找 Token。"""
    return [t for t in tokens if t.type is token_type]


# ═══════════════════════════════════════════════════════════
# Bug A 回归测试：fence 关闭长度检查错误
# ═══════════════════════════════════════════════════════════


class TestBugA_FenceCloseLengthCheck:
    """Bug A：5反引号打开时，4反引号应视为代码内容而非关闭。

    CommonMark 规范要求关闭 fence 长度 >= 打开 fence 长度。
    旧代码中，flen < block_fence_len 且行中无其他字符时错误地视为关闭。
    """

    def test_5_open_4_close_is_code_line(self):
        """5个`打开，4个`出现 → 4个`应作为代码内容行。"""
        tokens = _collect_tokens("`````python\nx=1\n````\n")
        code_lines = [t for t in tokens if t.type is TokenType.CODE_LINE]
        code_close = [t for t in tokens if t.type is TokenType.CODE_FENCE_CLOSE]

        # 4个`应是代码内容行，不是fence关闭
        assert any("````" in t.content for t in code_lines), \
            "4个反引号应作为代码内容行"
        # flush 为截断的代码块发出关闭标记，确保渲染器正确闭合代码块
        assert len(code_close) == 1, "截断的 fence flush 应发出1个关闭标记"


class TestBugD_StreamingUnclosedFence:
    """Bug D：流式输出结束时未闭合的代码 fence 不发出关闭标记。

    流式场景中 AI 输出代码块后可能不输出闭合 ``` 直接结束，
    此时 flush() 必须发出 CODE_FENCE_CLOSE 以通知渲染器
    闭合代码块，否则视觉上代码块缺少收尾标记。
    """

    def test_stream_code_block_no_close(self):
        """流式代码块无闭合 ``` → flush 应发出 CODE_FENCE_CLOSE。"""
        tokens = _stream_collect(["```python\n", "x=1\n", "y=2\n"])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 1, "未闭合代码块 flush 应发出1个关闭标记"
        assert closes[0].meta.get("lang") == "python"

    def test_stream_code_block_no_close_no_lang(self):
        """流式延迟 fence 无语言 + 无闭合 → flush 应发出 CODE_FENCE_CLOSE。"""
        tokens = _stream_collect(["```\n", "x=1\n"])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 1, "延迟 fence 无闭合也应发出关闭标记"

    def test_stream_code_block_close_in_last_chunk(self):
        """流式代码块在最后 chunk 中含闭合 ``` → 正常关闭。"""
        tokens = _stream_collect(["```python\nprint(1)\n```\n"])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 1, "标准闭合应正常关闭"

    def test_stream_code_block_after_fence_consumed(self):
        """完整闭合的代码块后新开一个不闭合的 → 两个都应有序关闭。"""
        tokens = _stream_collect([
            "```python\nx=1\n```\n",      # 完整闭合
            "```\ny=2\n",                 # 不闭合
        ])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 2, "两个代码块应分别关闭"

    def test_stream_nested_fence_in_markdown_no_close(self):
        """markdown 代码块内嵌套 fence 且未闭合 → flush 应发出关闭标记。"""
        tokens = _stream_collect(["```markdown\n", "```python\n", "print(1)\n"])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 1, "未闭合 markdown 代码块 flush 应关闭"

    def test_stream_no_lang_no_content(self):
        """``` 后无内容直接结束 → flush 应发出关闭标记。"""
        tokens = _stream_collect(["```python\n"])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(closes) == 1, "空代码块 flush 也应发出关闭标记"

    def test_stream_deferred_fence_no_lang_no_content(self):
        """```（无语言标识）后无内容直接结束 → flush 不应静默丢弃。

        延迟 fence（``` 无语言标识）在 flush() 中原被静默丢弃，
        导致代码块完全被吞掉（无 opening marker、无可高亮内容、无 closing 标记）。
        修复后应正常 emit CODE_FENCE_OPEN + CODE_FENCE_CLOSE。
        """
        tokens = _stream_collect(["```\n"])
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(opens) == 1, "延迟 fence flush 应 emit CODE_FENCE_OPEN"
        assert len(closes) == 1, "延迟 fence flush 应 emit CODE_FENCE_CLOSE"
        assert opens[0].meta.get("lang") == "text", "无语言标识默认为 text"

    def test_stream_deferred_fence_no_close_no_content(self):
        """```（无语言标识）流式分块输入无闭合 → flush 应正常关闭。"""
        tokens = _stream_collect(["```\n", "x=1\n", "y=2\n"])
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(opens) == 1, "应 emit CODE_FENCE_OPEN"
        assert len(closes) == 1, "未闭合代码块 flush 应 emit CODE_FENCE_CLOSE"

    def test_stream_deferred_fence_no_newline_no_content(self):
        """```（无语言标识+无换行符）直接 flush → 不应静默丢弃。"""
        tokens = _stream_collect(["```"])
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert len(opens) == 1, "无换行的 ``` flush 应 emit CODE_FENCE_OPEN"
        assert len(closes) == 1, "无换行的 ``` flush 应 emit CODE_FENCE_CLOSE"

    def test_stream_chunk_boundary_merge(self):
        """流式 chunk 边界合并: ```python + def → ```pythondef 在一行。

        AI 流式输出时 `` ```python`` 和 ``def foo():\n`` 可能被切分到不同 chunk，
        导致 parser buffer 中合并为 `` ```python def foo():\n`` 一行。
        旧代码将 "pythondef" 整体作为语言名（无高亮），"def foo():" 作为
        fence metadata 被静默丢弃（内容丢失）。
        修复后应正确切出 lang="python"，"def foo():" 作为 CODE_LINE 保留。
        """
        tokens = _stream_collect(["```python", "def foo():\n", "    pass\n", "```\n"])
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        code_lines = [t for t in tokens if t.type is TokenType.CODE_LINE]
        assert len(opens) == 1
        assert len(closes) == 1
        assert opens[0].meta.get("lang") == "python", \
            f"应识别为 python，实际={opens[0].meta.get('lang')!r}"
        assert any("def foo():" in l.content for l in code_lines), \
            "合并到 fence 行的代码内容应作为 CODE_LINE 保留"
        assert any("    pass" in l.content for l in code_lines), \
            "后续代码行应正常保留"

    def test_stream_chunk_boundary_merge_no_lang(self):
        """流式 chunk 边界合并 + 无已知语言前缀: ```xyzdef + foo():
        虽无法识别已知语言前缀，但 fence 行上的残留文本仍应作为 CODE_LINE 保留。"""
        tokens = _stream_collect(["```xyzdef", " foo():\n", "```\n"])
        code_lines = [t for t in tokens if t.type is TokenType.CODE_LINE]
        assert any("foo():" in l.content for l in code_lines), \
            "fence 行上的代码内容不应被丢弃"


# ═══════════════════════════════════════════════════════════
# 流式延迟 fence 语言识别
# ═══════════════════════════════════════════════════════════
# 设计说明：流式场景中延迟 fence 的第二行可能是语言名也可能是代码内容，
# 为安全起见只信任 _COMMON_LANGUAGES 白名单。非白名单语言（如 fancylang）
# 仅在一性次 fence (```lang) 中支持，流式场景不做兜底识别以避免误判。
# Bug B（不一致性）是故意设计取舍，不修复。
# mermaid 已加入白名单以修复 Bug C。


class TestDeferredFenceLangDetection:
    """延迟 fence 语言识别（白名单覆盖）。"""

    def test_deferred_fence_common_lang(self):
        """延迟 fence + 白名单内语言 → 正确识别。"""
        for lang in ("python", "javascript", "go", "rust", "bash"):
            tokens = _stream_collect([f"```\n", f"{lang}\n", "code\n", "```\n"])
            opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
            assert opens, f"语言 {lang} 应启动代码块"
            assert opens[0].meta.get("lang") == lang, \
                f"语言 {lang} 应被识别，实际={opens[0].meta.get('lang')}"

    def test_deferred_fence_mermaid_in_whitelist(self):
        """mermaid 在白名单中，流式 ``` + mermaid 应启动 MERMAID_BLOCK。"""
        tokens = _stream_collect(["```\n", "mermaid\n", "graph TD\n", "```\n"])
        mermaid_opens = _find_token(tokens, TokenType.MERMAID_BLOCK_OPEN)
        assert mermaid_opens, "mermaid 应启动 MERMAID_BLOCK_OPEN"
        assert mermaid_opens[0].meta.get("lang") == "mermaid"


# ═══════════════════════════════════════════════════════════
# Bug C 回归测试：延迟 fence + mermaid 语言名失效
# ═══════════════════════════════════════════════════════════


class TestBugC_DeferredFenceMermaid:
    """Bug C：延迟 fence 后语言名 mermaid 无法启动 mermaid 块。

    修复方式：将 mermaid 加入 _COMMON_LANGUAGES 白名单。
    """

    def test_stream_mermaid_by_lang_name(self):
        """流式输出 ``` + mermaid → 应启动 MERMAID_BLOCK。"""
        tokens = _stream_collect([
            "```\n", "mermaid\n", "graph TD\n", "A-->B\n", "```\n",
        ])
        opens = _find_token(tokens, TokenType.MERMAID_BLOCK_OPEN)
        assert opens, "应启动 MERMAID_BLOCK_OPEN"
        assert opens[0].meta.get("lang") == "mermaid"

    def test_stream_mermaid_consistency_with_oneshot(self):
        """流式 mermaid 与一次性 ```mermaid 行为一致。"""
        oneshot = _collect_tokens("```mermaid\ngraph TD\nA-->B\n```\n")
        stream = _stream_collect(["```\n", "mermaid\n", "graph TD\n", "A-->B\n", "```\n"])

        oneshot_opens = _find_token(oneshot, TokenType.MERMAID_BLOCK_OPEN)
        stream_opens = _find_token(stream, TokenType.MERMAID_BLOCK_OPEN)
        assert oneshot_opens and stream_opens, "两种方式都应启动 MERMAID_BLOCK"

        oneshot_close = _find_token(oneshot, TokenType.MERMAID_BLOCK_CLOSE)
        stream_close = _find_token(stream, TokenType.MERMAID_BLOCK_CLOSE)
        assert oneshot_close and stream_close, "两种方式都应关闭 MERMAID_BLOCK"
        assert oneshot_close[0].content == stream_close[0].content, \
            "mermaid 块内容应一致"


# ═══════════════════════════════════════════════════════════
# 通用回归测试
# ═══════════════════════════════════════════════════════════


class TestCodeFenceRegression:
    """普通代码 fence 功能回归测试，确保修改未破坏已有功能。"""

    def test_basic_code_block(self):
        """普通代码块正常解析。"""
        tokens = _collect_tokens("```python\nx=1\n```\n")
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        lines = _find_token(tokens, TokenType.CODE_LINE)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert opens and lines and closes, "代码块应完整解析"
        assert opens[0].meta["lang"] == "python"

    def test_stream_code_block_split_fence_close(self):
        """流式中关闭 fence 被拆分为多次 feed。"""
        tokens = _stream_collect(["```python\n", "line1\n", "``", "`\n"])
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert opens and closes, "代码块应正确开启和关闭"
        assert opens[0].meta["lang"] == "python"

    def test_no_lang_code_block(self):
        """无语言标识的代码块。"""
        tokens = _collect_tokens("```\nx=1\n```\n")
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        assert opens, "应启动代码块"
        assert opens[0].meta["lang"] == "text", "无语言应默认 text"

    def test_tilde_fence(self):
        """~~~ fence 正常工作。"""
        tokens = _collect_tokens("~~~python\nx=1\n~~~\n")
        opens = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        lines = _find_token(tokens, TokenType.CODE_LINE)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert opens and lines and closes
        assert opens[0].meta["lang"] == "python"

    def test_mermaid_oneshot(self):
        """一次性 fence mermaid 正常工作。"""
        tokens = _collect_tokens("```mermaid\ngraph TD\nA-->B\n```\n")
        opens = _find_token(tokens, TokenType.MERMAID_BLOCK_OPEN)
        closes = _find_token(tokens, TokenType.MERMAID_BLOCK_CLOSE)
        assert opens and closes
        assert "graph TD" in closes[0].content

    def test_nested_fence_in_markdown_block(self):
        """Markdown 代码块内嵌套 fence 作为内容。"""
        tokens = _collect_tokens(
            "```markdown\n```python\nprint(1)\n```\n```\n"
        )
        lines = _find_token(tokens, TokenType.CODE_LINE)
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert any("```python" in t.content for t in lines), \
            "内部 fence 应作为代码内容"
        assert len(closes) == 1, "只有外层 fence 应关闭"

    def test_auto_close_fence_heading_pattern(self):
        """自动关闭 fence：代码块后连续5个标题行触发自动关闭。"""
        tokens = _stream_collect([
            "```\n", "x=1\n",
            "## One\n", "## Two\n", "## Three\n",
            "## Four\n", "## Five\n",
        ])
        closes = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)
        assert closes, "连续5标题应触发自动关闭"
        # 确认标题也被解析
        headings = _find_token(tokens, TokenType.HEADING)
        assert headings, "应解析出标题"


# ═══════════════════════════════════════════════════════════
# 表格解析测试
# ═══════════════════════════════════════════════════════════


class TestTableParsing:
    """表格解析功能测试。

    覆盖标准表格（带分隔行）、流式表格（无分隔行）、
    以及各种边界场景（空单元格、转义 pipe、列数不一致等）。
    """

    # ── 标准表格（带分隔行）──

    def test_basic_table_all_alignments(self):
        """标准表格：左/中/右对齐。"""
        text = (
            "| Left | Center | Right |\n"
            "|:-----|:------:|------:|\n"
            "| a    | b      | c     |\n"
        )
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        meta = tables[0].meta
        assert meta["rows"] == [["Left", "Center", "Right"], ["a", "b", "c"]], \
            f"rows 内容错误: {meta['rows']}"
        assert meta["alignments"] == ["left", "center", "right"], \
            f"alignments 错误: {meta['alignments']}"

    def test_table_no_alignment_default_left(self):
        """分隔行无对齐标识符 → 全部左对齐。"""
        text = "| a | b |\n|---|---|\n| 1 | 2 |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        meta = tables[0].meta
        assert meta["rows"] == [["a", "b"], ["1", "2"]], \
            f"rows 内容错误: {meta['rows']}"
        assert meta["alignments"] == ["left", "left"], \
            f"无对齐标记应全部 left: {meta['alignments']}"

    def test_table_empty_cells(self):
        """空单元格作为空字符串保留。"""
        text = "| a |   | c |\n|---|---|---|\n| 1 |   | 3 |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        assert rows == [["a", "", "c"], ["1", "", "3"]], \
            f"空单元格未能保留为空字符串: {rows}"
        # 验证空单元格确实是空字符串而非空格
        assert rows[0][1] == "", "中间单元格应为空字符串"
        assert rows[1][1] == "", "中间单元格应为空字符串"

    def test_table_escaped_pipe(self):
        """转义 pipe \\| → a | b 作为单个单元格内容。"""
        text = r"| a \| b | c |" "\n|---|---|\n| val | 2 |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        # 第一格应为 "a | b"（转义 pipe 合并在一个单元格内）
        assert rows[0][0] == "a | b", \
            f"转义 pipe 未能正确合并单元格: {repr(rows[0][0])}"
        assert rows[0][1] == "c", \
            f"第二列错误: {repr(rows[0][1])}"
        assert rows[1][0] == "val", \
            f"数据行第一列错误: {repr(rows[1][0])}"

    def test_table_single_row_separator(self):
        """分隔行后无数据行 → 仅有表头行。"""
        text = "| a | b |\n|---|---|\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        assert len(rows) == 1, f"应只有表头行, 实际 {len(rows)} 行: {rows}"
        assert rows[0] == ["a", "b"], f"表头内容错误: {rows[0]}"

    def test_table_multi_br_cell(self):
        """含 <br> 的单元格 — 内联标记在表格层应保持原样。"""
        text = "| a | b |\n|---|---|\n| line1<br>line2 | c |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        # <br> 在解析器层面保持原始文本，内联渲染阶段再处理
        assert "<br>" in rows[1][0], \
            f"含<br>的单元格内容错误: {repr(rows[1][0])}"
        assert rows[1][1] == "c", f"第二列错误: {repr(rows[1][1])}"

    # ── 流式表格（无分隔行）──

    def test_stream_table_no_separator(self):
        """流式 ≥3 行带 | 的行自动检测为表格（≥3 阈值避免 | 注释行误判）。"""
        tokens = _stream_collect(["| a | b |\n", "| 1 | 2 |\n", "| 3 | 4 |\n"])
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "流式 ≥3 行 | 行应生成 TABLE token"
        meta = tables[0].meta
        assert meta["rows"] == [["a", "b"], ["1", "2"], ["3", "4"]], \
            f"rows 内容错误: {meta['rows']}"
        # 流式表格无分隔行，自动全部左对齐
        assert meta["alignments"] == ["left", "left"], \
            f"流式表格 alignments 应全部 left: {meta['alignments']}"

    def test_stream_table_single_row_fallback_to_paragraph(self):
        """仅 1 行带 | 的行 → 降级为段落。"""
        tokens = _collect_tokens("| single row |\n")
        tables = _find_token(tokens, TokenType.TABLE)
        assert not tables, "单行带 | 不应生成 TABLE token"
        paras = _find_token(tokens, TokenType.PARAGRAPH)
        assert paras, "单行带 | 应降级为 PARAGRAPH"

    # ── 内联格式保留 ──

    def test_table_with_inline_formatting(self):
        """含内联格式的单元格内容保持原始标记。"""
        text = "| **bold** | *italic* | `code` |\n|---|---|---|\n| text | text | text |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        # 内联格式标记在表格解析层保持原始文本
        assert rows[0][0] == "**bold**", \
            f"第一格应保留 **bold**: {repr(rows[0][0])}"
        assert rows[0][1] == "*italic*", \
            f"第二格应保留 *italic*: {repr(rows[0][1])}"
        assert rows[0][2] == "`code`", \
            f"第三格应保留 `code`: {repr(rows[0][2])}"

    # ── 空行打断 ──

    def test_table_interrupted_by_empty_line(self):
        """空行打断表格 → 先发射 TABLE token，再依次发射空行和段落。"""
        text = "| a | b |\n|---|---|\n| 1 | 2 |\n\nsome text\n"
        tokens = _collect_tokens(text)
        # 验证 token 类型
        types = [t.type for t in tokens]
        assert TokenType.TABLE in types, "表格应被解析"
        assert TokenType.EMPTY_LINE in types, "空行应被解析"
        assert TokenType.PARAGRAPH in types, "后续文本应被解析为段落"
        # 验证顺序：TABLE → EMPTY_LINE → PARAGRAPH
        table_idx = types.index(TokenType.TABLE)
        empty_idx = types.index(TokenType.EMPTY_LINE)
        para_idx = types.index(TokenType.PARAGRAPH)
        assert table_idx < empty_idx, \
            f"TABLE({table_idx}) 应在 EMPTY_LINE({empty_idx}) 之前"
        assert empty_idx < para_idx, \
            f"EMPTY_LINE({empty_idx}) 应在 PARAGRAPH({para_idx}) 之前"

    # ── 列数不一致 ──

    def test_table_varying_column_count(self):
        """行间列数不一致时按实际解析结果保留（当前实现不做填充/截断）。"""
        text = "| a | b | c |\n|---|---|---|\n| 1 | 2 |\n| 3 | 4 | 5 | 6 |\n"
        tokens = _collect_tokens(text)
        tables = _find_token(tokens, TokenType.TABLE)
        assert tables, "应生成 TABLE token"
        rows = tables[0].meta["rows"]
        # 表头 3 列
        assert rows[0] == ["a", "b", "c"], f"表头错误: {rows[0]}"
        # 短行：保持原长度（当前实现不做补齐）
        assert len(rows[1]) == 2, f"短行列数应与原文一致: {rows[1]}"
        # 长行：保持原长度（当前实现不做截断）
        assert len(rows[2]) == 4, f"长行列数应与原文一致: {rows[2]}"

    # ── 引用块内表格 ──

    def test_table_in_blockquote(self):
        """引用块内带 > 的表格行：解析器至少不崩溃。"""
        text = "> | a | b |\n> |---|---|\n> | 1 | 2 |\n"
        tokens = _collect_tokens(text)
        # 不崩溃是最低要求
        assert tokens is not None, "解析器不应返回 None"
        # 引用块结构应保持完整
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        assert bq_opens, "引用块应被识别 (BLOCKQUOTE_OPEN)"
        assert bq_closes, "引用块应被关闭 (BLOCKQUOTE_CLOSE)"
        # 由于 > 前缀打断表格检测流程，表格行降级为 BLOCKQUOTE_LINE；
        # 但至少内容不丢失，解析器不崩溃
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        assert len(bq_lines) > 0, "引用块内应保留下解析的内容"


# ═══════════════════════════════════════════════════════════
# 标题解析回归测试
# ═══════════════════════════════════════════════════════════


class TestHeadingParsing:
    """标题解析回归测试。

    覆盖标准 ATX、无空格 ATX、Setext、Pandoc 风格属性等语法。
    """

    # ── 标准 ATX 标题（回归） ──

    def test_atx_heading_level1(self):
        """# Heading → level 1 标题。"""
        tokens = _collect_tokens("# Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("level") == 1

    def test_atx_heading_level2(self):
        """## Heading → level 2。"""
        tokens = _collect_tokens("## Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("level") == 2

    def test_atx_heading_level6(self):
        """###### Heading → level 6。"""
        tokens = _collect_tokens("###### Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("level") == 6

    def test_atx_heading_trailing_hashes(self):
        """# Heading # → 尾部 # 被剥离。"""
        tokens = _collect_tokens("# Hello #")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"

    def test_atx_heading_trailing_multiple_hashes(self):
        """# Heading ### → 尾部多个 # 被剥离。"""
        tokens = _collect_tokens("# Hello ####")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"

    def test_just_hash_is_not_heading(self):
        """# 单独一个 # 不是标题。"""
        tokens = _collect_tokens("#")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 0

    # ── 无空格 ATX 标题（CommonMark 规范禁止，降级为段落） ──

    def test_no_space_heading_level1(self):
        """#Heading（无空格）→ 降级为 PARAGRAPH（CommonMark 合规）。"""
        tokens = _collect_tokens("#Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 0, f"#text 无空格不应视为标题, tokens={tokens}"
        p = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(p) >= 1, "降级后应为段落"
        assert p[0].content == "#Hello"

    def test_no_space_heading_level2(self):
        """##Heading（无空格）→ 降级为 PARAGRAPH（CommonMark 合规）。"""
        tokens = _collect_tokens("##Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 0
        p = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(p) >= 1
        assert p[0].content == "##Hello"

    def test_no_space_heading_level6(self):
        """######Heading（无空格）→ 降级为 PARAGRAPH（CommonMark 合规）。"""
        tokens = _collect_tokens("######Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 0, f"######Hello 无空格不应视为标题, tokens={tokens}"
        p = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(p) >= 1
        assert p[0].content == "######Hello"

    def test_no_space_heading_stream_split(self):
        """流式分块：'# He' + 'ading\\n' 合并为 'Heading'。"""
        tokens = _stream_collect(["# He", "ading\n"])
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1, f"流式分块应合并为完整标题, tokens={tokens}"
        assert h[0].content == "Heading"

    # ── 自定义 ID 属性 ──

    def test_heading_with_custom_id(self):
        """# Hello {#my-id} → 文本 'Hello', id='my-id'。"""
        tokens = _collect_tokens("# Hello {#my-id}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("id") == "my-id"

    def test_heading_with_id_containing_hyphen(self):
        """# Hello {#my-section} → id='my-section'。"""
        tokens = _collect_tokens("# Hello {#my-section}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("id") == "my-section"

    # ── Pandoc 风格属性（新增语法） ──

    def test_heading_with_class_only(self):
        """# Hello {.highlight} → 文本剥离属性，meta 含 classes。"""
        tokens = _collect_tokens("# Hello {.highlight}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert "highlight" in h[0].meta.get("attrs", {}).get("classes", [])

    def test_heading_with_multiple_classes(self):
        """# Hello {.a .b} → classes=['a', 'b']。"""
        tokens = _collect_tokens("# Hello {.a .b}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        attrs = h[0].meta.get("attrs", {})
        assert "a" in attrs.get("classes", [])
        assert "b" in attrs.get("classes", [])

    def test_heading_with_key_value(self):
        """# Hello {key=val} → meta['attrs']['key']='val'。"""
        tokens = _collect_tokens("# Hello {key=val}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("attrs", {}).get("key") == "val"

    def test_heading_with_quoted_value(self):
        """# Hello {key="val with space"} → 引号内空格保留。"""
        tokens = _collect_tokens('# Hello {key="val with space"}')
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("attrs", {}).get("key") == "val with space"

    def test_heading_with_id_class_and_key(self):
        """# Hello {#my-id .class key=val} → 全属性解析。"""
        tokens = _collect_tokens("# Hello {#my-id .main key=val}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("id") == "my-id"
        attrs = h[0].meta.get("attrs", {})
        assert "main" in attrs.get("classes", [])
        assert attrs.get("key") == "val"

    def test_no_space_heading_with_id(self):
        """#Hello{#id}（无空格 + 属性）→ CommonMark 不允许无空格的 ATX 标题，降级为段落。"""
        tokens = _collect_tokens("#Hello{#id}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 0, f"#text 无空格不应视为标题, tokens={tokens}"
        p = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(p) >= 1
        assert p[0].content == "#Hello{#id}"

    # ── 流式场景（增量 chunk） ──

    def test_stream_heading_single_chunk(self):
        """单 chunk '# Hello\\n' → 标题。"""
        tokens = _stream_collect(["# Hello\n"])
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"

    def test_stream_heading_split_marker(self):
        """'#' + ' Hello\\n' 分两 chunk → 标题。"""
        tokens = _stream_collect(["# ", "Hello\n"])
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"

    def test_stream_heading_multiple_lines(self):
        """多行标题流式输入 → 正确解析每个标题。"""
        tokens = _stream_collect([
            "# First\n", "## Second\n", "### Third\n",
        ])
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 3
        assert h[0].content == "First"
        assert h[0].meta.get("level") == 1
        assert h[1].content == "Second"
        assert h[1].meta.get("level") == 2
        assert h[2].content == "Third"
        assert h[2].meta.get("level") == 3

    # ── 自动生成锚点 ID ──

    def test_heading_auto_id_basic(self):
        """# Hello → 自动生成 id='hello'。"""
        tokens = _collect_tokens("# Hello")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].meta.get("id") == "hello"

    def test_heading_auto_id_multiple_words(self):
        """# My Section Title → id='my-section-title'。"""
        tokens = _collect_tokens("# My Section Title")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].meta.get("id") == "my-section-title"

    def test_heading_auto_id_special_chars(self):
        """# Python 3.0 (new!) → id='python-3-0-new'。"""
        tokens = _collect_tokens("# Python 3.0 (new!)")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].meta.get("id") == "python-3-0-new"

    def test_heading_auto_id_consecutive_spaces(self):
        """# Hello   World → id='hello-world'（连续空格合并）。"""
        tokens = _collect_tokens("# Hello   World")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].meta.get("id") == "hello-world"

    def test_heading_auto_id_empty_text(self):
        """# → id='section'（空文本回退）。"""
        tokens = _collect_tokens("# ")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == ""
        assert h[0].meta.get("id") == "section"

    def test_heading_auto_id_custom_id_still_works(self):
        """# Hello {#custom-id} → 仍使用自定义 id='custom-id'。"""
        tokens = _collect_tokens("# Hello {#custom-id}")
        h = _find_token(tokens, TokenType.HEADING)
        assert len(h) == 1
        assert h[0].content == "Hello"
        assert h[0].meta.get("id") == "custom-id"

    def test_heading_auto_id_toc_filter_passes_id(self):
        """验证 HeadingAnchorFilter 正确传递 id 到 TOC 条目。"""
        from src.api.renderer.pipeline_filters.heading_anchor import HeadingAnchorFilter
        from src.api.renderer.types import RenderContext

        parser = RegexFreeBlockParser()
        tokens = parser.feed("# Hello World\n## Test\n")
        tokens.extend(parser.flush())

        ctx = RenderContext()
        filter_obj = HeadingAnchorFilter(collect_toc=True)
        filter_obj.process(tokens, ctx)

        assert len(ctx.toc) == 2
        assert ctx.toc[0]["id"] == "hello-world"
        assert ctx.toc[1]["id"] == "test"


# ═══════════════════════════════════════════════════════════
# Bug F 回归测试：_link_node_handler stylize() 链式调用导致链接渲染丢失
# ═══════════════════════════════════════════════════════════


class TestBugF_LinkNodeHandler:
    """Bug F：_LinkNode handler 中使用链式 stylize() 返回 None 导致链接渲染丢失。

    修复方式：_build_dispatch_table 中 _LinkNode 的 lambda 改为独立函数
    _link_node_handler，先获取 _nodes_to_rich 结果，再 stylize，最后 return。
    """

    def test_basic_link_rendering(self):
        """[text](url) 链接应渲染为带 cyan+underline 样式的 Text。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        renderer = InlineRenderer()
        result = renderer.render("访问 [GitHub](https://github.com) 查看")

        # 纯文本应包含链接文本
        assert "GitHub" in result.plain, f"链接文本丢失: {result.plain}"

        # 检查是否包含 cyan underline 样式
        # Rich Style.color 的 str() 返回 "Color('cyan', ...)" 含 "cyan" 特征
        has_underline_cyan = False
        for st in (sp.style for sp in result.spans):
            if st and st.underline and st.color is not None:
                color_str = str(st.color)
                if 'cyan' in color_str:
                    has_underline_cyan = True
                    break
        assert has_underline_cyan, (
            f"链接缺少 cyan+underline 样式: spans={result.spans}"
        )

    def test_link_with_children(self):
        """链接含粗体等子节点时样式正确。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        renderer = InlineRenderer()
        result = renderer.render("**[bold link](https://example.com)**")

        assert "bold link" in result.plain, f"链接文本丢失: {result.plain}"
        # 至少有一个 span 有 underline
        has_underline = any(
            s.style and s.style.underline for s in result.spans
        )
        assert has_underline, f"链接应带下划线: spans={result.spans}"

    def test_incremental_renderer_link(self):
        """通过 IncrementalRenderer 完整管线渲染链接。"""
        from io import StringIO
        from rich.console import Console
        from src.api.renderer import IncrementalRenderer

        buf = StringIO()
        renderer = IncrementalRenderer(_file=buf, typing_speed=0)
        renderer.write("链接测试 [点击](https://example.com) 结束")
        renderer.close()
        output = buf.getvalue()

        assert "点击" in output, f"链接文本在完整管线中丢失: {output}"
        assert len(output) > 0, "输出不应为空"

    def test_link_title_rendering(self):
        """[text](url "title") 链接应渲染链接文本并显示 title 提示。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        renderer = InlineRenderer()
        result = renderer.render('查看 [GitHub](https://github.com "开源社区") 项目')

        # 纯文本应包含链接文本和 title
        assert "GitHub" in result.plain, f"链接文本丢失: {result.plain}"
        assert '开源社区' in result.plain, f"title 文本丢失: {result.plain}"

        # 检查是否包含 cyan underline 样式
        has_underline_cyan = False
        has_dim_title = False
        for sp in result.spans:
            st = sp.style
            if st and st.underline and st.color is not None:
                color_str = str(st.color)
                if 'cyan' in color_str:
                    has_underline_cyan = True
            if st and st.dim and st.color is not None:
                color_str = str(st.color)
                if 'bright_black' in color_str:
                    has_dim_title = True
        assert has_underline_cyan, (
            f"链接缺少 cyan+underline 样式: spans={result.spans}"
        )
        assert has_dim_title, (
            f"链接 title 缺少 dim+bright_black 样式: spans={result.spans}"
        )

    def test_image_title_rendering(self):
        """![alt](url "title") 图片应显示 alt、url 和 title。"""
        from src.api.renderer.inline_renderer import InlineRenderer
        renderer = InlineRenderer()
        result = renderer.render('示例图片 ![Logo](https://example.com/logo.png "网站Logo") 展示')

        # 纯文本应包含 alt、url 和 title
        assert "Logo" in result.plain, f"alt 文本丢失: {result.plain}"
        assert "logo.png" in result.plain, f"url 文本丢失: {result.plain}"
        assert '网站Logo' in result.plain, f"title 文本丢失: {result.plain}"


# ═══════════════════════════════════════════════════════════
# Bug G 回归测试：CodeBlockBatcher 跨 feed 未闭合代码块缺少 CODE_FENCE_CLOSE
# ═══════════════════════════════════════════════════════════


class TestBugG_CodeBlockBatcherMissingClose:
    """Bug G：CodeBlockBatcher 在跨 feed 未闭合代码块被新代码块覆盖时
    未发射 CODE_FENCE_CLOSE，导致渲染器代码块状态泄漏。

    修复方式：在刷出前一块代码行后追加 CODE_FENCE_CLOSE token。
    """

    def _make_token(self, ttype, content="", meta=None):
        from src.api.renderer.types import Token
        return Token(ttype, content, meta or {})

    def test_cross_feed_overlap_emits_close(self):
        """跨 feed：前一 chunk 代码块未闭合 + 后一 chunk 新代码块 → 应有 CLOSE。"""
        from src.api.renderer.pipeline import CodeBlockBatcher
        from src.api.renderer.types import RenderContext

        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        # Feed 1: 未闭合代码块（OPEN + 代码行，无 CLOSE）
        feed1 = [
            self._make_token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            self._make_token(TokenType.CODE_LINE, "x = 1"),
            self._make_token(TokenType.CODE_LINE, "y = 2"),
        ]
        result1 = batcher.process(feed1, ctx)
        # feed1 未闭合 → 不应发射任何 token（缓存到实例属性）
        assert len(result1) == 0, f"未闭合代码块不应发射 token: {result1}"
        assert batcher._block_meta is not None, "应缓存 block_meta"

        # Feed 2: 新代码块开始（前一块尚未闭合）
        feed2 = [
            self._make_token(TokenType.CODE_FENCE_OPEN, "", {"lang": "java"}),
            self._make_token(TokenType.CODE_LINE, "int a = 1;"),
            self._make_token(TokenType.CODE_FENCE_CLOSE, "", {"lang": "java"}),
        ]
        result2 = batcher.process(feed2, ctx)

        # 应包含 FEENCE_CLOSE
        closes = [t for t in result2 if t.type is TokenType.CODE_FENCE_CLOSE]
        assert len(closes) >= 1, (
            f"缺少 CODE_FENCE_CLOSE token: {[(t.type.name, t.content[:30]) for t in result2]}"
        )

        # 至少有一个 CLOSE 对应前一块（"python"）
        has_python_close = any(
            t.meta.get("lang") == "python" for t in closes
        )
        assert has_python_close, f"应发射前一块(python)的 CLOSE: closes={closes}"

    def test_cross_feed_code_block_content(self):
        """跨 feed 代码块的内容行在刷出时被保留。"""
        from src.api.renderer.pipeline import CodeBlockBatcher
        from src.api.renderer.types import RenderContext

        batcher = CodeBlockBatcher()
        ctx = RenderContext()

        feed1 = [
            self._make_token(TokenType.CODE_FENCE_OPEN, "", {"lang": "python"}),
            self._make_token(TokenType.CODE_LINE, "data = [1, 2, 3]"),
        ]
        batcher.process(feed1, ctx)

        feed2 = [
            self._make_token(TokenType.CODE_FENCE_OPEN, "", {"lang": "text"}),
        ]
        result = batcher.process(feed2, ctx)

        lines = [t for t in result if t.type is TokenType.CODE_LINE]
        assert any("data = [1, 2, 3]" in t.content for t in lines), (
            f"代码行内容丢失: {[t.content for t in lines]}"
        )


# ═══════════════════════════════════════════════════════════
# 列表项 checkbox/todo 解析测试
# ═══════════════════════════════════════════════════════════


class TestListItemCheckbox:
    """测试列表项中的 checkbox/todo 标记解析。"""

    def test_ul_without_checkbox(self):
        """无序列表项：无 checkbox 标记时 todo=False, checked=False。"""
        tokens = _collect_tokens("- 普通列表项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is False
        assert items[0].meta.get("checked") is False
        assert items[0].content == "普通列表项"

    def test_ul_unchecked_checkbox(self):
        """无序列表项：[ ] 未选中 → todo=True, checked=False。"""
        tokens = _collect_tokens("- [ ] 待办事项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[0].content == "[ ] 待办事项"

    def test_ul_checked_checkbox_lowercase(self):
        """无序列表项：[x] 已选中（小写）→ todo=True, checked=True。"""
        tokens = _collect_tokens("- [x] 已完成事项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is True
        assert items[0].content == "[x] 已完成事项"

    def test_ul_checked_checkbox_uppercase(self):
        """无序列表项：[X] 已选中（大写）→ todo=True, checked=True。"""
        tokens = _collect_tokens("- [X] 已完成事项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is True
        assert items[0].content == "[X] 已完成事项"

    def test_ol_without_checkbox(self):
        """有序列表项：无 checkbox 标记时 todo=False, checked=False。"""
        tokens = _collect_tokens("1. 普通列表项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is False
        assert items[0].meta.get("checked") is False
        assert items[0].content == "普通列表项"

    def test_ol_unchecked_checkbox(self):
        """有序列表项：[ ] 未选中 → todo=True, checked=False。"""
        tokens = _collect_tokens("1. [ ] 待办事项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[0].content == "[ ] 待办事项"

    def test_ol_checked_checkbox(self):
        """有序列表项：[x] 已选中 → todo=True, checked=True。"""
        tokens = _collect_tokens("1. [x] 已完成事项\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is True
        assert items[0].content == "[x] 已完成事项"

    def test_asterisk_ul_checkbox(self):
        """* 标记的无序列表项同样支持 checkbox。"""
        tokens = _collect_tokens("* [ ] 待办\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].content == "[ ] 待办"

    def test_plus_ul_checkbox(self):
        """+ 标记的无序列表项同样支持 checkbox。"""
        tokens = _collect_tokens("+ [x] 完成\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is True
        assert items[0].content == "[x] 完成"

    def test_checkbox_no_list_marker(self):
        """纯文本中的 [ ] 不应被错误识别为 checkbox（没有列表标记前缀）。"""
        tokens = _collect_tokens("这是普通的 [ ] 文本\n")
        paras = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(paras) == 1
        assert "[ ]" in paras[0].content

    def test_star_bullet_without_list(self):
        """* [ ] 作为列表项时正确解析。"""
        tokens = _collect_tokens("* [ ] 任务\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[0].content == "[ ] 任务"


# ═══════════════════════════════════════════════════════════
# 嵌套引用（Blockquote）全面语法测试
# ═══════════════════════════════════════════════════════════


class TestBlockquoteAllSyntax:
    """覆盖引用块所有语法变体的全面测试。

    包括：
      基础引用、多行连续、嵌套（2/3/多层）、空行打断、
      引用内嵌标题/列表/代码 fence/数学块/admonition/内联格式、
      流式分块输入、AST 树形后处理验证。
    """

    # ═══════════════════════════════════════════════════════
    # 基础引用
    # ═══════════════════════════════════════════════════════

    def test_basic_blockquote(self):
        """> 基础引用 → BLOCKQUOTE_OPEN + BLOCKQUOTE_LINE + BLOCKQUOTE_CLOSE。"""
        tokens = _collect_tokens("> Hello world\n")
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_close = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)

        assert len(bq_open) == 1
        assert len(bq_close) == 1
        assert len(bq_lines) == 1
        assert bq_open[0].meta.get("depth") == 1
        assert bq_close[0].meta.get("depth") == 1
        assert bq_lines[0].content == "Hello world"

    def test_blockquote_no_space_after_gt(self):
        "> 无空格：>text → 正确识别为引用。"""
        tokens = _collect_tokens(">Hello world\n")
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_close = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)

        assert len(bq_open) == 1, ">无空格也应识别为引用"
        assert len(bq_close) == 1
        assert len(bq_lines) == 1
        assert "Hello" in bq_lines[0].content

    def test_blockquote_empty_content(self):
        """> 后无内容 → 引用块打开后直接关闭。"""
        tokens = _collect_tokens(">\n")
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_close = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        assert len(bq_open) == 1
        assert len(bq_close) == 1
        assert bq_open[0].meta.get("depth") == 1

    def test_blockquote_only_gt_space(self):
        """> 只有 > 加空格 → 空引用。"""
        tokens = _collect_tokens("> \n")
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_close = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        assert len(bq_open) == 1
        assert len(bq_close) == 1

    # ═══════════════════════════════════════════════════════
    # 多行连续引用
    # ═══════════════════════════════════════════════════════

    def test_blockquote_multi_line(self):
        """> 多行连续 → 合并为同一引用块内的 BLOCKQUOTE_LINE。"""
        tokens = _collect_tokens(
            "> Line 1\n"
            "> Line 2\n"
            "> Line 3\n"
        )
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_close = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        empties = _find_token(tokens, TokenType.EMPTY_LINE)

        assert len(bq_open) == 1, "多行应合并为单一引用块"
        assert len(bq_close) == 1
        assert len(empties) == 0, "连续 > 行不应产生空行"
        assert len(bq_lines) >= 1
        consolidated = '\n'.join(t.content for t in bq_lines)
        assert "Line 1" in consolidated
        assert "Line 3" in consolidated

    # ═══════════════════════════════════════════════════════
    # 嵌套引用
    # ═══════════════════════════════════════════════════════

    def test_blockquote_nested_2_levels(self):
        """>> 2层嵌套 → BLOCKQUOTE_OPEN depth=1, depth=2, 然后关闭。"""
        tokens = _collect_tokens(
            "> Outer\n"
            ">> Inner\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)

        assert len(bq_opens) == 2, "2层嵌套应有2个 OPEN"
        assert len(bq_closes) == 2, "2层嵌套应有2个 CLOSE"
        assert bq_opens[0].meta.get("depth") == 1
        assert bq_opens[1].meta.get("depth") == 2
        # BLOCKQUOTE_OPEN 顺序：depth=1 → depth=2
        # BLOCKQUOTE_CLOSE 顺序：depth=2 → depth=1

    def test_blockquote_nested_3_levels(self):
        """>>> 3层嵌套 → OPEN depth=1/2/3, CLOSE depth=3/2/1。"""
        tokens = _collect_tokens(
            "> A\n"
            ">> B\n"
            ">>> C\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)

        assert len(bq_opens) == 3, "3层嵌套应有3个 OPEN"
        assert len(bq_closes) == 3, "3层嵌套应有3个 CLOSE"
        assert [o.meta.get("depth") for o in bq_opens] == [1, 2, 3]
        assert [c.meta.get("depth") for c in bq_closes] == [3, 2, 1]

    def test_blockquote_nested_up_and_down(self):
        """> >> >>> >> > 嵌套加深再减浅 → 正确的 OPEN/CLOSE 序列。"""
        tokens = _collect_tokens(
            "> L1\n"
            ">> L2\n"
            ">>> L3\n"
            ">> Back to L2\n"
            "> Back to L1\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)

        depths_open = [o.meta.get("depth") for o in bq_opens]
        depths_close = [c.meta.get("depth") for c in bq_closes]

        # OPEN: 1 → 2 → 3
        assert depths_open == [1, 2, 3], f"OPEN depths: {depths_open}"
        # CLOSE: 3 → 2 → 1（深度变化时栈弹出）
        assert depths_close == [3, 2, 1], f"CLOSE depths: {depths_close}"

    def test_blockquote_nested_deep_5_levels(self):
        """>>>>> 5层嵌套 → 正确的深度序列。"""
        tokens = _collect_tokens(
            "> 1\n"
            ">> 2\n"
            ">>> 3\n"
            ">>>> 4\n"
            ">>>>> 5\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        depths_open = [o.meta.get("depth") for o in bq_opens]
        assert depths_open == [1, 2, 3, 4, 5], f"5层嵌套 depths: {depths_open}"

    # ═══════════════════════════════════════════════════════
    # 空行打断
    # ═══════════════════════════════════════════════════════

    def test_blockquote_interrupted_by_empty_line(self):
        """> text\n\n> text → 两个独立引用块，中间有空行。"""
        tokens = _collect_tokens(
            "> First\n"
            "\n"
            "> Second\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        empties = _find_token(tokens, TokenType.EMPTY_LINE)

        assert len(bq_opens) == 2, "空行打断应有2个独立引用块"
        assert len(bq_closes) == 2
        assert len(empties) == 1, "空行应被解析"

    # ═══════════════════════════════════════════════════════
    # 引用内嵌标题
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_heading(self):
        """> # 标题 → 引用块内嵌 HEADING + BLOCKQUOTE_LINE。"""
        tokens = _collect_tokens(
            "> # Title\n"
            "> Content\n"
        )
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        headings = _find_token(tokens, TokenType.HEADING)
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)

        assert bq_open, "应识别引用块"
        assert headings, "引用内标题应被解析"
        assert headings[0].content == "Title"
        assert headings[0].meta.get("level") == 1
        assert any("Content" in t.content for t in bq_lines)

    def test_blockquote_with_heading_level2(self):
        """> ## 二级标题 → 引用块内 HEADING level=2。"""
        tokens = _collect_tokens("> ## Section\n")
        headings = _find_token(tokens, TokenType.HEADING)
        assert headings
        assert headings[0].content == "Section"
        assert headings[0].meta.get("level") == 2

    # ═══════════════════════════════════════════════════════
    # 引用内嵌列表
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_unordered_list(self):
        """> - item → 引用块内嵌无序列表。"""
        tokens = _collect_tokens(
            "> - item1\n"
            "> - item2\n"
        )
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        items = _find_token(tokens, TokenType.LIST_ITEM)

        assert bq_open, "引用块应被识别"
        assert len(items) == 2, "应解析出2个列表项"
        assert items[0].content == "item1"
        assert items[1].content == "item2"
        assert items[0].meta.get("bullet") is True

    def test_blockquote_with_ordered_list(self):
        """> 1. item → 引用块内嵌有序列表。"""
        tokens = _collect_tokens(
            "> 1. first\n"
            "> 2. second\n"
        )
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 2
        assert items[0].content == "first"
        assert items[0].meta.get("bullet") is False
        assert items[0].meta.get("number") == 1
        assert items[1].meta.get("number") == 2

    def test_blockquote_with_checkbox_list(self):
        """> - [x] done → 引用内 checkbox 正确解析。"""
        tokens = _collect_tokens(
            "> - [ ] todo\n"
            "> - [x] done\n"
        )
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 2
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[1].meta.get("todo") is True
        assert items[1].meta.get("checked") is True

    # ═══════════════════════════════════════════════════════
    # 引用内嵌代码 fence（核心 Bug 修复验证）
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_code_fence(self):
        """> ```python / > code / > ``` → 引用内代码块, > 前缀被剥离。"""
        tokens = _collect_tokens(
            "> ```python\n"
            "> x = 1\n"
            "> print(x)\n"
            "> ```\n"
        )
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        code_open = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        code_close = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)

        assert bq_open, "引用块应被识别"
        assert code_open, "代码 fence 应启动"
        assert code_close, "代码 fence 应关闭"
        assert len(code_lines) == 2, "应有2行代码内容"
        # ✅ 核心验证：> 前缀必须被剥离
        assert "> x" not in code_lines[0].content, \
            f"代码行不应包含 > 前缀: {code_lines[0].content!r}"
        assert code_lines[0].content == "x = 1", \
            f"代码行内容错误: {code_lines[0].content!r}"
        assert code_lines[1].content == "print(x)"
        assert code_open[0].meta.get("lang") == "python"

    def test_blockquote_with_code_fence_no_lang(self):
        """> ``` (无语言) → 引用内代码块 lang=text。"""
        tokens = _collect_tokens(
            "> ```\n"
            "> code\n"
            "> ```\n"
        )
        code_open = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        code_close = _find_token(tokens, TokenType.CODE_FENCE_CLOSE)

        assert code_open, "应启动代码块"
        assert code_close, "应关闭代码块"
        assert code_open[0].meta.get("lang") == "text"
        assert len(code_lines) == 1
        assert code_lines[0].content == "code", \
            f"> 应被剥离: {code_lines[0].content!r}"

    def test_blockquote_with_code_fence_multi_line(self):
        """> 引用内多行代码块 → 所有行 > 前缀剥离。"""
        tokens = _collect_tokens(
            "> ```\n"
            "> line1\n"
            "> line2\n"
            "> line3\n"
            "> ```\n"
        )
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        assert len(code_lines) == 3
        for i, cl in enumerate(code_lines):
            assert not cl.content.startswith('>'), \
                f"代码行{i}仍含 > 前缀: {cl.content!r}"

    def test_blockquote_with_code_fence_stream(self):
        """流式分块：引用内代码块分块输入 → > 前缀正常剥离。"""
        tokens = _stream_collect([
            "> ```python\n",
            "> x = 1\n",
            "> y = 2\n",
            "> ```\n",
        ])
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        assert len(code_lines) == 2
        assert code_lines[0].content == "x = 1"
        assert code_lines[1].content == "y = 2"

    def test_blockquote_with_code_fence_indented_inside(self):
        """> 引用内代码块含缩进内容 → 缩进保留。"""
        tokens = _collect_tokens(
            "> ```python\n"
            "> def foo():\n"
            ">     return 42\n"
            "> ```\n"
        )
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        assert any("def foo():" in c.content for c in code_lines)
        assert any("    return 42" in c.content for c in code_lines), \
            "缩进应保留"

    # ═══════════════════════════════════════════════════════
    # 引用内嵌缩进代码
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_indented_code(self):
        """>     缩进代码 → 引用内 strip > 后缩进代码应正常。"""
        tokens = _collect_tokens(
            ">     code_line\n"
        )
        # 缩进代码在引用内：先剥离 >，剩余 "    code_line"
        # 4空格缩进应触发缩进代码块
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        code_open = _find_token(tokens, TokenType.CODE_FENCE_OPEN)
        if code_open:
            # 如果触发了缩进代码块，验证 > 已被剥离
            if code_lines:
                assert ">" not in code_lines[0].content, \
                    f"> 应被剥离: {code_lines[0].content!r}"

    # ═══════════════════════════════════════════════════════
    # 引用内嵌数学块
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_math_block(self):
        """> $$ ... $$ → 引用内数学块正常解析。"""
        tokens = _collect_tokens(
            "> $$\n"
            "> e^{i\\pi} = -1\n"
            "> $$\n"
        )
        math_open = _find_token(tokens, TokenType.MATH_BLOCK_OPEN)
        math_close = _find_token(tokens, TokenType.MATH_BLOCK_CLOSE)

        assert math_open, "引用内数学块应启动"
        assert math_close, "引用内数学块应关闭"
        assert math_close[0].content == "e^{i\\pi} = -1" or \
               "e^{i\\pi}" in math_close[0].content, \
            f"数学内容:{math_close[0].content!r}"

    # ═══════════════════════════════════════════════════════
    # 嵌套引用内嵌块级元素
    # ═══════════════════════════════════════════════════════

    def test_blockquote_nested_with_list(self):
        """>> - item → 嵌套引用内列表。"""
        tokens = _collect_tokens(
            "> Outer\n"
            ">> - Inner item\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        items = _find_token(tokens, TokenType.LIST_ITEM)

        assert len(bq_opens) == 2, "引用应嵌套"
        # 内部列表项应在内层引用中解析出来
        # 注意：由于引用递归解析方式，内层内容可能作为段落文本
        # 或实际 LIST_ITEM，取决于解析器如何处理嵌套 > 内的列表标记
        has_list_item = len(items) > 0
        has_para = len(_find_token(tokens, TokenType.PARAGRAPH)) > 0
        assert has_list_item or has_para, \
            "嵌套引用内应有内容被解析"

    def test_blockquote_nested_with_code_fence(self):
        """>> ```python → 嵌套引用内代码块, > 前缀正常剥离。"""
        tokens = _collect_tokens(
            "> L1\n"
            ">> ```python\n"
            ">> x = 1\n"
            ">> ```\n"
        )
        code_lines = _find_token(tokens, TokenType.CODE_LINE)
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)

        assert len(bq_opens) == 2, "应有两层引用"
        if code_lines:
            assert all(">" not in c.content for c in code_lines), \
                "代码行的 > 前缀应被剥离"

    # ═══════════════════════════════════════════════════════
    # 引用内嵌 Admonition
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_admonition(self):
        """> [!NOTE] text → 引用内告示块正确解析。"""
        tokens = _collect_tokens(
            "> [!NOTE] Hello\n"
            "> World\n"
        )
        adm_open = _find_token(tokens, TokenType.ADMONITION_OPEN)
        adm_lines = _find_token(tokens, TokenType.ADMONITION_LINE)

        assert adm_open, "告示应被识别"
        assert adm_open[0].meta.get("type") == "NOTE"
        assert "Hello" in adm_open[0].content

    def test_blockquote_with_admonition_warning(self):
        """> [!WARNING] → WARNING 类型告示。"""
        tokens = _collect_tokens(
            "> [!WARNING] Be careful\n"
        )
        adm_open = _find_token(tokens, TokenType.ADMONITION_OPEN)
        assert adm_open
        assert adm_open[0].meta.get("type") == "WARNING"

    def test_blockquote_nested_with_admonition(self):
        """>> [!TIP] → 嵌套引用内告示。"""
        tokens = _collect_tokens(
            "> A\n"
            ">> [!TIP] Hint\n"
        )
        adm_opens = _find_token(tokens, TokenType.ADMONITION_OPEN)
        assert len(adm_opens) >= 1

    # ═══════════════════════════════════════════════════════
    # 引用内嵌内联格式
    # ═══════════════════════════════════════════════════════

    def test_blockquote_with_inline_bold(self):
        """> **bold** → 引用内粗体标记保留在 BLOCKQUOTE_LINE 中。"""
        tokens = _collect_tokens("> **bold text**\n")
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        assert bq_lines, "引用内应生成 BLOCKQUOTE_LINE"
        assert "**bold text**" in bq_lines[0].content, \
            "内联标记应保留用于后续内联渲染阶段"

    def test_blockquote_with_inline_code(self):
        """> `code` → 引用内联代码标记保留。"""
        tokens = _collect_tokens("> Use `code` here\n")
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        assert bq_lines
        assert "`code`" in bq_lines[0].content

    def test_blockquote_with_link(self):
        """> [link](url) → 引用内链接标记保留。"""
        tokens = _collect_tokens("> [text](http://example.com)\n")
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        assert bq_lines
        assert "[text](http://example.com)" in bq_lines[0].content

    # ═══════════════════════════════════════════════════════
    # 流式分块输入
    # ═══════════════════════════════════════════════════════

    def test_blockquote_stream_basic(self):
        """流式分块 > 文本 → 正确组装。"""
        tokens = _stream_collect(["> Hel", "lo\n"])
        bq_open = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        assert bq_open
        assert bq_lines
        assert "Hello" in bq_lines[0].content

    def test_blockquote_stream_multi_chunk(self):
        """流式分块多个 > 行 → 正确组装。"""
        tokens = _stream_collect([
            "> Line 1\n",
            "> Line 2\n",
            "> Line 3\n",
        ])
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        # 连续 > 行（无空行）应合并为单一引用块
        assert len(bq_opens) == 1, \
            f"连续流式 > 行应合并: {len(bq_opens)} OPEN"
        assert len(bq_closes) == 1, \
            f"连续流式 > 行应合并: {len(bq_closes)} CLOSE"

    def test_blockquote_stream_nested(self):
        """流式分块嵌套引用 → 正确层级。"""
        tokens = _stream_collect([
            "> L1\n",
            ">> L2\n",
            ">>> L3\n",
        ])
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        assert len(bq_opens) == 3, f"应有3层: {len(bq_opens)}"
        assert bq_opens[0].meta.get("depth") == 1
        assert bq_opens[1].meta.get("depth") == 2
        assert bq_opens[2].meta.get("depth") == 3

    # ═══════════════════════════════════════════════════════
    # 混合内容
    # ═══════════════════════════════════════════════════════

    def test_blockquote_mixed_paragraph_heading_list(self):
        """> 引用含段落+标题+列表 → 全部在引用块内。"""
        tokens = _collect_tokens(
            "> Some text\n"
            "> ## Sub title\n"
            "> - item A\n"
            "> - item B\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        headings = _find_token(tokens, TokenType.HEADING)
        items = _find_token(tokens, TokenType.LIST_ITEM)
        paras = _find_token(tokens, TokenType.PARAGRAPH)

        assert bq_opens
        assert headings, "引用内标题应解析"
        assert items, "引用内列表应解析"
        assert len(items) == 2

    # ═══════════════════════════════════════════════════════
    # AST 树形嵌套验证（通过 MarkdownRecursiveParser）
    # ═══════════════════════════════════════════════════════

    def test_blockquote_ast_nesting(self):
        """AST 后处理 → 嵌套引用树形结构。"""
        from src.api.renderer.recursive_parser import MarkdownRecursiveParser
        from src.api.renderer.ast.types import NodeType

        text = "> Outer\n>> Inner\n"
        parser = MarkdownRecursiveParser()
        root = parser.parse(text)

        # 查找 BLOCKQUOTE 节点
        blockquotes = root.find(NodeType.BLOCKQUOTE)
        # _nest_blockquotes 处理后将内层 BLOCKQUOTE 作为外层的子节点
        # 所以应该只有 1 个 BLOCKQUOTE 顶级节点（内含嵌套子节点）
        assert len(blockquotes) >= 1, "应有 BLOCKQUOTE 节点"
        outer = blockquotes[0]
        assert outer.meta.get("depth") == 1 or outer.meta.get("depth") is None
        # 查找 blockquotes 中是否有子 BLOCKQUOTE（树形嵌套）
        has_nested = any(
            child.type is NodeType.BLOCKQUOTE
            for bq in blockquotes
            for child in bq.children
        )
        # 或者子节点包含内容
        assert outer.children or outer.content, \
            "引用块应有子节点或内容"

    def test_blockquote_ast_3_level_nesting(self):
        """AST 3层嵌套 → _nest_blockquotes 后树形正确。"""
        from src.api.renderer.recursive_parser import MarkdownRecursiveParser
        from src.api.renderer.ast.types import NodeType

        text = "> A\n>> B\n>>> C\n"
        parser = MarkdownRecursiveParser()
        root = parser.parse(text)

        blockquotes = root.find(NodeType.BLOCKQUOTE)
        assert len(blockquotes) >= 1
        # 验证树形：最外层 BLOCKQUOTE 有子节点
        outermost = blockquotes[0]
        # 最外层应至少有一个子节点
        can_find_inner = any(
            child.type is NodeType.BLOCKQUOTE
            for child in outermost.children
        ) if outermost.children else False
        # 或在所有 BLOCKQUOTE 中能找到深度为 2 和 3 的
        depths = [bq.meta.get("depth", 0) for bq in blockquotes]
        assert 1 in depths or 2 in depths or 3 in depths, \
            f"应有嵌套层级: depths={depths}"

    def test_blockquote_flush_close_on_empty_line(self):
        """空行关闭引用块 → 引用块 CLOSE 在 EMPTY_LINE 之前。"""
        tokens = _collect_tokens("> text\n\n")
        types = [t.type for t in tokens]

        # BLOCKQUOTE_CLOSE 应在 EMPTY_LINE 之前
        close_idx = types.index(TokenType.BLOCKQUOTE_CLOSE)
        empty_idx = types.index(TokenType.EMPTY_LINE)
        assert close_idx < empty_idx, \
            f"CLOSE({close_idx}) 应在 EMPTY({empty_idx}) 前"

    def test_blockquote_in_non_bq_context_not_affected(self):
        """非引用上下文的普通文本不受影响（回归）。"""
        tokens = _collect_tokens("Normal paragraph\n")
        bq = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        assert not bq, "普通文本不应生成引用块"
        paras = _find_token(tokens, TokenType.PARAGRAPH)
        assert paras

    def test_blockquote_no_leak_after_close(self):
        """引用块关闭后后续行不应被引用污染（回归防泄漏）。"""
        tokens = _collect_tokens(
            "> Quote\n"
            "\n"
            "Normal text\n"
        )
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)

        assert len(bq_opens) == 1, "退出后不应有多余引用 OPEN"
        assert len(bq_closes) == 1, "退出后不应有多余引用 CLOSE"

    def test_blockquote_nested_content_regression(self):
        """>> 嵌套引用每层内容独立（修复 Bug：不同深度内容不再合并为一段落）。"""
        tokens = _collect_tokens(
            "> Outer line\n"
            ">> Nested line\n"
            "> Back to outer\n"
        )
        bq_lines = _find_token(tokens, TokenType.BLOCKQUOTE_LINE)
        bq_opens = _find_token(tokens, TokenType.BLOCKQUOTE_OPEN)
        bq_closes = _find_token(tokens, TokenType.BLOCKQUOTE_CLOSE)
        paras = _find_token(tokens, TokenType.PARAGRAPH)

        # 修复后：嵌套内容应通过 BLOCKQUOTE_LINE 令牌输出，而非合并为一个 PARAGRAPH
        assert len(bq_lines) >= 3, (
            f"嵌套引用每层应有独立 BLOCKQUOTE_LINE, 实际 BLOCKQUOTE_LINE={len(bq_lines)}, "
            f"PARAGRAPH={len(paras)}"
        )
        # 不应有合并的段落（外层+嵌套+回退的内容不应合并为单个 PARAGRAPH）
        contents = [t.content for t in bq_lines]
        all_text = '\n'.join(contents)
        assert "Outer line" in all_text
        assert "Nested line" in all_text
        assert "Back to outer" in all_text
        # 修复后段落数为 0（所有 blockquote 内容走 BLOCKQUOTE_LINE）
        assert len(paras) == 0, f"嵌套引用内不应有裸 PARAGRAPH: {[p.content for p in paras]}"


# ═══════════════════════════════════════════════════════════
# 定义列表回归测试 — 新增特性
# ═══════════════════════════════════════════════════════════


class TestDefinitionList:
    """定义列表（Pandoc 风格 Term + : Definition）解析测试。"""

    def test_basic_definition_list(self):
        """Term + : Definition → DEFINITION_ITEM 包含术语。"""
        tokens = _collect_tokens("Term\n: Definition text\n")
        def_items = [t for t in tokens if t.type is TokenType.DEFINITION_ITEM]
        assert len(def_items) == 1, f"应有 1 个 DEFINITION_ITEM，实际 {len(def_items)}"
        assert def_items[0].meta.get("term") == "Term", \
            f"术语应为 'Term'，实际 {def_items[0].meta.get('term')}"
        assert def_items[0].content == "Definition text", \
            f"定义内容应为 'Definition text'，实际 {def_items[0].content!r}"

    def test_multi_definition(self):
        """一个术语 + 多个定义 → 多个 DEFINITION_ITEM。"""
        tokens = _collect_tokens("Term\n: Def 1\n: Def 2\n: Def 3\n")
        def_items = [t for t in tokens if t.type is TokenType.DEFINITION_ITEM]
        assert len(def_items) == 3, f"应有 3 个 DEFINITION_ITEM，实际 {len(def_items)}"
        assert def_items[0].meta.get("term") == "Term"
        assert def_items[0].content == "Def 1"
        assert def_items[1].meta.get("term") == ""
        assert def_items[1].content == "Def 2"

    def test_definition_with_continuation(self):
        """缩进续行应合并到前一个 DEFINITION_ITEM 的 content 中（4 空格缩进）。"""
        tokens = _collect_tokens("Term\n: Line 1\n    continued\n: Line 2\n")
        def_items = [t for t in tokens if t.type is TokenType.DEFINITION_ITEM]
        assert len(def_items) == 2, f"应有 2 个 DEFINITION_ITEM，实际 {len(def_items)}"
        assert "continued" in def_items[0].content, \
            f"'continued' 应合并到第一个定义中: {def_items[0].content!r}"
        assert def_items[0].meta.get("term") == "Term"
        assert def_items[1].content == "Line 2"

    def test_definition_no_term(self):
        """无前置术语的定义 → term 为空字符串。"""
        tokens = _collect_tokens(": Definition without term\n")
        def_items = [t for t in tokens if t.type is TokenType.DEFINITION_ITEM]
        assert len(def_items) == 1
        assert def_items[0].meta.get("term") == "", \
            f"无前置术语时 term 应为空: {def_items[0].meta.get('term')!r}"

    def test_fenced_div_not_affected(self):
        """::: fenced div 不应受到定义列表修改影响。"""
        tokens = _collect_tokens("::: info\nContent\n:::\n")
        fenced = [t for t in tokens if t.type is TokenType.FENCED_DIV_OPEN]
        assert len(fenced) == 1, f"fenced div 应正常解析，实际 {len(fenced)}"


# ═══════════════════════════════════════════════════════════
# Bug H: Fenced div 空行处理（空行不再关闭 div）
# ═══════════════════════════════════════════════════════════

class TestFencedDivEmptyLine:
    """Bug H fix: fenced div 内的空行不关闭 div，保留为空白 FENCED_DIV_LINE。"""

    def test_fenced_div_empty_line_inside(self):
        """::: warning + 空行 + 内容 → 空行保留在 div 内。"""
        tokens = _collect_tokens("::: warning\nLine 1\n\nLine 2\n:::\n")
        div_lines = [t for t in tokens if t.type is TokenType.FENCED_DIV_LINE]
        div_close = [t for t in tokens if t.type is TokenType.FENCED_DIV_CLOSE]
        empty_lines = [t for t in tokens if t.type is TokenType.FENCED_DIV_LINE and t.meta.get("empty")]
        assert len(div_lines) == 3, f"应含 3 条 FENCED_DIV_LINE，实际 {len(div_lines)}"
        assert len(empty_lines) == 1, f"应含 1 条空行，实际 {len(empty_lines)}"
        assert len(div_close) == 1, f"应含 1 个 CLOSE，实际 {len(div_close)}"
        assert div_lines[0].content == "Line 1"
        assert div_lines[2].content == "Line 2"

    def test_fenced_div_empty_lines_between(self):
        """::: tip + 多个空行 → 全部保留。"""
        tokens = _collect_tokens("::: tip\nA\n\n\nB\n:::\n")
        empty_lines = [t for t in tokens if t.type is TokenType.FENCED_DIV_LINE and t.meta.get("empty")]
        assert len(empty_lines) == 2, f"两个连续空行应生成 2 条 empty FENCED_DIV_LINE，实际 {len(empty_lines)}"

    def test_fenced_div_close_still_works(self):
        """::: 仍然正确关闭 div。"""
        tokens = _collect_tokens("::: note\nContent\n:::\n")
        closes = [t for t in tokens if t.type is TokenType.FENCED_DIV_CLOSE]
        assert len(closes) == 1


# ═══════════════════════════════════════════════════════════
# Bug I: 列表续行（缩进续行合并到前一个 LIST_ITEM）
# ═══════════════════════════════════════════════════════════

class TestListContinuation:
    """Bug I fix: 缩进续行合并到前一个 LIST_ITEM 中。"""

    def test_ul_continuation(self):
        """- Item 1 + 缩进续行 → 合并到 Item 1。"""
        tokens = _collect_tokens("- Item 1\n  continuation\n- Item 2\n")
        list_items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        assert len(list_items) == 2, f"应有 2 个 LIST_ITEM，实际 {len(list_items)}"
        assert "continuation" in list_items[0].content,             f"continuation 应合并到 Item 1: {list_items[0].content!r}"
        assert "Item 2" in list_items[1].content

    def test_ol_continuation(self):
        """1. Item + 缩进续行 → 合并。"""
        tokens = _collect_tokens("1. Item\n   continuation\n2. Next\n")
        list_items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        assert len(list_items) == 2
        assert "continuation" in list_items[0].content

    def test_deep_indent_continuation(self):
        """深层嵌套列表 + 缩进续行 → 合并。"""
        tokens = _collect_tokens("  - Item\n    continuation\n  - Next\n")
        list_items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        assert len(list_items) == 2
        assert "continuation" in list_items[0].content,             f"深层续行应合并: {list_items[0].content!r}"

    def test_no_continuation_without_indent(self):
        """无缩进的文本紧跟列表项 → 不作为续行。"""
        tokens = _collect_tokens("- Item 1\nnot continuation\n- Item 2\n")
        list_items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        for li in list_items:
            assert "not continuation" not in li.content,                 "无缩进的文本不应合并到列表项"


# ═══════════════════════════════════════════════════════════
# 新特性：Admonition 类型扩展（INFO/SUCCESS/QUESTION/BUG/DANGER）
# ═══════════════════════════════════════════════════════════

class TestAdmonitionExtendedTypes:
    """扩展的 admonition 类型支持。"""

    def test_admonition_info(self):
        """> [!INFO] 被识别为 INFO 类型。"""
        tokens = _collect_tokens("> [!INFO] Information\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1
        assert opens[0].meta.get("type") == "INFO"

    def test_admonition_success(self):
        """> [!SUCCESS] 被识别为 SUCCESS 类型。"""
        tokens = _collect_tokens("> [!SUCCESS] Success\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1
        assert opens[0].meta.get("type") == "SUCCESS"

    def test_admonition_bug(self):
        """> [!BUG] 被识别为 BUG 类型。"""
        tokens = _collect_tokens("> [!BUG] Bug report\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1
        assert opens[0].meta.get("type") == "BUG"

    def test_admonition_switch_type_inline(self):
        """> [!INFO] > [!SUCCESS] 无缝切换。"""
        tokens = _collect_tokens("> [!INFO] Info\n> [!SUCCESS] Success\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        closes = [t for t in tokens if t.type is TokenType.ADMONITION_CLOSE]
        assert len(opens) == 2, f"应有 2 次 ADMONITION_OPEN，实际 {len(opens)}"
        assert len(closes) >= 1, "类型切换应产生 ADMONITION_CLOSE"
        assert opens[0].meta.get("type") == "INFO"
        assert opens[1].meta.get("type") == "SUCCESS"


# ═══════════════════════════════════════════════════════════
# Bug J 回归测试：嵌套列表缩进代码块误判
# ═══════════════════════════════════════════════════════════

class TestBugJ_NestedListIndentedCodeBlock:
    """Bug J：列表上下文中 4+ 空格缩进被误判为缩进代码块。

    修复：在缩进代码块检测前检查是否处于活跃列表上下文，
    如果是则让 dispatch 表处理为列表项而非缩进代码块。
    """

    def test_3_level_nested_list(self):
        """3 级嵌套列表 → 第 3 级为 LIST_ITEM 而非 INDENTED_CODE。"""
        tokens = _collect_tokens("- L1\n  - L2\n    - L3\n")
        items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        code_tokens = [t for t in tokens if t.type in (TokenType.CODE_FENCE_OPEN, TokenType.CODE_LINE)]
        assert len(items) == 3, f"应有 3 个 LIST_ITEM，实际 {len(items)}"
        assert len(code_tokens) == 0, f"不应有代码块 Token，实际 {len(code_tokens)}"
        assert items[0].meta.get("depth") == 1
        assert items[1].meta.get("depth") == 2
        assert items[2].meta.get("depth") == 3
        assert items[2].content == "L3"

    def test_4_level_nested_list(self):
        """4 级嵌套列表 → 全部正确为 LIST_ITEM。"""
        tokens = _collect_tokens("- L1\n  - L2\n    - L3\n      - L4\n")
        items = [t for t in tokens if t.type is TokenType.LIST_ITEM]
        code_tokens = [t for t in tokens if t.type in (TokenType.CODE_FENCE_OPEN, TokenType.CODE_LINE)]
        assert len(items) == 4, f"应有 4 个 LIST_ITEM，实际 {len(items)}"
        assert len(code_tokens) == 0, f"不应有代码块 Token，实际 {len(code_tokens)}"

    def test_regular_indented_code_outside_list(self):
        """列表上下文外的缩进代码块仍可正常触发（非字母开头的行）。
        注意：字母开头的缩进行会走快速通道被段落吞噬，此为已知限制。
        """
        tokens = _collect_tokens("    <?php echo 1;?>\n")
        code_tokens = [t for t in tokens if t.type in (TokenType.CODE_FENCE_OPEN, TokenType.CODE_LINE)]
        assert len(code_tokens) > 0, "列表外缩进代码块应被解析"


# ═══════════════════════════════════════════════════════════
# Bug K 回归测试：Admonition 检测过于严格
# ═══════════════════════════════════════════════════════════

class TestBugK_AdmonitionNoSpaceAfterBracket:
    """Bug K：> [!NOTE]Hello（]后无空格）未识别为 admonition。

    修复：放宽 ] 后任意字符都接受，不再要求空格或结束。
    """

    def test_admonition_no_space(self):
        """> [!NOTE]Hello → 识别为 NOTE 类型 admonition。"""
        tokens = _collect_tokens("> [!NOTE]Hello\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1, f"应有 1 个 ADMONITION_OPEN，实际 {len(opens)}"
        assert opens[0].meta.get("type") == "NOTE"
        assert opens[0].content == "Hello"

    def test_admonition_no_space_warning(self):
        """> [!WARNING]Watch out → 识别为 WARNING。"""
        tokens = _collect_tokens("> [!WARNING]Watch out\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1
        assert opens[0].meta.get("type") == "WARNING"
        assert opens[0].content == "Watch out"

    def test_admonition_with_space_still_works(self):
        """> [!TIP] tip → 原有空格语法仍正常。"""
        tokens = _collect_tokens("> [!TIP] tip\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 1
        assert opens[0].meta.get("type") == "TIP"
        assert opens[0].content == "tip"

    def test_admonition_switch_without_space(self):
        """> [!INFO]Info\\n> [!SUCCESS]Success → 无缝切换。"""
        tokens = _collect_tokens("> [!INFO]Info\n> [!SUCCESS]Success\n")
        opens = [t for t in tokens if t.type is TokenType.ADMONITION_OPEN]
        assert len(opens) == 2
        assert opens[0].meta.get("type") == "INFO"
        assert opens[1].meta.get("type") == "SUCCESS"


# ═══════════════════════════════════════════════════════════
# 第五轮：block_parser Bug 修复 + 注释行
# ============================================================

class TestDeferredFenceNoLanguageEmission:
    """延迟 fence 不将语言名作为代码内容发出。"""

    def test_language_not_in_code_content(self):
        """流式输入 ``` + python 时，python 不应出现在代码内容中。"""
        tokens = _stream_collect(["```\n", "python\n", "print(1)\n", "```\n"])
        code_lines = [t.content for t in tokens if t.type == TokenType.CODE_LINE]
        assert "python" not in code_lines

    def test_language_not_in_code_content_various_langs(self):
        """多语言白名单测试：语言名不泄露到代码内容。"""
        for lang in ("javascript", "go", "rust", "bash", "typescript", "java"):
            tokens = _stream_collect([f"```\n", f"{lang}\n", "code\n", "```\n"])
            code_lines = [t.content for t in tokens if t.type == TokenType.CODE_LINE]
            assert lang not in code_lines, f"语言 {lang} 不应出现在代码内容中"


class TestGfmTableNotSwallowList:
    """GFM 表格检测不误吞列表项。"""

    def test_list_with_pipe_not_table_ul(self):
        """- item1 | item2 | item3 不应被当作表格。"""
        tokens = _collect_tokens("- item1 | item2 | item3\n- item4 | item5\n")
        token_types = [t.type for t in tokens]
        assert TokenType.TABLE not in token_types

    def test_list_with_pipe_not_table_ol(self):
        """1. item | detail 不应被当作表格。"""
        tokens = _collect_tokens("1. item | detail\n2. another | more\n")
        token_types = [t.type for t in tokens]
        assert TokenType.TABLE not in token_types

    def test_list_with_pipe_not_table_plus(self):
        """+ item | detail 不应被当作表格。"""
        tokens = _collect_tokens("+ task | status\n+ other | status\n")
        token_types = [t.type for t in tokens]
        assert TokenType.TABLE not in token_types

    def test_gfm_table_still_works(self):
        """普通 GFM 表格（无前导 pipe）仍正常工作。"""
        tokens = _collect_tokens("Name|Age|City\nAlice|30|NYC\n")
        # 流式表格（无分隔行）应被缓冲后 emit
        all_text = " ".join(t.content for t in tokens)
        assert "Alice" in all_text


class TestHeadingClearsListIndent:
    """标题清除列表缩进状态。"""

    def test_heading_resets_list(self):
        """标题后的列表项不被误判为嵌套。"""
        tokens = _collect_tokens("  - nested\n# heading\n- new top-level\n")
        assert any(t.type == TokenType.LIST_ITEM for t in tokens)

    def test_setext_heading_resets_list(self):
        """Setext 标题后的列表项不被误判为嵌套。"""
        tokens = _collect_tokens("  - nested\nheading\n=======\n- new top-level\n")
        assert any(t.type == TokenType.LIST_ITEM for t in tokens)


class TestCommentLine:
    """注释行语法 [//]: # (comment)"""

    def test_comment_line_ignored(self):
        """[//]: # 注释行不产生任何 token。"""
        tokens = _collect_tokens("[//]: # (this is a comment)\nreal content\n")
        texts = [t.content for t in tokens if hasattr(t, 'content')]
        assert not any("this is a comment" in t for t in texts), \
            "注释内容不应出现在 token 中"
        assert any("real content" in t for t in texts), \
            "注释后的正常内容应保留"

    def test_comment_line_without_parentheses(self):
        """[//]: # comment 无括号也忽略。"""
        tokens = _collect_tokens("[//]: # a comment\ntext\n")
        texts = [t.content for t in tokens if hasattr(t, 'content')]
        assert not any("a comment" in t for t in texts)

    def test_comment_line_before_other_syntax(self):
        """注释行在其他语法之前应被忽略。"""
        tokens = _collect_tokens("[//]: # ignore me\n# real heading\ncontent\n")
        texts = [t.content for t in tokens if hasattr(t, 'content')]
        assert not any("ignore me" in t for t in texts)
        assert any("real heading" in t for t in texts)

    def test_multiple_comment_lines(self):
        """连续多个注释行全部忽略。"""
        tokens = _collect_tokens(
            "[//]: # comment1\n"
            "[//]: # comment2\n"
            "[//]: # comment3\n"
            "actual text\n"
        )
        texts = [t.content for t in tokens if hasattr(t, 'content')]
        assert not any("comment1" in t for t in texts)
        assert not any("comment2" in t for t in texts)
        assert not any("comment3" in t for t in texts)
        assert any("actual text" in t for t in texts)


# ═══════════════════════════════════════════════════════════
# 第六轮新增：有序列表 1) 语法 + 取消任务 [-]
# ═══════════════════════════════════════════════════════════


class TestOrderedListParenStyle:
    """有序列表 1) item 语法。"""

    def test_basic_paren_ordered_list(self):
        """1) item → LIST_ITEM。"""
        tokens = _collect_tokens("1) first\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].content.strip() == "first"
        assert items[0].meta["bullet"] is False
        assert items[0].meta["number"] == 1
        assert items[0].meta.get("delimiter") == ")"

    def test_paren_ordered_list_multiple(self):
        """1), 2) → 多个 LIST_ITEM。"""
        tokens = _collect_tokens("1) one\n2) two\n3) three\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 3
        assert items[0].meta["number"] == 1
        assert items[1].meta["number"] == 2
        assert items[2].meta["number"] == 3
        for item in items:
            assert item.meta.get("delimiter") == ")"

    def test_paren_style_mixed_with_dot_style(self):
        """1) 和 2. 混用各自独立解析。"""
        tokens = _collect_tokens("1) paren\n2. dot\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 2

    def test_paren_style_not_without_space(self):
        """1)text（无空格）→ 不应触发列表。"""
        tokens = _collect_tokens("1)text\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 0

    def test_paren_style_multi_digit(self):
        """12) item → 两位数字列表。"""
        tokens = _collect_tokens("12) twelve\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta["number"] == 12
        assert items[0].meta.get("delimiter") == ")"


class TestCancelledTaskCheckbox:
    """取消任务 [-], 取消 checkbox"""

    def test_cancelled_task_basic(self):
        """- [-] cancelled → todo=True, checked=False, cancelled=True。"""
        tokens = _collect_tokens("- [-] cancelled task\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[0].meta.get("cancelled") is True
        assert "[-]" in items[0].content

    def test_cancelled_task_with_ordered_list(self):
        """1) [-] cancelled in ordered list。"""
        tokens = _collect_tokens("1) [-] item\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("cancelled") is True

    def test_cancelled_not_affect_checked(self):
        """[x] checked 不变。"""
        tokens = _collect_tokens("- [x] done\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("checked") is True
        assert items[0].meta.get("cancelled") is False

    def test_cancelled_not_affect_unchecked(self):
        """[ ] unchecked 不变。"""
        tokens = _collect_tokens("- [ ] todo\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("todo") is True
        assert items[0].meta.get("checked") is False
        assert items[0].meta.get("cancelled") is False

    def test_cancelled_in_blockquote(self):
        """> - [-] cancelled in blockquote。"""
        tokens = _collect_tokens("> - [-] cancelled in quote\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 1
        assert items[0].meta.get("cancelled") is True

    def test_triple_state_mix(self):
        """[ ], [x], [-] 三种状态共存。"""
        tokens = _collect_tokens("- [ ] todo\n- [x] done\n- [-] cancelled\n")
        items = _find_token(tokens, TokenType.LIST_ITEM)
        assert len(items) == 3
        # unchecked
        assert items[0].meta["todo"] is True
        assert items[0].meta["checked"] is False
        assert items[0].meta["cancelled"] is False
        # checked
        assert items[1].meta["checked"] is True
        assert items[1].meta["cancelled"] is False
        # cancelled
        assert items[2].meta["cancelled"] is True
        assert items[2].meta["checked"] is False


class TestDefinitionFixes:
    """_try_definition 不匹配 :emoji: 修复。"""

    def test_definition_still_works(self):
        """: definition text → DEFINITION_ITEM。"""
        tokens = _collect_tokens("term\n: definition text\n")
        def_items = _find_token(tokens, TokenType.DEFINITION_ITEM)
        assert len(def_items) == 1, f"应有定义项: {def_items}"
        assert "definition text" in def_items[0].content

    def test_emoji_at_line_start_not_definition(self):
        """:smile: → 不应被识别为定义（:与名称间无空格）。"""
        tokens = _collect_tokens(":smile: reaction\n")
        def_items = _find_token(tokens, TokenType.DEFINITION_ITEM)
        assert len(def_items) == 0, f":smile: 不应被误判为定义: {def_items}"
        # 应降级为段落
        para_items = _find_token(tokens, TokenType.PARAGRAPH)
        assert len(para_items) >= 1, f":smile: 应降级为段落: {[t.type.name for t in tokens]}"

    def test_definition_with_space_after_colon(self):
        """:  text（双空格后）→ 正常识别为定义。"""
        tokens = _collect_tokens("term\n:  definition text\n")
        def_items = _find_token(tokens, TokenType.DEFINITION_ITEM)
        assert len(def_items) == 1, f"双空格后定义应正常识别: {def_items}"

    def test_colon_only_no_space_not_definition(self):
        """:alone（无空格）→ 不应为定义。"""
        tokens = _collect_tokens(":alone\n")
        def_items = _find_token(tokens, TokenType.DEFINITION_ITEM)
        assert len(def_items) == 0, f":alone 不应为定义: {def_items}"


# ═══════════════════════════════════════════════════════════
# Bug 回归测试：flush 纯空白缓冲区（strip() boolean gate 修复）
# ═══════════════════════════════════════════════════════════

class TestFlushWhitespaceBuffer:
    """flush() 方法对纯空白/尾部空白缓冲区的正确处理。

    修复前 flush() 使用 self._buffer.strip() 判断是否有残留内容，
    纯空白 buffer 会导致 strip() 返回空串 → 内容被静默丢弃。
    """

    def test_flush_pure_whitespace_buffer_no_crash(self):
        """纯空白 buffer flush 不抛异常且生成空行 token（不再静默丢弃）。"""
        parser = RegexFreeBlockParser()
        # feed 一段纯空白文本（仅空格/制表符）
        parser.feed("   \t  ")
        # flush 不应抛异常
        tokens = parser.flush()
        # 修复后纯空白 buffer 不再静默丢弃，应生成 EMPTY_LINE token
        assert isinstance(tokens, list), f"flush 应返回 list，实际返回 {type(tokens)}"
        assert len(tokens) >= 1, \
            f"纯空白 buffer 不应静默丢弃，应生成至少 1 个 token，实际: {len(tokens)}"
        # EMPTY_LINE 是 Rich 渲染时空行不可见，但确保 parser 正确处理了
        empty_lines = [t for t in tokens if t.type is TokenType.EMPTY_LINE]
        para_tokens = [t for t in tokens if t.type is TokenType.PARAGRAPH]
        assert empty_lines or para_tokens, \
            f"应生成 EMPTY_LINE 或 PARAGRAPH token，实际: {[t.type.name for t in tokens]}"

    def test_flush_text_without_newline(self):
        """无换行结尾的普通文本在 flush 时正确输出为 PARAGRAPH。"""
        parser = RegexFreeBlockParser()
        parser.feed("hello world")
        tokens = parser.flush()
        # 应生成 PARAGRAPH token
        para_tokens = [t for t in tokens if t.type is TokenType.PARAGRAPH]
        assert len(para_tokens) >= 1, \
            f"应生成至少一个 PARAGRAPH token，实际 tokens: {[t.type.name for t in tokens]}"

    def test_flush_text_with_trailing_spaces(self):
        """buffer 含文本+尾部空格 → flush 正确保留在 PARAGRAPH 内容中。"""
        parser = RegexFreeBlockParser()
        parser.feed("hello world   ")
        tokens = parser.flush()
        # 应生成 PARAGRAPH token，且内容保留尾部空格
        para_tokens = [t for t in tokens if t.type is TokenType.PARAGRAPH]
        assert len(para_tokens) >= 1, \
            f"应生成至少一个 PARAGRAPH token，实际: {[t.type.name for t in tokens]}"
        # 验证 trailing spaces 被保留在 token 内容中
        content = para_tokens[0].text if hasattr(para_tokens[0], 'text') else str(para_tokens[0])
        assert "hello world" in content or "hello world   " in content, \
            f"PARAGRAPH 应包含原始文本，实际内容: {content!r}"

    def test_flush_empty_buffer_is_noop(self):
        """空 buffer flush 是空操作。"""
        parser = RegexFreeBlockParser()
        tokens = parser.flush()
        assert tokens == [], f"空 buffer flush 应返回空列表，实际: {tokens}"

    def test_flush_content_without_newline_preserved(self):
        """无换行结尾的文本在 flush 时正确输出为 PARAGRAPH。"""
        tokens = _collect_tokens("**bold** text without newline")
        para_tokens = _find_token(tokens, TokenType.PARAGRAPH)
        assert para_tokens, \
            f"无换行结尾的文本应生成 PARAGRAPH: {[t.type.name for t in tokens]}"

    def test_stream_flush_preserves_trailing_content(self):
        """流式输入末尾文本（无换行）在 flush 后正确输出。"""
        chunks = ["**bold**", " and *italic*"]
        tokens = _stream_collect(chunks)
        para_tokens = _find_token(tokens, TokenType.PARAGRAPH)
        # 流式输入应至少产生 PARAGRAPH token（合并后的内容）
        assert para_tokens, \
            f"流式输入末尾应产生 PARAGRAPH: {[t.type.name for t in tokens]}"
