"""配置 schema 测试 — 覆盖 src/config/schema.py。

验证旧 LLM 访问配置清理与其余配置校验（LLM 访问参数唯一来源 = 模型档案：
环境变量 CHAT_API_KEY/CHAT_MODEL/CHAT_BASE_URL/CHAT_LOW_MODEL 与 RC 旧键
api_key/base_url/model/provider/low_model 均已移除）。
"""

import pytest

from src.config.schema import _validate_rc


# ── 旧 LLM 访问配置清理 ───────────────────────────────────

@pytest.mark.parametrize("legacy_key", [
    "models", "api_key", "base_url", "model", "provider", "low_model",
])
def test_validate_rc_removes_legacy_llm_keys(legacy_key):
    rc = {legacy_key: "legacy-value", "theme": "dark"}
    _validate_rc(rc)
    assert legacy_key not in rc
    assert rc.get("theme") == "dark"


# ── _validate_rc 类型校验 ─────────────────────────────────

def test_validate_rc_bool_string_conversion():
    rc = {"enable_notifications": "true"}
    _validate_rc(rc)
    assert rc.get("enable_notifications") is True


def test_validate_rc_bool_invalid_value_fallback():
    rc = {"enable_notifications": "not-a-bool"}
    _validate_rc(rc)
    assert isinstance(rc.get("enable_notifications"), bool)


def test_validate_rc_temperature_out_of_range():
    rc = {"temperature": 99.0}
    _validate_rc(rc)
    assert 0.0 <= rc["temperature"] <= 2.0


def test_validate_rc_reasoning_effort_invalid():
    rc = {"reasoning_effort": "INVALID"}
    _validate_rc(rc)
    assert rc["reasoning_effort"] in {"low", "medium", "high", "max"}


def test_validate_rc_no_crash_empty():
    rc = {}
    result = _validate_rc(rc)
    assert isinstance(result, dict)


def test_token_prices_fallback_to_active_profile_provider(monkeypatch):
    """token_prices 缺省时按当前生效档案的 provider 回退内置价表。"""
    import src.config as cfg

    rc = {
        "model_profiles": [{"model": "deepseek-v4-pro", "provider": "deepseek"}],
        "active_model_profile": 0,
        "token_prices": {},
    }
    monkeypatch.setattr(cfg, "get_rc", lambda: rc)
    assert cfg.TOKEN_PRICES

    monkeypatch.setattr(cfg, "get_rc", lambda: {
        "model_profiles": [{"model": "m", "provider": "deepseek"}],
        "active_model_profile": 0,
        "token_prices": {"m": {"input": 1.0, "output": 2.0}},
    })
    assert cfg.TOKEN_PRICES == {"m": {"input": 1.0, "output": 2.0}}
