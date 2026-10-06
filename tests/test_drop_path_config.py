"""拖放文件路径规范化的配置与装配接线测试（2026-10-07 用户需求）。

覆盖：RC 键（DEFAULTS / CONFIG_KEYS / 配置中心描述）→ TuiConfig 字段 →
``create_shared`` 装配读取 → Input 外观注入开关的完整链路。
"""

from __future__ import annotations

from src.config.defaults import CONFIG_KEYS, DEFAULTS


def test_rc_default_enabled():
    assert DEFAULTS["tui_drop_path_normalize"] is True
    entry = CONFIG_KEYS["TUI_DROP_PATH_NORMALIZE"]
    assert entry["rc_path"] == ("tui_drop_path_normalize",)
    assert entry["type"] is bool
    assert entry["default"] is True


def test_rc_key_resolvable():
    from src.config import TUI_DROP_PATH_NORMALIZE
    assert isinstance(TUI_DROP_PATH_NORMALIZE, bool)


def test_config_entry_desc_present():
    from src.config.view_model import CONFIG_ENTRY_DESCS
    assert "TUI_DROP_PATH_NORMALIZE" in CONFIG_ENTRY_DESCS
    assert "拖" in CONFIG_ENTRY_DESCS["TUI_DROP_PATH_NORMALIZE"]


def test_config_entry_boolean_select():
    from src.config.view_model import build_config_entries
    entries = {e["key"]: e for e in build_config_entries()}
    entry = entries["TUI_DROP_PATH_NORMALIZE"]
    assert entry["type"] is bool
    assert entry["edit_kind"] == "select"
    assert [v for v, _desc in entry["options"]] == ["true", "false"]


def test_tui_config_field_default():
    from src.tui._config import TuiConfig
    cfg = TuiConfig.defaults()
    assert cfg.drop_path_normalize is True
    assert cfg.with_overrides(drop_path_normalize=False).drop_path_normalize is False


def test_create_shared_reads_rc(monkeypatch):
    """RC 关闭 → 装配出的 TuiConfig 关闭拖放路径规范化。"""
    monkeypatch.setattr("src.config.TUI_DROP_PATH_NORMALIZE", False)
    from src.tui._assembly_steps import create_shared
    cfg, _model = create_shared()
    assert cfg.drop_path_normalize is False


def test_create_shared_default_on(monkeypatch):
    monkeypatch.setattr("src.config.TUI_DROP_PATH_NORMALIZE", True)
    from src.tui._assembly_steps import create_shared
    cfg, _model = create_shared()
    assert cfg.drop_path_normalize is True
