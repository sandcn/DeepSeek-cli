"""内容区定位解耦测试。

覆盖：
  1. app 层 ``find_committed_span`` 在组件树中定位 ``static-lines`` host
     （含 root 缓存复用）；
  2. ``InkSession.set_content_span_provider`` 注入后渲染帧会调用 provider，
     框架不再直接内置聊天语义。
"""

from __future__ import annotations

import io

from src.tui._config import TuiConfig
from src.tui._screen import TerminalWidthCache
from src.tui.app.committed_span import find_committed_span
from src.tui.ink import TEXT, h
from src.tui.ink._render_api import _SimpleModel
from src.tui.ink.fiber import TAG_HOST, TAG_ROOT, Fiber
from src.tui.ink.session import InkSession


def test_find_committed_span_returns_static_lines_host():
    root = Fiber(TAG_ROOT, props={})
    root._committed_chat_present = True
    host = Fiber(TAG_HOST, "static-lines", {"lines": []}, return_=root)
    root.child = host

    assert find_committed_span(root) is host
    # 第二次命中 root 缓存，不再 DFS
    assert find_committed_span(root) is host


def test_find_committed_span_returns_none_without_host():
    root = Fiber(TAG_ROOT, props={})
    root._committed_chat_present = False
    assert find_committed_span(root) is None


def test_session_invokes_content_span_provider():
    def _build_tree(model, width):
        return h(TEXT, {"children": "span"})

    session = InkSession(
        model=_SimpleModel(),
        build_tree=_build_tree,
        stream=io.StringIO(),
        width_cache=TerminalWidthCache(),
        config=TuiConfig.defaults(),
    )
    seen: list = []
    session.set_content_span_provider(lambda root: (seen.append(root), (None, 0))[1])

    session._render_frame()
    assert seen == [session._root_fiber]
