"""测试 B4 + B5 修复。

B4: schema.py bool 值被 int_fields 的 continue 跳过
B5: update_config 键名路径不一致
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.config.schema import _validate_rc
from src.config.defaults import DEFAULTS, CONFIG_KEYS


class TestB4BoolIntField:
    """B4 修复：bool 值被 int_fields 跳过 → 回退到默认值"""

    @pytest.mark.parametrize("field,default_value", [
        ("max_context_chars", DEFAULTS.get("max_context_chars", 60000)),
        ("max_retries", DEFAULTS.get("max_retries", 3)),
        ("max_session_messages", DEFAULTS.get("max_session_messages", 0)),
        ("summary_token_budget", DEFAULTS.get("summary_token_budget", 2000)),
    ])
    def test_bool_true_falls_back_to_default(self, field, default_value):
        """bool=True 时回退到默认值"""
        rc = {field: True}
        result = _validate_rc(rc)
        assert result[field] == default_value, (
            f"bool True 应回退到默认值 {default_value}，实际为 {result[field]}"
        )

    @pytest.mark.parametrize("field,default_value", [
        ("max_context_chars", DEFAULTS.get("max_context_chars", 60000)),
        ("max_retries", DEFAULTS.get("max_retries", 3)),
    ])
    def test_bool_false_falls_back_to_default(self, field, default_value):
        """bool=False 时回退到默认值"""
        rc = {field: False}
        result = _validate_rc(rc)
        assert result[field] == default_value, (
            f"bool False 应回退到默认值 {default_value}，实际为 {result[field]}"
        )

    def test_int_value_preserved(self):
        """int 值不受影响"""
        rc = {"max_context_chars": 12345}
        result = _validate_rc(rc)
        assert result["max_context_chars"] == 12345

    def test_invalid_string_falls_back(self):
        """无效字符串回退到默认值"""
        rc = {"max_retries": "abc"}
        result = _validate_rc(rc)
        assert result["max_retries"] == DEFAULTS.get("max_retries", 3)

    def test_numeric_string_converted(self):
        """数字字符串转为 int"""
        rc = {"max_context_chars": "80000"}
        result = _validate_rc(rc)
        assert result["max_context_chars"] == 80000


class TestB5UpdateConfigKeyPath:
    """B5 修复：update_config 键名路径一致性"""

    @pytest.fixture(autouse=True)
    def _protect_rc_file(self):
        """使用 patch 阻止 update_config 写入真实的 RC 文件。"""
        with patch("src.config.loader.RC_FILE") as mock_rc:
            mock_rc.exists.return_value = True
            mock_rc.read_text.return_value = "{}"
            yield

    def test_model_key_maps_to_lowercase(self):
        """update_config('MODEL', ...) 写入 rc['model']"""
        from src.config.loader import update_config, get_rc
        # 重置 _RC_LOADED 以获取干净状态
        import src.config.loader as loader_mod
        loader_mod._RC = None
        loader_mod._RC_LOADED = False

        rc = get_rc()
        old_model = rc.get("model", "")
        try:
            update_config("MODEL", "__test_model__")
            assert rc.get("model") == "__test_model__", (
                f"写入后 rc['model'] 应为 '__test_model__'，实际为 {rc.get('model')}"
            )
        finally:
            update_config("MODEL", old_model)

    def test_unknown_key_backward_compat(self):
        """未知键直接写入 rc[key]（向后兼容）"""
        from src.config.loader import update_config, get_rc
        import src.config.loader as loader_mod
        loader_mod._RC = None
        loader_mod._RC_LOADED = False

        rc = get_rc()
        try:
            update_config("__test_unknown_key__", "__test_value__")
            assert rc.get("__test_unknown_key__") == "__test_value__"
        finally:
            rc.pop("__test_unknown_key__", None)

    def test_simple_path_key(self):
        """单层路径键（如 provider）直接写入"""
        from src.config.loader import update_config, get_rc
        import src.config.loader as loader_mod
        loader_mod._RC = None
        loader_mod._RC_LOADED = False

        rc = get_rc()
        old_provider = rc.get("provider", "")
        try:
            update_config("provider", "__test_provider__")
            assert rc.get("provider") == "__test_provider__"
        finally:
            update_config("provider", old_provider)

    @pytest.mark.skip(reason="嵌套路径写入影响持久 RC 文件")
    def test_nested_path_key(self):
        """嵌套路径键（如 HTTP_CONNECT_TIMEOUT）写入嵌套字典"""
        from src.config.loader import update_config, get_rc
        import src.config.loader as loader_mod
        loader_mod._RC = None
        loader_mod._RC_LOADED = False

        path = CONFIG_KEYS["HTTP_CONNECT_TIMEOUT"]["rc_path"]
        rc = get_rc()
        try:
            update_config("HTTP_CONNECT_TIMEOUT", 60)
            target = rc
            for part in path:
                target = target.get(part, {})
            assert target == 60, f"嵌套路径 {path} 应写入 60，实际为 {target}"
        finally:
            update_config("HTTP_CONNECT_TIMEOUT",
                          CONFIG_KEYS["HTTP_CONNECT_TIMEOUT"]["default"])
