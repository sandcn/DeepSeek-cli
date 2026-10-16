"""配置数值范围校验 + 重置输出键名一致性（CLI /config 写入路径回归）。

★ 修复回归（2026-10）：
  1. 配置层此前不校验数值区间：``config set temperature 99``、
     ``config set max_retries -5`` 均写入成功（与文档/运行语义不符，直到调用
     API 才报错）。现于 ``config.loader.update_config``（所有写入路径的唯一
     入口）按 ``config.defaults.CONFIG_BOUNDS`` 拒绝越界值（``ValueError``）。
  2. ``config reset <键>`` 输出大写写回键（``TEMPERATURE``），与 ``config
     get/set`` 的显示路径（``temperature``）不一致；现统一用显示路径。
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from src.config import defaults as cfg_defaults
from src.config import loader as cfg_loader
from src.config.loader import check_config_bounds, get_rc, update_config


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


# ── 边界校验（update_config 唯一写入口） ────────────────

def test_update_config_rejects_temperature_above_max(isolated_rc):
    with pytest.raises(ValueError):
        update_config("TEMPERATURE", 99.0)
    # 未落盘（文件不存在或没有该键）
    if isolated_rc.exists():
        assert json.loads(isolated_rc.read_text(encoding="utf-8")).get("temperature") != 99.0


def test_update_config_rejects_temperature_below_min(isolated_rc):
    with pytest.raises(ValueError):
        update_config("TEMPERATURE", -0.5)


def test_update_config_rejects_negative_max_retries(isolated_rc):
    with pytest.raises(ValueError):
        update_config("MAX_RETRIES", -5)


def test_update_config_accepts_boundary_values(isolated_rc):
    update_config("TEMPERATURE", 0.0)
    assert get_rc()["temperature"] == 0.0
    update_config("TEMPERATURE", 2.0)
    assert get_rc()["temperature"] == 2.0
    update_config("MAX_RETRIES", 0)
    assert get_rc()["max_retries"] == 0
    update_config("IMAGE_UPLOAD_QUALITY", 100)
    assert get_rc()["image_upload_quality"] == 100


def test_update_config_rejects_image_quality_out_of_range(isolated_rc):
    with pytest.raises(ValueError):
        update_config("IMAGE_UPLOAD_QUALITY", 0)
    with pytest.raises(ValueError):
        update_config("IMAGE_UPLOAD_QUALITY", 101)


def test_bounds_apply_to_rc_leaf_key_name(isolated_rc):
    """小写 rc_path 末段键名（``temperature``）同样受校验（ConfigPort 路径）。"""
    with pytest.raises(ValueError):
        update_config("temperature", 5.0)
    update_config("temperature", 1.5)
    assert get_rc()["temperature"] == 1.5


def test_unbounded_keys_unaffected(isolated_rc):
    update_config("theme", "dark")
    assert get_rc()["theme"] == "dark"
    update_config("MODEL_PROFILES", [{"model": "m"}])
    assert get_rc()["model_profiles"] == [{"model": "m"}]
    update_config("keybindings_overrides", {"ctrl+x": "clear_screen"})
    assert get_rc()["keybindings_overrides"] == {"ctrl+x": "clear_screen"}


def test_model_context_tokens_min_is_one(isolated_rc):
    update_config("MODEL_CONTEXT_TOKENS", 1)
    assert get_rc()["model_context_tokens"] == 1
    with pytest.raises(ValueError):
        update_config("MODEL_CONTEXT_TOKENS", 0)


def test_check_config_bounds_bool_and_non_numeric_skipped(isolated_rc):
    """bool / 非数值值不做区间比较（True/False 不是数值输入）。"""
    check_config_bounds("TEMPERATURE", True)
    check_config_bounds("TEMPERATURE", "abc")
    check_config_bounds("UNKNOWN_KEY", 999)


def test_check_config_bounds_message_mentions_key_and_range():
    with pytest.raises(ValueError) as exc:
        check_config_bounds("TEMPERATURE", 99.0)
    assert "TEMPERATURE" in str(exc.value)
    assert "2.0" in str(exc.value)


# ── CLI 写入路径（config set / reset） ──────────────────

def _run_config_cmd(monkeypatch, argv_tail: list) -> list:
    """执行 ``chat.py config <...>`` 处理器并收集 publish_output 文本。"""
    from src.app_init import _config_cmd as cli

    lines: list = []
    monkeypatch.setattr(cli, "publish_output", lambda text, **kw: lines.append(str(text)))
    parser = argparse.ArgumentParser()
    parser.add_argument("config_cmd", nargs="?")
    parser.add_argument("key", nargs="?")
    parser.add_argument("value", nargs="?")
    args = parser.parse_args(argv_tail)
    cli._handle_config_command(args)
    return lines


def test_cli_config_set_rejects_out_of_range(isolated_rc, monkeypatch):
    lines = _run_config_cmd(monkeypatch, ["set", "temperature", "99"])
    joined = "\n".join(lines)
    assert "写入配置失败" in joined
    assert get_rc()["temperature"] != 99


def test_cli_config_set_accepts_valid_value(isolated_rc, monkeypatch):
    lines = _run_config_cmd(monkeypatch, ["set", "temperature", "0.7"])
    assert "已设置 temperature = 0.7" in "\n".join(lines)
    assert get_rc()["temperature"] == 0.7


def test_cli_config_reset_prints_display_path(isolated_rc, monkeypatch):
    """重置提示与 get/set 一致使用显示路径（小写 rc 键名）。"""
    update_config("TEMPERATURE", 1.2)
    lines = _run_config_cmd(monkeypatch, ["reset", "temperature"])
    joined = "\n".join(lines)
    assert "已重置 temperature =" in joined
    assert "TEMPERATURE" not in joined
    assert get_rc()["temperature"] == cfg_defaults.CONFIG_KEYS["TEMPERATURE"]["default"]
