"""配置 schema 测试 — 覆盖 src/config/schema.py。

验证 API Key provider 探测与配置校验。
"""

import pytest

from src.config.schema import _detect_provider_from_api_key, _validate_rc


# ── _detect_provider_from_api_key ─────────────────────────

def test_detect_anthropic_key():
    assert _detect_provider_from_api_key("sk-ant-api03-xyz") == \
        ("anthropic", "claude-sonnet-4-6")


def test_detect_empty_key():
    assert _detect_provider_from_api_key("") == (None, None)


def test_detect_unknown_key():
    assert _detect_provider_from_api_key("sk-other-key") == (None, None)


# ── _validate_rc 类型校验 ─────────────────────────────────

def test_validate_rc_provider_invalid_fallback():
    rc = {"provider": "not_a_provider"}
    _validate_rc(rc)
    assert rc["provider"] != "not_a_provider"


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
