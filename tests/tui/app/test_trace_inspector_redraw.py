"""轨迹视图右栏检查器按键重绘回归测试（2026-10-23）。

背景：``TraceView`` 在视图注册表中声明 ``idle_static=True``（空闲帧复用），
输入处理直接改 model 字段（``trace_inspector_cursor`` / ``scroll`` 等）不会
像 ``use_state`` 那样请求重绘——修复前右栏 j/k 移动只有 1s 定时刷新才上屏
（用户反馈「右边界面按 jk 移动很卡」）。台账经 ListView 内部 use_state
触发重绘，故只有右栏卡。

覆盖：
- 检查器焦点 j / ↓ 消费按键后必须请求重绘（schedule 回调被调用），且光标
  实际移动；
- 非导航类视图态变更（h 返回台账）同样立即请求重绘。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.renderer.ansi.helpers import AnsiLine
from src.tui.app.model import AppModel, ChatBlock
from src.tui.app.trace_view import TraceView
from src.tui.ink import h, hooks as _hooks
from src.tui.ink.reconciler import Reconciler

_WIDTH = 120
_HEIGHT = 45


def _model(pane: str = "inspector") -> AppModel:
    model = AppModel()
    model.trace_open = True
    model.trace_pane = pane
    model.trace_selected = 0
    model.blocks = [
        ChatBlock(
            kind="content",
            lines=[AnsiLine.of(f"line {i}") for i in range(40)],
            closed=True,
        ),
    ]
    return model


def _render(reconciler: Reconciler, root, model: AppModel) -> None:
    reconciler.render(
        root, h(TraceView, {"model": model, "width": _WIDTH}), _WIDTH, _HEIGHT,
    )


def _harness(model: AppModel):
    redraws: list = []
    routers: list = []
    reconciler = Reconciler(lambda: redraws.append(1))
    reconciler.hook_context.input_router_callback = lambda router: routers.append(router)
    root = Reconciler.create_root()
    _render(reconciler, root, model)
    assert routers, "TraceView 未发布输入 router"
    return reconciler, root, routers[-1], redraws


def _route(reconciler: Reconciler, router, event) -> bool:
    """模拟会话输入阶段（渲染线程激活本会话 HookContext）后分发按键。"""
    _hooks.push_context(reconciler.hook_context)
    try:
        return bool(router(event))
    finally:
        _hooks.pop_context()


def test_inspector_j_requests_redraw_and_moves_cursor() -> None:
    model = _model("inspector")
    reconciler, _root, router, redraws = _harness(model)
    assert redraws == []

    handled = _route(
        reconciler, router, SimpleNamespace(kind="char", char="j", modifier=0),
    )
    assert handled is True
    assert model.trace_inspector_cursor == 1
    assert redraws, "检查器 j 消费后必须请求重绘（否则空闲复用等 1s 才上屏）"


def test_inspector_arrow_down_requests_redraw() -> None:
    model = _model("inspector")
    model.trace_inspector_cursor = 3
    reconciler, _root, router, redraws = _harness(model)

    handled = _route(
        reconciler, router, SimpleNamespace(kind="arrow_down", modifier=0),
    )
    assert handled is True
    assert model.trace_inspector_cursor == 4
    assert redraws, "检查器 ↓ 消费后必须请求重绘"


def test_inspector_pane_switch_requests_redraw() -> None:
    """非导航类视图态变更（h 返回台账）同样须立即重绘。"""
    model = _model("inspector")
    reconciler, _root, router, redraws = _harness(model)

    handled = _route(
        reconciler, router, SimpleNamespace(kind="char", char="h", modifier=0),
    )
    assert handled is True
    assert model.trace_pane == "ledger"
    assert redraws, "面板切换消费后必须请求重绘"
