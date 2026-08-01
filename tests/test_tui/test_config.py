"""测试 _config.py — TuiConfig 配置类。

验证所有字段默认值和不可变性。
"""
import pytest

from src.tui._config import ConfigBase, TuiConfig


class TestConfigBase:
    """测试 ConfigBase 基类。"""

    def test_defaults_returns_instance(self):
        cfg = TuiConfig.defaults()
        assert isinstance(cfg, TuiConfig)

    def test_with_overrides_returns_new_instance(self):
        cfg = TuiConfig.defaults()
        new_cfg = cfg.with_overrides(render_interval=0.5)
        assert new_cfg is not cfg
        assert new_cfg.render_interval == 0.5
        assert cfg.render_interval == 0.1  # 原实例不变

    def test_with_overrides_multiple(self):
        cfg = TuiConfig.defaults()
        new_cfg = cfg.with_overrides(
            render_interval=0.05,
            max_batch_size=100,
        )
        assert new_cfg.render_interval == 0.05
        assert new_cfg.max_batch_size == 100
        assert new_cfg.cmd_queue_maxsize == 10000  # 未覆盖的保持默认


class TestTuiConfigDefaults:
    """测试 TuiConfig 所有字段默认值。"""

    def setup_method(self):
        self.cfg = TuiConfig.defaults()

    def test_render_engine_params(self):
        assert self.cfg.render_interval == 0.1
        assert self.cfg.max_batch_size == 50
        assert self.cfg.drain_lock_timeout == 0.1
        assert self.cfg.cmd_queue_maxsize == 10000
        assert self.cfg.consecutive_full_threshold == 10
        assert self.cfg.bottom_redraw_interval == 0.1

    def test_animation_params(self):
        assert self.cfg.breath_cycle_len == 12
        assert self.cfg.pulse_cycle_len == 4

    def test_truncation_params(self):
        assert self.cfg.max_error_length == 200

    def test_fade_params(self):
        assert self.cfg.fade_total_frames == 6
        assert self.cfg.fade_start_color == 238

    def test_eventbus_params(self):
        assert self.cfg.eventbus_throttle == 0.3
        assert self.cfg.default_history == 3

    def test_mock_params(self):
        assert self.cfg.mock_terminal_width == 120
        assert self.cfg.mock_terminal_height == 40

    def test_recover_params(self):
        assert self.cfg.max_recover_attempts == 3
        assert self.cfg.recover_delay == 0.5


class TestTuiConfigFrozen:
    """测试 TuiConfig 不可变性。"""

    def test_cannot_set_attribute(self):
        cfg = TuiConfig.defaults()
        try:
            cfg.render_interval = 0.5
            assert False, "Should have raised FrozenInstanceError"
        except Exception:
            pass  # dataclass frozen=True 禁止修改

    def test_with_overrides_does_not_mutate_original(self):
        cfg = TuiConfig.defaults()
        orig_interval = cfg.render_interval
        cfg.with_overrides(render_interval=0.99)
        assert cfg.render_interval == orig_interval


class TestFadeConfig:
    """步骤7 — 动效时间基配置默认值与兼容性。"""

    def test_fade_config_defaults_regression(self):
        """新字段默认值：fade_duration_sec==0.6、spinner_tick_hz==10.0。"""
        cfg = TuiConfig.defaults()
        assert cfg.fade_duration_sec == 0.6
        assert cfg.spinner_tick_hz == 10.0
        assert cfg.fade_start_color == 238
        assert cfg.fade_total_frames == 6
        # 时间基默认值 = 帧数 × 渲染间隔（0.6 = 6 × 0.1），语义自洽（浮点容差）
        assert cfg.fade_duration_sec == pytest.approx(
            cfg.fade_total_frames * cfg.render_interval)

    def test_old_config_compat_regression(self):
        """旧字段（不含新字段的构造方式）with_overrides 仍可用不抛异常。"""
        cfg = TuiConfig.defaults()
        old_style = cfg.with_overrides(
            fade_total_frames=10,
            fade_start_color=240,
            breath_cycle_len=8,
        )
        assert old_style.fade_total_frames == 10
        assert old_style.fade_start_color == 240
        assert old_style.breath_cycle_len == 8
        # 未覆盖的新字段保持默认
        assert old_style.fade_duration_sec == 0.6
        assert old_style.spinner_tick_hz == 10.0

    def test_new_field_overrides_regression(self):
        """新字段可经 with_overrides 覆盖（frozen dataclass 天然支持）。"""
        cfg = TuiConfig.defaults()
        new_style = cfg.with_overrides(fade_duration_sec=1.2, spinner_tick_hz=8.0)
        assert new_style.fade_duration_sec == 1.2
        assert new_style.spinner_tick_hz == 8.0
        # 原实例不变
        assert cfg.fade_duration_sec == 0.6


class TestReverseSearchConfig:
    """方向D 步骤14 — reverse_search_enabled 配置字段。"""

    def test_reverse_search_enabled_default_false(self):
        """默认 False（保持既有 Ctrl+R switch_model 语义，键位冲突门控）。"""
        cfg = TuiConfig.defaults()
        assert cfg.reverse_search_enabled is False

    def test_reverse_search_enabled_override(self):
        """with_overrides 可覆盖 reverse_search_enabled。"""
        cfg = TuiConfig.defaults()
        new_cfg = cfg.with_overrides(reverse_search_enabled=True)
        assert new_cfg.reverse_search_enabled is True
        # 原实例不变
        assert cfg.reverse_search_enabled is False


class TestEscCancelConfig:
    """方向D 步骤16 — esc_cancel_input 配置字段。"""

    def test_esc_cancel_input_default_false(self):
        """默认 False（保持既有 Esc 中断语义，键位语义门控）。"""
        cfg = TuiConfig.defaults()
        assert cfg.esc_cancel_input is False

    def test_esc_cancel_input_override(self):
        """with_overrides 可覆盖 esc_cancel_input。"""
        cfg = TuiConfig.defaults()
        new_cfg = cfg.with_overrides(esc_cancel_input=True)
        assert new_cfg.esc_cancel_input is True
        # 原实例不变
        assert cfg.esc_cancel_input is False


class TestStep17ConfigSummary:
    """横切步骤17 — 新增配置项汇总核对（方向D 步骤14/16 字段）。"""

    def test_new_fields_exist_with_correct_defaults(self):
        """新增字段存在且默认值正确（汇总核对清单）。"""
        cfg = TuiConfig.defaults()
        # 步骤14：Ctrl+R 反向历史搜索（默认 False 保持 switch_model）
        assert cfg.reverse_search_enabled is False
        # 步骤16：Esc 取消输入（默认 False 保持中断语义）
        assert cfg.esc_cancel_input is False

    def test_defaults_constructible_with_new_fields(self):
        """TuiConfig.defaults() 可构造（frozen dataclass 无类型错误）。"""
        cfg = TuiConfig.defaults()
        assert isinstance(cfg, TuiConfig)
        # 全部新字段可读且类型正确
        assert isinstance(cfg.reverse_search_enabled, bool)
        assert isinstance(cfg.esc_cancel_input, bool)

    def test_with_overrides_covers_all_new_fields(self):
        """with_overrides 一次性覆盖新增字段有效（原实例不变）。"""
        cfg = TuiConfig.defaults()
        new_cfg = cfg.with_overrides(
            reverse_search_enabled=True,
            esc_cancel_input=True,
        )
        assert new_cfg.reverse_search_enabled is True
        assert new_cfg.esc_cancel_input is True
        # 原实例不变
        assert cfg.reverse_search_enabled is False
        assert cfg.esc_cancel_input is False

