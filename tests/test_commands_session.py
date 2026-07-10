"""测试 src/core/commands_session.py — 会话命令处理函数

测试策略
--------
- 使用 importlib 直接加载模块文件，避免触发 src/__init__.py 的级联导入
- 在加载前 mock 所有外部依赖（colors/config/ports/context_selector/sandbox_manager/_command_core）
- 每个测试函数关注一个命令处理函数的一种行为
"""

import sys
import types
import pytest
import importlib.util
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════════════════════
#  Mock 模块工厂
# ═══════════════════════════════════════════════════════════════════════════

# 保存原始模块，用于测试结束后恢复
_ORIGINAL_MODULES_SESSION: dict[str, object] = {}


def _make_mock_module(name, is_package=False, **attrs):
    """创建 mock 模块并注册到 sys.modules（保存原始模块以便恢复）。

    is_package=True 时设置 __path__ 和 __package__，使 mock 被识别为包，
    确保相对导入（如 from ..ui.msg_list import ...）可正确解析。
    """
    if name not in _ORIGINAL_MODULES_SESSION:
        _ORIGINAL_MODULES_SESSION[name] = sys.modules.get(name)
    m = types.ModuleType(name)
    if is_package:
        m.__path__ = []
        m.__package__ = name
    for k, v in attrs.items():
        setattr(m, k, v)
    sys.modules[name] = m
    return m


# ═══════════════════════════════════════════════════════════════════════════
#  预置 mock 依赖（加载 commands_session.py 之前必须完成）
# ═══════════════════════════════════════════════════════════════════════════

# 父包占位（相对导入必需）—— is_package=True 确保相对导入正确解析
_make_mock_module('src', is_package=True)
_make_mock_module('src.core', is_package=True)
_make_mock_module('src.core.ports', is_package=True)
_make_mock_module('src.ui', is_package=True)
_make_mock_module('src.ui.msg_list',
    edit_current_messages=MagicMock(),
    _display_messages=MagicMock(),
)

# ── src.ui.colors ─────────────────────────────────────────
_make_mock_module('src.ui.colors',
    GREEN='\033[32m', YELLOW='\033[33m', RED='\033[31m',
    DIM='\033[2m', RESET='\033[0m', TEAL='\033[36m', CYAN='\033[36m',
)

# ── src.ui.diff_renderer ──────────────────────────────────
_make_mock_module('src.ui.diff_renderer',
    render_diff_to_ansi=lambda path, before, after: f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-diff",
)

# ── src.config ────────────────────────────────────────────
# 通过 __getattr__ 懒加载的配置包也可能导入，仅需暴露 MAX_CONTEXT_CHARS
_make_mock_module('src.config', MAX_CONTEXT_CHARS=60000, MODEL='deepseek-v4-flash')

# ── src.core.ports.output ─────────────────────────────────

class _MockOutputPort:
    """模拟输出端口，记录 write 调用"""
    def __init__(self):
        self.writes = []

    def write(self, text, level='info', source='core'):
        self.writes.append((text, level, source))

    def write_with_lock(self, text, level='info', source='core'):
        self.writes.append((text, level, source))

    def locked(self):
        from contextlib import nullcontext
        return nullcontext()


_mock_output_instance = _MockOutputPort()

_make_mock_module('src.core.ports.output',
    get_default_output_port=lambda: _mock_output_instance,
    OutputPort=type('OutputPort', (), {}),
    DefaultOutputAdapter=type('DefaultOutputAdapter', (), {}),
    set_default_output_port=lambda p: None,
    reset_default_output_port=lambda: None,
)

# ── src.core.context_selector ────────────────────────────
_make_mock_module('src.core.context_selector',
    total_chars=lambda msgs: 100,
)

# ── src.core.sandbox_manager ────────────────────────────

class _MockSandboxManager:
    """模拟沙盒管理器"""
    def __init__(self):
        self.cleared = False

    def clear(self):
        self.cleared = True

    def remap_indices(self, removed_indices):
        pass


_mock_sandbox = _MockSandboxManager()

_make_mock_module('src.core.sandbox_manager',
    get_sandbox_manager=lambda: _mock_sandbox,
    SandboxManager=_MockSandboxManager,
)

# ── src.core.internal._command_core ───────────────────────────────
# 提供 register_command、CommandContext、_pop_assistant_tool_messages
# 三个 commands_session.py 顶层导入的符号


class _MockCommandContext:
    """模拟 CommandContext，与真实实现保持相同 __slots__"""
    __slots__ = ('messages', 'state', 'arg', 'build_system_prompt',
                 'get_user_input', 'context_manager', 'edit_msg', 'session')

    def __init__(self, messages, state, arg='',
                 build_system_prompt=None, get_user_input=None,
                 context_manager=None):
        self.messages = messages
        self.state = state
        self.arg = arg
        self.build_system_prompt = build_system_prompt or (lambda: [])
        self.get_user_input = get_user_input or (lambda prompt='': '')
        self.context_manager = context_manager
        self.edit_msg = None
        self.session = None


def _mock_register_command(name, handler, help_text=''):
    """模拟注册命令（不实际注册）"""
    pass


def _mock_pop_assistant_tool_messages(messages):
    """模拟 _pop_assistant_tool_messages：从末尾移除 assistant/tool 消息"""
    removed = 0
    while messages and messages[-1].get('role') in ('assistant', 'tool'):
        messages.pop()
        removed += 1
    return removed


_make_mock_module('src.core.internal._command_core',
    register_command=_mock_register_command,
    CommandContext=_MockCommandContext,
    _pop_assistant_tool_messages=_mock_pop_assistant_tool_messages,
    handle_command=lambda *a, **kw: False,
    get_registered_command_names=lambda: [],
    COMMANDS_HELP='',
    _out=_MockOutputPort(),
)

# ── 延迟导入的模块（函数内 import），仅需存在于 sys.modules ──
# _cmd_clear 中: from .message_edit import clear_all_messages
# _cmd_editmsg 中: from ..core.message_edit import edit_current_messages
# _cmd_compress 中: from ..core.context_manager import ContextManager


def _mock_clear_all_messages(messages, build_system_prompt):
    """清空消息并重新构建 system prompt"""
    messages.clear()
    for part in build_system_prompt():
        messages.append({'role': 'system', 'content': part})


_make_mock_module('src.core.message_edit',
    clear_all_messages=_mock_clear_all_messages,
    edit_current_messages=lambda agent, state: None,
    truncate_messages=lambda msgs, keep: [],
)


class _MockContextManager:
    """模拟 ContextManager"""
    def __init__(self, messages, model, summarize_fn=None,
                 on_messages_changed=None, strategies=None):
        self.messages = messages
        self.model = model
        self.check_and_compress_called = False
        self.force_arg = None

    def check_and_compress(self, force=False):
        self.check_and_compress_called = True
        self.force_arg = force


_make_mock_module('src.core.context_manager',
    ContextManager=_MockContextManager,
)

# 确保 src.core 包可被 import（已有 mock 模块，但加载器需要 PackageLoader）
# 对于已经注册的纯 mock 模块，Python 可以正确解析其子模块的相对导入


# ═══════════════════════════════════════════════════════════════════════════
#  使用 importlib 直接加载 commands_session.py
# ═══════════════════════════════════════════════════════════════════════════

_SCRIPT_DIR = '/home/simple/chat/src/core'

_commands_session_spec = importlib.util.spec_from_file_location(
    'src.core.commands_session',
    f'{_SCRIPT_DIR}/commands_session.py',
)
_commands_session_module = importlib.util.module_from_spec(_commands_session_spec)
sys.modules['src.core.commands_session'] = _commands_session_module
_commands_session_spec.loader.exec_module(_commands_session_module)

# ── 提取所有被测试符号 ────────────────────────────────────
_cmd_clear = _commands_session_module._cmd_clear
_cmd_compress = _commands_session_module._cmd_compress
_cmd_pin = _commands_session_module._cmd_pin
_cmd_undo = _commands_session_module._cmd_undo
_cmd_retry = _commands_session_module._cmd_retry
_cmd_edit = _commands_session_module._cmd_edit
_cmd_editmsg = _commands_session_module._cmd_editmsg

# 测试中可访问的共享 mock 对象
shared_mocks = {
    'output': _mock_output_instance,
    'sandbox': _mock_sandbox,
}


def _make_ctx(messages=None, state=None, arg='',
              build_system_prompt=None, get_user_input=None,
              context_manager=None):
    """辅助：快速构建 Mock CommandContext"""
    return _MockCommandContext(
        messages=messages or [],
        state=state or {},
        arg=arg,
        build_system_prompt=build_system_prompt or (lambda: []),
        get_user_input=get_user_input or (lambda prompt='': ''),
        context_manager=context_manager,
    )


# ═══════════════════════════════════════════════════════════════════════════
#  Fixtures
# ═══════════════════════════════════════════════════════════════════════════

def _setup_mocks():
    """创建所有 mock 模块并注册到 sys.modules"""
    _make_mock_module('src', is_package=True)
    _make_mock_module('src.core', is_package=True)
    _make_mock_module('src.core.ports', is_package=True)
    _make_mock_module('src.ui', is_package=True)
    _make_mock_module('src.ui.msg_list',
        edit_current_messages=MagicMock(),
        _display_messages=MagicMock(),
    )
    _make_mock_module('src.ui.colors',
        GREEN='\033[32m', YELLOW='\033[33m', RED='\033[31m',
        DIM='\033[2m', RESET='\033[0m', TEAL='\033[36m', CYAN='\033[36m',
    )
    _make_mock_module('src.ui.diff_renderer',
        render_diff_to_ansi=lambda path, before, after: f"--- {path}\n+++ {path}\n@@ -1 +1 @@\n-diff",
    )
    _make_mock_module('src.config', MAX_CONTEXT_CHARS=60000, MODEL='deepseek-v4-flash')
    _make_mock_module('src.core.ports.output',
        get_default_output_port=lambda: _mock_output_instance,
        OutputPort=type('OutputPort', (), {}),
        DefaultOutputAdapter=type('DefaultOutputAdapter', (), {}),
        set_default_output_port=lambda p: None,
        reset_default_output_port=lambda: None,
    )
    _make_mock_module('src.core.context_selector', total_chars=lambda msgs: 100)
    _make_mock_module('src.core.sandbox_manager',
        get_sandbox_manager=lambda: _mock_sandbox,
        SandboxManager=_MockSandboxManager,
    )
    _make_mock_module('src.core.internal._command_core',
        register_command=_mock_register_command,
        CommandContext=_MockCommandContext,
        _pop_assistant_tool_messages=_mock_pop_assistant_tool_messages,
        handle_command=lambda *a, **kw: False,
        get_registered_command_names=lambda: [],
        COMMANDS_HELP='',
        _out=_MockOutputPort(),
    )
    _make_mock_module('src.core.message_edit',
        clear_all_messages=_mock_clear_all_messages,
        edit_current_messages=lambda agent, state: None,
        truncate_messages=lambda msgs, keep: [],
    )
    _make_mock_module('src.core.context_manager',
        ContextManager=_MockContextManager,
    )


@pytest.fixture(autouse=True)
def reset_mocks():
    """每个测试前重置 mock 状态，测试后恢复原始模块"""
    # 保存当前模块状态
    _saved_modules: dict[str, object] = {}
    for _name in _MOCKED_MODULE_NAMES:
        _saved_modules[_name] = sys.modules.get(_name)
    _setup_mocks()
    if 'src.core.commands_session' not in sys.modules:
        sys.modules['src.core.commands_session'] = _commands_session_module
    shared_mocks['output'].writes.clear()
    shared_mocks['sandbox'].cleared = False
    yield
    # 测试后恢复原始模块（防止 mock 污染后续测试）
    for _name in _MOCKED_MODULE_NAMES:
        _orig = _saved_modules.get(_name) or _ORIGINAL_MODULES_SESSION.get(_name)
        if _orig is not None:
            sys.modules[_name] = _orig
        elif _name in sys.modules:
            del sys.modules[_name]
    # 确保关键模块可被后续测试正常导入
    for _name in ('src.core.sandbox_manager', 'src.core.internal._command_core'):
        if _name not in sys.modules:
            try:
                import importlib
                importlib.import_module(_name)
            except ImportError:
                pass


@pytest.fixture
def basic_ctx():
    """返回一个包含两条消息（system + user）的基本上下文"""
    return _make_ctx(
        messages=[
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Hello'},
        ],
        state={'model': 'gpt-4o'},
    )


@pytest.fixture
def ctx_with_assistant():
    """包含 system + user + assistant 的上下文"""
    return _make_ctx(
        messages=[
            {'role': 'system', 'content': 'You are a helpful assistant.'},
            {'role': 'user', 'content': 'Hello'},
            {'role': 'assistant', 'content': 'Hi there!'},
        ],
        state={'model': 'gpt-4o'},
    )


@pytest.fixture
def cm():
    """Mock ContextManager 实例"""
    return _MockContextManager([], 'gpt-4o')


# ═══════════════════════════════════════════════════════════════════════════
#  1. _cmd_clear — 清空对话
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdClear:

    def test_clears_messages_and_sandbox(self, basic_ctx):
        """清空消息列表并清空沙盒"""
        result = _cmd_clear(basic_ctx)

        assert result is True
        # 消息清空后只剩 system 消息（保留用户追加的 system 内容）
        assert len(basic_ctx.messages) == 1
        assert basic_ctx.messages[0]["role"] == "system"
        # 沙盒被清空
        assert shared_mocks['sandbox'].cleared is True

    def test_output_written(self, basic_ctx):
        """输出清空提示"""
        _cmd_clear(basic_ctx)

        assert len(shared_mocks['output'].writes) >= 1
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '对话已清空' in written or 'clear' in written.lower()

    def test_returns_true(self, basic_ctx):
        """返回值始终为 True"""
        result = _cmd_clear(basic_ctx)
        assert result is True

    def test_empty_messages(self):
        """消息已为空时也能正常调用"""
        ctx = _make_ctx(messages=[])
        result = _cmd_clear(ctx)

        assert result is True
        assert shared_mocks['sandbox'].cleared is True


# ═══════════════════════════════════════════════════════════════════════════
#  2. _cmd_compress — 手动压缩上下文
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdCompress:

    def test_skip_when_too_few_non_system_messages(self):
        """非系统消息 ≤2 时跳过压缩"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'You are a helpful assistant.'},
                {'role': 'user', 'content': 'Hello'},
            ],
            state={'model': 'gpt-4o'},
        )
        result = _cmd_compress(ctx)

        assert result is True
        # 检查输出了跳过提示
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '无需压缩' in written or 'too few' in written.lower() or '太少' in written

    @staticmethod
    def _make_compressible_ctx():
        """创建非系统消息 >2 条的上下文，供压缩测试使用"""
        return _make_ctx(
            messages=[
                {'role': 'system', 'content': 'You are a helpful assistant.'},
                {'role': 'user', 'content': 'Q1'},
                {'role': 'assistant', 'content': 'A1'},
                {'role': 'user', 'content': 'Q2'},
                {'role': 'assistant', 'content': 'A2'},
                {'role': 'user', 'content': 'Q3'},
            ],
            state={'model': 'gpt-4o'},
        )

    def test_compress_with_context_manager(self):
        """有足够消息时调用 context_manager.check_and_compress"""
        ctx = self._make_compressible_ctx()
        cm = _MockContextManager(ctx.messages, 'gpt-4o')
        ctx.context_manager = cm

        result = _cmd_compress(ctx)

        assert result is True
        assert cm.check_and_compress_called is True
        assert cm.force_arg is True

    def test_compress_creates_context_manager_if_none(self, basic_ctx):
        """context_manager 为 None 时自动创建"""
        basic_ctx.messages.append({'role': 'assistant', 'content': 'Hi'})
        basic_ctx.messages.append({'role': 'user', 'content': 'How are you?'})
        basic_ctx.messages.append({'role': 'assistant', 'content': 'Fine'})

        # context_manager 为 None — 函数内部会创建新的 ContextManager
        result = _cmd_compress(basic_ctx)

        assert result is True

    def test_output_compression_info(self):
        """输出压缩状态信息"""
        ctx = self._make_compressible_ctx()
        cm = _MockContextManager(ctx.messages, 'gpt-4o')
        ctx.context_manager = cm

        _cmd_compress(ctx)

        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '消息数' in written

    def test_returns_true(self):
        """返回值始终为 True"""
        ctx = self._make_compressible_ctx()
        cm = _MockContextManager(ctx.messages, 'gpt-4o')
        ctx.context_manager = cm

        result = _cmd_compress(ctx)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
#  3. _cmd_pin — 标记重要消息
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdPin:

    def test_no_messages_to_pin(self):
        """消息 ≤1 条时提示没有可标记的消息"""
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'sys'}],
        )
        result = _cmd_pin(ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '没有可标记的消息' in written

    def test_pin_by_valid_index(self, basic_ctx):
        """按有效序号标记消息（切换 pinned 状态）"""
        basic_ctx.arg = '1'
        result = _cmd_pin(basic_ctx)

        assert result is True
        assert basic_ctx.messages[1].get('pinned') is True

        # 再次标记同一序号（取消标记）
        _cmd_pin(basic_ctx)
        assert basic_ctx.messages[1].get('pinned') is False

    def test_pin_by_index_invalid(self, basic_ctx):
        """无效序号提示错误信息"""
        basic_ctx.arg = '99'
        result = _cmd_pin(basic_ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '无效序号' in written

    def test_pin_index_zero_is_invalid(self, basic_ctx):
        """序号 0 无效（序号从 1 开始）"""
        basic_ctx.arg = '0'
        result = _cmd_pin(basic_ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '无效序号' in written

    def test_pin_last_round_no_arg(self, ctx_with_assistant):
        """无参数时标记最后一条 user 消息及之后的所有消息"""
        ctx_with_assistant.messages.append(
            {'role': 'user', 'content': 'Follow up'},
        )
        ctx_with_assistant.messages.append(
            {'role': 'assistant', 'content': 'Follow up reply'},
        )
        # arg 为空
        ctx_with_assistant.arg = ''

        result = _cmd_pin(ctx_with_assistant)

        assert result is True
        # 最后一条 user 消息的索引 = 2（0=system, 1=user, 2=assistant, 3=user, 4=assistant）
        last_user_idx = 3
        for i in range(last_user_idx, len(ctx_with_assistant.messages)):
            assert ctx_with_assistant.messages[i].get('pinned') is True

    def test_no_user_message_found(self):
        """没有 user 消息且消息数 >1 时提示找不到用户消息"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'assistant', 'content': 'reply'},
            ],
        )
        ctx.arg = ''
        result = _cmd_pin(ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '没有找到' in written

    def test_returns_true(self, basic_ctx):
        """返回值始终为 True"""
        basic_ctx.arg = '1'
        result = _cmd_pin(basic_ctx)
        assert result is True


# ═══════════════════════════════════════════════════════════════════════════
#  4. _cmd_undo — 撤销上一轮对话
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdUndo:

    def test_undo_removes_last_round(self, ctx_with_assistant):
        """撤销移除最后一组 assistant/tool + user 消息"""
        # 构造：system + user + assistant
        result = _cmd_undo(ctx_with_assistant)

        assert result is True
        # 应移除 assistant（1条）+ user（1条）= 2条
        # 或仅移除 assistant（如果 _pop_assistant_tool_messages 只移除 assistant）
        # 实际行为：_pop_assistant_tool_messages 移除末尾的 assistant（1条）
        # 然后如果末尾是 user，再移除 user（1条），总共 2条
        assert len(ctx_with_assistant.messages) == 1  # 只剩 system

    def test_undo_multi_round(self):
        """撤销多轮对话中的最后一轮"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'Q1'},
                {'role': 'assistant', 'content': 'A1'},
                {'role': 'user', 'content': 'Q2'},
                {'role': 'assistant', 'content': 'A2'},
            ],
        )
        result = _cmd_undo(ctx)

        assert result is True
        # 移除 assistant A2 + user Q2 = 2条
        assert len(ctx.messages) == 3
        assert ctx.messages[-1]['content'] == 'A1'

    def test_undo_only_assistant(self):
        """末尾只有 assistant 时移除 assistant"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'Q'},
                {'role': 'assistant', 'content': 'A'},
            ],
        )
        result = _cmd_undo(ctx)

        assert result is True
        # 移除 assistant（1条）+ user（1条）
        assert len(ctx.messages) == 1  # 只剩 system

    def test_undo_empty_no_error(self):
        """无消息可撤销时不会崩溃"""
        ctx = _make_ctx(messages=[])
        result = _cmd_undo(ctx)

        assert result is True
        assert len(ctx.messages) == 0

    def test_undo_output(self, ctx_with_assistant):
        """撤销后输出提示"""
        _cmd_undo(ctx_with_assistant)

        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '已撤销' in written

    def test_undo_sets_retry_when_user_remains(self):
        """撤销后如果最后一条是 user 消息，标记 retry_pending（I7）"""
        # 场景：连续两条 user 消息（第二条未得到回复），undo 移除末条 user 后
        # 最后一条仍是 user → 应标记 retry
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'Q1'},
                {'role': 'user', 'content': 'Q2'},
            ],
            state={'model': 'gpt-4o'},
        )
        result = _cmd_undo(ctx)

        assert result is True
        # 移除 user Q2，剩下 system + user Q1
        assert len(ctx.messages) == 2
        assert ctx.messages[-1]['role'] == 'user'
        assert ctx.state.get('retry') is True

    def test_undo_no_retry_when_last_is_assistant(self):
        """撤销后最后一条是 assistant，不标记 retry"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'Q1'},
                {'role': 'assistant', 'content': 'A1'},
                {'role': 'user', 'content': 'Q2'},
                {'role': 'assistant', 'content': 'A2'},
            ],
            state={'model': 'gpt-4o'},
        )
        result = _cmd_undo(ctx)

        assert result is True
        # 移除 assistant A2 + user Q2，剩下 system + user Q1 + assistant A1
        assert len(ctx.messages) == 3
        assert ctx.messages[-1]['role'] == 'assistant'
        assert ctx.state.get('retry') is not True


# ═══════════════════════════════════════════════════════════════════════════
#  5. _cmd_retry — 重新生成上一条回答
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdRetry:

    def test_retry_with_user_message(self, ctx_with_assistant):
        """有用户消息可重试时设置 retry 标志"""
        # system + user + assistant → _pop 删除 assistant → 剩 system + user
        # 然后检查末尾 role == 'user' → 设置 retry
        result = _cmd_retry(ctx_with_assistant)

        assert result is True
        assert ctx_with_assistant.state.get('retry') is True
        # assistant 被移除，保留 system + user
        assert len(ctx_with_assistant.messages) == 2

    def test_retry_no_messages(self):
        """无消息可重试时输出提示"""
        ctx = _make_ctx(messages=[], state={})
        result = _cmd_retry(ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '没有可重试' in written
        assert ctx.state.get('retry') is not True

    def test_retry_after_removal_no_user(self):
        """删除回答后没有用户消息（仅 system + assistant）"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'assistant', 'content': 'A1'},
            ],
        )
        result = _cmd_retry(ctx)

        assert result is True
        # _pop_assistant_tool_messages 移除 assistant → 只剩 system
        # 然后 messages[-1].role == 'system' 不是 'user'
        # 所以走 elif removed > 0 分支
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '未找到对应的用户消息' in written

    def test_retry_output_when_retrying(self, ctx_with_assistant):
        """开始重试时输出提示"""
        _cmd_retry(ctx_with_assistant)

        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '重新生成' in written


# ═══════════════════════════════════════════════════════════════════════════
#  6. _cmd_edit — 编辑最后一条用户消息
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdEdit:

    def test_edit_user_message(self, ctx_with_assistant):
        """编辑最后一条用户消息并设置 retry"""
        ctx_with_assistant.get_user_input = lambda prompt='': 'Updated message'

        result = _cmd_edit(ctx_with_assistant)

        assert result is True
        # 最后一条消息被替换为新内容
        assert ctx_with_assistant.messages[-1]['content'] == 'Updated message'
        assert ctx_with_assistant.state.get('retry') is True

    def test_no_user_message(self):
        """没有可编辑的用户消息时提示"""
        ctx = _make_ctx(
            messages=[{'role': 'system', 'content': 'sys'}],
        )
        result = _cmd_edit(ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '没有可编辑' in written

    def test_user_cancels_edit(self, basic_ctx):
        """用户取消编辑（返回空字符串）"""
        basic_ctx.get_user_input = lambda prompt='': ''

        result = _cmd_edit(basic_ctx)

        assert result is True
        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '已取消' in written
        # 消息不应被修改
        assert basic_ctx.messages[-1]['content'] == 'Hello'

    def test_edit_removes_subsequent_assistant(self):
        """编辑用户消息后清除其后的 assistant 消息"""
        ctx = _make_ctx(
            messages=[
                {'role': 'system', 'content': 'sys'},
                {'role': 'user', 'content': 'Original'},
                {'role': 'assistant', 'content': 'Reply'},
            ],
        )
        ctx.get_user_input = lambda prompt='': 'Edited'

        _cmd_edit(ctx)

        # assistant 回复被移除，只剩 system + edited user
        assert len(ctx.messages) == 2
        assert ctx.messages[-1]['content'] == 'Edited'

    def test_edit_output_shows_original(self, basic_ctx):
        """输出原内容和新内容"""
        basic_ctx.get_user_input = lambda prompt='': 'Updated'
        _cmd_edit(basic_ctx)

        written = ''.join(t for t, l, s in shared_mocks['output'].writes)
        assert '原内容' in written
        assert '已更新' in written


# ═══════════════════════════════════════════════════════════════════════════
#  7. _cmd_editmsg — 设置编辑信号
# ═══════════════════════════════════════════════════════════════════════════

class TestCmdEditMsg:

    def test_sets_edit_msg_signal(self, basic_ctx):
        """设置 edit_msg 联络信号"""
        result = _cmd_editmsg(basic_ctx)

        assert result is True
        assert basic_ctx.edit_msg is not None
        assert isinstance(basic_ctx.edit_msg, dict)
        assert 'handler' in basic_ctx.edit_msg
        assert basic_ctx.edit_msg['model'] == 'gpt-4o'
        assert basic_ctx.edit_msg['retry'] is False
        assert basic_ctx.edit_msg['prefill'] == ''

    def test_edit_msg_includes_handler(self, basic_ctx):
        """edit_msg 包含可调用的 handler"""
        _cmd_editmsg(basic_ctx)

        assert callable(basic_ctx.edit_msg['handler'])

    def test_edit_msg_model_from_state(self, basic_ctx):
        """model 从 state 中读取"""
        basic_ctx.state['model'] = 'gpt-4-turbo'
        _cmd_editmsg(basic_ctx)

        assert basic_ctx.edit_msg['model'] == 'gpt-4-turbo'

    def test_edit_msg_model_default_empty(self):
        """state 中无 model 时默认为空字符串"""
        ctx = _make_ctx(messages=[], state={})
        _cmd_editmsg(ctx)

        assert ctx.edit_msg['model'] == ''

    def test_edit_msg_with_retry_flag(self):
        """state 中的 retry 标志传递到 edit_msg"""
        ctx = _make_ctx(messages=[], state={'retry': True})
        _cmd_editmsg(ctx)

        assert ctx.edit_msg['retry'] is True

    def test_edit_msg_with_prefill(self):
        """state 中的 prefill 传递到 edit_msg"""
        ctx = _make_ctx(messages=[], state={'prefill': 'some prefill text'})
        _cmd_editmsg(ctx)

        assert ctx.edit_msg['prefill'] == 'some prefill text'


# ═══════════════════════════════════════════════════════════════════════════
#  8. 集成测试：注册表
# ═══════════════════════════════════════════════════════════════════════════

class TestModuleRegistration:

    def test_module_loaded_successfully(self):
        """模块成功加载"""
        assert _commands_session_module is not None
        assert hasattr(_commands_session_module, '_cmd_clear')
        assert hasattr(_commands_session_module, '_cmd_compress')
        assert hasattr(_commands_session_module, '_cmd_pin')
        assert hasattr(_commands_session_module, '_cmd_undo')
        assert hasattr(_commands_session_module, '_cmd_retry')
        assert hasattr(_commands_session_module, '_cmd_edit')
        assert hasattr(_commands_session_module, '_cmd_editmsg')

    def test_all_functions_return_true(self, basic_ctx):
        """所有命令函数返回 True"""
        basic_ctx.arg = '1'
        basic_ctx.get_user_input = lambda p: 'test'
        cm = _MockContextManager(basic_ctx.messages, 'gpt-4o')
        basic_ctx.context_manager = cm
        basic_ctx.messages.append({'role': 'assistant', 'content': 'reply'})

        assert _cmd_clear(_make_ctx(messages=[{'role': 'user', 'content': 'hi'}])) is True
        assert _cmd_compress(basic_ctx) is True
        assert _cmd_pin(basic_ctx) is True
        assert _cmd_undo(basic_ctx) is True
        assert _cmd_retry(basic_ctx) is True
        assert _cmd_edit(basic_ctx) is True
        assert _cmd_editmsg(basic_ctx) is True


# ── 清理 sys.modules 中的 mock，恢复原始模块 ───────────────
# 移除被 mock 污染的模块条目，恢复原始模块避免影响其他测试文件的导入
_MOCKED_MODULE_NAMES = [
    'src', 'src.core', 'src.core.ports', 'src.ui',
    'src.ui.msg_list', 'src.ui.colors', 'src.ui.diff_renderer',
    'src.config',
    'src.core.ports.output', 'src.core.context_selector',
    'src.core.sandbox_manager', 'src.core.internal._command_core',
    'src.core.message_edit', 'src.core.context_manager',
    'src.core.commands_session',
]
for _mod_name in _MOCKED_MODULE_NAMES:
    orig = _ORIGINAL_MODULES_SESSION.get(_mod_name)
    if orig is not None:
        sys.modules[_mod_name] = orig
    else:
        sys.modules.pop(_mod_name, None)
