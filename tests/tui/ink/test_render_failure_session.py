"""整帧渲染异常 → 会话级可见错误帧（2026-10-23 端到端回归测试）。"""

from __future__ import annotations

import io

from src.tui.app.model import AppModel
from src.tui.ink import Text, h
from src.tui.ink.session import InkSession


class _WidthCache:
    def get_width(self) -> int:
        return 80

    def get_height(self) -> int:
        return 24

    def poll(self, ttl: float) -> None:
        return None

    def force_refresh(self) -> None:
        return None


def test_session_render_failure_outputs_visible_error_frame(monkeypatch) -> None:
    stream = io.StringIO()
    session = InkSession(
        model=AppModel(),
        build_tree=lambda model, width: h(Text, None, "x"),
        width_cache=_WidthCache(),
        stream=stream,
    )

    def _boom(*_args, **_kwargs):
        raise RuntimeError("layout exploded")

    monkeypatch.setattr(session._reconciler, "render", _boom)
    session._render_frame()

    output = stream.getvalue()
    assert "render error" in output
    assert "RuntimeError" in output
    assert "layout exploded" in output
