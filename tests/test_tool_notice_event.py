"""工具通知（▎通知 块）链路验证测试（2026-08-18）。

工具的警告/提示输出改用「▎通知」通知块显示（用户需求：与 Ctrl+B 空模式
切换通知 ``+ 主 Agent 已进入空模式`` 同款），且入口收敛到 ``Func`` 基类
——**所有工具通用**：

  ``Func._publish_tool_notice(text)``                     （工具层通用入口）
    → ``ToolNoticeEvent``（EventBus，label/tool_id 归属当前工具上下文）
    → ``EventDispatcher._on_tool_notice``（过滤：仅主 agent / 非 subagent
      label / 非 assistant 兜底 / 非空文本）
    → ``NotificationCmd``
    → ``apply._do_notification``（``▎通知`` 角色头 + ``  │ + ...`` 行）

本文件验证：事件定义、发布入口、dispatcher 路由过滤、read_file 三处警告
迁移、渲染端块结构。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.tools.base import Func
from src.tui._dispatcher import EventDispatcher
from src.tui._const import NotificationCmd, RenderCommand


# ── 测试辅助 ─────────────────────────────────────────────

class _Capture:
    """捕获 emit(event) 调用（monkeypatch src.tui.events.publish.emit）。"""

    def __init__(self):
        self.events: list = []

    def __call__(self, event, *, bus=None):
        self.events.append(event)


@pytest.fixture()
def capture_emit(monkeypatch):
    cap = _Capture()
    import src.tui.events.publish as publish_mod
    monkeypatch.setattr(publish_mod, "emit", cap)
    return cap


def _make_dispatcher() -> tuple[EventDispatcher, list]:
    """构造 dispatcher + 命令收集列表。"""
    cmds: list = []
    disp = EventDispatcher(push_cmd=cmds.append, main_label="main")
    return disp, cmds


# ── 1. 事件定义 ──────────────────────────────────────────

def test_tool_notice_event_fields():
    """ToolNoticeEvent 字段与默认值（label/text/tool_id）。"""
    from src.tui.events import ToolNoticeEvent

    ev = ToolNoticeEvent(label="t1", text="+ 警告：x", tool_id="t1", source="agent")
    assert ev.label == "t1"
    assert ev.text == "+ 警告：x"
    assert ev.tool_id == "t1"
    assert ev.source == "agent"
    # 默认值
    ev2 = ToolNoticeEvent()
    assert ev2.label == "" and ev2.text == "" and ev2.tool_id == ""
    # frozen dataclass
    with pytest.raises(Exception):
        ev.text = "other"


def test_tool_notice_event_registered():
    """ToolNoticeEvent 在 ALL_EVENT_TYPES 注册表中且被包导出。"""
    from src.tui.events import ALL_EVENT_TYPES, ToolNoticeEvent
    import src.tui.events as events_pkg

    assert ToolNoticeEvent in ALL_EVENT_TYPES
    assert events_pkg.ToolNoticeEvent is ToolNoticeEvent
    assert "ToolNoticeEvent" in events_pkg.__all__


# ── 2. Func._publish_tool_notice（所有工具通用入口） ─────

def test_publish_tool_notice_emits_event(capture_emit):
    """发布 ToolNoticeEvent，text 统一补 '+ ' 前缀。"""
    Func._publish_tool_notice("警告：start_line (400) 大于 end_line (380)，已自动交换", "call_1")
    assert len(capture_emit.events) == 1
    ev = capture_emit.events[0]
    from src.tui.events import ToolNoticeEvent
    assert isinstance(ev, ToolNoticeEvent)
    assert ev.label == "call_1"
    assert ev.tool_id == "call_1"
    assert ev.source == "agent"
    assert ev.text == "+ 警告：start_line (400) 大于 end_line (380)，已自动交换"


def test_publish_tool_notice_no_duplicate_prefix(capture_emit):
    """text 已带 '+ ' 前缀时不重复添加。"""
    Func._publish_tool_notice("+ 主 Agent 已进入空模式", "call_2")
    ev = capture_emit.events[0]
    assert ev.text == "+ 主 Agent 已进入空模式"


def test_publish_tool_notice_empty_text_skipped(capture_emit):
    """空文本/纯换行文本跳过（rstrip 剥光尾换行后为空），不发布事件。"""
    Func._publish_tool_notice("", "call_3")
    Func._publish_tool_notice("\n\n", "call_3")
    assert capture_emit.events == []


def test_publish_tool_notice_resolves_context_tool_id(capture_emit, monkeypatch):
    """tool_id 缺省时从当前工具上下文（contextvar）解析归属。"""
    from src.core.internal.agent._tool_context import (
        set_current_tool_id, reset_current_tool_id,
    )
    token = set_current_tool_id("call_ctx")
    try:
        Func._publish_tool_notice("警告：x")
    finally:
        reset_current_tool_id(token)
    ev = capture_emit.events[0]
    assert ev.label == "call_ctx" and ev.tool_id == "call_ctx"


def test_publish_tool_notice_exception_swallowed(capture_emit, monkeypatch):
    """emit 抛异常时吞掉不崩溃（与 _publish_tool_text 同模式）。"""
    def _boom(event, *, bus=None):
        raise RuntimeError("bus down")

    monkeypatch.setattr("src.tui.events.publish.emit", _boom)
    Func._publish_tool_notice("警告：y", "call_4")  # 不应抛出


def test_publish_tool_notice_available_on_all_tool_funcs():
    """通用性：任意工具子类（Func 后代）均可用 _publish_tool_notice。"""
    from src.tools import ReadFile, BashOpt

    for cls in (ReadFile, BashOpt):
        assert callable(getattr(cls, "_publish_tool_notice", None))
        # 继承自 Func 基类（同一实现，无子类覆盖）
        assert cls._publish_tool_notice is Func._publish_tool_notice


# ── 3. dispatcher 路由（EventDispatcher._on_tool_notice） ─

def test_dispatcher_routes_notice_to_notification_cmd():
    """主 agent 工具通知 → NotificationCmd（▎通知 块）。"""
    from src.tui.events import ToolNoticeEvent

    disp, cmds = _make_dispatcher()
    disp._on_tool_notice(ToolNoticeEvent(
        label="call_9", tool_id="call_9", text="+ 警告：已自动交换", source="agent",
    ))
    assert len(cmds) == 1
    cmd = cmds[0]
    assert isinstance(cmd, NotificationCmd)
    assert cmd.cid == RenderCommand.NOTIFICATION
    assert cmd.text == "+ 警告：已自动交换"


def test_dispatcher_notice_filters_subagent_label():
    """subagent 工具（label/tool_id agent- 前缀）通知不进主聊天区。"""
    from src.tui.events import ToolNoticeEvent

    disp, cmds = _make_dispatcher()
    disp._on_tool_notice(ToolNoticeEvent(
        label="agent-1", tool_id="agent-1", text="+ 警告：x", source="agent",
    ))
    disp._on_tool_notice(ToolNoticeEvent(
        label="call_a", tool_id="agent-2", text="+ 警告：y", source="agent",
    ))
    assert cmds == []


def test_dispatcher_notice_filters_non_agent_source_and_assistant():
    """非 agent source 与 assistant 兜底归属均过滤。"""
    from src.tui.events import ToolNoticeEvent

    disp, cmds = _make_dispatcher()
    disp._on_tool_notice(ToolNoticeEvent(label="t", text="+ a", source=""))
    disp._on_tool_notice(ToolNoticeEvent(label="t", text="+ b", source="parallel"))
    disp._on_tool_notice(ToolNoticeEvent(
        label="assistant", tool_id="assistant", text="+ c", source="agent",
    ))
    assert cmds == []


def test_dispatcher_notice_skips_empty_text():
    """空文本（含尾换行剥光后为空）不产命令。"""
    from src.tui.events import ToolNoticeEvent

    disp, cmds = _make_dispatcher()
    disp._on_tool_notice(ToolNoticeEvent(
        label="t", text="\n", source="agent",
    ))
    assert cmds == []


def test_dispatcher_notice_multiline_text_single_cmd():
    """含换行文本整条透传（_do_notification 按 \\n 拆行渲染）。"""
    from src.tui.events import ToolNoticeEvent

    disp, cmds = _make_dispatcher()
    disp._on_tool_notice(ToolNoticeEvent(
        label="t", text="+ 第一行\n+ 第二行", source="agent",
    ))
    assert len(cmds) == 1
    assert cmds[0].text == "+ 第一行\n+ 第二行"


def test_dispatcher_registered_in_handler_table():
    """ToolNoticeEvent 已注册进 list_handlers（装配订阅生效）。"""
    from src.tui.events import ToolNoticeEvent

    disp, _ = _make_dispatcher()
    handlers = disp.list_handlers()
    assert ToolNoticeEvent in handlers
    assert handlers[ToolNoticeEvent] == disp._on_tool_notice


# ── 4. read_file 警告迁移 ────────────────────────────────

def test_read_file_swap_warning_publishes_notice(capture_emit, monkeypatch):
    """start_line > end_line：警告走 ToolNoticeEvent（▎通知），不再进工具卡。"""
    from src.tools import ReadFile

    monkeypatch.setattr(Func, "_publish_tool_text", lambda *a, **k: pytest.fail("不应走工具卡输出"))
    inst = ReadFile.from_args({
        "path": "src/tools/read_file.py",
        "start_line": 400, "end_line": 380,
    })
    assert inst.start_line == 380 and inst.end_line == 400
    assert len(capture_emit.events) == 1
    ev = capture_emit.events[0]
    from src.tui.events import ToolNoticeEvent
    assert isinstance(ev, ToolNoticeEvent)
    assert "start_line (400) 大于 end_line (380)" in ev.text
    assert ev.text.startswith("+ ")


def test_read_file_validate_warning_uses_notice(capture_emit):
    """行号 <1 自动调整为 1 的警告同样走通知块。"""
    from src.tools import ReadFile

    inst = ReadFile.from_args({"path": "x.py", "start_line": 0, "end_line": 5})
    assert inst.start_line == 1
    assert len(capture_emit.events) == 1
    assert "start_line 必须 >= 1" in capture_emit.events[0].text


def test_read_file_validate_non_int_ignored_uses_notice(capture_emit):
    """非整数行号忽略参数的警告同样走通知块。"""
    from src.tools import ReadFile

    inst = ReadFile.from_args({"path": "x.py", "start_line": "abc"})
    assert inst.start_line is None
    assert len(capture_emit.events) == 1
    assert "应为整数" in capture_emit.events[0].text


def test_read_file_normal_args_no_notice(capture_emit):
    """正常参数（含合法行号范围）不产生任何通知。"""
    from src.tools import ReadFile

    inst = ReadFile.from_args({"path": "x.py", "start_line": 10, "end_line": 20})
    assert inst.start_line == 10 and inst.end_line == 20
    assert capture_emit.events == []


# ── 5. 渲染端（▎通知 块结构固化） ────────────────────────

class _FakeModel:
    """apply._do_notification 最小模型桩：记录 append_committed 调用。"""

    def __init__(self):
        self.committed: list[tuple[str, list]] = []

    def append_committed(self, kind: str, lines: list) -> None:
        self.committed.append((kind, lines))


def test_apply_notification_renders_notice_block():
    """NotificationCmd → 'notification' 块：每行 '  │ ' 前缀（▎通知 头由角色头渲染）。"""
    from src.tui._const import NotificationCmd
    from src.tui.app.apply import apply_cmd

    model = _FakeModel()
    apply_cmd(model, NotificationCmd(text="+ 警告：start_line (400) 大于 end_line (380)，已自动交换"))
    assert len(model.committed) == 1
    kind, lines = model.committed[0]
    assert kind == "notification"
    assert len(lines) == 1
    line = lines[0]
    assert line.runs[0].text == "  │ "
    assert "警告：start_line (400) 大于 end_line (380)，已自动交换" in line.runs[1].text


def test_role_header_notification_is_notice_label():
    """notification 块角色头固化「▎通知」（与空模式通知同款）。"""
    from src.tui.app._model_helpers import _role_header_runs

    block = SimpleNamespace(kind="notification", closed=True)
    runs = _role_header_runs(block, live=False)
    assert "".join(r.text for r in runs) == "▎通知"
