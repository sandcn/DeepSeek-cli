"""模型档案生成参数（温度 / 思考级别）纯逻辑测试。

2026-10-17 用户需求「/models 支持设置对应的温度跟思考级别，删除以前全局
的命令」：温度与思考级别按模型档案独立设置；档案字段为空时沿用全局默认
（``temperature`` / ``reasoning_effort`` 配置键）。全局 /temperature 与
/reasoning 命令已删除。
"""

from __future__ import annotations

import math

import pytest

import src.config.model_profiles as mp
from src.config.schema import _validate_rc


# ── 字段元数据 / 规范化 ─────────────────────────────────

class TestFields:

    def test_field_order_and_kinds(self):
        assert mp.FIELD_KEYS[-3:] == ("temperature", "reasoning_effort", "multimodal")
        assert (mp.field_by_key("temperature") or {}).get("kind") == "input"
        assert (mp.field_by_key("reasoning_effort") or {}).get("kind") == "select"
        assert (mp.field_by_key("multimodal") or {}).get("kind") == "select"

    def test_multimodal_defaults_false(self):
        assert mp.empty_profile()["multimodal"] == "false"
        assert mp.normalize_profile({"model": "m"})["multimodal"] == "false"
        assert mp.profile_supports_multimodal({"multimodal": "false"}) is False
        assert mp.profile_supports_multimodal({"multimodal": "TRUE"}) is True
        assert mp.profile_supports_multimodal({"multimodal": True}) is True

    def test_multimodal_choices(self):
        assert [c[0] for c in mp.multimodal_choices()] == ["false", "true"]
        assert all(desc for _value, desc in mp.multimodal_choices())

    def test_normalize_keeps_generation_fields_as_text(self):
        out = mp.normalize_profile({
            "model": "m", "temperature": 0.7, "reasoning_effort": "HIGH",
        })
        assert out["temperature"] == "0.7"
        assert out["reasoning_effort"] == "HIGH"

    def test_empty_profile_has_generation_fields(self):
        p = mp.empty_profile()
        assert p["temperature"] == "" and p["reasoning_effort"] == ""


# ── 多模态读图开关（模型档案唯一判据） ──────────────────

class TestMultimodalSetting:

    def test_model_supports_multimodal_by_profile(self, monkeypatch):
        rc = {
            "model_profiles": [
                {"model": "text-model", "multimodal": "false"},
                {"model": "vision-model", "multimodal": "true"},
            ],
            "active_model_profile": 0,
        }
        monkeypatch.setattr("src.config.loader.get_rc", lambda: rc)
        assert mp.model_supports_multimodal("vision-model") is True
        assert mp.model_supports_multimodal("text-model") is False
        assert mp.model_supports_multimodal("unknown-model") is False
        # 空模型名 → 当前生效档案（text-model）
        assert mp.model_supports_multimodal(None) is False
        assert mp.current_multimodal() is False

    def test_active_profile_priority_for_duplicate_model_name(self, monkeypatch):
        rc = {
            "model_profiles": [
                {"model": "dup", "multimodal": "false"},
                {"model": "dup", "multimodal": "true"},
            ],
            "active_model_profile": 1,
        }
        monkeypatch.setattr("src.config.loader.get_rc", lambda: rc)
        assert mp.model_profile_for_model("dup")["multimodal"] == "true"
        assert mp.model_supports_multimodal("dup") is True


# ── 校验 / 规范化 ───────────────────────────────────────

class TestValidate:

    def _profile(self, **kwargs):
        p = mp.empty_profile()
        p.update(model="m", **kwargs)
        return p

    def test_empty_generation_fields_pass(self):
        p = self._profile()
        assert mp.validate_profile(p, []) == ""
        assert p["temperature"] == "" and p["reasoning_effort"] == ""
        assert p["multimodal"] == "false"

    def test_multimodal_normalized_on_validate(self):
        p = self._profile(multimodal="TRUE")
        assert mp.validate_profile(p, []) == ""
        assert p["multimodal"] == "true"
        p2 = self._profile(multimodal="bogus")
        assert mp.validate_profile(p2, []) == ""
        assert p2["multimodal"] == "false"

    def test_temperature_normalized(self):
        p = self._profile(temperature=" 0.757 ")
        assert mp.validate_profile(p, []) == ""
        assert p["temperature"] == "0.76"

    def test_temperature_integer_string(self):
        p = self._profile(temperature="1")
        assert mp.validate_profile(p, []) == ""
        assert p["temperature"] == "1"

    @pytest.mark.parametrize("raw", ["3", "-0.1", "abc", "nan", "inf", ""])
    def test_temperature_out_of_range_or_non_finite(self, raw):
        if raw == "":
            p = self._profile(temperature=raw)
            assert mp.validate_profile(p, []) == ""
            return
        p = self._profile(temperature=raw)
        err = mp.validate_profile(p, [])
        assert "温度" in err
        assert p["temperature"] == raw  # 校验失败不规范化

    def test_reasoning_effort_lowercased(self):
        p = self._profile(reasoning_effort="HIGH")
        assert mp.validate_profile(p, []) == ""
        assert p["reasoning_effort"] == "high"

    def test_reasoning_effort_invalid(self):
        p = self._profile(reasoning_effort="ultra")
        err = mp.validate_profile(p, [])
        assert "思考级别" in err


# ── 有效值（档案 > 全局默认） ───────────────────────────

class TestEffectiveValues:

    def test_profile_overrides_global(self):
        profile = {"temperature": "0.9", "reasoning_effort": "low"}
        rc = {"temperature": 0.2, "reasoning_effort": "max"}
        assert mp.effective_temperature(profile, rc) == 0.9
        assert mp.effective_reasoning_effort(profile, rc) == "low"

    def test_empty_falls_back_to_global(self):
        profile = {"temperature": "", "reasoning_effort": ""}
        rc = {"temperature": 0.4, "reasoning_effort": "high"}
        assert mp.effective_temperature(profile, rc) == 0.4
        assert mp.effective_reasoning_effort(profile, rc) == "high"

    def test_invalid_falls_back_to_global(self):
        profile = {"temperature": "nan", "reasoning_effort": "ultra"}
        rc = {"temperature": 0.3, "reasoning_effort": "medium"}
        assert mp.effective_temperature(profile, rc) == 0.3
        assert mp.effective_reasoning_effort(profile, rc) == "medium"

    def test_global_invalid_uses_builtin_defaults(self):
        profile = {}
        rc = {"temperature": float("nan"), "reasoning_effort": "bogus"}
        assert mp.effective_temperature(profile, rc) == 0.2
        assert mp.effective_reasoning_effort(profile, rc) == "max"

    def test_current_values_use_active_profile(self, monkeypatch):
        rc = {
            "model_profiles": [
                {"model": "a", "temperature": "0.1", "reasoning_effort": "low"},
                {"model": "b", "temperature": "1.5", "reasoning_effort": "max"},
            ],
            "active_model_profile": 1,
            "temperature": 0.2,
            "reasoning_effort": "high",
        }
        monkeypatch.setattr("src.config.loader.get_rc", lambda: rc)
        assert mp.current_temperature() == 1.5
        assert mp.current_reasoning_effort() == "max"
        assert mp.current_temperature(rc) == 1.5


# ── 候选项 / 条目 ──────────────────────────────────────

class TestChoicesAndEntries:

    def test_reasoning_effort_choices(self):
        choices = mp.reasoning_effort_choices("max")
        values = [c[0] for c in choices]
        assert values == ["", "low", "medium", "high", "max"]
        assert "当前 max" in dict(choices)[""]

    def test_build_model_entries_contains_raw_and_effective(self):
        rc = {
            "model_profiles": [
                {"model": "m1", "provider": "deepseek",
                 "temperature": "0.7", "reasoning_effort": "high"},
                {"model": "m2", "provider": "deepseek"},
            ],
            "active_model_profile": 0,
            "temperature": 0.25,
            "reasoning_effort": "medium",
        }
        entries = mp.build_model_entries(rc)
        assert entries[0]["temperature"] == "0.7"
        assert entries[0]["effective_temperature"] == 0.7
        assert entries[0]["reasoning_effort"] == "high"
        assert entries[0]["effective_reasoning_effort"] == "high"
        assert entries[0]["multimodal"] == "false"
        assert entries[1]["temperature"] == ""
        assert entries[1]["effective_temperature"] == 0.25
        assert entries[1]["reasoning_effort"] == ""
        assert entries[1]["effective_reasoning_effort"] == "medium"


# ── schema 兜底清洗 ────────────────────────────────────

class TestSchemaCleanup:

    def test_invalid_profile_generation_values_cleared(self):
        rc = {
            "model_profiles": [
                {"model": "m", "temperature": "nan", "reasoning_effort": "ULTRA"},
                {"model": "m2", "temperature": "1.5", "reasoning_effort": "HIGH"},
                {"model": "m3", "temperature": 3, "reasoning_effort": ""},
                "not-a-dict",
            ],
        }
        _validate_rc(rc)
        profiles = rc["model_profiles"]
        assert len(profiles) == 3
        assert profiles[0]["temperature"] == ""
        assert profiles[0]["reasoning_effort"] == ""
        assert profiles[1]["temperature"] == "1.5"
        assert profiles[1]["reasoning_effort"] == "high"
        assert profiles[2]["temperature"] == ""  # 越界 → 空（回退全局）

    def test_non_list_profiles_reset(self):
        rc = {"model_profiles": {"bad": "type"}}
        _validate_rc(rc)
        assert rc["model_profiles"] == []

    def test_multimodal_flag_cleaned(self):
        rc = {
            "model_profiles": [
                {"model": "m1", "multimodal": "TRUE"},
                {"model": "m2", "multimodal": True},
                {"model": "m3", "multimodal": "bogus"},
                {"model": "m4"},
            ],
        }
        _validate_rc(rc)
        profiles = rc["model_profiles"]
        assert profiles[0]["multimodal"] == "true"
        assert profiles[1]["multimodal"] == "true"
        assert profiles[2]["multimodal"] == "false"
        assert profiles[3]["multimodal"] == "false"

    def test_global_temperature_still_validated(self):
        rc = {"temperature": 99.0}
        _validate_rc(rc)
        assert 0.0 <= rc["temperature"] <= 2.0
        assert math.isfinite(rc["temperature"])
