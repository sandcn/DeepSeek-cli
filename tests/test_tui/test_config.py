"""测试 _config.py — TuiConfig 配置类。

验证所有字段默认值和不可变性。
"""
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
        assert self.cfg.active_render_interval == 0.1
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
