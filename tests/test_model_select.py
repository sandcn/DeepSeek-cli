"""模型选择界面独立模块 + 弹窗上下键无效果修复测试（2026-08-19）。

覆盖两项需求：

1. **代码独立**：/model 命令逻辑从 ``_config_cmd.py`` 独立到
   ``_model_cmd.py``——导出兼容（``commands.__init__`` / ``_config_cmd``
   re-export / ``model_plugin`` 导入路径）、序号/名称快速切换、
   PROVIDERS 聚合回退、provider 同步、无参数弹窗路径
   （``run_bottom_bar_selection`` 协议）。
2. **上下键无效果修复**：``ModelPlugin`` 无参数分支不再执行
   ``chat_ui.suspend()`` / ``monitor.stop()``——根因：键盘事件分发由
   render 线程渲染循环 INPUT 阶段驱动（``_phase_process_input``），
   suspend + stop 后弹窗显示但 SelectInput 收不到 ↑↓/Enter（60s 超时
   「已取消」）。修复后保持 render 线程 + cbreak 运行（对齐 editmsg），
   弹窗交互前 flush_stdin + clear_interrupted 清残留。
"""

from __future__ import annotations

import asyncio
import types

import pytest

from src.core.commands import _model_cmd
from src.core.commands import _config_cmd
from src.core.commands.plugins import model_plugin as model_plugin_mod
from src.core.commands.plugins.model_plugin import ModelPlugin


# ── 测试桩 ──────────────────────────────────────────────

class _FakeConfigPort:
    """ConfigPort 桩：固定模型列表/当前模型，记录 provider 写入。"""

    def __init__(self, models, model, provider="test"):
        self._models = list(models)
        self._model = model
        self.provider = provider
        self.set_calls: list[tuple[str, object]] = []

    def get_models(self):
        return list(self._models)

    def get_model(self):
        return self._model

    def get(self, key, default=None):
        if key == "provider":
            return self.provider
        return default

    def set(self, key, value):
        self.set_calls.append((key, value))


class _RecordingAdapter:
    """ui_adapter 桩：记录 run_bottom_bar_selection 调用参数，返回预置结果。"""

    def __init__(self, action="confirmed", index=0):
        self.calls: list[dict] = []
        self._result = {"action": action, "index": index}

    def run_bottom_bar_selection(self, items, display_items, initial_idx=0,
                                 title="选择", **kwargs):
        self.calls.append({
            "items": list(items),
            "display_items": list(display_items),
            "initial_idx": initial_idx,
            "title": title,
        })
        return dict(self._result)


class _Ctx(types.SimpleNamespace):
    """CommandContext 最小桩。"""

    def __init__(self, state, arg="", config_port=None, ui_adapter=None,
                 session=None):
        super().__init__(
            messages=[], state=state, arg=arg,
            build_system_prompt=lambda: "",
            get_user_input=lambda prompt="": "",
            context_manager=None, session=session,
            config_port=config_port, ui_adapter=ui_adapter,
        )


class _FakeMonitor:
    """EscapeMonitor 桩：记录 suspend/stop/start/clear_interrupted 调用。"""

    def __init__(self):
        self.stop_calls = 0
        self.start_calls = 0
        self.clear_interrupted_calls = 0

    def stop(self):
        self.stop_calls += 1

    def start(self, *_a, **_k):
        self.start_calls += 1

    def clear_interrupted(self):
        self.clear_interrupted_calls += 1


class _FakeInput:
    def __init__(self):
        self.flushed = 0

    def flush_stdin_buffer(self):
        self.flushed += 1


class _FakeBottomBar:
    def __init__(self):
        self.model_names: list[str] = []

    def set_model_name(self, name):
        self.model_names.append(name)


class _FakeChatUI:
    """ChatUIConsumer 桩：记录 suspend/resume，暴露 bottom_bar/get_input。"""

    def __init__(self, input_=None):
        self.suspend_calls = 0
        self.resume_calls = 0
        self.bottom_bar = _FakeBottomBar()
        self.input = input_ if input_ is not None else _FakeInput()

    def suspend(self):
        self.suspend_calls += 1

    def resume(self):
        self.resume_calls += 1

    def get_input(self):
        return self.input


class _FakeSession:
    def __init__(self, model="old-model"):
        self.messages = []
        self.model = model
        self.agent = types.SimpleNamespace(build_system_prompt=lambda: "")
        self.context_manager = None
        self._config_port = None


class _FakeLoop:
    def __init__(self, chat_ui, monitor):
        self._chat_ui = chat_ui
        self._monitor = monitor


_MODELS = ["deepseek-v4", "deepseek-v4-pro", "test-model"]


def _make_plugin(chat_ui, monitor):
    plugin = ModelPlugin()
    plugin.bind_loop(_FakeLoop(chat_ui, monitor))
    return plugin


def _flush_model_provider_writes(monkeypatch):
    """拦截 _model_cmd 内的 update_config 直写（无 config_port 路径）。"""
    writes: list[tuple[str, object]] = []

    class _FakeLoader(types.SimpleNamespace):
        pass

    fake_loader = _FakeLoader()
    fake_loader.get_rc = lambda: {"provider": "test"}
    fake_loader.update_config = lambda k, v: writes.append((k, v))
    import src.config.loader as loader_mod
    monkeypatch.setattr(loader_mod, "get_rc", fake_loader.get_rc)
    monkeypatch.setattr(loader_mod, "update_config", fake_loader.update_config)
    return writes


# ── 1. 代码独立：模块导出与兼容 re-export ────────────────

def test_model_cmd_module_exports():
    """_model_cmd 独立模块导出完整（命令函数 + provider 辅助）。"""
    assert callable(_model_cmd._cmd_model)
    assert callable(_model_cmd._infer_model_provider)
    assert callable(_model_cmd._collect_models)
    assert callable(_model_cmd._sync_provider)


def test_config_cmd_reexport_compatibility():
    """_config_cmd 保留 re-export：旧导入路径（_special_keys 等）不变。"""
    assert _config_cmd._cmd_model is _model_cmd._cmd_model
    assert _config_cmd._infer_model_provider is _model_cmd._infer_model_provider


def test_commands_pkg_exports_model_cmd():
    """commands 包 __init__ 从 _model_cmd 导入 _cmd_model。"""
    import src.core.commands as pkg
    assert pkg._cmd_model is _model_cmd._cmd_model


def test_infer_model_provider():
    """provider 推断：PROVIDERS 内命中返回名，自定义模型返回 None。"""
    from src.config.defaults import PROVIDERS
    first_provider = next(iter(PROVIDERS))
    first_model = PROVIDERS[first_provider].get("models", [""])[0]
    assert _model_cmd._infer_model_provider(first_model) == first_provider
    assert _model_cmd._infer_model_provider("totally-custom-model") is None


# ── 2. /model 有参数：直接切换（无弹窗） ─────────────────

def test_cmd_model_switch_by_index():
    """/model 2 序号切换：state 更新 + provider 同步（config_port 路径）。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0], provider="other")
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, arg="2", config_port=port)) is True
    assert state["model"] == _MODELS[1]


def test_cmd_model_switch_by_name():
    """/model test-model 名称切换：唯一模糊匹配生效。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0], provider="test")
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, arg="test-model", config_port=port)) is True
    assert state["model"] == "test-model"


def test_cmd_model_invalid_index():
    """/model 99 无效序号：不切换（错误提示不崩溃）。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0])
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, arg="99", config_port=port)) is True
    assert state["model"] == _MODELS[0]


def test_cmd_model_ambiguous_name():
    """/model deepseek 多匹配：提示歧义、不切换。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0])
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, arg="deepseek", config_port=port)) is True
    assert state["model"] == _MODELS[0]


def test_cmd_model_syncs_provider_via_rc(monkeypatch):
    """无 config_port 回退路径：provider 不一致时 update_config 写 RC。"""
    writes = _flush_model_provider_writes(monkeypatch)
    monkeypatch.setattr("src.config.MODELS", _MODELS)
    monkeypatch.setattr("src.config.MODEL", _MODELS[0])
    from src.config.defaults import PROVIDERS
    first_provider = next(iter(PROVIDERS))
    first_model = PROVIDERS[first_provider].get("models", [""])[0]
    state = {"model": first_model}
    _model_cmd._cmd_model(_Ctx(state, arg=first_model))
    assert ("provider", first_provider) in writes


def test_collect_models_providers_fallback(monkeypatch):
    """MODELS 为空时从 PROVIDERS 聚合回退（去重保序）。"""
    monkeypatch.setattr("src.config.MODELS", [])
    port = _FakeConfigPort([], _MODELS[0])
    models = _model_cmd._collect_models(_Ctx({}, config_port=port))
    assert models, "PROVIDERS 聚合回退应返回非空模型列表"
    assert len(models) == len(set(models)), "聚合应去重"


# ── 3. /model 无参数：弹窗交互选择协议 ───────────────────

def test_cmd_model_popup_confirmed_switch():
    """无参数弹窗确认：run_bottom_bar_selection 收到正确参数，切换生效。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0], provider="test")
    adapter = _RecordingAdapter(action="confirmed", index=2)
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, config_port=port, ui_adapter=adapter)) is True
    call = adapter.calls[0]
    assert call["items"] == _MODELS
    assert call["title"] == "模型选择"
    assert call["initial_idx"] == 0
    assert "<-当前" in call["display_items"][0]
    assert state["model"] == _MODELS[2]


def test_cmd_model_popup_initial_idx_current():
    """弹窗初始光标定位到当前模型。"""
    port = _FakeConfigPort(_MODELS, _MODELS[1])
    adapter = _RecordingAdapter(action="cancel", index=None)
    _model_cmd._cmd_model(_Ctx({"model": _MODELS[1]}, config_port=port, ui_adapter=adapter))
    assert adapter.calls[0]["initial_idx"] == 1


def test_cmd_model_popup_cancel_keeps_model():
    """弹窗取消：模型不变。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0])
    adapter = _RecordingAdapter(action="cancel", index=None)
    state = {"model": _MODELS[0]}
    _model_cmd._cmd_model(_Ctx(state, config_port=port, ui_adapter=adapter))
    assert state["model"] == _MODELS[0]


def test_cmd_model_popup_no_ui_adapter():
    """无 ui_adapter（无 TUI 上下文）：error 提示不崩溃，模型不变。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0])
    state = {"model": _MODELS[0]}
    assert _model_cmd._cmd_model(_Ctx(state, config_port=port, ui_adapter=None)) is True
    assert state["model"] == _MODELS[0]


def test_cmd_model_popup_same_model():
    """弹窗确认当前模型：提示「当前已是」，不重复切换。"""
    port = _FakeConfigPort(_MODELS, _MODELS[0])
    adapter = _RecordingAdapter(action="confirmed", index=0)
    state = {"model": _MODELS[0]}
    _model_cmd._cmd_model(_Ctx(state, config_port=port, ui_adapter=adapter))
    assert state["model"] == _MODELS[0]
    assert not port.set_calls


# ── 4. 上下键无效果修复：ModelPlugin 不 suspend/stop ─────

@pytest.mark.asyncio
async def test_plugin_popup_keeps_render_thread_alive(monkeypatch):
    """★ 核心修复断言：无参数弹窗分支不 suspend / 不 stop。

    根因：键盘分发由 render 线程 INPUT 阶段驱动——suspend + stop 后弹窗
    显示但 ↑↓/Enter 无效果（60s 超时）。修复后保持 render 线程 + cbreak。
    """
    chat_ui = _FakeChatUI()
    monitor = _FakeMonitor()
    plugin = _make_plugin(chat_ui, monitor)

    flush_calls: list = []

    def fake_flush_stdin(input_instance=None):
        flush_calls.append(input_instance)

    monkeypatch.setattr(
        "src.api.interrupt_async.flush_stdin", fake_flush_stdin,
    )
    # model_plugin 经 from ....api.interrupt_async import flush_stdin 延迟导入
    # （_prepare_selection_input 内函数体 import）——patch 源模块即可命中。

    async def fake_to_thread(fn, *args):
        # 模拟 _cmd_model 在工作线程执行：用户在弹窗中选择 test-model
        ctx = args[0]
        ctx.state["model"] = "test-model"
        return True

    monkeypatch.setattr(model_plugin_mod.asyncio, "to_thread", fake_to_thread)

    session = _FakeSession(model=_MODELS[0])
    state = {"model": _MODELS[0]}
    ctx = _Ctx(state, arg="", session=session)
    assert await plugin.async_execute(ctx) is True

    # ★ 不再暂停渲染线程 / 停止监听（上下键无效果的根因）
    assert chat_ui.suspend_calls == 0
    assert monitor.stop_calls == 0
    # 也无需恢复（从未停止）
    assert chat_ui.resume_calls == 0
    assert monitor.start_calls == 0
    # 入口清残留：flush_stdin 收到 chat_ui.get_input() 实例
    assert flush_calls and flush_calls[0] is chat_ui.input
    assert monitor.clear_interrupted_calls >= 1
    # 选择生效：session.model 同步 + 状态栏模型名刷新
    assert session.model == "test-model"
    assert chat_ui.bottom_bar.model_names == ["test-model"]


@pytest.mark.asyncio
async def test_plugin_with_args_direct_switch(monkeypatch):
    """有参数分支：直接切换（无弹窗交互，无需清输入残留）。"""
    chat_ui = _FakeChatUI()
    monitor = _FakeMonitor()
    plugin = _make_plugin(chat_ui, monitor)

    flush_calls: list = []

    def fake_flush_stdin(input_instance=None):
        flush_calls.append(input_instance)

    monkeypatch.setattr("src.api.interrupt_async.flush_stdin", fake_flush_stdin)

    async def fake_to_thread(fn, *args):
        ctx = args[0]
        ctx.state["model"] = "deepseek-v4"
        return True

    monkeypatch.setattr(model_plugin_mod.asyncio, "to_thread", fake_to_thread)

    session = _FakeSession(model="old")
    state = {"model": "old"}
    ctx = _Ctx(state, arg="deepseek-v4", session=session)
    assert await plugin.async_execute(ctx) is True
    assert session.model == "deepseek-v4"
    assert chat_ui.bottom_bar.model_names == ["deepseek-v4"]
    # 有参数分支无弹窗 → 无需 flush_stdin
    assert flush_calls == []


@pytest.mark.asyncio
async def test_plugin_no_model_change_no_bar_refresh(monkeypatch):
    """模型未变化（取消/确认当前）：不刷新底部栏。"""
    chat_ui = _FakeChatUI()
    monitor = _FakeMonitor()
    plugin = _make_plugin(chat_ui, monitor)

    async def fake_to_thread(fn, *args):
        return True  # 命令处理完成但模型未变（取消）

    monkeypatch.setattr(model_plugin_mod.asyncio, "to_thread", fake_to_thread)

    session = _FakeSession(model=_MODELS[0])
    state = {"model": _MODELS[0]}
    await plugin.async_execute(_Ctx(state, arg="", session=session))
    assert chat_ui.bottom_bar.model_names == []


@pytest.mark.asyncio
async def test_plugin_without_chat_ui(monkeypatch):
    """无 chat_ui（异常环境）：不崩溃、命令照常执行。"""
    monitor = _FakeMonitor()
    plugin = _make_plugin(None, monitor)

    async def fake_to_thread(fn, *args):
        ctx = args[0]
        ctx.state["model"] = "x-model"
        return True

    monkeypatch.setattr(model_plugin_mod.asyncio, "to_thread", fake_to_thread)

    session = _FakeSession(model="old")
    state = {"model": "old"}
    assert await plugin.async_execute(_Ctx(state, arg="", session=session)) is True
    assert session.model == "x-model"


@pytest.mark.asyncio
async def test_plugin_execute_sync_raises():
    """同步 execute 防误调用：抛 RuntimeError。"""
    plugin = ModelPlugin()
    with pytest.raises(RuntimeError):
        plugin.execute(_Ctx({}))


def test_plugin_registered():
    """ModelPlugin 模块级自注册：注册表可查 /model。"""
    from src.core.commands.base import get_plugin_registry
    reg = get_plugin_registry()
    assert reg.exists("model")


# ── 5. run_bottom_bar_selection 弹窗协议（独立模块联动） ──

def test_run_bottom_bar_selection_uses_user_select_popup(monkeypatch):
    """CommandUiAdapter 弹窗协议：设置 UserSelectState + bottom_view 轮询。"""
    from src.core.commands import _ui_adapter as uia
    from src.tui.app.model import AppModel

    model = AppModel()
    fake = types.SimpleNamespace(
        get_model=lambda: model, request_bottom_redraw=lambda: None,
    )
    monkeypatch.setattr(
        uia.CommandUiAdapter, "_get_active_chat_ui", lambda self: fake,
    )

    def fake_sleep(_sec):
        model.user_select.try_set_final("confirmed", ["deepseek-v4"])

    monkeypatch.setattr(uia.time, "sleep", fake_sleep)

    adapter = uia.CommandUiAdapter()
    result = adapter.run_bottom_bar_selection(
        items=_MODELS, display_items=_MODELS, initial_idx=1,
        title="模型选择", bottom_bar=None,
    )
    assert result == {"action": "confirmed", "index": 1}
    assert model.bottom_view == ""
    assert not model.user_select.visible
