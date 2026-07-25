"""测试 src.tui.config — TUI 统一配置（废弃代码清理验证）。"""

from __future__ import annotations

import pytest


class TestDeprecatedFieldsRemoved:
    """验证废弃字段/变量/别名已被清理（方向1 — 步骤9）。"""

    # ── max_output_len 从 TuiConfig 移除 ─────────────────────

    def test_tui_config_no_max_output_len(self):
        """TuiConfig.defaults() 不再包含已废弃的 max_output_len 字段。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults()
        assert not hasattr(cfg, "max_output_len"), (
            "废弃字段 max_output_len 应从 TuiConfig 中移除"
        )

    def test_chat_config_still_has_max_output_len(self):
        """ChatConfig.max_output_len 仍保留（为活跃字段）。"""
        from src.tui.consumer.chat_config import ChatConfig
        assert hasattr(ChatConfig, "max_output_len"), (
            "ChatConfig.max_output_len 应为活跃字段"
        )

    # ── _MAIN_LABEL 从 consumer 模块移除 ─────────────────────

    def test_main_label_not_in_consumer_init(self):
        """_MAIN_LABEL 不应再从 src.tui.consumer 可导入。"""
        with pytest.raises(ImportError, match="cannot import name '_MAIN_LABEL'"):
            from src.tui.consumer import _MAIN_LABEL  # type: ignore[import-unused]

    # ── output_lock 别名从 lock 模块移除 ─────────────────────

    def test_output_lock_not_in_engine_lock(self):
        """output_lock 不应再从 src.tui.engine.lock 可导入。"""
        with pytest.raises(ImportError, match="cannot import name 'output_lock'"):
            from src.tui.widgets.lock import output_lock  # type: ignore[import-unused]

    def test_output_lock_not_in_widgets_lock(self):
        """output_lock 不应再从 src.tui.widgets.lock 可导入。"""
        with pytest.raises(ImportError, match="cannot import name 'output_lock'"):
            from src.tui.widgets.lock import output_lock  # type: ignore[import-unused]

    def test_output_lock_not_in_widgets_init(self):
        """output_lock 不应再从 src.tui.widgets 可导入。"""
        with pytest.raises(ImportError, match="cannot import name 'output_lock'"):
            from src.tui.widgets import output_lock  # type: ignore[import-unused]

    # ── render_lock 和 _try_acquire_output_lock 仍可用 ──────

    def test_render_lock_still_accessible(self):
        """render_lock 仍可通过各模块正常导入。"""
        from src.tui.widgets.lock import render_lock as rl1
        from src.tui.widgets.lock import render_lock as rl2
        from src.tui.widgets import render_lock as rl3
        assert rl1 is rl2 is rl3

    def test_try_acquire_output_lock_still_accessible(self):
        """_try_acquire_output_lock 仍可通过各模块正常导入。"""
        from src.tui.widgets.lock import _try_acquire_output_lock as t1
        from src.tui.widgets.lock import _try_acquire_output_lock as t2
        from src.tui.widgets import _try_acquire_output_lock as t3
        assert t1 is t2 is t3


class TestNewConfigFields:
    """验证新增配置字段（方向8 — 配置硬编码清理）。"""

    # ── eventbus_throttle ─────────────────────────────────

    def test_eventbus_throttle_default(self):
        """TuiConfig.defaults().eventbus_throttle 默认值为 0.3。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults()
        assert hasattr(cfg, "eventbus_throttle"), "缺少 eventbus_throttle 字段"
        assert cfg.eventbus_throttle == 0.3

    def test_eventbus_throttle_override(self):
        """TuiConfig.with_overrides 可覆盖 eventbus_throttle。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults().with_overrides(eventbus_throttle=0.5)
        assert cfg.eventbus_throttle == 0.5

    def test_eventbus_throttle_frozen(self):
        """eventbus_throttle 不可变（frozen dataclass）。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults()
        with pytest.raises(Exception):
            cfg.eventbus_throttle = 0.5  # type: ignore[misc]

    # ── default_history ───────────────────────────────────

    def test_default_history_default(self):
        """TuiConfig.defaults().default_history 默认值为 3。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults()
        assert hasattr(cfg, "default_history"), "缺少 default_history 字段"
        assert cfg.default_history == 3

    def test_default_history_override(self):
        """TuiConfig.with_overrides 可覆盖 default_history。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults().with_overrides(default_history=5)
        assert cfg.default_history == 5

    def test_default_history_frozen(self):
        """default_history 不可变（frozen dataclass）。"""
        from src.tui.config import TuiConfig
        cfg = TuiConfig.defaults()
        with pytest.raises(Exception):
            cfg.default_history = 5  # type: ignore[misc]

    # ── parallel_display 读取配置 ──────────────────────────

    def test_parallel_display_reads_eventbus_throttle_from_config(self):
        """ParallelDisplay 实例从 TuiConfig 读取 eventbus_throttle。"""
        from src.tui.parallel_display import ParallelDisplay
        from src.tui.config import TuiConfig
        display = ParallelDisplay(max_history=1)
        expected = TuiConfig.defaults().eventbus_throttle
        assert display._eventbus_throttle == expected, (
            f"ParallelDisplay 应使用 TuiConfig 的 eventbus_throttle 值 {expected}，"
            f"而不是模块级常量"
        )

    def test_parallel_display_retains_module_constants_as_fallback(self):
        """_EVENTBUS_THROTTLE 和 _DEFAULT_HISTORY 模块级常量保留作为 fallback。"""
        from src.tui import parallel_display as pd
        assert hasattr(pd, "_EVENTBUS_THROTTLE"), "_EVENTBUS_THROTTLE 应保留为 fallback"
        assert hasattr(pd, "_DEFAULT_HISTORY"), "_DEFAULT_HISTORY 应保留为 fallback"
        assert pd._EVENTBUS_THROTTLE == 0.3
        assert pd._DEFAULT_HISTORY == 3
