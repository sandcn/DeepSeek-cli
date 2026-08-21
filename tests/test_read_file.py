"""read_file 工具测试（2026-08）。

覆盖：
- show_line_numbers 参数：默认关闭（返回内容不带行号）
- show_line_numbers=True：整文件读取时为每行附加行号（从 1 起）
- show_line_numbers + 行号范围读取：从 start_line 起连续编号
- from_args 布尔解析（bool/字符串/数字/None 兼容）
- 空文件返回「文件为空」且不崩
- 末尾换行符不额外产生空行行号
- to_tool_schema 声明 show_line_numbers 且默认 False
- display_params 显示 line-num 标志
"""

from __future__ import annotations

import pytest

from src.tools.read_file import ReadFileFunc


def _write(path, content: str) -> str:
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return str(path)


# ── 1. 默认关闭：不带行号 ───────────────────────────────

async def test_show_line_numbers_default_off(tmp_path):
    """默认（不传 show_line_numbers）时不附加行号。"""
    p = _write(tmp_path / "a.txt", "line1\nline2\nline3")
    out = await ReadFileFunc(path=p).execute()
    assert out == f"文件: {p}\nline1\nline2\nline3"
    assert "1  line1" not in out


async def test_show_line_numbers_false_explicit(tmp_path):
    """显式传 show_line_numbers=False 也不附加行号。"""
    p = _write(tmp_path / "a.txt", "line1\nline2")
    out = await ReadFileFunc(path=p, show_line_numbers=False).execute()
    assert out == f"文件: {p}\nline1\nline2"


# ── 2. 开启：整文件带行号 ───────────────────────────────

async def test_show_line_numbers_whole_file(tmp_path):
    """整文件读取时每行带行号（从 1 起）。"""
    p = _write(tmp_path / "a.txt", "line1\nline2\nline3")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    assert out.startswith(f"文件: {p}\n")
    body = out.split("\n", 1)[1]
    assert body == "1  line1\n2  line2\n3  line3"


async def test_show_line_numbers_does_not_affect_plain_read(tmp_path):
    """show_line_numbers=True 不改变行内容本身（仅前缀）。"""
    p = _write(tmp_path / "a.txt", "alpha\nbeta")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    assert "1  alpha" in out
    assert "2  beta" in out


# ── 3. 范围读取：从 start_line 起编号 ────────────────────

async def test_show_line_numbers_with_range(tmp_path):
    """行号范围读取时从 start_line 起连续编号。"""
    p = _write(tmp_path / "a.txt", "line1\nline2\nline3\nline4\nline5")
    out = await ReadFileFunc(path=p, start_line=2, end_line=4, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "2  line2\n3  line3\n4  line4"


async def test_show_line_numbers_with_start_only(tmp_path):
    """只给 start_line（读到末尾）：编号从 start_line 起。"""
    p = _write(tmp_path / "a.txt", "l1\nl2\nl3")
    out = await ReadFileFunc(path=p, start_line=2, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "2  l2\n3  l3"


# ── 4. from_args 布尔解析 ───────────────────────────────

def test_from_args_coerce_bool_true_forms():
    """字符串/数字/布尔若干真值归一化为 True。"""
    for val in (True, "true", "True", "1", 1, "yes", "on"):
        inst = ReadFileFunc.from_args({"path": "x.py", "show_line_numbers": val})
        assert inst.show_line_numbers is True


def test_from_args_coerce_bool_false_forms():
    """假值/缺省归一化为 False。"""
    for val in (False, None, "false", "0", 0, "", "no", "off"):
        inst = ReadFileFunc.from_args({"path": "x.py", "show_line_numbers": val})
        assert inst.show_line_numbers is False


def test_from_args_default_false_when_missing():
    """未传 show_line_numbers 时默认为 False。"""
    inst = ReadFileFunc.from_args({"path": "x.py"})
    assert inst.show_line_numbers is False


# ── 5. 空文件 ───────────────────────────────────────────

async def test_show_line_numbers_empty_file(tmp_path):
    """空文件返回「文件为空」提示，不因 show_line_numbers 崩溃。"""
    p = _write(tmp_path / "a.txt", "")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    assert out == f"(文件为空: {p})"


# ── 6. 末尾换行处理 ─────────────────────────────────────

async def test_show_line_numbers_trailing_newline_no_extra_line(tmp_path):
    """末尾换行不额外产生空行行号。"""
    p = _write(tmp_path / "a.txt", "a\nb\n")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "1  a\n2  b"


async def test_show_line_numbers_middle_blank_line_kept(tmp_path):
    """中间空行保留行号（空行仍编号）。"""
    p = _write(tmp_path / "a.txt", "a\n\nb")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "1  a\n2  \n3  b"


# ── 7. schema 与 display_params ─────────────────────────

def test_schema_contains_show_line_numbers():
    """to_tool_schema 声明 show_line_numbers 且默认 False。"""
    schema = ReadFileFunc.to_tool_schema()
    params = schema["function"]["parameters"]["properties"]
    assert "show_line_numbers" in params
    assert params["show_line_numbers"]["type"] == "boolean"
    assert params["show_line_numbers"]["default"] is False

# ── 8. 边界防护（review 清零后补充覆盖） ──────────────

async def test_start_line_out_of_range_returns_error(tmp_path):
    """start_line 超过文件总行数返回越界错误，不误报为空文件。"""
    p = _write(tmp_path / "a.txt", "l1\nl2\nl3")
    out = await ReadFileFunc(path=p, start_line=100).execute()
    assert out == "(行号越界: 文件共 3 行，起始行 100)"


async def test_end_line_non_positive_clamped(tmp_path):
    """直接构造 end_line<=0 时被 clamp 为 1，读第一行。"""
    p = _write(tmp_path / "a.txt", "l1\nl2\nl3")
    out = await ReadFileFunc(path=p, start_line=1, end_line=0).execute()
    assert out.startswith(f"文件: {p}\n")
    assert out.split("\n", 1)[1] == "l1\n"


def test_from_args_empty_path_raises():
    """from_args 空 path / 空 list / 缺 path 抛出 ValueError。"""
    with pytest.raises(ValueError):
        ReadFileFunc.from_args({"path": ""})
    with pytest.raises(ValueError):
        ReadFileFunc.from_args({"path": []})
    with pytest.raises(ValueError):
        ReadFileFunc.from_args({})


def test_direct_construct_empty_path_raises():
    """直接构造空 path 抛出 ValueError。"""
    with pytest.raises(ValueError):
        ReadFileFunc(path="")


def test_display_params_range_labels():
    """display_params 对范围参数显示 L 标签。"""
    assert "L2-4" in ReadFileFunc.display_params(
        {"path": "x.py", "start_line": 2, "end_line": 4}
    )
    assert "L2+" in ReadFileFunc.display_params({"path": "x.py", "start_line": 2})
    assert "L1-4" in ReadFileFunc.display_params({"path": "x.py", "end_line": 4})


# ── 9. 边界与行尾一致性（review 二轮清零补充） ─────────

async def test_direct_construct_start_gt_end_swaps(tmp_path):
    """直接构造 start_line>end_line 时交换（与 from_args 一致）。"""
    p = _write(tmp_path / "a.txt", "l1\nl2\nl3\nl4")
    out = await ReadFileFunc(path=p, start_line=4, end_line=2, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "2  l2\n3  l3\n4  l4"


async def test_end_line_exceeds_total_clamps_to_total(tmp_path):
    """end_line>total 时 clamp 到 total，只返回实际行。"""
    p = _write(tmp_path / "a.txt", "l1\nl2\nl3")
    out = await ReadFileFunc(path=p, start_line=1, end_line=100, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "1  l1\n2  l2\n3  l3"


async def test_lone_cr_line_ending_consistent_whole_vs_range(tmp_path):
    """包含 lone \\r 行尾：整文件与范围读取行为一致（归一化为 \\n）。"""
    p = _write(tmp_path / "a.txt", "a\rb\rc")
    whole = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    body = whole.split("\n", 1)[1]
    assert body == "1  a\n2  b\n3  c"
    rng = await ReadFileFunc(path=p, start_line=2, show_line_numbers=True).execute()
    body2 = rng.split("\n", 1)[1]
    assert body2 == "2  b\n3  c"


async def test_crlf_line_ending_normalized(tmp_path):
    """\\r\\n 行尾归一化为 \\n 并在行号显示时无残留 \\r。"""
    p = _write(tmp_path / "a.txt", "a\r\nb\r\nc")
    out = await ReadFileFunc(path=p, show_line_numbers=True).execute()
    body = out.split("\n", 1)[1]
    assert body == "1  a\n2  b\n3  c"
    assert "\r" not in body

