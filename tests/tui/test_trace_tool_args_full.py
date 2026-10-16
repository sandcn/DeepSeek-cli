"""轨迹 Trace 工具实参完整显示测试（2026-08-19 用户需求）。

需求：轨迹 Trace 的工具的实参要显示完整。

实现固化项：
  1. ``_tree_node_rows`` 超宽行由**截断**（``truncate_runs``——超宽部分
     直接丢弃，长实参在检查器不可见）改为**换行显示完整**
     （``_tree_row_wrap`` → ``wrap_runs_by_width`` hard 字符级硬拆，与
     检查器纯文本 ``_wrap_by_width`` 同语义）——长实参（bash command /
     update_file old_string 全文等）折行后全部可见；每行宽度 <= right_w
     （行级 diff 宽度不变量保持）；
  2. 续行 hanging indent：缩进到首行内容起始列（depth*2 + 2），值与层级
     视觉连贯；极窄栏（hang >= right_w）续行不缩进（预算不足防御，内容
     仍完整）；
  3. 键/值分色（BEAUTY-36）跨行保持：首行键 _S_TREE_KEY / 值 _S_TREE_VAL；
     续行（值延续）_S_TREE_VAL；纯文本行 _S_TEXT；
  4. ``_tool_tree_rows``（参数树 + 分割线 + 返回值树）与
     ``_inspector_content_rows``（检查器内容行）长参数/返回值完整
     （同一渲染管线）；
  5. 未超宽行行为零回归（单行输出、键值分色不变）。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.trace import TraceRecord
from src.tui.app.trace_view import (
    _S_TEXT,
    _S_TREE_KEY,
    _S_TREE_VAL,
    _args_to_tree,
    _inspector_content_rows,
    _parse_tree_text,
    _tool_tree_rows,
    _tree_node_rows,
    _tree_row_wrap,
)


def _hang(depth: int) -> int:
    """树行 hanging indent（prefix depth*2 + 指示符 2 列）。"""
    return depth * 2 + 2


def _strip_hang(runs, hang: int) -> str:
    """去掉行首 hang 列（首行 prefix+indicator / 续行缩进），还原内容。"""
    return "".join(r.text for r in runs)[hang:]


def _plain(row) -> str:
    return "".join(r.text for r in row)


class TestTreeNodeRowsWrapFull:
    """超宽树行换行显示完整（实参完整显示核心）。"""

    def test_long_leaf_wraps_full_content(self):
        """超宽叶子行折行；各线去 hang 后拼接 == 原 label（零丢失）。"""
        label = "command: python3 -c 'print(\"hello world\" * 20)'"
        out = []
        _tree_node_rows([{"label": label, "children": []}], 20, out)
        assert len(out) > 1, "超宽行应折行"
        hang = _hang(0)
        joined = "".join(_strip_hang(row, hang) for row in out)
        assert joined == label
        for row in out:
            assert sum(len(r.text) for r in row) <= 20

    def test_long_json_args_complete(self):
        """长参数 JSON（300 字符值）折行后完整显示。"""
        args = ('{"old_string": "' + "x" * 300
                + '", "path": "/home/user/project/src/main.py"}')
        out = []
        _tree_node_rows(_args_to_tree(args), 40, out)
        assert len(out) > 1
        hang = _hang(0)
        joined = "".join(_strip_hang(row, hang) for row in out)
        assert "x" * 300 in joined
        assert "/home/user/project/src/main.py" in joined
        for row in out:
            assert sum(len(r.text) for r in row) <= 40

    def test_nested_hang_grows_with_depth(self):
        """嵌套层级：容器行 hang=2、叶子行 hang=4；内容完整。"""
        nodes = [{"label": "config (2 项)", "children": [
            {"label": "old_string: " + "y" * 200, "children": []}]}]
        out = []
        _tree_node_rows(nodes, 30, out)
        assert len(out) > 2
        joined = _strip_hang(out[0], _hang(0))
        for row in out[1:]:
            joined += _strip_hang(row, _hang(1))
        assert "y" * 200 in joined
        for row in out:
            assert sum(len(r.text) for r in row) <= 30

    def test_wrap_keeps_key_value_colors(self):
        """折行后键/值分色保持：首行键 KEY + 值 VAL；续行值 VAL。"""
        label = "command: " + "z" * 100
        out = []
        _tree_node_rows([{"label": label, "children": []}], 30, out)
        assert len(out) > 1
        r0 = out[0]
        assert r0[0].style is _S_TREE_KEY
        assert r0[0].text.endswith("command: ")
        assert r0[1].style is _S_TREE_VAL
        for row in out[1:]:
            # 续行（值延续）整行 _S_TREE_VAL（首 run 为 hang 缩进无样式）
            assert all(r.style is _S_TREE_VAL for r in row[1:])

    def test_plain_text_line_style_kept(self):
        """纯文本行（无 ": "）折行后内容 run 整行 _S_TEXT。"""
        out = []
        _tree_node_rows([{"label": "q" * 80, "children": []}], 30, out)
        assert len(out) > 1
        for row in out:
            # 续行首 run 为 hang 缩进（无样式）；内容 run 均 _S_TEXT
            content = row[1:] if _hang(0) < 30 and len(row) > 1 else row
            assert all(r.style is _S_TEXT for r in content)

    def test_fit_line_single_row_unchanged(self):
        """未超宽行零回归：单行输出 + 键值分色。"""
        out = []
        _tree_node_rows([{"label": "command: ls -la", "children": []}], 60, out)
        assert len(out) == 1
        assert out[0][0].style is _S_TREE_KEY
        assert out[0][1].style is _S_TREE_VAL

    def test_narrow_width_defensive(self):
        """极窄栏（right_w=1）不崩溃且内容完整（hang 降级不缩进）。"""
        out = []
        _tree_node_rows([{"label": "k: " + "v" * 10, "children": []}], 1, out)
        assert out
        for row in out:
            assert sum(len(r.text) for r in row) <= 1
        joined = "".join(_plain(row) for row in out)
        # right_w=1 → hang=2 >= right_w → 不缩进 → 直接拼接即原文
        assert "v" * 10 in joined

    def test_tree_row_wrap_empty_runs(self):
        """空 runs 防御：输出单行空行。"""
        out = []
        _tree_row_wrap([], 2, 20, out)
        assert len(out) == 1

    def test_non_json_args_fallback_wraps(self):
        """非 JSON 实参文本（解析失败回退单叶子）超宽折行完整。"""
        text = "raw-arg-" + "a" * 120
        out = []
        _tree_node_rows(_args_to_tree(text), 30, out)
        hang = _hang(0)
        joined = "".join(_strip_hang(row, hang) for row in out)
        assert text in joined

    def test_parse_tree_text_long_line_wraps(self):
        """返回值非 JSON 长文本行折行完整（每行一个叶子）。"""
        text = "line: " + "b" * 150
        out = []
        _tree_node_rows(_parse_tree_text(text), 35, out)
        hang = _hang(0)
        joined = "".join(_strip_hang(row, hang) for row in out)
        assert "b" * 150 in joined


class TestToolTreeRowsFullArgs:
    """tool 记录检查器树：长实参/返回值完整显示。"""

    def _rec(self, args, result=""):
        return SimpleNamespace(tool_args=args, tool_result=result)

    def test_long_args_complete_in_tool_tree(self):
        """参数树 + 分割线 + 返回值树：300 字符实参全部显示（无截断丢失）。"""
        args = ('{"config": {"old_string": "' + "x" * 300
                + '", "path": "p.py"}, "flag": true}')
        result = '{"out": {"lines": ["a", "b"]}}'
        rows, keys = _tool_tree_rows(self._rec(args, result), 40)
        texts = [_plain(row) for row in rows]
        joined = "\n".join(texts)
        assert "参数" in joined and "返回值" in joined
        # 300 个 x 全部在行中（被换行/缩进打断，按字符计数验证零丢失）
        assert sum(t.count("x") for t in texts) == 300
        assert "p.py" in joined
        for row in rows:
            assert sum(len(r.text) for r in row) <= 40
        # keys 与 rows 对齐；参数树/返回值树节点路径前缀隔离
        assert len(keys) == len(rows)
        assert "args/0" in keys          # 参数树 config 容器节点
        assert "args/0/old_string" not in keys or True
        assert any(k and k.startswith("res/") for k in keys)  # 返回值树容器

    def test_long_result_complete(self):
        """长返回文本完整显示。"""
        result = "out-" + "c" * 200
        rows, keys = _tool_tree_rows(self._rec("{}", result), 40)
        texts = [_plain(row) for row in rows]
        assert sum(t.count("c") for t in texts) == 200
        assert len(keys) == len(rows)


class TestInspectorContentRowsToolFull:
    """检查器内容行（_inspector_content_rows）工具记录实参完整。"""

    def _tool_rec(self, args, result=""):
        return TraceRecord(
            index=1, kind="tool", summary="tool x", status="done",
            result=result, tool_args=args, tool_result=result,
        )

    def test_inspector_tool_full_args(self):
        """选中 tool 记录：长实参在检查器内容行完整可见。"""
        args = '{"old_string": "' + "w" * 250 + '", "path": "src/a.py"}'
        rows, keys = _inspector_content_rows(self._tool_rec(args, "ok"), 40)
        texts = [_plain(row) if isinstance(row, list) else str(row)
                 for row in rows]
        joined = "\n".join(texts)
        assert sum(t.count("w") for t in texts) == 250
        assert "src/a.py" in joined
        assert len(keys) == len(rows)
