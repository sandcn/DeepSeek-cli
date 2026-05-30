"""Tests for src/config/defaults.py — 配置包常量定义"""

from pathlib import Path

from src.config.defaults import (
    CONFIG_DIR,
    LOG_FILE,
    RC_FILE,
    INPUT_HISTORY_FILE,
    INPUT_DRAFT_FILE,
    PROVIDERS,
    DEFAULTS,
)


# ═══════════════════════════════════════════════════════════════
# CONFIG_DIR 路径测试
# ═══════════════════════════════════════════════════════════════

class TestConfigDirPath:
    """CONFIG_DIR 常量路径验证"""

    # ── 类型 ──────────────────────────────────────────────────

    def test_is_path_object(self):
        """CONFIG_DIR 是 Path 对象"""
        assert isinstance(CONFIG_DIR, Path)

    # ── 路径内容 ──────────────────────────────────────────────

    def test_ends_with_chat_config(self):
        """CONFIG_DIR 以 .chat_config 结尾"""
        assert CONFIG_DIR.name == ".chat_config"

    def test_based_on_home(self):
        """CONFIG_DIR 基于用户 home 目录"""
        assert CONFIG_DIR == Path.home() / ".chat_config"


# ═══════════════════════════════════════════════════════════════
# LOG_FILE / RC_FILE / INPUT_HISTORY_FILE 路径测试
# ═══════════════════════════════════════════════════════════════

class TestDerivedPaths:
    """基于 CONFIG_DIR 的派生路径常量验证"""

    # ── 类型 ──────────────────────────────────────────────────

    def test_log_file_is_path(self):
        """LOG_FILE 是 Path 对象"""
        assert isinstance(LOG_FILE, Path)

    def test_rc_file_is_path(self):
        """RC_FILE 是 Path 对象"""
        assert isinstance(RC_FILE, Path)

    def test_input_history_file_is_path(self):
        """INPUT_HISTORY_FILE 是 Path 对象"""
        assert isinstance(INPUT_HISTORY_FILE, Path)

    # ── 父目录 ────────────────────────────────────────────────

    def test_log_file_under_config_dir(self):
        """LOG_FILE 位于 CONFIG_DIR 下"""
        assert LOG_FILE.parent == CONFIG_DIR

    def test_rc_file_under_config_dir(self):
        """RC_FILE 位于 CONFIG_DIR 下"""
        assert RC_FILE.parent == CONFIG_DIR

    def test_input_history_file_under_config_dir(self):
        """INPUT_HISTORY_FILE 位于 CONFIG_DIR 下"""
        assert INPUT_HISTORY_FILE.parent == CONFIG_DIR

    # ── 文件名 ────────────────────────────────────────────────

    def test_log_file_name(self):
        """LOG_FILE 文件名为 audit.log"""
        assert LOG_FILE.name == "audit.log"

    def test_rc_file_name(self):
        """RC_FILE 文件名为 chatrc.json"""
        assert RC_FILE.name == "chatrc.json"

    def test_input_history_file_name(self):
        """INPUT_HISTORY_FILE 文件名为 input_history"""
        assert INPUT_HISTORY_FILE.name == "input_history"

    # ── 完整路径 ──────────────────────────────────────────────

    def test_log_file_full_path(self):
        """LOG_FILE 完整路径正确"""
        assert LOG_FILE == CONFIG_DIR / "audit.log"

    def test_rc_file_full_path(self):
        """RC_FILE 完整路径正确"""
        assert RC_FILE == CONFIG_DIR / "chatrc.json"

    def test_input_history_file_full_path(self):
        """INPUT_HISTORY_FILE 完整路径正确"""
        assert INPUT_HISTORY_FILE == CONFIG_DIR / "input_history"

    # ── INPUT_DRAFT_FILE ──────────────────────────────────────

    def test_input_draft_file_is_path(self):
        """INPUT_DRAFT_FILE 是 Path 对象"""
        assert isinstance(INPUT_DRAFT_FILE, Path)

    def test_input_draft_file_under_config_dir(self):
        """INPUT_DRAFT_FILE 位于 CONFIG_DIR 下"""
        assert INPUT_DRAFT_FILE.parent == CONFIG_DIR

    def test_input_draft_file_name(self):
        """INPUT_DRAFT_FILE 文件名为 input_draft"""
        assert INPUT_DRAFT_FILE.name == "input_draft"

    def test_input_draft_file_full_path(self):
        """INPUT_DRAFT_FILE 完整路径正确"""
        assert INPUT_DRAFT_FILE == CONFIG_DIR / "input_draft"


# ═══════════════════════════════════════════════════════════════
# PROVIDERS 结构测试
# ═══════════════════════════════════════════════════════════════

class TestProvidersStructure:
    """PROVIDERS 字典结构验证"""

    EXPECTED_PROVIDERS = {"glm", "deepseek", "openai", "claude", "custom"}
    EXPECTED_KEYS = {"base_url", "default_model", "models", "token_prices"}

    # ── 顶层结构 ──────────────────────────────────────────────

    def test_contains_all_providers(self):
        """PROVIDERS 包含全部 5 个 provider"""
        assert set(PROVIDERS) == self.EXPECTED_PROVIDERS

    def test_each_provider_has_required_keys(self):
        """每个 provider 包含 base_url / default_model / models / token_prices"""
        for name, cfg in PROVIDERS.items():
            assert set(cfg) == self.EXPECTED_KEYS, (
                f"provider '{name}' 缺少键: {self.EXPECTED_KEYS - set(cfg)}"
            )

    # ── 特定 provider URL ─────────────────────────────────────

    def test_deepseek_base_url_contains_deepseek_dot_com(self):
        """deepseek 的 base_url 包含 deepseek.com"""
        assert "deepseek.com" in PROVIDERS["deepseek"]["base_url"]

    def test_glm_base_url_contains_z_ai(self):
        """glm 的 base_url 包含 z.ai"""
        assert "z.ai" in PROVIDERS["glm"]["base_url"]

    def test_openai_base_url_contains_openai_dot_com(self):
        """openai 的 base_url 包含 openai.com"""
        assert "openai.com" in PROVIDERS["openai"]["base_url"]

    # ── models 数量 ───────────────────────────────────────────

    def test_deepseek_has_at_least_two_models(self):
        """deepseek 包含至少 2 个 models"""
        assert len(PROVIDERS["deepseek"]["models"]) >= 2

    def test_openai_has_at_least_three_models(self):
        """openai 包含至少 3 个 models"""
        assert len(PROVIDERS["openai"]["models"]) >= 3

    def test_claude_has_at_least_two_models(self):
        """claude 包含至少 2 个 models"""
        assert len(PROVIDERS["claude"]["models"]) >= 2

    def test_custom_models_is_empty(self):
        """custom 的 models 为空列表"""
        assert PROVIDERS["custom"]["models"] == []

    # ── token_prices ──────────────────────────────────────────

    def test_deepseek_token_prices_non_empty(self):
        """deepseek 的 token_prices 非空"""
        assert len(PROVIDERS["deepseek"]["token_prices"]) > 0

    def test_deepseek_token_prices_has_two_models(self):
        """deepseek 的 token_prices 包含 2 个模型条目"""
        assert len(PROVIDERS["deepseek"]["token_prices"]) == 2

    def test_each_price_entry_has_input_and_output(self):
        """每个 token_prices 条目含 'input' 和 'output' 键"""
        for provider_name, cfg in PROVIDERS.items():
            for model_name, prices in cfg["token_prices"].items():
                assert "input" in prices, (
                    f"{provider_name}/{model_name} 缺少 'input'"
                )
                assert "output" in prices, (
                    f"{provider_name}/{model_name} 缺少 'output'"
                )

    def test_all_prices_are_positive(self):
        """所有 price 值为正数"""
        for provider_name, cfg in PROVIDERS.items():
            for model_name, prices in cfg["token_prices"].items():
                assert prices["input"] > 0, (
                    f"{provider_name}/{model_name} input price 非正"
                )
                assert prices["output"] > 0, (
                    f"{provider_name}/{model_name} output price 非正"
                )


# ═══════════════════════════════════════════════════════════════
# DEFAULTS 结构测试
# ═══════════════════════════════════════════════════════════════

class TestDefaultsStructure:
    """DEFAULTS 字典结构验证"""

    EXPECTED_KEYS = {
        "provider", "base_url", "api_key", "model",
        "max_context_chars", "max_output_chars", "max_retries", "retry_base_sec",
        "max_session_messages", "keep_recent_messages",
        "max_context_tokens", "summary_token_budget", "auto_force_compress_threshold",
        "enable_notifications", "notify_on_chat_completion",
        "models", "token_prices", "tool_output_truncate", "theme",
    }

    def test_contains_all_expected_keys(self):
        """DEFAULTS 包含所有预期键"""
        assert set(DEFAULTS) == self.EXPECTED_KEYS

    # ── 特定默认值 ────────────────────────────────────────────

    def test_default_provider_is_deepseek(self):
        """provider 默认值为 deepseek"""
        assert DEFAULTS["provider"] == "deepseek"

    def test_default_model_is_deepseek_v4_flash(self):
        """model 默认值为 deepseek-v4-flash"""
        assert DEFAULTS["model"] == "deepseek-v4-flash"

    def test_default_max_context_chars(self):
        """max_context_chars 默认值为 60000"""
        assert DEFAULTS["max_context_chars"] == 60000

    def test_default_max_retries(self):
        """max_retries 默认值为 3"""
        assert DEFAULTS["max_retries"] == 3

    def test_default_tool_output_truncate(self):
        """tool_output_truncate 默认值为 500"""
        assert DEFAULTS["tool_output_truncate"] == 500

    def test_default_enable_notifications(self):
        """enable_notifications 默认值为 True"""
        assert DEFAULTS["enable_notifications"] is True

    def test_default_theme_is_dark(self):
        """theme 默认值为 dark"""
        assert DEFAULTS["theme"] == "dark"

    def test_default_token_prices_is_empty_dict(self):
        """token_prices 默认为空 dict"""
        assert DEFAULTS["token_prices"] == {}

    def test_default_models_is_empty_list(self):
        """models 默认为空 list"""
        assert DEFAULTS["models"] == []

    def test_default_base_url_is_empty(self):
        """base_url 默认为空字符串"""
        assert DEFAULTS["base_url"] == ""

    def test_default_api_key_is_empty(self):
        """api_key 默认为空字符串"""
        assert DEFAULTS["api_key"] == ""


# ═══════════════════════════════════════════════════════════════
# 类型测试
# ═══════════════════════════════════════════════════════════════

class TestTypes:
    """常量类型验证"""

    # ── 顶层类型 ──────────────────────────────────────────────

    def test_providers_is_dict(self):
        """PROVIDERS 为 dict 类型"""
        assert isinstance(PROVIDERS, dict)

    def test_defaults_is_dict(self):
        """DEFAULTS 为 dict 类型"""
        assert isinstance(DEFAULTS, dict)

    def test_config_dir_is_path(self):
        """CONFIG_DIR 为 Path 类型"""
        assert isinstance(CONFIG_DIR, Path)

    # ── PROVIDERS 嵌套类型 ────────────────────────────────────

    def test_providers_values_token_prices_are_dict(self):
        """每个 provider 的 token_prices 值为 dict"""
        for name, cfg in PROVIDERS.items():
            assert isinstance(cfg["token_prices"], dict), (
                f"provider '{name}' token_prices 不是 dict"
            )

    def test_providers_values_models_are_list(self):
        """每个 provider 的 models 值为 list"""
        for name, cfg in PROVIDERS.items():
            assert isinstance(cfg["models"], list), (
                f"provider '{name}' models 不是 list"
            )

    def test_providers_base_url_is_string(self):
        """每个 provider 的 base_url 值为 str"""
        for name, cfg in PROVIDERS.items():
            assert isinstance(cfg["base_url"], str), (
                f"provider '{name}' base_url 不是 str"
            )

    def test_providers_default_model_is_string(self):
        """每个 provider 的 default_model 值为 str"""
        for name, cfg in PROVIDERS.items():
            assert isinstance(cfg["default_model"], str), (
                f"provider '{name}' default_model 不是 str"
            )

    # ── DEFAULTS 嵌套类型 ─────────────────────────────────────

    def test_defaults_models_is_list(self):
        """DEFAULTS.models 是 list"""
        assert isinstance(DEFAULTS["models"], list)

    def test_defaults_token_prices_is_dict(self):
        """DEFAULTS.token_prices 是 dict"""
        assert isinstance(DEFAULTS["token_prices"], dict)

    def test_numeric_fields_are_int_or_float(self):
        """所有数值字段为 int 或 float"""
        numeric_keys = [
            "max_context_chars", "max_output_chars", "max_retries",
            "retry_base_sec", "max_session_messages", "keep_recent_messages",
            "max_context_tokens", "summary_token_budget",
            "auto_force_compress_threshold", "tool_output_truncate",
        ]
        for key in numeric_keys:
            val = DEFAULTS[key]
            assert isinstance(val, (int, float)), (
                f"DEFAULTS['{key}'] 值 {val!r} 不是数值"
            )

    def test_boolean_fields_are_bool(self):
        """布尔字段为 bool 类型"""
        bool_keys = ["enable_notifications", "notify_on_chat_completion"]
        for key in bool_keys:
            val = DEFAULTS[key]
            assert isinstance(val, bool), (
                f"DEFAULTS['{key}'] 值 {val!r} 不是 bool"
            )

    def test_theme_is_string(self):
        """DEFAULTS.theme 是 str"""
        assert isinstance(DEFAULTS["theme"], str)

    def test_provider_key_is_string(self):
        """DEFAULTS.provider 是 str"""
        assert isinstance(DEFAULTS["provider"], str)

    def test_model_key_is_string(self):
        """DEFAULTS.model 是 str"""
        assert isinstance(DEFAULTS["model"], str)
