"""ToolDAG 单元测试 — 依赖检测、拓扑分层、缓存失效与批量扩展。

覆盖 ``src/core/tool_dag.py`` 的四层依赖检测（显式引用 / 路径重叠 /
user_select 独占 / 工具类别约束）、Kahn 分层、环检测、批间边与节点移除，
以及本次性能/正确性改造：

- ``_add_edge`` 写边后必须失效可达性缓存（``_path_cache``）——否则过期
  「不可达」会被当作事实，可能加出环；
- ``_build_path_indexes`` 的原始顺序索引与 ``original_order`` 下标一致
  （由循环内 ``list.index`` 改为预建映射后的行为等价）；
- 显式引用正则使用模块级编译对象（不再惰性 global）。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core.tool_dag import ToolCallNode, ToolDAG, _TC_ID_REF_RE


class _FakeRegistry:
    """最小元数据注册表：按工具名返回 parallel_safe / requires_terminal / 类别。"""

    def __init__(self, meta: dict | None = None):
        self._meta = meta or {}

    def get_metadata(self, name: str):
        entry = self._meta.get(name)
        if entry is None:
            return None
        return SimpleNamespace(
            parallel_safe=entry.get("parallel_safe", False),
            requires_terminal=entry.get("requires_terminal", False),
            tool_category=entry.get("tool_category", "general"),
        )


def _tc(tc_id: str, name: str, **args) -> dict:
    return {"id": tc_id, "name": name, "arguments": args}


def _edges(dag: ToolDAG) -> set[tuple[str, str]]:
    out = set()
    for tc_id, node in dag.nodes.items():
        for dep in node.dependencies:
            out.add((dep, tc_id))  # (前置, 后继)
    return out


# ── 节点构建 ──────────────────────────────────────────────


def test_build_nodes_reads_metadata():
    registry = _FakeRegistry({
        "bash": {"parallel_safe": False, "requires_terminal": True, "tool_category": "bash"},
        "read_file": {"parallel_safe": True, "tool_category": "read"},
    })
    dag = ToolDAG([_tc("a", "bash"), _tc("b", "read_file")], registry)
    assert dag.size == 2
    assert dag.get_node("a").requires_terminal is True
    assert dag.get_node("a").tool_category == "bash"
    assert dag.get_node("b").parallel_safe is True
    assert dag.original_order == ["a", "b"]


def test_unknown_tool_falls_back_to_defaults():
    dag = ToolDAG([_tc("a", "mystery")], _FakeRegistry())
    node = dag.get_node("a")
    assert node.parallel_safe is False
    assert node.requires_terminal is False
    assert node.tool_category == "general"


# ── 显式依赖 ──────────────────────────────────────────────


def test_explicit_dollar_reference_creates_dependency():
    """引用形如 ``$call_xxx``，tc_id 需与引用字面量一致（LLM 生成的调用 ID）。"""
    dag = ToolDAG([
        _tc("call_a", "mystery_one"),
        _tc("call_b", "mystery_two", content="$call_a"),
    ], _FakeRegistry())
    assert ("call_a", "call_b") in _edges(dag)


def test_explicit_reference_nested_in_list_and_dict():
    dag = ToolDAG([
        _tc("call_a", "mystery_one"),
        _tc("call_b", "mystery_two", payload={"items": ["$call_a"]}),
    ], _FakeRegistry())
    assert ("call_a", "call_b") in _edges(dag)


def test_explicit_reference_ignores_unknown_ids():
    dag = ToolDAG([_tc("call_a", "t", v="$call_nope")], _FakeRegistry())
    assert _edges(dag) == set()


def test_explicit_reference_skips_self_reference():
    dag = ToolDAG([_tc("call_a", "t", v="$call_a")], _FakeRegistry())
    assert _edges(dag) == set()


def test_reference_regex_is_module_level_compiled():
    assert _TC_ID_REF_RE.pattern == r"\$(call_[a-zA-Z0-9_]+)"
    # 正则含捕获组 → findall 返回组内容
    assert _TC_ID_REF_RE.findall("x $call_ab_1 y") == ["call_ab_1"]


# ── 路径重叠 ──────────────────────────────────────────────


def test_read_depends_on_write_same_path():
    dag = ToolDAG([
        _tc("w", "write_file", path="a.txt"),
        _tc("r", "read_file", path="a.txt"),
    ], _FakeRegistry({"write_file": {"tool_category": "write"},
                      "read_file": {"tool_category": "read"}}))
    assert ("w", "r") in _edges(dag)


def test_same_path_writes_serialize_in_original_order():
    dag = ToolDAG([
        _tc("w1", "write_file", path="a.txt"),
        _tc("w2", "write_file", path="a.txt"),
    ], _FakeRegistry({"write_file": {"tool_category": "write"}}))
    assert ("w1", "w2") in _edges(dag)
    assert ("w2", "w1") not in _edges(dag)


def test_parent_child_path_dependency():
    dag = ToolDAG([
        _tc("d", "mkdir", path="out"),
        _tc("f", "write_file", path="out/a.txt"),
    ], _FakeRegistry({"mkdir": {"tool_category": "write"},
                      "write_file": {"tool_category": "write"}}))
    assert ("d", "f") in _edges(dag)


def test_cp_reads_source_and_writes_destination():
    dag = ToolDAG([
        _tc("cp", "cp", source="s.txt", destination="d.txt"),
        _tc("r", "read_file", path="d.txt"),
    ], _FakeRegistry({"cp": {"tool_category": "write"},
                      "read_file": {"tool_category": "read"}}))
    # 读 d.txt 依赖 cp 写 d.txt
    assert ("cp", "r") in _edges(dag)


# ── user_select 独占层 ────────────────────────────────────


def test_user_select_precedes_other_tools_but_concurrent_with_each_other():
    dag = ToolDAG([
        _tc("u1", "user_select", question="q1"),
        _tc("u2", "user_select", question="q2"),
        _tc("r", "read_file", path="a.txt"),
    ], _FakeRegistry({"read_file": {"tool_category": "read"}}))
    edges = _edges(dag)
    assert ("u1", "r") in edges
    assert ("u2", "r") in edges
    # 并发需求：多个 user_select 之间不再串行
    assert ("u1", "u2") not in edges
    assert ("u2", "u1") not in edges


def test_concurrent_user_select_share_one_layer():
    dag = ToolDAG([
        _tc("u1", "user_select"),
        _tc("u2", "user_select"),
        _tc("r", "read_file", path="a.txt"),
    ], _FakeRegistry({"read_file": {"tool_category": "read"}}))
    layers = dag.topological_sort()
    assert layers is not None
    layer_of = {tc: idx for idx, layer in enumerate(layers) for tc in layer}
    assert layer_of["u1"] == layer_of["u2"]
    assert layer_of["r"] > layer_of["u1"]


# ── 工具类别约束 ──────────────────────────────────────────


def test_bash_nodes_chain_in_original_order():
    dag = ToolDAG([
        _tc("b1", "bash", command="x"),
        _tc("b2", "bash", command="y"),
        _tc("b3", "bash", command="z"),
    ], _FakeRegistry({"bash": {"tool_category": "bash"}}))
    edges = _edges(dag)
    assert ("b1", "b2") in edges
    assert ("b2", "b3") in edges
    layers = dag.topological_sort()
    assert layers is not None
    assert [len(layer) for layer in layers] == [1, 1, 1]


def test_non_bash_precedes_bash():
    dag = ToolDAG([
        _tc("b", "bash", command="x"),
        _tc("r", "read_file", path="a.txt"),
    ], _FakeRegistry({"bash": {"tool_category": "bash"},
                      "read_file": {"tool_category": "read"}}))
    assert ("r", "b") in _edges(dag)


def test_read_precedes_write_by_category():
    dag = ToolDAG([
        _tc("w", "write_file", path="x.txt"),
        _tc("r", "read_file", path="y.txt"),
    ], _FakeRegistry({"write_file": {"tool_category": "write"},
                      "read_file": {"tool_category": "read"}}))
    assert ("r", "w") in _edges(dag)


def test_general_category_node_has_no_category_constraints():
    dag = ToolDAG([
        _tc("g", "mystery"),
        _tc("r", "read_file", path="a.txt"),
    ], _FakeRegistry({"read_file": {"tool_category": "read"}}))
    assert _edges(dag) == set()


# ── 拓扑排序 / 环 ─────────────────────────────────────────


def test_topological_sort_assigns_layers_and_order():
    """同路径「读依赖写」→ 写先于读（路径重叠优先于类别默认顺序）。"""
    registry = _FakeRegistry({
        "read_file": {"tool_category": "read"},
        "write_file": {"tool_category": "write"},
    })
    dag = ToolDAG([
        _tc("r", "read_file", path="a.txt"),
        _tc("w", "write_file", path="a.txt"),
    ], registry)
    layers = dag.topological_sort()
    assert layers == [["w"], ["r"]]
    assert dag.get_level("w") == 0
    assert dag.get_level("r") == 1


def test_topological_sort_empty_dag():
    dag = ToolDAG([], _FakeRegistry())
    assert dag.topological_sort() == []


def test_cycle_detection_returns_none():
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t")], _FakeRegistry())
    dag._add_edge("a", "b")
    dag._add_edge("b", "a")
    assert dag.has_cycle() is True
    assert dag.topological_sort() is None


def test_acyclic_dag_has_no_cycle():
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t")], _FakeRegistry())
    dag._add_edge("a", "b")
    assert dag.has_cycle() is False


# ── _add_edge / 缓存失效（本次修复的核心） ────────────────


def test_add_edge_is_idempotent_and_records_both_directions():
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t")], _FakeRegistry())
    dag._add_edge("a", "b")
    dag._add_edge("a", "b")
    assert dag.get_node("b").dependencies == {"a"}
    assert dag.get_node("a").dependents == {"b"}


def test_add_edge_ignores_missing_nodes():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    dag._add_edge("a", "ghost")
    dag._add_edge("ghost", "a")
    assert _edges(dag) == set()


def test_add_edge_invalidates_stale_reachability_cache():
    """写边后 _path_cache 必须失效——否则过期的「不可达」会掩盖新路径。"""
    dag = ToolDAG(
        [_tc("a", "t"), _tc("b", "t"), _tc("c", "t")],
        _FakeRegistry(),
    )
    dag._add_edge("a", "b")
    # 先写缓存：(a, c) 不可达
    assert dag._path_exists("a", "c") is False
    # 新增 b → c：a 现在可达 c，缓存必须已被失效
    dag._add_edge("b", "c")
    assert dag._path_exists("a", "c") is True
    assert dag._path_cache.get(("a", "c")) is True


def test_add_edge_noop_keeps_cache():
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t")], _FakeRegistry())
    dag._add_edge("a", "b")
    assert dag._path_exists("a", "b") is True
    cached = dict(dag._path_cache)
    dag._add_edge("a", "b")  # 幂等：不应清缓存
    assert dag._path_cache == cached


def test_path_exists_handles_missing_nodes_and_self():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    dag._add_edge("a", "a")  # 自环边（理论上不会由约束产生）
    assert dag._path_exists("a", "a") is True


# ── 路径索引的原始顺序映射 ────────────────────────────────


def test_path_index_order_matches_original_order():
    """``_build_path_indexes`` 的 order 必须等于 original_order 下标。"""
    registry = _FakeRegistry({"write_file": {"tool_category": "write"}})
    dag = ToolDAG([
        _tc("w1", "write_file", path="a.txt"),
        _tc("w2", "write_file", path="b.txt"),
        _tc("w3", "write_file", path="c.txt"),
    ], registry)
    write_paths, read_paths, delete_paths, has_any = dag._collect_node_paths()
    write_index, _, _, _ = dag._build_path_indexes(
        write_paths, read_paths, delete_paths, has_any)
    assert write_index  # 三条不同路径均有写入者
    for entries in write_index.values():
        for tc_id, order in entries:
            assert order == dag.original_order.index(tc_id)
    # 不同路径 → 无相互依赖（类别约束同属 write，不产生边）
    assert _edges(dag) == set()


def test_same_path_writes_serialize_with_order_from_index():
    registry = _FakeRegistry({"write_file": {"tool_category": "write"}})
    dag = ToolDAG([
        _tc("w1", "write_file", path="a.txt"),
        _tc("w2", "write_file", path="a.txt"),
    ], registry)
    assert ("w1", "w2") in _edges(dag)


# ── 批量扩展 ──────────────────────────────────────────────


def test_add_batch_creates_cross_batch_edges():
    registry = _FakeRegistry({"read_file": {"tool_category": "read"}})
    dag = ToolDAG([_tc("a", "read_file", path="x.txt")], registry)
    dag.add_batch([_tc("b", "read_file", path="y.txt")], registry,
                  prev_non_dispatch_ids={"a"})
    assert "b" in dag.nodes
    assert dag.original_order == ["a", "b"]
    assert ("a", "b") in _edges(dag)


def test_add_batch_without_prev_ids_adds_no_cross_edges():
    registry = _FakeRegistry()
    dag = ToolDAG([_tc("a", "t")], registry)
    dag.add_batch([_tc("b", "t")], registry)
    assert _edges(dag) == set()


def test_add_batch_empty_is_noop():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    dag.add_batch([], _FakeRegistry())
    assert dag.original_order == ["a"]


def test_add_batch_clears_path_cache():
    registry = _FakeRegistry()
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t")], registry)
    dag._path_exists("a", "b")
    assert dag._path_cache
    dag.add_batch([_tc("c", "t")], registry)
    assert dag._path_cache == {}


def test_add_batch_skips_removed_prev_ids():
    registry = _FakeRegistry()
    dag = ToolDAG([_tc("a", "t")], registry)
    dag.remove_nodes({"a"})
    dag.add_batch([_tc("b", "t")], registry, prev_non_dispatch_ids={"a"})
    assert _edges(dag) == set()


# ── 节点移除 ──────────────────────────────────────────────


def test_remove_nodes_cleans_both_directions():
    dag = ToolDAG([_tc("a", "t"), _tc("b", "t"), _tc("c", "t")], _FakeRegistry())
    dag._add_edge("a", "b")
    dag._add_edge("b", "c")
    dag.remove_nodes({"b"})
    assert dag.size == 2
    assert dag.original_order == ["a", "c"]
    assert dag.get_node("a").dependents == set()
    assert dag.get_node("c").dependencies == set()
    assert dag._path_cache == {}


def test_remove_nodes_ignores_unknown_and_empty():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    dag.remove_nodes(set())
    dag.remove_nodes({"ghost"})
    assert dag.size == 1


def test_remove_nodes_returns_none_typed_node():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    dag.remove_nodes({"a"})
    assert dag.get_node("a") is None
    assert dag.get_level("a") == -1


# ── 参数扫描 ──────────────────────────────────────────────


def test_scan_args_handles_non_string_values():
    dag = ToolDAG([_tc("a", "t", n=1, f=1.5, b=True, none=None)], _FakeRegistry())
    node = dag.get_node("a")
    assert dag._scan_args_for_refs(node.arguments, _TC_ID_REF_RE) == set()


def test_extract_tool_paths_classifies_read_write_delete():
    wp, rp, dp = ToolDAG._extract_tool_paths("rm", {"path": "a.txt"})
    assert dp and not wp and not rp
    wp, rp, dp = ToolDAG._extract_tool_paths("mv", {"source": "a", "destination": "b"})
    assert wp and dp and not rp
    wp, rp, dp = ToolDAG._extract_tool_paths("cp", {"source": "a", "destination": "b"})
    assert wp and rp and not dp
    wp, rp, dp = ToolDAG._extract_tool_paths("bash", {"command": "ls"})
    assert not wp and not rp and not dp


def test_tool_call_node_defaults():
    node = ToolCallNode("id", "name", {}, True, False)
    assert node.tool_category == "general"
    assert node.dependencies == set()
    assert node.dependents == set()
    assert node.layer == -1


def test_nodes_property_returns_copy():
    dag = ToolDAG([_tc("a", "t")], _FakeRegistry())
    snapshot = dag.nodes
    snapshot.pop("a")
    assert dag.size == 1


@pytest.mark.parametrize("count", [1, 5, 20])
def test_layer_count_bounded_by_bash_chain(count):
    registry = _FakeRegistry({"bash": {"tool_category": "bash"}})
    dag = ToolDAG([_tc(f"b{i}", "bash", command=str(i)) for i in range(count)], registry)
    layers = dag.topological_sort()
    assert layers is not None
    assert len(layers) == count  # bash 链式串行 → 每个一层
