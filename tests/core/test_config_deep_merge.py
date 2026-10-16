"""配置合并语义 + 写入路径完整性校验（P0 修复回归）。

★ 修复 1（P0）：``config.loader._safe_merge`` 原为**浅合并**——RC 只配置复合键
  的部分子键（如 ``{"skills": {"enabled": false}}``）时会整体替换 defaults 的
  ``skills``，defaults 的其余子键丢失（读取方靠 ``.get`` 兜底才不崩）。
  现改为**递归合并**：两侧均为 dict 时逐键递归，其余类型（list/标量）整体覆盖。

★ 修复 2（P0）：``update_config`` 中的 ``assert``（``rc_path`` 路径段完整性）
  改为显式 ``ValueError``——``assert`` 在 ``python -O`` 下被剥离，配置写入路径
  的完整性校验会静默消失。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.config import defaults as cfg_defaults
from src.config import loader as cfg_loader
from src.config.loader import _safe_merge, get_rc, update_config


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path: Path):
    """隔离 RC 配置文件（临时目录）——测试写入不污染真实用户配置。"""
    rc_file = tmp_path / "chatrc.json"
    monkeypatch.setattr(cfg_loader, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_loader, "LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(cfg_loader, "_RC_LOADED", False)
    monkeypatch.setattr(cfg_loader, "_RC", None)
    monkeypatch.setattr(cfg_defaults, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_defaults, "CONFIG_DIR", tmp_path)
    return rc_file


# ── _safe_merge 递归合并语义 ────────────────────────────

def test_merge_preserves_nested_default_subkeys():
    """只覆盖嵌套 dict 的一个子键时，其余 defaults 子键必须保留。"""
    defaults = {"skills": {"enabled": True, "auto_load": [], "catalog_description_max_length": 500}}
    merged = _safe_merge(defaults, {"skills": {"enabled": False}})
    assert merged["skills"]["enabled"] is False
    assert merged["skills"]["auto_load"] == []
    assert merged["skills"]["catalog_description_max_length"] == 500


def test_merge_recurses_multiple_levels():
    defaults = {"a": {"b": {"c": 1, "d": 2}}}
    merged = _safe_merge(defaults, {"a": {"b": {"c": 9}}})
    assert merged == {"a": {"b": {"c": 9, "d": 2}}}


def test_merge_replaces_lists_wholesale():
    """list 不做元素级合并（顺序/长度语义无法定义），整体覆盖。"""
    defaults = {"model_profiles": [{"model": "a"}], "mcp_servers": [{"name": "s"}]}
    merged = _safe_merge(defaults, {"model_profiles": [], "mcp_servers": [{"name": "t"}]})
    assert merged["model_profiles"] == []
    assert merged["mcp_servers"] == [{"name": "t"}]


def test_merge_type_mismatch_replaces():
    """两侧类型不一致（dict↔标量）时以 overrides 为准。"""
    assert _safe_merge({"x": {"a": 1}}, {"x": 5})["x"] == 5
    assert _safe_merge({"x": 5}, {"x": {"a": 1}})["x"] == {"a": 1}


def test_merge_keeps_override_only_top_level_keys():
    """overrides 中 defaults 不存在的顶层键（如 performance）原样保留。"""
    merged = _safe_merge({"a": 1}, {"performance": {"http_client": {"read_timeout": 5}}})
    assert merged["a"] == 1
    assert merged["performance"]["http_client"]["read_timeout"] == 5


def test_merge_does_not_mutate_inputs():
    defaults = {"skills": {"enabled": True}}
    overrides = {"skills": {"enabled": False}}
    _safe_merge(defaults, overrides)
    assert defaults == {"skills": {"enabled": True}}
    assert overrides == {"skills": {"enabled": False}}


def test_load_rc_deep_merges_partial_nested_config(isolated_rc):
    """端到端：磁盘上只写 skills.enabled，加载后其余 skills 子键取默认值。"""
    isolated_rc.write_text(json.dumps({"skills": {"enabled": False}}), encoding="utf-8")
    rc = get_rc()
    assert rc["skills"]["enabled"] is False
    for key, value in cfg_defaults.DEFAULTS["skills"].items():
        if key == "enabled":
            continue
        assert rc["skills"][key] == value


def test_load_rc_deep_merges_compaction_partial(isolated_rc):
    """compaction 是复合键：部分覆盖不得丢失其余子键。"""
    isolated_rc.write_text(json.dumps({"compaction": {"enabled": False}}), encoding="utf-8")
    rc = get_rc()
    assert rc["compaction"]["enabled"] is False
    assert rc["compaction"]["threshold_ratio"] == cfg_defaults.DEFAULTS["compaction"]["threshold_ratio"]


# ── rc_path 完整性校验（assert → ValueError） ───────────

def test_update_config_rejects_invalid_rc_path_segment(isolated_rc, monkeypatch):
    """rc_path 含空串路径段时抛 ValueError（显式校验，不依赖 assert）。"""
    monkeypatch.setitem(cfg_loader.CONFIG_KEYS, "BADRCKEY", {"rc_path": ("", "x")})
    with pytest.raises(ValueError) as exc:
        update_config("BADRCKEY", 1)
    assert "rc_path" in str(exc.value)


def test_update_config_rejects_non_string_rc_path_segment(isolated_rc, monkeypatch):
    monkeypatch.setitem(cfg_loader.CONFIG_KEYS, "BADRCKEY2", {"rc_path": (1, "x")})
    with pytest.raises(ValueError):
        update_config("BADRCKEY2", 1)


def test_update_config_accepts_valid_nested_rc_path(isolated_rc):
    """正常嵌套键仍可写入（校验不得误伤合法路径）。"""
    update_config("max_context_tokens", 12345)
    assert get_rc()["max_context_tokens"] == 12345
