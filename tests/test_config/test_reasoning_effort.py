"""测试推理等级配置（REASONING_EFFORT）。

覆盖：默认值、CONFIG_KEYS 元数据、schema 值域校验、update_config 写入、ConfigProxy 访问。
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.defaults import DEFAULTS, CONFIG_KEYS
from src.config.schema import _validate_rc


class TestReasoningEffortDefaults:
    """默认值与元数据。"""

    def test_default_in_defaults(self):
        assert DEFAULTS["reasoning_effort"] == "max"

    def test_config_keys_metadata(self):
        entry = CONFIG_KEYS["REASONING_EFFORT"]
        assert entry["rc_path"] == ("reasoning_effort",)
        assert entry["type"] is str
        assert entry["default"] == "max"
        assert entry["cacheable"] is True


class TestReasoningEffortSchema:
    """schema._validate_rc 值域校验。"""

    @pytest.mark.parametrize("level", ["low", "medium", "high", "max"])
    def test_valid_level_preserved(self, level):
        rc = {"reasoning_effort": level}
        result = _validate_rc(rc)
        assert result["reasoning_effort"] == level

    def test_uppercase_normalized_to_lower(self):
        rc = {"reasoning_effort": "HIGH"}
        result = _validate_rc(rc)
        assert result["reasoning_effort"] == "high"

    @pytest.mark.parametrize("bad", ["", "ultra", "maxx", "1", "none", 42, None])
    def test_invalid_level_falls_back_to_max(self, bad):
        rc = {"reasoning_effort": bad}
        result = _validate_rc(rc)
        assert result["reasoning_effort"] == "max"

    def test_missing_key_safe(self):
        """缺失键时 _validate_rc 不报错（安全）。"""
        result = _validate_rc({})
        assert result.get("reasoning_effort", "max") == "max"


class TestReasoningEffortUpdateConfig:
    """update_config 写入 RC。"""

    @pytest.fixture(autouse=True)
    def _protect_rc_file(self):
        """patch 阻止 update_config 写入真实 RC 文件。"""
        with patch("src.config.loader.RC_FILE") as mock_rc:
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = "{}"
            yield

    def _reset_rc(self):
        import src.config.loader as loader_mod
        loader_mod._RC = None
        loader_mod._RC_LOADED = False

    def test_upper_key_maps_to_lowercase_rc_field(self):
        """update_config('REASONING_EFFORT', ...) 写入 rc['reasoning_effort']。"""
        from src.config.loader import update_config, get_rc
        self._reset_rc()
        rc = get_rc()
        old = rc.get("reasoning_effort", "max")
        try:
            update_config("REASONING_EFFORT", "low")
            assert rc.get("reasoning_effort") == "low"
        finally:
            update_config("REASONING_EFFORT", old)

    def test_lower_key_backward_compat(self):
        """小写键（RC 字段名）直接写入（向后兼容）。"""
        from src.config.loader import update_config, get_rc
        self._reset_rc()
        rc = get_rc()
        old = rc.get("reasoning_effort", "max")
        try:
            update_config("reasoning_effort", "high")
            assert rc.get("reasoning_effort") == "high"
        finally:
            update_config("reasoning_effort", old)

    def test_config_value_cache_cleared_after_write(self):
        """写入后配置缓存清除，下次读取拿到新值。"""
        import src.config as _cfg
        from src.config.loader import update_config
        self._reset_rc()
        old = _cfg.REASONING_EFFORT
        try:
            update_config("REASONING_EFFORT", "medium")
            # 必须通过模块属性访问（缓存已清除，重新读取 RC）
            assert _cfg.REASONING_EFFORT == "medium"
        finally:
            update_config("REASONING_EFFORT", old)


class TestConfigProxyReasoningEffort:
    """ConfigProxy / DefaultConfigAdapter 访问。"""

    @patch("src.config.proxy._config.REASONING_EFFORT", "high")
    def test_get_reasoning_effort_via_proxy(self):
        from src.config.proxy import config
        assert config.get_reasoning_effort() == "high"
        assert config.REASONING_EFFORT == "high"

    def test_mock_config_adapter(self):
        from src.core.adapters.config import MockConfigAdapter
        adapter = MockConfigAdapter()
        assert adapter.get_reasoning_effort() == "max"
        adapter.set("reasoning_effort", "low")
        assert adapter.get_reasoning_effort() == "low"

    def test_default_config_adapter_inherits_proxy(self):
        from src.core.adapters.config import DefaultConfigAdapter
        adapter = DefaultConfigAdapter()
        # 方法存在且返回合法值（默认 max）
        assert adapter.get_reasoning_effort() in ("low", "medium", "high", "max")
