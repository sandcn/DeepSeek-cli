"""TUI 鼠标上报开关（``tui_mouse``）配置与装配测试。

覆盖：``TuiConfig.mouse`` 默认/覆盖、``DEFAULTS`` / ``CONFIG_KEYS`` 元数据、
``src.config.TUI_MOUSE`` 惰性导出、装配层 ``_rc_mouse`` 读取与回退、
``create_shared`` 注入、``InkSession.set_terminal_modes(mouse=...)`` 请求记录。
"""

from __future__ import annotations

import builtins

import pytest

from src.tui._config import TuiConfig


# ── TuiConfig ────────────────────────────────────────────


def test_tui_config_mouse_default_on():
    """默认启用鼠标上报（滚轮/点击交互）。"""
    assert TuiConfig.defaults().mouse is True
    assert TuiConfig().mouse is True


def test_tui_config_mouse_override_is_immutable():
    """with_overrides 覆盖 mouse 且不改动原实例。"""
    cfg = TuiConfig.defaults().with_overrides(mouse=False)
    assert cfg.mouse is False
    assert TuiConfig.defaults().mouse is True


# ── 配置元数据 ────────────────────────────────────────────


def test_defaults_declares_tui_mouse():
    from src.config.defaults import CONFIG_KEYS, DEFAULTS

    assert DEFAULTS["tui_mouse"] is True
    entry = CONFIG_KEYS["TUI_MOUSE"]
    assert entry["rc_path"] == ("tui_mouse",)
    assert entry["type"] is bool
    assert entry["default"] is True
    assert entry["cacheable"] is True


def test_config_module_exposes_tui_mouse():
    """``src.config`` 经 CONFIG_KEYS 自动派生 TUI_MOUSE。"""
    import src.config as cfg

    assert cfg.TUI_MOUSE is True


# ── 装配层 ────────────────────────────────────────────────


def test_rc_mouse_returns_bool():
    from src.tui import _assembly_steps

    assert isinstance(_assembly_steps._rc_mouse(), bool)


def test_rc_mouse_falls_back_to_default_when_config_unavailable(monkeypatch):
    """RC 读取失败（导入异常）时回退 TuiConfig 默认值 True。"""
    from src.tui import _assembly_steps

    real_import = builtins.__import__

    def _boom(name, *args, **kwargs):
        if name == "src.config":
            raise RuntimeError("config unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _boom)
    assert _assembly_steps._rc_mouse() is True


def test_create_shared_injects_mouse(monkeypatch):
    """装配 create_shared 把 _rc_mouse 结果写入 TuiConfig。"""
    from src.tui import _assembly_steps

    monkeypatch.setattr(_assembly_steps, "_rc_mouse", lambda: False)
    cfg, _model = _assembly_steps.create_shared()
    assert cfg.mouse is False


# ── InkSession 终端模式请求 ────────────────────────────────


def test_session_records_mouse_mode_request(monkeypatch):
    """set_terminal_modes(mouse=...) 记录请求（终端 I/O 用桩隔离）。"""
    from src.tui.ink import session as session_mod

    monkeypatch.setattr(
        session_mod.InkSession, "_apply_requested_modes", lambda self: None,
    )
    sess = session_mod.InkSession(
        model=object(),
        apply_cmd=lambda *a, **k: None,
        build_tree=lambda *a, **k: None,
        config=TuiConfig.defaults(),
    )
    sess.set_terminal_modes(mouse=True)
    assert sess._requested_modes["mouse"] is True
    sess.set_terminal_modes(mouse=False)
    assert sess._requested_modes["mouse"] is False


def test_session_mouse_mode_default_off_without_request(monkeypatch):
    """未显式请求时不启用鼠标（框架默认行为保持不变）。"""
    from src.tui.ink import session as session_mod

    monkeypatch.setattr(
        session_mod.InkSession, "_apply_requested_modes", lambda self: None,
    )
    sess = session_mod.InkSession(
        model=object(),
        apply_cmd=lambda *a, **k: None,
        build_tree=lambda *a, **k: None,
        config=TuiConfig.defaults(),
    )
    assert sess._mouse_enabled is False


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
