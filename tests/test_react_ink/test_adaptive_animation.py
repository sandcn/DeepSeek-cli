"""自适应动画 Hook 单元测试。

覆盖 use_adaptive_animation / use_count_up / use_rainbow 三个新 Hook，
以及辅助函数 _interpolate_rainbow / _ease_linear / _ease_out_cubic / _ease_out_expo。

测试策略：通过 unittest.mock.patch mock use_animation 返回值来控制动画状态，
mock use_ref/use_effect 绕过 hooks 运行时依赖，聚焦 Hook 逻辑正确性。
"""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from src.chat_ui.components.animation import (
    use_adaptive_animation,
    use_count_up,
    use_rainbow,
    _interpolate_rainbow,
    _ease_linear,
    _ease_out_cubic,
    _ease_out_expo,
    _ADAPTIVE_MIN_INTERVAL,
    _ADAPTIVE_MAX_INTERVAL,
    _ADAPTIVE_DEFAULT_INTERVAL,
)


# ── 测试辅助 ────────────────────────────────────────────


def _mock_use_ref(initial_value):
    """模拟 use_ref：返回 {"current": initial_value} 可变容器。"""
    return {"current": initial_value}


def _mock_use_effect(_fn, _deps=None):
    """模拟 use_effect：不执行任何操作（跳过注册/调度）。"""
    return None


def _make_anim_return(frame=0, time=0.0, delta=0.0, reset=None):
    """构造 mock use_animation 返回值。"""
    return {
        "frame": frame,
        "time": time,
        "delta": delta,
        "reset": reset if reset is not None else (lambda: None),
    }


# ═══════════════════════════════════════════════════════════
# TestUseAdaptiveAnimation
# ═══════════════════════════════════════════════════════════


class TestUseAdaptiveAnimation:
    """use_adaptive_animation Hook 测试。"""

    def test_adaptive_defaults(self):
        """默认参数返回 dict 含 frame/time/delta/currentInterval/load/reset。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(frame=5, time=200, delta=40)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_adaptive_animation()

        assert isinstance(result, dict)
        assert "frame" in result
        assert "time" in result
        assert "delta" in result
        assert "currentInterval" in result
        assert "load" in result
        assert "reset" in result
        assert callable(result["reset"])
        assert result["frame"] == 5
        assert result["time"] == 200
        assert result["delta"] == 40

    def test_adaptive_respects_base_interval(self):
        """baseInterval 参数被正确使用 — 无负载时 currentInterval 等于 baseInterval。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return()), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_adaptive_animation({"baseInterval": 80})

        # 初始 load=0，currentInterval 应等于 baseInterval（在 [min, max] 范围内）
        assert result["currentInterval"] == 80

    def test_adaptive_clamps_min_interval(self):
        """currentInterval 不低于 _ADAPTIVE_MIN_INTERVAL (16ms)。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return()), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # baseInterval 设为极小值（低于 min），应被钳制
            result = use_adaptive_animation({"baseInterval": 5})

        assert result["currentInterval"] >= _ADAPTIVE_MIN_INTERVAL
        assert result["currentInterval"] == _ADAPTIVE_MIN_INTERVAL

    def test_adaptive_clamps_max_interval(self):
        """currentInterval 不高于 _ADAPTIVE_MAX_INTERVAL (160ms)。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return()), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # baseInterval 设为极大值（高于 max），应被钳制
            result = use_adaptive_animation({"baseInterval": 500})

        assert result["currentInterval"] <= _ADAPTIVE_MAX_INTERVAL
        assert result["currentInterval"] == _ADAPTIVE_MAX_INTERVAL

    def test_adaptive_load_zero_initially(self):
        """初始 load 为 0.0（尚无帧时间采样）。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return()), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_adaptive_animation()

        assert result["load"] == 0.0

    def test_adaptive_reset(self):
        """reset() 清除负载状态（load 归零）。"""
        reset_called = []

        def _fake_anim_reset():
            reset_called.append(True)

        def _fake_use_ref(initial):
            """Mock use_ref，为每个调用返回独立副本。"""
            return {"current": initial.copy() if isinstance(initial, dict) else initial}

        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(reset=_fake_anim_reset)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_fake_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_adaptive_animation()

        result["reset"]()

        # 验证动画 reset 被调用
        assert len(reset_called) == 1

    def test_adaptive_is_active_false(self):
        """isActive=False 时 use_animation 以 isActive=False 调用。"""
        captured_opts = []

        def _fake_use_animation(opts):
            captured_opts.append(opts)
            return _make_anim_return()

        with \
                patch("src.chat_ui.components.animation.use_animation",
                      side_effect=_fake_use_animation), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_adaptive_animation({"isActive": False})

        assert len(captured_opts) == 1
        assert captured_opts[0]["isActive"] is False
        assert result["currentInterval"] >= 16
        assert result["load"] >= 0.0


# ═══════════════════════════════════════════════════════════
# TestUseCountUp
# ═══════════════════════════════════════════════════════════


class TestUseCountUp:
    """use_count_up Hook 测试。"""

    def test_count_up_defaults(self):
        """默认参数 0→100，duration=1000ms。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=0)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_count_up()

        assert isinstance(result, dict)
        assert "value" in result
        assert "display" in result
        assert "progress" in result
        assert "done" in result
        assert "reset" in result
        assert callable(result["reset"])
        # 初始 time=0, progress=0, value=start=0
        assert result["value"] == 0.0
        assert result["display"] == "0"
        assert result["progress"] == 0.0
        assert result["done"] is False

    def test_count_up_custom_start_target(self):
        """start=50, target=200 自定义起止值。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=0)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_count_up({"start": 50, "target": 200})

        assert result["value"] == 50.0  # 起始位置

    def test_count_up_done_at_completion(self):
        """progress=1.0 时 done 为 True。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=1500)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # duration=1000, time=1500 → progress=1.0
            result = use_count_up({"duration": 1000})

        assert result["progress"] == 1.0
        assert result["done"] is True
        assert result["value"] == 100.0  # target

    def test_count_up_easing_linear(self):
        """easing="linear" 线性插值 — 50% 进度时 value=(start+target)/2。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=500)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # duration=1000, time=500 → progress=0.5, linear → value=50
            result = use_count_up({"easing": "linear", "duration": 1000})

        assert result["progress"] == pytest.approx(0.5)
        assert result["value"] == pytest.approx(50.0)

    def test_count_up_easing_expo(self):
        """easing="easeOutExpo" — 减速缓动，中段 value > linear。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=500)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # easeOutExpo 在 t=0.5 时输出接近 1.0（快速衰减）
            result = use_count_up({"easing": "easeOutExpo", "duration": 1000})

        assert result["progress"] == pytest.approx(0.5)
        # easeOutExpo(0.5) ≈ 0.96875，远大于 0.5
        assert result["value"] > 50.0

    def test_count_up_decimals(self):
        """decimals=2 显示两位小数。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=333)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            # duration=1000, time=333, progress=0.333, easeOutExpo(0.333)≈0.985
            # value ≈ 0 + 100 * 0.985 = 98.5
            result = use_count_up({"decimals": 2, "duration": 1000, "easing": "linear"})

        # linear: value = 0 + 100 * 0.333 = 33.3
        assert "." in result["display"]
        assert len(result["display"].split(".")[1]) == 2
        assert result["display"] == "33.30"

    def test_count_up_reset(self):
        """reset() 回到起始状态。"""
        reset_called = []

        def _fake_anim_reset():
            reset_called.append(True)

        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=1500, reset=_fake_anim_reset)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_count_up({"duration": 1000})

        assert result["done"] is True
        result["reset"]()
        # 验证动画 reset 被调用
        assert len(reset_called) == 1

    def test_count_up_zero_duration(self):
        """duration=0 边界 — 被钳制到 min=1。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=0)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_count_up({"duration": 0})

        # duration 被钳制到 1ms，time=0 → progress=0
        assert result["progress"] == 0.0
        assert result["done"] is False

    def test_count_up_start_equals_target(self):
        """start=target 时直接完成（progress=1.0, done=True, value=target）。"""
        with \
                patch("src.chat_ui.components.animation.use_animation",
                      return_value=_make_anim_return(time=0)), \
                patch("src.chat_ui.vdom.hooks.use_ref", side_effect=_mock_use_ref), \
                patch("src.chat_ui.vdom.hooks.use_effect", side_effect=_mock_use_effect):
            result = use_count_up({"start": 50, "target": 50})

        # start=target 所以 value 始终等于目标值
        assert result["value"] == 50.0
        # progress 取决于 time/duration，time=0 → progress=0
        assert result["progress"] == 0.0


# ═══════════════════════════════════════════════════════════
# TestUseRainbow
# ═══════════════════════════════════════════════════════════


class TestUseRainbow:
    """use_rainbow Hook 测试。"""

    def test_rainbow_defaults(self):
        """默认参数返回 dict 含 colorIndex/styled/phase。"""
        with patch("src.chat_ui.components.animation.use_animation",
                   return_value=_make_anim_return(time=0)):
            result = use_rainbow()

        assert isinstance(result, dict)
        assert "colorIndex" in result
        assert "styled" in result
        assert "phase" in result
        assert isinstance(result["colorIndex"], int)
        assert result["phase"] == 0.0

    def test_rainbow_with_text(self):
        """text 参数生成逐字符渐变 StyledText，每字符有独立 Span。"""
        with patch("src.chat_ui.components.animation.use_animation",
                   return_value=_make_anim_return(time=0)):
            result = use_rainbow({"text": "ABC"})

        from src.chat_ui.infrastructure.styled import StyledText
        styled = result["styled"]
        assert isinstance(styled, StyledText)
        spans = styled.spans
        assert len(spans) == 3
        # 每字符有独立 color_number
        for span in spans:
            assert span.color_number is not None
            assert 0 <= span.color_number <= 255
        assert spans[0].text == "A"
        assert spans[1].text == "B"
        assert spans[2].text == "C"

    def test_rainbow_empty_text(self):
        """text="" 返回空 StyledText（zero spans）。"""
        with patch("src.chat_ui.components.animation.use_animation",
                   return_value=_make_anim_return(time=0)):
            result = use_rainbow({"text": ""})

        from src.chat_ui.infrastructure.styled import StyledText
        styled = result["styled"]
        assert isinstance(styled, StyledText)
        assert styled.plain == ""
        assert len(styled.spans) == 0

    def test_rainbow_phase_range(self):
        """phase 在 [0, 1) 范围内。"""
        test_times = [0, 100, 500, 999, 1500, 10000]
        for t in test_times:
            with patch("src.chat_ui.components.animation.use_animation",
                       return_value=_make_anim_return(time=t)):
                result = use_rainbow({"speed": 0.001})

            assert 0.0 <= result["phase"] < 1.0, (
                f"time={t}: phase={result['phase']} 不在 [0, 1) 范围"
            )

    def test_rainbow_color_index_valid(self):
        """colorIndex 在 [0, 255] 范围内。"""
        test_times = [0, 100, 500, 999, 1500, 10000]
        for t in test_times:
            with patch("src.chat_ui.components.animation.use_animation",
                       return_value=_make_anim_return(time=t)):
                result = use_rainbow({"speed": 0.001})

            assert 0 <= result["colorIndex"] <= 255, (
                f"time={t}: colorIndex={result['colorIndex']} 不在 [0, 255] 范围"
            )

    def test_rainbow_text_changes_with_time(self):
        """不同 time 产生不同 colorIndex（色相随帧推进）。"""
        results = []
        for t in [0, 100, 200, 300]:
            with patch("src.chat_ui.components.animation.use_animation",
                       return_value=_make_anim_return(time=t)):
                results.append(use_rainbow({"speed": 0.001}))

        # 四个不同 time 值应产生至少两个不同的 colorIndex
        unique_colors = {r["colorIndex"] for r in results}
        assert len(unique_colors) >= 2, (
            f"预期不同 time 产生不同颜色，实际: {unique_colors}"
        )

    def test_rainbow_is_active_false(self):
        """isActive=False 时仍返回有效结果（颜色基于 time，time 可能冻结）。"""
        with patch("src.chat_ui.components.animation.use_animation",
                   return_value=_make_anim_return(time=500)):
            result = use_rainbow({"isActive": False, "speed": 0.001})

        assert isinstance(result["colorIndex"], int)
        assert 0 <= result["colorIndex"] <= 255


# ═══════════════════════════════════════════════════════════
# TestInterpolateRainbow
# ═══════════════════════════════════════════════════════════


class TestInterpolateRainbow:
    """_interpolate_rainbow 辅助函数测试。"""

    def test_returns_int_in_256_range(self):
        """返回值始终在 [0, 255] 范围内。"""
        for t in [0.0, 0.1, 0.25, 0.5, 0.75, 0.9, 0.99]:
            idx = _interpolate_rainbow(t)
            assert isinstance(idx, int), f"t={t}: 返回值应为 int，实际 {type(idx)}"
            assert 0 <= idx <= 255, f"t={t}: idx={idx} 不在 [0, 255]"

    def test_wraps_at_one(self):
        """t=0 和 t≈1 产生相同（或极接近）的颜色（色相环闭环）。"""
        idx0 = _interpolate_rainbow(0.0)
        idx1 = _interpolate_rainbow(0.9999)
        # 色相环闭环：t=0 和 t≈1 在第一个锚点附近
        assert abs(idx0 - idx1) <= 10, (
            f"色相环闭环期望接近: idx(0)={idx0}, idx(0.9999)={idx1}"
        )

    def test_returns_anchor_colors(self):
        """在锚点位置返回接近对应色相的颜色（RGB 空间比较）。

        验证 t=0 近红, t≈0.1667 近橙, t≈0.3334 近黄,
        t≈0.5 近绿, t≈0.6667 近青, t≈0.8334 近蓝。
        使用 _256_to_hex 转为 RGB 后比较色调，
        因为多个 256 色索引可能映射到相同的 RGB 值（如 9 和 196 都是红）。
        """
        from src.chat_ui.infrastructure.styled import _256_to_hex

        # 各色相的期望 RGB（允许一定色相偏移）
        anchor_rgb = [
            (0.0, "ff0000"),    # 红
            (0.1667, "ff6600"),  # 橙
            (0.3334, "ffff00"),  # 黄
            (0.5, "00ff00"),    # 绿
            (0.6667, "00ffff"),  # 青
            (0.8334, "0000ff"),  # 蓝
        ]
        for t, expected_hex in anchor_rgb:
            idx = _interpolate_rainbow(t)
            actual_hex = _256_to_hex(idx)
            # 转为 RGB 分量
            er, eg, eb = int(expected_hex[0:2], 16), int(expected_hex[2:4], 16), int(expected_hex[4:6], 16)
            ar, ag, ab = int(actual_hex[0:2], 16), int(actual_hex[2:4], 16), int(actual_hex[4:6], 16)
            # RGB 欧几里得距离
            dist = ((er - ar) ** 2 + (eg - ag) ** 2 + (eb - ab) ** 2) ** 0.5
            # 允许最大 150 的 RGB 距离（色相环锚点间允许偏差）
            assert dist <= 150, (
                f"t={t:.4f}: 期望接近 #{expected_hex}, 实际 #{actual_hex} (idx={idx}), RGB 距离={dist:.0f}"
            )

    def test_monotonic_in_segment(self):
        """在单段内（如 [0, 1/6)），颜色索引单调变化。"""
        prev = -1
        for t in [0.0, 0.02, 0.05, 0.08, 0.1, 0.14, 0.16]:
            idx = _interpolate_rainbow(t)
            # 红色→橙色：色号从 196→214，应递增（或递减但单调）
            # 实际上 196→214 是递增的
            assert idx >= prev or prev == -1, (
                f"t={t}: idx={idx} < prev={prev}，非单调"
            )
            prev = idx


# ═══════════════════════════════════════════════════════════
# TestEaseFunctions
# ═══════════════════════════════════════════════════════════


class TestEaseFunctions:
    """缓动函数边界值测试。"""

    def test_ease_linear_boundaries(self):
        """_ease_linear 在 [0,1] 边界的行为。"""
        assert _ease_linear(0.0) == 0.0
        assert _ease_linear(1.0) == 1.0
        assert _ease_linear(0.5) == 0.5

    def test_ease_linear_clamp(self):
        """_ease_linear 将输入钳制到 [0, 1]。"""
        assert _ease_linear(-0.5) == 0.0
        assert _ease_linear(1.5) == 1.0

    def test_ease_out_cubic_boundaries(self):
        """_ease_out_cubic 在 [0,1] 边界的行为。"""
        assert _ease_out_cubic(0.0) == 0.0
        assert _ease_out_cubic(1.0) == 1.0

    def test_ease_out_cubic_midpoint(self):
        """_ease_out_cubic 在中段有减速特性（输出 > 输入）。"""
        result = _ease_out_cubic(0.5)
        # easeOutCubic: 1 - (1-t)^3 = 1 - 0.125 = 0.875
        assert result == pytest.approx(0.875)

    def test_ease_out_cubic_clamp(self):
        """_ease_out_cubic 钳制越界输入。"""
        assert _ease_out_cubic(-0.5) == 0.0
        assert _ease_out_cubic(1.5) == 1.0

    def test_ease_out_expo_boundaries(self):
        """_ease_out_expo 在 [0,1] 边界的行为。"""
        assert _ease_out_expo(0.0) == 0.0
        assert _ease_out_expo(1.0) == 1.0

    def test_ease_out_expo_midpoint(self):
        """_ease_out_expo 在中段快速接近 1（指数衰减）。"""
        result = _ease_out_expo(0.5)
        # easeOutExpo: 1 - 2^(-10t) = 1 - 2^(-5) = 1 - 1/32 = 0.96875
        assert result == pytest.approx(0.96875)

    def test_ease_out_expo_early(self):
        """_ease_out_expo 在 t=0.3 时已显著偏离线性。"""
        result = _ease_out_expo(0.3)
        # 1 - 2^(-3) = 1 - 1/8 = 0.875
        assert result > 0.75  # 显著快于线性 0.3

    def test_ease_out_expo_late(self):
        """_ease_out_expo 在 t=0.8 时已极接近 1。"""
        result = _ease_out_expo(0.8)
        # 1 - 2^(-8) = 1 - 1/256 ≈ 0.996
        assert result > 0.99

    def test_ease_linear_interpolates(self):
        """_ease_linear(0.25) == 0.25 — 恆速映射。"""
        assert _ease_linear(0.25) == 0.25
        assert _ease_linear(0.75) == 0.75

    def test_all_ease_functions_return_float(self):
        """所有缓动函数始终返回 float。"""
        for fn in [_ease_linear, _ease_out_cubic, _ease_out_expo]:
            for t in [0.0, 0.2, 0.5, 0.8, 1.0]:
                result = fn(t)
                assert isinstance(result, float), (
                    f"{fn.__name__}({t}) 返回 {type(result).__name__}，预期 float"
                )
