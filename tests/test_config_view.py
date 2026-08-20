"""config 命令 / 配置中心视图测试（2026-08-20 用户需求：config 命令独立界面）。

覆盖：
  1. ``view_model`` 纯逻辑：build_config_entries / format_config_value /
     parse_config_value / resolve_config_key / format_config_text；
  2. ``ConfigViewState`` 跨线程终态协议（try_set_final first-write-wins）；
  3. ``_cmd_config`` 命令各分支（无参回退文本 / show / get / set / reset /
     未知子命令）+ ``_open_config_ui`` 打开/清理协议；
  4. ``ConfigView`` 组件渲染与交互（浏览导航 / Enter 编辑 / char 累积 /
     Enter 确认写回 / Esc 取消 / Esc 关闭 / 编辑错误显示）；
  5. ``reset_display`` 重置 config_view 保留 seq。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tui.ink.fiber import InputHook


# ── 测试辅助 ──────────────────────────────────────────────

class _Recorder:
    """命令输出捕获（替换 _config_cmd._out）。"""

    def __init__(self):
        self.calls: list[str] = []

    def write(self, text, level="info", source="cmd"):
        self.calls.append(text)


class _FakeChatUI:
    """最小 ChatUIConsumer 桩（get_model / request_bottom_redraw 协议）。"""

    def __init__(self, model):
        self._model = model
        self.bottom_bar = SimpleNamespace(is_completion_visible=False)

    def get_model(self):
        return self._model

    def get_input_component(self):
        return SimpleNamespace(flush_stdin_buffer=lambda: None)

    def request_bottom_redraw(self):
        pass

    def flush_input_router(self, _sec):
        pass


@pytest.fixture
def isolated_rc(monkeypatch, tmp_path):
    """隔离 RC 配置文件（临时目录）——命令/组件写回不污染真实用户配置。

    loader.py 模块级导入 defaults 的 RC_FILE/CONFIG_DIR 引用（绑定到 loader
    命名空间），view_model/_config_cmd 的 format_config_text 在函数体内经
    defaults 取 RC_FILE——两处均需替换；_RC_LOADED 置 False 强制重读。
    """
    from src.config import loader as cfg_loader
    from src.config import defaults as cfg_defaults
    rc_file = tmp_path / "chatrc.json"
    monkeypatch.setattr(cfg_loader, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_loader, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(cfg_loader, "LOG_FILE", tmp_path / "audit.log")
    monkeypatch.setattr(cfg_loader, "_RC_LOADED", False)
    monkeypatch.setattr(cfg_loader, "_RC", None)
    monkeypatch.setattr(cfg_defaults, "RC_FILE", rc_file)
    monkeypatch.setattr(cfg_defaults, "CONFIG_DIR", tmp_path)
    return rc_file


def _make_ctx(arg: str, ui_adapter=None):
    """构造最小 CommandContext（config 命令需要）。"""
    from src.core.internal.commands._command_core import CommandContext
    return CommandContext(
        messages=[], state={}, arg=arg,
        build_system_prompt=None, get_user_input=None,
        context_manager=None, session=None,
        config_port=None, ui_adapter=ui_adapter,
    )


def _render_component(component, model, width=80, fiber=None):
    """在手动 fiber 上下文渲染组件（返回 fiber + 元素树）。

    与 test_user_select_seq.py 同模式：手动 fiber 渲染父组件（子组件
    ListView 不递归渲染——元素树经 h() 构造，props 可直接访问回调）。
    """
    from src.tui.ink import hooks
    from src.tui.ink.fiber import Fiber, TAG_FUNCTION
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, {"model": model, "width": width})
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = component({"model": model, "width": width})
    finally:
        hooks._pop_current()
    return fiber, el


def _find_input_handler(fiber):
    """查找 fiber 上注册的活跃 use_input handler（ConfigView._handle）。"""
    if fiber is None:
        return None
    for hook in getattr(fiber, "hooks", None) or []:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _ev(kind: str, char: str = ""):
    return SimpleNamespace(kind=kind, char=char, modifier=0, keycode=0, raw=b"")


def _sample_entries():
    """构造最小配置项列表（选择界面：MODEL/bool；输入界面：api_key/temperature）。

    字段与 ``view_model.build_config_entries`` 产出结构一致（含 edit_kind）。
    """
    return [
        {
            "key": "MODEL", "path": "model", "type": str,
            "value": "deepseek-v4-flash", "value_text": "deepseek-v4-flash",
            "default_text": "deepseek-v4-flash", "desc": "当前模型", "sensitive": False,
            "options": [("deepseek-v4-pro", ""), ("deepseek-v4-flash", "")],
            "edit_kind": "select",
        },
        {
            "key": "ENABLE_NOTIFICATIONS", "path": "enable_notifications",
            "type": bool, "value": True, "value_text": "true",
            "default_text": "true", "desc": "启用系统通知", "sensitive": False,
            "options": [("true", "开启"), ("false", "关闭")],
            "edit_kind": "select",
        },
        {
            "key": "TEMPERATURE", "path": "temperature", "type": float,
            "value": 0.2, "value_text": "0.2",
            "default_text": "0.2", "desc": "大模型温度", "sensitive": False,
            "options": None, "edit_kind": "input",
        },
        {
            "key": "api_key", "path": "api_key", "type": str,
            "value": "sk-test123456", "value_text": "sk-...3456",
            "default_text": "", "desc": "API Key", "sensitive": True,
            "options": None, "edit_kind": "input",
        },
    ]


# ═══════════════════════════════════════════════════════════
# 1. view_model 纯逻辑
# ═══════════════════════════════════════════════════════════

class TestViewModel:

    def test_build_config_entries_structure(self, isolated_rc):
        from src.config.view_model import build_config_entries
        entries = build_config_entries()
        # CONFIG_KEYS(26) + 额外键(4) = 30
        assert len(entries) == 30
        keys = [e["key"] for e in entries]
        assert "MODEL" in keys and "HTTP_CONNECT_TIMEOUT" in keys
        assert "provider" in keys and "api_key" in keys
        # 每条目字段齐全
        for e in entries:
            assert {"key", "path", "type", "value", "value_text",
                    "default_text", "desc", "sensitive", "options"} <= set(e.keys())
        # api_key 敏感
        api = next(e for e in entries if e["key"] == "api_key")
        assert api["sensitive"] is True
        # 嵌套路径显示
        http = next(e for e in entries if e["key"] == "HTTP_CONNECT_TIMEOUT")
        assert http["path"] == "performance.http_client.connect_timeout"

    def test_build_config_entries_options(self, isolated_rc):
        """按类型提供编辑候选选项：枚举/布尔/模型有选择界面，数值/文本无。"""
        from src.config.view_model import build_config_entries
        entries = {e["key"]: e for e in build_config_entries()}
        # 枚举（provider/theme/reasoning_effort）→ 选择界面
        assert [o[0] for o in entries["provider"]["options"]] == [
            "deepseek", "custom", "anthropic", "glm", "mimo",
        ]
        assert [o[0] for o in entries["THEME"]["options"]] == [
            "dark", "light", "high-contrast",
        ]
        assert [o[0] for o in entries["REASONING_EFFORT"]["options"]] == [
            "low", "medium", "high", "max",
        ]
        # bool → true/false 选择界面
        assert [o[0] for o in entries["ENABLE_NOTIFICATIONS"]["options"]] == ["true", "false"]
        assert [o[0] for o in entries["HTTP_ENABLE_POOL"]["options"]] == ["true", "false"]
        # MODEL → 当前可用模型列表（选择界面）
        model_opts = [o[0] for o in entries["MODEL"]["options"] or []]
        assert "deepseek-v4-flash" in model_opts
        assert "deepseek-v4-pro" in model_opts
        # 数值/字符串 → 输入界面（options None）
        assert entries["TEMPERATURE"]["options"] is None
        assert entries["api_key"]["options"] is None

    def test_build_config_entries_edit_kind(self, isolated_rc):
        """编辑界面类型：select=枚举/布尔/模型；json=有子 JSON 的 list/dict；
        input=数值/字符串。"""
        from src.config.view_model import build_config_entries
        entries = {e["key"]: e for e in build_config_entries()}
        # select：枚举/布尔/模型
        assert entries["provider"]["edit_kind"] == "select"
        assert entries["THEME"]["edit_kind"] == "select"
        assert entries["REASONING_EFFORT"]["edit_kind"] == "select"
        assert entries["MODEL"]["edit_kind"] == "select"
        assert entries["ENABLE_NOTIFICATIONS"]["edit_kind"] == "select"
        # json：list/dict 有子结构的配置项
        assert entries["MODELS"]["edit_kind"] == "json"
        assert entries["TOKEN_PRICES"]["edit_kind"] == "json"
        assert entries["skills"]["edit_kind"] == "json"
        # input：数值/字符串
        assert entries["TEMPERATURE"]["edit_kind"] == "input"
        assert entries["MAX_RETRIES"]["edit_kind"] == "input"
        assert entries["api_key"]["edit_kind"] == "input"

    def test_format_config_value_bool(self):
        from src.config.view_model import format_config_value
        assert format_config_value(True, bool) == "true"
        assert format_config_value(False, bool) == "false"

    def test_format_config_value_list_dict(self):
        from src.config.view_model import format_config_value
        assert format_config_value(["a", "b"], list) == '["a", "b"]'
        assert format_config_value({"k": 1}, dict) == '{"k": 1}'

    def test_format_config_value_sensitive(self):
        from src.config.view_model import format_config_value
        assert format_config_value("sk-abcdefghijkl", str, sensitive=True) == "sk-...ijkl"
        assert format_config_value("", str, sensitive=True) == "(空)"

    def test_format_config_value_truncate(self):
        from src.config.view_model import format_config_value
        long_str = "x" * 100
        out = format_config_value(long_str, str, max_len=10)
        assert len(out) <= 10
        assert out.endswith("\u2026")

    def test_parse_config_value_types(self):
        from src.config.view_model import parse_config_value
        assert parse_config_value(bool, "true") == (True, "")
        assert parse_config_value(bool, "0") == (False, "")
        assert parse_config_value(int, "42") == (42, "")
        assert parse_config_value(float, "0.7") == (0.7, "")
        assert parse_config_value(list, '["a"]') == (["a"], "")
        assert parse_config_value(dict, '{"k": 1}') == ({"k": 1}, "")
        assert parse_config_value(str, "hello") == ("hello", "")
        # 空 list/dict 回退空容器
        assert parse_config_value(list, "") == ([], "")

    def test_parse_config_value_errors(self):
        from src.config.view_model import parse_config_value
        val, err = parse_config_value(bool, "maybe")
        assert val is None and "布尔" in err
        val, err = parse_config_value(int, "abc")
        assert val is None and "整数" in err
        val, err = parse_config_value(float, "x")
        assert val is None and "数字" in err
        val, err = parse_config_value(list, "not json")
        assert val is None and "JSON" in err
        val, err = parse_config_value(dict, "[1, 2]")
        assert val is None and "JSON 类型应为 dict" in err

    def test_resolve_config_key(self):
        from src.config.view_model import resolve_config_key
        assert resolve_config_key("MODEL") == "MODEL"
        assert resolve_config_key("model") == "MODEL"
        assert resolve_config_key("connect_timeout") == "HTTP_CONNECT_TIMEOUT"
        assert resolve_config_key("performance.http_client.connect_timeout") == "HTTP_CONNECT_TIMEOUT"
        assert resolve_config_key("api_key") == "api_key"
        assert resolve_config_key("API_KEY") == "api_key"
        assert resolve_config_key("nope") is None
        assert resolve_config_key("") is None

    def test_format_config_text(self, isolated_rc):
        from src.config.view_model import format_config_text, build_config_entries
        text = format_config_text(build_config_entries())
        assert "配置中心" in text
        assert "model" in text
        assert "api_key" in text  # 敏感项仍显示键名（值脱敏）


# ═══════════════════════════════════════════════════════════
# 2. ConfigViewState 终态协议
# ═══════════════════════════════════════════════════════════

class TestConfigViewState:

    def test_try_set_final_first_write_wins(self):
        from src.tui.app._state_types import ConfigViewState
        s = ConfigViewState(visible=True)
        assert s.try_set_final("cancel") is True
        assert s.done and s.action == "cancel"
        # 第二次写入被拒绝（first-write-wins）
        assert s.try_set_final("timeout") is False
        assert s.action == "cancel"


# ═══════════════════════════════════════════════════════════
# 3. _cmd_config 命令分支
# ═══════════════════════════════════════════════════════════

class TestCmdConfig:

    def test_no_arg_fallback_text(self, monkeypatch, isolated_rc):
        """无 ChatUI 时 /config 回退文本显示全部配置。"""
        from src.core.commands import _config_cmd as cc
        monkeypatch.setattr(
            "src.tui.consumer.get_active_chat_ui", lambda: None,
        )
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("")) is True
        assert any("配置中心" in c for c in rec.calls)
        assert any("model" in c for c in rec.calls)

    def test_show_and_list(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("show")) is True
        assert any("配置中心" in c for c in rec.calls)
        rec.calls.clear()
        assert cc._cmd_config(_make_ctx("list")) is True
        assert any("配置中心" in c for c in rec.calls)

    def test_get(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("get model")) is True
        joined = "\n".join(rec.calls)
        assert "model" in joined and "deepseek-v4-flash" in joined

    def test_get_unknown_key(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("get nope")) is True
        joined = "\n".join(rec.calls)
        assert "未找到配置键" in joined

    def test_set_persists(self, monkeypatch, isolated_rc):
        """set 写回隔离 RC 文件并给出成功提示。"""
        from src.core.commands import _config_cmd as cc
        from src.config.loader import get_rc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("set temperature 0.7")) is True
        joined = "\n".join(rec.calls)
        assert "已设置" in joined and "temperature" in joined
        # 已持久化到隔离 RC
        assert get_rc()["temperature"] == 0.7

    def test_set_equals_form(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        from src.config.loader import get_rc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("set temperature=0.5")) is True
        assert get_rc()["temperature"] == 0.5

    def test_set_bool(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        from src.config.loader import get_rc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("set enable_notifications false")) is True
        assert get_rc()["enable_notifications"] is False

    def test_set_type_error(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("set temperature abc")) is True
        joined = "\n".join(rec.calls)
        assert "请输入数字" in joined

    def test_set_missing_args(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("set")) is True
        assert "用法" in "\n".join(rec.calls)

    def test_reset(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        from src.config.loader import get_rc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        # 先改值
        get_rc()["temperature"] = 1.9
        assert cc._cmd_config(_make_ctx("reset temperature")) is True
        assert get_rc()["temperature"] == 0.2  # 默认值
        assert "已重置" in "\n".join(rec.calls)

    def test_unknown_sub(self, monkeypatch, isolated_rc):
        from src.core.commands import _config_cmd as cc
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("foo")) is True
        joined = "\n".join(rec.calls)
        assert "未知 config 子命令" in joined

    def test_open_config_ui_opens_and_cleans(self, monkeypatch, isolated_rc):
        """有 ChatUI：/config 打开全屏视图 → 组件关闭（done）→ 清理恢复。"""
        from src.core.commands import _config_cmd as cc
        from src.tui.app.model import AppModel
        model = AppModel()
        fake = _FakeChatUI(model)
        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: fake)

        def fake_sleep(_sec):
            # 模拟组件 Esc 关闭（first-write-wins）
            model.config_view.try_set_final("cancel")

        monkeypatch.setattr(cc._time, "sleep", fake_sleep)
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("")) is True
        # 清理完成：fullscreen 置空 + config_view 重置（保留 seq）
        assert model.fullscreen == ""
        assert not model.config_view.visible
        assert model.config_view.seq == 1
        assert "配置界面已关闭" in "\n".join(rec.calls)

    def test_open_config_ui_timeout(self, monkeypatch, isolated_rc):
        """超时：命令线程原子置终态并清理。"""
        from src.core.commands import _config_cmd as cc
        from src.tui.app.model import AppModel
        model = AppModel()
        fake = _FakeChatUI(model)
        monkeypatch.setattr("src.tui.consumer.get_active_chat_ui", lambda: fake)
        original_monotonic = cc._time.monotonic

        def fake_sleep(_sec):
            # 让 deadline 立即过期（首次轮询即超时）
            monkeypatch.setattr(
                cc._time, "monotonic", lambda: original_monotonic() + 700,
            )

        monkeypatch.setattr(cc._time, "sleep", fake_sleep)
        rec = _Recorder()
        monkeypatch.setattr(cc, "_out", rec)
        assert cc._cmd_config(_make_ctx("")) is True
        assert model.fullscreen == ""
        assert not model.config_view.visible
        assert "超时关闭" in "\n".join(rec.calls)


# ═══════════════════════════════════════════════════════════
# 4. ConfigView 组件渲染与交互
# ═══════════════════════════════════════════════════════════

class TestConfigViewComponent:

    def _render(self, model, width=80, fiber=None):
        from src.tui.app.config_view import ConfigView
        return _render_component(ConfigView, model, width=width, fiber=fiber)

    def _active_cv(self, model):
        return model.config_view

    def test_invisible_returns_empty(self):
        from src.tui.app.model import AppModel
        model = AppModel()  # config_view 默认不可见
        _, el = self._render(model)
        # 根元素为 TEXT 空串（不可见时零高度）
        assert el.type == "text"

    def test_header_shows_title_and_count(self, isolated_rc):
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        _, el = self._render(model)
        header = el.children[0]
        text = header.props.get("children") or ""
        runs = header.props.get("styled") or []
        plain = "".join(getattr(r, "text", "") or "" for r in runs) + str(text)
        assert "配置中心" in plain
        assert "4 项" in plain

    # ── 输入界面（无候选选项：字符串/数值/JSON） ────────────

    def test_input_mode_enter_edit_char_commit(self, monkeypatch, isolated_rc):
        """temperature（无候选）Enter → 输入界面：char 累积 → Enter 确认 →
        update_config 写回 + 显示值刷新 + message 更新。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler is not None

        # 选中第 2 项 temperature → Enter 进入输入界面
        model.config_view.selected = 2
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is True
        assert cv.edit_mode == "input"
        assert cv.edit_key == "TEMPERATURE"
        assert cv.edit_value == "0.2"  # 预填当前值

        # 字符累积/退格
        assert handler(_ev("char", "5")) is True
        assert cv.edit_value == "0.25"
        assert handler(_ev("backspace")) is True
        assert cv.edit_value == "0.2"
        assert handler(_ev("char", "7")) is True
        assert cv.edit_value == "0.27"

        # Enter 确认（写回隔离 RC）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.edit_error == ""
        assert "已更新" in cv.message
        assert get_rc()["temperature"] == 0.27
        # 显示值刷新
        assert cv.entries[2]["value_text"] == "0.27"

    def test_input_mode_type_error_keeps_editing(self, monkeypatch, isolated_rc):
        """输入界面类型校验失败：错误提示 + 保持编辑。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        model.config_view.selected = 2  # temperature（float）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "input"
        # 输入非法值
        cv.edit_value = ""
        for ch in "abc":
            handler(_ev("char", ch))
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is True  # 校验失败保持编辑
        assert "数字" in cv.edit_error

    def test_input_mode_escape_cancel(self, isolated_rc):
        """输入界面 Esc 取消（不保存、不写回）。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        model.config_view.selected = 3  # api_key（sensitive，预填空）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "input"
        assert cv.edit_value == ""  # 敏感项预填空
        for ch in "abc":
            handler(_ev("char", ch))
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.message == ""
        assert cv.edit_error == ""

    # ── 选择界面（有候选选项：枚举/布尔/模型） ──────────────

    def test_select_mode_model_enter_confirm(self, monkeypatch, isolated_rc):
        """MODEL Enter → 选择界面：候选列表、当前值定位、确认写回。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, el = self._render(model)
        handler = _find_input_handler(fiber)
        # 选中第 0 项 MODEL → Enter 进入选择界面
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is True
        assert cv.edit_mode == "select"
        assert cv.edit_key == "MODEL"
        assert cv.edit_options == ["deepseek-v4-pro", "deepseek-v4-flash"]
        # 当前值 deepseek-v4-flash 定位到索引 1
        assert cv.edit_selected == 1

        # 重新渲染（select 模式）→ 主区为候选 ListView——导航回调写 edit_selected
        fiber, el = self._render(model, fiber=fiber)
        pick_ledger = el.children[1].children[0]
        pick_ledger.props["onNavigate"](0)
        assert cv.edit_selected == 0

        # Enter 确认（写回选中的 deepseek-v4-pro）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.edit_error == ""
        assert "已更新" in cv.message
        assert get_rc()["model"] == "deepseek-v4-pro"
        assert cv.entries[0]["value_text"] == "deepseek-v4-pro"

    def test_select_mode_bool_confirm(self, monkeypatch, isolated_rc):
        """bool 配置项走选择界面：导航到 false → Enter 确认写回。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, el = self._render(model)
        handler = _find_input_handler(fiber)
        model.config_view.selected = 1  # ENABLE_NOTIFICATIONS
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "select"
        assert cv.edit_options == ["true", "false"]
        assert cv.edit_selected == 0  # 当前值 true 定位到 0
        # 重新渲染（select 模式）→ 导航到 false
        fiber, el = self._render(model, fiber=fiber)
        el.children[1].children[0].props["onNavigate"](1)
        assert cv.edit_selected == 1
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert get_rc()["enable_notifications"] is False
        assert cv.entries[1]["value_text"] == "false"

    def test_select_mode_escape_cancel(self, isolated_rc):
        """选择界面 Esc 取消（不写回）。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("enter")) is True  # MODEL → select
        cv = self._active_cv(model)
        assert cv.edit_mode == "select"
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.message == ""
        assert cv.edit_error == ""

    def test_escape_closes_view(self, isolated_rc):
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.done is True
        assert cv.action == "cancel"

    def test_ctrl_h_closes_view(self, isolated_rc):
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("ctrl_key", "\x08")) is True
        cv = self._active_cv(model)
        assert cv.done is True

    def test_on_navigate_writes_selected(self, isolated_rc):
        """ListView 导航回调写回 config_view.selected。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=_sample_entries(),
        )
        _, el = self._render(model)
        # 根结构：Column[ header, Row[ledger], bottom ]
        row = el.children[1]
        ledger = row.children[0]
        ledger.props["onNavigate"](2)
        assert model.config_view.selected == 2

    def test_reset_display_preserves_seq(self):
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=7, entries=_sample_entries(),
        )
        model.reset_display()
        assert model.config_view.seq == 7
        assert not model.config_view.visible
        assert model.fullscreen == ""

    def test_entries_updated_by_commit_reflected_in_state(self, monkeypatch, isolated_rc):
        """命令线程持有的 entries 列表与组件共享——select 确认后 value 同步可见。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        entries = _sample_entries()
        model.config_view = ConfigViewState(visible=True, seq=1, entries=entries)
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        # 编辑 bool 项（select 界面）→ 导航到 false → 确认
        model.config_view.selected = 1
        handler(_ev("enter"))
        model.config_view.edit_selected = 1
        handler(_ev("enter"))
        assert model.config_view.entries[1]["value_text"] == "false"

    # ── 子 JSON 结构化编辑界面（list/dict 配置项） ──────────

    def _json_entries(self):
        """含 list（MODELS）与 dict（TOKEN_PRICES）子 JSON 配置项。"""
        return [
            {
                "key": "MODELS", "path": "models", "type": list,
                "value": ["deepseek-v4-pro", "deepseek-v4-flash"],
                "value_text": '["deepseek-v4-pro", "deepseek-v4-flash"]',
                "default_text": "[]", "desc": "可用模型列表", "sensitive": False,
                "options": None, "edit_kind": "json",
            },
            {
                "key": "TOKEN_PRICES", "path": "token_prices", "type": dict,
                "value": {"deepseek-v4-pro": {"input": 0.55, "output": 2.19}},
                "value_text": '{"deepseek-v4-pro": {"input": 0.55, ...}}',
                "default_text": "{}", "desc": "token 价格表", "sensitive": False,
                "options": None, "edit_kind": "json",
            },
        ]

    def test_json_mode_list_edit_append_delete(self, monkeypatch, isolated_rc):
        """list 型子 JSON：Enter 编辑元素 → a 追加 → d 删除 → Esc 写回。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=self._json_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        # 选中 MODELS（第 0 项）→ Enter 进入 json 界面
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.editing is True
        assert cv.edit_mode == "json"
        assert cv.edit_json_data == ["deepseek-v4-pro", "deepseek-v4-flash"]
        assert cv.edit_json_selected == 0

        # Enter 编辑选中元素（[0]）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_json_action == "edit"
        assert cv.edit_value == "deepseek-v4-pro"
        # 修改后确认 → 返回 json 界面
        cv.edit_value = ""
        for ch in "deepseek-v4-max":
            handler(_ev("char", ch))
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_data[0] == "deepseek-v4-max"

        # a 追加新元素
        assert handler(_ev("char", "a")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_json_action == "append"
        for ch in "custom-model":
            handler(_ev("char", ch))
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_data == ["deepseek-v4-max", "deepseek-v4-flash", "custom-model"]

        # d 删除选中（当前选中最后一项 custom-model）
        cv.edit_json_selected = 2
        assert handler(_ev("char", "d")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_data == ["deepseek-v4-max", "deepseek-v4-flash"]

        # Esc 保存写回
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.message.startswith("已更新")
        assert get_rc()["models"] == ["deepseek-v4-max", "deepseek-v4-flash"]
        assert "deepseek-v4-max" in cv.entries[0]["value_text"]

    def test_json_mode_dict_recursive_edit_append_delete(self, monkeypatch, isolated_rc):
        """dict 型子 JSON 递归编辑：Enter 嵌套值递归进入下一层 → 编辑标量 →
        a 追加 → d 删除 → Esc 逐级返回 → 顶层 Esc 写回。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=self._json_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        # 选中 TOKEN_PRICES（第 1 项）→ Enter 进入 json 界面（顶层 dict）
        model.config_view.selected = 1
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_keys == ["deepseek-v4-pro"]
        assert cv.edit_json_path == []

        # Enter 选中 deepseek-v4-pro（值为嵌套 dict）→ 递归进入下一层
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_path == ["deepseek-v4-pro"]
        assert cv.edit_json_keys == ["input", "output"]
        assert cv.edit_json_selected == 0

        # Enter 编辑 input（标量 0.55）→ 子输入
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_value == "0.55"
        # 修改后确认 → 返回 json（当前层）
        cv.edit_value = "0.5"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_data["deepseek-v4-pro"]["input"] == 0.5

        # 当前层 a 追加 key=value
        assert handler(_ev("char", "a")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_json_action == "append"
        cv.edit_value = "input_cache_hit=0.07"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_data["deepseek-v4-pro"]["input_cache_hit"] == 0.07

        # 非法追加格式提示（保持子输入）
        assert handler(_ev("char", "a")) is True
        cv.edit_value = "没有分隔符"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert "key=value" in cv.edit_error
        assert handler(_ev("escape")) is True  # 取消返回 json
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"

        # 当前层 d 删除 output 键（导航到索引 1）
        cv.edit_json_selected = 1
        assert handler(_ev("char", "d")) is True
        cv = self._active_cv(model)
        assert "output" not in cv.edit_json_data["deepseek-v4-pro"]

        # Esc 返回顶层（path 变空，修改保留）
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_path == []
        assert cv.edit_json_keys == ["deepseek-v4-pro"]

        # 顶层 Esc 保存写回
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert get_rc()["token_prices"]["deepseek-v4-pro"]["input"] == 0.5
        assert get_rc()["token_prices"]["deepseek-v4-pro"]["input_cache_hit"] == 0.07
        assert "output" not in get_rc()["token_prices"]["deepseek-v4-pro"]

    def test_json_mode_dict_top_level_append_delete(self, monkeypatch, isolated_rc):
        """顶层 dict 追加/删除模型键（递归返回后操作顶层容器）。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=self._json_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        model.config_view.selected = 1  # TOKEN_PRICES
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == []

        # 顶层 a 追加模型键
        assert handler(_ev("char", "a")) is True
        cv.edit_value = "deepseek-v4-flash={\"input\": 0.55, \"output\": 2.19}"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert "deepseek-v4-flash" in cv.edit_json_data

        # 顶层 d 删除 deepseek-v4-flash（导航到索引 1）
        cv.edit_json_selected = 1
        assert handler(_ev("char", "d")) is True
        cv = self._active_cv(model)
        assert "deepseek-v4-flash" not in cv.edit_json_data

        # Esc 保存写回
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert "deepseek-v4-flash" not in get_rc()["token_prices"]

    def test_json_mode_escape_cancel_sub_input(self, isolated_rc):
        """json 子输入 Esc 取消 → 返回 json 界面（不修改数据）。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=self._json_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        assert handler(_ev("enter")) is True  # 进入 json 界面
        assert handler(_ev("enter")) is True  # 进入子输入
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        cv.edit_value = "changed"
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        # 数据未修改
        assert cv.edit_json_data[0] == "deepseek-v4-pro"

    def test_json_mode_esc_writes_back_even_empty_list(self, monkeypatch, isolated_rc):
        """json 界面直接 Esc（未做修改）也写回当前数据（幂等）。"""
        from src.tui.app.model import AppModel, ConfigViewState
        from src.config.loader import get_rc
        model = AppModel()
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=self._json_entries(),
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)
        model.config_view.selected = 1  # TOKEN_PRICES
        assert handler(_ev("enter")) is True
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert get_rc()["token_prices"]["deepseek-v4-pro"]["input"] == 0.55

    def test_json_mode_nested_list_in_dict_recursion(self, monkeypatch, isolated_rc):
        """多层递归：dict 值内嵌 list（如 skills.auto_load）→ 逐层下钻编辑。"""
        from src.tui.app.model import AppModel, ConfigViewState
        model = AppModel()
        entries = [
            {
                "key": "skills", "path": "skills", "type": dict,
                "value": {
                    "enabled": True,
                    "auto_load": ["read_file", "search"],
                    "nested": {"level2": [{"x": 1}]},
                },
                "value_text": "{...}", "default_text": "{}",
                "desc": "技能配置", "sensitive": False,
                "options": None, "edit_kind": "json",
            },
        ]
        model.config_view = ConfigViewState(
            visible=True, seq=1, entries=entries,
        )
        fiber, _el = self._render(model)
        handler = _find_input_handler(fiber)

        # 进入 skills json 界面（顶层 dict）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == []
        assert cv.edit_json_keys == ["enabled", "auto_load", "nested"]

        # 递归进入 auto_load（list）
        cv.edit_json_selected = 1
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == ["auto_load"]
        assert isinstance(cv.edit_json_data["auto_load"], list)

        # 编辑 auto_load[0]（标量 read_file）
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_value == "read_file"
        cv.edit_value = "write_file"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json"
        assert cv.edit_json_data["auto_load"][0] == "write_file"

        # Esc 返回顶层
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == []
        assert cv.edit_json_keys == ["enabled", "auto_load", "nested"]

        # 递归进入 nested → level2（list）→ [0]（dict）→ x（标量）
        cv.edit_json_selected = 2
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == ["nested"]
        assert cv.edit_json_keys == ["level2"]
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == ["nested", "level2"]
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == ["nested", "level2", "0"]
        assert cv.edit_json_keys == ["x"]
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_mode == "json_input"
        assert cv.edit_value == "1"
        cv.edit_value = "42"
        assert handler(_ev("enter")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_data["nested"]["level2"][0]["x"] == 42

        # 连续 Esc 逐级返回（不写回）→ 最后顶层 Esc 写回
        assert handler(_ev("escape")) is True
        assert handler(_ev("escape")) is True
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.edit_json_path == []
        assert handler(_ev("escape")) is True
        cv = self._active_cv(model)
        assert cv.editing is False
        assert cv.edit_json_data["nested"]["level2"][0]["x"] == 42
        assert cv.edit_json_data["auto_load"][0] == "write_file"


# ═══════════════════════════════════════════════════════════
# 6. CLI 参数解析（P0 review：-m/-v 全局选项 + 子命令默认值兜底）
# ═══════════════════════════════════════════════════════════

class TestCliParseArgs:

    def _parse(self, argv):
        import sys
        from src.app_init._args import _parse_args
        old = sys.argv
        sys.argv = ["chat.py"] + argv
        try:
            return _parse_args()
        finally:
            sys.argv = old

    def test_old_syntax_global_flags(self):
        """旧语法全局选项（P0 review）：``-v/-vv/-m/--version`` 经 run 子命令
        解析正常（修复前 ``unrecognized arguments`` 退出）。"""
        ns = self._parse(["-v", "--version"])
        assert ns.verbose == 1 and ns.version is True
        ns = self._parse(["-vv", "--version"])
        assert ns.verbose == 2 and ns.version is True
        ns = self._parse(["-m", "deepseek-v4-pro", "--version"])
        assert ns.model == "deepseek-v4-pro" and ns.version is True
        ns = self._parse(["run", "--model", "deepseek-v4-pro", "--version"])
        assert ns.model == "deepseek-v4-pro" and ns.version is True

    def test_version_flag(self):
        ns = self._parse(["--version"])
        assert ns.version is True
        ns = self._parse(["version"])
        assert ns.command == "version"

    def test_subcommand_defaults(self):
        """子命令（session/config/clawbot）经 parser.set_defaults 兜底
        model/verbose——main.py 读取不报 AttributeError。"""
        ns = self._parse(["session", "list"])
        assert ns.command == "session" and ns.model == "" and ns.verbose == 0
        ns = self._parse(["config", "list"])
        assert ns.command == "config" and ns.model == "" and ns.verbose == 0
        ns = self._parse(["clawbot"])
        assert ns.command == "clawbot" and ns.model == "" and ns.verbose == 0

    def test_config_sub_args(self):
        ns = self._parse(["config", "set", "model", "deepseek-v4-pro"])
        assert ns.command == "config"
        assert ns.config_cmd == "set"
        assert ns.key == "model" and ns.value == "deepseek-v4-pro"


# ═══════════════════════════════════════════════════════════
# 5. /config 参数补全
# ═══════════════════════════════════════════════════════════

class TestConfigCompletion:

    def _complete(self, text):
        from src.tui._completion_engine import CompletionEngine
        engine = CompletionEngine(commands_source=lambda: [])
        return engine.complete(text)

    def test_config_no_arg_completes_subcommands(self):
        items = self._complete("/config")
        texts = [i.text for i in items]
        assert "/config show" in texts
        assert "/config get" in texts
        assert "/config set" in texts
        assert "/config reset" in texts

    def test_config_sub_prefix_completes(self):
        items = self._complete("/config se")
        texts = [i.text for i in items]
        assert any("set" in t for t in texts)

    def test_config_get_completes_keys(self, isolated_rc):
        items = self._complete("/config get ")
        texts = [i.text for i in items]
        assert any("model" in t for t in texts)
        assert any("api_key" in t for t in texts)

    def test_config_set_prefix_completes_keys(self, isolated_rc):
        items = self._complete("/config set temp")
        texts = [i.text for i in items]
        assert any("temperature" in t for t in texts)

    def test_config_set_no_arg_preserves_subcommand(self, isolated_rc):
        """P1（review 2026-08-20）：``/config set`` + Tab（无尾随空格）——
        候选 ``set <键>`` + 替换子命令词，词边界拼接后保留 ``/config set``
        前缀（应用结果 ``/config set model``）。修复前按「最后一个词」替换
        把 ``set`` 整体替换为键名 → ``/config model``。"""
        from src.tui._completion import _apply_completion
        items = self._complete("/config set")
        # 候选为 ``set {key}`` + start_pos=-len("set")
        assert items and all(i.start_pos == -3 for i in items)
        repl = next(i for i in items if "model" in i.text)
        # 真实弹窗确认路径：orig_prefix=last_word("set")
        applied = _apply_completion("/config set", repl.text, repl.start_pos, "set")
        assert applied == "/config set model"

    def test_config_set_with_prefix_replaces_last_word(self, isolated_rc):
        """``/config set temp`` + Tab——替换最后词，应用后 ``/config set temperature``。"""
        from src.tui._completion import _apply_completion
        items = self._complete("/config set temp")
        repl = next(i for i in items if "temperature" in i.text)
        applied = _apply_completion(
            "/config set temp", repl.text, repl.start_pos, "temp",
        )
        assert applied == "/config set temperature"

    def test_config_show_no_more_params(self):
        items = self._complete("/config show")
        assert items == []
