"""Tests for src/core/tool_dag.py — ToolDAG 引擎

覆盖内容：
  1. 显式依赖检测（$tool_call_id 引用）
  2. 隐式依赖检测（path 重叠）
  3. user_select 独占层约束
  4. Kahn 拓扑排序（链/钻石/扇出/扇入/全独立）
  5. 环检测（DFS 着色法）
  6. 空列表/边界场景
  7. 混合场景综合验证
"""

import pytest
from unittest.mock import MagicMock

from typing import Optional

from src.core.tool_dag import ToolDAG, ToolCallNode


# ═══════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════

def _meta(parallel_safe=False, requires_terminal=False, tool_category="general"):
    """创建 mock metadata 对象"""
    m = MagicMock()
    m.parallel_safe = parallel_safe
    m.requires_terminal = requires_terminal
    m.tool_category = tool_category
    return m


def _make_registry(metadata_map: "Optional[dict[str, tuple]]" = None):
    """创建 mock registry

    Args:
        metadata_map: 工具名 → 元组映射，支持两种格式：
            - tuple[bool, bool]: (parallel_safe, requires_terminal) 旧格式
            - tuple[bool, bool, str]: (parallel_safe, requires_terminal, tool_category) 新格式
    """
    registry = MagicMock()

    def get_metadata(name):
        if metadata_map and name in metadata_map:
            entry = metadata_map[name]
            if len(entry) == 3:
                ps, rt, tc = entry
                return _meta(parallel_safe=ps, requires_terminal=rt, tool_category=tc)
            else:
                ps, rt = entry
                return _meta(parallel_safe=ps, requires_terminal=rt)
        # 默认（旧格式，tool_category 使用 _meta 默认值 "general"）
        defaults = {
            "read_file": (True, False),
            "search": (True, False),
            "find": (True, False),
            "ls": (True, False),
            "web_search": (True, False),
            "write_file": (False, False),
            "update_file": (False, False),
            "bash": (False, False),
            "cp": (False, False),
            "mv": (False, False),
            "rm": (False, False),
            "mkdir": (False, False),
            "dispatch_agent": (False, False),
            "user_select": (False, True),
        }
        if name in defaults:
            ps, rt = defaults[name]
            return _meta(parallel_safe=ps, requires_terminal=rt)
        return None

    registry.get_metadata = MagicMock(side_effect=get_metadata)
    return registry


# ═══════════════════════════════════════════════════════════════
# 节点构建
# ═══════════════════════════════════════════════════════════════

class TestToolCallNode:
    """ToolCallNode dataclass 基础"""

    def test_create_node(self):
        """创建节点并设置属性"""
        node = ToolCallNode(
            tc_id="call_1",
            name="read_file",
            arguments={"path": "/a.txt"},
            parallel_safe=True,
            requires_terminal=False,
        )
        assert node.tc_id == "call_1"
        assert node.name == "read_file"
        assert node.arguments == {"path": "/a.txt"}
        assert node.parallel_safe is True
        assert node.requires_terminal is False
        assert node.dependencies == set()
        assert node.dependents == set()
        assert node.layer == -1

    def test_node_defaults(self):
        """验证默认值"""
        node = ToolCallNode(
            tc_id="call_1", name="test",
            arguments={}, parallel_safe=False,
            requires_terminal=False,
        )
        assert node.dependencies == set()
        assert node.dependents == set()
        assert node.layer == -1


# ═══════════════════════════════════════════════════════════════
# DAG 构建 — 显式依赖
# ═══════════════════════════════════════════════════════════════

class TestExplicitDependencies:
    """$tool_call_id 显式引用检测"""

    def test_simple_ref(self):
        """参数中直接引用 $call_id"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "hello"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "$call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.size == 2
        node_b = dag.get_node("call_B")
        assert "call_A" in node_b.dependencies
        assert "call_B" in dag.get_node("call_A").dependents

    def test_ref_in_nested_dict(self):
        """嵌套 dict 中的引用"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "out.txt", "content": "result: $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_ref_in_list(self):
        """list 中的引用"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo", "args": ["$call_A", "static"]}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_ref_in_nested_list_dict(self):
        """list of dict 中的引用"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"items": [{"ref": "$call_A", "name": "test"}]}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_no_false_positive(self):
        """普通 $ 前缀字符串不误判"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $HOME"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "$file_path"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # 两节点应无依赖（$HOME 和 $file_path 不是 call_xxx 模式）
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0

    def test_self_ref_ignored(self):
        """工具引用自身 ID 应被忽略"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert len(dag.get_node("call_A").dependencies) == 0

    def test_multiple_refs(self):
        """一个工具引用多个工具"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "a"}},
            {"id": "call_B", "name": "search", "arguments": {"query": "b"}},
            {"id": "call_C", "name": "write_file",
             "arguments": {"path": "out.txt",
                           "content": "$call_A $call_B"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        deps = dag.get_node("call_C").dependencies
        assert "call_A" in deps
        assert "call_B" in deps


# ═══════════════════════════════════════════════════════════════
# DAG 构建 — 隐式依赖（path 重叠）
# ═══════════════════════════════════════════════════════════════

class TestImplicitDependencies:
    """path 重叠隐式依赖"""

    def test_read_depends_on_write_same_path(self):
        """read(path=X) + write(path=X) → read 依赖 write"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/test.txt", "content": "hello"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/test.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_write_depends_on_write_same_path(self):
        """write(path=X) + write(path=X) → 后者依赖前者"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/test.txt", "content": "first"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/test.txt", "content": "second"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_read_read_no_dep(self):
        """read(path=X) + read(path=X) → 无依赖（并行安全）"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "/tmp/test.txt"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/test.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0

    def test_different_path_no_dep(self):
        """不同路径 → 无依赖"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/a.txt", "content": "hello"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/b.txt", "content": "world"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0

    def test_write_then_update_same_path(self):
        """write(path=X) + update(path=X) → update 依赖 write"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/test.txt", "content": "hello"}},
            {"id": "call_B", "name": "update_file",
             "arguments": {"path": "/tmp/test.txt",
                           "old_string": "hello", "new_string": "world"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_no_path_arg(self):
        """无 path 参数的工具不产生隐式依赖"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo hi"}},
            {"id": "call_B", "name": "search",
             "arguments": {"query": "hello"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0

    # ── cp/mv 多路径依赖检测 ───────────────────────────────

    def test_cp_destination_overlap_with_mkdir(self):
        """cp(dst=X) + mkdir(path=X) → cp 依赖 mkdir（写同一目标路径）"""
        tool_calls = [
            {"id": "call_A", "name": "mkdir",
             "arguments": {"path": "/tmp/outdir", "parents": True}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/outdir"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_cp_destination_overlap_with_rm(self):
        """cp(dst=X) + rm(path=X) → 后者依赖前者（写同一目标路径）"""
        tool_calls = [
            {"id": "call_A", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/out.txt"}},
            {"id": "call_B", "name": "rm",
             "arguments": {"path": "/tmp/out.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_cp_destination_overlap_with_cp(self):
        """cp(dst=X) + cp(dst=X) → 后者依赖前者（写同一目标路径）"""
        tool_calls = [
            {"id": "call_A", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/out.txt"}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/b.txt", "destination": "/tmp/out.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_cp_source_is_read_only(self):
        """cp(source=X) 只读 source，不写 source → 不对 source 产生写冲突"""
        tool_calls = [
            {"id": "call_A", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/b.txt"}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/c.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # 两个 cp 都读 source "/tmp/a.txt"，不写它 → 无依赖
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0

    def test_cp_read_source_depends_on_write(self):
        """cp(source=X) 读 X + write_file(path=X) 写 X → cp 依赖 write_file"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/a.txt", "content": "hello"}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/b.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_cp_destination_overlap_with_write_file(self):
        """cp(dst=X) + write_file(path=X) → 按原始顺序串行化"""
        tool_calls = [
            {"id": "call_A", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/out.txt"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "data"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_mv_destination_overlap_with_cp(self):
        """mv(dst=X) + cp(dst=X) → 后者依赖前者"""
        tool_calls = [
            {"id": "call_A", "name": "mv",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/out.txt"}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/b.txt", "destination": "/tmp/out.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_mv_source_is_write(self):
        """mv(source=X) 删除 source → read_file(path=X) 依赖 mv"""
        tool_calls = [
            {"id": "call_A", "name": "mv",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/b.txt"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/a.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_mkdir_cp_rm_same_target_serialized(self):
        """mkdir(path=DIR) + cp(src, dst=DIR/sub/) + rm(path=DIR/sub/)
        mkdir 创建父目录 → cp 写子路径（父子路径依赖）→ rm 同子路径（精确匹配）
        完全串行化三层"""
        tool_calls = [
            {"id": "call_A", "name": "mkdir",
             "arguments": {"path": "/tmp/mydir", "parents": True}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/x.txt",
                           "destination": "/tmp/mydir/sub/"}},
            {"id": "call_C", "name": "rm",
             "arguments": {"path": "/tmp/mydir/sub/", "recursive": True}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        # call_A (mk /tmp/mydir) 是父目录 → call_B (cp → /tmp/mydir/sub/) 依赖 call_A
        # call_C (rm /tmp/mydir/sub/) 与 call_B 同精确路径 → 依赖 call_B
        # 并且 call_C 也因父子路径依赖 call_A
        assert len(layers) == 3
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]
        assert layers[2] == ["call_C"]

    def test_cp_and_mk_different_paths_parallel(self):
        """cp(dst=X) + mk(path=Y) → 不同路径，可并行"""
        tool_calls = [
            {"id": "call_A", "name": "cp",
             "arguments": {"source": "/tmp/a.txt", "destination": "/tmp/out1.txt"}},
            {"id": "call_B", "name": "mkdir",
             "arguments": {"path": "/tmp/otherdir"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}

    def test_cp_read_write_with_write_same_path(self):
        """write(path=X) + cp(src=X, dst=Y) + read(path=Y)
        写 X → cp 读 X 写 Y → 读 Y，三层全串行"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/data.txt", "content": "hello"}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/data.txt",
                           "destination": "/tmp/copy.txt"}},
            {"id": "call_C", "name": "read_file",
             "arguments": {"path": "/tmp/copy.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3
        # call_A 写 data.txt → call_B 读 data.txt（依赖 call_A），写 copy.txt → call_C 读 copy.txt（依赖 call_B）
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]
        assert layers[2] == ["call_C"]

    # ── 父子路径依赖检测 ───────────────────────────────────

    def test_parent_dir_mkdir_before_child_cp(self):
        """mkdir(path=DIR) + cp(dst=DIR/file) → cp 依赖 mkdir（父子路径，父目录先创建）"""
        tool_calls = [
            {"id": "call_A", "name": "mkdir",
             "arguments": {"path": "/tmp/mydir", "parents": True}},
            {"id": "call_B", "name": "cp",
             "arguments": {"source": "/tmp/x.txt",
                           "destination": "/tmp/mydir/file.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_parent_dir_mkdir_before_child_write(self):
        """mkdir(path=DIR) + write_file(path=DIR/sub/a.txt) → write_file 依赖 mkdir"""
        tool_calls = [
            {"id": "call_A", "name": "mkdir",
             "arguments": {"path": "/tmp/mydir", "parents": True}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/mydir/sub/a.txt", "content": "data"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_parent_dir_read_after_child_write(self):
        """write_file(path=DIR/sub/x) + read_file(path=DIR) → read_file 不依赖 write_file
        （读父目录不需要子目录文件已写入；父子路径只处理 child→parent 方向）"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/mydir/sub/x.txt", "content": "data"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/mydir"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # 父子路径检测只处理"child 的写入/读取依赖 parent 的写入"，不支持 parent→child 反向
        assert "call_A" not in dag.get_node("call_B").dependencies

    def test_sibling_paths_no_parent_child_dep(self):
        """同一父目录下的兄弟路径 → 无父子路径依赖"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/mydir/a.txt", "content": "hello"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/mydir/b.txt", "content": "world"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # 兄弟路径，无父子关系，写不同文件可并行
        assert len(dag.get_node("call_A").dependencies) == 0
        assert len(dag.get_node("call_B").dependencies) == 0


# ═══════════════════════════════════════════════════════════════
# user_select 独占层约束
# ═══════════════════════════════════════════════════════════════

class TestUserSelectConstraint:
    """user_select 独占终端层"""

    def test_user_select_blocks_all(self):
        """user_select 存在时，所有其他节点依赖 user_select"""
        tool_calls = [
            {"id": "call_A", "name": "user_select",
             "arguments": {"title": "pick", "options": ["a", "b"]}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "test.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # call_B 依赖 call_A
        assert "call_A" in dag.get_node("call_B").dependencies

    def test_user_select_in_own_layer(self):
        """user_select 独占一层（拓扑排序后）"""
        tool_calls = [
            {"id": "call_A", "name": "user_select",
             "arguments": {"title": "pick", "options": ["a", "b"]}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "test.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        # user_select 应在第一层
        assert layers[0] == ["call_A"]
        # read_file 应在第二层（依赖 user_select）
        assert layers[1] == ["call_B"]

    def test_no_user_select_no_constraint(self):
        """无 user_select 时无额外约束"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "a.txt"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "b.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        # 应在同一层
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}


# ═══════════════════════════════════════════════════════════════
# 拓扑排序
# ═══════════════════════════════════════════════════════════════

class TestTopologicalSort:
    """Kahn 算法拓扑排序"""

    def test_no_deps_all_same_layer(self):
        """全独立工具 → 单层"""
        tool_calls = [
            {"id": "call_A", "name": "read_file", "arguments": {"path": "a.txt"}},
            {"id": "call_B", "name": "read_file", "arguments": {"path": "b.txt"}},
            {"id": "call_C", "name": "read_file", "arguments": {"path": "c.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B", "call_C"}

    def test_chain(self):
        """链状 A→B→C → 三层"""
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]
        assert layers[2] == ["call_C"]

    def test_diamond(self):
        """钻石 A→{B,C}→D → 三层"""
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_D", "name": "bash",
             "arguments": {"command": "echo $call_B $call_C"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3
        assert layers[0] == ["call_A"]
        assert set(layers[1]) == {"call_B", "call_C"}
        assert layers[2] == ["call_D"]

    def test_fan_out(self):
        """扇出 A→{B,C,D} → 两层"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "$call_A"}},
            {"id": "call_C", "name": "read_file",
             "arguments": {"path": "$call_A"}},
            {"id": "call_D", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]
        assert set(layers[1]) == {"call_B", "call_C", "call_D"}

    def test_fan_in(self):
        """扇入 {A,B}→C → 两层"""
        tool_calls = [
            {"id": "call_A", "name": "search", "arguments": {"query": "x"}},
            {"id": "call_B", "name": "search", "arguments": {"query": "y"}},
            {"id": "call_C", "name": "write_file",
             "arguments": {"path": "out.txt", "content": "$call_A $call_B"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert set(layers[0]) == {"call_A", "call_B"}
        assert layers[1] == ["call_C"]

    def test_empty(self):
        """空列表"""
        dag = ToolDAG([], _make_registry())
        assert dag.size == 0
        layers = dag.topological_sort()
        assert layers == []

    def test_single_node(self):
        """单节点"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "test.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert layers[0] == ["call_A"]

    def test_mixed_all_types(self):
        """混合场景：显式+隐式+user_select"""
        tool_calls = [
            {"id": "call_US", "name": "user_select",
             "arguments": {"title": "pick", "options": ["a", "b"]}},
            {"id": "call_S", "name": "search",
             "arguments": {"query": "find_me"}},
            {"id": "call_R", "name": "read_file",
             "arguments": {"path": "$call_S"}},
            {"id": "call_W", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "$call_R"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "cat /tmp/out.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        # Layer 0: user_select 独占终端（所有其他节点依赖它）
        assert layers[0] == ["call_US"]
        # Layer 1: search + bash 仅依赖 user_select，无其他依赖
        assert set(layers[1]) == {"call_S", "call_B"}
        # Layer 2: read_file 依赖 call_S（显式）
        assert layers[2] == ["call_R"]
        # Layer 3: write_file 依赖 call_R（显式）
        assert layers[3] == ["call_W"]
        # bash "cat /tmp/out.txt" 无 path 参数，不产生隐式依赖
        # 所以 bash 在 Layer 1，write_file 在 Layer 3（显式链 user→search→read→write）
        assert len(layers) == 4


# ═══════════════════════════════════════════════════════════════
# 环检测
# ═══════════════════════════════════════════════════════════════

class TestCycleDetection:
    """DFS 着色法环检测"""

    def test_no_cycle(self):
        """无环 DAG"""
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.has_cycle() is False

    def test_simple_cycle(self):
        """A→B→A 环"""
        # 手动构造：bash A 依赖 bash B（$call_B），bash B 依赖 bash A（$call_A）
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.has_cycle() is True

    def test_three_node_cycle(self):
        """A→B→C→A 三节点环"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_C"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.has_cycle() is True

    def test_self_ref_ignored_no_cycle(self):
        """A→A 自引用（工具引用自身 ID）被 DAG 忽略，不产生环"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        # _detect_explicit_deps 会跳过 ref_id == tc_id 的情况
        assert dag.has_cycle() is False

    def test_cycle_returns_none(self):
        """有环时 topological_sort 返回 None"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.topological_sort() is None


# ═══════════════════════════════════════════════════════════════
# 边界场景
# ═══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """边界场景"""

    def test_unknown_tool_metadata(self):
        """metadata 查询返回 None 的工具使用默认值"""
        tool_calls = [
            {"id": "call_A", "name": "unknown_tool",
             "arguments": {"arg": "val"}},
        ]
        registry = MagicMock()
        registry.get_metadata = MagicMock(return_value=None)
        dag = ToolDAG(tool_calls, registry)
        assert dag.size == 1
        node = dag.get_node("call_A")
        assert node.parallel_safe is False  # 默认
        assert node.requires_terminal is False  # 默认

    def test_metadata_query_exception(self):
        """metadata 查询异常时使用默认值"""
        tool_calls = [
            {"id": "call_A", "name": "broken_tool",
             "arguments": {"arg": "val"}},
        ]
        registry = MagicMock()
        registry.get_metadata = MagicMock(side_effect=RuntimeError("broken"))
        dag = ToolDAG(tool_calls, registry)
        assert dag.size == 1
        node = dag.get_node("call_A")
        assert node.parallel_safe is False  # 异常时默认

    def test_layer_assignment(self):
        """拓扑排序后 layer 正确赋值"""
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo $call_A"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo $call_B"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        dag.topological_sort()
        assert dag.get_node("call_A").layer == 0
        assert dag.get_node("call_B").layer == 1
        assert dag.get_node("call_C").layer == 2

    def test_get_level(self):
        """get_level 返回正确的层号"""
        tool_calls = [
            {"id": "call_A", "name": "bash", "arguments": {"command": "echo 1"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        assert dag.get_level("call_A") == -1  # 未排序
        dag.topological_sort()
        assert dag.get_level("call_A") == 0

    def test_get_level_unknown(self):
        """不存在的 tc_id 返回 -1"""
        dag = ToolDAG([], _make_registry())
        assert dag.get_level("nonexistent") == -1

    def test_nodes_property(self):
        """nodes 属性返回副本"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "a.txt"}},
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        nodes = dag.nodes
        assert "call_A" in nodes
        # 修改返回的 dict 不应影响原始数据
        nodes["new"] = MagicMock()
        assert "new" not in dag._nodes

    def test_large_independent_set(self):
        """100 个独立工具 → 单层"""
        tool_calls = [
            {"id": f"call_{i}", "name": "read_file",
             "arguments": {"path": f"/tmp/file_{i}.txt"}}
            for i in range(100)
        ]
        dag = ToolDAG(tool_calls, _make_registry())
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert len(layers[0]) == 100


# ═══════════════════════════════════════════════════════════════
# 工具类别约束
# ═══════════════════════════════════════════════════════════════

class TestToolCategoryConstraints:
    """工具类别调度约束（层 D）

    覆盖 4 条规则：
    - 规则 a：bash ↔ non-bash 双向隔断（non-bash 在前）
    - 规则 b：bash → bash 链式串行（按原始顺序）
    - 规则 c：read → write 默认边
    - 规则 d：防环保护（已有反向路径时跳过）
    """

    # ── 规则 a：bash ↔ non-bash 双向隔断 ─────────────────────

    def test_bash_isolated_from_reads(self):
        """bash + read_file → bash 在单独层"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "/tmp/a.txt"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "cat /tmp/a.txt"}},
        ]
        meta = {
            "read_file": (True, False, "read"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]   # read_file 在前层
        assert layers[1] == ["call_B"]   # bash 在单独后层

    def test_bash_isolated_from_writes(self):
        """bash + write_file → bash 在单独层"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "data"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "cat /tmp/out.txt"}},
        ]
        meta = {
            "write_file": (False, False, "write"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]   # write_file 在前层
        assert layers[1] == ["call_B"]   # bash 在单独后层

    def test_bash_isolated_from_interactive(self):
        """user_select (interactive) + bash → 不同层"""
        tool_calls = [
            {"id": "call_A", "name": "user_select",
             "arguments": {"title": "pick", "options": ["a"]}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo done"}},
        ]
        meta = {
            "user_select": (False, True, "interactive"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        # user_select 独占终端 + interactive 类别约束 → bash 在后层
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]

    # ── 规则 b：bash → bash 链式串行 ───────────────────────

    def test_bash_chain_serial(self):
        """3 个 bash → 三层逐层串行"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo 2"}},
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo 3"}},
        ]
        meta = {
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]
        assert layers[2] == ["call_C"]

    def test_bash_single_per_layer(self):
        """每个层最多一个 bash（链式串行）"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo 2"}},
        ]
        meta = {
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        # 各层恰好一个 bash
        for layer in layers:
            assert len(layer) == 1

    def test_single_bash_no_chain(self):
        """单独一个 bash 不产生链式边"""
        tool_calls = [
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo hi"}},
        ]
        meta = {
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert layers[0] == ["call_A"]

    # ── 规则 c：read → write 默认边 ─────────────────────────

    def test_read_before_write(self):
        """read_file + write_file → read 在前层，write 在后层"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "/tmp/a.txt"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/b.txt", "content": "data"}},
        ]
        meta = {
            "read_file": (True, False, "read"),
            "write_file": (False, False, "write"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        # read_file (read) → write_file (write) 由类别约束添加
        # 同时路径不同，无路径重叠干扰
        assert len(layers) == 2
        assert layers[0] == ["call_A"]   # read 在前
        assert layers[1] == ["call_B"]   # write 在后

    def test_reads_concurrent_same_layer(self):
        """两个无路径重叠的 read → 同层并发"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "/tmp/a.txt"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/b.txt"}},
        ]
        meta = {
            "read_file": (True, False, "read"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}

    def test_writes_serial_by_path_overlap_with_category(self):
        """两个 write_file 同路径 → 路径重叠约束串行（不因类别约束冲突）"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "first"}},
            {"id": "call_B", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "second"}},
        ]
        meta = {
            "write_file": (False, False, "write"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]

    # ── 规则 d：防环保护 ──────────────────────────────────

    def test_read_write_no_cycle(self):
        """已有 write→read 显式依赖时，类别约束不添加反向 read→write 边"""
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt",
                           "content": "result: $call_B"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/in.txt"}},
        ]
        meta = {
            "read_file": (True, False, "read"),
            "write_file": (False, False, "write"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        # write_file 显式依赖 read_file（$call_B）
        # 类别约束想加 read → write，但 _path_exists(write, read)=True → 跳过
        assert dag.has_cycle() is False
        layers = dag.topological_sort()
        assert layers is not None
        # read_file 在前层（入度 0），write_file 在后层
        assert layers[0] == ["call_B"]
        assert layers[1] == ["call_A"]

    def test_bash_explicit_dep_preserved(self):
        """已有 bash→read 显式依赖时，non-bash→bash 防环跳过"""
        tool_calls = [
            {"id": "call_A", "name": "read_file",
             "arguments": {"path": "/tmp/in.txt"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "cat $call_A"}},
        ]
        meta = {
            "read_file": (True, False, "read"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        # bash 显式依赖 read_file（$call_A）
        # 类别约束想加 read→bash，但 _path_exists(read, bash)=True（显式边）→ 跳过
        assert dag.has_cycle() is False
        # bash 仍因显式依赖在 read 之后
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]
        assert layers[1] == ["call_B"]

    # ── general 不参与约束 ─────────────────────────────────

    def test_general_ignored(self):
        """general 类别不参与任何类别约束，可与 read 同层"""
        tool_calls = [
            {"id": "call_A", "name": "dispatch_agent",
             "arguments": {"prompt": "hello"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/a.txt"}},
        ]
        meta = {
            "dispatch_agent": (False, False, "general"),
            "read_file": (True, False, "read"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        # general 不参与约束，无 read→dispatch 边
        # 两者无显式/隐式依赖，应在同一层
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}

    def test_general_with_bash_no_constraint(self):
        """general + bash → general 不参与 non-bash→bash 约束"""
        tool_calls = [
            {"id": "call_A", "name": "dispatch_agent",
             "arguments": {"prompt": "hello"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo hi"}},
        ]
        meta = {
            "dispatch_agent": (False, False, "general"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        # general 不加入 non_bash_nodes，所以无 non_bash→bash 边
        # bash 是唯一 bash 节点，无链式边
        # 两者无依赖，同层并发
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}

    # ── 综合场景 ──────────────────────────────────────────

    def test_mixed_all_categories(self):
        """read + write + bash + general + interactive 综合场景"""
        tool_calls = [
            {"id": "call_US", "name": "user_select",
             "arguments": {"title": "pick", "options": ["a", "b"]}},
            {"id": "call_S", "name": "search",
             "arguments": {"query": "find_me"}},
            {"id": "call_R", "name": "read_file",
             "arguments": {"path": "$call_S"}},
            {"id": "call_G", "name": "dispatch_agent",
             "arguments": {"prompt": "summarize"}},
            {"id": "call_W", "name": "write_file",
             "arguments": {"path": "/tmp/out.txt", "content": "$call_R"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "cat /tmp/out.txt"}},
        ]
        meta = {
            "user_select": (False, True, "interactive"),
            "search": (True, False, "read"),
            "read_file": (True, False, "read"),
            "dispatch_agent": (False, False, "general"),
            "write_file": (False, False, "write"),
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None

        # 约束分析：
        # ─ 显式依赖：read_file → search, write_file → read_file
        # ─ user_select 约束：所有非 user_select → user_select
        # ─ 类别约束：
        #   · non_bash（search/read_file/write_file/user_select）→ bash
        #   · read（search/read_file）→ write（write_file）
        #   · general（dispatch_agent）不参与
        #
        # 最终依赖链：
        #   user_select(interactive)
        #     ├→ search(read)
        #     │    ├→ read_file(read)
        #     │    │    ├→ write_file(write)
        #     │    │    │    └→ bash(bash)
        #     │    └→ bash (non_bash→bash)
        #     └→ dispatch_agent(general) — 仅依赖 user_select
        #
        # 拓扑分层：
        #   层 0: user_select
        #   层 1: search, dispatch_agent（search 仅依赖 user_select）
        #   层 2: read_file（依赖 user_select + search）
        #   层 3: write_file（依赖 user_select + read_file）
        #   层 4: bash（依赖 user_select + search + read_file + write_file）

        assert len(layers) >= 4  # 至少 4 层
        # user_select 独占首层
        assert layers[0] == ["call_US"]
        # search 在第 1 层或之后
        assert "call_S" in layers[1]
        # dispatch_agent（general）只依赖 user_select → 也在层 1
        assert "call_G" in layers[1]
        # bash 必须在所有 non-bash 之后
        bash_layer = next(i for i, layer in enumerate(layers) if "call_B" in layer)
        us_layer = 0
        s_layer = next(i for i, layer in enumerate(layers) if "call_S" in layer)
        r_layer = next(i for i, layer in enumerate(layers) if "call_R" in layer)
        w_layer = next(i for i, layer in enumerate(layers) if "call_W" in layer)
        assert bash_layer > us_layer
        assert bash_layer >= s_layer
        assert bash_layer >= r_layer
        assert bash_layer >= w_layer
        # read_file 在 search 之后或同层
        assert r_layer >= s_layer
        # write_file 在 read_file 之后
        assert w_layer >= r_layer

    # ── 边界与防御 ─────────────────────────────────────────

    def test_empty_nodes_no_error(self):
        """空 _nodes 时 _detect_tool_category_constraints 不抛异常"""
        dag = ToolDAG([], _make_registry())
        # 直接调用内部方法验证防御
        dag._detect_tool_category_constraints()  # 不应抛异常
        assert dag.size == 0

    def test_no_category_tools_graceful(self):
        """所有工具均为 general（无类别）时，类别约束不产生额外边"""
        tool_calls = [
            {"id": "call_A", "name": "dispatch_agent",
             "arguments": {"prompt": "hello"}},
            {"id": "call_B", "name": "dispatch_agent",
             "arguments": {"prompt": "world"}},
        ]
        meta = {
            "dispatch_agent": (False, False, "general"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 1
        assert set(layers[0]) == {"call_A", "call_B"}

    def test_bash_chain_respects_original_order(self):
        """bash 链式边按原始顺序（非乱序）"""
        tool_calls = [
            {"id": "call_C", "name": "bash",
             "arguments": {"command": "echo 3"}},
            {"id": "call_A", "name": "bash",
             "arguments": {"command": "echo 1"}},
            {"id": "call_B", "name": "bash",
             "arguments": {"command": "echo 2"}},
        ]
        meta = {
            "bash": (False, False, "bash"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 3
        # 链式边按原始顺序：call_C → call_A → call_B
        assert layers[0] == ["call_C"]
        assert layers[1] == ["call_A"]
        assert layers[2] == ["call_B"]

    def test_category_constraint_no_cycle_with_path_overlap(self):
        """类别约束不与路径重叠约束形成环"""
        # write_file(path=X) + read_file(path=X) → 路径重叠导致 read 依赖 write
        # 类别约束想加 read→write 边
        # 但 _path_exists(write, read)=True（路径重叠的 read 依赖 write）
        # 防环：跳过 read→write（已有的 write→read 方向会形成环）
        tool_calls = [
            {"id": "call_A", "name": "write_file",
             "arguments": {"path": "/tmp/data.txt", "content": "hello"}},
            {"id": "call_B", "name": "read_file",
             "arguments": {"path": "/tmp/data.txt"}},
        ]
        meta = {
            "write_file": (False, False, "write"),
            "read_file": (True, False, "read"),
        }
        dag = ToolDAG(tool_calls, _make_registry(meta))
        # 路径重叠：read 依赖 write（call_B 依赖 call_A）
        # 类别约束想加 read(call_B) → write(call_A)，但 _path_exists(write, read)=True → 跳过
        assert dag.has_cycle() is False
        layers = dag.topological_sort()
        assert layers is not None
        assert len(layers) == 2
        assert layers[0] == ["call_A"]  # write 先
        assert layers[1] == ["call_B"]  # read 后
