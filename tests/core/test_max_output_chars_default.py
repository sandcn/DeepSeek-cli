"""max_output_chars 默认值（3000 → 1M 字符）单元测试。

覆盖：
  - DEFAULTS["max_output_chars"] 默认 1M 字符（1_000_000）；
  - CONFIG_KEYS["MAX_OUTPUT_CHARS"] 元数据（rc_path / type / default 引用
    DEFAULTS 单一事实源 / cacheable）；
  - 配置读取链路：RC 缺省（首次运行无配置文件）时 get_rc / 模块属性
    MAX_OUTPUT_CHARS / ConfigProxy 属性均为新默认 1M；
  - RC 显式配置优先于默认值（用户覆盖不被默认值抹掉）；
  - schema 类型校验：字符串数字转 int、bool 等非法值回退新默认；
  - /config 界面把 MAX_OUTPUT_CHARS 纳入可编辑条目与说明表。
"""

from __future__ import annotations

import pytest

from src.config.defaults import CONFIG_KEYS, DEFAULTS

_ONE_MILLION = 1_000_000
_LEGACY_DEFAULT = 3000


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path):
    """隔离 RC 配置文件（临时目录）并重置缓存，避免污染真实用户配置。"""
    from src.config import loader as cfg_loader
    from src.config import defaults as cfg_defaults
    rc_file = tmp_path / "chatrc.json"
    monkeypatch.setattr(cfg_loader, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_loader, "LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(cfg_loader, "_RC_LOADED", False)
    monkeypatch.setattr(cfg_loader, "_RC", None)
    monkeypatch.setattr(cfg_defaults, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_defaults, "CONFIG_DIR", tmp_path)

    import src.config as cfg
    cfg._clear_value_cache()
    yield rc_file
    cfg._clear_value_cache()


class TestDefaultsAndMetadata:
    def test_default_is_one_million_chars(self):
        assert DEFAULTS["max_output_chars"] == _ONE_MILLION

    def test_legacy_default_replaced(self):
        assert DEFAULTS["max_output_chars"] != _LEGACY_DEFAULT

    def test_config_key_metadata(self):
        entry = CONFIG_KEYS["MAX_OUTPUT_CHARS"]
        assert entry["rc_path"] == ("max_output_chars",)
        assert entry["type"] is int
        assert entry["cacheable"] is True
        # 单一事实源：CONFIG_KEYS 的 default 与 DEFAULTS 同源（防双源漂移）
        assert entry["default"] == DEFAULTS["max_output_chars"] == _ONE_MILLION

    def test_entry_listed_in_config_ui(self):
        from src.presentation_data import CONFIG_ENTRY_DESC_MAP

        assert "MAX_OUTPUT_CHARS" in CONFIG_ENTRY_DESC_MAP


class TestConfigReadPaths:
    def test_rc_default_matches_new_default(self, isolated_rc):
        from src.config.loader import get_rc

        rc = get_rc()
        assert rc["max_output_chars"] == _ONE_MILLION

    def test_module_attribute_follows_default(self, isolated_rc):
        import src.config as cfg

        assert cfg.MAX_OUTPUT_CHARS == _ONE_MILLION

    def test_proxy_attribute_follows_default(self, isolated_rc):
        from src.config.proxy import config

        assert config.MAX_OUTPUT_CHARS == _ONE_MILLION

    def test_rc_explicit_value_wins(self, isolated_rc):
        import json

        from src.config import loader as cfg_loader
        import src.config as cfg

        isolated_rc.write_text(
            json.dumps({"max_output_chars": 123456}), encoding="utf-8")
        cfg_loader._RC_LOADED = False
        cfg_loader._RC = None
        cfg._clear_value_cache()
        assert cfg.MAX_OUTPUT_CHARS == 123456


class TestSchemaValidation:
    def test_numeric_string_coerced(self):
        from src.config.schema import _validate_rc

        rc = _validate_rc({"max_output_chars": "1000000"})
        assert rc["max_output_chars"] == _ONE_MILLION

    def test_bool_falls_back_to_default(self):
        from src.config.schema import _validate_rc

        rc = _validate_rc({"max_output_chars": True})
        assert rc["max_output_chars"] == DEFAULTS["max_output_chars"] == _ONE_MILLION

    def test_invalid_value_falls_back_to_default(self):
        from src.config.schema import _validate_rc

        rc = _validate_rc({"max_output_chars": "abc"})
        assert rc["max_output_chars"] == _ONE_MILLION
