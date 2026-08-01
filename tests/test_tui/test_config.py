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
