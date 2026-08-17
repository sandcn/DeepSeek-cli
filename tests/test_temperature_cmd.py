"""/temperature 命令与温度配置测试。

覆盖链路：
1. 配置默认值 / CONFIG_KEYS 元数据（defaults）
2. 配置校验（schema：合法值 / 非法类型 / 越界回退）
3. 端口读取（ConfigProxy / MockConfigAdapter）
4. 命令层（_cmd_temperature：设置 / 显示 / 非法值 / 越界值）
5. 持久化（update_config 写入 RC 文件 + 重启后仍生效）
6. 适配器请求构造（deepseek / openai_compat / anthropic / ollama 使用配置温度）
7. handle_command 集成（/temperature <数值> 走完整命令链路）
"""
from __future__ import annotations

import json

import pytest

from src.config.defaults import DEFAULTS, CONFIG_KEYS
from src.config.schema import _validate_rc
from src.core.adapters.config import MockConfigAdapter
from src.core.commands._config_cmd import _cmd_temperature
from src.core.commands import get_plugin_registry
from src.core.internal.commands._command_core import CommandContext, handle_command


def _make_ctx(arg: str, config_port=None):
    return CommandContext(
        messages=[], state={}, arg=arg,
        build_system_prompt=None, get_user_input=None, context_manager=None,
        session=None, persistence_port=None, config_port=config_port, ui_adapter=None,
    )


@pytest.fixture(autouse=True)
def _capture_output(monkeypatch):
    """重定向命令输出端口（_out 模块级引用），避免测试污染真实终端。"""
    from src.core.commands import _config_cmd as _cmd_module
    monkeypatch.setattr(_cmd_module, "_out", MockOutput())
    yield


class MockOutput:
    """命令输出捕获端口（与 DefaultOutputAdapter 接口兼容）。"""
    def __init__(self):
        self.lines: list[str] = []

    def write(self, text: str = "", level: str = "info", source: str = ""):
        self.lines.append(text)


# ═══════════════════════════════════════════════════════════
# 1. 配置默认值 / CONFIG_KEYS 元数据
# ═══════════════════════════════════════════════════════════

class TestConfigDefaults:
    def test_defaults_contains_temperature(self):
        assert "temperature" in DEFAULTS
        assert DEFAULTS["temperature"] == 0.2

    def test_config_keys_has_temperature_entry(self):
        entry = CONFIG_KEYS["TEMPERATURE"]
        assert entry["rc_path"] == ("temperature",)
        assert entry["type"] is float
        assert entry["default"] == 0.2
        assert entry["cacheable"] is True


# ═══════════════════════════════════════════════════════════
# 2. 配置校验（schema）
# ═══════════════════════════════════════════════════════════

class TestValidateRc:
    def test_valid_temperature_kept(self):
        rc = _validate_rc({"temperature": 0.7})
        assert rc["temperature"] == 0.7

    def test_int_temperature_converted_to_float(self):
        rc = _validate_rc({"temperature": 1})
        assert rc["temperature"] == 1.0

    def test_string_temperature_converted(self):
        rc = _validate_rc({"temperature": "0.5"})
        assert rc["temperature"] == 0.5

    def test_out_of_range_high_falls_back(self):
        rc = _validate_rc({"temperature": 2.5})
        assert rc["temperature"] == DEFAULTS["temperature"]

    def test_out_of_range_low_falls_back(self):
        rc = _validate_rc({"temperature": -0.5})
        assert rc["temperature"] == DEFAULTS["temperature"]

    def test_invalid_type_falls_back(self):
        rc = _validate_rc({"temperature": "abc"})
        assert rc["temperature"] == DEFAULTS["temperature"]

    def test_bool_falls_back(self):
        rc = _validate_rc({"temperature": True})
        assert rc["temperature"] == DEFAULTS["temperature"]


# ═══════════════════════════════════════════════════════════
# 3. 端口读取
# ═══════════════════════════════════════════════════════════

class TestConfigPort:
    def test_mock_adapter_get_temperature_default(self):
        cp = MockConfigAdapter()
        assert cp.get_temperature() == 0.2

    def test_mock_adapter_get_temperature_from_data(self):
        cp = MockConfigAdapter({"temperature": 0.9})
        assert cp.get_temperature() == 0.9

    def test_mock_adapter_set_temperature(self):
        cp = MockConfigAdapter()
        cp.set("temperature", 0.6)
        assert cp.get_temperature() == 0.6
        assert cp.last_set_key == "temperature"
        assert cp.last_set_value == 0.6


# ═══════════════════════════════════════════════════════════
# 4. 命令层（_cmd_temperature）
# ═══════════════════════════════════════════════════════════

class TestCmdTemperature:
    def test_no_arg_returns_true_without_set(self):
        cp = MockConfigAdapter({"temperature": 0.7})
        assert _cmd_temperature(_make_ctx("", cp)) is True
        assert cp.set_count == 0

    def test_set_valid_value(self):
        cp = MockConfigAdapter()
        assert _cmd_temperature(_make_ctx("0.5", cp)) is True
        assert cp.get("temperature") == 0.5
        assert cp.set_count == 1
        assert cp.last_set_key == "temperature"

    def test_set_int_value(self):
        cp = MockConfigAdapter()
        assert _cmd_temperature(_make_ctx("1", cp)) is True
        assert cp.get("temperature") == 1.0

    def test_set_rounds_to_two_decimals(self):
        cp = MockConfigAdapter()
        assert _cmd_temperature(_make_ctx("0.777", cp)) is True
        assert cp.get("temperature") == 0.78

    def test_invalid_value_not_set(self):
        cp = MockConfigAdapter()
        assert _cmd_temperature(_make_ctx("abc", cp)) is True
        assert cp.set_count == 0

    def test_out_of_range_not_set(self):
        for bad in ("2.5", "-0.1", "100", "-100"):
            cp = MockConfigAdapter()
            assert _cmd_temperature(_make_ctx(bad, cp)) is True
            assert cp.set_count == 0, f"温度 {bad} 不应被接受"

    def test_no_config_port_fallback_display(self):
        """无 ConfigPort 时回退读 config 模块，仅显示不写入。"""
        assert _cmd_temperature(_make_ctx("", None)) is True
        assert _cmd_temperature(_make_ctx("0.4", None)) is True

    def test_registry_contains_temperature(self):
        assert get_plugin_registry().exists("temperature")


# ═══════════════════════════════════════════════════════════
# 5. 持久化（写入 RC 文件 + 重启后仍生效）
# ═══════════════════════════════════════════════════════════

class TestPersistence:
    def test_update_config_persists_temperature(self, tmp_path, monkeypatch):
        import src.config.loader as loader

        monkeypatch.setattr(loader, "RC_FILE", tmp_path / "chatrc.json")
        monkeypatch.setattr(loader, "CONFIG_DIR", tmp_path)
        monkeypatch.setattr(loader, "_RC_LOADED", False)
        monkeypatch.setattr(loader, "_RC", None)
        from src.config import _clear_value_cache
        _clear_value_cache()
        try:
            loader.update_config("temperature", 0.8)
            # 立即读取
            assert loader.get_rc().get("temperature") == 0.8
            # 模拟重启：重置加载状态后重新加载
            monkeypatch.setattr(loader, "_RC_LOADED", False)
            monkeypatch.setattr(loader, "_RC", None)
            _clear_value_cache()
            assert loader.get_rc().get("temperature") == 0.8
            # RC 文件内容校验
            saved = json.loads((tmp_path / "chatrc.json").read_text(encoding="utf-8"))
            assert saved.get("temperature") == 0.8
        finally:
            _clear_value_cache()


# ═══════════════════════════════════════════════════════════
# 6. 适配器请求构造
# ═══════════════════════════════════════════════════════════

class TestAdapters:
    def test_deepseek_adapter_uses_configured_temperature(self, monkeypatch):
        from src.api.adapters.deepseek import DeepSeekAdapter
        monkeypatch.setattr("src.config.TEMPERATURE", 0.9)
        adapter = DeepSeekAdapter()
        kwargs = adapter.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}], model="deepseek-v4-pro",
        )
        assert kwargs["temperature"] == 0.9

    def test_openai_compat_adapter_uses_configured_temperature(self, monkeypatch):
        from src.api.adapters.openai_compat import OpenAICompatAdapter
        monkeypatch.setattr("src.config.TEMPERATURE", 0.4)
        adapter = OpenAICompatAdapter()
        kwargs = adapter.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}], model="glm-5.2",
        )
        assert kwargs["temperature"] == 0.4

    def test_anthropic_adapter_clamps_temperature(self, monkeypatch):
        from src.api.adapters.anthropic import AnthropicAdapter
        monkeypatch.setattr("src.config.TEMPERATURE", 2.0)
        adapter = AnthropicAdapter()
        kwargs = adapter.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}], model="claude-sonnet-4-6",
        )
        assert kwargs["temperature"] == 1.0

    def test_ollama_adapter_uses_configured_temperature(self, monkeypatch):
        from src.api.adapters.ollama import OllamaAdapter
        monkeypatch.setattr("src.config.TEMPERATURE", 0.3)
        adapter = OllamaAdapter()
        kwargs = adapter.build_request_kwargs(
            messages=[{"role": "user", "content": "hi"}], model="ollama-llama3",
        )
        assert kwargs["temperature"] == 0.3

    def test_get_temperature_falls_back_on_bad_value(self, monkeypatch):
        from src.api.adapters.deepseek import _get_temperature
        monkeypatch.setattr("src.config.TEMPERATURE", None)
        assert _get_temperature() == 0.2


# ═══════════════════════════════════════════════════════════
# 7. handle_command 集成
# ═══════════════════════════════════════════════════════════

class TestHandleCommand:
    def test_handle_temperature_command_sets_value(self):
        cp = MockConfigAdapter()
        handled = handle_command(
            "/temperature 0.6", [], {}, None, None,
            config_port=cp,
        )
        assert handled is True
        assert cp.get("temperature") == 0.6

    def test_handle_temperature_invalid_arg_not_set(self):
        cp = MockConfigAdapter()
        handled = handle_command(
            "/temperature 9.9", [], {}, None, None,
            config_port=cp,
        )
        assert handled is True
        assert cp.set_count == 0

    def test_unknown_command_not_handled(self):
        assert handle_command("/nonexistent-xyz", [], {}, None, None) is False
