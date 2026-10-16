"""启动模型配置引导测试（未配置模型档案 → 提示并打开模型选择器）。

覆盖 ``InteractiveLoop._maybe_prompt_model_setup``：
  - 未配置任何模型档案 → 显示「三步配置」引导 + 打开模型选择器；
  - 选择器内应用了档案 → 同步 ``state.model`` / ``session.model`` / 状态栏；
  - 已配置档案 → 不提示、不打开；
  - 检查异常（导入/构建失败）→ 静默不中断启动。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.app_loop._loop import InteractiveLoop
from src.app_loop._session_setup import SessionState


class _FakeChatUI:
    def __init__(self):
        self.lines: list = []
        self.notifications: list = []
        self.model_names: list = []
        self.bottom_bar = self
        self.input = None

    def write_line(self, text):
        self.lines.append(text)

    def on_notification(self, text):
        self.notifications.append(text)

    def set_model_name(self, name):
        self.model_names.append(name)

    def wait_for_user_input(self, *args, **kwargs):
        return ""


class _FakeSession:
    def __init__(self, model="old-model"):
        self.messages: list = []
        self.model = model
        self.agent = SimpleNamespace(build_system_prompt=lambda: "")
        self.context_manager = SimpleNamespace()


@pytest.fixture
def loop_env(monkeypatch):
    loop = InteractiveLoop()
    chat_ui = _FakeChatUI()
    loop._chat_ui = chat_ui
    loop._monitor = None
    session = _FakeSession()
    state = SessionState(model="old-model")
    return loop, chat_ui, session, state


def test_prompt_and_open_when_no_profiles(loop_env, monkeypatch):
    loop, chat_ui, session, state = loop_env
    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries", lambda rc=None: [],
    )
    opened: list = []

    def _fake_open(ctx):
        opened.append(ctx)
        ctx.state["model"] = "new-model"
        return True

    monkeypatch.setattr("src.core.commands._model_cmd._open_model_view", _fake_open)
    loop._maybe_prompt_model_setup(session, state)

    assert opened, "未配置模型时应打开模型选择器"
    # 引导提示（消息区优先，回退通知）
    guide_text = "".join(chat_ui.lines + chat_ui.notifications)
    assert "尚未配置模型" in guide_text and "/models" in guide_text
    # 应用结果同步到会话状态 / 状态栏
    assert state.model == "new-model"
    assert session.model == "new-model"
    assert chat_ui.model_names == ["new-model"]


def test_no_prompt_when_profiles_exist(loop_env, monkeypatch):
    loop, chat_ui, session, state = loop_env
    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries",
        lambda rc=None: [{"model": "configured"}],
    )
    called: list = []
    monkeypatch.setattr(
        "src.core.commands._model_cmd._open_model_view",
        lambda ctx: called.append(ctx) or True,
    )
    loop._maybe_prompt_model_setup(session, state)
    assert not called
    assert chat_ui.lines == [] and chat_ui.notifications == []
    assert state.model == "old-model"


def test_import_failure_is_silent(loop_env, monkeypatch):
    loop, chat_ui, session, state = loop_env

    def _boom(rc=None):
        raise RuntimeError("config broken")

    monkeypatch.setattr("src.config.model_profiles.build_model_entries", _boom)
    loop._maybe_prompt_model_setup(session, state)  # 不抛异常
    assert chat_ui.lines == []
    assert state.model == "old-model"


def test_open_view_failure_is_silent(loop_env, monkeypatch):
    loop, chat_ui, session, state = loop_env
    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries", lambda rc=None: [],
    )

    def _boom(ctx):
        raise RuntimeError("view failed")

    monkeypatch.setattr("src.core.commands._model_cmd._open_model_view", _boom)
    loop._maybe_prompt_model_setup(session, state)  # 不抛异常
    assert state.model == "old-model"


def test_notification_fallback_when_no_write_line(loop_env, monkeypatch):
    loop, chat_ui, session, state = loop_env
    monkeypatch.delattr(_FakeChatUI, "write_line", raising=False)  # 仅剩通知通道
    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries", lambda rc=None: [],
    )
    monkeypatch.setattr(
        "src.core.commands._model_cmd._open_model_view", lambda ctx: True,
    )
    loop._maybe_prompt_model_setup(session, state)
    assert chat_ui.notifications and "尚未配置模型" in chat_ui.notifications[-1]


# ── 单次模式（-p）未配置模型档案提示 ─────────────────────

def test_single_mode_notice_when_no_profiles(monkeypatch):
    from src.app_loop._single import _missing_model_profile_notice

    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries", lambda rc=None: [],
    )
    notice = _missing_model_profile_notice()
    assert notice and "未配置模型档案" in notice


def test_single_mode_no_notice_when_configured(monkeypatch):
    from src.app_loop._single import _missing_model_profile_notice

    monkeypatch.setattr(
        "src.config.model_profiles.build_model_entries",
        lambda rc=None: [{"model": "configured"}],
    )
    assert _missing_model_profile_notice() is None


def test_single_mode_notice_silent_on_error(monkeypatch):
    from src.app_loop._single import _missing_model_profile_notice

    def _boom(rc=None):
        raise RuntimeError("config broken")

    monkeypatch.setattr("src.config.model_profiles.build_model_entries", _boom)
    assert _missing_model_profile_notice() is None
