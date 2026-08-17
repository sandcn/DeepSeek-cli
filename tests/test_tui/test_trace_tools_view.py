"""工具列表详情视图测试（轨迹 Trace 工具列表 Enter 进入新界面）。

用户需求（2026-08-17）：轨迹 Trace 中选中 **#0 工具列表** 记录按 Enter →
进入新界面（模态全屏视图 ``"trace_tools"``）——左右布局：**左边工具名列表
上下选择**，**右边树控件显示需要的参数**。

覆盖：
  1. 数据层（trace.py）：``_tools_schema_list``（与注册表一致/异常降级/单
     工具失败降级/TTL 缓存）与 ``build_tools_params_tree``（参数容器 + 属性
     叶子/必需标记/枚举默认/无参数占位）；
  2. 渲染层（trace_tools_view.py）：``_tool_row_runs``（选中高亮）/
     ``_inspector_children``（标题/元信息/描述/参数树/截断省略/空态）；
  3. 组件：TraceToolsView 左右布局 + 导航写回 ``trace_tools_selected`` +
     Esc/Ctrl+H 返回主轨迹；
  4. 集成：TraceView Enter 工具列表记录 → ``fullscreen="trace_tools"``；
     App 整屏渲染 TraceToolsView；返回主轨迹后 trace 视图恢复。
"""

from __future__ import annotations

import pytest

from src.renderer.ansi.helpers import AnsiLine
from src.tui._input_parser import KeyEvent
from src.tui.app.app import build_app_element
from src.tui.app.model import AppModel
from src.tui.app.trace import (
    build_tools_params_tree,
    _tools_schema_list,
)
from src.tui.app.trace_tools_view import (
    TraceToolsView,
    _inspector_children,
    _tool_row_runs,
)
from src.tui.app.trace_view import TraceView
from src.tui.ink import hooks
from src.tui.ink.fiber import TAG_FUNCTION, Fiber
from src.tui.ink.widgets.listview import ListView


def _make_model_with_blocks() -> AppModel:
    """构造带聊天块的 AppModel（build_trace_records 首条 = #0 工具列表）。"""
    m = AppModel()
    m.append_committed("user", [AnsiLine.of("> 你好")])
    m.append_committed("content", [AnsiLine.of("回答内容")])
    return m


def _render(component, props, fiber=None):
    """在 hook 环境下渲染函数组件（与 test_trace_view 同模式）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, dict(props))
    hooks._push_current(fiber)
    try:
        return component(props), fiber
    finally:
        hooks._pop_current()


def _walk(el, out):
    """收集元素树中全部组件（递归）。"""
    out.append(el)
    for c in getattr(el, "children", None) or ():
        if isinstance(c, tuple):
            for x in c:
                _walk(x, out)
        elif c is not None:
            _walk(c, out)


def _input_handler(fiber):
    """从 fiber hooks 中取出 use_input 注册的 InputHook handler。"""
    for hook in fiber.hooks:
        if getattr(hook, "is_active", None) is not None and hasattr(hook, "handler"):
            return hook.handler
    raise AssertionError("fiber 中无 use_input hook")


# ═══════════════════════════════════════════════════════════
# 1. 数据层：_tools_schema_list
# ═══════════════════════════════════════════════════════════

def test_tools_schema_list_matches_registry(monkeypatch):
    """_tools_schema_list 返回 [(name, properties, required, description)]——
    工具顺序与注册表一致（原名）；read_file 参数解析正确（path 必需）。"""
    from src.tools.registry import ToolRegistry
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)
    schemas = _tools_schema_list()
    assert isinstance(schemas, list) and schemas
    tools = list(ToolRegistry.default().get_tools())
    assert [s[0] for s in schemas] == tools, "工具顺序应与注册表一致（原名）"
    assert len(schemas) >= 10, "内置工具应多于 10 个"
    for item in schemas:
        name, props, required, desc = item
        assert isinstance(name, str) and name
        assert isinstance(props, dict)
        assert isinstance(required, list)
        assert isinstance(desc, str)
    # read_file 参数解析（path 必需）
    rf = next(s for s in schemas if s[0] == "read_file")
    assert "path" in rf[1], "read_file 应有 path 参数"
    assert "path" in rf[2], "read_file path 应为必需"
    assert rf[3], "read_file 应有描述"


def test_tools_schema_list_defensive_registry_failure(monkeypatch):
    """注册表获取异常 → 空列表（静默降级零成本）；异常结果不缓存。"""
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)

    class _Boom:
        @staticmethod
        def default():
            raise RuntimeError("registry broken")

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _Boom)
    assert _tools_schema_list() == []
    # 异常不缓存：再次调用仍走异常路径（返回 []，不抛）
    assert _tools_schema_list() == []


def test_tools_schema_list_empty_registry(monkeypatch):
    """注册表为空（自动发现失败/清空）→ 空列表（界面显示空态）。"""
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)

    class _EmptyReg:
        @staticmethod
        def get_tools():
            return {}

    class _EmptyRegistry:
        @staticmethod
        def default():
            return _EmptyReg()

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _EmptyRegistry)
    assert _tools_schema_list() == []


def test_tools_schema_list_single_tool_failure(monkeypatch):
    """单个工具 to_tool_schema 抛异常 → 该工具降级 (name, {}, [], "")，
    不阻断其他工具（异常结果不缓存——本测试直接重新构建）。"""
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)

    class _BadTool:
        name = "bad_tool"

        @classmethod
        def to_tool_schema(cls):
            raise RuntimeError("boom")

    class _GoodTool:
        name = "good_tool"

        @classmethod
        def to_tool_schema(cls):
            return {
                "type": "function",
                "function": {
                    "name": "good_tool",
                    "parameters": {
                        "properties": {"x": {"type": "string"}},
                        "required": ["x"],
                    },
                },
            }

    class _Reg:
        @staticmethod
        def get_tools():
            return {"good_tool": _GoodTool, "bad_tool": _BadTool}

    class _Registry:
        @staticmethod
        def default():
            return _Reg()

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _Registry)
    out = _tools_schema_list()
    by_name = {item[0]: item for item in out}
    assert by_name["good_tool"][1] == {"x": {"type": "string"}}
    assert by_name["good_tool"][2] == ["x"]
    assert by_name["bad_tool"] == ("bad_tool", {}, [], "")


def test_tools_schema_list_cached(monkeypatch):
    """TTL 缓存：TTL 内第二次调用命中（同一列表引用）。"""
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)
    a = _tools_schema_list()
    b = _tools_schema_list()
    assert a is b, "TTL 内第二次调用应命中缓存（同一列表引用）"


# ═══════════════════════════════════════════════════════════
# 2. 数据层：build_tools_params_tree（树控件显示需要的参数）
# ═══════════════════════════════════════════════════════════

def test_build_tools_params_tree_structure():
    """参数树：``参数 (N 项)`` 容器 + 每参数节点（类型/描述/必需叶子）。"""
    props = {
        "path": {"type": "string", "description": "文件路径"},
        "start_line": {"type": "integer", "minimum": 1},
    }
    nodes = build_tools_params_tree(props, ["path"])
    assert len(nodes) == 1
    root = nodes[0]
    assert root["label"] == "参数 (2 项)"
    children = root["children"]
    assert len(children) == 2
    # 必需参数：* 标记 + 必需叶子
    path_node = children[0]
    assert path_node["label"] == "path *", "必需参数应有 * 标记"
    path_labels = [c["label"] for c in path_node["children"]]
    assert "类型: string" in path_labels
    assert "描述: 文件路径" in path_labels
    assert "必需: 是" in path_labels
    # 非必需参数：无 * 标记 + 约束叶子
    line_node = children[1]
    assert line_node["label"] == "start_line"
    line_labels = [c["label"] for c in line_node["children"]]
    assert "类型: integer" in line_labels
    assert "minimum: 1" in line_labels
    assert "必需: 是" not in line_labels


def test_build_tools_params_tree_no_params():
    """无参数 → ``参数: (无)`` 叶子。"""
    nodes = build_tools_params_tree({}, [])
    assert nodes == [{"label": "参数: (无)", "children": []}]


def test_param_node_enum_default_items():
    """枚举/默认值/数组元素定义 → 叶子展示。"""
    props = {
        "mode": {"type": "string", "enum": ["fast", "safe"], "default": "fast"},
        "tags": {"type": "array", "items": {"type": "string"}},
    }
    nodes = build_tools_params_tree(props, [])
    children = nodes[0]["children"]
    mode = children[0]
    mode_labels = [c["label"] for c in mode["children"]]
    assert "枚举: fast, safe" in mode_labels
    assert "默认值: fast" in mode_labels
    tags = children[1]
    tags_labels = [c["label"] for c in tags["children"]]
    assert any(l.startswith("元素: ") for l in tags_labels), \
        f"items 应展示元素定义: {tags_labels}"


def test_param_node_compact_value():
    """布尔/None/字典值 → JSON 字面量紧凑文本（树叶子标签单行语义）。"""
    from src.tui.app.trace import _compact_value
    assert _compact_value(None) == "null"
    assert _compact_value(True) == "true"
    assert _compact_value(False) == "false"
    assert _compact_value({"a": 1}) == '{"a": 1}'
    long_list = list(range(100))
    s = _compact_value(long_list)
    assert s.endswith("...") and len(s) <= 60


# ═══════════════════════════════════════════════════════════
# 3. 渲染层：_tool_row_runs / _inspector_children
# ═══════════════════════════════════════════════════════════

def test_tool_row_runs_basic():
    """工具名行：名称 + 无 ▶（未选中）；宽截断。"""
    runs = _tool_row_runs("read_file", sel=False, left_w=30)
    text = "".join(r.text for r in runs)
    assert "read_file" in text
    assert not text.startswith("\u25b6")
    # 超宽截断
    runs2 = _tool_row_runs("read_file_with_long_name", sel=False, left_w=8)
    assert len("".join(r.text for r in runs2)) <= 8


def test_tool_row_runs_selected_highlight():
    """选中行：▶ 标记 + 整行背景高亮。"""
    runs = _tool_row_runs("read_file", sel=True, left_w=30)
    assert runs[0].text.startswith("\u25b6")
    for r in runs:
        assert r.style is not None and r.style.bg is not None, \
            "选中行应整行背景高亮"


def test_inspector_children_structure():
    """右侧检查器：标题/元信息/描述/▸ 参数小节/参数树行。"""
    children = _inspector_children(
        "read_file",
        {"path": {"type": "string", "description": "文件路径"}},
        ["path"],
        "读取文件内容",
        40, 20,
    )
    texts = [str(c.props.get("children", "")) for c in children]
    assert texts[0] == "read_file", f"标题应为工具名: {texts[0]}"
    assert "1 个参数" in texts[1] and "1 个必需" in texts[1], f"元信息: {texts[1]}"
    joined = "\n".join(texts)
    assert "读取文件内容" in joined, "描述应显示"
    assert "\u25b8 参数" in joined, "应有参数小节标题"
    assert "path *" in joined, "参数节点（必需标记）应显示"
    assert "类型: string" in joined, "参数类型叶子应显示"
    assert "描述: 文件路径" in joined, "参数描述叶子应显示"


def test_inspector_children_no_description():
    """无描述：跳过描述行与分割线，直接元信息 + 参数树。"""
    children = _inspector_children(
        "bash", {"command": {"type": "string"}}, ["command"], "", 40, 10,
    )
    texts = [str(c.props.get("children", "")) for c in children]
    joined = "\n".join(texts)
    assert "\u2500" not in joined, "无描述不应有分割线"
    assert "\u25b8 参数" in joined


def test_inspector_children_budget_truncated():
    """内容超视口预算 → 「… 后 N 行省略」后置（head-first）。"""
    children = _inspector_children(
        "bash",
        {"command": {"type": "string", "description": "c" * 200}},
        ["command"],
        "d" * 300,
        20, 5,
    )
    texts = [str(c.props.get("children", "")) for c in children]
    omitted = [t for t in texts if "省略" in t]
    assert omitted and "后" in omitted[0], f"应显示后 N 行省略: {texts}"
    assert texts[-1] == omitted[0], f"省略提示应在最后一行: {texts}"


def test_inspector_children_empty_state():
    """空数据源（无工具）→ 「无可用工具」占位。"""
    children = _inspector_children("", {}, [], "", 40, 10)
    texts = [str(c.props.get("children", "")) for c in children]
    assert "无可用工具" in texts
    assert len(children) == 1


# ═══════════════════════════════════════════════════════════
# 4. 组件：TraceToolsView（左右布局 + 导航 + 关闭）
# ═══════════════════════════════════════════════════════════

def test_trace_tools_view_renders_split_layout():
    """左右布局：左栏工具名列表（ListView）+ │ + 右栏参数检查器。"""
    from src.tools.registry import ToolRegistry
    m = AppModel()
    m.fullscreen = "trace_tools"
    el, _ = _render(TraceToolsView, {"model": m, "width": 100})
    parts = list(el.children)
    assert len(parts) == 2, "头部 + 左右 Row"
    header = parts[0]
    header_text = "".join(r.text for r in header.props.get("styled", []))
    assert "工具列表" in header_text
    row_el = parts[1]
    left, sep, right = list(row_el.children)
    assert left.type is ListView, "左栏应为 ListView"
    assert sep.props["children"] == "\u2502"
    # 左侧 items = 注册表全部工具名（原名，注册顺序）
    items = left.props["items"]
    tools = list(ToolRegistry.default().get_tools())
    assert items == tools, "左栏应列出全部工具（注册顺序）"
    assert len(items) >= 10
    # 右侧检查器 = 首个工具（注册表首个）
    first_tool = tools[0]
    right_texts = [str(c.props.get("children", "")) for c in right.children]
    assert right_texts[0] == first_tool, f"右侧标题应为首个工具: {right_texts[0]}"
    assert "个参数" in right_texts[1], f"应有参数元信息: {right_texts[1]}"


def test_trace_tools_view_empty_registry(monkeypatch):
    """注册表为空 → 左栏空列表 + 右栏「无可用工具」占位（不崩溃）。"""
    monkeypatch.setattr("src.tui.app.trace._tools_schema_cache", None)

    class _EmptyReg:
        @staticmethod
        def get_tools():
            return {}

    class _EmptyRegistry:
        @staticmethod
        def default():
            return _EmptyReg()

    monkeypatch.setattr("src.tools.registry.ToolRegistry", _EmptyRegistry)
    m = AppModel()
    m.fullscreen = "trace_tools"
    el, _ = _render(TraceToolsView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    left, _, right = list(row_el.children)
    assert left.props["items"] == []
    right_texts = [str(c.props.get("children", "")) for c in right.children]
    assert "无可用工具" in right_texts


def test_trace_tools_view_navigation_writes_selected():
    """↑↓ 导航（经 ListView 控件）写回 model.trace_tools_selected（受控光标）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = AppModel()
    m.fullscreen = "trace_tools"
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    # 下移 → 选中索引 1
    assert router(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is True
    assert m.trace_tools_selected == 1
    # 下一帧重建（渲染循环）→ 继续下移
    rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_down", raw=b"\x1b[B")) is True
    assert m.trace_tools_selected == 2
    # 下一帧重建 → 上移返回
    rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="arrow_up", raw=b"\x1b[A")) is True
    assert m.trace_tools_selected == 1
    # g → 首项
    rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="char", char="g", raw=b"g")) is True
    assert m.trace_tools_selected == 0


def test_trace_tools_view_selection_updates_inspector():
    """选中变化 → 右侧检查器显示对应工具参数（受控光标 + use_memo deps）。"""
    from src.tools.registry import ToolRegistry
    m = AppModel()
    m.fullscreen = "trace_tools"
    # 选中 read_file（记录索引）
    tools = list(ToolRegistry.default().get_tools())
    idx = tools.index("read_file")
    m.trace_tools_selected = idx
    el, _ = _render(TraceToolsView, {"model": m, "width": 100})
    row_el = list(el.children)[1]
    right = list(row_el.children)[2]
    right_texts = [str(c.props.get("children", "")) for c in right.children]
    assert right_texts[0] == "read_file"
    joined = "\n".join(right_texts)
    assert "path" in joined, "read_file 参数树应显示 path"
    # 选中 bash → 右侧切到 bash（command 参数）
    idx2 = tools.index("bash")
    m.trace_tools_selected = idx2
    el2, _ = _render(TraceToolsView, {"model": m, "width": 100})
    row_el2 = list(el2.children)[1]
    right2 = list(row_el2.children)[2]
    right_texts2 = [str(c.props.get("children", "")) for c in right2.children]
    assert right_texts2[0] == "bash"
    joined2 = "\n".join(right_texts2)
    assert "command" in joined2, "bash 参数树应显示 command"


def test_trace_tools_view_esc_returns_to_main_trace():
    """Esc → 返回主轨迹（fullscreen = "trace"，TraceView 恢复）。"""
    m = AppModel()
    m.fullscreen = "trace_tools"
    el, fiber = _render(TraceToolsView, {"model": m, "width": 100})
    handler = _input_handler(fiber)
    assert handler(KeyEvent(kind="escape", raw=b"\x1b")) is True
    assert m.fullscreen == "trace"


def test_trace_tools_view_ctrl_h_returns_to_main_trace():
    """Ctrl+H → 返回主轨迹（fullscreen = "trace"）。"""
    m = AppModel()
    m.fullscreen = "trace_tools"
    el, fiber = _render(TraceToolsView, {"model": m, "width": 100})
    handler = _input_handler(fiber)
    assert handler(KeyEvent(kind="ctrl_key", char="\x08", raw=b"\x08")) is True
    assert m.fullscreen == "trace"


def test_trace_tools_view_enter_passes_through():
    """Enter 不消费（返回 False——router 模态吞掉：不落入输入缓冲）。"""
    m = AppModel()
    m.fullscreen = "trace_tools"
    el, fiber = _render(TraceToolsView, {"model": m, "width": 100})
    handler = _input_handler(fiber)
    assert handler(KeyEvent(kind="enter", raw=b"\r")) is False
    assert m.fullscreen == "trace_tools"


# ═══════════════════════════════════════════════════════════
# 5. 集成：TraceView Enter 工具列表 → trace_tools；App 整屏渲染
# ═══════════════════════════════════════════════════════════

def test_trace_view_enter_tools_record_opens_tools_view():
    """主轨迹选中 #0 工具列表记录按 Enter → fullscreen = "trace_tools"、
    trace_tools_selected 归零。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 0  # records[0] = #0 工具列表
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert m.fullscreen == "trace_tools", "Enter 工具列表应进入工具列表详情视图"
    assert m.trace_tools_selected == 0
    # fullscreen 已切换 → trace_open（= fullscreen=="trace"）为 False
    assert m.trace_open is False


def test_trace_view_enter_non_tools_passes_through():
    """主轨迹 Enter 非工具列表记录 → 不进入 trace_tools（仍为主轨迹）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = -1  # 跟随尾部（末条 = content 记录）
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert m.fullscreen == "trace", "非工具列表记录不应切换视图"
    assert m.trace_tools_selected == 0


def test_subagent_trace_enter_tools_record_opens_tools_view():
    """subagent 轨迹中选中 #0 工具列表记录按 Enter → 同样进入工具列表
    详情视图；Esc 返回 subagent 轨迹（trace_subagent_label 保留）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    from src.tui.subagent import SubAgentPanelController
    ctl = SubAgentPanelController.get_default()
    try:
        ctl._store.clear()
        ctl._store.add_agent("agent-1", "解析模块", status="done")
        slot = ctl._store._agents["agent-1"]
        slot.messages = [{"role": "user", "content": "读取 user.py"}]
        m = _make_model_with_blocks()
        m.trace_open = True
        m.trace_selected = -1
        m.trace_subagent_label = "agent-1"  # 已在 subagent 轨迹
        rec = Reconciler()
        root = rec.create_root()
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # 导航到 #0 工具列表（subagent 轨迹首条）→ 下一帧重建（渲染循环）
        assert router(KeyEvent(kind="home", raw=b"\x1b[H")) is True
        assert m.trace_selected == 0
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router = rec._build_input_router(root)
        # Enter → 进入工具列表详情视图
        assert router(KeyEvent(kind="enter", raw=b"\r")) is True
        assert m.fullscreen == "trace_tools"
        assert m.trace_tools_selected == 0
        # TraceToolsView Esc → 返回 subagent 轨迹（label 保留）
        rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
        router2 = rec._build_input_router(root)
        assert router2(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.fullscreen == "trace"
        assert m.trace_subagent_label == "agent-1", \
            "返回应回到 subagent 轨迹（label 保留）"
        # subagent 轨迹 Esc → 返回主轨迹
        rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
        router3 = rec._build_input_router(root)
        assert router3(KeyEvent(kind="escape", raw=b"\x1b")) is True
        assert m.trace_subagent_label is None
        assert m.fullscreen == "trace"
    finally:
        ctl._store.clear()


def test_app_renders_trace_tools_view_fullscreen():
    """fullscreen == "trace_tools" → App 整屏只渲染 TraceToolsView
    （其他 TUI 组件全部不显示）。"""
    from src.tui.app.app import App
    from src.tui.app.chat_view import ChatView
    from src.tui.app.input_area import InputArea
    from src.tui.app.status_bar import StatusBar
    m = _make_model_with_blocks()
    m.fullscreen = "trace_tools"
    el, _ = _render(App, {"model": m, "width": 100})
    all_els = []
    _walk(el, all_els)
    assert any(e.type is TraceToolsView for e in all_els)
    assert not any(e.type is ChatView for e in all_els)
    assert not any(e.type is StatusBar for e in all_els)
    assert not any(e.type is InputArea for e in all_els)
    assert el.type is TraceToolsView, "根元素应为 TraceToolsView"


def test_app_build_element_switches_fullscreen_views():
    """App 组件：fullscreen "trace"（TraceView）↔ "trace_tools"
    （TraceToolsView）整屏切换（注册表按 id 分发）。"""
    from src.tui.app.app import App
    m = _make_model_with_blocks()
    m.fullscreen = "trace"
    el1, _ = _render(App, {"model": m, "width": 100})
    assert el1.type is TraceView
    m.fullscreen = "trace_tools"
    el2, _ = _render(App, {"model": m, "width": 100})
    assert el2.type is TraceToolsView


def test_fullscreen_toggle_returns_to_trace():
    """trace_tools 内关闭类按键返回 trace 后，TraceView 仍可正常关闭整个
    轨迹视图（fullscreen 恢复 "trace"——toggle 语义不破坏）。"""
    from src.tui.ink.element import h as h_el
    from src.tui.ink.reconciler import Reconciler
    m = _make_model_with_blocks()
    m.trace_open = True
    m.trace_selected = 0
    # 进入工具列表
    rec = Reconciler()
    root = rec.create_root()
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router = rec._build_input_router(root)
    assert router(KeyEvent(kind="enter", raw=b"\r")) is True
    assert m.fullscreen == "trace_tools"
    # 工具列表 Esc → 返回主轨迹
    rec.render(root, h_el(TraceToolsView, {"model": m, "width": 100}), 100, 24)
    router2 = rec._build_input_router(root)
    assert router2(KeyEvent(kind="escape", raw=b"\x1b")) is True
    assert m.fullscreen == "trace"
    # 主轨迹 Esc → 关闭整个轨迹视图
    rec.render(root, h_el(TraceView, {"model": m, "width": 100}), 100, 24)
    router3 = rec._build_input_router(root)
    assert router3(KeyEvent(kind="escape", raw=b"\x1b")) is True
    assert m.fullscreen == ""
    assert m.trace_open is False


def test_reset_display_clears_tools_selection():
    """Ctrl+L 清屏（reset_display）→ trace_tools_selected 复位 0。"""
    m = _make_model_with_blocks()
    m.trace_tools_selected = 5
    m.reset_display()
    assert m.trace_tools_selected == 0
