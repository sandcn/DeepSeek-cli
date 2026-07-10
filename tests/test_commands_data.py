"""测试 src.core.commands._data_cmd：数据命令处理函数（/init, /load, /sessions）

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- 预先在 sys.modules 中 mock 所有外部依赖
- 模块内部有延迟导入（_cmd_init 中 from ..tools.utils import atomic_write_file，
  _cmd_load 中 from ..ui.tui._message_display import _display_messages），因此 mocks
  在测试执行期间保留在 sys.modules 中
- 使用 pytest fixture 管理共享的 mock 状态和测试隔离
- 每个测试类对应一个命令函数，每个测试方法覆盖一个场景

覆盖内容
--------
1. _cmd_init — 生成 init.md（文件不存在成功生成、已存在确认覆盖/取消、
   模型无返回错误、摘要过长截断、无项目文件、生成异常）
2. _cmd_load — 无参数提示用法、加载成功（含自动保存当前+清空沙盒）、
   会话不存在、会话无消息、最后一条 user 消息设 retry、最后一条 assistant
   消息提示继续、自动保存失败警戒
3. _cmd_sessions — 无会话时提示、有会话时显示列表（有标题/无标题）
"""

import sys
import os
import pytest
import importlib.util
from unittest.mock import MagicMock, patch, ANY, call

# ── 在导入被测试模块前 mock 所有外部依赖 ────────────────────────────────
_MODULE_PATH = '/home/simple/chat/src/core/commands/_data_cmd.py'

# 预创建输出端口 mock（被 _out 变量引用，后续可在 fixture 中重置）
_output_port_mock = MagicMock()

# 模拟所有外部模块，确保 module 级导入不触发真实依赖
_MOCK_MODULES = {
    'src': MagicMock(),
    'src.ui': MagicMock(),
    'src.ui.colors': MagicMock(
        GREEN='\x1b[32m', YELLOW='\x1b[33m', RED='\x1b[31m',
        DIM='\x1b[2m', RESET='\x1b[0m', TEAL='\x1b[36m', CYAN='\x1b[36m',
    ),
    'src.ui.ansi': MagicMock(
        GREEN='\x1b[32m', YELLOW='\x1b[33m', RED='\x1b[31m',
        DIM='\x1b[2m', RESET='\x1b[0m', TEAL='\x1b[36m', CYAN='\x1b[36m',
    ),
    'src.core': MagicMock(),
    'src.core.ports': MagicMock(),
    'src.core.ports.output': MagicMock(
        get_default_output_port=MagicMock(return_value=_output_port_mock),
    ),
    'src.config': MagicMock(MODEL='deepseek-v4-flash'),
    'src.api': MagicMock(),
    'src.api.model_async': MagicMock(),
    'src.prompt_builder': MagicMock(),
    'src.prompt_builder.project_summary': MagicMock(),
    'src.chat_msgs': MagicMock(),
    'src.core.sandbox_manager': MagicMock(),
    'src.core.internal._command_core': MagicMock(),
    'src.tools': MagicMock(),
    'src.tools.utils': MagicMock(),
    'src.ui.tui': MagicMock(),
    'src.ui.tui._message_display': MagicMock(),
    'src.ui.msg_list': MagicMock(),
}

_ORIGINAL_MODULES_DATA: dict[str, object] = {}
for mod_name, mod in _MOCK_MODULES.items():
    if mod_name not in _ORIGINAL_MODULES_DATA:
        _ORIGINAL_MODULES_DATA[mod_name] = sys.modules.get(mod_name)
    sys.modules[mod_name] = mod

# ── 直接加载 commands_data.py ──────────────────────────────────
_spec = importlib.util.spec_from_file_location(
    'src.core.commands._data_cmd', _MODULE_PATH,
)
_module = importlib.util.module_from_spec(_spec)
sys.modules['src.core.commands._data_cmd'] = _module
_spec.loader.exec_module(_module)

# ── 提取被测试符号 ──────────────────────────────────────────────
_cmd_init = _module._cmd_init
_cmd_load = _module._cmd_load
_cmd_sessions = _module._cmd_sessions


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════

def _deep_reset_mock(m):
    """深度重置单个 mock 及其所有子 mock 的 side_effect 和 return_value。

    Python 3.13 中 MagicMock.reset_mock() 不清理 children 的 _mock_side_effect
    （复现: parent.reset_mock() 后 child._mock_side_effect 仍保留），
    导致 side_effect 跨测试泄漏。此函数确保深度清理。
    """
    m.reset_mock()
    m._mock_side_effect = None
    # 递归清理所有子 mock
    for child_name in list(getattr(m, '_mock_children', {}).keys()):
        child = getattr(m, child_name, None)
        if isinstance(child, MagicMock):
            _deep_reset_mock(child)


@pytest.fixture(autouse=True)
def reset_mocks():
    """每个测试前后重置并恢复所有 mock，保证测试隔离"""
    # 测试前：重新注入 mock
    for mod_name, mod in _MOCK_MODULES.items():
        if mod_name not in _ORIGINAL_MODULES_DATA:
            _ORIGINAL_MODULES_DATA[mod_name] = sys.modules.get(mod_name)
        sys.modules[mod_name] = mod
    _output_port_mock.reset_mock()
    _output_port_mock._mock_side_effect = None
    for mod in _MOCK_MODULES.values():
        _deep_reset_mock(mod)
    yield
    # 测试后：恢复原始模块
    for mod_name in list(_MOCK_MODULES.keys()):
        orig = _ORIGINAL_MODULES_DATA.get(mod_name)
        if orig is not None:
            sys.modules[mod_name] = orig
        elif mod_name in sys.modules:
            del sys.modules[mod_name]


def _make_ctx(messages=None, state=None, arg='', get_user_input=None):
    """工厂方法：创建 CommandContext 的简易替代品"""
    ctx = MagicMock()
    ctx.messages = messages or []
    ctx.state = state or {}
    ctx.arg = arg
    ctx.get_user_input = get_user_input or (lambda prompt: '')
    ctx.persistence_port = None  # 无端口注入时回退到直接导入
    return ctx


def _mock_call_model_sync():
    """快捷：获取 call_model_sync mock"""
    return _MOCK_MODULES['src.api.model_async'].call_model_sync


def _mock_generate_summary_prompt():
    """快捷：获取 generate_summary_prompt mock"""
    return _MOCK_MODULES['src.prompt_builder.project_summary'].generate_summary_prompt


def _mock_atomic_write_file():
    """快捷：获取 atomic_write_file mock"""
    return _MOCK_MODULES['src.tools.utils'].atomic_write_file


def _mock_save_session():
    """快捷：获取 save_session mock"""
    return _MOCK_MODULES['src.chat_msgs'].save_session


def _mock_load_session():
    """快捷：获取 load_session mock"""
    return _MOCK_MODULES['src.chat_msgs'].load_session


def _mock_list_sessions():
    """快捷：获取 list_sessions mock"""
    return _MOCK_MODULES['src.chat_msgs'].list_sessions


def _mock_get_sandbox_manager():
    """快捷：获取 get_sandbox_manager mock"""
    return _MOCK_MODULES['src.core.sandbox_manager'].get_sandbox_manager


def _mock_display_messages():
    """快捷：获取 _display_messages mock"""
    return _MOCK_MODULES['src.ui.tui._message_display']._display_messages


# ═══════════════════════════════════════════════════════════════════════════
# 1. _cmd_init — 生成 init.md
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdInit:
    """_cmd_init：生成 init.md 项目摘要文件。"""

    def test_init_file_not_exists_success(self):
        """init.md 不存在时成功生成摘要。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = '项目摘要 prompt'
            _mock_call_model_sync().return_value = ('id-123', '这是项目摘要内容')
            ctx = _make_ctx(state={'model': 'gpt-4'})

            result = _cmd_init(ctx)

            assert result is True
            _mock_generate_summary_prompt().assert_called_once()
            _mock_call_model_sync().assert_called_once_with(
                [{'role': 'user', 'content': '项目摘要 prompt'}],
                model='gpt-4',
            )
            _mock_atomic_write_file().assert_called_once_with('init.md', '这是项目摘要内容')
            _output_port_mock.write.assert_any_call(
                '\x1b[32m  + 已生成 init.md\x1b[0m', level='raw', source='cmd',
            )

    def test_init_file_not_exists_default_model(self):
        """init.md 不存在时，state 中没有 model 则使用默认 MODEL。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', '内容')
            ctx = _make_ctx(state={})  # 无 model key

            result = _cmd_init(ctx)

            assert result is True
            _mock_call_model_sync().assert_called_once_with(
                [{'role': 'user', 'content': 'prompt'}],
                model='deepseek-v4-flash',  # 来自 mock MODEL
            )

    def test_init_file_exists_confirm_overwrite(self):
        """init.md 已存在，用户确认覆盖。"""
        with patch('os.path.exists', return_value=True):
            get_user_input = MagicMock(return_value='y')
            ctx = _make_ctx(get_user_input=get_user_input)
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', '新内容')

            result = _cmd_init(ctx)

            assert result is True
            get_user_input.assert_called_once()
            _mock_atomic_write_file().assert_called_once_with('init.md', '新内容')

    def test_init_file_exists_cancel(self):
        """init.md 已存在，用户取消（输入 n）。"""
        with patch('os.path.exists', return_value=True):
            ctx = _make_ctx(get_user_input=lambda prompt: 'n')

            result = _cmd_init(ctx)

            assert result is True
            _mock_call_model_sync().assert_not_called()
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[32m  + 已取消\x1b[0m', level='raw', source='cmd',
            )

    def test_init_file_exists_cancel_non_y(self):
        """init.md 已存在，用户输入非 'y' 的内容均取消。"""
        with patch('os.path.exists', return_value=True):
            for answer in ['', 'N', 'yes', ' ', 'cancel']:
                _output_port_mock.reset_mock()
                _mock_call_model_sync().reset_mock()
                _mock_generate_summary_prompt().reset_mock()
                ctx = _make_ctx(get_user_input=lambda prompt, a=answer: a)

                result = _cmd_init(ctx)

                assert result is True
                _mock_call_model_sync().assert_not_called()

    def test_init_model_returns_none(self):
        """模型返回 None 时报错。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = None
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 生成项目摘要失败（模型无返回）\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_model_returns_tuple_empty_content(self):
        """模型返回 (id, None) 时视为失败。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', None)
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 生成项目摘要失败（模型无返回）\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_model_returns_tuple_empty_string(self):
        """模型返回 (id, '') 时，空字符串为 falsy，视为失败。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', '')
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 生成项目摘要失败（模型无返回）\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_summary_truncated_too_long(self):
        """摘要过长（>50000字符）时截断。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            long_text = 'A' * 60000
            _mock_call_model_sync().return_value = ('id', long_text)
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            written_content = _mock_atomic_write_file().call_args[0][1]
            assert len(written_content) == 50000
            _output_port_mock.write.assert_any_call(
                '\x1b[33m  ! 摘要过长(60000字符)，已截断至50000字符\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_summary_exactly_50000_no_truncation(self):
        """摘要恰好 50000 字符时不截断。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            text = 'B' * 50000
            _mock_call_model_sync().return_value = text  # 纯字符串非 tuple
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            written_content = _mock_atomic_write_file().call_args[0][1]
            assert len(written_content) == 50000

    def test_init_summary_model_returns_plain_string(self):
        """模型返回纯字符串（非 tuple）也能正常处理。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = '纯文本摘要内容'
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_atomic_write_file().assert_called_once_with(
                'init.md', '纯文本摘要内容',
            )

    def test_init_no_project_files(self):
        """项目扫描未找到文件时 generate_summary_prompt 返回空字符串。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = ''
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_call_model_sync().assert_not_called()
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 未读取到项目文件，无法生成摘要\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_exception_during_generation(self):
        """生成过程中抛出异常应捕获并提示。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().side_effect = RuntimeError('API 超时')
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _mock_atomic_write_file().assert_not_called()
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 生成文件失败: API 超时\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_exception_during_atomic_write(self):
        """atomic_write_file 抛出异常应捕获并提示。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', '摘要内容')
            _mock_atomic_write_file().side_effect = PermissionError('权限不足')
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            _output_port_mock.write.assert_any_call(
                '\x1b[31m  x 生成文件失败: 权限不足\x1b[0m',
                level='raw', source='cmd',
            )

    def test_init_displays_tip_after_success(self):
        """成功生成后应输出项目摘要提示信息。"""
        with patch('os.path.exists', return_value=False):
            _mock_generate_summary_prompt().return_value = 'prompt'
            _mock_call_model_sync().return_value = ('id', '内容')
            ctx = _make_ctx()

            result = _cmd_init(ctx)

            assert result is True
            expected_tip = '  \x1b[2m项目摘要已由模型生成，包含项目名称、描述、技术栈、结构等信息。\x1b[0m'
            _output_port_mock.write.assert_any_call(
                expected_tip, level='raw', source='cmd',
            )


# ═══════════════════════════════════════════════════════════════════════════
# 2. _cmd_load — 加载保存的对话
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdLoad:
    """_cmd_load：加载保存的对话。"""

    def test_load_no_arg_shows_usage(self):
        """无参数时显示用法提示。"""
        ctx = _make_ctx(arg='')

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[33m  ! 用法: /load <会话ID>\x1b[0m',
            level='raw', source='cmd',
        )
        _output_port_mock.write.assert_any_call(
            ANY, level='raw', source='cmd',
        )
        _mock_save_session().assert_not_called()
        _mock_load_session().assert_not_called()

    def test_load_success_with_auto_save(self):
        """加载成功：有非 system 消息时自动保存当前会话并清空沙盒。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_save_session().return_value = 'saved-session-id-123456'
        _mock_load_session().return_value = {
            'id': 'target-session',
            'title': '测试会话',
            'model': 'gpt-4',
            'messages': [
                {'role': 'user', 'content': '你好'},
                {'role': 'assistant', 'content': '你好！'},
            ],
        }
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': '你是助手'},
                {'role': 'user', 'content': '旧对话'},
            ],
            state={'model': 'deepseek-v4-flash'},
            arg='target-session',
        )

        result = _cmd_load(ctx)

        assert result is True
        # 验证自动保存（只保存非 system 消息）
        _mock_save_session().assert_called_once_with(
            [{'role': 'user', 'content': '旧对话'}], model='deepseek-v4-flash',
        )
        # 验证沙盒清空
        sandbox_mock.clear.assert_called_once()
        # 验证加载
        _mock_load_session().assert_called_once_with('target-session')
        # 验证消息替换（保留 system 消息）
        assert len(ctx.messages) == 3
        assert ctx.messages[0]['role'] == 'system'
        assert ctx.messages[1]['role'] == 'user'
        assert ctx.messages[1]['content'] == '你好'
        # 验证状态
        assert ctx.state['model'] == 'gpt-4'
        # 验证输出
        _output_port_mock.write.assert_any_call(
            '\x1b[32m  + 已加载会话 「测试会话」 target-session (2 条消息, 模型: gpt-4)\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_only_system_no_auto_save(self):
        """当前会话只有 system 消息时不自动保存。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': '你是助手'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _mock_save_session().assert_not_called()
        sandbox_mock.clear.assert_called_once()

    def test_load_session_not_found(self):
        """会话不存在时提示未找到。"""
        _mock_load_session().return_value = None
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': '你是助手'}],
            arg='non-existent-id',
        )

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[33m  ! 未找到会话 \'non-existent-id\'\x1b[0m',
            level='raw', source='cmd',
        )
        assert len(ctx.messages) == 1

    def test_load_session_no_messages(self):
        """会话存在但没有消息时提示。"""
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[33m  ! 该会话没有消息\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_last_message_is_user_sets_retry(self):
        """最后一条消息是 user 角色时设置 ctx.state['retry'] = True。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'user', 'content': '请回答'}],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        assert ctx.state.get('retry') is True
        _output_port_mock.write.assert_any_call(
            '  \x1b[2m  最后一条是用户消息，将自动继续生成回复…\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_last_message_is_assistant(self):
        """最后一条消息是 assistant 时提示继续输入。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'assistant', 'content': '你好！'}],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        assert ctx.state.get('retry') is None
        _output_port_mock.write.assert_any_call(
            '  \x1b[2m  继续输入开始新的对话\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_last_message_is_tool(self):
        """最后一条消息是 tool 角色时提示继续输入。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'tool', 'content': '工具结果'}],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        assert ctx.state.get('retry') is None
        _output_port_mock.write.assert_any_call(
            '  \x1b[2m  继续输入开始新的对话\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_with_title_in_data(self):
        """会话数据中有 title 时显示标题信息。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'title': '重要对话',
            'model': 'claude',
            'messages': [{'role': 'user', 'content': '测试'}],
        }
        ctx = _make_ctx(arg='sid')

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[32m  + 已加载会话 「重要对话」 sid (1 条消息, 模型: claude)\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_without_title(self):
        """会话数据中没有 title 时只显示 ID。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'model': 'gpt-4',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        ctx = _make_ctx(arg='sid')

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[32m  + 已加载会话 sid (1 条消息, 模型: gpt-4)\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_auto_save_fails_shows_warning(self):
        """自动保存当前会话失败时显示警告信息。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_save_session().side_effect = RuntimeError('保存失败')
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': '旧消息'},
            ],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[33m  ! 自动保存当前会话失败: 保存失败\x1b[0m',
            level='raw', source='cmd',
        )
        # 即使保存失败，加载仍应继续
        _mock_load_session().assert_called_once_with('sid')

    def test_load_displays_loaded_messages(self):
        """加载成功后显示恢复的消息摘要。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        loaded_msgs = [
            {'role': 'user', 'content': '你好'},
            {'role': 'assistant', 'content': '你好！'},
        ]
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': loaded_msgs,
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _mock_display_messages().assert_called_once()
        call_args = _mock_display_messages().call_args
        non_system = [m for m in ctx.messages if m.get('role') != 'system']
        # call_args[0] 是位置参数元组，第一个元素是 data 列表
        assert len(call_args[0][0]) == len(non_system)
        assert call_args[1]['speed'] == 1000

    def test_load_with_tool_calls_in_messages(self):
        """消息中含有 tool_calls 字段也能正常加载。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [
                {'role': 'assistant', 'content': '', 'tool_calls': [
                    {'function': {'name': 'get_weather'}},
                ]},
            ],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _mock_display_messages().assert_called_once()
        _output_port_mock.write.assert_any_call(
            '  \x1b[2m  继续输入开始新的对话\x1b[0m',
            level='raw', source='cmd',
        )

    def test_load_no_sandbox_manager(self):
        """get_sandbox_manager 返回 None 时仍继续执行（不报错）。"""
        _mock_get_sandbox_manager().return_value = None
        _mock_load_session().return_value = {
            'id': 'sid',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'system'}],
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        _mock_load_session().assert_called_once_with('sid')
        assert len(ctx.messages) == 2

    def test_load_model_from_data_overrides_state(self):
        """会话数据中的 model 覆盖 state 中已有的 model。"""
        sandbox_mock = MagicMock()
        _mock_get_sandbox_manager().return_value = sandbox_mock
        _mock_load_session().return_value = {
            'id': 'sid',
            'model': 'claude-3',
            'messages': [{'role': 'user', 'content': 'hi'}],
        }
        ctx = _make_ctx(
            state={'model': 'deepseek-v4-flash'},
            arg='sid',
        )

        result = _cmd_load(ctx)

        assert result is True
        assert ctx.state['model'] == 'claude-3'


# ═══════════════════════════════════════════════════════════════════════════
# 3. _cmd_sessions — 列出所有保存的对话
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdSessions:
    """_cmd_sessions：列出所有保存的对话。"""

    def test_sessions_empty(self):
        """无保存的对话时提示。"""
        _mock_list_sessions().return_value = []
        ctx = _make_ctx()

        result = _cmd_sessions(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\x1b[33m  ! 没有保存的对话\x1b[0m',
            level='raw', source='cmd',
        )
        _mock_list_sessions().assert_called_once()

    def test_sessions_with_list(self):
        """有保存的对话时显示列表。"""
        _mock_list_sessions().return_value = [
            {
                'id': 'abc123def456',
                'title': '',
                'model': 'gpt-4',
                'message_count': 5,
                'saved_at': '2025-01-15T10:00:00',
            },
            {
                'id': 'xyz789',
                'title': '',
                'model': 'claude-3',
                'message_count': 3,
                'saved_at': '2025-01-14T09:00:00',
            },
        ]
        ctx = _make_ctx()

        result = _cmd_sessions(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '\n\x1b[2m  \u2500 已保存的对话\x1b[0m',
            level='raw', source='cmd',
        )
        _output_port_mock.write.assert_any_call(
            '  \x1b[36m  abc123def456\x1b[0m  \x1b[2mgpt-4  5条消息  2025-01-15T10:00:00\x1b[0m',
            source='cmd',
        )
        _output_port_mock.write.assert_any_call(
            '  \x1b[36m  xyz789\x1b[0m  \x1b[2mclaude-3  3条消息  2025-01-14T09:00:00\x1b[0m',
            source='cmd',
        )

    def test_sessions_with_titles(self):
        """会话有标题时显示 ID 截断+标题格式。"""
        _mock_list_sessions().return_value = [
            {
                'id': 'abc123def456',
                'title': '项目讨论',
                'model': 'gpt-4',
                'message_count': 10,
                'saved_at': '2025-01-15T10:00:00',
            },
        ]
        ctx = _make_ctx()

        result = _cmd_sessions(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '  \x1b[36m  abc123de\x1b[0m  \x1b[2m项目讨论\x1b[0m',
            source='cmd',
        )
        _output_port_mock.write.assert_any_call(
            '  \x1b[2m     gpt-4  10条  2025-01-15T10:00:00\x1b[0m',
            source='cmd',
        )

    def test_sessions_mixed_titles(self):
        """混合有标题和无标题的会话。"""
        _mock_list_sessions().return_value = [
            {
                'id': 'session-with-title-1',
                'title': '有标题',
                'model': 'gpt-4',
                'message_count': 2,
                'saved_at': '2025-01-16',
            },
            {
                'id': 'session-no-title-2',
                'title': '',
                'model': 'claude',
                'message_count': 1,
                'saved_at': '2025-01-15',
            },
        ]
        ctx = _make_ctx()

        result = _cmd_sessions(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call(
            '  \x1b[36m  session-\x1b[0m  \x1b[2m有标题\x1b[0m',
            source='cmd',
        )
        _output_port_mock.write.assert_any_call(
            '  \x1b[36m  session-no-title-2\x1b[0m  \x1b[2mclaude  1条消息  2025-01-15\x1b[0m',
            source='cmd',
        )

    def test_sessions_final_newline(self):
        """列表末尾输出空字符串作为换行。"""
        _mock_list_sessions().return_value = [
            {
                'id': 'sid1',
                'title': '',
                'model': 'gpt-4',
                'message_count': 1,
                'saved_at': '2025-01-15',
            },
        ]
        ctx = _make_ctx()

        result = _cmd_sessions(ctx)

        assert result is True
        _output_port_mock.write.assert_any_call('', level='raw', source='cmd')


# ── 清理 sys.modules 中的 mock，恢复原始模块 ───────────────
for _mod_name in list(_MOCK_MODULES.keys()):
    orig = _ORIGINAL_MODULES_DATA.get(_mod_name)
    if orig is not None:
        sys.modules[_mod_name] = orig
    elif _mod_name in sys.modules:
        del sys.modules[_mod_name]
