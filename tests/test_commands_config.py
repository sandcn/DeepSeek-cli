"""测试 src.core.commands._config_cmd：配置命令处理函数。

模块概况
--------
- _cmd_model：显示模型列表、选择模型
- _cmd_system：追加系统提示词、显示系统提示词列表、无参数交互
- _cmd_cost：调用 show_cost 显示费用
- _cmd_theme：切换主题、显示当前主题

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- 预先在 sys.modules 中 mock 所有外部依赖（ui.colors, config, core.ports.output,
  api.stats, ui.theme, core._command_core 等）
- 每个测试函数关注一个命令处理函数的一种行为，遵循"一个断言概念一个测试"
- 边界值、异常路径、正常路径全覆盖
"""

import sys
import pytest
import types
import importlib.util
from pathlib import Path
from unittest.mock import MagicMock, call, ANY

# ═══════════════════════════════════════════════════════════════════════════
# 全局 mock 对象（被所有测试函数共享）
# ═══════════════════════════════════════════════════════════════════════════

# -- 输出端口 --
mock_out = MagicMock()
mock_get_default_output_port = MagicMock(return_value=mock_out)

# -- config --
_mock_config = MagicMock()
_mock_config.MODELS = ['gpt-4o', 'gpt-4o-mini', 'claude-3.5-sonnet']
_mock_config.MODEL = 'gpt-4o'
_mock_config.TOKEN_PRICES = {'gpt-4o': {'input': 0.01, 'output': 0.03}}
_mock_config.MAX_CONTEXT_CHARS = 128000
_mock_config.update_config = MagicMock()

# -- ui.colors --
_mock_colors = MagicMock()
_mock_colors.GREEN = '\x1b[32m'
_mock_colors.YELLOW = '\x1b[33m'
_mock_colors.RED = '\x1b[31m'
_mock_colors.DIM = '\x1b[2m'
_mock_colors.RESET = '\x1b[0m'
_mock_colors.TEAL = '\x1b[36m'
_mock_colors.CYAN = '\x1b[36m'

# -- ui.theme --
_mock_theme = MagicMock()
_mock_theme.set_theme = MagicMock()
_mock_theme.get_active_theme = MagicMock()
_mock_theme.get_theme_names_with_desc = MagicMock()

# -- api.stats --
_mock_stats = MagicMock()
_mock_stats.get_token_stats = MagicMock()
_mock_stats.get_session_start_time = MagicMock()

# -- core._command_core --
_mock_cmd_core = MagicMock()
_mock_cmd_core.register_command = MagicMock()
_mock_cmd_core.CommandContext = MagicMock()
_mock_cmd_core.show_cost = MagicMock()

# -- 构建 mock 模块注册表 --
_MOCK_MODULES = {
    'src': MagicMock(),
    'src.core': MagicMock(),
    'src.core.ports': MagicMock(),
    'src.core.ports.output': MagicMock(get_default_output_port=mock_get_default_output_port),
    'src.core.internal._command_core': _mock_cmd_core,
    'src.config': _mock_config,
    'src.ui': MagicMock(),
    'src.ui.colors': _mock_colors,
    'src.ui.theme': _mock_theme,
    'src.ui._lock': MagicMock(),
    'src.ui._bottom_bar': MagicMock(),
    'src.ui._completion': MagicMock(),
    'src.ui.picker': MagicMock(),  # 已删除的 picker.py，防御性 mock
    'src.api': MagicMock(),
    'src.api.stats': _mock_stats,
    'src.api.escape_monitor': MagicMock(get_active_monitor=MagicMock(return_value=None)),
    'src.chat_ui': MagicMock(get_active_chat_ui=MagicMock(return_value=None)),
}

# 注册父包为 proper package（使相对导入可解析）
# 这些包不能使用 MagicMock（无 __path__），使用 ModuleType + __path__ = []
# 重要：这些包不在 _MOCK_MODULES 中，以避免被下方的循环用 MagicMock 覆盖
import types as _types
_PACKAGE_MODULES: dict[str, object] = {}
for _pkg in ('src', 'src.core', 'src.core.ports', 'src.core.commands', 'src.core.internal'):
    _pm = _types.ModuleType(_pkg)
    _pm.__path__ = []
    _pm.__package__ = _pkg
    _PACKAGE_MODULES[_pkg] = _pm

# 保存原始模块，无条件注入 mock（即使模块已被其他测试加载）
_ORIGINAL_MODULES: dict[str, object] = {}
# 先注入包模块（不会被 _MOCK_MODULES 覆盖因为有 if 判断保护）
for _pkg_name, _pkg_mod in _PACKAGE_MODULES.items():
    _ORIGINAL_MODULES[_pkg_name] = sys.modules.get(_pkg_name)
    sys.modules[_pkg_name] = _pkg_mod
# 再注入 mock 模块
for mod_name, mod in _MOCK_MODULES.items():
    _ORIGINAL_MODULES[mod_name] = sys.modules.get(mod_name)
    sys.modules[mod_name] = mod

# ═══════════════════════════════════════════════════════════════════════════
# 使用 importlib 直接加载 commands_config.py
# ═══════════════════════════════════════════════════════════════════════════

_SCRIPT_DIR = str(Path(__file__).resolve().parent.parent / 'src' / 'core')
_spec = importlib.util.spec_from_file_location(
    'src.core.commands._config_cmd', f'{_SCRIPT_DIR}/commands/_config_cmd.py',
)
_mod = importlib.util.module_from_spec(_spec)
sys.modules['src.core.commands._config_cmd'] = _mod
_spec.loader.exec_module(_mod)

# ── 提取被测试符号 ──────────────────────────────────────────────────────
_cmd_model = _mod._cmd_model
_cmd_system = _mod._cmd_system
_cmd_cost = _mod._cmd_cost
_cmd_theme = _mod._cmd_theme

# ── 清理 mock，恢复原始模块 ────────────────────────────────────────────
for mod_name in list(_MOCK_MODULES.keys()):
    orig = _ORIGINAL_MODULES.get(mod_name)
    if orig is not None:
        sys.modules[mod_name] = orig
    else:
        sys.modules.pop(mod_name, None)
sys.modules.pop('src.core.commands._config_cmd', None)
for _pkg_name in list(_PACKAGE_MODULES.keys()):
    orig = _ORIGINAL_MODULES.get(_pkg_name)
    if orig is not None:
        sys.modules[_pkg_name] = orig
    else:
        sys.modules.pop(_pkg_name, None)


# ═══════════════════════════════════════════════════════════════════════════
# 辅助函数
# ═══════════════════════════════════════════════════════════════════════════

def _make_ctx(**kwargs):
    """创建模拟的 CommandContext 对象。"""
    ctx = MagicMock()
    ctx.arg = kwargs.get('arg', '')
    ctx.state = kwargs.get('state', {})
    ctx.messages = kwargs.get('messages', None)
    ctx.get_user_input = kwargs.get('get_user_input', MagicMock(return_value=''))
    ctx.build_system_prompt = kwargs.get('build_system_prompt', MagicMock())
    return ctx


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

@pytest.fixture(autouse=True)
def reset_mocks():
    """每个测试前重置全局 mock 的状态。"""
    mock_out.reset_mock()
    _mock_config.update_config.reset_mock()
    _mock_theme.set_theme.reset_mock()
    _mock_theme.get_active_theme.reset_mock()
    _mock_theme.get_theme_names_with_desc.reset_mock()
    _mock_cmd_core.show_cost.reset_mock()


# ═══════════════════════════════════════════════════════════════════════════
# 1. _cmd_model
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdModel:
    """模型选择命令。"""

    def _mock_bottom_bar(self, monkeypatch, action="confirmed", index=1):
        """Mock run_bottom_bar_selection 返回指定结果。

        通过 monkeypatch.setattr 直接修改模块对象的属性，
        sys.modules 清理后仍有效（_cmd_model.__globals__ 持有模块 dict 引用）。
        注：commands_config 中 run_bottom_bar_selection 是从 _bottom_bar 导入的，
        monkeypatch 修改 _mod 的属性即可拦截 _cmd_model 中的调用。
        """
        from unittest.mock import MagicMock
        mock_select = MagicMock(return_value={"action": action, "index": index})
        monkeypatch.setattr(_mod, 'run_bottom_bar_selection', mock_select)
        return mock_select

    def test_select_valid_model(self, monkeypatch):
        """底部栏选择 #1 (gpt-4o-mini) 时更新 state['model']。"""
        self._mock_bottom_bar(monkeypatch, action="confirmed", index=1)
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o-mini"
        mock_out.write.assert_any_call(
            '\x1b[32m  + 已切换到 gpt-4o-mini\x1b[0m',
            level="raw", source="cmd",
        )

    def test_select_last_model(self, monkeypatch):
        """选择最后一个模型 (index=2 → claude-3.5-sonnet)。"""
        self._mock_bottom_bar(monkeypatch, action="confirmed", index=2)
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "claude-3.5-sonnet"

    def test_select_first_model(self, monkeypatch):
        """选择第一个模型 (index=0 → gpt-4o)。"""
        self._mock_bottom_bar(monkeypatch, action="confirmed", index=0)
        state = {"model": "claude-3.5-sonnet"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"

    def test_select_same_model(self, monkeypatch):
        """选择当前模型时提示不变。"""
        self._mock_bottom_bar(monkeypatch, action="confirmed", index=0)
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"  # 不变
        mock_out.write.assert_any_call(
            '\x1b[2m  当前已是 gpt-4o\x1b[0m',
            level="raw", source="cmd",
        )

    def test_select_cancel(self, monkeypatch):
        """取消时不切换。"""
        self._mock_bottom_bar(monkeypatch, action="cancel", index=None)
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"  # 不变
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 已取消\x1b[0m',
            level="raw", source="cmd",
        )

    def test_select_error_fallback(self, monkeypatch):
        """底部栏不可用时回退到文本提示。"""
        self._mock_bottom_bar(monkeypatch, action="error", index=None)
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"  # 不变
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 底部栏不可用，请直接指定模型名称\x1b[0m',
            level="raw", source="cmd",
        )

    def test_no_state_model_fallback_default(self, monkeypatch):
        """state 中没有 model 键时使用 config.MODEL 作为当前模型。"""
        self._mock_bottom_bar(monkeypatch, action="confirmed", index=1)
        state = {}
        ctx = _make_ctx(state=state)

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o-mini"

    def test_direct_param_by_number(self):
        """直接参数 /model 2 按序号切换。"""
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state, arg="2")

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o-mini"

    def test_direct_param_by_name(self):
        """直接参数 /model gpt-4o-mini 按名称切换。"""
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state, arg="gpt-4o-mini")

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o-mini"

    def test_direct_param_invalid_number(self):
        """直接参数无效序号显示错误。"""
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state, arg="99")

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 无效序号，范围 1-3\x1b[0m',
            level="raw", source="cmd",
        )

    def test_direct_param_not_found(self):
        """直接参数未知模型名显示错误。"""
        state = {"model": "gpt-4o"}
        ctx = _make_ctx(state=state, arg="unknown-model")

        result = _cmd_model(ctx)

        assert result is True
        assert ctx.state["model"] == "gpt-4o"


# ═══════════════════════════════════════════════════════════════════════════
# 2. _cmd_system
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdSystem:
    """系统提示词命令。"""

    def test_empty_messages_returns_warning(self):
        """消息列表为空时显示警告，不执行其他操作。"""
        ctx = _make_ctx(messages=[], arg="")

        result = _cmd_system(ctx)

        assert result is True
        # 应输出警告信息
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 消息列表为空，无法修改\x1b[0m',
            level="raw", source="cmd",
        )

    def test_append_with_arg_no_system_exists(self):
        """有 arg、但 messages 中没有 system 消息时，last_system_idx 为 -1，
        追加到索引 0 位置。"""
        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi"},
        ]
        ctx = _make_ctx(messages=messages, arg="Be helpful.")

        result = _cmd_system(ctx)

        assert result is True
        # last_system_idx = -1, 所以插入在 0 位置
        assert len(ctx.messages) == 3
        assert ctx.messages[0] == {"role": "system", "content": "Be helpful."}

    def test_append_with_arg_after_last_system(self):
        """有 arg、有 system 消息时，追加在最后一条 system 消息之后。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(messages=messages, arg="Be concise.")

        result = _cmd_system(ctx)

        assert result is True
        assert len(ctx.messages) == 3
        assert ctx.messages[1] == {"role": "system", "content": "Be concise."}

    def test_append_with_arg_after_multiple_systems(self):
        """有 arg、有多条 system 消息时，追加在最后一条非摘要 system 之后。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "Be polite."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(messages=messages, arg="Be concise.")

        result = _cmd_system(ctx)

        assert result is True
        assert len(ctx.messages) == 4
        assert ctx.messages[2] == {"role": "system", "content": "Be concise."}

    def test_append_with_arg_skip_dialogue_summary(self):
        """有 arg、有 [对话摘要] 开头的 system 消息时，跳过摘要消息定位。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "system", "content": "[对话摘要] 前面讨论了..."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(messages=messages, arg="Be concise.")

        result = _cmd_system(ctx)

        assert result is True
        # last_system_idx 是 0（跳过摘要），追加到索引 1
        assert ctx.messages[1] == {"role": "system", "content": "Be concise."}

    def test_append_with_arg_only_summary_system(self):
        """有 arg、但仅有的 system 消息是 [对话摘要] 时，追加在摘要之后。"""
        messages = [
            {"role": "system", "content": "[对话摘要] 这是摘要"},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(messages=messages, arg="Be helpful.")

        result = _cmd_system(ctx)

        assert result is True
        # 只有摘要没有非摘要 system → 放在摘要之后（索引 1）
        assert ctx.messages[1] == {"role": "system", "content": "Be helpful."}

    def test_display_system_prompts_and_cancel(self):
        """无 arg 时显示系统提示词列表，用户回车跳过则取消。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(
            messages=messages,
            arg="",
            get_user_input=MagicMock(return_value=""),
        )

        result = _cmd_system(ctx)

        assert result is True
        assert len(ctx.messages) == 2  # 没有新增
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 已取消\x1b[0m',
            level="raw", source="cmd",
        )

    def test_display_system_prompts_and_add(self):
        """无 arg 时显示列表后用户输入内容，追加系统提示词。"""
        messages = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(
            messages=messages,
            arg="",
            get_user_input=MagicMock(return_value="Be polite."),
        )

        result = _cmd_system(ctx)

        assert result is True
        assert len(ctx.messages) == 3
        assert ctx.messages[1] == {"role": "system", "content": "Be polite."}

    def test_display_shows_content_and_char_count(self):
        """无 arg 时显示每条 system 消息的标签和字符数。"""
        messages = [
            {"role": "system", "content": "# Role\nYou are helpful."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(
            messages=messages,
            arg="",
            get_user_input=MagicMock(return_value=""),
        )

        _cmd_system(ctx)

        # 验证输出了字符数（"# Role\nYou are helpful." 共 23 个字符）
        found_char_count = any(
            '(23 字符)' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_char_count

        # 验证输出了标签（从 # Role 提取 "Role"）
        found_label = any(
            'Role' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_label

    def test_multiline_content_label_extraction(self):
        """标签从第一行非空行提取，去掉 # 前缀。"""
        messages = [
            {"role": "system", "content": "\n  \n  # Custom Prompt\nMore text here."},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(
            messages=messages,
            arg="",
            get_user_input=MagicMock(return_value=""),
        )

        _cmd_system(ctx)

        # 标签应为 "Custom Prompt"
        found_label = any(
            'Custom Prompt' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_label

    def test_empty_content_label_fallback(self):
        """system 消息 content 为空时标签显示 '第 N 段'。"""
        messages = [
            {"role": "system", "content": ""},
            {"role": "user", "content": "Hello"},
        ]
        ctx = _make_ctx(
            messages=messages,
            arg="",
            get_user_input=MagicMock(return_value=""),
        )

        _cmd_system(ctx)

        found_fallback = any(
            '第 0 段' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_fallback


# ═══════════════════════════════════════════════════════════════════════════
# 3. _cmd_cost
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdCost:
    """费用统计命令。"""

    def test_calls_show_cost_with_state_model(self):
        """调用 show_cost 并传入 ctx。"""
        state = {"model": "gpt-4o-mini"}
        ctx = _make_ctx(state=state)

        result = _cmd_cost(ctx)

        assert result is True
        _mock_cmd_core.show_cost.assert_called_once_with(ctx)

    def test_uses_default_model_when_state_missing(self):
        """state 中没有 model 时使用 config.MODEL 默认值。"""
        state = {}
        ctx = _make_ctx(state=state)

        result = _cmd_cost(ctx)

        assert result is True
        _mock_cmd_core.show_cost.assert_called_once_with(ctx)

    def test_returns_true(self):
        """始终返回 True。"""
        ctx = _make_ctx(state={})

        assert _cmd_cost(ctx) is True


# ═══════════════════════════════════════════════════════════════════════════
# 4. _cmd_theme
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdTheme:
    """主题切换命令。"""

    _THEMES = [
        ("dark", "深色主题"),
        ("light", "浅色主题"),
        ("high-contrast", "高对比度主题"),
    ]

    def test_switch_valid_theme(self):
        """有效主题名称时切换并保存配置。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="light")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_called_once_with("light")
        _mock_config.update_config.assert_called_once_with("theme", "light")
        # 验证输出切换确认消息
        mock_out.write.assert_any_call(
            '\x1b[32m  + 已切换到主题「light」(浅色主题)\x1b[0m',
            level="raw", source="cmd",
        )

    def test_switch_dark_theme(self):
        """切换到 dark 主题。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="dark")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_called_once_with("dark")
        _mock_config.update_config.assert_called_once_with("theme", "dark")

    def test_switch_high_contrast_theme(self):
        """切换到 high-contrast 主题。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="high-contrast")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_called_once_with("high-contrast")
        _mock_config.update_config.assert_called_once_with("theme", "high-contrast")

    def test_switch_unknown_theme(self):
        """未知主题名称时显示错误和可用主题列表。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="unknown_theme")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_not_called()
        _mock_config.update_config.assert_not_called()
        # 应输出错误信息
        mock_out.write.assert_any_call(
            '\x1b[33m  ! 未知主题: unknown_theme\x1b[0m',
            level="raw", source="cmd",
        )
        # 应输出可用主题列表行（包含逗号分隔的主题名）
        found_available = any(
            'dark, light, high-contrast' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_available

    def test_no_arg_shows_current_theme(self):
        """无参数时显示当前主题和可用主题列表。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        _mock_theme.get_active_theme.return_value = "dark"
        ctx = _make_ctx(arg="")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_not_called()
        _mock_config.update_config.assert_not_called()
        # 应输出当前主题
        mock_out.write.assert_any_call(
            '  \x1b[2m\u2502\x1b[0m 当前: \x1b[36mdark\x1b[0m',
            level="raw", source="cmd",
        )
        # 应输出使用说明
        mock_out.write.assert_any_call(
            '  \x1b[2m 使用: /theme <名称> 切换\x1b[0m',
            level="raw", source="cmd",
        )

    def test_no_arg_current_theme_marked(self):
        """无参数时当前主题在列表中带 <- 标记。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        _mock_theme.get_active_theme.return_value = "dark"
        ctx = _make_ctx(arg="")

        _cmd_theme(ctx)

        # 验证 dark 行包含 <-
        found_marker = any(
            '<-' in str(call_args)
            for call_args in mock_out.write.call_args_list
        )
        assert found_marker

    def test_switch_theme_with_trailing_spaces(self):
        """arg 带有前后空格时仍能匹配（arg.strip() 处理）。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="  light  ")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_called_once_with("light")
        _mock_config.update_config.assert_called_once_with("theme", "light")

    def test_switch_theme_case_sensitive(self):
        """主题名称大小写敏感：'Dark' 不同于 'dark'。"""
        _mock_theme.get_theme_names_with_desc.return_value = self._THEMES
        ctx = _make_ctx(arg="Dark")

        result = _cmd_theme(ctx)

        assert result is True
        _mock_theme.set_theme.assert_not_called()
        _mock_config.update_config.assert_not_called()


# ═══════════════════════════════════════════════════════════════════════════
# 5. 注册命令验证
# ═══════════════════════════════════════════════════════════════════════════

class TestCommandRegistration:
    """验证模块加载时注册了正确的命令。"""

    def test_all_commands_registered(self):
        """模块加载时调用了 register_command 注册所有四个命令。"""
        # 在模块加载时 register_command 被调用了 4 次
        assert _mock_cmd_core.register_command.call_count == 4

    def test_model_command_registered(self):
        """注册了 /model 命令。"""
        calls = _mock_cmd_core.register_command.call_args_list
        found = any(
            args[0] == '/model'
            for args, _ in calls
        )
        assert found

    def test_system_command_registered(self):
        """注册了 /system 命令。"""
        calls = _mock_cmd_core.register_command.call_args_list
        found = any(
            args[0] == '/system'
            for args, _ in calls
        )
        assert found

    def test_cost_command_registered(self):
        """注册了 /cost 命令。"""
        calls = _mock_cmd_core.register_command.call_args_list
        found = any(
            args[0] == '/cost'
            for args, _ in calls
        )
        assert found

    def test_theme_command_registered(self):
        """注册了 /theme 命令。"""
        calls = _mock_cmd_core.register_command.call_args_list
        found = any(
            args[0] == '/theme'
            for args, _ in calls
        )
        assert found

    def test_registration_has_handler_and_help(self):
        """每个注册项包含 handler 和 help_text。"""
        calls = _mock_cmd_core.register_command.call_args_list
        for args, _ in calls:
            assert len(args) == 3  # (name, handler, help_text)
            assert callable(args[1])    # handler 是可调用的
            assert isinstance(args[2], str)  # help_text 是字符串
