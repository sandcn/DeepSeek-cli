"""/theme 主题选择器回归测试。

覆盖 2026-10 审查确认的缺陷：
  - 过滤开启/关闭后 ``selected`` 坐标混用导致预览与实际主题脱钩；
  - 过滤开启时重新搜索使用旧 ``index_map``，应用错主题；
  - 帮助面板非模态（Enter/方向键/斜杠仍作用于列表）；
  - 同批按键使用渲染期旧选择；
  - 无匹配搜索后 ``search_filter`` 卡死；
  - 背景色槽位（code_bg/selection_bg）预览显示 ``-``；
  - 当前主题 ✓ 被描述截断；
  - 长搜索查询溢出终端宽度；
  - 右栏/帮助无滚动；
  - 超时关闭恢复原主题。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.commands._config_cmd import (
    _cmd_theme,
    _open_theme_ui,
    _restore_original_theme,
)
from src.core.commands._view_opener import open_fullscreen_view
from src.tui._width import wcswidth_simple
from src.tui.app import theme_view
from src.tui.app._state_types import ThemeViewState
from src.tui.app.theme_view import ThemeView, _palette_preview_rows, _search_prompt
from src.tui.ink import h
from src.tui.ink import components as _components
from src.tui.ink.helpers import strip_ansi
from src.tui.ink.reconciler import Reconciler


class _Event:
    def __init__(self, kind: str, char: str = "") -> None:
        self.kind = kind
        self.char = char
        self.modifier = 0
        self.keycode = 0
        self.kitty_bits = -1
        self.event_type = ""
        self.raw = None


def _entries():
    return [
        {"name": "dark", "desc": "暗色", "active": True},
        {"name": "light", "desc": "亮色", "active": False},
        {"name": "dracula", "desc": "Dracula 糖果色", "active": False},
        {"name": "nord", "desc": "Nord 冷色调", "active": False},
    ]


def _state(**kwargs):
    defaults = dict(
        visible=True,
        entries=_entries(),
        original="dark",
        selected=0,
    )
    defaults.update(kwargs)
    return ThemeViewState(**defaults)


def _mount(model, width: int = 80):
    element = h(ThemeView, {"model": model, "width": width})
    reconciler = Reconciler(None)
    routers = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()

    def redraw() -> None:
        reconciler.render(root, element, width, 24)

    def press(kind: str, char: str = "") -> bool:
        ok = routers[-1](_Event(kind, char))
        redraw()
        return ok

    def snapshot() -> str:
        frame = _components.render_frame(root, width)
        return strip_ansi("\n".join(line.render() for line in frame.lines))

    redraw()
    return root, press, snapshot


def _patch_apply(monkeypatch, result=True):
    calls: list[str] = []

    def fake_apply(
        name: str, *, persist: bool = True, allow_unregistered: bool = False,
    ) -> bool:
        calls.append(name)
        return result if not callable(result) else result(name)

    monkeypatch.setattr(theme_view, "apply_theme", fake_apply)
    return calls


def test_filter_toggle_keeps_selected_full_index_and_preview(monkeypatch) -> None:
    calls = _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    assert press("arrow_down") is True
    assert model.theme_view.selected == 1
    assert calls[-1] == "light"

    # 搜索 dark → 过滤开 → 过滤关 → 下移选中 light → 再开过滤。
    for ch in "/dark":
        press("char", ch)
    press("enter")
    assert model.theme_view.search_matches == [0]
    press("char", "f")
    press("char", "f")
    press("arrow_down")
    assert model.theme_view.selected == 1
    assert calls[-1] == "light"

    press("char", "f")
    # 修复后：过滤开启时当前选中不在匹配集，选择切到首匹配并应用 dark，
    # 而不是只改显示、实际主题留在 light。
    assert model.theme_view.selected == 0
    assert calls[-1] == "dark"


def test_search_while_filter_on_applies_new_match_not_old_row(monkeypatch) -> None:
    calls = _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    for ch in "/dark":
        press("char", ch)
    press("enter")
    press("char", "f")
    assert model.theme_view.search_filter is True
    assert model.theme_view.selected == 0

    press("char", "/")
    for _ in range(len("dark")):
        press("backspace")
    for ch in "light":
        press("char", ch)
    press("enter")

    # 旧实现用旧 index_map=[0] 映射完整下标 1 → 越界返回 0，实际应用 dark；
    # 新实现直接使用完整下标 1，应用 light。
    assert calls[-1] == "light"
    assert model.theme_view.selected == 1


def test_preview_is_memory_only_until_enter(monkeypatch) -> None:
    calls = []
    persist_flags = []

    def fake_apply(
        name: str, *, persist: bool = True, allow_unregistered: bool = False,
    ) -> bool:
        calls.append(name)
        persist_flags.append(persist)
        return True

    monkeypatch.setattr(theme_view, "apply_theme", fake_apply)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    press("arrow_down")
    assert calls[-1] == "light"
    assert persist_flags[-1] is False

    press("enter")
    assert calls[-1] == "light"
    assert persist_flags[-1] is True
    assert model.theme_view.done is True


def test_same_batch_navigation_then_enter_uses_live_selection(monkeypatch) -> None:
    calls = _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    element = h(ThemeView, {"model": model, "width": 80})
    reconciler = Reconciler(None)
    routers = []
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()
    reconciler.render(root, element, 80, 24)
    router = routers[-1]

    # 同一输入批内不重新渲染：↓ 推进 ListView cursor_ref 并预览 light，
    # 紧接着 Enter 必须读取实时 selected（1），不能使用渲染期旧 sel（0）。
    assert router(_Event("arrow_down")) is True
    assert router(_Event("enter")) is True
    assert model.theme_view.selected == 1
    assert model.theme_view.done is True
    assert calls[-1] == "light"


def test_help_panel_is_modal(monkeypatch) -> None:
    calls = _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    press("char", "?")
    assert model.theme_view.help_open is True
    press("enter")
    assert model.theme_view.done is False
    press("arrow_down")
    assert model.theme_view.selected == 0
    assert calls == []

    # 全角问号同样关闭帮助。
    press("char", "\uff1f")
    assert model.theme_view.help_open is False
    press("enter")
    assert model.theme_view.done is True
    assert calls[-1] == "dark"


def test_help_panel_scrolls_with_page_keys(monkeypatch) -> None:
    _patch_apply(monkeypatch)
    monkeypatch.setattr(theme_view, "viewport_rows", lambda: 8)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    press("char", "?")
    assert model.theme_view.help_open is True
    press("page_down")
    assert model.theme_view.help_open is True
    assert model.theme_view.help_scroll > 0


def test_escape_restores_original_theme(monkeypatch) -> None:
    calls = _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    press("arrow_down")
    assert calls[-1] == "light"
    press("escape")
    assert model.theme_view.done is True
    assert model.theme_view.action == "cancel"
    assert calls[-1] == "dark"


def test_enter_does_not_close_when_apply_fails(monkeypatch) -> None:
    _patch_apply(monkeypatch, result=lambda name: name == "dark")
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    press("arrow_down")
    press("enter")
    assert model.theme_view.done is False
    assert "应用失败" in model.theme_view.status_message

    # Esc 恢复 dark 成功后才允许关闭。
    press("escape")
    assert model.theme_view.done is True


def test_filter_is_reset_when_search_has_no_match(monkeypatch) -> None:
    _patch_apply(monkeypatch)
    model = SimpleNamespace(theme_view=_state())
    _, press, _ = _mount(model)

    for ch in "/dark":
        press("char", ch)
    press("enter")
    press("char", "f")
    assert model.theme_view.search_filter is True

    press("char", "/")
    for _ in range(len("dark")):
        press("backspace")
    for ch in "zzz":
        press("char", ch)
    press("enter")
    assert model.theme_view.search_matches == []
    assert model.theme_view.search_filter is False
    # 过滤态已复位，f 不会再被“无匹配”卡住。
    press("char", "f")
    assert "过滤需先搜索且有匹配" in model.theme_view.status_message


def test_palette_preview_shows_background_only_slots() -> None:
    rows = _palette_preview_rows("dark", 60)
    texts = ["".join(run.text for run in row) for row in rows]
    code_bg = next(text for text in texts if text.startswith("code_bg"))
    selection_bg = next(text for text in texts if text.startswith("selection_bg"))
    assert "-" not in code_bg
    assert "-" not in selection_bg
    assert "235" in code_bg


def test_palette_swatch_uses_foreground_color() -> None:
    from src.tui.core._theme import ThemeRegistry

    rows = _palette_preview_rows("dark", 60)
    palette = ThemeRegistry.resolve("dark")
    for slot in ("accent", "code_bg", "selection_bg"):
        row = next(row for row in rows if row and row[0].text.startswith(slot))
        bar = row[2]
        expected = theme_view._slot_color(getattr(palette, slot))
        assert bar.text == "\u2588" * 8
        assert bar.style is not None
        # █ 用前景色绘制；修复前这里只有 bg，导致全部显示默认白色。
        assert bar.style.fg == expected
        assert bar.style.bg is None


def test_active_marker_survives_long_description() -> None:
    entries = [
        {"name": "tokyo-night", "desc": "Tokyo Night 深蓝夜色", "active": True},
        {"name": "dark", "desc": "暗色", "active": False},
    ]
    model = SimpleNamespace(
        theme_view=ThemeViewState(visible=True, entries=entries, original="tokyo-night", selected=1)
    )
    _, _, snapshot = _mount(model, width=80)
    lines = snapshot().splitlines()
    active_line = next(
        line for line in lines
        if "tokyo-night" in line and "\u258d" not in line
    )
    assert "\u2713" in active_line


def test_search_prompt_is_width_bounded() -> None:
    prompt = _search_prompt("x" * 200, 40)
    assert wcswidth_simple(prompt) <= 40
    assert prompt.startswith("/\u2026")
    assert prompt.endswith("\u258f")


def test_right_pane_scrolls_with_page_keys(monkeypatch) -> None:
    _patch_apply(monkeypatch)
    monkeypatch.setattr(theme_view, "viewport_rows", lambda: 10)
    model = SimpleNamespace(theme_view=_state())
    _, press, snapshot = _mount(model)

    assert "placeholder" not in snapshot()
    press("page_down")
    press("page_down")
    assert model.theme_view.scroll > 0
    assert "placeholder" in snapshot()


def test_restore_original_theme_helper() -> None:
    class _Adapter:
        def __init__(self):
            self.current = "light"
            self.calls = []
            self.persist_calls = []

        def get_active_theme(self):
            return self.current

        def set_theme(self, name, *, persist=True, allow_unregistered=False):
            self.calls.append(name)
            self.persist_calls.append(persist)
            self.current = name
            return True

    adapter = _Adapter()
    state = SimpleNamespace(original="dark")
    assert _restore_original_theme(adapter, state) is True
    assert adapter.calls == ["dark"]
    assert adapter.persist_calls == [False]
    assert adapter.current == "dark"

    # 已经是原主题：不重复写配置。
    adapter.calls.clear()
    assert _restore_original_theme(adapter, state) is True
    assert adapter.calls == []


def test_open_fullscreen_view_invokes_on_timeout(monkeypatch) -> None:
    from src.core.adapters import ui_runtime

    model = SimpleNamespace(theme_view=ThemeViewState(), fullscreen="")
    chat_ui = SimpleNamespace(
        get_model=lambda: model,
        request_bottom_redraw=lambda: None,
        flush_input_router=lambda timeout: None,
    )
    monkeypatch.setattr(ui_runtime, "get_active_chat_ui", lambda: chat_ui)
    calls = []

    assert open_fullscreen_view(
        None,
        view_id="theme",
        state_attr="theme_view",
        state_cls=ThemeViewState,
        timeout=0.0,
        on_timeout=lambda state: calls.append(state),
    ) is True
    assert len(calls) == 1
    assert model.fullscreen == ""


def test_update_config_reports_write_failure(monkeypatch) -> None:
    from src.config import loader

    monkeypatch.setattr(loader, "get_rc", lambda: {"theme": "dark"})
    monkeypatch.setattr(loader, "_write_rc_file", lambda rc: False)
    assert loader.update_config("theme", "light") is False


def test_update_config_memory_only_skips_disk(monkeypatch) -> None:
    from src.config import loader

    rc = {"theme": "dark"}
    writes = []
    monkeypatch.setattr(loader, "get_rc", lambda: rc)
    monkeypatch.setattr(loader, "_write_rc_file", lambda data: writes.append(data) or True)
    assert loader.update_config("theme", "light", persist=False) is True
    assert rc["theme"] == "light"
    assert writes == []


def test_apply_theme_reports_persistence_failure(monkeypatch) -> None:
    from src.config import loader

    monkeypatch.setattr(theme_view, "_theme_names", lambda: ("dark", "light"))
    monkeypatch.setattr(
        loader, "update_config",
        lambda key, value, *, persist=True: False,
    )
    assert theme_view.apply_theme("light") is False


def test_apply_theme_can_restore_unregistered_original(monkeypatch) -> None:
    from src.config import loader

    monkeypatch.setattr(theme_view, "_theme_names", lambda: ("dark", "light"))
    calls = []
    monkeypatch.setattr(
        loader, "update_config",
        lambda key, value, *, persist=True: calls.append((key, value, persist)) or True,
    )
    # 普通切换拒绝未注册主题；只有恢复路径（Esc/超时）允许写回原值。
    assert theme_view.apply_theme("legacy", persist=False) is False
    assert theme_view.apply_theme(
        "legacy", persist=False, allow_unregistered=True,
    ) is True
    assert calls == [("theme", "legacy", False)]


def test_cmd_theme_does_not_write_config_twice(monkeypatch) -> None:
    calls = []
    port_calls = []

    class _Adapter:
        def get_theme_names_with_desc(self):
            return [("dark", "暗色")]

        def get_active_theme(self):
            return "dark"

        def set_theme(self, name):
            calls.append(name)
            return True

    class _Port:
        def set(self, key, value):
            port_calls.append((key, value))

    ctx = SimpleNamespace(arg="dark", ui_adapter=_Adapter(), config_port=_Port())
    assert _cmd_theme(ctx) is True
    assert calls == ["dark"]
    # set_theme 已写 RC；不能再经 config_port.set 写第二遍。
    assert port_calls == []


def test_open_theme_ui_without_themes_falls_back_to_text(monkeypatch) -> None:
    class _Adapter:
        def get_theme_names_with_desc(self):
            return []

        def get_active_theme(self):
            return "dark"

        def set_theme(self, name):
            return True

    ctx = SimpleNamespace(ui_adapter=_Adapter())
    # 没有主题数据时不应打开空白全屏视图，交给 _cmd_theme 的文本列表兜底。
    assert _open_theme_ui(ctx) is False
