"""工具卡片自动折叠测试（2026-08-15 用户需求）。

需求：工具执行完成后卡片自动折叠为单行（只显示标题行——状态图标 ✔/✖ +
工具名 + 参数），对齐 Claude Code 收起工具结果；``/toolcard`` 命令手动
切换折叠状态（展开查看输出后再次折叠）。

覆盖：
  1. ``close_tool_box`` 完成后自动置 ``block.tool_collapsed``；
  2. 折叠后 committed_lines 只含标题行 + 尾空行；展开后完整内容恢复；
  3. ``set_tool_collapsed`` / ``fold_tool_cards``（最后一张 / all / 按 id）；
  4. ``ToolFoldCmd`` 渲染命令处理（渲染线程执行）；
  5. 增量提交块（长工具输出 > _TOOL_INCREMENTAL_THRESHOLD）折叠重建；
  6. 被夹住块（前面未关闭块）折叠/展开的 live 渲染；
  7. reflow_committed（宽度变化）保持折叠状态；
  8. /toolcard 命令插件参数解析。
"""

from __future__ import annotations

from types import SimpleNamespace

from src.tui.app.model import AppModel
from src.tui.app.apply import apply_cmd
from src.tui.app.chat_view import _block_styled_lines
from src.tui.app.toolcard import tool_card_lines
from src.tui._const import (
    ToolOpenCmd, ToolOutputCmd, ToolCloseCmd, ToolFoldCmd,
    ContentCmd, PhaseDoneCmd,
)


def _plain(lines) -> list:
    """ink Line 列表 → plain 文本列表（含空行）。"""
    return ["" if getattr(l, "plain", "") == "" else l.plain for l in lines]


def _open_tool(model: AppModel, tool_id: str, name: str = "Bash", detail: str = "ls", lines: int = 2) -> None:
    """打开工具卡并追加输出（默认 2 行内容）。"""
    apply_cmd(model, ToolOpenCmd(tool_name=name, tool_id=tool_id, detail=detail))
    for i in range(lines):
        apply_cmd(model, ToolOutputCmd(text=f"out-{i}", tool_id=tool_id))


# ── close_tool_box 自动折叠 ─────────────────────────────

def test_close_tool_box_auto_collapses():
    """工具完成后自动折叠：block.tool_collapsed 置 True。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1")
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    block = model.blocks[0]
    assert block.kind == "tool"
    assert block.closed
    assert block.tool_collapsed is True, "工具完成后应自动折叠"


def test_close_tool_box_collapsed_committed_title_only():
    """折叠后 committed_lines 只含标题行 + 尾空行（无内容行）。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1", lines=3)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    lines = _plain(model.committed_lines)
    assert lines == ["✔ Bash ls", ""], f"折叠后应只有标题+空行，实际: {lines}"


def test_expand_restores_full_content():
    """展开后 committed_lines 恢复标题 + 全部内容 + 尾空行。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1", lines=3)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    changed = model.fold_tool_cards("", False)
    assert changed == 1
    lines = _plain(model.committed_lines)
    assert lines[0] == "✔ Bash ls", f"标题行异常: {lines[0]!r}"
    assert any("out-0" in l for l in lines), "展开后应含内容行"
    assert lines[-1] == "", "末尾应为卡片分隔空行"
    block = model.blocks[0]
    assert block.tool_collapsed is False


# ── set_tool_collapsed / fold_tool_cards ────────────────

def test_set_tool_collapsed_toggle():
    """set_tool_collapsed 无状态参数时 toggle。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1")
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    block = model.blocks[0]
    assert block.tool_collapsed is True
    assert model.set_tool_collapsed(block) is True  # toggle → False
    assert block.tool_collapsed is False
    assert model.set_tool_collapsed(block) is True  # toggle → True
    assert block.tool_collapsed is True
    assert model.set_tool_collapsed(block, True) is False  # 无变化


def test_set_tool_collapsed_non_tool_noop():
    """set_tool_collapsed 对非工具块 no-op。"""
    model = AppModel()
    apply_cmd(model, ContentCmd(text="回答"))
    apply_cmd(model, PhaseDoneCmd(phase="content"))
    block = model.blocks[0]
    assert block.kind == "content"
    assert model.set_tool_collapsed(block, True) is False
    assert not hasattr(block, "tool_collapsed") or block.tool_collapsed is False


def test_fold_tool_cards_targets_last():
    """fold_tool_cards 无参切换最后一张已关闭工具卡。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1")
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    _open_tool(model, "t2")
    apply_cmd(model, ToolCloseCmd(tool_id="t2", success=True))
    # 先折叠最后一张（t2）→ 展开（collapsed=False）
    changed = model.fold_tool_cards("", False)
    assert changed == 1
    assert model.blocks[1].tool_collapsed is False
    assert model.blocks[0].tool_collapsed is True  # t1 仍折叠


def test_fold_tool_cards_all():
    """fold_tool_cards('all') 切换全部已关闭工具卡。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1")
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    _open_tool(model, "t2")
    apply_cmd(model, ToolCloseCmd(tool_id="t2", success=True))
    changed = model.fold_tool_cards("all", False)
    assert changed == 2
    assert all(b.tool_collapsed is False for b in model.blocks)
    changed = model.fold_tool_cards("all", True)
    assert changed == 2
    assert all(b.tool_collapsed is True for b in model.blocks)


def test_fold_tool_cards_by_id():
    """fold_tool_cards 按 tool_id 定位目标。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1")
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    _open_tool(model, "t2")
    apply_cmd(model, ToolCloseCmd(tool_id="t2", success=True))
    changed = model.fold_tool_cards("t1", False)
    assert changed == 1
    assert model.blocks[0].tool_collapsed is False
    assert model.blocks[1].tool_collapsed is True


# ── ToolFoldCmd 渲染命令 ───────────────────────────────

def test_apply_tool_fold_cmd():
    """ToolFoldCmd 经 apply_cmd 处理（渲染线程路径）。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1", lines=3)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    # 折叠 → 展开
    apply_cmd(model, ToolFoldCmd(tool_id="", collapsed=False))
    assert model.blocks[0].tool_collapsed is False
    assert len(_plain(model.committed_lines)) > 2
    # 再折叠
    apply_cmd(model, ToolFoldCmd(tool_id="", collapsed=True))
    assert model.blocks[0].tool_collapsed is True
    assert _plain(model.committed_lines) == ["✔ Bash ls", ""]


# ── 增量提交块（长工具输出）折叠重建 ────────────────────

def test_incremental_block_fold_rebuilds_committed():
    """长工具输出（>64 行触发增量提交）折叠后历史内容行被移除。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1", name="Bash", detail="big", lines=100)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    block = model.blocks[0]
    assert block.committed_line_count > 64  # 确认触发过增量提交
    folded = _plain(model.committed_lines)
    assert folded == ["✔ Bash big", ""], f"折叠后应只标题+空行，实际 {len(folded)} 行"
    # 展开恢复全部
    model.fold_tool_cards("", False)
    expanded = _plain(model.committed_lines)
    assert len(expanded) > 100, "展开后应含全部内容行"
    assert any("out-99" in l for l in expanded)


# ── 被夹住块（前面未关闭块）live 渲染 ──────────────────

def test_stuck_block_live_render_collapsed():
    """被夹住块（前面有未关闭 content）折叠时 live 渲染仅标题行。"""
    model = AppModel()
    model.width = 60
    apply_cmd(model, ContentCmd(text="正在生成中..."))
    _open_tool(model, "t1", lines=2)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    block = model.blocks[1]
    assert block.closed and block.tool_collapsed
    styled = _block_styled_lines(block, 0, 60)
    texts = ["".join(r.text for r in row) for row in styled]
    assert len(texts) == 1 and "Bash" in texts[0], f"折叠 live 渲染应仅标题行: {texts}"


def test_stuck_block_live_render_expanded():
    """被夹住块展开后 live 渲染标题 + 内容。"""
    model = AppModel()
    model.width = 60
    apply_cmd(model, ContentCmd(text="正在生成中..."))
    _open_tool(model, "t1", lines=2)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    model.fold_tool_cards("", False)
    block = model.blocks[1]
    assert block.tool_collapsed is False
    styled = _block_styled_lines(block, 0, 60)
    texts = ["".join(r.text for r in row) for row in styled]
    assert len(texts) == 3, f"展开 live 渲染应标题+2内容: {texts}"
    assert any("out-0" in t for t in texts) and any("out-1" in t for t in texts)


# ── reflow_committed 保持折叠状态 ───────────────────────

def test_reflow_committed_keeps_collapsed():
    """终端宽度变化重排（reflow_committed）后折叠状态保持。"""
    model = AppModel()
    model.width = 60
    _open_tool(model, "t1", lines=3)
    apply_cmd(model, ToolCloseCmd(tool_id="t1", success=True))
    assert _plain(model.committed_lines) == ["✔ Bash ls", ""]
    model.reflow_committed(100)
    assert _plain(model.committed_lines) == ["✔ Bash ls", ""], "重排后应保持折叠"
    # 展开后重排也应保持展开
    model.fold_tool_cards("", False)
    model.reflow_committed(120)
    expanded = _plain(model.committed_lines)
    assert any("out-" in l for l in expanded), "展开 + 重排后应含内容行"


# ── 工具卡渲染行（折叠状态贯通 tool_card_lines） ─────────

def test_tool_card_lines_respects_collapsed_flag():
    """tool_card_lines 直接消费 block.tool_collapsed（渲染与模型一致）。"""
    block = SimpleNamespace(
        lines=[
            __import__("src.renderer.ansi.helpers", fromlist=["AnsiLine"]).AnsiLine.of("标题"),
            __import__("src.renderer.ansi.helpers", fromlist=["AnsiLine"]).AnsiLine.of("内容"),
        ],
        closed=True,
        tool_collapsed=True,
        extra={
            "tool_status": "done", "tool_name": "bash", "tool_detail": "ls",
            "_bash_omitted_lines": 0, "_head_omitted_lines": 0,
        },
    )
    rows = tool_card_lines(block, 40)
    assert len(rows) == 1, "折叠块 tool_card_lines 仅标题行"
    block.tool_collapsed = False
    rows = tool_card_lines(block, 40)
    assert len(rows) == 2, "展开块 tool_card_lines 标题+内容"


# ── /toolcard 命令插件 ─────────────────────────────────

def test_toolcard_plugin_parses_args():
    """ToolcardPlugin 参数解析：无参/expand/collapse/all。"""
    from src.core.commands.plugins.toolcard_plugin import ToolcardPlugin
    plugin = ToolcardPlugin()
    calls = []

    class FakeLoop:
        _chat_ui = SimpleNamespace(
            fold_tool_cards=lambda tool_id="", collapsed=None: calls.append((tool_id, collapsed)),
            on_notification=lambda text: None,
        )

    plugin.bind_loop(FakeLoop())
    ctx = SimpleNamespace(arg="")
    assert plugin.execute(ctx) is True
    assert calls[-1] == ("", None), "无参 → 最后一张 toggle"

    ctx = SimpleNamespace(arg="expand")
    plugin.execute(ctx)
    assert calls[-1] == ("", False), "expand → 展开最后一张"

    ctx = SimpleNamespace(arg="collapse all")
    plugin.execute(ctx)
    assert calls[-1] == ("all", True), "collapse all → 折叠全部"

    ctx = SimpleNamespace(arg="all expand")
    plugin.execute(ctx)
    assert calls[-1] == ("all", False), "all expand → 展开全部"


def test_toolcard_plugin_registered():
    """ToolcardPlugin 已注册到命令注册表（/toolcard、别名 /fold /expand）。"""
    # 显式导入触发插件包模块级自注册（与 app_loop 加载路径一致）
    import src.core.commands.plugins  # noqa: F401
    from src.core.commands.base import get_plugin_registry
    registry = get_plugin_registry()
    assert registry.get("toolcard") is not None
    assert registry.get("fold") is not None
    assert registry.get("expand") is not None


# ── Ctrl+Y 快捷键（2026-08-15 用户需求：手动展开/折叠） ────

def test_ctrl_y_dispatches_tool_fold():
    """Ctrl+Y（\x19）分发到 'tool_fold' 特殊按键动作。"""
    from src.tui._input_dispatcher import InputDispatcher
    dispatcher = InputDispatcher.__new__(InputDispatcher)
    dispatcher._reverse_search_enabled = False
    actions: list = []
    dispatcher._handle_special_key = lambda action: actions.append(action)
    dispatcher._handle_ctrl_key("\x19")
    assert actions == ["tool_fold"], f"Ctrl+Y 应分发 tool_fold: {actions}"


def test_special_key_tool_fold_toggles_last_card():
    """_special_keys 'tool_fold' 动作调用 fold_tool_cards 并保持输入缓冲。"""
    from src.app_loop._special_keys import make_special_key_callback
    calls: list = []

    class FakeChatUi:
        def fold_tool_cards(self, tool_id="", collapsed=None):
            calls.append((tool_id, collapsed))

        def on_notification(self, text):
            pass

    cb = make_special_key_callback(
        loop=None, session=None, state=None, chat_ui=FakeChatUi(), monitor=None,
    )
    result = cb("tool_fold", "hello")
    assert result == "hello", "tool_fold 应返回原 text（保持输入缓冲）"
    assert calls == [("", None)], f"应 toggle 最后一张工具卡: {calls}"


def test_ctrl_y_full_chain():
    """Ctrl+Y → tool_fold → fold_tool_cards 完整链路。"""
    from src.app_loop._special_keys import make_special_key_callback
    from src.tui._input_dispatcher import InputDispatcher
    calls: list = []

    class FakeChatUi:
        def fold_tool_cards(self, tool_id="", collapsed=None):
            calls.append((tool_id, collapsed))

        def on_notification(self, text):
            pass

    cb = make_special_key_callback(
        loop=None, session=None, state=None, chat_ui=FakeChatUi(), monitor=None,
    )
    dispatcher = InputDispatcher.__new__(InputDispatcher)
    dispatcher._special_key_callback = cb
    dispatcher._buffer_editor = SimpleNamespace(
        get_current_text=lambda: "hello", handle_chars=lambda _t: None,
    )
    dispatcher._reverse_search_enabled = False
    dispatcher._handle_ctrl_key("\x19")
    assert calls == [("", None)], f"完整链路应触发 fold_tool_cards: {calls}"
