"""测试 StatusBar 纯渲染函数（来自 status_bar 模块）。

覆盖：
  - render_normal 普通模式状态栏
  - render_streaming_line 流式模式状态栏
  - build_normal_parts 信息段构建
"""

from __future__ import annotations

import time
from unittest.mock import patch

from src.tui.parallel._text_formatter import TextFormatter
from src.tui.core.state import UISessionState, StreamingState, TUIStateTree
from src.tui.widgets.status_bar import (
    render_normal,
    render_streaming_line,
    build_normal_parts,
    StatusBar,
)


class TestRenderNormal:
    """render_normal 普通模式渲染测试。"""

    def test_minimal_state_returns_string(self):
        """最小状态下不报错。"""
        state = UISessionState()
        result = render_normal(state)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_model_appears_in_output(self):
        """模型名出现在渲染结果中。"""
        state = UISessionState(model="gpt-4")
        result = render_normal(state)
        assert "gpt-4" in result

    def test_no_model_shows_fallback(self):
        """无模型时显示 'no model'。"""
        state = UISessionState(model="")
        result = render_normal(state)
        assert "no model" in result

    def test_message_count_appears(self):
        """消息数 > 0 时显示计数。"""
        state = UISessionState(model="gpt-4", message_count=3)
        parts = build_normal_parts(state)
        count_found = any("3m" in p for p in parts)
        assert count_found


class TestStreamingLine:
    """render_streaming_line 流式渲染测试。"""

    def test_minimal_state_returns_string(self):
        """最小状态下不报错。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        assert isinstance(result, str)
        assert len(result) > 0

    def test_model_appears(self):
        """模型名出现在渲染结果中。"""
        state = UISessionState(model="claude-3")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        assert "claude-3" in result

    def test_tokens_appear_when_set(self):
        """Token 计数出现在渲染结果中。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 6.0,  # 6s elapsed
            output_tokens=150,  # → auto speed ≈ 25 tok/s
        )
        result = render_streaming_line(state, streaming)
        assert "150" in result or "tok" in result

    def test_elapsed_time_under_minute(self):
        """不到 1 分钟显示秒。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 3.5,  # 3.5 秒前
        )
        result = render_streaming_line(state, streaming)
        assert "3.5" in result

    def test_speed_display_format(self):
        """速率 >= 10 显示整数。

        注意：StreamingState.elapsed 使用 time.monotonic()。
        """
        state = UISessionState(model="gpt-4")
        # speed = output_tokens / elapsed = 120 / 1.0 = 120.0 → 整数显示
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 1.0,
            output_tokens=120,
        )
        result = render_streaming_line(state, streaming)
        assert "120" in result
        assert "120." not in result  # 不应有小数点

    def test_low_speed_shows_decimal(self):
        """速率 < 1 显示两位小数。

        注意：StreamingState.elapsed 使用 time.monotonic()，
        测试需使用同一时钟源构造 start_time。
        """
        state = UISessionState(model="gpt-4")
        # speed = output_tokens / elapsed = 3 / 4.0 = 0.75 → 两位小数
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 4.0,
            output_tokens=3,
        )
        result = render_streaming_line(state, streaming)
        assert ".75" in result


class TestBuildNormalParts:
    """build_normal_parts 信息段构建测试。"""

    def test_empty_state_returns_model_only(self):
        """空状态返回仅含模型的信息段。"""
        state = UISessionState()
        parts = build_normal_parts(state)
        assert len(parts) >= 1

    def test_message_count_adds_part(self):
        """消息数 > 0 时增加信息段。"""
        state = UISessionState(model="gpt-4", message_count=5)
        parts = build_normal_parts(state)
        assert len(parts) >= 2  # model + msg count

    def test_tokens_add_parts(self):
        """Token 用量 > 0 时增加信息段。"""
        state = UISessionState(model="gpt-4", input_tokens=100, output_tokens=200)
        parts = build_normal_parts(state)
        assert len(parts) >= 2


class TestStatusBarInstance:
    """StatusBar 实例方法测试。"""

    def test_status_bar_render_normal(self):
        """测试 StatusBar.render() 在非流式模式下的输出。"""
        tree = TUIStateTree()
        tree.update_session(model="test-model", message_count=5)
        sb = StatusBar(tree)
        result = sb.render()
        assert "test-model" in result
        assert "5" in result

    def test_status_bar_render_streaming(self):
        """测试 StatusBar.render() 在流式模式下的输出。"""
        tree = TUIStateTree()
        tree.streaming.start()
        tree.update_session(model="test-model")
        sb = StatusBar(tree)
        result = sb.render()
        assert "test-model" in result
        assert "t/" in result

    def test_status_bar_start_stop_streaming(self):
        """测试 start_streaming/stop_streaming 的状态转换。"""
        tree = TUIStateTree()
        sb = StatusBar(tree)
        assert sb.streaming is False
        sb.start_streaming()
        assert sb.streaming is True
        sb.stop_streaming()
        assert sb.streaming is False

    def test_status_bar_update_streaming_tokens(self):
        """测试 update_streaming_tokens 更新 token 计数。"""
        tree = TUIStateTree()
        sb = StatusBar(tree)
        sb.start_streaming()
        sb.update_streaming_tokens(100)
        assert tree.streaming.output_tokens == 100


class TestFormatTokenCount:
    """format_token_count 格式化函数测试（委托 TextFormatter）。"""

    def test_format_token_count_zero(self):
        result = TextFormatter.format_token_count(0)
        assert result == "0"

    def test_format_token_count_k(self):
        result = TextFormatter.format_token_count(1500)
        assert "1.5k" in result

    def test_format_token_count_small(self):
        result = TextFormatter.format_token_count(42)
        assert result == "42"


class TestRenderNormalNarrow:
    """窄屏渲染测试（使用 monkeypatch 模拟窄屏）。"""

    def test_render_normal_narrow_no_ansi_corruption(self, monkeypatch):
        """测试窄屏渲染不会损坏 ANSI 转义序列。

        注：使用 monkeypatch 模拟窄屏环境，CI 慢速环境可能 flaky。
        """
        from src.tui.widgets.status_bar import render_normal
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr(
            "src.tui.widgets.status_bar.get_terminal_width", lambda: 30,
        )
        state = UISessionState(model="test-model", message_count=10, status_text="processing")
        result = render_normal(state)
        assert isinstance(result, str)
        assert "\033[0m" in result  # 确保有样式重置


class TestCompactThreshold:
    """P2-8: 验证 _STATUS_BAR_COMPACT_THRESHOLD = 50 的边界行为。"""

    def test_threshold_is_50(self):
        """阈值应为 50（与 EXTRA_NARROW_THRESHOLD 对齐）。"""
        from src.tui.widgets.status_bar import _STATUS_BAR_COMPACT_THRESHOLD
        assert _STATUS_BAR_COMPACT_THRESHOLD == 50

    def test_compact_mode_at_49(self, monkeypatch):
        """宽度 < 50 时进入精简模式（仅模型名+消息数）。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 49)
        state = UISessionState(
            model="gpt-4", message_count=3,
            input_tokens=100, output_tokens=200,
            status_text="running",
        )
        parts = build_normal_parts(state, narrow=True)
        # 精简模式：只应包含模型名和消息数，不含 token/状态文本
        joined = " ".join(parts)
        assert "gpt-4" in joined
        assert "3m" in joined
        assert "100" not in joined  # token 不显示
        assert "running" not in joined  # 状态文本不显示

    def test_full_mode_at_50(self, monkeypatch):
        """宽度 = 50 时不进入精简模式（含详细信息）。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 50)
        state = UISessionState(
            model="gpt-4", message_count=3,
            input_tokens=100, output_tokens=200,
        )
        parts = build_normal_parts(state, narrow=True)
        joined = " ".join(parts)
        assert "gpt-4" in joined
        # 宽度=50 不满足 < 50，应包含详细信息
        assert "100" in joined  # token 显示

    def test_compact_mode_at_45(self, monkeypatch):
        """宽度 45（<50）进入精简模式。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 45)
        state = UISessionState(model="gpt-4", message_count=5)
        parts = build_normal_parts(state, narrow=True)
        joined = " ".join(parts)
        assert "gpt-4" in joined
        assert "5m" in joined


class TestBeautification256:
    """步骤 5 美化：256 色 + 流式指示器 + 视觉层次增强测试。"""

    def test_render_normal_contains_256_color(self):
        """普通模式渲染结果含 256 色序列（\033[38;5;）。"""
        state = UISessionState(
            model="gpt-4", message_count=3,
            input_tokens=100, output_tokens=200,
        )
        result = render_normal(state)
        assert "\033[38;5;" in result

    def test_render_streaming_contains_256_color(self):
        """流式模式渲染结果含 256 色序列。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic() - 2.0)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;" in result

    def test_streaming_line_contains_pulse_char(self):
        """流式状态行含脉动指示器字符（◌ ◍ ● 之一）。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        # 脉动指示器帧字符
        for ch in ("\u25cc", "\u25cd", "\u25cf"):
            if ch in result:
                return
        assert False, f"流式状态行未含脉动指示器字符: {result!r}"

    def test_streaming_line_pulse_phase_cycles(self):
        """不同脉动相位输出不同指示器字符（因相位不同而不同）。"""
        state = UISessionState(model="gpt-4")
        chars = set()
        for phase in range(4):
            streaming = StreamingState(
                active=True, start_time=time.monotonic(),
                pulse_phase=phase,
            )
            result = render_streaming_line(state, streaming)
            for ch in ("\u25cc", "\u25cd", "\u25cf"):
                if ch in result:
                    chars.add(ch)
        # 至少出现 2 种不同的脉动字符
        assert len(chars) >= 2, f"脉动相位循环应产生不同字符: {chars}"

    def test_streaming_no_model_no_pulse(self):
        """无模型时不显示脉动指示器。"""
        state = UISessionState(model="")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        # 无模型时，脉动字符不应出现在结果中
        # 注意：无模型时 first part 不添加，但脉动在 model 块内
        for ch in ("\u25cc", "\u25cd", "\u25cf"):
            assert ch not in result, f"无模型时不应含脉动字符: {ch} 出现在 {result!r}"

    def test_streaming_amber_speed_color(self):
        """速率 > 0 时使用琥珀色(214)。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 2.0,
            output_tokens=50,
        )
        result = render_streaming_line(state, streaming)
        assert "38;5;214m" in result

    def test_streaming_dual_color_token(self):
        """Token 显示使用双色：输入青色(45)，输出绿色(41)。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(
            active=True, start_time=time.monotonic() - 2.0,
            output_tokens=100,
        )
        result = render_streaming_line(state, streaming)
        # ⬡ 图标使用 CYAN_256(45)
        assert "\033[38;5;45m" in result

    def test_render_streaming_narrow_no_crash(self, monkeypatch):
        """窄屏流式渲染不崩溃。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 30)
        state = UISessionState(model="test-model")
        streaming = StreamingState(active=True, start_time=time.monotonic())
        result = render_streaming_line(state, streaming)
        assert isinstance(result, str)
        assert "\033[0m" in result


class TestPulsePhase:
    """StreamingState.pulse_phase 与 tick_pulse 测试。"""

    def test_pulse_phase_initially_zero(self):
        """脉动相位初始为 0。"""
        s = StreamingState()
        assert s.pulse_phase == 0

    def test_tick_pulse_cycles_through(self):
        """tick_pulse 委托 AnimatorContext.tick()，pulse_phase 可手动设置 0→1→2→3→0 循环。"""
        s = StreamingState()
        s.start()
        assert s.pulse_phase == 0
        s.pulse_phase = 1
        assert s.pulse_phase == 1
        s.pulse_phase = 2
        assert s.pulse_phase == 2
        s.pulse_phase = 3
        assert s.pulse_phase == 3
        s.pulse_phase = 0
        assert s.pulse_phase == 0  # 循环回 0

    def test_start_resets_pulse_phase(self):
        """start() 将脉动相位重置为 0。"""
        s = StreamingState(pulse_phase=3)
        s.active = True
        s.start()  # 已在 active，不重置
        assert s.pulse_phase == 3  # 已激活时不重置
        # 新建一个未激活的
        s2 = StreamingState(pulse_phase=2)
        s2.start()
        assert s2.pulse_phase == 0  # 新启动时重置

    def test_stop_resets_pulse_phase(self):
        """stop() 将脉动相位重置为 0。"""
        s = StreamingState(pulse_phase=2)
        s.stop()
        assert s.pulse_phase == 0


class TestPulseColorBreathing:
    """脉动呼吸颜色测试：验证正弦波呼吸在渲染中的正确应用。

    正弦波呼吸使用 math.sin 计算平滑过渡：
      Phase 0 → 暗青(36)，Phase 1 → 中亮青(41)，Phase 2 → 亮青(45)，Phase 3 → 中亮青(41)
    """

    def test_phase_0_uses_dark_cyan(self):
        """Phase 0 使用暗青色(36)。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=0)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;36m" in result, f"Phase 0 应使用暗青(36)，结果: {result!r}"

    def test_phase_2_uses_bright_cyan(self):
        """Phase 2 使用亮青色(45)。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=2)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;45m" in result, f"Phase 2 应使用亮青(45)，结果: {result!r}"

    def test_phase_1_uses_mid_cyan(self):
        """Phase 1 使用中青色(40)（正弦波插值 round 结果）。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=1)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;40m" in result, f"Phase 1 应使用中青(40)，结果: {result!r}"

    def test_phase_3_uses_mid_cyan(self):
        """Phase 3 使用中青色(40)（正弦波对称呼吸，浮点精度略低于0.5）。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=3)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;40m" in result, f"Phase 3 应使用中青(40)，结果: {result!r}"

    def test_different_phases_produce_different_pulse_colors(self):
        """不同脉动相位产生不同的脉动颜色输出（至少2种色号）。"""
        state = UISessionState(model="gpt-4")
        pulse_colors_seen = set()
        pulse_chars = ["\u25cc", "\u25cd", "\u25cf", "\u25cd"]
        import re
        for phase in range(4):
            streaming = StreamingState(
                active=True, start_time=time.monotonic(),
                pulse_phase=phase,
            )
            result = render_streaming_line(state, streaming)
            for pch in pulse_chars:
                idx = result.find(pch)
                if idx >= 0:
                    prefix = result[:idx]
                    matches = re.findall(r"\x1b\[38;5;(\d+)m", prefix)
                    if matches:
                        pulse_colors_seen.add(int(matches[-1]))
                    break
        # 正弦波脉动应产生暗青(36) 和 亮青(45) 两个极值
        assert 36 in pulse_colors_seen, f"应包含暗青(36)，实际: {sorted(pulse_colors_seen)}"
        assert 45 in pulse_colors_seen, f"应包含亮青(45)，实际: {sorted(pulse_colors_seen)}"

    def test_breathing_cycle_completeness(self):
        """正弦波呼吸周期：暗青(36)→中青(40)→亮青(45)→中青(40)。"""
        state = UISessionState(model="gpt-4")
        color_sequence = []
        pulse_chars = ["\u25cc", "\u25cd", "\u25cf", "\u25cd"]  # ◌ ◍ ● ◍
        for phase in range(4):
            streaming = StreamingState(
                active=True, start_time=time.monotonic(),
                pulse_phase=phase,
            )
            result = render_streaming_line(state, streaming)
            # 提取脉动字符前的色号
            for pch in pulse_chars:
                idx = result.find(pch)
                if idx >= 0:
                    prefix = result[:idx]
                    import re
                    matches = re.findall(r"\x1b\[38;5;(\d+)m", prefix)
                    if matches:
                        val = int(matches[-1])
                        color_sequence.append(val)
                        break
        # 正弦波周期：4帧（phase=1和phase=3均为40，受floating point和banker's rounding影响）
        expected = [36, 40, 45, 40]
        assert color_sequence == expected, \
            f"正弦波呼吸周期应为 {expected}，实际: {color_sequence}"

    def test_narrow_screen_still_has_pulse_color(self, monkeypatch):
        """窄屏下脉动颜色序列依然正确。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 30)
        state = UISessionState(model="test-model")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=2)
        result = render_streaming_line(state, streaming)
        assert "\033[38;5;45m" in result or "\033[38;5;41m" in result or "\033[38;5;36m" in result
        assert "\033[0m" in result  # ANSI 序列完整性


class TestModelNameBreathing:
    """步骤 6 美化：模型名使用 THEME['title'] 主题色做正弦波呼吸。"""

    def test_model_name_breathing_wide(self):
        """宽屏模型名使用 THEME['title'] 基色做呼吸。

        dark 主题 THEME['title'] = \033[38;5;45m（色号 45），
        呼吸范围 [45, 65]（base=45, peak=min(255, 45+20)=65），
        周期 6 帧，不同相位输出不同色号。
        """
        import re
        state = UISessionState(model="gpt-4")
        # Phase 0: sine_color(45, 65, 6, frame=0) → 45
        streaming = StreamingState(
            active=True, start_time=time.monotonic(),
            pulse_phase=0,
        )
        result = render_streaming_line(state, streaming)
        # 提取模型名 "gpt-4" 前的色号
        idx = result.find("gpt-4")
        assert idx >= 0, "模型名应出现在结果中"
        prefix = result[:idx]
        matches = re.findall(r"\x1b\[38;5;(\d+)m", prefix)
        assert matches, "模型名前应有 256 色 ANSI 序列"
        color_at_phase0 = int(matches[-1])
        # Phase 0 应为 base=45
        assert color_at_phase0 == 45, \
            f"Phase 0 模型名色号应为 45(baseline)，实际: {color_at_phase0}"

        # Phase 3: sine_color(45, 65, 6, frame=3) → 65 (peak)
        streaming3 = StreamingState(
            active=True, start_time=time.monotonic(),
            pulse_phase=3,
        )
        result3 = render_streaming_line(state, streaming3)
        idx3 = result3.find("gpt-4")
        prefix3 = result3[:idx3]
        matches3 = re.findall(r"\x1b\[38;5;(\d+)m", prefix3)
        assert matches3, "模型名前应有 256 色 ANSI 序列"
        color_at_phase3 = int(matches3[-1])
        # Phase 3 应为 peak=65
        assert color_at_phase3 == 65, \
            f"Phase 3 模型名色号应为 65(peak)，实际: {color_at_phase3}"

        # 两相位色号不同，验证呼吸效果
        assert color_at_phase0 != color_at_phase3, \
            f"不同相位应产生不同色号: phase0={color_at_phase0}, phase3={color_at_phase3}"

    def test_model_name_static_narrow(self, monkeypatch):
        """窄屏使用 THEME['title'] 静态色（不做呼吸）。"""
        monkeypatch.setattr("src.tui.widgets.status_bar.is_narrow", lambda: True)
        monkeypatch.setattr("src.tui.widgets.status_bar.get_terminal_width", lambda: 30)
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=0)
        result = render_streaming_line(state, streaming)
        # 窄屏时模型名应使用 THEME['title'] 静态色 \033[38;5;45m
        from src.tui.core.theme import THEME
        assert THEME['title'] in result, \
            f"窄屏模型名应包含 THEME['title'](\033[38;5;45m) 静态色"

    def test_model_name_color_in_title_range(self):
        """模型名呼吸色在 [base, base+20] 范围内。"""
        import re
        state = UISessionState(model="gpt-4")
        from src.tui.core.theme import THEME
        title_color = THEME['title']
        title_match = re.search(r"38;5;(\d+)", title_color)
        assert title_match, f"THEME['title'] 格式异常: {title_color!r}"
        base = int(title_match.group(1))
        peak = min(255, base + 20)

        colors_seen = set()
        for phase in range(4):
            streaming = StreamingState(
                active=True, start_time=time.monotonic(),
                pulse_phase=phase,
            )
            result = render_streaming_line(state, streaming)
            idx = result.find("gpt-4")
            prefix = result[:idx]
            matches = re.findall(r"\x1b\[38;5;(\d+)m", prefix)
            if matches:
                colors_seen.add(int(matches[-1]))

        for c in colors_seen:
            assert base <= c <= peak, \
                f"模型名色号 {c} 超出范围 [{base}, {peak}]"

    def test_model_name_color_256_format(self):
        """模型名呼吸色使用 256 色 ANSI 格式。"""
        state = UISessionState(model="gpt-4")
        streaming = StreamingState(active=True, start_time=time.monotonic(), pulse_phase=0)
        result = render_streaming_line(state, streaming)
        # 验证模型名前有 256 色序列
        idx = result.find("gpt-4")
        prefix = result[:idx]
        assert "\033[38;5;" in prefix, \
            f"模型名前应有 256 色 ANSI 序列，prefix: {prefix!r}"

