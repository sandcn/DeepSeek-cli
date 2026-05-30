"""Tests for src/config/schema.py — _validate_rc 配置校验函数"""

from src.config.schema import _validate_rc


# ═══════════════════════════════════════════════════════════════════
# 基本功能测试
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcBasicFunctionality:
    """_validate_rc 基本功能"""

    def test_empty_dict_returns_complete_config(self):
        """空字典经校验后 provider/base_url/models/token_prices 自动填充
        注意：provider/model 键在输入中不存在时不会自动创建"""
        result = _validate_rc({})

        # provider 不在输入中时不会自动创建键
        assert "provider" not in result
        # 但 provider 默认值用于自动填充逻辑
        assert result["base_url"] == "https://api.deepseek.com/v1/chat/completions"
        assert "deepseek-v4-pro" in result["models"]
        assert "deepseek-v4-flash" in result["models"]
        assert result["token_prices"]["deepseek-v4-pro"]["input"] == 0.55

    def test_valid_config_preserved(self):
        """完整合法的配置输入，类型正确值时原样保留"""
        rc = {
            "provider": "openai",
            "base_url": "https://custom.openai.com/v1",
            "api_key": "sk-xxx",
            "model": "gpt-4o",
            "max_context_chars": 50000,
            "max_output_chars": 2000,
            "max_retries": 5,
            "retry_base_sec": 2.0,
            "max_session_messages": 10,
            "keep_recent_messages": 3,
            "max_context_tokens": 40000,
            "summary_token_budget": 1500,
            "auto_force_compress_threshold": 50000,
            "enable_notifications": True,
            "notify_on_chat_completion": False,
            "models": ["gpt-4o", "gpt-3.5-turbo"],
            "token_prices": {
                "gpt-4o": {"input": 5.0, "output": 15.0}
            }
        }
        result = _validate_rc(dict(rc))

        for key in rc:
            assert result[key] == rc[key], f"字段 {key} 不应被修改"

    def test_returns_all_auto_created_keys(self):
        """返回的字典包含所有自动创建的配置键（不在输入中但由函数添加的键）"""
        result = _validate_rc({})

        # 以下键由函数自动创建（即使不在输入中）
        auto_created = ["base_url", "models", "token_prices"]
        for key in auto_created:
            assert key in result, f"缺少自动创建键: {key}"

        # 以下键仅在输入中存在时才会被处理，不会自动创建
        # （它们由调用方负责提供默认值，或由上层 merge 逻辑补充）
        conditional_keys = [
            "provider", "api_key", "model",
            "max_context_chars", "max_output_chars", "max_retries",
            "retry_base_sec", "max_session_messages", "keep_recent_messages",
            "max_context_tokens", "summary_token_budget",
            "auto_force_compress_threshold",
            "enable_notifications", "notify_on_chat_completion",
        ]
        for key in conditional_keys:
            assert key not in result, f"条件键不应自动创建: {key}"


# ═══════════════════════════════════════════════════════════════════
# int 字段验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcIntFields:
    """int 类型字段验证"""

    INTS = [
        "max_context_chars", "max_output_chars", "max_retries",
        "max_session_messages", "keep_recent_messages",
        "max_context_tokens", "summary_token_budget",
        "auto_force_compress_threshold",
    ]

    def test_int_value_preserved(self):
        """合法的 int 值保持不变"""
        for field in self.INTS:
            rc = {field: 42}
            result = _validate_rc(rc)
            assert result[field] == 42, f"{field} int 值不应被修改"

    def test_string_digit_converted_to_int(self):
        """字符串数字（如 '5000'）被转换为 int"""
        cases = [
            ("max_context_chars", "5000", 5000),
            ("max_retries", "3", 3),
            ("max_output_chars", "100", 100),
        ]
        for field, val, expected in cases:
            rc = {field: val}
            result = _validate_rc(rc)
            assert result[field] == expected, f"{field} 字符串数字应转为 int"

    def test_bool_not_converted_to_int(self):
        """布尔值不被转换为 int（bool 是 int 的子类，特殊处理）"""
        for field in self.INTS:
            rc = {field: True}
            result = _validate_rc(rc)
            assert result[field] is True, f"{field} bool True 不应被转为 int"

            rc = {field: False}
            result = _validate_rc(rc)
            assert result[field] is False, f"{field} bool False 不应被转为 int"

    def test_invalid_string_falls_back(self):
        """非数字字符串回退为默认值"""
        from src.config.defaults import DEFAULTS
        for field in self.INTS:
            rc = {field: "not-a-number"}
            result = _validate_rc(rc)
            assert result[field] == DEFAULTS.get(field, 0), \
                f"{field} 无效字符串应回退为默认值 {DEFAULTS.get(field, 0)}"

    def test_none_falls_back(self):
        """None 回退为默认值（TypeError: int(None)）"""
        from src.config.defaults import DEFAULTS
        for field in self.INTS:
            rc = {field: None}
            result = _validate_rc(rc)
            assert result[field] == DEFAULTS.get(field, 0), \
                f"{field} None 应回退为默认值 {DEFAULTS.get(field, 0)}"


# ═══════════════════════════════════════════════════════════════════
# float 字段验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcFloatFields:
    """float 类型字段验证"""

    def test_float_value_preserved(self):
        """合法的 float 值保持不变"""
        rc = {"retry_base_sec": 3.5}
        result = _validate_rc(rc)
        assert result["retry_base_sec"] == 3.5

    def test_int_kept_as_int(self):
        """int 值保持不变（int 是 (int, float) 的子类，不会被转换）"""
        rc = {"retry_base_sec": 2}
        result = _validate_rc(rc)
        assert result["retry_base_sec"] == 2
        assert isinstance(result["retry_base_sec"], int)

    def test_string_digit_converted_to_float(self):
        """字符串数字（如 '2.5'）被转换为 float"""
        rc = {"retry_base_sec": "2.5"}
        result = _validate_rc(rc)
        assert result["retry_base_sec"] == 2.5
        assert isinstance(result["retry_base_sec"], float)

    def test_invalid_string_falls_back(self):
        """非数字字符串回退为默认值"""
        rc = {"retry_base_sec": "not-a-float"}
        result = _validate_rc(rc)
        assert result["retry_base_sec"] == 1.0

    def test_none_falls_back(self):
        """None 回退为默认值"""
        rc = {"retry_base_sec": None}
        result = _validate_rc(rc)
        assert result["retry_base_sec"] == 1.0


# ═══════════════════════════════════════════════════════════════════
# bool 字段验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcBoolFields:
    """bool 类型字段验证"""

    BOOLS = ["enable_notifications", "notify_on_chat_completion"]

    def test_bool_value_preserved(self):
        """布尔值保持不变"""
        for field in self.BOOLS:
            rc = {field: True}
            result = _validate_rc(rc)
            assert result[field] is True, f"{field} True 应保留"

            rc = {field: False}
            result = _validate_rc(rc)
            assert result[field] is False, f"{field} False 应保留"

    def test_string_true_variants(self):
        """字符串 'true'/'True'/'1'/'yes'/'on' → True"""
        trues = ["true", "True", "TRUE", "1", "yes", "YES", "on", "ON"]
        for field in self.BOOLS:
            for val in trues:
                rc = {field: val}
                result = _validate_rc(rc)
                assert result[field] is True, \
                    f"{field} 字符串 {repr(val)} 应转为 True"

    def test_string_false_variants(self):
        """字符串 'false'/'False'/'0'/'no'/'off' → False"""
        falses = ["false", "False", "FALSE", "0", "no", "NO", "off", "OFF"]
        for field in self.BOOLS:
            for val in falses:
                rc = {field: val}
                result = _validate_rc(rc)
                assert result[field] is False, \
                    f"{field} 字符串 {repr(val)} 应转为 False"

    def test_other_string_falls_to_false(self):
        """无法识别的字符串回退为 False（因 lower() 不在真值元组中）"""
        for field in self.BOOLS:
            rc = {field: "unknown"}
            result = _validate_rc(rc)
            # 注意: 此处"unknown"不是真值元组中的字符串, lower() 后也不在
            # 所以 result 应为 False
            assert result[field] is False, \
                f"{field} 无法识别的字符串应转为 False"

    def test_int_zero_false(self):
        """int 0 → False"""
        for field in self.BOOLS:
            rc = {field: 0}
            result = _validate_rc(rc)
            assert result[field] is False, f"{field} int 0 应转为 False"

    def test_int_one_true(self):
        """int 1 → True"""
        for field in self.BOOLS:
            rc = {field: 1}
            result = _validate_rc(rc)
            assert result[field] is True, f"{field} int 1 应转为 True"

    def test_int_positive_true(self):
        """int 非零值 → True（bool(n) 对非零值为 True）"""
        for field in self.BOOLS:
            rc = {field: 42}
            result = _validate_rc(rc)
            assert result[field] is True, f"{field} int 42 应转为 True"

    def test_other_types_fallback(self):
        """其他类型（list/dict/None 等）回退为默认值"""
        from src.config.defaults import DEFAULTS
        for field in self.BOOLS:
            for val in ([], {}, None, 3.14):
                rc = {field: val}
                result = _validate_rc(rc)
                expected = DEFAULTS.get(field, True)
                assert result[field] is expected, \
                    f"{field} {repr(val)} 应回退为默认值 {expected}"


# ═══════════════════════════════════════════════════════════════════
# provider 验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcProvider:
    """provider 字段验证"""

    def test_valid_provider_preserved(self):
        """合法 provider 字符串保留"""
        valid_providers = ["deepseek", "openai", "glm", "claude", "custom"]
        for provider in valid_providers:
            rc = {"provider": provider}
            result = _validate_rc(rc)
            assert result["provider"] == provider, \
                f"合法 provider {repr(provider)} 应保留"

    def test_invalid_provider_string_fallback(self):
        """非法 provider 字符串回退为默认值 'deepseek'"""
        rc = {"provider": "unknown-llm"}
        result = _validate_rc(rc)
        assert result["provider"] == "deepseek"

    def test_non_string_provider_fallback(self):
        """非字符串 provider 回退为默认值"""
        for val in (123, None, True, ["openai"], {"provider": "x"}):
            rc = {"provider": val}
            result = _validate_rc(rc)
            assert result["provider"] == "deepseek", \
                f"provider {repr(val)} 应回退为 deepseek"


# ═══════════════════════════════════════════════════════════════════
# base_url / api_key 验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcStringFields:
    """base_url 和 api_key 字符串字段验证"""

    def test_string_values_preserved(self):
        """合法字符串保留"""
        rc = {"base_url": "https://custom.api.com", "api_key": "sk-xxx"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://custom.api.com"
        assert result["api_key"] == "sk-xxx"

    def test_non_string_base_url_fallback(self):
        """非字符串 base_url 先回退为默认空字符串，再被 provider 自动填充"""
        from src.config.defaults import DEFAULTS
        for val in (123, None, True, ["url"]):
            rc = {"base_url": val, "provider": "custom"}
            result = _validate_rc(rc)
            assert result["base_url"] == DEFAULTS["base_url"], \
                f"base_url {repr(val)} 应回退为默认空字符串"

    def test_non_string_api_key_fallback(self):
        """非字符串 api_key 回退"""
        from src.config.defaults import DEFAULTS
        for val in (123, None, True, ["key"]):
            rc = {"api_key": val}
            result = _validate_rc(rc)
            assert result["api_key"] == DEFAULTS["api_key"], \
                f"api_key {repr(val)} 应回退为默认值"


# ═══════════════════════════════════════════════════════════════════
# models 验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcModels:
    """models 字段验证"""

    def test_list_preserved_elements_to_str(self):
        """列表保留，元素转换为 str"""
        rc = {"models": ["gpt-4o", 42, None]}
        result = _validate_rc(rc)
        assert result["models"] == ["gpt-4o", "42", "None"]

    def test_tuple_converted_to_list(self):
        """元组转换为列表"""
        rc = {"models": ("deepseek-v4-pro", "deepseek-chat")}
        result = _validate_rc(rc)
        assert result["models"] == ["deepseek-v4-pro", "deepseek-chat"]
        assert isinstance(result["models"], list)

    def test_non_list_tuple_fallback(self):
        """非列表/元组回退为默认值 []"""
        for val in ("string", 123, None, True, {"a": "b"}):
            rc = {"models": val}
            result = _validate_rc(rc)
            # provider 为默认 deepseek 时会自动填充 models
            assert result["models"] == [
                "deepseek-v4-pro", "deepseek-v4-flash",
            ], f"models {repr(val)} 应回退并由 provider 填充"

    def test_models_not_overwritten_when_provided(self):
        """显式提供的 models 不会被 provider 覆盖"""
        rc = {"provider": "deepseek", "models": ["custom-model"]}
        result = _validate_rc(rc)
        assert result["models"] == ["custom-model"]

    def test_model_not_in_list_gets_appended(self):
        """当前 model 不在 models 列表中时自动追加"""
        rc = {"model": "my-custom-model", "models": ["deepseek-v4-pro"]}
        result = _validate_rc(rc)
        assert "my-custom-model" in result["models"]

    def test_model_already_in_list_not_duplicated(self):
        """当前 model 已在 models 列表中时不重复追加"""
        rc = {"model": "deepseek-v4-pro", "models": ["deepseek-v4-pro", "deepseek-chat"]}
        result = _validate_rc(rc)
        assert result["models"] == ["deepseek-v4-pro", "deepseek-chat"]

    def test_auto_filled_models_include_default_model(self):
        """provider 自动填充 models 后，默认 model 应被追加"""
        # provider=openai 时 models 为空
        rc = {"provider": "openai", "model": "custom-model"}
        result = _validate_rc(rc)
        assert "custom-model" in result["models"]
        assert result["models"] == ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "custom-model"]


# ═══════════════════════════════════════════════════════════════════
# token_prices 验证
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcTokenPrices:
    """token_prices 字段验证"""

    def test_valid_token_prices_preserved(self):
        """合法格式的 token_prices 保留"""
        rc = {
            "token_prices": {
                "model-a": {"input": 0.5, "output": 1.0},
                "model-b": {"input": 1.5, "output": 3.0}
            }
        }
        result = _validate_rc(rc)
        assert result["token_prices"] == {
            "model-a": {"input": 0.5, "output": 1.0},
            "model-b": {"input": 1.5, "output": 3.0}
        }

    def test_values_converted_to_float(self):
        """input/output 值自动转为 float"""
        rc = {
            "token_prices": {
                "model-a": {"input": "0.5", "output": "1"}
            }
        }
        result = _validate_rc(rc)
        assert result["token_prices"]["model-a"]["input"] == 0.5
        assert result["token_prices"]["model-a"]["output"] == 1.0
        assert isinstance(result["token_prices"]["model-a"]["input"], float)
        assert isinstance(result["token_prices"]["model-a"]["output"], float)

    def test_missing_input_output_skipped(self):
        """缺少 input 或 output 的项被跳过"""
        rc = {
            "token_prices": {
                "valid": {"input": 1.0, "output": 2.0},
                "no_input": {"output": 2.0},
                "no_output": {"input": 1.0},
                "empty": {}
            }
        }
        result = _validate_rc(rc)
        assert "valid" in result["token_prices"]
        assert "no_input" not in result["token_prices"]
        assert "no_output" not in result["token_prices"]
        assert "empty" not in result["token_prices"]

    def test_non_floatable_value_skipped(self):
        """值无法转 float 的项被跳过"""
        rc = {
            "token_prices": {
                "good": {"input": 1.0, "output": 2.0},
                "bad_input": {"input": "not-a-number", "output": 2.0},
                "bad_output": {"input": 1.0, "output": [1, 2, 3]},
            }
        }
        result = _validate_rc(rc)
        assert "good" in result["token_prices"]
        assert "bad_input" not in result["token_prices"]
        assert "bad_output" not in result["token_prices"]

    def test_non_dict_value_skipped(self):
        """值不是 dict 的项被跳过"""
        rc = {
            "token_prices": {
                "good": {"input": 1.0, "output": 2.0},
                "string": "not-a-dict",
                "number": 42,
                "list": [1, 2]
            }
        }
        result = _validate_rc(rc)
        assert "good" in result["token_prices"]
        assert "string" not in result["token_prices"]
        assert "number" not in result["token_prices"]
        assert "list" not in result["token_prices"]

    def test_non_dict_token_prices_cleared(self):
        """token_prices 非 dict 时清空（回退为空 dict 并由 provider 填充）"""
        for val in ("string", 123, None, True, [1, 2]):
            rc = {"token_prices": val}
            result = _validate_rc(rc)
            # provider 默认 deepseek 会填充 token_prices
            assert isinstance(result["token_prices"], dict)
            assert len(result["token_prices"]) > 0

    def test_keys_converted_to_str(self):
        """model 名称转为 str"""
        rc = {
            "token_prices": {
                123: {"input": 1.0, "output": 2.0}
            }
        }
        result = _validate_rc(rc)
        assert "123" in result["token_prices"]
        assert 123 not in result["token_prices"]


# ═══════════════════════════════════════════════════════════════════
# 边界条件
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcBoundaryConditions:
    """边界条件与特殊值"""

    def test_negative_max_retries_fallback(self):
        """max_retries 负数回退为默认值"""
        rc = {"max_retries": -1}
        result = _validate_rc(rc)
        assert result["max_retries"] == 3

    def test_negative_max_context_chars_fallback(self):
        """max_context_chars 负数回退为默认值"""
        rc = {"max_context_chars": -100}
        result = _validate_rc(rc)
        assert result["max_context_chars"] == 60000

    def test_zero_max_retries_kept(self):
        """max_retries = 0 保留（非负数）"""
        rc = {"max_retries": 0}
        result = _validate_rc(rc)
        assert result["max_retries"] == 0

    def test_negative_after_conversion_fallback(self):
        """字符串负数转换后仍被检测并回退"""
        rc = {"max_retries": "-5"}
        result = _validate_rc(rc)
        # 先转为 int -5，然后检测 < 0，回退为 3
        assert result["max_retries"] == 3

    def test_empty_base_url_filled_by_provider(self):
        """空字符串 base_url 由 provider 填充"""
        rc = {"provider": "openai", "base_url": ""}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.openai.com/v1/chat/completions"

    def test_empty_models_filled_by_provider(self):
        """空列表 models 由 provider 填充"""
        rc = {"provider": "openai", "models": []}
        result = _validate_rc(rc)
        assert result["models"] == ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo"]

    def test_empty_token_prices_filled_by_provider(self):
        """空 dict token_prices 由 provider 填充"""
        rc = {"provider": "deepseek", "token_prices": {}}
        result = _validate_rc(rc)
        assert len(result["token_prices"]) > 0

    def test_missing_base_url_filled_by_provider(self):
        """缺失 base_url 键由 provider 填充"""
        rc = {"provider": "openai"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.openai.com/v1/chat/completions"

    def test_explicit_base_url_not_overwritten(self):
        """显式提供的非空 base_url 不被 provider 覆盖"""
        rc = {"provider": "openai", "base_url": "https://my-proxy.com/v1"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://my-proxy.com/v1"

    def test_none_max_retries_no_attribute_error(self):
        """max_retries 为 None 时 get 后 < 0 比较不应报错"""
        # None < 0 → TypeError in Python 3? 实际上在 Python 3 中
        # None 与 int 比较会 TypeError。但代码中是 rc.get("max_retries", 1) < 0，
        # 如果 max_retries 是 None，则 rc.get("max_retries", 1) 返回 None，
        # None < 0 在 Python 3 中会抛出 TypeError。
        # 但注意：在 int 字段处理中，None 已经被 try/except 捕获并回退为默认值。
        # 所以 max_retries 不会是 None。
        rc = {"max_retries": None}
        result = _validate_rc(rc)
        # None 先被 int 字段处理回退为 3
        assert result["max_retries"] == 3

    def test_claude_provider_auto_fills(self):
        """claude provider 自动填充 base_url/models/token_prices"""
        rc = {"provider": "claude"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.anthropic.com/v1"
        assert len(result["models"]) >= 1
        assert isinstance(result["token_prices"], dict)


# ═══════════════════════════════════════════════════════════════════
# provider 自动填充综合测试
# ═══════════════════════════════════════════════════════════════════

class TestValidateRcProviderAutoFill:
    """provider 自动填充行为"""

    def test_deepseek_auto_fill(self):
        """deepseek provider 自动填充 base_url/models/token_prices"""
        rc = {"provider": "deepseek"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.deepseek.com/v1/chat/completions"
        assert "deepseek-v4-pro" in result["models"]
        assert "deepseek-v4-flash" in result["models"]
        assert result["token_prices"]["deepseek-v4-pro"]["input"] == 0.55

    def test_openai_auto_fill(self):
        """openai provider 自动填充 base_url/models"""
        rc = {"provider": "openai"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.openai.com/v1/chat/completions"
        assert "gpt-4o" in result["models"]
        assert result["token_prices"] == {}  # openai 没有 token_prices

    def test_glm_auto_fill(self):
        """glm provider 自动填充 base_url/models"""
        rc = {"provider": "glm"}
        result = _validate_rc(rc)
        assert result["base_url"] == "https://api.z.ai/api/coding/paas/v4"
        assert "glm-5.1" in result["models"]
        assert result["token_prices"] == {}

    def test_custom_provider_no_auto_fill(self):
        """custom provider 不填充任何值"""
        rc = {"provider": "custom", "base_url": "", "models": [], "token_prices": {}}
        result = _validate_rc(rc)
        assert result["base_url"] == ""
        assert result["models"] == []
        assert result["token_prices"] == {}

    def test_non_default_provider_with_model_appended(self):
        """非 deepseek provider 且 model 不在列表中时自动追加"""
        rc = {"provider": "openai", "model": "my-custom-model"}
        result = _validate_rc(rc)
        assert "my-custom-model" in result["models"]
        # model 是在 auto-fill 之后追加的
        assert result["models"] == ["gpt-4o", "gpt-4-turbo", "gpt-3.5-turbo", "my-custom-model"]
