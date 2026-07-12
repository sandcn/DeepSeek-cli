"""TuiComponent 入场动效单元测试。

测试范围：
1. entry_frame=-1 时无动效（_entry_phase 返回 1.0）
2. entry_frame>=0 时 _entry_phase 在 6 帧内从 0.0 增长到 1.0
3. _get_bounce_prefix() 在动效激活时返回非空 ANSI 前缀
4. 动效结束后 _get_bounce_prefix() 返回空字符串
"""

from __future__ import annotations

import importlib.util as _util
from unittest.mock import MagicMock

import pytest

from src.ui.tui._animator import AnimatorContext
from src.ui.tui._text_utils import build_bounce_ansi

# 绕过 __init__.py 循环导入，直接加载 _base.py 模块
_base_spec = _util.spec_from_file_location(
    "chat_ui.components._base",
    "src/chat_ui/components/_base.py",
)
_base_mod = _util.module_from_spec(_base_spec)
_base_spec.loader.exec_module(_base_mod)
TuiComponent = _base_mod.TuiComponent


# ── 测试用桩组件 ──────────────────────────────────

class _SimpleComponent(TuiComponent):
    """仅返回固定文本的桩组件。"""
    def __init__(self, text: str = "hello"):
        self._text = text

    def render(self) -> str:
        return self._text


# ── Fixtures ────────────────────────────────────────────

@pytest.fixture
def mock_adapter():
    """Mock OutputAdapter。"""
    return MagicMock()


@pytest.fixture(autouse=True)
def _reset_animator():
    """每个测试前后重置 AnimatorContext 实例，确保测试隔离。"""
    AnimatorContext._default_instance = None
    yield
    AnimatorContext._default_instance = None


# ═══════════════════════════════════════════════════════
# 入场动效基础设施测试
# ═══════════════════════════════════════════════════════

class TestEntryPhase:
    """_entry_phase 属性测试"""

    def test_default_is_one(self):
        """entry_frame=-1 时 _entry_phase 恒返回 1.0。"""
        comp = _SimpleComponent()
        assert comp._entry_frame == -1
        assert comp._entry_phase == 1.0

    def test_set_entry_frame_stores_value(self):
        """set_entry_frame() 正确存储帧号。"""
        comp = _SimpleComponent()
        comp.set_entry_frame(42)
        assert comp._entry_frame == 42

    def test_begins_at_zero(self):
        """设 entry_frame 后，首帧 _entry_phase 为 0.0。"""
        ctx = AnimatorContext.get_default()
        for _ in range(50):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        assert comp._entry_phase == 0.0

    def test_reaches_one_after_six_frames(self):
        """入场 6 帧后 _entry_phase 达到 1.0。"""
        ctx = AnimatorContext.get_default()
        for _ in range(50):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        for _ in range(6):
            ctx.tick()
        assert comp._entry_phase == 1.0

    def test_intermediate_values(self):
        """入场过程中 _entry_phase 返回中间值。"""
        ctx = AnimatorContext.get_default()
        for _ in range(10):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        for _ in range(3):
            ctx.tick()
        assert abs(comp._entry_phase - 0.5) < 0.001


class TestGetBouncePrefix:
    """_get_bounce_prefix() 方法测试"""

    def test_active_returns_ansi(self):
        """动效激活时返回非空 ANSI 前缀。"""
        ctx = AnimatorContext.get_default()
        for _ in range(20):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        prefix = comp._get_bounce_prefix()
        assert prefix != ""
        assert "\033[" in prefix

    def test_inactive_returns_empty(self):
        """无入场动效时返回空字符串。"""
        comp = _SimpleComponent()
        assert comp._get_bounce_prefix() == ""

    def test_ends_after_six_frames(self):
        """6 帧后返回空字符串。"""
        ctx = AnimatorContext.get_default()
        for _ in range(30):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        assert comp._get_bounce_prefix() != ""
        for _ in range(6):
            ctx.tick()
        assert comp._entry_phase == 1.0
        assert comp._get_bounce_prefix() == ""

    def test_matches_direct_call_frame_0(self):
        """首帧 _get_bounce_prefix 与 build_bounce_ansi(0, 6) 一致。"""
        ctx = AnimatorContext.get_default()
        for _ in range(10):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        assert comp._get_bounce_prefix() == build_bounce_ansi(0, 6)

    def test_matches_direct_call_frame_2(self):
        """第 3 帧时 frame_offset=2，与 build_bounce_ansi(2, 6) 一致。"""
        ctx = AnimatorContext.get_default()
        for _ in range(10):
            ctx.tick()
        comp = _SimpleComponent()
        comp.set_entry_frame(ctx.frame)
        ctx.tick()
        ctx.tick()
        assert comp._get_bounce_prefix() == build_bounce_ansi(2, 6)


class TestRenderToAdapter:
    """render_to_adapter 入场动效包裹测试"""

    def test_with_entry_wraps_with_raw(self, mock_adapter):
        """入场动效激活时使用 write_raw 包裹输出。"""
        comp = _SimpleComponent("test text")
        ctx = AnimatorContext.get_default()
        for _ in range(40):
            ctx.tick()
        comp.set_entry_frame(ctx.frame)
        comp.render_to_adapter(mock_adapter)
        mock_adapter.write_raw.assert_called_once()
        args = mock_adapter.write_raw.call_args[0][0]
        assert "\033[" in args
        assert "test text" in args
        assert "\033[0m" in args

    def test_without_animation_uses_write(self, mock_adapter):
        """无入场动效时使用 adapter.write。"""
        comp = _SimpleComponent("test text")
        comp.render_to_adapter(mock_adapter)
        mock_adapter.write.assert_called_once()

    def test_content_lines_unaffected(self, mock_adapter):
        """入场动效不影响行数估算。"""
        comp = _SimpleComponent("line1\nline2\nline3")
        ctx = AnimatorContext.get_default()
        for _ in range(50):
            ctx.tick()
        comp.set_entry_frame(ctx.frame)
        result = comp.render_to_adapter(mock_adapter)
        assert result == 3

    def test_no_entry_returns_zero_for_non_str(self, mock_adapter):
        """非 str/Text render 返回 0（不受入场动效影响）。"""
        class _NonStrComponent(TuiComponent):
            def render(self):
                return 123  # not str or Text
        comp = _NonStrComponent()
        comp.set_entry_frame(10)
        result = comp.render_to_adapter(mock_adapter)
        assert result == 0


class TestConcreteBlockInheritance:
    """具体组件继承入场动效基础设施。"""

    def test_base_class_has_entry_attrs(self):
        """TuiComponent 基类有 _entry_frame/set_entry_frame/_entry_phase/_get_bounce_prefix。"""
        assert hasattr(TuiComponent, '_entry_frame')
        assert hasattr(TuiComponent, 'set_entry_frame')
        assert hasattr(TuiComponent, '_entry_phase')
        assert hasattr(TuiComponent, '_get_bounce_prefix')
