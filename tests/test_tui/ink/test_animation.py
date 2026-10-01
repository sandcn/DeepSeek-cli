"""useAnimation 官方语义 + 共享动画驱动测试。"""

from __future__ import annotations

import time

from src.tui.ink import h, TEXT, useAnimation
from src.tui.ink import hooks as H
from tests.test_tui.ink._harness import Harness


def _animation_component(props):
    res = useAnimation(props.get("options"))
    props["_out"]["frame"] = res["frame"]
    props["_out"]["time"] = res["time"]
    props["_out"]["delta"] = res["delta"]
    props["_out"]["reset"] = res["reset"]
    return h(TEXT, {"children": f"f{res['frame']}"})


def test_use_animation_result_keys():
    out = {}
    harness = Harness(20)
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 50}}))
    assert set(out) == {"frame", "time", "delta", "reset"}
    assert callable(out["reset"])
    assert out["frame"] == 0
    assert out["delta"] >= 0.0


def test_use_animation_frame_advances_with_driver():
    out = {}
    harness = Harness(20)
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 50}}))
    time.sleep(0.12)
    H.advance_animation()
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 50}}))
    assert out["frame"] >= 2


def test_use_animation_inactive_returns_zero():
    out = {}
    harness = Harness(20)
    harness.render(h(_animation_component, {"_out": out, "options": {"isActive": False}}))
    assert out["frame"] == 0
    assert out["time"] == 0.0
    assert out["delta"] == 0.0


def test_use_animation_subscribes_driver():
    out = {}
    harness = Harness(20)
    H.reset_animation_state()
    harness.render(h(_animation_component, {"_out": out, "options": {}}))
    assert H.has_active_animations() is True
    H.reset_animation_state()
    assert H.has_active_animations() is False


def test_reset_restarts_timing():
    out = {}
    harness = Harness(20)
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 10}}))
    time.sleep(0.05)
    H.advance_animation()
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 10}}))
    assert out["frame"] > 0
    out["reset"]()
    harness.render(h(_animation_component, {"_out": out, "options": {"interval": 10}}))
    assert out["frame"] == 0


def test_advance_animation_does_not_force_render():
    """advance 不通知订阅者（避免 force 破坏 10Hz 节流）。"""
    calls = []
    from src.tui.ink._animation import subscribe_animation, notify_animation_listeners

    token = subscribe_animation(lambda: calls.append(1))
    try:
        H.advance_animation()
        assert calls == []
        notify_animation_listeners()
        assert calls == [1]
    finally:
        token()
    H.reset_animation_state()
