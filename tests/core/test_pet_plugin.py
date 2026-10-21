"""``/pet`` 命令插件：开关、参数校验与无界面降级。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.commands.plugins.pet_plugin import PetPlugin
from src.plugins.manifest_entries import COMMAND_PLUGIN_ENTRIES


class _FakeChatUI:
    def __init__(self) -> None:
        self.lines: list[str] = []
        self.open_calls = 0
        self.close_calls = 0
        self.open_state = False

    def write_line(self, text: str) -> None:
        self.lines.append(str(text))

    def open_pet_floating_window(self) -> bool:
        self.open_calls += 1
        self.open_state = True
        return True

    def close_pet_floating_window(self) -> bool:
        self.close_calls += 1
        if not self.open_state:
            return False
        self.open_state = False
        return True

    def is_pet_floating_window_open(self) -> bool:
        return self.open_state


def _ctx(arg: str = "") -> SimpleNamespace:
    return SimpleNamespace(arg=arg)


def _plugin(chat_ui) -> PetPlugin:
    plugin = PetPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=chat_ui))
    return plugin


def test_plugin_metadata_and_manifest_entry() -> None:
    plugin = PetPlugin()
    assert plugin.meta.name == "pet"
    assert plugin.meta.usage == "[on|off]"
    assert "宠物" in plugin.meta.description

    entries = [
        entry for entry in COMMAND_PLUGIN_ENTRIES
        if entry.get("id") == "cmd_pet"
    ]
    assert len(entries) == 1
    assert entries[0]["config"]["name"] == "pet"
    assert entries[0]["config"]["command"].endswith(".PetPlugin")


def test_pet_opens_and_toggles_closed() -> None:
    ui = _FakeChatUI()
    plugin = _plugin(ui)

    assert plugin.execute(_ctx()) is True
    assert ui.open_calls == 1
    assert ui.open_state is True
    assert any("出现" in line for line in ui.lines)

    assert plugin.execute(_ctx()) is True
    assert ui.close_calls == 1
    assert ui.open_state is False
    assert any("游走" in line for line in ui.lines)


def test_pet_on_forces_reopen_and_off_closes() -> None:
    ui = _FakeChatUI()
    plugin = _plugin(ui)

    plugin.execute(_ctx("on"))
    plugin.execute(_ctx("on"))
    assert ui.open_calls == 2, "/pet on 应强制重新开窗"

    plugin.execute(_ctx("off"))
    assert ui.open_state is False
    assert ui.close_calls == 1

    # 已关闭时再 /pet off：提示但不报错。
    plugin.execute(_ctx("off"))
    assert ui.close_calls == 2
    assert any("本来就没开" in line for line in ui.lines)


def test_pet_unknown_argument_prints_usage_without_side_effect() -> None:
    ui = _FakeChatUI()
    plugin = _plugin(ui)

    assert plugin.execute(_ctx("dance")) is True
    assert ui.open_calls == 0
    assert ui.close_calls == 0
    assert any("用法" in line for line in ui.lines)


def test_pet_without_chat_ui_reports_unavailable(monkeypatch) -> None:
    import src.core.commands.plugins.pet_plugin as mod

    monkeypatch.setattr(mod, "_active_chat_ui", lambda _loop: None)
    plugin = PetPlugin()
    plugin.bind_loop(SimpleNamespace(_chat_ui=None))
    emitted: list[str] = []
    monkeypatch.setattr(plugin, "output", lambda text: emitted.append(str(text)))

    assert plugin.execute(_ctx()) is True
    assert emitted
    assert "交互式终端" in emitted[0]


def test_pet_reports_unsupported_window(monkeypatch) -> None:
    class _NoWindowUI:
        def __init__(self) -> None:
            self.lines: list[str] = []

        def write_line(self, text: str) -> None:
            self.lines.append(str(text))

    ui = _NoWindowUI()
    plugin = _plugin(ui)
    assert plugin.execute(_ctx()) is True
    assert any("不支持" in line for line in ui.lines)
