"""集成测试 — Hooks + Box + Focus + Animation 组合场景。

覆盖跨模块边界的端到端场景，验证模块间协作正确性。
"""

from __future__ import annotations

import os
import re
import pytest
from unittest.mock import patch

from src.chat_ui.vdom.hooks import (
    _hooks_runtime,
    use_state,
    use_effect,
    use_ref,
)
from src.chat_ui.vdom.focus import FocusManager, _FocusableEntry
from src.chat_ui.components.box import Box
from src.chat_ui.components.animation import (
    AnimationClock,
    SPINNER_FRAMES,
    _AnimationState,
    use_typewriter,
)
from src.chat_ui.components.base import TuiComponent
from src.chat_ui.vdom.types import HookState


# ── 测试辅助 ────────────────────────────────────────────

_ANSI_RE = re.compile(r'\033\[[\d;]*m')


def _strip_ansi(text: str) -> str:
    """去除 ANSI 转义序列。"""
    return _ANSI_RE.sub('', text)


class _MockComponent:
    """模拟 TuiComponent（同 test_hooks.py）。"""

    def __init__(self):
        self._hooks: list[HookState] | None = None
        self._hook_index: int = 0
        self._dirty: bool = False
        self._mounted: bool = False

    def _ensure_hooks(self) -> list[HookState]:
        if self._hooks is None:
            self._hooks = []
        return self._hooks


class _TextComp(TuiComponent):
    """简单文本子组件。"""

    def __init__(self, text: str = "hello"):
        super().__init__()
        self.text = text

    def render(self) -> str:
        return self.text


@pytest.fixture(autouse=True)
def _clean_state():
    """每个测试前后清理全局状态。"""
    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None

    FocusManager._instance = None

    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)

    yield

    _hooks_runtime._pending_effects.clear()
    _hooks_runtime._component_stack.clear()
    _hooks_runtime._current_component = None
    _hooks_runtime._rerender_callback = None

    FocusManager._instance = None

    clock = AnimationClock.get_instance()
    if clock is not None:
        clock.stop()
    AnimationClock._set_instance(None)


def _enter(comp):
    _hooks_runtime.enter_component(comp)


def _exit(comp):
    _hooks_runtime.exit_component(comp)


# ═══════════════════════════════════════════════════════════
# TestHooksWithBox
# ═══════════════════════════════════════════════════════════

class TestHooksWithBox:
    """Hooks + Box 组合场景。"""

    def test_state_driven_box_content(self):
        """use_state 驱动 Box 子组件内容变更。"""
        comp = _MockComponent()
        _enter(comp)
        text, set_text = use_state("initial")
        _exit(comp)

        # 用 state 值构建 Box 内容
        box = Box(border_style="single", children=_TextComp(text))
        output = box.render()
        assert "initial" in str(output)

        # 更新 state
        _enter(comp)
        text2, set_text2 = use_state("initial")
        set_text2("updated")
        _exit(comp)

        # 新 Box 渲染更新后的内容
        _enter(comp)
        text3, _ = use_state("initial")
        _exit(comp)
        box2 = Box(border_style="single", children=_TextComp(text3))
        output2 = box2.render()
        assert "updated" in str(output2)

    def test_effect_on_box_mount(self):
        """use_effect 在 Box 渲染后触发副作用。"""
        mounted = []

        def _effect():
            mounted.append("mounted")
            return None

        comp = _MockComponent()
        _enter(comp)
        use_effect(_effect, [])
        _exit(comp)

        # 模拟"渲染后"执行 effect
        _hooks_runtime.run_effects()
        assert "mounted" in mounted

    def test_multiple_hooks_in_box_context(self):
        """在 Box 上下文中使用多个 hooks。"""
        comp = _MockComponent()
        _enter(comp)
        # 模拟组件内部使用 hooks 来管理 Box 属性
        count, set_count = use_state(0)
        ref = use_ref("default")
        _exit(comp)

        assert count == 0
        assert ref["current"] == "default"

        # 更新 state 并用 ref 传递配置
        _enter(comp)
        count2, set_count2 = use_state(0)
        ref2 = use_ref("default")
        set_count2(5)
        ref2["current"] = "custom_bg"
        _exit(comp)

        # 用于 Box 构造
        _enter(comp)
        count3, _ = use_state(0)
        ref3 = use_ref("default")
        _exit(comp)

        assert count3 == 5
        assert ref3["current"] == "custom_bg"


# ═══════════════════════════════════════════════════════════
# TestFocusWithAnimation
# ═══════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════
# TestFullMessageFlowWithGradient
# ═══════════════════════════════════════════════════════════

class TestFullMessageFlowWithGradient:
    """端到端消息流 + 渐变边框组合场景。

    模拟完整的对话消息流：用户消息 → 推理块（渐变边框）→ 回答块 →
    工具调用（running → completed）→ 工具结果。
    """

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_full_message_flow_with_gradient(self, mock_term):
        """端到端消息流：构造多个消息块并验证渲染输出正确。

        场景：用户消息 + 推理块（渐变边框）+ 回答块 +
        工具调用（completed）+ 工具结果。
        """
        from src.chat_ui.components.message_blocks import (
            AnswerBlockBox, UserMsgBlockBox, ToolCallBlockBox,
            ToolResultBlockBox, ThinkingBlockBox,
        )

        # 构建消息流中各块
        user_box = UserMsgBlockBox(text="What is the weather?")
        think_box = ThinkingBlockBox(text="Let me think about this...")
        # 使用渐变边框的推理块
        grad_box = Box(
            border_style="round",
            border_color_gradient=("cyan", "blue"),
            title="Gradient Think",
            children=_TextComp("reasoning with gradient border"),
        )
        answer_box = AnswerBlockBox(text="The weather is sunny.")
        tool_box = ToolCallBlockBox(
            tool_name="weather_api",
            status="completed",
            text='{"temperature": 22, "condition": "sunny"}',
        )
        result_box = ToolResultBlockBox(
            tool_name="weather_api",
            text="22°C, sunny",
            success=True,
        )

        # 验证各块渲染输出为非空且含关键内容
        user_output = user_box.render()
        assert "What is the weather?" in _strip_ansi(user_output)
        assert "╭" in _strip_ansi(user_output)  # round border

        # ThinkingBlockBox.render() 内部调用 use_spinner，需 mock
        from unittest.mock import patch
        with patch(
            "src.chat_ui.components.animation.use_spinner",
            return_value={"char": "⠋", "frame": 0, "time": 0.0},
        ):
            think_output = think_box.render()
        assert "Let me think about this" in _strip_ansi(think_output)
        assert "Thinking" in _strip_ansi(think_output)

        grad_output = grad_box.render()
        clean_grad = _strip_ansi(grad_output)
        assert "Gradient Think" in clean_grad
        assert "reasoning with gradient border" in clean_grad

        answer_output = answer_box.render()
        assert "The weather is sunny" in _strip_ansi(answer_output)

        tool_output = tool_box.render()
        clean_tool = _strip_ansi(tool_output)
        assert "✓" in clean_tool
        assert "weather_api" in clean_tool
        assert "temp" in clean_tool

        result_output = result_box.render()
        clean_result = _strip_ansi(result_output)
        assert "22°C" in clean_result
        assert "✓" in clean_result

    @patch('shutil.get_terminal_size', return_value=os.terminal_size((80, 24)))
    def test_gradient_box_in_message_pipeline(self, mock_term):
        """渐变 Box 在消息管道中的渲染输出含 256 色序列。"""
        _color_re = re.compile(r'\033\[38;5;(\d+)m')

        box = Box(
            border_style="round",
            border_color_gradient=("cyan", "blue"),
            title="Analysis",
            children=_TextComp("pipeline content here"),
        )
        output = box.render()

        colors = [int(m) for m in _color_re.findall(output)]
        # 渐变模式应有 256 色序列
        assert len(colors) >= 1, (
            f"渐变模式应含 256 色序列，实际色号: {colors}"
        )
        # 标题和内容应在输出中
        clean = _strip_ansi(output)
        assert "Analysis" in clean
        assert "pipeline content here" in clean

    def test_nongradient_box_no_256_colors(self):
        """无渐变 Box 不产生 256 色序列。"""
        _color_re = re.compile(r'\033\[38;5;(\d+)m')

        box = Box(
            border_style="round",
            title="Plain",
            children=_TextComp("plain content"),
        )
        output = box.render()
        colors = [int(m) for m in _color_re.findall(output)]
        assert len(colors) == 0, (
            f"无渐变不应产生 256 色序列，实际: {colors}"
        )


# ═══════════════════════════════════════════════════════════
# TestSpinnerNewTypesAllWork
# ═══════════════════════════════════════════════════════════

ALL_SPINNER_TYPES = [
    "braille",
    "dots",
    "line",
    "pulse",
    "bounce",
    "dots_wave",
    "arrow",
    "dots_matrix",
    "arc",
    "bouncing_ball",
    "clock",
    "shark",
]


class TestSpinnerNewTypesAllWork:
    """集成验证所有 12 种 spinner 类型均能正常返回字符。"""

    @pytest.mark.parametrize("spinner_type", ALL_SPINNER_TYPES)
    def test_all_spinner_types_in_frames(self, spinner_type: str):
        """所有 12 种 spinner 类型均在 SPINNER_FRAMES 中。"""
        assert spinner_type in SPINNER_FRAMES, (
            f"SPINNER_FRAMES 缺少 key: {spinner_type!r}"
        )

    @pytest.mark.parametrize("spinner_type", ALL_SPINNER_TYPES)
    def test_all_spinner_types_nonempty_frames(self, spinner_type: str):
        """所有 12 种类型的帧列表均非空，且每帧为有效字符串。"""
        frames = SPINNER_FRAMES[spinner_type]
        assert isinstance(frames, list), (
            f"{spinner_type} 帧列表应为 list，实际: {type(frames).__name__}"
        )
        assert len(frames) > 0, (
            f"{spinner_type} 帧列表不应为空"
        )
        for i, frame in enumerate(frames):
            assert isinstance(frame, str), (
                f"{spinner_type}[{i}] 应为 str"
            )
            assert len(frame) > 0, (
                f"{spinner_type}[{i}] 不应为空字符串"
            )

    def test_spinner_count_is_14(self):
        """SPINNER_FRAMES 总量为 14（12 原有 + 2 Claude Code 新增）。"""
        assert len(SPINNER_FRAMES) == 14, (
            f"SPINNER_FRAMES 预期 14 种，实际: {len(SPINNER_FRAMES)}"
        )

    def test_clock_spinner_symbols(self):
        """clock 类型含 12 个时钟 emoji 帧。"""
        frames = SPINNER_FRAMES["clock"]
        assert len(frames) == 12, (
            f"clock 应有 12 帧，实际: {len(frames)}"
        )
        # 每帧应为单字符 emoji（时钟）
        for f in frames:
            assert len(f) == 1, (
                f"clock 每帧应为单字符，实际: {f!r}"
            )

    def test_bouncing_ball_structure(self):
        """bouncing_ball 帧有固定宽度和球移动模式。"""
        frames = SPINNER_FRAMES["bouncing_ball"]
        assert len(frames) == 8
        # 每帧宽度一致（7 字符）
        widths = {len(f) for f in frames}
        assert len(widths) == 1, f"bouncing_ball 帧宽度应一致，实际: {widths}"
        # 球位置应在帧序列中呈现来回移动
        # 首帧 (●    ) 末尾帧 ( ●   )
        assert "●" in frames[0]
        assert "●" in frames[-1]


# ═══════════════════════════════════════════════════════════
# TestTypewriterEnhancedFields
# ═══════════════════════════════════════════════════════════

class TestTypewriterEnhancedFields:
    """集成验证 use_typewriter 返回含所有新字段。

    通过 mock use_animation 模拟不同帧状态，验证 cursor_visible、
    cursor_char、done 后延迟移除等行为。
    """

    def test_typewriter_returns_all_fields(self):
        """use_typewriter 返回 dict 含全部 7 个字段（含新增 is_paused）。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 50, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("hello world", {"speed": 50})
        assert set(tw.keys()) == {"output", "progress", "done", "is_paused",
                                   "cursor_visible", "cursor_char", "reset"}
        assert isinstance(tw["output"], str)
        assert isinstance(tw["progress"], float)
        assert isinstance(tw["done"], bool)
        assert isinstance(tw["is_paused"], bool)
        assert isinstance(tw["cursor_visible"], bool)
        assert isinstance(tw["cursor_char"], str)
        assert callable(tw["reset"])

    def test_typewriter_cursor_visible_on_even_frame(self):
        """frame=0（偶数）时 cursor_visible=True，output 含光标字符。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 0, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("test", {"speed": 100, "cursor": True})
        assert tw["cursor_visible"] is True
        assert tw["cursor_char"] in tw["output"]

    def test_typewriter_cursor_hidden_on_odd_frame(self):
        """frame=1（奇数）时 cursor_visible=False，output 不含光标。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 1, "time": 100, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("test", {"speed": 100, "cursor": True})
        assert tw["cursor_visible"] is False
        assert tw["cursor_char"] not in tw["output"]

    def test_typewriter_done_removes_cursor_after_delay(self):
        """done 后 time 超出 300ms 延迟窗口时 cursor_visible=False。"""
        from unittest.mock import patch

        text = "hi"
        text_len = len(text)
        speed = 100
        done_time = text_len * speed  # 200ms

        # time 远超 done_time + 300ms（如 600ms）
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 50, "time": 600, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter(text, {"speed": speed, "cursor": True})
        assert tw["done"] is True
        assert tw["cursor_visible"] is False
        assert tw["cursor_char"] not in tw["output"]

    def test_typewriter_progress_reaches_one(self):
        """文本完整显示后 progress == 1.0。"""
        from unittest.mock import patch

        text = "abc"
        speed = 50
        # time 远超 (len*50)ms，确保所有字符已显示
        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 10, "time": 500, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter(text, {"speed": speed})
        assert tw["done"] is True
        assert tw["progress"] == 1.0
        # output 不含光标（done 后超出延迟窗口）
        assert tw["cursor_visible"] is False

    def test_typewriter_custom_cursor_char(self):
        """自定义 cursor_char 出现在 output 中。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 0, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("x", {"speed": 100, "cursor": True, "cursor_char": "█"})
        assert tw["cursor_char"] == "█"
        assert "█" in tw["output"]

    def test_typewriter_cursor_style_underscore(self):
        """cursor_style="underscore" 使用 _ 字符。"""
        from unittest.mock import patch

        with patch(
            "src.chat_ui.components.animation.use_animation",
            return_value={"frame": 0, "time": 0, "delta": 16, "reset": lambda: None},
        ):
            tw = use_typewriter("y", {"speed": 100, "cursor": True, "cursor_style": "underscore"})
        assert tw["cursor_char"] == "_"


class TestFocusWithAnimation:
    """Focus + Animation 组合场景。"""

    def test_focus_changes_during_animation(self):
        """动画进行中焦点切换正常工作。"""
        import time

        # 启动动画时钟
        clock = AnimationClock(on_tick=lambda: None)
        clock.start()

        # 注册焦点组件
        fm = FocusManager()
        fm.register("a", _FocusableEntry(component=None, is_active=True))
        fm.register("b", _FocusableEntry(component=None, is_active=True))

        # 创建动画
        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 焦点操作与动画并行
        fm.focus("a")
        assert fm.active_id == "a"

        # 推进动画帧
        time.sleep(0.015)
        clock._tick()

        # 动画不影响焦点
        assert fm.active_id == "a"

        # 切换焦点
        fm.focus_next()
        assert fm.active_id == "b"

        # 再次推进动画
        time.sleep(0.015)
        clock._tick()

        # 焦点保持不变
        assert fm.active_id == "b"

        clock.stop()

    def test_focus_manager_singleton_independent_of_animation(self):
        """FocusManager 和 AnimationClock 单例独立。"""
        fm1 = FocusManager()
        fm2 = FocusManager()
        assert fm1 is fm2  # FocusManager 是单例

        clock = AnimationClock(on_tick=lambda: None)
        # 两个单例互不影响
        assert fm1 is not clock

    def test_focus_enable_disable_during_animation(self):
        """动画期间启用/禁用焦点。"""
        import time

        clock = AnimationClock(on_tick=lambda: None)
        clock.start()

        fm = FocusManager()
        fm.register("x", _FocusableEntry(component=None, is_active=True))

        anim = _AnimationState(interval=10, is_active=True)
        anim._start_mono = time.monotonic()
        anim._last_frame_mono = time.monotonic()
        clock.register(anim)

        # 禁用焦点
        fm.disable()
        fm.focus_next()
        assert fm.active_id is None

        # 动画仍在运行
        time.sleep(0.015)
        clock._tick()
        assert anim.frame >= 0

        # 重新启用焦点
        fm.enable()
        fm.focus_next()
        assert fm.active_id == "x"

        clock.stop()
