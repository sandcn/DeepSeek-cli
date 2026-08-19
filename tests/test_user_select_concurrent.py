"""user_select 并发 + tab 切换测试（2026-08-19 用户需求）。

需求：user_select 工具可以并发（parallel_safe=True），多个并发弹窗**全部
一起显示**、按 Tab 切换（参考 Claude Code AskUserQuestion 多问题 tab 界面）：

    [×] 测试1:语言  [ ] 测试2:优先级  [ ] 测试3:工作流  [ ] 测试4:UI  √ Submit
    测试2:这个项目接下来最想优先做什么？

    > 1. 修 Bug
         先处理现有的问题
     ...

协议：
  - 每个并发 user_select 工具调用 append 一个 ``UserSelectState`` 到
    ``model.user_selects`` 并发队列（真源）+ 同步兼容字段 ``model.user_select``；
  - ``UserSelectPopup`` 读取队列以 tab 形式全部显示；Tab/←/→ 切换焦点；
  - Enter 确认当前 tab（try_set_final first-write-wins）→ 该工具协程返回，
    已完成 tab **保留显示**（[×] 标记）；当**全部**问题 done 时（最后一个
    完成的协程）统一清空列表 + 关闭 bottom_view；
  - ToolDAG ``_add_user_select_constraints`` 不再强制 user_select 串行化
    （同层可并发）。
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from src.tui.app.model import AppModel, UserSelectState
from src.tui.app.user_select import UserSelectPopup
from src.tui.ink import h, TEXT
from src.tui.ink import hooks
from src.tui.ink.fiber import Fiber, InputHook, TAG_FUNCTION
from src.tui.ink.reconciler import Reconciler


# ── 测试辅助 ──────────────────────────────────────────────

class _FakeInput:
    def flush_stdin_buffer(self):
        pass


class _FakeBottomBar:
    is_completion_visible = False


class _FakeChatUI:
    """最小 ChatUIConsumer 桩（get_model / request_bottom_redraw 协议）。"""

    def __init__(self, model):
        self._model = model
        self.bottom_bar = _FakeBottomBar()

    def get_model(self):
        return self._model

    def get_input_component(self):
        return _FakeInput()

    def request_bottom_redraw(self):
        pass

    def flush_input_router(self, _timeout=2.0):
        pass


def _render_component(component, model, fiber=None):
    """在手动 fiber 上下文渲染弹窗组件（返回 fiber + 元素树）。"""
    if fiber is None:
        fiber = Fiber(TAG_FUNCTION, component, {"model": model, "width": 80})
    else:
        fiber.reset_hooks()
    hooks._push_current(fiber)
    try:
        el = component({"model": model, "width": 80})
    finally:
        hooks._pop_current()
    return fiber, el


def _top_input_handler(fiber):
    """顶层 fiber 的第一个 InputHook handler（UserSelectPopup 的 tab 切换）。"""
    for hook in fiber.hooks:
        if isinstance(hook, InputHook) and hook.is_active and hook.handler is not None:
            return hook.handler
    return None


def _tab_titles(el) -> list:
    """tab 栏（children[0] Row）中各 tab 文本列表。"""
    tab_bar = el.children[0]
    out = []
    for child in tab_bar.children:
        txt = child.props.get("children", "")
        if isinstance(txt, str) and txt.strip():
            out.append(txt)
    return out


def _title(el) -> str:
    """弹窗标题行文本（按 key="us-title" 定位，tab 栏/单 tab 结构通用）。"""
    for c in el.children:
        if c.props.get("key") == "us-title":
            return c.props["children"]
    return ""


def _has_done_result(el) -> bool:
    """弹窗是否渲染了只读结果行（key="us-done-result"，已完成 tab）。"""
    for c in el.children:
        if c.props.get("key") == "us-done-result":
            return True
    return False


def _tab_key(ev_kind: str, modifier: int = 0):
    """构造按键事件（对齐 KeyEvent 属性访问语义）。"""
    return SimpleNamespace(kind=ev_kind, char="", modifier=modifier, keycode=0, raw=b"")


def _make_tool_env(monkeypatch):
    """构造 user_select 工具执行环境（终端/非交互/ ChatUI 桩）。"""
    from src.tools import user_select as us_mod

    monkeypatch.setattr(us_mod, "HAS_TERMIOS", True)
    monkeypatch.setattr(us_mod.os, "isatty", lambda fd: True)

    class _FakeStdin:
        def fileno(self):
            return 0

    class _FakeSys:
        def __init__(self, stdin):
            self.stdin = stdin

    monkeypatch.setattr(us_mod, "sys", _FakeSys(_FakeStdin()))
    model = AppModel()
    fake = _FakeChatUI(model)
    monkeypatch.setattr(us_mod, "get_active_chat_ui", lambda: fake)
    return us_mod, model, fake


# ── 1. 元数据：parallel_safe=True（可并发调度） ───────────

def test_user_select_parallel_safe():
    """user_select 元数据 parallel_safe=True（ToolDAG 同层可并发）。"""
    from src.tools.user_select import UserSelectFunc
    meta = UserSelectFunc.get_metadata()
    assert meta is not None
    assert meta.parallel_safe is True


def test_tool_dag_user_select_same_layer():
    """ToolDAG：多个 user_select 不再串行化——同一层可并发执行。

    修复前 ``_add_user_select_constraints`` 在 user_select 间加链式依赖
    （各占一层）；修复后同层并行（各自独立 tab 等待用户）。
    """
    from src.tools.registry import ToolRegistry
    from src.core.tool_dag import ToolDAG

    calls = [
        {"id": "call_us1", "name": "user_select", "arguments": {"title": "问题1", "options": ["A", "B"]}},
        {"id": "call_us2", "name": "user_select", "arguments": {"title": "问题2", "options": ["X", "Y"]}},
        {"id": "call_read", "name": "read_file", "arguments": {"path": "a.py"}},
    ]
    dag = ToolDAG(calls, ToolRegistry.default())
    layers = dag.topological_sort()
    assert layers is not None
    # user_select 同层（find 包含两个 user_select 的层）
    us_layer = None
    for layer in layers:
        names = {dag.get_node(t).name for t in layer}
        if "user_select" in names:
            us_layer = layer
            break
    assert us_layer is not None
    us_ids = [t for t in us_layer if dag.get_node(t).name == "user_select"]
    assert len(us_ids) == 2, f"两个 user_select 应同层并发: {us_layer}"
    # read_file 在 user_select 之后（独占层约束保持）
    read_layer_idx = next(
        i for i, layer in enumerate(layers)
        if any(dag.get_node(t).name == "read_file" for t in layer)
    )
    us_layer_idx = next(
        i for i, layer in enumerate(layers)
        if any(dag.get_node(t).name == "user_select" for t in layer)
    )
    assert read_layer_idx > us_layer_idx


# ── 2. 工具并发执行（队列协议） ──────────────────────────

@pytest.mark.asyncio
async def test_concurrent_two_tools_append_and_close(monkeypatch):
    """两个 user_select 并发执行：各自 append 队列、确认后返回、全部完成关闭。

    行为链：
      1. 两协程并发打开 → ``model.user_selects`` 长度 2（tab 一起显示）；
      2. 确认 us1 → 协程1 返回（selected=["A"]）→ **us1 保留**（[×] 标记，
         bottom_view 仍 "user_select"）；
      3. 确认 us2 → 协程2 返回 → 全部 done → 队列清空 + bottom_view 复位。
    """
    us_mod, model, _fake = _make_tool_env(monkeypatch)
    from src.tools.user_select import UserSelectFunc

    async def fake_sleep(_sec):
        # 每轮 sleep 确认一个尚未 done 的问题（模拟用户在 tab 上 Enter）
        for s in model.user_selects:
            if not s.done:
                s.try_set_final("confirmed", ["A"] if s.title == "问题1" else ["X"])
                return

    monkeypatch.setattr(us_mod.asyncio, "sleep", fake_sleep)

    func1 = UserSelectFunc(title="问题1", options=["A", "B"], timeout=120)
    func2 = UserSelectFunc(title="问题2", options=["X", "Y"], timeout=120)

    r1, r2 = await asyncio.gather(func1.execute(), func2.execute())
    d1 = json.loads(r1)
    d2 = json.loads(r2)
    assert d1["action"] == "confirmed"
    assert d1["selected"] == ["A"]
    assert d2["selected"] == ["X"]
    # 全部完成后：队列清空 + bottom_view 复位 + 兼容字段复位
    assert model.user_selects == []
    assert model.bottom_view == ""
    assert not model.user_select.visible


@pytest.mark.asyncio
async def test_first_done_tab_kept_until_all_done(monkeypatch):
    """第一个确认的 tab 保留显示（[×]）——直到全部完成才整体关闭。

    用真实轮询 sleep（不替换）：分阶段手动确认，精确断言中间状态——
    us1 确认后其协程返回，但 us1 **保留在队列**（[×] 标记显示）、bottom_view
    仍激活；us2 确认后全部完成 → 队列清空 + bottom_view 复位。
    """
    us_mod, model, _fake = _make_tool_env(monkeypatch)
    from src.tools.user_select import UserSelectFunc

    func1 = UserSelectFunc(title="问题1", options=["A", "B"], timeout=120)
    func2 = UserSelectFunc(title="问题2", options=["X", "Y"], timeout=120)

    t1 = asyncio.ensure_future(func1.execute())
    await asyncio.sleep(0.15)  # us1 已打开并进入轮询
    t2 = asyncio.ensure_future(func2.execute())
    await asyncio.sleep(0.15)  # us2 已打开并进入轮询
    assert len(model.user_selects) == 2, "两个并发问题应同时挂起（tab 一起显示）"

    # 阶段1：确认 us1 → t1 完成返回；us1 **保留**（[×] 标记），弹窗不关闭
    model.user_selects[0].try_set_final("confirmed", ["A"])
    await asyncio.wait_for(t1, 5)
    assert json.loads(t1.result())["selected"] == ["A"]
    assert len(model.user_selects) == 2, "us1 确认后应保留显示（[×]）"
    assert model.user_selects[0].done
    assert not model.user_selects[1].done
    assert model.bottom_view == "user_select", "尚有未完成问题，弹窗不应关闭"

    # 阶段2：确认 us2 → t2 完成返回；全部完成 → 队列清空 + bottom_view 复位
    model.user_selects[1].try_set_final("confirmed", ["X"])
    await asyncio.wait_for(t2, 5)
    assert json.loads(t2.result())["selected"] == ["X"]
    assert model.user_selects == [], "全部完成后队列清空"
    assert model.bottom_view == ""
    assert not model.user_select.visible


@pytest.mark.asyncio
async def test_tools_wait_until_submit_before_return(monkeypatch):
    """多问题端到端：回答（mark_answered）**不返回**，Submit 统一提交才返回。

    新协议（2026-08-19）：Enter 确认仅标记 answered（可重答），工具协程
    继续等待；Submit 页 Enter 统一置 done → 所有协程一起返回各自结果。
    """
    us_mod, model, _fake = _make_tool_env(monkeypatch)
    from src.tools.user_select import UserSelectFunc

    func1 = UserSelectFunc(title="问题1", options=["A", "B"], timeout=120)
    func2 = UserSelectFunc(title="问题2", options=["X", "Y"], timeout=120)

    t1 = asyncio.ensure_future(func1.execute())
    await asyncio.sleep(0.15)
    t2 = asyncio.ensure_future(func2.execute())
    await asyncio.sleep(0.15)
    assert len(model.user_selects) == 2

    # 模拟用户回答问题（组件 mark_answered）——协程**不应**返回（等待 Submit）
    us1 = model.user_selects[0]
    us2 = model.user_selects[1]
    assert us1.mark_answered("confirmed", ["A"]) is True
    assert us2.mark_answered("confirmed", ["X"]) is True
    await asyncio.sleep(0.15)
    assert not t1.done(), "回答后协程应继续等待 Submit（可重答）"
    assert not t2.done()
    assert not us1.done and not us2.done

    # 模拟 Submit 页 Enter：统一提交（try_set_final 置 done）
    us1.try_set_final(us1.action or "confirmed", list(us1.result or []))
    us2.try_set_final(us2.action or "confirmed", list(us2.result or []))
    await asyncio.wait_for(t1, 5)
    await asyncio.wait_for(t2, 5)
    assert json.loads(t1.result())["selected"] == ["A"]
    assert json.loads(t2.result())["selected"] == ["X"]
    assert model.user_selects == [], "提交后全部完成 → 队列清空"
    assert model.bottom_view == ""


# ── 3. 组件多 tab 渲染 ───────────────────────────────────

def test_popup_multi_tab_render_two_questions():
    """两个并发问题：组件渲染 tab 栏（[ ] 问题1 [ ] 问题2）+ 当前问题标题。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(
            visible=True, seq=1, title="问题1", options=["A", "B"],
        ),
        UserSelectState(
            visible=True, seq=2, title="问题2", options=["X", "Y"],
            option_descriptions=["说明X", "说明Y"],
        ),
    ]
    model.user_select = model.user_selects[0]
    _, el = _render_component(UserSelectPopup, model)
    # 多 tab：children[0] = tab 栏（Row），children[1] = 标题
    titles = _tab_titles(el)
    assert any("问题1" in t and "[ ]" in t for t in titles), titles
    assert any("问题2" in t and "[ ]" in t for t in titles), titles
    # 当前焦点（active=0）是问题1：标题行显示问题1
    assert "问题1" in _title(el)
    assert "(1/2)" in _title(el)


def test_popup_tab_switch_by_tab_key():
    """Tab 键切换焦点问题：标题从问题1 切到问题2。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    assert "问题1" in _title(el)

    handler = _top_input_handler(fiber)
    assert handler is not None, "顶层 tab 切换 use_input handler 未注册"
    # Tab 键 → 下一个 tab
    assert handler(_tab_key("tab", 0)) is True
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el)
    assert "(1/2)" in _title(el)

    # Shift+Tab（modifier=2）→ 回到问题1
    assert handler(_tab_key("tab", 2)) is True
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题1" in _title(el)


def test_popup_tab_switch_by_arrow_keys():
    """←/→ 键切换焦点问题（与 Tab 等价）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    handler = _top_input_handler(fiber)
    assert handler(_tab_key("arrow_right", 0)) is True
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el)
    assert handler(_tab_key("arrow_left", 0)) is True
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题1" in _title(el)


def test_popup_done_tab_marked_and_readonly():
    """已完成 tab：标记 [×] + 只读显示已选结果（无交互控件）。"""
    model = AppModel()
    us1 = UserSelectState(
        visible=True, seq=1, title="问题1", options=["A", "B"], done=True,
        action="confirmed", result=["A"],
    )
    us2 = UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"])
    model.user_selects = [us1, us2]
    model.user_select = us1
    # 初始焦点 active=0 → 问题1（已完成）只读
    _, el = _render_component(UserSelectPopup, model)
    titles = _tab_titles(el)
    assert any("[×]" in t and "问题1" in t for t in titles), titles
    assert any("[ ]" in t and "问题2" in t for t in titles), titles
    # 标题行显示已选结果；只读结果行渲染（无交互控件）
    assert "已选择: A" in _title(el)
    assert _has_done_result(el)

    # Tab 切到问题2（未完成）→ 可交互控件出现
    fiber, el2 = _render_component(UserSelectPopup, model)
    handler = _top_input_handler(fiber)
    handler(_tab_key("tab", 0))
    fiber, el2 = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el2)


def test_popup_all_done_shows_submit():
    """全部完成：tab 栏显示 [✓ 提交]（Submit tab，供玩家确认提交）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(
            visible=True, seq=1, title="问题1", options=["A", "B"],
            done=True, action="confirmed", result=["A"],
        ),
        UserSelectState(
            visible=True, seq=2, title="问题2", options=["X", "Y"],
            done=True, action="confirmed", result=["X"],
        ),
    ]
    model.user_select = model.user_selects[0]
    _, el = _render_component(UserSelectPopup, model)
    titles = _tab_titles(el)
    assert any("[✓ 提交]" in t for t in titles), titles
    assert all("[×]" in t for t in titles if "问题" in t), titles


def test_popup_single_tab_no_tab_bar():
    """单问题（非并发）：不渲染 tab 栏（零额外行，与旧版一致）。"""
    model = AppModel()
    model.user_selects = []
    model.user_select = UserSelectState(
        visible=True, seq=3, title="单问题", options=["A", "B"],
    )
    _, el = _render_component(UserSelectPopup, model)
    # children[0] 直接是标题行（无 tab 栏）
    assert "单问题" in el.children[0].props["children"]
    assert "(1/2)" in el.children[0].props["children"]


def test_popup_compat_single_user_select_fallback():
    """兼容：user_selects 为空时回退 model.user_select 单例（旧调用/测试）。"""
    model = AppModel()
    model.user_select = UserSelectState(
        visible=True, seq=5, title="兼容问题", options=["A", "B"],
    )
    _, el = _render_component(UserSelectPopup, model)
    assert "兼容问题" in _title(el)
    assert "(1/2)" in _title(el)


def test_popup_control_key_includes_active_for_multi():
    """多 tab：控件 key 含 active 后缀——tab 切换强制控件重挂载（重置选中）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[0]
    _, el = _render_component(UserSelectPopup, model)
    # children[0]=tab栏, children[1]=标题, children[2]=控件
    control = el.children[2]
    assert control.props.get("key") == "us-select-1-0"


# ── 4. 回车自动切换下一个未选择（2026-08-19 用户需求） ───

def _confirm_current(el, value: str = "A") -> None:
    """模拟在单选控件上按 Enter（onSelect 回调，选中 value）。"""
    control = el.children[2]
    control.props["onSelect"]({"label": value, "value": value})


def test_enter_auto_advances_to_next_pending():
    """回车确认当前问题 → 焦点自动切到下一个未选择的问题（不提交）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
        UserSelectState(visible=True, seq=3, title="问题3", options=["M", "N"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    assert "问题1" in _title(el)
    # Enter 确认问题1 → 自动切到问题2（answered 标记，未提交 done）
    _confirm_current(el)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert model.user_selects[0].answered
    assert not model.user_selects[0].done, "多问题 Enter 仅标记回答，等待 Submit"
    assert "问题2" in _title(el), "回车后应自动切到下一个未选择的问题"
    # 再确认问题2 → 自动切到问题3
    _confirm_current(el)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert model.user_selects[1].answered
    assert "问题3" in _title(el)


def test_enter_auto_advance_skips_done():
    """回车确认后自动跳过已完成的问题（跳到下一个未选择）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(
            visible=True, seq=2, title="问题2", options=["X", "Y"],
            done=True, action="confirmed", result=["X"],
        ),
        UserSelectState(visible=True, seq=3, title="问题3", options=["M", "N"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    assert "问题1" in _title(el)
    _confirm_current(el)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    # 问题2 已 done → 应跳过直接到问题3
    assert "问题3" in _title(el), "应跳过已完成的问题2，切到问题3"


def test_enter_no_advance_when_all_done():
    """全部回答后回车确认最后一个 → 焦点自动切到 Submit 页（提交前可重答）。"""
    model = AppModel()
    us1 = UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"])
    us2 = UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"])
    model.user_selects = [us1, us2]
    model.user_select = us2  # 兼容字段指向最近
    us1.done = True
    us1.action = "confirmed"
    us1.result = ["A"]
    fiber, el = _render_component(UserSelectPopup, model)
    # 焦点初始 active=0（问题1 已 done）——Tab 切到问题2
    handler = _top_input_handler(fiber)
    handler(_tab_key("tab", 0))
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el)
    _confirm_current(el)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    # 全部 answered → 焦点自动切到 Submit 页
    assert us2.answered
    assert "提交全部答案" in _submit_title(el), "全部回答后应切到 Submit 页"


def test_cancel_auto_advances():
    """Esc 取消当前问题 → 同样自动切到下一个未选择的问题。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    assert "问题1" in _title(el)
    # Esc 取消问题1 → 自动切到问题2
    control = el.children[2]
    control.props["onCancel"]({"label": "A", "value": "A"})
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert model.user_selects[0].action == "cancel"
    assert model.user_selects[0].answered
    assert "问题2" in _title(el), "取消后也应自动切到下一个未选择的问题"


# ── 5. 已答可重答 + Submit 页（2026-08-19 用户需求） ─────

def _submit_title(el) -> str:
    """Submit 页标题行文本（key="us-submit-title"）。"""
    for c in el.children:
        if c.props.get("key") == "us-submit-title":
            return c.props["children"]
    return ""


def _submit_rows(el) -> list:
    """Submit 页各问题汇总行（key 前缀 us-submit-row-）。"""
    out = []
    for c in el.children:
        key = c.props.get("key", "")
        if isinstance(key, str) and key.startswith("us-submit-row-"):
            out.append(c.props["children"])
    return out


def test_answered_can_reanswer():
    """已经回答的可以重新答：切回已答 tab 重新导航 + Enter 覆盖旧答案。"""
    model = AppModel()
    us1 = UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"])
    us2 = UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"])
    model.user_selects = [us1, us2]
    model.user_select = us1
    fiber, el = _render_component(UserSelectPopup, model)
    # 回答问题1（选 A）
    _confirm_current(el)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert us1.answered and us1.result == ["A"]
    # 切回问题1（Tab 后退）——仍渲染可交互控件（可重答）
    handler = _top_input_handler(fiber)
    handler(_tab_key("tab", 2))  # Shift+Tab 后退
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题1" in _title(el)
    assert not _has_done_result(el), "已答未提交的 tab 仍可交互（重答）"
    # 重新导航到 B 并 Enter → 覆盖旧答案
    control = el.children[2]
    control.props["onHighlight"](1)
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    control = el.children[2]
    control.props["onSelect"]({"label": "B", "value": "B"})
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert us1.result == ["B"], "重答应覆盖旧答案"
    assert us1.action == "confirmed"


def test_advance_to_submit_when_all_answered():
    """全部回答后自动切到 Submit 页（玩家确认是否提交）。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(visible=True, seq=1, title="问题1", options=["A", "B"]),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[0]
    fiber, el = _render_component(UserSelectPopup, model)
    _confirm_current(el, "A")  # 回答问题1
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el)
    _confirm_current(el, "X")  # 回答问题2（最后一个）
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    # 全部 answered → 焦点在 Submit 页
    assert _submit_title(el) and "提交全部答案" in _submit_title(el)
    rows = _submit_rows(el)
    assert len(rows) == 2
    assert "问题1" in rows[0] and "✓ A" in rows[0]
    assert "问题2" in rows[1] and "✓ X" in rows[1]


def _tab_to_submit(fiber, el, model):
    """通过 ←/→ 按键切到 Submit 页（每次按键后重渲染刷新 handler 闭包）。"""
    handler = _top_input_handler(fiber)
    handler(_tab_key("arrow_right", 0))
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    handler = _top_input_handler(fiber)
    handler(_tab_key("arrow_right", 0))
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    return fiber, el


def test_submit_page_enter_commits_all():
    """Submit 页 Enter：统一提交全部问题（done 置位，各自结果保留）。"""
    model = AppModel()
    us1 = UserSelectState(
        visible=True, seq=1, title="问题1", options=["A", "B"],
        answered=True, action="confirmed", result=["A"],
    )
    us2 = UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"])
    model.user_selects = [us1, us2]
    model.user_select = us2
    fiber, el = _render_component(UserSelectPopup, model)
    fiber, el = _tab_to_submit(fiber, el, model)
    assert "提交全部答案" in _submit_title(el)
    # Submit 页 Enter → 全部 done
    handler = _top_input_handler(fiber)
    assert handler(_tab_key("enter", 0)) is True
    assert us1.done and us1.action == "confirmed" and us1.result == ["A"]
    assert us2.done and us2.action == "confirmed" and us2.result == []


def test_submit_unanswered_uses_default():
    """Submit 时未回答的问题用 default_options（未回答即取默认）。"""
    model = AppModel()
    us1 = UserSelectState(
        visible=True, seq=1, title="问题1", options=["A", "B"],
        answered=True, action="confirmed", result=["A"],
    )
    us2 = UserSelectState(
        visible=True, seq=2, title="问题2", options=["X", "Y"],
        default_options=["Y"],
    )
    model.user_selects = [us1, us2]
    model.user_select = us2
    fiber, el = _render_component(UserSelectPopup, model)
    fiber, el = _tab_to_submit(fiber, el, model)
    handler = _top_input_handler(fiber)
    handler(_tab_key("enter", 0))
    assert us2.done and us2.result == ["Y"], "未回答的问题提交时取 default_options"


def test_submit_escape_back_to_questions():
    """Submit 页 Esc：返回问题 tab（最后一个未回答）。"""
    model = AppModel()
    us1 = UserSelectState(
        visible=True, seq=1, title="问题1", options=["A", "B"],
        answered=True, action="confirmed", result=["A"],
    )
    us2 = UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"])
    model.user_selects = [us1, us2]
    model.user_select = us2
    fiber, el = _render_component(UserSelectPopup, model)
    fiber, el = _tab_to_submit(fiber, el, model)
    assert "提交全部答案" in _submit_title(el)
    # Submit 页 Esc → 返回最后一个未回答的问题（问题2）
    handler = _top_input_handler(fiber)
    assert handler(_tab_key("escape", 0)) is True
    fiber, el = _render_component(UserSelectPopup, model, fiber)
    assert "问题2" in _title(el)


def test_submit_page_tab_bar_marks_answered():
    """tab 栏：已答未提交显示 [×]，未回答显示 [ ]，Submit tab 显示 [提交]。"""
    model = AppModel()
    model.user_selects = [
        UserSelectState(
            visible=True, seq=1, title="问题1", options=["A", "B"],
            answered=True, action="confirmed", result=["A"],
        ),
        UserSelectState(visible=True, seq=2, title="问题2", options=["X", "Y"]),
    ]
    model.user_select = model.user_selects[1]
    _, el = _render_component(UserSelectPopup, model)
    titles = _tab_titles(el)
    assert any("[×]" in t and "问题1" in t for t in titles), titles
    assert any("[ ]" in t and "问题2" in t for t in titles), titles
    assert any("[提交]" in t and "[✓ 提交]" not in t for t in titles), titles
