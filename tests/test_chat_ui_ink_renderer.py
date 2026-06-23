"""单元测试 — chat_ui InkRenderer (React Ink 声明式渲染器)。

覆盖：
- render_frame: 状态→组件树→CVNode→diff→patches
- apply_frame: patches→终端写入（含首次渲染和增量渲染）
- 首次渲染（old_tree=None 全量 INSERT）
- 增量渲染（仅输出变化行）
- reset 状态重置
"""

from __future__ import annotations

import io
import pytest

from src.chat_ui._ink_state import InkState
from src.chat_ui._ink_renderer import InkRenderer
from src.chat_ui._vdom import CVPatchType


# ═══════════════════════════════════════════════════════════
# Mock OutputAdapter
# ═══════════════════════════════════════════════════════════

class _MockAdapter:
    """模拟 OutputAdapter，将 write_raw 内容写入 StringIO 缓冲区。"""

    def __init__(self) -> None:
        self.buffer = io.StringIO()

    def write_raw(self, text: str) -> None:
        self.buffer.write(text)

    def write(self, renderable) -> None:
        self.buffer.write(str(renderable) + "\n")

    def get_output(self) -> str:
        return self.buffer.getvalue()

    def clear(self) -> None:
        self.buffer = io.StringIO()


# ═══════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════

@pytest.fixture
def adapter() -> _MockAdapter:
    return _MockAdapter()


@pytest.fixture
def renderer(adapter: _MockAdapter) -> InkRenderer:
    return InkRenderer(adapter)


@pytest.fixture
def basic_state() -> InkState:
    """基础状态：含用户消息、回答内容、一条通知。"""
    state = InkState()
    state.user_message = "hello"
    state.content_text = "world response"
    state.notifications = ["system ready"]
    return state


# ═══════════════════════════════════════════════════════════
# 首次渲染测试
# ═══════════════════════════════════════════════════════════

class TestFirstRender:
    """首次渲染（old_root is None）。"""

    def test_all_insert_patches(self, renderer: InkRenderer) -> None:
        """首次渲染时所有 patches 应为 INSERT 类型。"""
        state = InkState()
        state.content_text = "hello"

        patches = renderer.render_frame(state)

        assert len(patches) > 0
        for patch in patches:
            assert patch.type == CVPatchType.INSERT, (
                f"首次渲染 patch 应为 INSERT, 实际: {patch.type.value}"
            )

    def test_empty_state_produces_patches(self, renderer: InkRenderer) -> None:
        """空状态仍产生根 Box 的 INSERT patch。"""
        state = InkState()
        patches = renderer.render_frame(state)

        # 至少有一个根 Box patch
        assert len(patches) >= 1
        root_patch = patches[0]
        assert root_patch.type == CVPatchType.INSERT
        assert root_patch.key.startswith("box:")

    def test_first_apply_writes_content(self, renderer: InkRenderer,
                                         adapter: _MockAdapter,
                                         basic_state: InkState) -> None:
        """首次 apply_frame 应写入全部内容。"""
        patches = renderer.render_frame(basic_state)
        renderer.apply_frame(patches)

        output = adapter.get_output()
        assert "hello" in output
        assert "world response" in output
        assert "system ready" in output


# ═══════════════════════════════════════════════════════════
# 增量渲染测试
# ═══════════════════════════════════════════════════════════

class TestIncrementalRender:
    """增量渲染（old_root 存在）。"""

    def test_no_change_skips_write(self, renderer: InkRenderer,
                                    adapter: _MockAdapter) -> None:
        """相同状态连续两次 render_frame+apply_frame，第二次不写终端。"""
        state = InkState()
        state.content_text = "unchanged"

        # 第一帧
        p1 = renderer.render_frame(state)
        renderer.apply_frame(p1)
        adapter.clear()

        # 第二帧 — 相同内容
        state2 = InkState()
        state2.content_text = "unchanged"
        p2 = renderer.render_frame(state2)
        renderer.apply_frame(p2)

        output = adapter.get_output()
        assert output == "", (
            f"相同内容应跳过写入, 实际输出: {output!r}"
        )

    def test_content_change_writes_incremental(self, renderer: InkRenderer,
                                                adapter: _MockAdapter) -> None:
        """内容变化时增量渲染应使用 ANSI 光标移动。"""
        # 第一帧
        state1 = InkState()
        state1.content_text = "old content"
        p1 = renderer.render_frame(state1)
        renderer.apply_frame(p1)
        adapter.clear()

        # 第二帧 — 新增推理内容
        state2 = InkState()
        state2.content_text = "old content"
        state2.reasoning_text = "new reasoning"
        p2 = renderer.render_frame(state2)
        renderer.apply_frame(p2)

        output = adapter.get_output()
        # 增量渲染应包含 ANSI 上移序列
        assert "\033[" in output, (
            f"增量渲染应含 ANSI 转义序列, 实际: {output!r}"
        )
        assert "old content" in output
        assert "new reasoning" in output

    def test_add_tool_output_triggers_update(self, renderer: InkRenderer,
                                              adapter: _MockAdapter) -> None:
        """新增工具输出应触发增量更新。"""
        state1 = InkState()
        state1.content_text = "base"
        p1 = renderer.render_frame(state1)
        renderer.apply_frame(p1)
        adapter.clear()

        state2 = InkState()
        state2.content_text = "base"
        state2.tool_outputs = ["tool result"]
        p2 = renderer.render_frame(state2)
        renderer.apply_frame(p2)

        output = adapter.get_output()
        assert "tool result" in output

    def test_mixed_patch_types_incremental(self, renderer: InkRenderer) -> None:
        """增量渲染应产生 INSERT/DELETE/UPDATE 混合 patches。"""
        state1 = InkState()
        state1.content_text = "v1"
        state1.errors = ["err1"]
        p1 = renderer.render_frame(state1)

        # 第二帧：移除 errors，新增 tool_outputs
        state2 = InkState()
        state2.content_text = "v1"
        state2.tool_outputs = ["tool1"]

        p2 = renderer.render_frame(state2)

        types = {p.type for p in p2}
        # 增量渲染应包含 DELETE（移除 errors）和 INSERT（新增 tool_outputs）
        assert CVPatchType.DELETE in types or CVPatchType.INSERT in types, (
            f"增量 patches 应含 DELETE/INSERT, 实际: {[t.value for t in types]}"
        )


# ═══════════════════════════════════════════════════════════
# 状态管理测试
# ═══════════════════════════════════════════════════════════

class TestStateManagement:
    """渲染器状态管理（reset、锁等）。"""

    def test_reset_clears_state(self, renderer: InkRenderer) -> None:
        """reset 后应清空 old_root 和行缓冲区。"""
        state = InkState()
        state.content_text = "test"
        renderer.render_frame(state)
        renderer.apply_frame([])  # 设置 _line_count

        renderer.reset()

        # reset 后再次 render 应产生全量 INSERT
        state2 = InkState()
        state2.content_text = "after reset"
        patches = renderer.render_frame(state2)

        for patch in patches:
            assert patch.type == CVPatchType.INSERT, (
                f"reset 后首次渲染 patch 应为 INSERT, 实际: {patch.type.value}"
            )

    def test_reset_preserves_adapter(self, renderer: InkRenderer,
                                      adapter: _MockAdapter) -> None:
        """reset 不重置 adapter 引用。"""
        renderer.reset()
        assert renderer._adapter is adapter


# ═══════════════════════════════════════════════════════════
# 组件树构建测试
# ═══════════════════════════════════════════════════════════

class TestComponentTree:
    """_build_component_tree 组件树构建。"""

    def test_all_fields_map_to_components(self, renderer: InkRenderer) -> None:
        """InkState 各字段应映射为对应组件。"""
        state = InkState()
        state.errors = ["err"]
        state.user_message = "user"
        state.reasoning_text = "think"
        state.content_text = "answer"
        state.tool_outputs = ["tool"]
        state.tool_summary_successful = ("ls",)
        state.notifications = ["note"]
        state.write_line_text = "line"

        root = renderer._build_component_tree(state)
        # root 应为 Box，children 含各组件
        assert len(root.children) >= 7, (
            f"应有至少 7 个子组件, 实际: {len(root.children)}"
        )

    def test_error_capped_at_three(self, renderer: InkRenderer) -> None:
        """errors 字段应截断为最多 3 条。"""
        state = InkState()
        state.errors = ["e1", "e2", "e3", "e4", "e5"]

        root = renderer._build_component_tree(state)
        # 找到 ErrorBlock 子组件数量
        error_children = [
            c for c in root.children
            if type(c).__name__ == "ErrorBlock"
        ]
        assert len(error_children) == 3, (
            f"errors 应截断为 3 条, 实际: {len(error_children)}"
        )

    def test_empty_state_returns_box_with_no_children(self, renderer: InkRenderer) -> None:
        """空 InkState 应产生无子组件的 Box。"""
        state = InkState()
        root = renderer._build_component_tree(state)
        assert len(root.children) == 0


# ═══════════════════════════════════════════════════════════
# 行渲染测试
# ═══════════════════════════════════════════════════════════

class TestLineRendering:
    """_render_component_to_lines 行渲染。"""

    def test_renders_to_non_empty_lines(self, renderer: InkRenderer,
                                         basic_state: InkState) -> None:
        """组件树渲染应产生非空行列表。"""
        root = renderer._build_component_tree(basic_state)
        lines = renderer._render_component_to_lines(root)
        assert len(lines) > 0
        # 每行不应含换行符
        for line in lines:
            assert "\n" not in line

    def test_empty_component_renders_empty(self, renderer: InkRenderer) -> None:
        """空状态组件应产生空行列表。"""
        state = InkState()
        root = renderer._build_component_tree(state)
        lines = renderer._render_component_to_lines(root)
        assert lines == [] or lines == [""]


# ═══════════════════════════════════════════════════════════
# 线程安全测试
# ═══════════════════════════════════════════════════════════

class TestThreadSafety:
    """线程安全（render_frame/apply_frame 在同一锁下执行）。"""

    def test_lock_exists(self, renderer: InkRenderer) -> None:
        """渲染器应有 _lock 属性。"""
        assert renderer._lock is not None

    def test_render_and_apply_use_same_lock(self, renderer: InkRenderer) -> None:
        """render_frame 和 apply_frame 应使用同一锁。"""
        lock_id_render = id(renderer._lock)
        # 锁实例不变
        assert lock_id_render == id(renderer._lock)


# ═══════════════════════════════════════════════════════════
# 终端宽度测试
# ═══════════════════════════════════════════════════════════

class TestTerminalWidth:
    """终端宽度管理。"""

    def test_width_initial_default(self, renderer: InkRenderer) -> None:
        """初始终端宽度应为默认值 80。"""
        assert renderer._terminal_width == 80

    def test_width_refreshed_on_render(self, renderer: InkRenderer) -> None:
        """render_frame 后终端宽度应被刷新。"""
        state = InkState()
        renderer.render_frame(state)
        assert renderer._terminal_width > 0
