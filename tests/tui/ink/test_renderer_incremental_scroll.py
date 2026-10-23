"""底部对齐增量渲染回归测试（2026-10-23 整屏重写闪烁修复）。

用户报障：TUI 一次性改变原来的行太多了，会闪。

根因：文档**底部对齐**（状态栏/输入区贴屏幕底部）时，高度变化走
``_grow_drifted`` / ``_rewrite_drifted`` 的物理映射逐行比较——映射整体位移
使**每一行都判定为变化**，每帧重写整个可见区（屏幕 30 行 → 30 次
``\\r``+EL+内容）。触发面很广：

  - 会话启动初期 / 输入区换行、退格、补全弹窗开关（文档矮于一屏，
    首帧 ``_buf_h = max(height, n)`` → 永远走漂移路径）；
  - 流式输出期间解析行消失/恢复（文档高度瞬时缩短后再增长，``_buf_h``
    不回收 → 后续每帧增长都整屏重写）。

修复：

  - **增长**（``_scroll_grow``）：底部写 ``\\n`` 让终端原生滚动 delta 行，
    已有内容免费上移到位；只重写差异行 + 位移区 ``[anchor, new_h)``；
  - **缩短**（``_insert_shrink``）：``\\033[nL`` 在文档首行（或屏幕第 1 行）
    插入 n 空行，下方内容下移、底部 n 行丢弃；只重写插入空行区、被丢弃行
    与内容真正变化的行。

覆盖：
  1. 矮文档增长/缩短：写入行数远小于屏幕高度，pyte 屏幕与新帧一致；
  2. 高文档缩短（删除末尾/近末尾）：可见窗口前移，写入行数远小于屏幕高度；
  3. 高文档缩短后增长（漂移窗口）：不再整屏重写，屏幕一致；
  4. 高文档中间删除（内容整体位移，无法避免）：屏幕仍必须完全正确；
  5. 用户历史（首帧之前终端输出）不被清除或重写；
  6. 记账：``_buf_h`` / ``_cursor_row`` / ``_top_aligned`` 与物理布局一致。
"""

from __future__ import annotations

import io

import pytest

from src.tui.ink.output import Frame, Line
from src.tui.ink.renderer import InkRenderer


def _line(text: str) -> Line:
    line = Line()
    line.append(text, None)
    return line


def _frame(texts: list[str]) -> Frame:
    return Frame([_line(t) for t in texts])


def _expected_screen(texts: list[str], height: int) -> list[str]:
    """底部对齐时屏幕应有内容：文档末 height 行，上方补空行。"""
    out = [""] * height
    tail = texts[-height:]
    out[height - len(tail):] = tail
    return out


class _Harness:
    """渲染器 + pyte 终端模拟器（首帧与增量帧都喂给模拟器）。"""

    def __init__(self, height: int = 30, width: int = 40) -> None:
        pyte = pytest.importorskip("pyte")
        self.height = height
        self.stream = io.StringIO()
        self.screen = pyte.Screen(width, height)
        self.parser = pyte.Stream(self.screen)
        self.renderer = InkRenderer(stream=self.stream, height=height)

    def _drain(self) -> str:
        data = self.stream.getvalue()
        self.stream.seek(0)
        self.stream.truncate(0)
        if data:
            self.parser.feed(data)
        return data

    def init(self, texts: list[str]) -> None:
        self.renderer.render(_frame(texts))
        self._drain()

    def render(self, texts: list[str]) -> tuple[int, str]:
        """渲染一帧，返回 ``(写行数, 原始输出)``。"""
        self.renderer.render(_frame(texts))
        data = self._drain()
        return data.count("\x1b[K"), data

    def lines(self) -> list[str]:
        return [line.rstrip() for line in self.screen.display]

    def assert_screen(self, texts: list[str]) -> None:
        assert self.lines() == _expected_screen(texts, self.height)


# ── 1. 矮文档（会话启动初期 / 输入区高度变化） ──────────


def test_short_document_growth_rewrites_only_new_rows() -> None:
    """矮文档增长：滚动 + 只写新增行（修复前整屏 21 行重写）。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(18)] + ["status", "input"]
    h.init(doc)
    assert h.renderer._buf_h == 30
    assert h.renderer.cursor_row == 30

    grown = doc[:-1] + ["input", "input2"]
    rows, _ = h.render(grown)
    assert rows <= 3, f"矮文档增长重写行数过多：{rows}"
    h.assert_screen(grown)
    assert h.renderer._buf_h == 31
    assert h.renderer.cursor_row == 30
    assert h.renderer._top_aligned is False


def test_short_document_shrink_rewrites_almost_nothing() -> None:
    """矮文档缩短：IL 下移内容 + 只重写被丢弃/变化行（修复前整屏 20 行）。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(18)] + ["status", "input", "input2"]
    h.init(doc)

    shrunk = doc[:-1]
    rows, _ = h.render(shrunk)
    assert rows <= 3, f"矮文档缩短重写行数过多：{rows}"
    h.assert_screen(shrunk)
    assert h.renderer.cursor_row == 30
    assert h.renderer._top_aligned is False


def test_short_document_shrink_keeps_user_history() -> None:
    """矮文档缩短只动文档区：首帧之前的终端历史原样保留、零重写。"""
    h = _Harness(height=30)
    # 模拟启动前终端里的 5 行历史（首帧底部锚定从第 6 行起写文档）。
    h.parser.feed("".join(f"\x1b[{i + 1};1HHIST-{i + 1}" for i in range(5)))
    h.parser.feed("\x1b[30;1H")
    doc = [f"L{i:02d}" for i in range(18)] + ["status", "input", "input2"]
    h.init(doc)
    assert h.renderer._painted_top == 9  # 文档首行物理行（0-based，屏幕第 10 行）

    before = h.lines()[:5]
    assert before == [f"HIST-{i + 1}" for i in range(5)]

    shrunk = doc[:-1]
    rows, data = h.render(shrunk)
    assert rows <= 3
    # 用户历史区既不重写也不清空。
    for i in range(5):
        assert f"\x1b[{i + 1};1H" not in data
    after = h.lines()
    assert after[:5] == before
    # 文档区（物理行 >= _painted_top，屏幕第 10 行起）与新帧一致。
    assert after[9:] == _expected_screen(shrunk, 30)[9:]


# ── 2. 高文档（流式输出 / 工具卡） ─────────────────────


def test_tall_document_shrink_at_tail_rewrites_few_rows() -> None:
    """高文档删除末尾行：可见窗口前移一行，只写顶部/底部各一行。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(56)] + ["status", "input", "mode", "hint"]
    h.init(doc)
    assert h.renderer._buf_h == 60

    shrunk = doc[:-1]
    rows, _ = h.render(shrunk)
    assert rows <= 4, f"高文档末尾缩短重写行数过多：{rows}"
    h.assert_screen(shrunk)
    assert h.renderer.cursor_row == 30


def test_tall_document_growth_after_shrink_does_not_rewrite_screen() -> None:
    """缩短后的漂移窗口增长：滚动 + 局部重写（修复前每帧整屏 30 行）。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(56)] + ["status", "input", "mode", "hint"]
    h.init(doc)

    # 1) 删除倒数第 4 行（解析进度行消失）。
    doc = [t for t in doc if t != "mode"]
    rows, _ = h.render(doc)
    assert rows <= 4

    # 2) 连续两帧增长（流式输出恢复）：每帧只写位移区。
    for k in range(2):
        doc = doc[:-2] + [f"new{k}"] + doc[-2:]
        rows, _ = h.render(doc)
        assert rows <= 8, f"漂移增长第 {k + 1} 帧重写行数过多：{rows}"
        h.assert_screen(doc)
    assert h.renderer.cursor_row == 30
    assert h.renderer._top_aligned is False


def test_tall_document_middle_shrink_still_correct() -> None:
    """高文档中间删除（内容整体位移）：允许较多重写，但屏幕必须完全正确。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(56)] + ["status", "input"]
    h.init(doc)

    doc = doc[:30] + doc[31:]
    rows, _ = h.render(doc)
    h.assert_screen(doc)
    assert h.renderer.cursor_row == 30
    # 中间删除后差异从删除点延伸到文档末尾，重写行数接近可见区高度，
    # 但不得整篇文档重写（≤ 屏幕高度）。
    assert rows <= 30


def test_tall_document_growth_after_middle_shrink_correct() -> None:
    """中间删除后增长：屏幕内容正确、写入量受控。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(56)] + ["status", "input"]
    h.init(doc)
    doc = doc[:30] + doc[31:]
    h.render(doc)

    doc = doc[:-2] + ["x"] + doc[-2:]
    rows, _ = h.render(doc)
    assert rows <= 8
    h.assert_screen(doc)


# ── 3. 顶部对齐路径不受影响（回归守卫） ──────────────


def test_top_aligned_growth_path_unchanged() -> None:
    """顶部对齐（无漂移高文档）增长仍走原路径，屏幕与记账一致。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(120)]
    h.init(doc)
    # 首帧底部锚定 → 底部对齐；软重置后首帧为顶部对齐路径（清屏重建场景）。
    h.renderer.full_clear()
    h.init(doc)
    assert h.renderer._top_aligned is True

    grown = doc + ["tail"]
    rows, _ = h.render(grown)
    assert rows <= 3
    # 顶部对齐 + 无漂移：物理缓冲 = 文档行数。
    assert h.renderer._buf_h == len(grown)
    assert h.renderer.cursor_row == 30
    # 屏幕显示文档末 30 行（底部对齐可见区语义）。
    assert h.lines() == _expected_screen(grown, 30)


# ── 4. 会话层集成（真实 App 树 + 输入区高度变化） ─────────

def test_session_input_area_growth_is_incremental() -> None:
    """真实 App 树：输入区换行让矮文档增长时只写少量行（修复前整屏）。

    覆盖用户报障的高频场景——会话初期文档不足一屏，输入框换行/退格使
    文档高度变化，修复前每帧重写整个可见区（屏幕 20 行 → 20 次写行）。
    """
    pyte = pytest.importorskip("pyte")
    from src.tui._config import TuiConfig
    from src.tui._screen import TerminalWidthCache
    from src.tui.app.app import build_app_element
    from src.tui.app.apply import apply_cmd
    from src.tui.app.model import AppModel
    from src.tui.ink.session import InkSession

    width, height = 60, 20
    cache = TerminalWidthCache(ttl=60)
    cache.set_dimensions(width, height)
    model = AppModel()
    stream = io.StringIO()
    session = InkSession(
        model=model,
        apply_cmd=apply_cmd,
        build_tree=build_app_element,
        width_cache=cache,
        config=TuiConfig.defaults(),
        stream=stream,
    )
    screen = pyte.Screen(width, height)
    parser = pyte.Stream(screen)

    def step() -> int:
        session._dirty = True
        session._idle_ok = False
        stream.seek(0)
        stream.truncate(0)
        session._render_frame()
        data = stream.getvalue()
        parser.feed(data)
        return data.count("\x1b[K")

    step()
    assert session._ink_renderer._top_aligned is False
    for text in ("a", "a\nb", "a\nb\nc", "a\nb"):
        model.input_text = text
        model.input_cursor = len(text)
        rows = step()
        assert rows <= 5, f"输入区高度变化重写行数过多：{rows}（text={text!r}）"
        # 屏幕底部对齐：最后 frame.height 行等于帧内容（上方向下补空）。
        frame = session._last_frame
        assert frame is not None
        expected = [""] * (height - frame.height) + [
            line.plain for line in frame.lines
        ]
        assert [line.rstrip() for line in screen.display] == [
            text_item.rstrip() for text_item in expected
        ]
    # 光标由会话按输入区位置定位（不要求停在屏幕底部），必须在可见区内。
    assert 1 <= session._ink_renderer.cursor_row <= height


# ── 5. 同步输出（DECSET 2026） ─────────────────────

def test_frame_write_is_wrapped_in_synchronized_update() -> None:
    """整帧写入用同步输出序列包裹：支持的终端原子刷新，消除滚动中间态。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(20)] + ["status", "input"]
    h.init(doc)

    rows, data = h.render(doc[:-1] + ["input", "input2"])
    assert rows >= 1
    begin = data.find("\033[?2026h")
    end = data.find("\033[?2026l")
    assert begin != -1 and end != -1, data
    assert begin < end
    assert data.count("\033[?2026h") == data.count("\033[?2026l") == 1
    # 同步序列包裹的是帧内容，不改变屏幕结果（pyte 忽略未知私有模式）。
    h.assert_screen(doc[:-1] + ["input", "input2"])


def test_unchanged_frame_writes_no_sync_sequence() -> None:
    """帧内容完全一致（无写入）时不发同步序列（空闲零 IO）。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(20)] + ["status", "input"]
    h.init(doc)

    rows, data = h.render(list(doc))
    assert rows == 0
    assert data == ""


# ── 6. 底栏保留区（DECSTBM 局部滚动，增长闪烁修复） ─────────

def _reserved_render(h: _Harness, texts: list[str], bottom: int) -> tuple[int, str]:
    """按会话语义先注入底栏行数，再渲染一帧。"""
    h.renderer.set_bottom_reserved(bottom)
    return h.render(texts)


def test_reserved_bottom_growth_keeps_footer_untouched() -> None:
    """增长时滚动区域限制在内容区：底栏位置不变、内容不变则零重写。"""
    h = _Harness(height=30)
    bottom = 4
    doc = [f"L{i:02d}" for i in range(26)] + ["status", "input", "mode", "hint"]
    h.renderer.set_bottom_reserved(bottom)
    h.init(doc)
    assert h.renderer._bottom_reserved == bottom

    grown = doc[:-4] + ["new"] + doc[-4:]
    rows, data = _reserved_render(h, grown, bottom)
    # DECSTBM：滚动区域 1..height-bottom，结束后重置。
    assert "\033[1;26r" in data
    assert "\033[r" in data
    # 底栏 4 行不变 → 只写新增的 1 行内容（修复前整屏滚动 + 底栏重写）。
    assert rows <= 2, f"底栏固定增长重写行数过多：{rows}"
    h.assert_screen(grown)
    assert h.renderer.cursor_row == 30


def test_reserved_bottom_footer_change_rewrites_only_changed_rows() -> None:
    """底栏内容变化（时钟/输入）只重写变化的底栏行，不整段重写。"""
    h = _Harness(height=30)
    bottom = 4
    doc = [f"L{i:02d}" for i in range(26)] + ["status", "input", "mode", "hint"]
    h.renderer.set_bottom_reserved(bottom)
    h.init(doc)

    # 内容增长 1 行 + 底栏第一行（status）变化。
    grown = doc[:-4] + ["new"] + doc[-4:]
    grown[-4] = "status2"
    rows, data = _reserved_render(h, grown, bottom)
    assert "\033[1;26r" in data
    assert rows <= 2, f"底栏变化重写行数过多：{rows}"
    h.assert_screen(grown)


def test_reserved_bottom_height_change_falls_back_to_full_scroll() -> None:
    """底栏高度变化（输入区换行）时回退整屏滚动，屏幕仍必须正确。"""
    h = _Harness(height=30)
    doc = [f"L{i:02d}" for i in range(26)] + ["status", "input", "mode", "hint"]
    h.renderer.set_bottom_reserved(4)
    h.init(doc)

    # 底栏从 4 行变为 5 行（末尾新增一行），同时内容增长 1 行。
    grown = doc[:-4] + ["new"] + doc[-4:] + ["input2"]
    rows, data = _reserved_render(h, grown, 5)
    assert "\033[r" not in data  # 未使用局部滚动（回退整屏滚动）
    h.assert_screen(grown)
    assert h.renderer.cursor_row == 30

