"""search 工具返回大模型的结果截断测试（2026-09-18）。

需求：search 返回给大模型的内容超过 1000 行时自动截断，
保留前 500 行 + 后 500 行，中间以省略标记替代（三路引擎 rg/grep/纯 Python
共用的 _format_results 出口统一生效）。
"""

from __future__ import annotations

import pytest

from src.tools.search import SearchFunc


# ── 1. 常量口径 ────────────────────────────────────────

def test_truncate_constants():
    assert SearchFunc.MAX_LINES == 1000
    assert SearchFunc.HEAD_LINES == 500
    assert SearchFunc.TAIL_LINES == 500
    assert SearchFunc.HEAD_LINES + SearchFunc.TAIL_LINES == SearchFunc.MAX_LINES


# ── 2. _truncate_output 边界 ───────────────────────────

def test_truncate_output_empty_and_short():
    assert SearchFunc._truncate_output("") == ""
    text = "\n".join(f"line{i}" for i in range(10))
    assert SearchFunc._truncate_output(text) == text


def test_truncate_output_exactly_max_lines_not_truncated():
    text = "\n".join(f"line{i}" for i in range(SearchFunc.MAX_LINES))
    assert SearchFunc._truncate_output(text) == text


def test_truncate_output_trailing_newline_not_counted():
    text = "\n".join(f"line{i}" for i in range(SearchFunc.MAX_LINES)) + "\n"
    assert SearchFunc._truncate_output(text) == text


def test_truncate_output_over_max_keeps_head_and_tail():
    total = SearchFunc.MAX_LINES + 1
    lines = [f"line{i}" for i in range(total)]
    out = SearchFunc._truncate_output("\n".join(lines))
    out_lines = out.split("\n")
    # 头 500 + 1 行省略标记 + 尾 500
    assert len(out_lines) == SearchFunc.HEAD_LINES + SearchFunc.TAIL_LINES + 1
    assert out_lines[0] == lines[0]
    assert out_lines[-1] == lines[-1]
    assert out_lines[SearchFunc.HEAD_LINES - 1] == lines[SearchFunc.HEAD_LINES - 1]
    assert out_lines[SearchFunc.HEAD_LINES + 1] == lines[-SearchFunc.TAIL_LINES]
    marker = out_lines[SearchFunc.HEAD_LINES]
    assert "结果已截断" in marker
    assert f"共 {total} 行" in marker
    assert "省略 1 行" in marker
    assert f"前 {SearchFunc.HEAD_LINES} 行" in marker
    assert f"后 {SearchFunc.TAIL_LINES} 行" in marker


def test_truncate_output_omitted_count():
    total = 3000
    lines = [f"line{i}" for i in range(total)]
    out = SearchFunc._truncate_output("\n".join(lines))
    assert f"省略 {total - SearchFunc.MAX_LINES} 行" in out


# ── 3. _format_results 出口截断 ────────────────────────

def _make_results(n: int, filename: str = "f.py"):
    return [(filename, i, f"match line {i}") for i in range(1, n + 1)]


def test_format_results_small_not_truncated():
    f = SearchFunc(query="match")
    out = f._format_results(_make_results(3))
    assert "结果已截断" not in out
    assert "共找到 3 处匹配" in out


def test_format_results_over_limit_truncated():
    f = SearchFunc(query="match")
    out = f._format_results(_make_results(1200))
    # header(1) + 空行 + 文件名行(2) + 1200 匹配行 = 1203 行 > 1000
    out_lines = out.split("\n")
    assert len(out_lines) == SearchFunc.HEAD_LINES + SearchFunc.TAIL_LINES + 1
    assert "结果已截断" in out
    assert out_lines[0] == "搜索「match」共找到 1200 处匹配:"
    assert out_lines[-1] == "    L1200:  match line 1200"
    assert "共 1203 行" in out


def test_format_results_no_match_message_not_truncated():
    f = SearchFunc(query="match")
    out = f._format_results([])
    assert out == "搜索「match」未找到结果"


def test_format_results_regex_error_message_not_truncated():
    f = SearchFunc(query="match")
    f._pattern = None
    f._regex_error = "bad pattern"
    out = f._format_results([])
    assert out == "(正则表达式错误: bad pattern)"


def test_format_results_skipped_binary_tail_preserved():
    f = SearchFunc(query="match")
    out = f._format_results(_make_results(3), skipped_binary=2)
    assert "跳过了 2 个二进制文件匹配" in out
    assert "结果已截断" not in out


# ── 4. rg/grep 出口（_format_lines）同样截断 ───────────

def test_format_lines_over_limit_truncated():
    f = SearchFunc(query="match")
    raw = [f"f.py:{i}:match line {i}" for i in range(1, 1201)]
    out = f._format_lines(raw)
    assert "结果已截断" in out
    assert len(out.split("\n")) == SearchFunc.HEAD_LINES + SearchFunc.TAIL_LINES + 1


def test_format_lines_small_not_truncated():
    f = SearchFunc(query="match")
    out = f._format_lines(["f.py:1:match line 1", "f.py:2:match line 2"])
    assert "结果已截断" not in out
    assert "共找到 2 处匹配" in out


# ── 5. 纯 Python 引擎端到端（execute 返回截断结果） ────

async def test_execute_pure_python_path_truncates(tmp_path):
    target = tmp_path / "big.txt"
    target.write_text(
        "\n".join(f"needle {i}" for i in range(1, 1201)) + "\n",
        encoding="utf-8",
    )
    f = SearchFunc(query="needle", path=str(tmp_path))
    f._has_rg = False
    f._has_grep = False
    out = await f.execute()
    out_lines = out.split("\n")
    assert out_lines[0] == "搜索「needle」共找到 1200 处匹配:"
    assert "结果已截断" in out
    assert len(out_lines) == SearchFunc.HEAD_LINES + SearchFunc.TAIL_LINES + 1
    assert "needle 1200" in out_lines[-1]


async def test_execute_pure_python_path_small_intact(tmp_path):
    target = tmp_path / "small.txt"
    target.write_text("needle 1\nother\nneedle 2\n", encoding="utf-8")
    f = SearchFunc(query="needle", path=str(tmp_path))
    f._has_rg = False
    f._has_grep = False
    out = await f.execute()
    assert "共找到 2 处匹配" in out
    assert "结果已截断" not in out


# ── 6. schema 描述同步 ─────────────────────────────────

def test_tool_schema_mentions_truncation():
    desc = SearchFunc.to_tool_schema()["function"]["description"]
    assert "1000 行" in desc
    assert "500 行" in desc
    assert "截断" in desc
