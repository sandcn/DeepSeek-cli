"""测试 /model 命令同步 provider 行为。

覆盖场景：
- 同 provider 内切换模型 → 不调 update_config
- 跨 provider 切换模型 → 调 update_config 更新到正确 provider
- 切回原 provider → 调 update_config 更新回去
- 自定义模型（不在任何 PROVIDERS 中）→ 不调 update_config
- config_port is None 回退路径
- Picker action="error" 路径
- 多匹配/无匹配不触发同步
- 空模型列表后 PROVIDERS fallback 路径
"""

import sys
import types
import pytest
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, ANY

# ═══════════════════════════════════════════════════════════════════════════
# 测试用的 PROVIDERS 字典（与 defaults.py 结构一致）
# ═══════════════════════════════════════════════════════════════════════════

TEST_PROVIDERS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "default_model": "deepseek-v4-pro",
        "models": ["deepseek-v4-pro", "deepseek-v4-flash"],
        "token_prices": {},
    },
    "glm": {
        "base_url": "https://open.bigmodel.cn/api/paas/v4/chat/completions",
        "default_model": "glm-5.2",
        "models": ["glm-5.2"],
        "token_prices": {},
    },
    "mimo": {
        "base_url": "https://token-plan-cn.xiaomimimo.com/v1/chat/completions",
        "default_model": "mimo-v2.5",
        "models": ["mimo-v2.5"],
        "token_prices": {},
    },
    "custom": {
        "base_url": "",
        "default_model": "",
        "models": [],
        "token_prices": {},
    },
}

# 从 TEST_PROVIDERS 聚合模型列表
ALL_MODELS = []
for _p_cfg in TEST_PROVIDERS.values():
    ALL_MODELS.extend(_p_cfg.get("models", []))


# ═══════════════════════════════════════════════════════════════════════════
# Mock 模块注册
# ═══════════════════════════════════════════════════════════════════════════

def _setup_mocks(provider="deepseek"):
    """设置 mock 模块环境，返回 (cmd_model, make_ctx, update_config_mock, mock_out, mock_loader_get_rc, cleanup)。

    provider 参数控制当前 RC 中的 provider 值。
    """
    mock_out = MagicMock()
    mock_get_default_output_port = MagicMock(return_value=mock_out)

    mock_update_config = MagicMock()
    mock_get_rc = MagicMock(return_value={"provider": provider, "base_url": ""})

    mock_defaults = types.ModuleType('src.config.defaults')
    mock_defaults.PROVIDERS = TEST_PROVIDERS
    mock_defaults.DEFAULTS = {
        "provider": "deepseek",
        "base_url": "",
        "api_key": "",
        "model": "deepseek-v4-flash",
    }

    mock_loader = types.ModuleType('src.config.loader')
    mock_loader.update_config = mock_update_config
    mock_loader.get_rc = mock_get_rc

    mock_config = MagicMock()
    mock_config.MODELS = list(ALL_MODELS)
    mock_config.MODEL = 'deepseek-v4-flash'

    # 包模块
    mock_src = types.ModuleType('src')
    mock_src.__path__ = []
    mock_src.__package__ = 'src'

    mock_src_core = types.ModuleType('src.core')
    mock_src_core.__path__ = []
    mock_src_core.__package__ = 'src.core'

    mock_src_core_commands = types.ModuleType('src.core.commands')
    mock_src_core_commands.__path__ = []
    mock_src_core_commands.__package__ = 'src.core.commands'

    mock_src_core_internal = types.ModuleType('src.core.internal')
    mock_src_core_internal.__path__ = []
    mock_src_core_internal.__package__ = 'src.core.internal'

    mock_src_core_internal_commands = types.ModuleType('src.core.internal.commands')
    mock_src_core_internal_commands.__path__ = []
    mock_src_core_internal_commands.__package__ = 'src.core.internal.commands'

    mock_constants = MagicMock()
    mock_constants.GREEN = '\x1b[32m'
    mock_constants.YELLOW = '\x1b[33m'
    mock_constants.RED = '\x1b[31m'
    mock_constants.DIM = '\x1b[2m'
    mock_constants.RESET = '\x1b[0m'
    mock_constants.CYAN = '\x1b[36m'
    mock_constants.TEAL = '\x1b[36m'

    mock_output_adapter = MagicMock()
    mock_output_adapter.get_default_output_port = mock_get_default_output_port

    mock_cmd_core = types.ModuleType('src.core.internal.commands._command_core')
    mock_cmd_core.CommandContext = MagicMock()
    mock_cmd_core.show_cost = MagicMock()

    mock_commands_base = MagicMock()
    mock_commands_base.CommandPlugin = type('CommandPlugin', (), {})
    mock_commands_base.CommandMeta = MagicMock(return_value=MagicMock(spec=['name', 'description']))
    mock_commands_base.get_plugin_registry = MagicMock(return_value=MagicMock(register=MagicMock()))
    mock_commands_base.CommandPluginRegistry = type('CommandPluginRegistry', (), {})

    _modules = {
        'src': mock_src,
        'src.core': mock_src_core,
        'src.core.commands': mock_src_core_commands,
        'src.core.internal': mock_src_core_internal,
        'src.core.internal.commands': mock_src_core_internal_commands,
        'src.core.internal.commands._command_core': mock_cmd_core,
        'src.core.constants': mock_constants,
        'src.core.adapters': MagicMock(),
        'src.core.adapters.output': mock_output_adapter,
        'src.core.commands.base': mock_commands_base,
        'src.config': mock_config,
        'src.config.defaults': mock_defaults,
        'src.config.loader': mock_loader,
        'src.ui': MagicMock(),
        'src.ui._lock': MagicMock(),
        'src.ui.colors': MagicMock(
            GREEN='\x1b[32m', YELLOW='\x1b[33m', RED='\x1b[31m',
            DIM='\x1b[2m', RESET='\x1b[0m', CYAN='\x1b[36m',
        ),
    }

    saved = {}
    for name, mod in _modules.items():
        saved[name] = sys.modules.get(name)
        sys.modules[name] = mod

    # 使用 importlib 加载被测试模块
    script_dir = str(Path(__file__).resolve().parent.parent / 'src' / 'core')
    spec = importlib.util.spec_from_file_location(
        'src.core.commands._config_cmd',
        f'{script_dir}/commands/_config_cmd.py',
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules['src.core.commands._config_cmd'] = mod
    spec.loader.exec_module(mod)

    _cmd_model = mod._cmd_model

    def make_ctx(**kwargs):
        ctx = MagicMock()
        ctx.arg = kwargs.get('arg', '')
        ctx.state = kwargs.get('state', {})
        ctx.messages = kwargs.get('messages', None)
        ctx.get_user_input = kwargs.get('get_user_input', MagicMock(return_value=''))
        ctx.build_system_prompt = kwargs.get('build_system_prompt', MagicMock())
        ctx.config_port = kwargs.get('config_port', None)
        ctx.ui_adapter = kwargs.get('ui_adapter', None)
        return ctx

    def cleanup():
        for name in _modules:
            saved_mod = saved.get(name)
            if saved_mod is not None:
                sys.modules[name] = saved_mod
            else:
                sys.modules.pop(name, None)
        sys.modules.pop('src.core.commands._config_cmd', None)

    return _cmd_model, make_ctx, mock_update_config, mock_out, mock_get_rc, cleanup


# ═══════════════════════════════════════════════════════════════════════════
# 辅助 fixture：创建常见 config_port mock
# ═══════════════════════════════════════════════════════════════════════════

def _make_config_port(provider="deepseek", models=None):
    cp = MagicMock()
    cp.get_models.return_value = models or list(ALL_MODELS)
    cp.get_model.return_value = (models or ALL_MODELS)[0]
    cp.get.side_effect = lambda key, default="": {
        "provider": provider,
    }.get(key, default)
    return cp


# ═══════════════════════════════════════════════════════════════════════════
# 测试类
# ═══════════════════════════════════════════════════════════════════════════

class TestModelSyncProvider:
    """/model 命令切换模型时同步 provider 的行为。"""

    # ── 按名称切换 ────────────────────────────────────────

    def test_same_provider_no_write(self):
        """同 provider 内按名称切换（deepseek→deepseek-v4-flash），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="deepseek-v4-flash", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-flash"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_cross_provider_write(self):
        """跨 provider 按名称切换（deepseek→glm-5.2），调 update_config("provider", "glm")。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="glm-5.2", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "glm-5.2"
            mock_update_config.assert_called_once_with("provider", "glm")
        finally:
            cleanup()

    def test_cross_provider_back(self):
        """从 GLM 按名称切回 deepseek，调 update_config("provider", "deepseek")。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="glm")
        try:
            cp = _make_config_port(provider="glm")
            ctx = make_ctx(state={"model": "glm-5.2"}, arg="deepseek-v4-pro", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"
            mock_update_config.assert_called_once_with("provider", "deepseek")
        finally:
            cleanup()

    def test_custom_model_no_write(self):
        """自定义模型（不在任何 PROVIDERS 中），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek", models=["deepseek-v4-pro", "deepseek-v4-flash", "my-custom-model"])
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="my-custom-model", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "my-custom-model"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    # ── 按序号切换 ────────────────────────────────────────

    def test_numeric_cross_provider(self):
        """按序号切换跨 provider 模型（idx=3 → glm-5.2），调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="3", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "glm-5.2"
            mock_update_config.assert_called_once_with("provider", "glm")
        finally:
            cleanup()

    def test_numeric_same_provider_no_write(self):
        """按序号同 provider 内切换（idx=2 → deepseek-v4-flash），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="2", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-flash"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    # ── Picker 交互 ───────────────────────────────────────

    def test_picker_cross_provider(self):
        """Picker 交互选择跨 provider 模型，调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            adapter = MagicMock()
            adapter.run_bottom_bar_selection.return_value = {"action": "confirmed", "index": 2}
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, ui_adapter=adapter, config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "glm-5.2"
            mock_update_config.assert_called_once_with("provider", "glm")
        finally:
            cleanup()

    def test_picker_same_model_no_write(self):
        """Picker 选择当前模型（未切换），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            adapter = MagicMock()
            adapter.run_bottom_bar_selection.return_value = {"action": "confirmed", "index": 0}
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, ui_adapter=adapter, config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_picker_same_provider_no_write(self):
        """Picker 同 provider 内切换（idx=1 → deepseek-v4-flash），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            adapter = MagicMock()
            adapter.run_bottom_bar_selection.return_value = {"action": "confirmed", "index": 1}
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, ui_adapter=adapter, config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-flash"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_picker_cancel_no_write(self):
        """Picker 取消选择，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            adapter = MagicMock()
            adapter.run_bottom_bar_selection.return_value = {"action": "cancel", "index": None}
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, ui_adapter=adapter, config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_picker_error_no_write(self):
        """Picker 报错（底部栏不可用），不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            adapter = MagicMock()
            adapter.run_bottom_bar_selection.return_value = {"action": "error", "index": None}
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, ui_adapter=adapter, config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    # ── 未触发切换的路径（不调用 _sync_provider）─────────

    def test_multi_match_no_write(self):
        """多匹配时不切换，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="deepseek", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_no_match_no_write(self):
        """无匹配时不切换，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="nonexistent", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_invalid_number_no_write(self):
        """无效序号不切换，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = _make_config_port(provider="deepseek")
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="99", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-pro"  # 不变
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    # ── config_port is None 回退路径 ─────────────────────

    def test_no_config_port_cross_provider(self):
        """config_port 为 None 时走 get_rc() 回退，仍能正确同步 provider。"""
        _cmd_model, make_ctx, mock_update_config, _, mock_get_rc, cleanup = _setup_mocks(provider="deepseek")
        try:
            mock_get_rc.return_value = {"provider": "deepseek", "base_url": ""}
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="glm-5.2", config_port=None)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "glm-5.2"
            mock_update_config.assert_called_once_with("provider", "glm")
        finally:
            cleanup()

    def test_no_config_port_same_provider(self):
        """config_port 为 None 时同 provider 内切换，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, mock_get_rc, cleanup = _setup_mocks(provider="deepseek")
        try:
            mock_get_rc.return_value = {"provider": "deepseek", "base_url": ""}
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="deepseek-v4-flash", config_port=None)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "deepseek-v4-flash"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    def test_no_config_port_custom_model(self):
        """config_port 为 None 时自定义模型，不调 update_config。"""
        _cmd_model, make_ctx, mock_update_config, _, mock_get_rc, cleanup = _setup_mocks(provider="deepseek")
        try:
            mock_get_rc.return_value = {"provider": "deepseek", "base_url": ""}
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="my-custom-model", config_port=None)
            # 需要走 PROVIDERS fallback 路径，模型列表中要有自定义模型
            result = _cmd_model(ctx)
            assert result is True
            # 自定义模型不在 PROVIDERS 中 → 走到无匹配分支，state 不变
            assert ctx.state["model"] == "deepseek-v4-pro"
            mock_update_config.assert_not_called()
        finally:
            cleanup()

    # ── PROVIDERS fallback 路径（空模型列表）──────────────

    def test_empty_models_fallback_cross_provider(self):
        """MODELS 为空时从 PROVIDERS fallback 聚合，跨 provider 切换仍能同步。"""
        _cmd_model, make_ctx, mock_update_config, _, _, cleanup = _setup_mocks(provider="deepseek")
        try:
            cp = MagicMock()
            cp.get_models.return_value = []  # 空列表 → 触发 PROVIDERS fallback
            cp.get_model.return_value = 'deepseek-v4-pro'
            cp.get.side_effect = lambda key, default="": {
                "provider": "deepseek",
            }.get(key, default)
            ctx = make_ctx(state={"model": "deepseek-v4-pro"}, arg="3", config_port=cp)
            result = _cmd_model(ctx)
            assert result is True
            assert ctx.state["model"] == "glm-5.2"  # fallback 后 index 3
            mock_update_config.assert_called_once_with("provider", "glm")
        finally:
            cleanup()
