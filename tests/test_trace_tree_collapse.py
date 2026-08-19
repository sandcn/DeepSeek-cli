"""轨迹 Trace 树控件空格展开/收缩测试（2026-08-19 用户需求）。

需求：树控件按空格可以展开和收缩，默认展开所有。

实现固化项：
  1. 树节点折叠状态（``collapsed`` 折叠节点路径 key 集合，默认空 = 全部
     展开）；折叠节点子级行不进入可见列表（与 ink Tree ``_collect_visible``
     同语义），指示符切换 ``▾``（展开）/ ``▸``（折叠）；
  2. 节点路径 key：递归 ``f"{path}/{i}"``（根为 ``"i"``，同数据同 key
     稳定、可区分同 label 兄弟）；参数树 ``"args"`` / 返回值树 ``"res"``
     前缀隔离；``keys`` 与内容行逐行对齐（可折叠节点行 = 路径 key，叶子/
     换行续行/小节标题/分割线 = None）；
  3. 检查器焦点空格 → 切换光标所在节点展开/收缩（叶子/非树行不消费）；
     折叠集合存 model（``trace_tree_collapsed`` / ``trace_tools_tree_collapsed``），
     切换记录/工具/进入子代理/关闭视图时复位（临时浏览状态，默认全展开）；
  4. ``_tool_tree_rows`` / ``_inspector_content_rows`` /
     ``_tools_inspector_content_rows`` 返回 ``(rows, keys)``——keys 供空格
     定位节点；折叠集合纳入 use_memo deps 与树内容缓存键（折叠触发重建）。
"""

from __future__ import annotations

import time as _time
from types import SimpleNamespace

import pytest

from src.tui.app.trace import TraceRecord
from src.tui.ink import h
from src.tui.ink.fiber import InputHook
from src.tui.ink.reconciler import Reconciler


def _render_root(component, props, width=100, height=24):
    rec = Reconciler(schedule_callback=None)
    root = rec.create_root()
    rec.render(root, h(component, props), width, height)
    return rec, root


def _find_input_handler(fiber):
    """查找 fiber 树中第一个活跃 use_input handler。"""
    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    r = _find_input_handler(fiber.child)
    if r is not None:
        return r
    return _find_input_handler(fiber.sibling)


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _plain(row) -> str:
    return "".join(r.text for r in row)


def _nested_nodes():
    """嵌套树：config 容器（2 子级）+ flag 叶子。"""
    return [{"label": "config (2 项)", "children": [
        {"label": "old_string: vvv", "children": []},
        {"label": "path: p.py", "children": []},
    ]}, {"label": "flag: true", "children": []}]


# ═══════════════════════════════════════════════════════════
# 1. 纯函数：树行折叠/展开（_tree_node_rows）
# ═══════════════════════════════════════════════════════════

class TestTreeNodeRowsCollapse:

    def test_default_all_expanded(self):
        """默认（collapsed=None/空）全部展开——子级行全部可见。"""
        from src.tui.app.trace_view import _tree_node_rows
        out, keys = [], []
        _tree_node_rows(_nested_nodes(), 40, out, keys=keys)
        texts = [_plain(r) for r in out]
        assert len(out) == 4  # config + 2 子级 + flag
        assert any("old_string" in t for t in texts)
        assert any("path" in t for t in texts)
        assert keys == ["0", None, None, None]

    def test_collapse_hides_children_with_closed_marker(self):
        """折叠容器节点 → 子级行消失，指示符 ▸。"""
        from src.tui.app.trace_view import _TREE_CLOSED, _tree_node_rows
        out, keys = [], []
        _tree_node_rows(_nested_nodes(), 40, out, collapsed={"0"}, keys=keys)
        assert len(out) == 2  # config(折叠) + flag
        assert _TREE_CLOSED in _plain(out[0])
        texts = [_plain(r) for r in out]
        assert not any("old_string" in t for t in texts)
        assert not any("path" in t for t in texts)
        assert keys == ["0", None]

    def test_expand_restores_children(self):
        """折叠后移除（再展开）→ 子级恢复。"""
        from src.tui.app.trace_view import _TREE_OPEN, _tree_node_rows
        out, keys = [], []
        _tree_node_rows(_nested_nodes(), 40, out, collapsed=set(), keys=keys)
        assert len(out) == 4
        assert _TREE_OPEN in _plain(out[0])

    def test_nested_collapse_key_path(self):
        """深层节点路径 key（0/0/0 形态）——折叠子级后其孙级消失。"""
        from src.tui.app.trace_view import _tree_node_rows
        nodes = [{"label": "a (1 项)", "children": [
            {"label": "b (1 项)", "children": [
                {"label": "c: 1", "children": []}]}]}]
        out, keys = [], []
        _tree_node_rows(nodes, 40, out, keys=keys)
        assert keys == ["0", "0/0", None]
        # 折叠 0/0 → b 的子级 c 消失
        out2, keys2 = [], []
        _tree_node_rows(nodes, 40, out2, collapsed={"0/0"}, keys=keys2)
        assert len(out2) == 2
        assert not any("c:" in _plain(r) for r in out2)
        assert keys2 == ["0", "0/0"]


# ═══════════════════════════════════════════════════════════
# 2. 纯函数：tool 树 / 检查器内容行折叠
# ═══════════════════════════════════════════════════════════

class TestToolTreeCollapse:

    def _rec(self, args='{"config": {"a": {"x": 1}, "b": 2}}',
             result='{"out": {"n": [1, 2]}}'):
        return SimpleNamespace(tool_args=args, tool_result=result)

    def test_tool_tree_rows_fold_args(self):
        """折叠 args/0 → 参数树行减少（返回值树不受影响）。"""
        from src.tui.app.trace_view import _tool_tree_rows
        rows_all, keys_all = _tool_tree_rows(self._rec(), 40)
        rows_fold, keys_fold = _tool_tree_rows(
            self._rec(), 40, {"args/0"},
        )
        assert len(rows_fold) < len(rows_all)
        # 折叠后 args 子级 key 消失；res 树仍展开
        assert "args/0/0" in keys_all   # config → a 容器
        assert "args/0/0" not in keys_fold
        assert "res/0" in keys_fold

    def test_tool_tree_rows_fold_res(self):
        """折叠 res/0 → 返回值树行减少（参数树不受影响）。"""
        from src.tui.app.trace_view import _tool_tree_rows
        rows_all, keys_all = _tool_tree_rows(self._rec(), 40)
        rows_fold, keys_fold = _tool_tree_rows(
            self._rec(), 40, {"res/0"},
        )
        assert len(rows_fold) < len(rows_all)
        assert "res/0/0" in keys_all
        assert "res/0/0" not in keys_fold
        assert "args/0" in keys_fold

    def test_inspector_content_rows_fold(self):
        """检查器内容行折叠 → (rows, keys) 行数减少且 keys 同步。"""
        from src.tui.app.trace_view import _inspector_content_rows
        rec = TraceRecord(
            index=1, kind="tool", summary="t", status="done",
            tool_args='{"config": {"a": 1, "b": 2}}',
            tool_result='{"out": [1, 2]}',
        )
        rows_all, keys_all = _inspector_content_rows(rec, 40)
        rows_fold, keys_fold = _inspector_content_rows(rec, 40, {"args/0"})
        assert len(rows_fold) < len(rows_all)
        assert len(keys_fold) == len(rows_fold)
        assert "args/0" in keys_all
        assert "args/0" in keys_fold  # 容器行仍在（折叠后指示符 ▸）
        assert "args/0/0" not in keys_fold

    def test_content_deps_include_collapsed(self):
        """_inspector_content_deps 含折叠集合（折叠触发重建）。"""
        from src.tui.app.trace_view import _inspector_content_deps
        rec = TraceRecord(
            index=1, kind="tool", summary="t", status="done",
            tool_args='{"config": {"a": 1}}', tool_result="",
        )
        d0 = _inspector_content_deps(rec, 40)
        d1 = _inspector_content_deps(rec, 40, {"args/0"})
        assert d0 != d1
        assert d0[-1] == ()            # 折叠集合展平：空元组
        assert d1[-1] == ("args/0",)   # 折叠集合展平：排序元组


# ═══════════════════════════════════════════════════════════
# 3. 纯函数：工具列表详情视图 schema 树折叠
# ═══════════════════════════════════════════════════════════

class TestToolsViewTreeCollapse:

    def test_tools_content_rows_fold_root(self):
        """折叠根容器（"0"）→ 参数树行消失（描述/标题保留）。"""
        from src.tui.app.trace_tools_view import _tools_inspector_content_rows
        props = {"command": {"type": "string", "description": "d"}}
        rows_all, keys_all = _tools_inspector_content_rows(
            "bash", props, ["command"], "desc", 40,
        )
        rows_fold, keys_fold = _tools_inspector_content_rows(
            "bash", props, ["command"], "desc", 40, {"0"},
        )
        assert len(rows_fold) < len(rows_all)
        assert keys_all.count("0") == 1
        assert "0" in keys_fold  # 容器行仍在（折叠后）
        assert "0/0" in keys_all   # 参数节点
        assert "0/0" not in keys_fold
        # 描述/参数小节标题保留
        texts_fold = [_plain(r) if isinstance(r, list) else str(r)
                      for r in rows_fold]
        assert any("desc" == t for t in texts_fold)
        assert any("\u25b8 \u53c2\u6570" in t for t in texts_fold)


# ═══════════════════════════════════════════════════════════
# 4. model 折叠集合字段
# ═══════════════════════════════════════════════════════════

class TestModelTreeCollapsed:

    def test_defaults_empty(self):
        """默认折叠集合为空 = 全部展开。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        assert model.trace_tree_collapsed == set()
        assert model.trace_tools_tree_collapsed == set()

    def test_reset_display_resets(self):
        """reset_display 复位两处折叠集合。"""
        from src.tui.app.model import AppModel
        model = AppModel()
        model.trace_tree_collapsed = {"args/0"}
        model.trace_tools_tree_collapsed = {"0"}
        model.reset_display()
        assert model.trace_tree_collapsed == set()
        assert model.trace_tools_tree_collapsed == set()


# ═══════════════════════════════════════════════════════════
# 5. TraceView 组件：空格切换展开/收缩
# ═══════════════════════════════════════════════════════════

class TestTraceViewSpaceToggle:

    @pytest.fixture(autouse=True)
    def _pin_records(self, monkeypatch):
        from src.tui.app import trace_view as tv
        rec = TraceRecord(
            index=1, kind="tool", summary="tool x", status="done",
            tool_args='{"config": {"a": 1, "b": 2}}', tool_result="",
        )
        monkeypatch.setattr(tv, "build_trace_records", lambda model: ([rec], [rec]))
        self.rec = rec

    def _setup(self):
        from src.tui.app.model import AppModel
        from src.tui.app.trace_view import TraceView
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_pane = "inspector"
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        return model, _find_input_handler(root)

    def test_space_collapses_and_expands(self):
        """检查器焦点、光标在容器行：空格折叠 → 再空格展开（默认全展开）。"""
        model, handler = self._setup()
        assert model.trace_tree_collapsed == set()  # 默认全展开
        # 光标移到容器行（内容行 1 = args/0）
        assert handler(_ev("char", "j")) is True
        assert model.trace_inspector_cursor == 1
        assert handler(_ev("char", " ")) is True
        assert model.trace_tree_collapsed == {"args/0"}
        assert handler(_ev("char", " ")) is True
        assert model.trace_tree_collapsed == set()

    def test_space_on_non_foldable_not_consumed(self):
        """光标在非可折叠行（参数标题/叶子）：空格不消费（collapsed 不变）。"""
        model, handler = self._setup()
        model.trace_inspector_cursor = 0  # 参数小节标题（key None）
        assert handler(_ev("char", " ")) is False
        assert model.trace_tree_collapsed == set()
        model.trace_inspector_cursor = 2  # 叶子行 a: 1（key None）
        assert handler(_ev("char", " ")) is False
        assert model.trace_tree_collapsed == set()

    def test_ledger_space_not_consumed(self):
        """台账焦点空格不消费（树交互仅检查器焦点）。"""
        model, handler = self._setup()
        model.trace_pane = "ledger"
        assert handler(_ev("char", " ")) is False
        assert model.trace_tree_collapsed == set()

    def test_navigate_resets_collapsed(self, monkeypatch):
        """切换记录（台账导航）→ 折叠集合复位（默认全展开）。"""
        from src.tui.app import trace_view as tv
        from src.tui.app.model import AppModel
        from src.tui.app.trace_view import TraceView
        rec2 = TraceRecord(
            index=2, kind="tool", summary="tool y", status="done",
            tool_args='{"cfg": {"x": 1}}', tool_result="",
        )
        monkeypatch.setattr(tv, "build_trace_records",
                            lambda model: ([self.rec, rec2], [self.rec, rec2]))
        model = AppModel()
        model.fullscreen = "trace"
        model.trace_selected = 0  # 选中首条（j 可下移到第二条触发导航）
        model.trace_tree_collapsed = {"args/0"}
        rec, root = _render_root(TraceView, {"model": model, "width": 100})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True  # 台账导航（pane=ledger）
        assert model.trace_tree_collapsed == set()

    def test_escape_resets_collapsed(self):
        """Esc 关闭/返回 → 折叠集合复位。"""
        model, handler = self._setup()
        model.trace_tree_collapsed = {"args/0"}
        assert handler(_ev("escape")) is True
        assert model.trace_tree_collapsed == set()


# ═══════════════════════════════════════════════════════════
# 6. TraceToolsView 组件：空格切换展开/收缩
# ═══════════════════════════════════════════════════════════

class TestTraceToolsViewSpaceToggle:

    @staticmethod
    def _schemas():
        return [("bash", {"command": {"type": "string", "description": "d"}},
                 ["command"], "desc"),
                ("ls", {"path": {"type": "string"}}, [], "列目录")]

    @pytest.fixture(autouse=True)
    def _pin_schemas(self, monkeypatch):
        from src.tui.app import trace as trace_mod
        monkeypatch.setattr(
            trace_mod, "_tools_schema_cache",
            (_time.monotonic(), self._schemas()),
        )

    def _setup(self):
        from src.tui.app.model import AppModel
        from src.tui.app.trace_tools_view import TraceToolsView
        model = AppModel()
        model.fullscreen = "trace_tools"
        model.trace_tools_pane = "inspector"
        rec, root = _render_root(TraceToolsView, {"model": model, "width": 80})
        return model, _find_input_handler(root)

    def test_space_collapses_and_expands(self):
        """右栏焦点、光标在容器行：空格折叠 → 再空格展开。"""
        model, handler = self._setup()
        assert model.trace_tools_tree_collapsed == set()
        # 右栏内容行：0=desc 1=分割线 2=参数标题 3=参数 (1 项) 容器（key=0）
        for _ in range(3):
            assert handler(_ev("char", "j")) is True
        assert model.trace_tools_cursor == 3
        assert handler(_ev("char", " ")) is True
        assert model.trace_tools_tree_collapsed == {"0"}
        assert handler(_ev("char", " ")) is True
        assert model.trace_tools_tree_collapsed == set()

    def test_space_on_non_foldable_not_consumed(self):
        """光标在非可折叠行（描述/标题/叶子）：空格不消费。"""
        model, handler = self._setup()
        assert handler(_ev("char", " ")) is False  # 描述行（str，key None）
        assert model.trace_tools_tree_collapsed == set()
        model.trace_tools_cursor = 5  # 叶子行（类型）
        assert handler(_ev("char", " ")) is False
        assert model.trace_tools_tree_collapsed == set()

    def test_navigate_resets_collapsed(self):
        """左栏切换工具 → 折叠集合复位（默认全展开）。"""
        from src.tui.app.model import AppModel
        from src.tui.app.trace_tools_view import TraceToolsView
        model = AppModel()
        model.fullscreen = "trace_tools"
        model.trace_tools_tree_collapsed = {"0"}
        rec, root = _render_root(TraceToolsView, {"model": model, "width": 80})
        router = rec._build_input_router(root)
        assert router(_ev("char", "j")) is True  # 左栏导航（pane=ledger）
        assert model.trace_tools_selected == 1
        assert model.trace_tools_tree_collapsed == set()

    def test_escape_resets_collapsed(self):
        """Esc 返回主轨迹 → 折叠集合复位。"""
        model, handler = self._setup()
        model.trace_tools_tree_collapsed = {"0"}
        assert handler(_ev("escape")) is True
        assert model.fullscreen == "trace"
        assert model.trace_tools_tree_collapsed == set()
